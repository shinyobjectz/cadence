//! mlx backend (Apple silicon): a Qwen2 decoder written against mlx-rs, so
//! the same safetensors directory the candle backend reads runs on MLX.
//! Feature `mlx`. Weights load as they are (bf16), RoPE/attention/MLP follow
//! the HF Qwen2 reference; tied embeddings are honoured when `lm_head` is
//! absent.

use super::tok::{HfConfig, Tok};
use super::{hit_stop, strip_stop, Backend, GenParams, Generation};
use anyhow::{bail, Context, Result};
use mlx_rs::ops::indexing::IndexOp;
use mlx_rs::ops::indexing::argmax_axis;
use mlx_rs::{ops, Array};
use std::collections::HashMap;
use std::path::Path;

#[derive(serde::Deserialize)]
struct Cfg {
    hidden_size: i32,
    num_attention_heads: i32,
    num_key_value_heads: i32,
    num_hidden_layers: i32,
    rms_norm_eps: f32,
    rope_theta: Option<f32>,
    #[serde(default)]
    tie_word_embeddings: bool,
}

struct Layer {
    ln1: Array,
    ln2: Array,
    q_w: Array, q_b: Array,
    k_w: Array, k_b: Array,
    v_w: Array, v_b: Array,
    o_w: Array,
    gate_w: Array, up_w: Array, down_w: Array,
    k_cache: Option<Array>,
    v_cache: Option<Array>,
}

pub struct MlxBackend {
    cfg: Cfg,
    embed: Array,
    norm: Array,
    lm_head: Array,
    layers: Vec<Layer>,
    tok: Tok,
    eos: Vec<u32>,
    label: String,
}

fn take(w: &mut HashMap<String, Array>, name: &str) -> Result<Array> {
    w.remove(name).ok_or_else(|| anyhow::anyhow!("missing weight {name}"))
}

fn rms_norm(x: &Array, w: &Array, eps: f32) -> Result<Array> {
    Ok(mlx_rs::fast::rms_norm(x, Some(w), eps)?)
}

fn linear(x: &Array, w: &Array, b: Option<&Array>) -> Result<Array> {
    let y = ops::matmul(x, &w.t())?;
    Ok(match b { Some(b) => ops::add(&y, b)?, None => y })
}

impl MlxBackend {
    pub fn open(dir: &str) -> Result<Self> {
        let dir = Path::new(dir);
        let cfg: Cfg = serde_json::from_str(&std::fs::read_to_string(dir.join("config.json"))?).context("config.json")?;
        let hf = HfConfig::open(dir)?;
        let mut weights: HashMap<String, Array> = HashMap::new();
        let mut files: Vec<_> = std::fs::read_dir(dir)?.filter_map(|e| e.ok()).map(|e| e.path())
            .filter(|p| p.extension().map(|x| x == "safetensors").unwrap_or(false)).collect();
        files.sort();
        if files.is_empty() { bail!("no .safetensors in {}", dir.display()); }
        for f in &files {
            let map = Array::load_safetensors(f.to_str().unwrap())?;
            weights.extend(map);
        }
        let embed = take(&mut weights, "model.embed_tokens.weight")?;
        let norm = take(&mut weights, "model.norm.weight")?;
        let lm_head = if cfg.tie_word_embeddings || !weights.contains_key("lm_head.weight") { embed.clone() } else { take(&mut weights, "lm_head.weight")? };
        let mut layers = Vec::new();
        for i in 0..cfg.num_hidden_layers {
            let p = |n: &str| format!("model.layers.{i}.{n}");
            layers.push(Layer {
                ln1: take(&mut weights, &p("input_layernorm.weight"))?,
                ln2: take(&mut weights, &p("post_attention_layernorm.weight"))?,
                q_w: take(&mut weights, &p("self_attn.q_proj.weight"))?, q_b: take(&mut weights, &p("self_attn.q_proj.bias"))?,
                k_w: take(&mut weights, &p("self_attn.k_proj.weight"))?, k_b: take(&mut weights, &p("self_attn.k_proj.bias"))?,
                v_w: take(&mut weights, &p("self_attn.v_proj.weight"))?, v_b: take(&mut weights, &p("self_attn.v_proj.bias"))?,
                o_w: take(&mut weights, &p("self_attn.o_proj.weight"))?,
                gate_w: take(&mut weights, &p("mlp.gate_proj.weight"))?,
                up_w: take(&mut weights, &p("mlp.up_proj.weight"))?,
                down_w: take(&mut weights, &p("mlp.down_proj.weight"))?,
                k_cache: None, v_cache: None,
            });
        }
        let tok = Tok::open(dir)?;
        let mut eos = hf.eos_ids();
        for t in ["<|im_end|>", "<|endoftext|>"] {
            if let Some(id) = tok.id(t) { if !eos.contains(&id) { eos.push(id); } }
        }
        let label = format!("mlx:{}", dir.file_name().and_then(|s| s.to_str()).unwrap_or("model"));
        Ok(MlxBackend { cfg, embed, norm, lm_head, layers, tok, eos, label })
    }

