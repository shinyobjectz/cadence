//! candle backend: Qwen2-family safetensors (Qwen2.5-Coder-*-Instruct and any
//! LoRA-merged fine-tune of it). CPU everywhere, Metal on the Mac (`--features
//! metal`), CUDA on cuda-box in WSL2 (`--features cuda`).

use super::tok::{HfConfig, Tok};
use super::{hit_stop, strip_stop, Backend, Device, GenParams, Generation};
use anyhow::{bail, Context, Result};
use candle_core::{DType, Tensor};
use candle_nn::VarBuilder;
use candle_transformers::generation::{LogitsProcessor, Sampling};
use candle_transformers::models::qwen2::{Config, ModelForCausalLM};
use std::path::{Path, PathBuf};

pub struct CandleBackend {
    model: ModelForCausalLM,
    tok: Tok,
    device: candle_core::Device,
    eos: Vec<u32>,
    label: String,
}

fn safetensor_files(dir: &Path) -> Result<Vec<PathBuf>> {
    let single = dir.join("model.safetensors");
    if single.exists() {
        return Ok(vec![single]);
    }
    let index = dir.join("model.safetensors.index.json");
    if index.exists() {
        let v: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(&index)?)?;
        let mut files: Vec<String> = v["weight_map"].as_object().map(|m| m.values().filter_map(|x| x.as_str().map(|s| s.to_string())).collect()).unwrap_or_default();
        files.sort();
        files.dedup();
        return Ok(files.into_iter().map(|f| dir.join(f)).collect());
    }
    bail!("no model.safetensors or index in {}", dir.display())
}

impl CandleBackend {
    pub fn open(dir: &str, device: Device) -> Result<Self> {
        let dir = Path::new(dir);
        let dev = match device {
            Device::Cpu => candle_core::Device::Cpu,
            Device::Metal => {
                #[cfg(feature = "metal")]
                { candle_core::Device::new_metal(0)? }
                #[cfg(not(feature = "metal"))]
                { bail!("built without --features metal") }
            }
            Device::Cuda(i) => {
                #[cfg(feature = "cuda")]
                { candle_core::Device::new_cuda(i)? }
                #[cfg(not(feature = "cuda"))]
                { let _ = i; bail!("built without --features cuda") }
            }
        };
        let dtype = match super::dtype_env().as_deref() {
            Some("f32") => DType::F32,
            Some("f16") => DType::F16,
            Some("bf16") => DType::BF16,
            Some(other) => bail!("CADENCE_DTYPE={other}: f32|f16|bf16"),
            None => match device { Device::Cpu => DType::F32, Device::Metal => DType::F16, Device::Cuda(_) => DType::BF16 },
        };
        let cfg_json = std::fs::read_to_string(dir.join("config.json")).context("config.json")?;
        let cfg: Config = serde_json::from_str(&cfg_json).context("qwen2 config")?;
        let hf = HfConfig::open(dir)?;
        let files = safetensor_files(dir)?;
        let vb = unsafe { VarBuilder::from_mmaped_safetensors(&files, dtype, &dev)? };
        let model = ModelForCausalLM::new(&cfg, vb).context("loading qwen2 weights")?;
        let tok = Tok::open(dir)?;
        let mut eos = hf.eos_ids();
        for t in ["<|im_end|>", "<|endoftext|>"] {
            if let Some(id) = tok.id(t) { if !eos.contains(&id) { eos.push(id); } }
        }
        let label = format!("candle:{}:{:?}", dir.file_name().and_then(|s| s.to_str()).unwrap_or("model"), dtype);
        Ok(CandleBackend { model, tok, device: dev, eos, label })
    }
}

impl Backend for CandleBackend {
    fn name(&self) -> String { self.label.clone() }

    fn generate(&mut self, prompt: &str, p: &GenParams) -> Result<Generation> {
        let t0 = std::time::Instant::now();
        self.model.clear_kv_cache();
        let ids = self.tok.encode(prompt)?;
        let sampling = if p.temperature <= 0.0 { Sampling::ArgMax } else if p.top_p < 1.0 { Sampling::TopP { p: p.top_p, temperature: p.temperature } } else { Sampling::All { temperature: p.temperature } };
        let mut lp = LogitsProcessor::from_sampling(p.seed, sampling);
        let mut out_ids: Vec<u32> = Vec::new();
        let mut text = String::new();
        // prefill
        let input = Tensor::new(ids.as_slice(), &self.device)?.unsqueeze(0)?;
        let mut logits = self.model.forward(&input, 0)?;
        let mut pos = ids.len();
        for _ in 0..p.max_new_tokens {
            let l = logits.squeeze(0)?.squeeze(0)?.to_dtype(DType::F32)?;
            let next = lp.sample(&l)?;
            if self.eos.contains(&next) { break; }
            out_ids.push(next);
            // decode incrementally for stop strings (cheap at these lengths)
            if !p.stop.is_empty() && out_ids.len() % 4 == 0 {
                text = self.tok.decode(&out_ids)?;
                if hit_stop(&text, &p.stop) { break; }
            }
            let input = Tensor::new(&[next], &self.device)?.unsqueeze(0)?;
            logits = self.model.forward(&input, pos)?;
            pos += 1;
        }
        text = self.tok.decode(&out_ids)?;
        let text = strip_stop(text, &p.stop);
        Ok(Generation { text, prompt_tokens: ids.len(), gen_tokens: out_ids.len(), secs: t0.elapsed().as_secs_f64() })
    }
}
