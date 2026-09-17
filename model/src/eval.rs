//! Suite runner. Two phases so the GPU box and the render box can differ:
//!   generate: task → prompt → backend reply, written to <out>/replies/<id>.txt
//!   validate: reply → apply → verify/props/frames → <out>/report.json + report.md
//! `run` does both in one process (mock, or a Mac with a Metal model).

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use crate::agent;
use crate::backend::{Backend, GenParams, Generation};
use crate::tasks::Task;
use crate::validate::{self, Props};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CheckResult {
    pub name: String,
    pub ok: bool,
    pub detail: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct TaskResult {
    pub id: String,
    pub comp: String,
    pub ok: bool,
    pub stage: String,
    pub generation: Option<Generation>,
    pub checks: Vec<CheckResult>,
    pub secs: f64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Report {
    pub backend: String,
    pub model: Option<String>,
    pub params: GenParams,
    pub tasks: usize,
    pub passed: usize,
    pub results: Vec<TaskResult>,
    pub gen_tokens: usize,
    pub gen_secs: f64,
}

pub fn replies_dir(out: &Path) -> PathBuf { out.join("replies") }

/// Phase 1: write one reply per task. Facts come from the props probe when
/// luajit is present, otherwise the prompt goes without them.
pub fn generate(backend: &mut dyn Backend, tasks: &[Task], params: &GenParams, out: &Path, root: &Path) -> Result<Vec<(String, Generation)>> {
    let rd = replies_dir(out);
    std::fs::create_dir_all(&rd)?;
    let mut gens = Vec::new();
    for t in tasks {
        let comp = t.comp_path(root);
        let source = std::fs::read_to_string(&comp).with_context(|| comp.display().to_string())?;
        let facts = validate::props(&comp, &[]).ok().map(|(f, _)| f);
        backend.set_context(&t.id);
        let (_p, g) = agent::generate_once(backend, &t.instruction, &source, facts.as_ref(), params)?;
        std::fs::write(rd.join(format!("{}.txt", t.id)), &g.text)?;
        std::fs::write(rd.join(format!("{}.meta.json", t.id)), serde_json::to_string_pretty(&g)?)?;
        eprintln!("gen  {:<40} {:>5} tok {:>6.1}s", t.id, g.gen_tokens, g.secs);
        gens.push((t.id.clone(), g));
    }
    Ok(gens)
}

fn frame_index(t: f64, fps: f64, n: usize) -> usize {
    ((t * fps).round() as usize).min(n.saturating_sub(1))
}

/// Phase 2: apply + validate every reply found in <out>/replies.
pub fn validate_replies(tasks: &[Task], out: &Path, root: &Path, backend_name: &str, model: Option<String>, params: &GenParams) -> Result<Report> {
    let rd = replies_dir(out);
    let mut report = Report { backend: backend_name.to_string(), model, params: params.clone(), tasks: tasks.len(), ..Default::default() };
    for t in tasks {
        let t0 = std::time::Instant::now();
        let mut r = TaskResult { id: t.id.clone(), comp: t.comp.clone(), ..Default::default() };
        let reply_path = rd.join(format!("{}.txt", t.id));
        let meta_path = rd.join(format!("{}.meta.json", t.id));
        if let Ok(m) = std::fs::read_to_string(&meta_path) {
            r.generation = serde_json::from_str(&m).ok();
            if let Some(g) = &r.generation {
                report.gen_tokens += g.gen_tokens;
                report.gen_secs += g.secs;
            }
        }
        let reply = match std::fs::read_to_string(&reply_path) {
            Ok(s) => s,
            Err(_) => {
                r.stage = "no_reply".into();
                r.checks.push(CheckResult { name: "reply".into(), ok: false, detail: format!("missing {}", reply_path.display()) });
                report.results.push(r);
                continue;
            }
        };
        match validate_one(t, &reply, root) {
            Ok((ok, stage, checks)) => { r.ok = ok; r.stage = stage; r.checks = checks; }
            Err(e) => { r.stage = "error".into(); r.checks.push(CheckResult { name: "harness".into(), ok: false, detail: e.to_string() }); }
        }
        r.secs = t0.elapsed().as_secs_f64();
        eprintln!("{}  {:<40} {}", if r.ok { "PASS" } else { "FAIL" }, t.id, r.stage);
        if r.ok { report.passed += 1; }
        report.results.push(r);
    }
    std::fs::create_dir_all(out)?;
    std::fs::write(out.join("report.json"), serde_json::to_string_pretty(&report)?)?;
    std::fs::write(out.join("report.md"), markdown(&report))?;
    Ok(report)
}

/// apply → verify → props → frames for one task. Returns (ok, stage, checks).
pub fn validate_one(t: &Task, reply: &str, root: &Path) -> Result<(bool, String, Vec<CheckResult>)> {
    let comp = t.comp_path(root);
    let source = std::fs::read_to_string(&comp)?;
    let mut checks = Vec::new();

    // 1. apply
    let edited = match agent::apply_reply(&source, reply) {
        Ok(s) => s,
        Err(e) => {
            checks.push(CheckResult { name: "apply".into(), ok: false, detail: e.to_string() });
            return Ok((false, "apply".into(), checks));
        }
    };
    let changed = edited != source;
    checks.push(CheckResult { name: "apply".into(), ok: true, detail: if changed { "edit applied".into() } else { "no change".into() } });
    let scratch = validate::scratch_comp(&comp, &edited, "eval")?;
    let result = (|| -> Result<(bool, String, Vec<CheckResult>)> {
        // 2. compile + lint
        let baseline = validate::verify(&comp)?;
        let v = validate::verify(&scratch)?;
        if t.checks.compile {
            checks.push(CheckResult { name: "compile".into(), ok: v.compile_ok, detail: v.error.clone().unwrap_or_else(|| format!("{} ms", v.ms)) });
            if !v.compile_ok { return Ok((false, "compile".into(), checks.clone())); }
        }
        if t.checks.no_new_lint_errors {
            let ok = v.errors <= baseline.errors;
            checks.push(CheckResult { name: "lint".into(), ok, detail: format!("errors {} (baseline {}), warnings {}", v.errors, baseline.errors, v.warnings) });
            if !ok { return Ok((false, "lint".into(), checks.clone())); }
        }
        // 3. props at t
        if !t.checks.props.is_empty() {
            let times: Vec<f64> = t.checks.props.iter().map(|p| p.t).collect();
            let (_, at): (_, Props) = validate::props(&scratch, &times)?;
            let mut all = true;
            for p in &t.checks.props {
                let key = format!("{}", p.t);
                let actual = at.get(&key).and_then(|row| row.get(&p.node)).and_then(|n| n.get(&p.prop)).cloned().unwrap_or(serde_json::Value::Null);
                let ok = validate::compare(&actual, &p.op, &p.value);
                all &= ok;
                checks.push(CheckResult { name: format!("prop {}.{} @{}", p.node, p.prop, p.t), ok, detail: format!("{actual} {} {}", p.op, p.value) });
            }
            if !all { return Ok((false, "props".into(), checks.clone())); }
        }
        // 4. frames
        let need_frames = !t.checks.frames_change.is_empty() || !t.checks.frames_hold.is_empty() || t.checks.match_reference_frames > 0.0;
        if need_frames {
            let orig = validate::frame_hashes(&comp)?;
            let new = validate::frame_hashes(&scratch)?;
            let (facts, _) = validate::props(&comp, &[])?;
            let fps = facts.fps;
            let mut all = true;
            for &tt in &t.checks.frames_change {
                let i = frame_index(tt, fps, orig.len());
                let ok = orig.get(i) != new.get(i);
                all &= ok;
                checks.push(CheckResult { name: format!("frame changes @{tt}"), ok, detail: format!("frame {i}") });
            }
            for &tt in &t.checks.frames_hold {
                let i = frame_index(tt, fps, orig.len());
                let ok = orig.get(i) == new.get(i);
                all &= ok;
                checks.push(CheckResult { name: format!("frame holds @{tt}"), ok, detail: format!("frame {i}") });
            }
            if t.checks.match_reference_frames > 0.0 && !t.reference.is_empty() {
                let ref_src = agent::apply_reply(&source, &t.reference)?;
                let ref_scratch = validate::scratch_comp(&comp, &ref_src, "ref")?;
                let rh = validate::frame_hashes(&ref_scratch);
                let _ = std::fs::remove_file(&ref_scratch);
                let rh = rh?;
                let same = new.iter().zip(rh.iter()).filter(|(a, b)| a == b).count();
                let frac = if rh.is_empty() { 0.0 } else { same as f64 / rh.len().max(new.len()) as f64 };
                let ok = frac >= t.checks.match_reference_frames - 1e-9;
                all &= ok;
                checks.push(CheckResult { name: "matches reference render".into(), ok, detail: format!("{same}/{} frames identical ({frac:.3} ≥ {})", rh.len(), t.checks.match_reference_frames) });
            }
            if !all { return Ok((false, "frames".into(), checks.clone())); }
        }
        Ok((true, "pass".into(), checks.clone()))
    })();
    let _ = std::fs::remove_file(&scratch);
    result
}

/// Both phases in one process.
pub fn run(backend: &mut dyn Backend, tasks: &[Task], params: &GenParams, out: &Path, root: &Path, model: Option<String>) -> Result<Report> {
    generate(backend, tasks, params, out, root)?;
    validate_replies(tasks, out, root, &backend.name(), model, params)
}

pub fn markdown(r: &Report) -> String {
    let mut s = format!("# cadence-model eval\n\nbackend `{}`{}  \ntasks {}  passed {}  ({:.0}%)  \ngenerated {} tokens in {:.1}s ({:.1} tok/s)\n\n| task | result | stage | checks |\n|---|---|---|---|\n",
        r.backend, r.model.as_ref().map(|m| format!(" model `{m}`")).unwrap_or_default(), r.tasks, r.passed,
        if r.tasks > 0 { 100.0 * r.passed as f64 / r.tasks as f64 } else { 0.0 },
        r.gen_tokens, r.gen_secs, if r.gen_secs > 0.0 { r.gen_tokens as f64 / r.gen_secs } else { 0.0 });
    for t in &r.results {
        let checks: Vec<String> = t.checks.iter().map(|c| format!("{} {}", if c.ok { "✅" } else { "❌" }, c.name)).collect();
        s.push_str(&format!("| {} | {} | {} | {} |\n", t.id, if t.ok { "PASS" } else { "FAIL" }, t.stage, checks.join("<br>")));
    }
    let mut by_stage: BTreeMap<String, usize> = BTreeMap::new();
    for t in &r.results { if !t.ok { *by_stage.entry(t.stage.clone()).or_default() += 1; } }
    if !by_stage.is_empty() {
        s.push_str("\nFailures by stage: ");
        s.push_str(&by_stage.iter().map(|(k, v)| format!("{k} {v}")).collect::<Vec<_>>().join(", "));
        s.push('\n');
    }
    s
}
