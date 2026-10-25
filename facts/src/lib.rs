//! The fact log, in Rust.
//!
//! ```prolog
//! holds(Fluent, T0, T1).      % the fluent is true over [T0, T1)
//! happens(Event, T).          % the event occurred at T
//! src(Fact, Producer, Conf).  % who measured that fact, and how sure
//! ```
//!
//! One rule carries the design: **a fact with no `src` line is exact**. A comp
//! knows its own truth, so a lift states facts flatly; footage is only ever
//! measured, so every perceived line names its producer. A reader never has to
//! ask which kind it is holding.
//!
//! This is the first tranche of the port out of `vision/cadence_vision`: the
//! grammar and the query, which are pure logic and need no model. The producers
//! follow, in the order their models are already ONNX. Python stays as the
//! offline export tool and the score-parity baseline, per the runtime decision
//! in CHECKS.md (2026-08-01): in-process, no Python sidecar.
//!
//! A query is a fact with holes in it. `happens(release(A, B), T)` reads every
//! release out of a log and says what A, B and T were. There is no second query
//! language, which is the point: whoever can read the log can query it.

use std::collections::HashMap;
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

/// What a line says: the head decides how its arguments are read.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Head {
    Holds,
    Happens,
    Entity,
    Clip,
    Other,
}

impl Head {
    fn of(name: &str) -> Head {
        match name {
            "holds" => Head::Holds,
            "happens" => Head::Happens,
            "entity" => Head::Entity,
            "clip" => Head::Clip,
            _ => Head::Other,
        }
    }

    pub fn as_str(self) -> &'static str {
        match self {
            Head::Holds => "holds",
            Head::Happens => "happens",
            Head::Entity => "entity",
            Head::Clip => "clip",
            Head::Other => "other",
        }
    }
}

/// One fact, with the provenance from the `src` line that names it folded in.
#[derive(Debug, Clone, PartialEq)]
pub struct Fact {
    /// The line as written, which is also how a `src` line refers to it.
    pub raw: String,
    pub head: Head,
    /// The fluent or event: `visible(e1)`, `text(t1, "SALE")`, `release(e1, e2)`.
    pub term: String,
    /// `holds`: the interval.
    pub t0: Option<f64>,
    pub t1: Option<f64>,
    /// `happens`: the instant.
    pub t: Option<f64>,
    pub producer: Option<String>,
    pub conf: Option<f64>,
}

impl Fact {
    /// No `src` line named it, and that absence is the claim that it is exact.
    pub fn exact(&self) -> bool {
        self.producer.is_none()
    }
}

/// Split on commas that are not inside brackets or a quoted string.
fn split_args(inside: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut depth = 0i32;
    let mut quoted = false;
    let mut start = 0usize;
    let bytes: Vec<char> = inside.chars().collect();
    let mut i = 0usize;
    while i < bytes.len() {
        let ch = bytes[i];
        if quoted {
            if ch == '\\' {
                i += 2;
                continue;
            }
            if ch == '"' {
                quoted = false;
            }
            i += 1;
            continue;
        }
        match ch {
            '"' => quoted = true,
            '(' => depth += 1,
            ')' => depth -= 1,
            ',' if depth == 0 => {
                out.push(bytes[start..i].iter().collect::<String>().trim().to_string());
                start = i + 1;
            }
            _ => {}
        }
        i += 1;
    }
    out.push(bytes[start..].iter().collect::<String>().trim().to_string());
    out
}

/// A compound term split into its name and its arguments: `f(a, b)` -> (f, [a, b]).
fn head_of(line: &str) -> Option<(String, Vec<String>)> {
    let line = line.trim();
    let open = line.find('(')?;
    if open == 0 || !line.ends_with(')') {
        return None;
    }
    let inside = &line[open + 1..line.len() - 1];
    Some((line[..open].to_string(), split_args(inside)))
}

fn num(text: &str) -> Option<f64> {
    text.trim().parse::<f64>().ok()
}

