//! The promptable editing agent: instruction in, validated comp out.
//! Generation and validation are separable so the GPU box can generate and
//! the Mac (which has LÖVE + ffmpeg) can validate.

use anyhow::{Context, Result};
use serde::{Deserialize, Serialize};
use std::path::Path;

use crate::apply;
use crate::backend::{Backend, GenParams, Generation};
use crate::prompt;
use crate::validate::{self, Facts};

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct Attempt {
    pub n: usize,
    pub prompt_chars: usize,
    pub generation: Generation,
    pub parsed: Option<String>,
    pub applied: bool,
    pub verify: Option<validate::VerifyResult>,
    pub failure: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct EditOutcome {
    pub ok: bool,
    pub source: String,
    pub attempts: Vec<Attempt>,
}

/// Build the first-turn prompt for an instruction against a comp.
pub fn build_prompt(instruction: &str, source: &str, facts: Option<&Facts>) -> String {
    prompt::chat_format(prompt::SYSTEM, &[(prompt::user_turn(instruction, facts, source, None), String::new())])
}

/// One generation, no validation (what the GPU box runs).
pub fn generate_once(backend: &mut dyn Backend, instruction: &str, source: &str, facts: Option<&Facts>, params: &GenParams) -> Result<(String, Generation)> {
    let p = build_prompt(instruction, source, facts);
    let g = backend.generate(&p, params)?;
    Ok((p, g))
}

/// Apply a model reply to `source`; error text is the feedback for a retry.
pub fn apply_reply(source: &str, reply: &str) -> Result<String> {
    let edit = apply::parse(reply)?;
    apply::apply(source, &edit)
}

/// Full loop: generate → apply → verify, retrying with findings as feedback.
pub fn edit(backend: &mut dyn Backend, comp: &Path, instruction: &str, params: &GenParams, attempts: usize, baseline_errors: Option<usize>) -> Result<EditOutcome> {
    let source = std::fs::read_to_string(comp).with_context(|| comp.display().to_string())?;
    let facts = validate::facts(comp).ok();
    let baseline = match baseline_errors {
        Some(b) => b,
        None => facts.as_ref().map(|f| f.lint_errors).unwrap_or(0),
    };
    let mut out = EditOutcome { source: source.clone(), ..Default::default() };
    let mut feedback: Option<String> = None;
    for n in 1..=attempts.max(1) {
        let user = prompt::user_turn(instruction, facts.as_ref(), &source, feedback.as_deref());
        let p = prompt::chat_format(prompt::SYSTEM, &[(user, String::new())]);
        let g = backend.generate(&p, params)?;
        let mut a = Attempt { n, prompt_chars: p.len(), generation: g.clone(), ..Default::default() };
        let new_src = match apply_reply(&source, &g.text) {
            Ok(s) => { a.parsed = Some(g.text.clone()); a.applied = true; s }
            Err(e) => {
                a.failure = Some(format!("apply: {e}"));
                feedback = Some(format!("The edit blocks could not be applied: {e}"));
                out.attempts.push(a);
                continue;
            }
        };
        let scratch = validate::scratch_comp(comp, &new_src, &format!("a{n}"))?;
        let v = validate::verify(&scratch);
        let _ = std::fs::remove_file(&scratch);
        match v {
            Ok(v) => {
                let ok = v.compile_ok && v.errors <= baseline;
                a.verify = Some(v.clone());
                if ok {
                    out.ok = true;
                    out.source = new_src;
                    out.attempts.push(a);
                    return Ok(out);
                }
                let why = if !v.compile_ok {
                    format!("compile failed: {}", v.error.clone().unwrap_or_default())
                } else {
                    format!("lint errors went from {baseline} to {}:\n{}", v.errors, v.findings.iter().filter(|f| f.starts_with("error")).cloned().collect::<Vec<_>>().join("\n"))
                };
                a.failure = Some(why.clone());
                feedback = Some(why);
            }
            Err(e) => {
                a.failure = Some(format!("verify: {e}"));
                feedback = Some(format!("verification could not run: {e}"));
            }
        }
        out.attempts.push(a);
    }
    Ok(out)
}
