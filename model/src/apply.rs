//! Turn model output into a new comp source.
//! Accepts: SEARCH/REPLACE blocks (preferred), a full file in a ```lua fence,
//! or the literal NO_CHANGE.

use anyhow::{bail, Result};
use regex::Regex;

#[derive(Debug, Clone, PartialEq)]
pub enum Edit {
    NoChange,
    Blocks(Vec<(String, String)>),
    FullFile(String),
}

pub fn parse(output: &str) -> Result<Edit> {
    let text = output.trim();
    if text.starts_with("NO_CHANGE") || text.is_empty() {
        return Ok(Edit::NoChange);
    }
    let block_re = Regex::new(
        r"(?s)<{7} SEARCH\n(.*?)\n?={7}\n(.*?)\n?>{7} REPLACE",
    )
    .unwrap();
    let mut blocks = Vec::new();
    for c in block_re.captures_iter(text) {
        blocks.push((c[1].to_string(), c[2].to_string()));
    }
    if !blocks.is_empty() {
        return Ok(Edit::Blocks(blocks));
    }
    let fence_re = Regex::new(r"(?s)```(?:lua)?\n(.*?)```").unwrap();
    if let Some(c) = fence_re.captures(text) {
        let body = c[1].to_string();
        if body.contains("e.comp") || body.contains("return") {
            return Ok(Edit::FullFile(body));
        }
    }
    if text.contains("e.comp {") && text.contains("return") {
        return Ok(Edit::FullFile(text.to_string()));
    }
    bail!("no edit blocks, no lua fence, not NO_CHANGE in model output")
}

/// Apply an edit to `source`. Blocks are applied in order; each SEARCH must
/// match exactly once (verbatim first, then with per-line trimmed whitespace).
pub fn apply(source: &str, edit: &Edit) -> Result<String> {
    match edit {
        Edit::NoChange => Ok(source.to_string()),
        Edit::FullFile(f) => Ok(f.clone()),
        Edit::Blocks(blocks) => {
            let mut cur = source.to_string();
            for (i, (search, replace)) in blocks.iter().enumerate() {
                cur = replace_once(&cur, search, replace)
                    .ok_or_else(|| anyhow::anyhow!("block {}: SEARCH text not found exactly once:\n{search}", i + 1))?;
            }
            Ok(cur)
        }
    }
}

fn replace_once(hay: &str, needle: &str, rep: &str) -> Option<String> {
    if !needle.is_empty() {
        let n = hay.matches(needle).count();
        if n == 1 {
            return Some(hay.replacen(needle, rep, 1));
        }
        if n > 1 {
            return None;
        }
    }
    // whitespace-tolerant: match by trimmed lines
    let hay_lines: Vec<&str> = hay.lines().collect();
    let nd: Vec<&str> = needle.lines().map(|l| l.trim()).filter(|l| !l.is_empty()).collect();
    if nd.is_empty() {
        return None;
    }
    let mut found: Option<(usize, usize)> = None;
    let mut i = 0;
    while i < hay_lines.len() {
        // skip blank hay lines inside the window like the needle does
        let mut j = i;
        let mut k = 0;
        let mut ok = true;
        while k < nd.len() {
            if j >= hay_lines.len() {
                ok = false;
                break;
            }
            let hl = hay_lines[j].trim();
            if hl.is_empty() {
                j += 1;
                continue;
            }
            if hl != nd[k] {
                ok = false;
                break;
            }
            j += 1;
            k += 1;
        }
        if ok {
            if found.is_some() {
                return None;
            }
            found = Some((i, j));
        }
        i += 1;
    }
    let (a, b) = found?;
    // keep the indentation of the first replaced line for the replacement
    let indent: String = hay_lines[a].chars().take_while(|c| c.is_whitespace()).collect();
    let rep_lines: Vec<String> = rep
        .lines()
        .map(|l| {
            let t = l.trim_start();
            if t.is_empty() { String::new() } else if l.starts_with(char::is_whitespace) { l.to_string() } else { format!("{indent}{t}") }
        })
        .collect();
    let mut out: Vec<String> = hay_lines[..a].iter().map(|s| s.to_string()).collect();
    out.extend(rep_lines);
    out.extend(hay_lines[b..].iter().map(|s| s.to_string()));
    let mut s = out.join("\n");
    if hay.ends_with('\n') {
        s.push('\n');
    }
    Some(s)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_blocks() {
        let out = "<<<<<<< SEARCH\n  x = 1,\n=======\n  x = 2,\n>>>>>>> REPLACE\n";
        let e = parse(out).unwrap();
        assert_eq!(e, Edit::Blocks(vec![("  x = 1,".into(), "  x = 2,".into())]));
        assert_eq!(apply("local a = {\n  x = 1,\n}\n", &e).unwrap(), "local a = {\n  x = 2,\n}\n");
    }

    #[test]
    fn whitespace_tolerant() {
        let e = Edit::Blocks(vec![("x = 1,".into(), "x = 3,".into())]);
        assert_eq!(apply("t {\n    x = 1,\n}\n", &e).unwrap(), "t {\n    x = 3,\n}\n");
    }

    #[test]
    fn ambiguous_fails() {
        let e = Edit::Blocks(vec![("x = 1,".into(), "x = 3,".into())]);
        assert!(apply("x = 1,\nx = 1,\n", &e).is_err());
    }

    #[test]
    fn no_change_and_fence() {
        assert_eq!(parse("NO_CHANGE").unwrap(), Edit::NoChange);
        let f = parse("```lua\nreturn e.comp { }\n```").unwrap();
        assert!(matches!(f, Edit::FullFile(_)));
    }
}