    fn clear(&mut self) {
        for l in &mut self.layers { l.k_cache = None; l.v_cache = None; }
    }

    /// One forward pass over `ids` at absolute position `offset`; returns last-token logits.
    fn forward(&mut self, ids: &[u32], offset: i32) -> Result<Array> {
        let n = ids.len() as i32;
        let h = self.cfg.num_attention_heads;
        let kvh = self.cfg.num_key_value_heads;
        let hd = self.cfg.hidden_size / h;
        let ids_arr = Array::from_slice(&ids.iter().map(|&x| x as i32).collect::<Vec<_>>(), &[1, n]);
        let mut x = self.embed.index(ids_arr); // [1, n, hidden]
        // causal only while prefilling; a single decoded token attends to the whole cache
        let causal = n > 1;
        let scale = 1.0 / (hd as f32).sqrt();
        let theta = self.cfg.rope_theta.unwrap_or(1_000_000.0);
        for l in &mut self.layers {
            let hn = rms_norm(&x, &l.ln1, self.cfg.rms_norm_eps)?;
            let q = linear(&hn, &l.q_w, Some(&l.q_b))?.reshape(&[1, n, h, hd])?.transpose_axes(&[0, 2, 1, 3])?;
            let k = linear(&hn, &l.k_w, Some(&l.k_b))?.reshape(&[1, n, kvh, hd])?.transpose_axes(&[0, 2, 1, 3])?;
            let v = linear(&hn, &l.v_w, Some(&l.v_b))?.reshape(&[1, n, kvh, hd])?.transpose_axes(&[0, 2, 1, 3])?;
            let q = mlx_rs::fast::rope(&q, hd, false, Some(theta), 1.0, offset, None)?;
            let k = mlx_rs::fast::rope(&k, hd, false, Some(theta), 1.0, offset, None)?;
            let (k, v) = match (&l.k_cache, &l.v_cache) {
                (Some(kc), Some(vc)) => (ops::concatenate_axis(&[kc, &k], 2)?, ops::concatenate_axis(&[vc, &v], 2)?),
                _ => (k, v),
            };
            l.k_cache = Some(k.clone());
            l.v_cache = Some(v.clone());
            let mask: Option<mlx_rs::fast::ScaledDotProductAttentionMask> = if causal { Some(mlx_rs::fast::ScaledDotProductAttentionMask::Causal) } else { None };
            let o = mlx_rs::fast::scaled_dot_product_attention(&q, &k, &v, scale, mask, None)?;
            let o = o.transpose_axes(&[0, 2, 1, 3])?.reshape(&[1, n, h * hd])?;
            let o = linear(&o, &l.o_w, None)?;
            x = ops::add(&x, &o)?;
            let hn = rms_norm(&x, &l.ln2, self.cfg.rms_norm_eps)?;
            let g = mlx_rs::nn::silu(&linear(&hn, &l.gate_w, None)?)?;
            let u = linear(&hn, &l.up_w, None)?;
            let m = linear(&ops::multiply(&g, &u)?, &l.down_w, None)?;
            x = ops::add(&x, &m)?;
        }
        let x = rms_norm(&x, &self.norm, self.cfg.rms_norm_eps)?;
        let last = x.index((.., -1, ..)); // [1, hidden]
        let logits = linear(&last, &self.lm_head, None)?; // [1, vocab]
        Ok(logits.as_dtype(mlx_rs::Dtype::Float32)?)
    }
}

impl Backend for MlxBackend {
    fn name(&self) -> String { self.label.clone() }

    fn generate(&mut self, prompt: &str, p: &GenParams) -> Result<Generation> {
        let t0 = std::time::Instant::now();
        self.clear();
        let ids = self.tok.encode(prompt)?;
        let mut out_ids: Vec<u32> = Vec::new();
        let mut text = String::new();
        let mut logits = self.forward(&ids, 0)?;
        let mut pos = ids.len() as i32;
        for _ in 0..p.max_new_tokens {
            let next = if p.temperature <= 0.0 {
                argmax_axis(&logits, -1, None)?.item::<u32>()
            } else {
                let scaled = ops::divide(&logits, &Array::from_f32(p.temperature as f32))?;
                mlx_rs::random::categorical(&scaled, None, None, None)?.item::<u32>()
            };
            if self.eos.contains(&next) { break; }
            out_ids.push(next);
            if !p.stop.is_empty() && out_ids.len() % 4 == 0 {
                text = self.tok.decode(&out_ids)?;
                if hit_stop(&text, &p.stop) { break; }
            }
            logits = self.forward(&[next], pos)?;
            pos += 1;
        }
        text = self.tok.decode(&out_ids)?;
        let text = strip_stop(text, &p.stop);
        Ok(Generation { text, prompt_tokens: ids.len(), gen_tokens: out_ids.len(), secs: t0.elapsed().as_secs_f64() })
    }
}