/// Every fact in a log, with `src` lines folded into the facts they name.
pub fn parse(text: &str) -> Vec<Fact> {
    let mut facts: Vec<Fact> = Vec::new();
    let mut provenance: Vec<(String, String, f64)> = Vec::new();

    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('%') {
            continue;
        }
        let body = trimmed.strip_suffix('.').unwrap_or(trimmed).trim();
        let Some((name, args)) = head_of(body) else { continue };

        if name == "src" {
            if args.len() >= 3 {
                let conf = num(&args[args.len() - 1]).unwrap_or(0.0);
                let producer = args[args.len() - 2].clone();
                let of = args[..args.len() - 2].join(", ");
                if !of.is_empty() && !producer.is_empty() {
                    provenance.push((of, producer, conf));
                }
            }
            continue;
        }

        let head = Head::of(&name);
        let mut fact = Fact {
            raw: body.to_string(),
            head,
            term: args.first().cloned().unwrap_or_default(),
            t0: None,
            t1: None,
            t: None,
            producer: None,
            conf: None,
        };
        match head {
            Head::Holds => {
                fact.t0 = args.get(1).and_then(|a| num(a));
                fact.t1 = args.get(2).and_then(|a| num(a));
            }
            Head::Happens => fact.t = args.get(1).and_then(|a| num(a)),
            // `entity(e1, "bottle", seed(8.520))` says the whole line.
            Head::Entity | Head::Clip => fact.term = args.join(", "),
            Head::Other => {}
        }
        facts.push(fact);
    }

    for (of, producer, conf) in provenance {
        for fact in facts.iter_mut() {
            if fact.raw == of {
                fact.producer = Some(producer.clone());
                fact.conf = Some(conf);
            }
        }
    }
    facts
}

/// Times are written to the millisecond, and a pattern binds the written form.
fn t_str(value: Option<f64>) -> String {
    match value {
        Some(v) => format!("{v:.3}"),
        None => String::new(),
    }
}

/// A variable is an unquoted atom starting with a capital, or `_` for "don't care".
fn is_var(token: &str) -> bool {
    let mut chars = token.chars();
    match chars.next() {
        Some(first) if first == '_' || first.is_ascii_uppercase() => {
            chars.all(|c| c.is_alphanumeric() || c == '_')
        }
        _ => false,
    }
}

/// A variable binds a string's value, not its quotes: `word(W, S)` gives S the
/// word that was said. The quotes are the grammar's, not the datum's.
fn unquote(text: &str) -> String {
    let trimmed = text.trim();
    if trimmed.len() >= 2 && trimmed.starts_with('"') && trimmed.ends_with('"') {
        return trimmed[1..trimmed.len() - 1].replace("\\\"", "\"");
    }
    trimmed.to_string()
}

/// Unify one pattern argument against one actual argument.
fn unify(pattern: &str, actual: &str, bindings: &mut HashMap<String, String>) -> bool {
    let p = pattern.trim();
    let a = actual.trim();
    if p == "_" {
        return true;
    }
    if is_var(p) {
        let value = unquote(a);
        return match bindings.get(p) {
            Some(bound) => *bound == value,
            None => {
                bindings.insert(p.to_string(), value);
                true
            }
        };
    }
    match (head_of(p), head_of(a)) {
        (Some((pn, pa)), Some((an, aa))) => {
            if pn != an || pa.len() != aa.len() {
                return false;
            }
            pa.iter().zip(aa.iter()).all(|(x, y)| unify(x, y, bindings))
        }
        (None, None) => p == a,
        _ => false,
    }
}

/// One match, with what each variable bound to.
#[derive(Debug, Clone)]
pub struct Hit {
    pub fact: Fact,
    pub bindings: HashMap<String, String>,
}

/// Every fact matching a pattern written in the log's own grammar.
///
/// A perceived hit below `min_conf` is dropped; an exact fact is never dropped,
/// because silence about confidence means certainty, not absence.
pub fn query(facts: &[Fact], pattern: &str, min_conf: f64) -> Vec<Hit> {
    let Some((name, args)) = head_of(pattern.trim().trim_end_matches('.')) else {
        return Vec::new();
    };
    let head = Head::of(&name);
    let mut hits = Vec::new();
    for fact in facts {
        if fact.head != head {
            continue;
        }
        if !fact.exact() && fact.conf.unwrap_or(0.0) < min_conf {
            continue;
        }
        let actual: Vec<String> = match head {
            Head::Holds => vec![fact.term.clone(), t_str(fact.t0), t_str(fact.t1)],
            Head::Happens => vec![fact.term.clone(), t_str(fact.t)],
            _ => vec![fact.term.clone()],
        };
        if args.len() != actual.len() {
            continue;
        }
        let mut bindings = HashMap::new();
        if args
            .iter()
            .zip(actual.iter())
            .all(|(p, a)| unify(p, a, &mut bindings))
        {
            hits.push(Hit { fact: fact.clone(), bindings });
        }
    }
    hits
}

