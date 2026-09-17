//! cadence-model: the editor model as a promptable editing agent.
//!
//! Pipeline: `prompt` (instruction + comp + timeline facts) → `backend`
//! (candle / ort / mlx / mock) → `apply` (SEARCH/REPLACE blocks or full file)
//! → `validate` (`bin/cadence verify`, frame hashes, node props at t).
//! `tasks` describe edit cases; `mutate` synthesises them from real comps;
//! `eval` runs a suite and writes a report.

pub mod agent;
pub mod apply;
pub mod backend;
pub mod eval;
pub mod mutate;
pub mod prompt;
pub mod tasks;
pub mod validate;

use std::path::{Path, PathBuf};

/// Repo root: `CADENCE_ROOT`, else the parent of this crate's directory.
pub fn repo_root() -> PathBuf {
    if let Ok(r) = std::env::var("CADENCE_ROOT") {
        return PathBuf::from(r);
    }
    let here = Path::new(env!("CARGO_MANIFEST_DIR"));
    here.parent().map(|p| p.to_path_buf()).unwrap_or_else(|| here.to_path_buf())
}
