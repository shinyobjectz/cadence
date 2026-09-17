//! ort backend: an optimum-exported decoder (`model.onnx` next to config.json
//! and tokenizer.json) with `past_key_values.N.key/value` inputs and
//! `present.N.key/value` outputs, as `optimum-cli export onnx --task
//! text-generation-with-past` produces. CPU by default, `--features ort-cuda`
//! + `--device cuda` for the CUDA execution provider on cuda-box.

use super::tok::{HfConfig, Tok};
use super::{hit_stop, strip_stop, Backend, Device, GenParams, Generation};
use anyhow::{bail, Context, Result};
use ort::session::{builder::GraphOptimizationLevel, Session};
use ort::value::Tensor;
use std::path::Path;

/// ort's error type is not Send + Sync, so it cannot ride `?` into anyhow.
trait Oe<T> { fn oe(self) -> Result<T>; }
impl<T, E: std::fmt::Display> Oe<T> for std::result::Result<T, E> {
    fn oe(self) -> Result<T> { self.map_err(|e| anyhow::anyhow!("ort: {e}")) }
}

pub struct OrtBackend {
    session: Session,
    tok: Tok,
    eos: Vec<u32>,
    layers: usize,
    kv_heads: usize,
    head_dim: usize,
    label: String,
    has_position_ids: bool,
    kv_names: Vec<(String, String)>, // (key input, value input) per layer
}

impl OrtBackend {
    pub fn open(dir: &str, device: Device) -> Result<Self> {
        let dir = Path::new(dir);
        let onnx = ["model.onnx", "decoder_model_merged.onnx", "onnx/model.onnx"]
            .iter().map(|f| dir.join(f)).find(|p| p.exists())
            .ok_or_else(|| anyhow::anyhow!("no model.onnx under {}", dir.display()))?;
        let hf = HfConfig::open(dir)?;
        let mut builder = Session::builder().oe()?.with_optimization_level(GraphOptimizationLevel::Level3).oe()?;
        match device {
            Device::Cuda(_i) => {
                #[cfg(feature = "ort-cuda")]
                { builder = builder.with_execution_providers([ort::execution_providers::CUDAExecutionProvider::default().build()]).oe()?; }
                #[cfg(not(feature = "ort-cuda"))]
                { bail!("built without --features ort-cuda") }
            }
            Device::Metal => bail!("ort has no Metal EP in this build; use --device cpu (CoreML EP not wired)"),
            Device::Cpu => {}
        }
        let session = builder.commit_from_file(&onnx).oe().with_context(|| onnx.display().to_string())?;
        let inputs: Vec<String> = session.inputs().iter().map(|i| i.name().to_string()).collect();
        let has_position_ids = inputs.iter().any(|n| n == "position_ids");
        let mut kv_names = Vec::new();
        for l in 0..hf.num_hidden_layers {
            let k = format!("past_key_values.{l}.key");
            let v = format!("past_key_values.{l}.value");
            if inputs.contains(&k) && inputs.contains(&v) { kv_names.push((k, v)); }
        }
        if kv_names.is_empty() {
            bail!("model.onnx has no past_key_values inputs; export with --task text-generation-with-past");
        }
        let kv_heads = hf.num_key_value_heads.unwrap_or(hf.num_attention_heads);
        let head_dim = hf.hidden_size / hf.num_attention_heads;
        let tok = Tok::open(dir)?;
        let mut eos = hf.eos_ids();
        for t in ["<|im_end|>", "<|endoftext|>"] {
            if let Some(id) = tok.id(t) { if !eos.contains(&id) { eos.push(id); } }
        }
        let label = format!("ort:{}", dir.file_name().and_then(|s| s.to_str()).unwrap_or("model"));
        Ok(OrtBackend { session, tok, eos, layers: hf.num_hidden_layers, kv_heads, head_dim, label, has_position_ids, kv_names })
    }
}