/// Everything true at a time: intervals containing it, events within `eps`.
pub fn at(facts: &[Fact], t: f64, eps: f64) -> Vec<Fact> {
    facts
        .iter()
        .filter(|fact| match fact.head {
            Head::Holds => fact.t0.unwrap_or(0.0) <= t && t < fact.t1.unwrap_or(0.0),
            Head::Happens => (fact.t.unwrap_or(0.0) - t).abs() <= eps,
            _ => false,
        })
        .cloned()
        .collect()
}

/// Write facts back out, with a `src` line under every measured one.
pub fn to_text(facts: &[Fact]) -> String {
    let mut out = String::new();
    for fact in facts {
        let line = match fact.head {
            Head::Holds => format!("holds({}, {}, {})", fact.term, t_str(fact.t0), t_str(fact.t1)),
            Head::Happens => format!("happens({}, {})", fact.term, t_str(fact.t)),
            _ => format!("{}({})", fact.head.as_str(), fact.term),
        };
        out.push_str(&line);
        out.push_str(".\n");
        if let (Some(producer), Some(conf)) = (&fact.producer, fact.conf) {
            out.push_str(&format!("src({line}, {producer}, {conf:.2}).\n"));
        }
    }
    out
}

/// A parent's log as a clip's own: times moved to the clip's zero, anything
/// outside the window dropped, anything crossing its edge clamped to it.
pub fn shift(facts: &[Fact], offset: f64, from: f64, to: f64) -> Vec<Fact> {
    let mut out = Vec::new();
    for fact in facts {
        let mut moved = fact.clone();
        match fact.head {
            Head::Holds => {
                let t0 = fact.t0.unwrap_or(0.0).max(from);
                let t1 = fact.t1.unwrap_or(0.0).min(to);
                if t1 <= t0 {
                    continue;
                }
                moved.t0 = Some(t0 - offset);
                moved.t1 = Some(t1 - offset);
            }
            Head::Happens => {
                let t = fact.t.unwrap_or(0.0);
                if t < from || t > to {
                    continue;
                }
                moved.t = Some(t - offset);
            }
            _ => {}
        }
        out.push(moved);
    }
    out
}

// ---- C ABI -----------------------------------------------------------------
// Loaded by LuaJIT FFI like decode/embed, so the Lua side reads a log without a
// sidecar. Answers are JSON text, because that is what the callers already read.
//
//   cf_query(log, pattern, min_conf) -> JSON array of hits, or "[]"
//   cf_at(log, t, eps)               -> JSON array of facts
//   cf_free(ptr)                     -> release a string this library returned

fn json_escape(text: &str) -> String {
    let mut out = String::with_capacity(text.len() + 2);
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04x}", c as u32)),
            c => out.push(c),
        }
    }
    out
}

fn fact_json(fact: &Fact) -> String {
    let mut parts = vec![
        format!("\"fact\":\"{}\"", json_escape(&fact.raw)),
        format!("\"term\":\"{}\"", json_escape(&fact.term)),
        format!("\"head\":\"{}\"", fact.head.as_str()),
    ];
    if let Some(t0) = fact.t0 {
        parts.push(format!("\"t0\":{t0}"));
    }
    if let Some(t1) = fact.t1 {
        parts.push(format!("\"t1\":{t1}"));
    }
    if let Some(t) = fact.t {
        parts.push(format!("\"t\":{t}"));
    }
    match (&fact.producer, fact.conf) {
        (Some(producer), Some(conf)) => {
            parts.push(format!("\"producer\":\"{}\"", json_escape(producer)));
            parts.push(format!("\"conf\":{conf}"));
        }
        // Saying so is the point of the field.
        _ => parts.push("\"exact\":true".to_string()),
    }
    format!("{{{}}}", parts.join(","))
}

fn hand_back(text: String) -> *mut c_char {
    match CString::new(text) {
        Ok(owned) => owned.into_raw(),
        Err(_) => std::ptr::null_mut(),
    }
}

unsafe fn borrow<'a>(ptr: *const c_char) -> Option<&'a str> {
    if ptr.is_null() {
        return None;
    }
    CStr::from_ptr(ptr).to_str().ok()
}

