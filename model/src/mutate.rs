//! Synthetic tasks by render-and-mutate. Cadence renders deterministically,
//! so a mutated comp is its own ground truth: the instruction describes the
//! mutation in words, the reference is the SEARCH/REPLACE that performs it,
//! and validation asks the model's render to match the reference render.

use rand::rngs::StdRng;
use rand::seq::SliceRandom;
use rand::{Rng, SeedableRng};
use regex::Regex;
use std::path::Path;

use crate::tasks::{Checks, Task};

const EASES: &[&str] = &["linear", "quadOut", "cubicInOut", "sineInOut", "sineOut", "expoOut", "backOut"];

#[derive(Debug, Clone)]
pub struct Tween {
    pub line: String,
    pub node: String,
    pub dur: f64,
    pub props: String,
    pub ease: String,
}

pub fn find_tweens(source: &str) -> Vec<Tween> {
    let re = Regex::new(r#"t:tween\((\w+),\s*([\d.]+),\s*\{([^}]*)\},\s*"(\w+)"\)"#).unwrap();
    source
        .lines()
        .filter_map(|line| {
            re.captures(line).map(|c| Tween {
                line: line.to_string(),
                node: c[1].to_string(),
                dur: c[2].parse().unwrap_or(0.0),
                props: c[3].to_string(),
                ease: c[4].to_string(),
            })
        })
        .collect()
}

fn block(search: &str, replace: &str) -> String {
    format!("<<<<<<< SEARCH\n{search}\n=======\n{replace}\n>>>>>>> REPLACE\n")
}

fn fmt_num(x: f64) -> String {
    if (x - x.round()).abs() < 1e-9 { format!("{}", x as i64) } else { format!("{:.2}", x).trim_end_matches('0').trim_end_matches('.').to_string() }
}

/// One mutation of a tween line → (instruction, replaced line).
fn mutate_tween(tw: &Tween, rng: &mut StdRng) -> Option<(String, String)> {
    let kind = rng.gen_range(0..4);
    match kind {
        0 => {
            // duration scale
            let k = *[0.5, 1.5, 2.0].choose(rng).unwrap();
            let nd = (tw.dur * k * 100.0).round() / 100.0;
            if nd <= 0.05 { return None; }
            let new_line = tw.line.replacen(&format!("{}, {{", tw_dur_literal(&tw.line)), &format!("{}, {{", fmt_num(nd)), 1);
            if new_line == tw.line { return None; }
            let verb = if k < 1.0 { "faster" } else { "slower" };
            Some((format!("Make the {} tween on `{}` {}: change its duration from {} to {} seconds.", describe(&tw.props), tw.node, verb, fmt_num(tw.dur), fmt_num(nd)), new_line))
        }
        1 => {
            // ease swap
            let choices: Vec<&&str> = EASES.iter().filter(|e| **e != tw.ease).collect();
            let ne = *choices.choose(rng)?;
            let new_line = tw.line.replacen(&format!("\"{}\"", tw.ease), &format!("\"{ne}\""), 1);
            Some((format!("Change the easing of the `{}` tween that animates {} from {} to {}.", tw.node, describe(&tw.props), tw.ease, ne), new_line))
        }
        2 => {
            // numeric prop delta
            let re = Regex::new(r"(\w+)\s*=\s*(-?[\d.]+)").unwrap();
            let nums: Vec<(String, f64)> = re.captures_iter(&tw.props).map(|c| (c[1].to_string(), c[2].parse().unwrap_or(0.0))).collect();
            let (name, val) = nums.choose(rng)?.clone();
            let nv = match name.as_str() {
                "opacity" => if val >= 0.5 { 0.5 } else { 1.0 },
                "scale" => if val >= 1.0 { val * 0.5 } else { val * 2.0 },
                _ => val + *[-80.0, -40.0, 40.0, 80.0].choose(rng).unwrap(),
            };
            let old = format!("{} = {}", name, num_literal(&tw.props, &name)?);
            let new = format!("{} = {}", name, fmt_num(nv));
            let new_line = tw.line.replacen(&old, &new, 1);
            if new_line == tw.line { return None; }
            Some((format!("In the `{}` tween, animate `{}` to {} instead of {}.", tw.node, name, fmt_num(nv), fmt_num(val)), new_line))
        }
        _ => {
            // remove the tween — only a whole-line statement, not one inside `function() ... end`
            if tw.line.contains("function()") { return None; }
            Some((format!("Remove the {} animation on `{}` (delete that tween; keep everything else).", describe(&tw.props), tw.node), String::new()))
        }
    }
}

fn tw_dur_literal(line: &str) -> String {
    let re = Regex::new(r"t:tween\(\w+,\s*([\d.]+),").unwrap();
    re.captures(line).map(|c| c[1].to_string()).unwrap_or_default()
}

fn num_literal(props: &str, name: &str) -> Option<String> {
    let re = Regex::new(&format!(r"{name}\s*=\s*(-?[\d.]+)")).ok()?;
    re.captures(props).map(|c| c[1].to_string())
}

fn describe(props: &str) -> String {
    let re = Regex::new(r"(\w+)\s*=").unwrap();
    let names: Vec<String> = re.captures_iter(props).map(|c| c[1].to_string()).collect();
    if names.is_empty() { "property".into() } else { names.join("/") }
}

/// Generate up to `n` tasks from a comp. Reference edits are exact-line
/// blocks, so `match_reference_frames = 1.0` is the check.
pub fn from_comp(comp_rel: &str, source: &str, n: usize, seed: u64, hold_head: bool) -> Vec<Task> {
    let mut rng = StdRng::seed_from_u64(seed);
    let tweens = find_tweens(source);
    let mut out = Vec::new();
    let mut tries = 0;
    let stem = Path::new(comp_rel).file_stem().and_then(|s| s.to_str()).unwrap_or("comp").to_string();
    while out.len() < n && tries < n * 8 && !tweens.is_empty() {
        tries += 1;
        let tw = tweens.choose(&mut rng).unwrap();
        // the SEARCH must be unique in the file
        if source.matches(tw.line.trim()).count() != 1 { continue; }
        let Some((instruction, new_line)) = mutate_tween(tw, &mut rng) else { continue };
        let reference = block(tw.line.trim(), new_line.trim());
        let id = format!("{stem}_m{:02}", out.len() + 1);
        let mut checks = Checks { match_reference_frames: 1.0, ..Default::default() };
        if hold_head { checks.frames_hold = vec![0.0]; }
        out.push(Task { id, comp: comp_rel.to_string(), instruction, reference, checks, tags: vec!["synthetic".into()], source: None });
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    const SRC: &str = "s:script(function(t)\n  t:tween(box, 1.0, { x = 800 }, \"cubicInOut\")\n  t:wait(0.3)\n  t:tween(title, 0.8, { opacity = 1 }, \"sineOut\")\nend)\n";

    #[test]
    fn finds_tweens() {
        let tw = find_tweens(SRC);
        assert_eq!(tw.len(), 2);
        assert_eq!(tw[0].node, "box");
        assert_eq!(tw[1].ease, "sineOut");
    }

    #[test]
    fn generated_reference_applies() {
        let tasks = from_comp("x.lua", SRC, 6, 7, false);
        assert!(!tasks.is_empty());
        for t in tasks {
            let edited = crate::agent::apply_reply(SRC, &t.reference).unwrap();
            assert_ne!(edited, SRC, "{}", t.instruction);
        }
    }
}
