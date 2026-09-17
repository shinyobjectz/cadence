//! Validation against the real toolchain. Everything here shells out to
//! `bin/cadence` (LÖVE runtime, ffmpeg) or `luajit model/lua/props.lua`, so
//! it runs on the Mac (or any box with the renderer), not on the GPU box.

use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use crate::repo_root;

/// What the toolchain knows about a comp before editing (goes into the prompt).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Facts {
    pub fps: f64,
    pub duration: f64,
    pub nodes: Vec<(String, String)>,
    pub lint_errors: usize,
    pub lint_warnings: usize,
    pub findings: Vec<String>,
}

impl Facts {
    pub fn summary(&self) -> String {
        let mut s = format!("- duration {}s, fps {}\n- nodes: ", self.duration, self.fps);
        s.push_str(
            &self.nodes.iter().map(|(id, k)| format!("{id} ({k})")).collect::<Vec<_>>().join(", "),
        );
        s.push('\n');
        if !self.findings.is_empty() {
            s.push_str("- lint findings on the current file:\n");
            for f in self.findings.iter().take(12) {
                s.push_str("  - ");
                s.push_str(f);
                s.push('\n');
            }
        }
        s
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct VerifyResult {
    pub status: String,
    pub compile_ok: bool,
    pub errors: usize,
    pub warnings: usize,
    pub findings: Vec<String>,
    pub error: Option<String>,
    pub ms: u128,
}

/// Node states at given times: at[t][node][prop] = json value.
pub type Props = BTreeMap<String, BTreeMap<String, BTreeMap<String, serde_json::Value>>>;

#[derive(Debug, Clone, Deserialize)]
struct PropsOut {
    fps: f64,
    duration: f64,
    nodes: Vec<PropsNode>,
    at: Props,
}
#[derive(Debug, Clone, Deserialize)]
struct PropsNode {
    id: String,
    kind: String,
}

fn cadence_bin() -> PathBuf {
    repo_root().join("bin").join("cadence")
}

fn run_json(args: &[&str], cwd: &Path) -> Result<serde_json::Value> {
    let out = Command::new(cadence_bin()).args(args).current_dir(cwd).output()
        .with_context(|| format!("running bin/cadence {}", args.join(" ")))?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    // the CLI prints one JSON document; tolerate leading log lines
    let start = stdout.find('{').unwrap_or(0);
    serde_json::from_str(&stdout[start..])
        .with_context(|| format!("bin/cadence {} did not print JSON:\n{}\n{}", args.join(" "), stdout, String::from_utf8_lossy(&out.stderr)))
}

/// `bin/cadence verify --json` on a comp path (relative to root or absolute).
pub fn verify(comp: &Path) -> Result<VerifyResult> {
    let t0 = std::time::Instant::now();
    let root = repo_root();
    let v = run_json(&["verify", comp.to_str().unwrap(), "--json"], &root)?;
    let mut r = VerifyResult {
        status: v["status"].as_str().unwrap_or("").to_string(),
        errors: v["_meta"]["errors"].as_u64().unwrap_or(0) as usize,
        warnings: v["_meta"]["warnings"].as_u64().unwrap_or(0) as usize,
        error: v["error"].as_str().map(|s| s.to_string()),
        ms: t0.elapsed().as_millis(),
        ..Default::default()
    };
    r.compile_ok = v["steps"].as_array().map(|steps| {
        steps.iter().any(|s| s["tier"] == "compile" && s["status"] == "ok")
    }).unwrap_or(false);
    if let Some(f) = v["findings"].as_array() {
        for x in f {
            let sev = x["severity"].as_str().unwrap_or("?");
            let code = x["code"].as_str().unwrap_or("?");
            let node = x["node"].as_str().map(|n| format!(" [{n}]")).unwrap_or_default();
            let detail = x["detail"].as_str().unwrap_or("");
            r.findings.push(format!("{sev} {code}{node}: {detail}"));
        }
    }
    Ok(r)
}

/// Per-frame md5 list from `bin/cadence hash`.
pub fn frame_hashes(comp: &Path) -> Result<Vec<String>> {
    let root = repo_root();
    let out = Command::new(cadence_bin()).args(["hash", comp.to_str().unwrap()]).current_dir(&root).output()?;
    let stdout = String::from_utf8_lossy(&out.stdout);
    let mut v = Vec::new();
    for line in stdout.lines() {
        if let Some(rest) = line.strip_prefix("FRAME ") {
            if let Some((_, h)) = rest.split_once(' ') {
                v.push(h.trim().to_string());
            }
        }
    }
    if v.is_empty() {
        bail!("bin/cadence hash produced no frames: {}", String::from_utf8_lossy(&out.stderr).lines().take(3).collect::<Vec<_>>().join(" | "));
    }
    Ok(v)
}

/// Node states at times (host-free, luajit).
pub fn props(comp: &Path, times: &[f64]) -> Result<(Facts, Props)> {
    let root = repo_root();
    let mut cmd = Command::new("luajit");
    cmd.arg(root.join("model/lua/props.lua")).arg(&root).arg(comp).current_dir(&root);
    for t in times {
        cmd.arg(format!("{t}"));
    }
    let out = cmd.output().context("running luajit (brew install luajit)")?;
    if !out.status.success() {
        bail!("props.lua failed: {}", String::from_utf8_lossy(&out.stderr));
    }
    let po: PropsOut = serde_json::from_slice(&out.stdout).context("props.lua output")?;
    let facts = Facts {
        fps: po.fps,
        duration: po.duration,
        nodes: po.nodes.into_iter().map(|n| (n.id, n.kind)).collect(),
        ..Default::default()
    };
    Ok((facts, po.at))
}

/// Facts for the prompt: props (ids/kinds) + lint findings from verify.
pub fn facts(comp: &Path) -> Result<Facts> {
    let (mut f, _) = props(comp, &[])?;
    if let Ok(v) = verify(comp) {
        f.lint_errors = v.errors;
        f.lint_warnings = v.warnings;
        f.findings = v.findings;
    }
    Ok(f)
}

/// Write `source` next to the original so relative asset paths keep working.
pub fn scratch_comp(original: &Path, source: &str, tag: &str) -> Result<PathBuf> {
    let dir = original.parent().unwrap_or(Path::new("."));
    let stem = original.file_stem().and_then(|s| s.to_str()).unwrap_or("comp");
    // pid in the name: concurrent runs (two evals, gen-tasks beside an eval) must not share scratch files
    let p = dir.join(format!(".{stem}.{tag}.{}.model.lua", std::process::id()));
    std::fs::write(&p, source)?;
    Ok(p)
}

/// Compare a JSON prop value against an expectation.
pub fn compare(actual: &serde_json::Value, op: &str, expect: &serde_json::Value) -> bool {
    let num = |v: &serde_json::Value| v.as_f64();
    match op {
        "==" | "eq" => actual == expect || (num(actual).zip(num(expect)).map(|(a, b)| (a - b).abs() < 1e-3).unwrap_or(false)),
        "!=" | "ne" => !compare(actual, "==", expect),
        ">=" => num(actual).zip(num(expect)).map(|(a, b)| a >= b - 1e-6).unwrap_or(false),
        "<=" => num(actual).zip(num(expect)).map(|(a, b)| a <= b + 1e-6).unwrap_or(false),
        ">" => num(actual).zip(num(expect)).map(|(a, b)| a > b).unwrap_or(false),
        "<" => num(actual).zip(num(expect)).map(|(a, b)| a < b).unwrap_or(false),
        "contains" => actual.as_str().zip(expect.as_str()).map(|(a, b)| a.contains(b)).unwrap_or(false),
        _ => false,
    }
}