/// Query a log. The caller owns the answer and frees it with `cf_free`.
///
/// # Safety
/// `log` and `pattern` must be valid NUL-terminated C strings.
#[no_mangle]
pub unsafe extern "C" fn cf_query(log: *const c_char, pattern: *const c_char, min_conf: f64) -> *mut c_char {
    let (Some(log), Some(pattern)) = (borrow(log), borrow(pattern)) else {
        return hand_back("[]".to_string());
    };
    let facts = parse(log);
    let rows: Vec<String> = query(&facts, pattern, min_conf)
        .iter()
        .map(|hit| {
            let mut names: Vec<&String> = hit.bindings.keys().collect();
            names.sort();
            let bindings: Vec<String> = names
                .iter()
                .map(|name| format!("\"{}\":\"{}\"", json_escape(name), json_escape(&hit.bindings[*name])))
                .collect();
            format!(
                "{{\"hit\":{},\"bindings\":{{{}}}}}",
                fact_json(&hit.fact),
                bindings.join(",")
            )
        })
        .collect();
    hand_back(format!("[{}]", rows.join(",")))
}

/// Everything true at a time.
///
/// # Safety
/// `log` must be a valid NUL-terminated C string.
#[no_mangle]
pub unsafe extern "C" fn cf_at(log: *const c_char, t: f64, eps: f64) -> *mut c_char {
    let Some(log) = borrow(log) else {
        return hand_back("[]".to_string());
    };
    let facts = parse(log);
    let rows: Vec<String> = at(&facts, t, eps).iter().map(fact_json).collect();
    hand_back(format!("[{}]", rows.join(",")))
}