fn argmax(v: &[f32]) -> u32 {
    let mut best = 0usize;
    for (i, x) in v.iter().enumerate() { if *x > v[best] { best = i; } }
    best as u32
}

impl Backend for OrtBackend {
    fn name(&self) -> String { self.label.clone() }

    fn generate(&mut self, prompt: &str, p: &GenParams) -> Result<Generation> {
        let t0 = std::time::Instant::now();
        let ids = self.tok.encode(prompt)?;
        let mut out_ids: Vec<u32> = Vec::new();
        let mut text = String::new();
        // KV cache as owned f32 buffers: [1, kv_heads, past_len, head_dim]
        let mut past: Vec<(Vec<f32>, Vec<f32>)> = vec![(Vec::new(), Vec::new()); self.layers];
        let mut past_len = 0usize;
        let mut step_ids: Vec<i64> = ids.iter().map(|&x| x as i64).collect();
        let mut rng = p.seed;
        for _step in 0..=p.max_new_tokens {
            let cur = step_ids.len();
            let total = past_len + cur;
            let mut inputs: Vec<(String, ort::value::DynValue)> = Vec::new();
            inputs.push(("input_ids".into(), Tensor::from_array(([1usize, cur], step_ids.clone())).oe()?.into_dyn()));
            inputs.push(("attention_mask".into(), Tensor::from_array(([1usize, total], vec![1i64; total])).oe()?.into_dyn()));
            if self.has_position_ids {
                let pos: Vec<i64> = (past_len..total).map(|x| x as i64).collect();
                inputs.push(("position_ids".into(), Tensor::from_array(([1usize, cur], pos)).oe()?.into_dyn()));
            }
            for (l, (kn, vn)) in self.kv_names.iter().enumerate() {
                let shape = [1usize, self.kv_heads, past_len, self.head_dim];
                inputs.push((kn.clone(), Tensor::from_array((shape, past[l].0.clone())).oe()?.into_dyn()));
                inputs.push((vn.clone(), Tensor::from_array((shape, past[l].1.clone())).oe()?.into_dyn()));
            }
            let outputs = self.session.run(inputs).oe()?;
            let (lshape, logits) = outputs["logits"].try_extract_tensor::<f32>().oe()?;
            let vocab = lshape[lshape.len() - 1] as usize;
            let last = &logits[(cur - 1) * vocab..cur * vocab];
            let next = if p.temperature <= 0.0 { argmax(last) } else {
                // temperature sampling (no top-p) with a tiny LCG — deterministic per seed
                let mut probs: Vec<f64> = last.iter().map(|&x| (x as f64 / p.temperature).exp()).collect();
                let z: f64 = probs.iter().sum();
                for q in probs.iter_mut() { *q /= z; }
                rng = rng.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
                let mut u = (rng >> 11) as f64 / (1u64 << 53) as f64;
                let mut pick = 0u32;
                for (i, q) in probs.iter().enumerate() { if u < *q { pick = i as u32; break; } u -= q; pick = i as u32; }
                pick
            };
            for l in 0..self.layers {
                let (_, k) = outputs[format!("present.{l}.key")].try_extract_tensor::<f32>().oe()?;
                let (_, v) = outputs[format!("present.{l}.value")].try_extract_tensor::<f32>().oe()?;
                past[l] = (k.to_vec(), v.to_vec());
            }
            past_len = total;
            if self.eos.contains(&next) || out_ids.len() >= p.max_new_tokens { break; }
            out_ids.push(next);
            if !p.stop.is_empty() && out_ids.len() % 4 == 0 {
                text = self.tok.decode(&out_ids)?;
                if hit_stop(&text, &p.stop) { break; }
            }
            step_ids = vec![next as i64];
        }
        text = self.tok.decode(&out_ids)?;
        let text = strip_stop(text, &p.stop);
        Ok(Generation { text, prompt_tokens: ids.len(), gen_tokens: out_ids.len(), secs: t0.elapsed().as_secs_f64() })
    }
}
