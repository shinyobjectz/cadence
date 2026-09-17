//! Deterministic backend for the harness itself. Modes:
//! - `reference`: replies with the task's reference edit (set by the eval
//!   runner through `set_context` + `set_reference`), proving the apply →
//!   validate → report chain end to end without a model.
//! - `echo`: replies with a no-op edit, the baseline "model did nothing".
//! - `script:FILE`: replies with FILE's contents (manual what-if runs).

use super::{Backend, GenParams, Generation};
use anyhow::Result;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub enum MockMode {
    Reference,
    Echo,
    Script(String),
}

pub struct MockBackend {
    pub mode: MockMode,
    pub references: HashMap<String, String>,
    current: String,
}

impl MockBackend {
    pub fn new(mode: MockMode) -> Self {
        MockBackend { mode, references: HashMap::new(), current: String::new() }
    }
    /// `CADENCE_MOCK=reference|echo|script:FILE` (default reference)
    pub fn from_env() -> Result<Self> {
        let m = std::env::var("CADENCE_MOCK").unwrap_or_else(|_| "reference".into());
        let mode = match m.as_str() {
            "reference" => MockMode::Reference,
            "echo" => MockMode::Echo,
            s if s.starts_with("script:") => MockMode::Script(std::fs::read_to_string(&s[7..])?),
            other => anyhow::bail!("CADENCE_MOCK={other}: reference|echo|script:FILE"),
        };
        Ok(MockBackend::new(mode))
    }
    pub fn set_reference(&mut self, task_id: &str, reply: &str) {
        self.references.insert(task_id.to_string(), reply.to_string());
    }
}

impl Backend for MockBackend {
    fn name(&self) -> String {
        format!("mock:{}", match self.mode {
            MockMode::Reference => "reference",
            MockMode::Echo => "echo",
            MockMode::Script(_) => "script",
        })
    }
    fn set_context(&mut self, task_id: &str) {
        self.current = task_id.to_string();
    }
    fn generate(&mut self, _prompt: &str, _params: &GenParams) -> Result<Generation> {
        let text = match &self.mode {
            MockMode::Reference => self
                .references
                .get(&self.current)
                .cloned()
                .unwrap_or_else(|| "NO_CHANGE".to_string()),
            MockMode::Echo => "NO_CHANGE".to_string(),
            MockMode::Script(s) => s.clone(),
        };
        Ok(Generation { gen_tokens: text.split_whitespace().count(), text, ..Default::default() })
    }
}