/// Release a string this library returned.
///
/// # Safety
/// `ptr` must have come from this library and not been freed already.
#[no_mangle]
pub unsafe extern "C" fn cf_free(ptr: *mut c_char) {
    if !ptr.is_null() {
        drop(CString::from_raw(ptr));
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The same two logs the Python suite uses (vision/tests/test_query.py), so
    // the port is measured against the baseline rather than against itself.
    const PERCEIVED: &str = r#"
% fact log for a.mp4 — perceived
clip("a.mp4").
src(clip("a.mp4"), ffprobe, 0.99).
entity(e1, "beer bottle", seed(8.520)).
src(entity(e1, "beer bottle", seed(8.520)), detector_agreement, 0.71).
holds(visible(e1), 2.100, 10.800).
src(holds(visible(e1), 2.100, 10.800), sam2, 0.71).
holds(visible(e2), 1.000, 4.000).
src(holds(visible(e2), 1.000, 4.000), sam2, 0.30).
happens(release(e1, e2), 8.500).
src(happens(release(e1, e2), 8.500), contact_gap, 1.00).
"#;

    const EXACT: &str = r#"
% fact log for b.lua — lifted from comp state, exact (no src lines)
entity(text6, "text", node("text6")).
holds(text(text6, "TYPE"), 0.567, 1.467).
holds(visible(text6), 0.000, 3.600).
happens(text_change(text6), 0.567).
"#;

    #[test]
    fn comments_and_src_lines_are_not_facts() {
        let facts = parse(PERCEIVED);
        assert_eq!(facts.len(), 5);
        assert!(facts.iter().all(|f| !f.raw.starts_with('%')));
    }

    #[test]
    fn a_holds_line_carries_its_interval() {
        let facts = parse(PERCEIVED);
        let visible = facts.iter().find(|f| f.term == "visible(e1)").unwrap();
        assert_eq!(visible.t0, Some(2.1));
        assert_eq!(visible.t1, Some(10.8));
    }

    #[test]
    fn a_happens_line_carries_its_instant() {
        let facts = parse(PERCEIVED);
        let release = facts.iter().find(|f| f.head == Head::Happens).unwrap();
        assert_eq!(release.t, Some(8.5));
        assert_eq!(release.t0, None);
    }

    #[test]
    fn no_src_line_is_what_makes_a_fact_exact() {
        let perceived = parse(PERCEIVED);
        assert!(perceived.iter().filter(|f| f.head == Head::Holds).all(|f| !f.exact()));
        let exact = parse(EXACT);
        assert!(exact.iter().all(|f| f.exact()));
        assert!(exact.iter().all(|f| f.conf.is_none()));
    }

    #[test]
    fn provenance_names_its_producer() {
        let facts = parse(PERCEIVED);
        let visible = facts.iter().find(|f| f.term == "visible(e1)").unwrap();
        assert_eq!(visible.producer.as_deref(), Some("sam2"));
        assert_eq!(visible.conf, Some(0.71));
    }

    #[test]
    fn variables_bind_and_report_what_they_matched() {
        let facts = parse(PERCEIVED);
        let hits = query(&facts, "holds(visible(E), T0, T1)", 0.0);
        assert_eq!(hits.len(), 2);
        let first = hits.iter().find(|h| h.bindings["E"] == "e1").unwrap();
        assert_eq!(first.bindings["T0"], "2.100");
    }

    #[test]
    fn underscore_matches_anything() {
        let facts = parse(PERCEIVED);
        assert_eq!(query(&facts, "holds(_, _, _)", 0.0).len(), 2);
    }

    #[test]
    fn several_variables_in_one_term() {
        let facts = parse(PERCEIVED);
        let hits = query(&facts, "happens(release(A, B), T)", 0.0);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].bindings["A"], "e1");
        assert_eq!(hits[0].bindings["B"], "e2");
        assert_eq!(hits[0].bindings["T"], "8.500");
    }

    #[test]
    fn a_variable_binds_a_strings_value_not_its_quotes() {
        let facts = parse("happens(word(w1, \"hello\"), 1.310).\n");
        let hits = query(&facts, "happens(word(W, S), T)", 0.0);
        assert_eq!(hits.len(), 1);
        assert_eq!(hits[0].bindings["S"], "hello");
        assert_eq!(hits[0].bindings["W"], "w1");
    }

    #[test]
    fn a_quoted_string_is_always_a_literal() {
        let facts = parse(EXACT);
        assert_eq!(query(&facts, "holds(text(E, \"TYPE\"), T0, T1)", 0.0).len(), 1);
        assert_eq!(query(&facts, "holds(text(E, \"OTHER\"), T0, T1)", 0.0).len(), 0);
    }

    #[test]
    fn confidence_filters_perceived_hits_and_never_exact_ones() {
        let perceived = parse(PERCEIVED);
        assert_eq!(query(&perceived, "holds(visible(E), _, _)", 0.0).len(), 2);
        assert_eq!(query(&perceived, "holds(visible(E), _, _)", 0.5).len(), 1);
        // An exact fact has no confidence, and silence there means certainty.
        let exact = parse(EXACT);
        assert_eq!(query(&exact, "holds(visible(E), _, _)", 0.9).len(), 1);
    }

    #[test]
    fn what_is_true_at_a_time() {
        let facts = parse(PERCEIVED);
        let now = at(&facts, 3.0, 0.04);
        assert!(now.iter().any(|f| f.term == "visible(e1)"));
        assert!(now.iter().any(|f| f.term == "visible(e2)"));
        let later = at(&facts, 5.0, 0.04);
        assert!(!later.iter().any(|f| f.term == "visible(e2)"));
        assert!(at(&facts, 8.51, 0.04).iter().any(|f| f.head == Head::Happens));
    }

    #[test]
    fn a_clip_inherits_its_parents_facts_at_its_own_zero() {
        let facts = parse(PERCEIVED);
        let clip = shift(&facts, 2.0, 2.0, 7.0);
        let visible = clip.iter().find(|f| f.term == "visible(e1)").unwrap();
        // The log is written to the millisecond, so that is the precision a
        // shifted time is compared at; binary floats do not land on 0.1 exactly.
        assert_eq!(t_str(visible.t0), "0.100");
        assert_eq!(t_str(visible.t1), "5.000");
        assert_eq!(visible.producer.as_deref(), Some("sam2"));
        assert!(!clip.iter().any(|f| f.head == Head::Happens));
    }

    #[test]
    fn writing_a_log_back_keeps_the_src_lines() {
        let facts = parse(PERCEIVED);
        let text = to_text(&facts);
        assert!(text.contains("holds(visible(e1), 2.100, 10.800)."));
        assert!(text.contains("src(holds(visible(e1), 2.100, 10.800), sam2, 0.71)."));
        // and an exact log comes back with none
        let exact = to_text(&parse(EXACT));
        assert!(!exact.contains("src("));
        // round trip: reading what we wrote gives the same facts back
        assert_eq!(parse(&text).len(), facts.len());
    }

    #[test]
    fn the_c_abi_answers_json() {
        let log = CString::new(PERCEIVED).unwrap();
        let pattern = CString::new("happens(release(A, B), T)").unwrap();
        unsafe {
            let answer = cf_query(log.as_ptr(), pattern.as_ptr(), 0.0);
            let text = CStr::from_ptr(answer).to_str().unwrap().to_string();
            cf_free(answer);
            assert!(text.contains("\"A\":\"e1\""));
            assert!(text.contains("\"producer\":\"contact_gap\""));

            let now = cf_at(log.as_ptr(), 3.0, 0.04);
            let text = CStr::from_ptr(now).to_str().unwrap().to_string();
            cf_free(now);
            assert!(text.contains("visible(e1)"));
        }
    }
}
