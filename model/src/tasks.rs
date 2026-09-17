//! Edit tasks: what to ask, and how to know the answer is right.
//!
//! ```json
//! {
//!   "id": "primitives_fade_slower",
//!   "comp": "evals/cases/primitives.lua",
//!   "instruction": "Make the title fade in over 1.2 seconds instead of 0.6.",
//!   "reference": "<<<<<<< SEARCH ... >>>>>>> REPLACE",   // what a correct model would reply
//!   "checks": {
//!     "compile": true,
//!     "no_new_lint_errors": true,
//!     "props": [ { "node": "text1", "t": 1.0, "prop": "opacity", "op": ">=", "value": 0.99 } ],
//!     "frames_change": [1.0],        // seconds where the frame hash must differ from the original
//!     "frames_hold": [0.0, 5.9],     // seconds where the frame hash must equal the original
//!     "match_reference_frames": 1.0  // fraction of frames that must equal the reference edit's frames
//!   }
//! }
//! ```

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PropCheck {
    pub node: String,
    pub t: f64,
    pub prop: String,
    pub op: String,
    pub value: serde_json::Value,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Checks {
    #[serde(default = "yes")]
    pub compile: bool,
    #[serde(default = "yes")]
    pub no_new_lint_errors: bool,
    #[serde(default)]
    pub props: Vec<PropCheck>,
    #[serde(default)]
    pub frames_change: Vec<f64>,
    #[serde(default)]
    pub frames_hold: Vec<f64>,
    /// 0 = skip; 1.0 = every frame must match the reference edit's render
    #[serde(default)]
    pub match_reference_frames: f64,
}

fn yes() -> bool { true }

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub comp: String,
    pub instruction: String,
    #[serde(default)]
    pub reference: String,
    #[serde(default)]
    pub checks: Checks,
    #[serde(default)]
    pub tags: Vec<String>,
    #[serde(default)]
    pub source: Option<String>,
}

impl Task {
    pub fn comp_path(&self, root: &Path) -> PathBuf {
        let p = Path::new(&self.comp);
        if p.is_absolute() { p.to_path_buf() } else { root.join(p) }
    }
}

/// Load every `*.json` task under `dir` (recursively), sorted by id.
pub fn load_dir(dir: &Path) -> Result<Vec<Task>> {
    let mut out = Vec::new();
    fn walk(d: &Path, out: &mut Vec<Task>) -> Result<()> {
        let mut entries: Vec<_> = std::fs::read_dir(d)?.filter_map(|e| e.ok()).collect();
        entries.sort_by_key(|e| e.path());
        for e in entries {
            let p = e.path();
            if p.is_dir() {
                walk(&p, out)?;
            } else if p.extension().map(|x| x == "json").unwrap_or(false) {
                let s = std::fs::read_to_string(&p).with_context(|| p.display().to_string())?;
                let mut t: Task = serde_json::from_str(&s).with_context(|| format!("parsing task {}", p.display()))?;
                t.source = Some(p.display().to_string());
                out.push(t);
            }
        }
        Ok(())
    }
    walk(dir, &mut out)?;
    out.sort_by(|a, b| a.id.cmp(&b.id));
    Ok(out)
}

pub fn save(task: &Task, path: &Path) -> Result<()> {
    if let Some(d) = path.parent() {
        std::fs::create_dir_all(d)?;
    }
    let mut t = task.clone();
    t.source = None;
    std::fs::write(path, serde_json::to_string_pretty(&t)? + "\n")?;
    Ok(())
}
