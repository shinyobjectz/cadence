//! Inference backends behind one trait. Every backend takes a fully formatted
//! chat prompt (see `prompt::chat_format`) and returns the assistant text.

use anyhow::{bail, Result};
use serde::{Deserialize, Serialize};

pub mod mock;
#[cfg(feature = "candle")]
pub mod candle;
#[cfg(feature = "ort")]
pub mod ort;
#[cfg(feature = "mlx")]
pub mod mlx;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GenParams {
    pub max_new_tokens: usize,
    pub temperature: f64,
    pub top_p: f64,
    pub seed: u64,
    /// generation stops when the decoded text ends with any of these
    pub stop: Vec<String>,
}

impl Default for GenParams {
    fn default() -> Self {
        GenParams {
            max_new_tokens: 1024,
            temperature: 0.0,
            top_p: 1.0,
            seed: 0,
            stop: vec!["<|im_end|>".into(), "<|endoftext|>".into()],
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Generation {
    pub text: String,
    pub prompt_tokens: usize,
    pub gen_tokens: usize,
    pub secs: f64,
}

pub trait Backend {
    fn name(&self) -> String;
    fn generate(&mut self, prompt: &str, params: &GenParams) -> Result<Generation>;
    /// Called by the eval runner before each task; mock backends key on it.
    fn set_context(&mut self, _task_id: &str) {}
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Device {
    Cpu,
    Metal,
    Cuda(usize),
}

impl Device {
    pub fn parse(s: &str) -> Result<Device> {
        Ok(match s {
            "cpu" => Device::Cpu,
            "metal" => Device::Metal,
            "cuda" => Device::Cuda(0),
            s if s.starts_with("cuda:") => Device::Cuda(s[5..].parse()?),
            other => bail!("unknown device {other} (cpu|metal|cuda|cuda:N)"),
        })
    }
}

/// Names the backends this binary was compiled with.
pub fn compiled() -> Vec<&'static str> {
    let mut v = vec!["mock"];
    if cfg!(feature = "candle") { v.push("candle"); }
    if cfg!(feature = "ort") { v.push("ort"); }
    if cfg!(feature = "mlx") { v.push("mlx"); }
    v
}

/// Build a backend by name. `model` is a directory (candle/mlx: config.json +
/// tokenizer.json + safetensors; ort: + model.onnx). `device` is advisory
/// for backends that support several.
/// Weight dtype for the candle/mlx backends: `CADENCE_DTYPE=f32|f16|bf16`
/// (default: f32 on CPU, f16 on Metal, bf16 on CUDA).
pub fn dtype_env() -> Option<String> {
    std::env::var("CADENCE_DTYPE").ok().filter(|s| !s.is_empty())
}

pub fn open(name: &str, model: Option<&str>, device: Device) -> Result<Box<dyn Backend>> {
    let _ = (model, device);
    match name {
        "mock" => Ok(Box::new(mock::MockBackend::from_env()?)),
        #[cfg(feature = "candle")]
        "candle" => {
            let dir = model.ok_or_else(|| anyhow::anyhow!("--model DIR required for candle"))?;
            Ok(Box::new(candle::CandleBackend::open(dir, device)?))
        }
        #[cfg(feature = "ort")]
        "ort" => {
            let dir = model.ok_or_else(|| anyhow::anyhow!("--model DIR required for ort"))?;
            Ok(Box::new(ort::OrtBackend::open(dir, device)?))
        }
        #[cfg(feature = "mlx")]
        "mlx" => {
            let dir = model.ok_or_else(|| anyhow::anyhow!("--model DIR required for mlx"))?;
            Ok(Box::new(mlx::MlxBackend::open(dir)?))
        }
        other => bail!(
            "backend {other} not available in this build (compiled: {}) — rebuild with --features {other}",
            compiled().join(",")
        ),
    }
}

/// Shared by the real backends: read the HF tokenizer and expose encode/decode.
#[cfg(any(feature = "candle", feature = "ort", feature = "mlx"))]
pub mod tok {
    use anyhow::{Context, Result};
    use std::path::Path;

    pub struct Tok(pub tokenizers::Tokenizer);

    impl Tok {
        pub fn open(dir: &Path) -> Result<Tok> {
            let p = dir.join("tokenizer.json");
            let t = tokenizers::Tokenizer::from_file(&p)
                .map_err(|e| anyhow::anyhow!("{}: {e}", p.display()))?;
            Ok(Tok(t))
        }
        pub fn encode(&self, text: &str) -> Result<Vec<u32>> {
            let enc = self.0.encode(text, false).map_err(|e| anyhow::anyhow!("{e}"))?;
            Ok(enc.get_ids().to_vec())
        }
        pub fn decode(&self, ids: &[u32]) -> Result<String> {
            self.0.decode(ids, true).map_err(|e| anyhow::anyhow!("{e}"))
        }
        pub fn id(&self, token: &str) -> Option<u32> {
            self.0.token_to_id(token)
        }
    }

    /// The `config.json` fields every backend needs for KV shapes / stop ids.
    #[derive(Debug, Clone, serde::Deserialize)]
    pub struct HfConfig {
        pub hidden_size: usize,
        pub num_attention_heads: usize,
        pub num_key_value_heads: Option<usize>,
        pub num_hidden_layers: usize,
        pub vocab_size: usize,
        #[serde(default)]
        pub eos_token_id: serde_json::Value,
    }

    impl HfConfig {
        pub fn open(dir: &Path) -> Result<HfConfig> {
            let p = dir.join("config.json");
            let s = std::fs::read_to_string(&p).with_context(|| p.display().to_string())?;
            Ok(serde_json::from_str(&s)?)
        }
        pub fn eos_ids(&self) -> Vec<u32> {
            match &self.eos_token_id {
                serde_json::Value::Number(n) => n.as_u64().map(|v| vec![v as u32]).unwrap_or_default(),
                serde_json::Value::Array(a) => a.iter().filter_map(|v| v.as_u64().map(|v| v as u32)).collect(),
                _ => vec![],
            }
        }
    }
}

/// Stop-string check shared by the streaming loops.
pub fn hit_stop(text: &str, stop: &[String]) -> bool {
    stop.iter().any(|s| !s.is_empty() && text.ends_with(s))
}

/// Strip a trailing stop string from the final text.
pub fn strip_stop(mut text: String, stop: &[String]) -> String {
    for s in stop {
        if !s.is_empty() && text.ends_with(s) {
            let n = text.len() - s.len();
            text.truncate(n);
        }
    }
    text
}
