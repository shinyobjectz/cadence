//! Editorial VO resolve: prerender, forced alignment, word timing sync.

use std::path::Path;
use std::process::Command;

use serde::{Deserialize, Serialize};
use sha1::{Digest, Sha1};

use crate::jobs::ellua_bin;
use crate::project::{open_editor_doc, save_editor_doc};

use crate::jobs::generate_local_vo;

pub const DEFAULT_VO_PROVIDER: &str = "pocket-tts";
const ALIGN_VERSION: &str = "speech-regions-v1";

#[derive(Clone, Debug, Serialize)]
pub struct EnsureVoResult {
    pub regenerated: bool,
    pub aligned: bool,
    pub vo_path: Option<String>,
    pub vo_key: Option<String>,
    pub script: String,
}

#[derive(Clone, Debug, Deserialize)]
struct AlignFile {
    words: Vec<AlignWord>,
}

#[derive(Clone, Debug, Deserialize)]
struct AlignWord {
    text: String,
    #[serde(default, alias = "t0")]
    start: f64,
    #[serde(default, alias = "t1")]
    end: f64,
}

#[derive(Clone, Copy)]
struct DocWordRef {
    line_index: usize,
    word_index: usize,
}

pub fn vo_cache_key(script: &str, provider: &str) -> String {
    Sha1::digest(format!("{script}|{provider}").as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn vo_align_cache_key(script: &str, provider: &str) -> String {
    Sha1::digest(format!("{script}|{provider}|{ALIGN_VERSION}").as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn transcript_script_from_doc(doc: &serde_json::Value) -> String {
    let Some(lines) = doc.get("lines").and_then(|v| v.as_array()) else {
        return String::new();
    };
    let mut parts = Vec::new();
    for line in lines {
        let Some(words) = line.get("words").and_then(|v| v.as_array()) else {
            continue;
        };
        let mut line_words = Vec::new();
        for word in words {
            if let Some(text) = word.get("text").and_then(|v| v.as_str()) {
                let t = text.trim();
                if !t.is_empty() {
                    line_words.push(t);
                }
            }
        }
        if !line_words.is_empty() {
            parts.push(line_words.join(" "));
        }
    }
    parts.join(" ").split_whitespace().collect::<Vec<_>>().join(" ")
}

fn vo_file_exists(project: &Path, rel: &str) -> bool {
    project.join(rel).is_file()
}

fn norm_token(text: &str) -> String {
    text.to_lowercase()
        .chars()
        .filter(|c| c.is_alphanumeric())
        .collect()
}

fn collect_doc_word_refs(doc: &serde_json::Value) -> Vec<DocWordRef> {
    let mut out = Vec::new();
    let Some(lines) = doc.get("lines").and_then(|v| v.as_array()) else {
        return out;
    };
    for (line_index, line) in lines.iter().enumerate() {
        let Some(words) = line.get("words").and_then(|v| v.as_array()) else {
            continue;
        };
        for word_index in 0..words.len() {
            out.push(DocWordRef {
                line_index,
                word_index,
            });
        }
    }
    out
}

/// Map forced-alignment output onto transcript words (order + fuzzy token match).
pub fn apply_word_timings(
    doc: &mut serde_json::Value,
    aligned: &[AlignWord],
) -> Result<usize, String> {
    let refs = collect_doc_word_refs(doc);
    if refs.is_empty() {
        return Ok(0);
    }
    if aligned.is_empty() {
        return Err("alignment returned no words".into());
    }

    let lines = doc
        .get_mut("lines")
        .and_then(|v| v.as_array_mut())
        .ok_or_else(|| "doc.lines missing".to_string())?;

    let doc_tokens: Vec<String> = refs
        .iter()
        .map(|r| {
            lines[r.line_index]["words"][r.word_index]["text"]
                .as_str()
                .unwrap_or("")
                .to_string()
        })
        .map(|t| norm_token(&t))
        .collect();

    let align_tokens: Vec<String> = aligned.iter().map(|w| norm_token(&w.text)).collect();

    let mut ai = 0usize;
    let mut updated = 0usize;

    for (di, pref) in refs.iter().enumerate() {
        let want = &doc_tokens[di];
        if want.is_empty() {
            continue;
        }
        while ai < align_tokens.len() && align_tokens[ai] != *want {
            ai += 1;
        }
        if ai >= aligned.len() {
            break;
        }
        let aw = &aligned[ai];
        let word = &mut lines[pref.line_index]["words"][pref.word_index];
        word["start"] = serde_json::json!(round4(aw.start));
        word["end"] = serde_json::json!(round4(aw.end.max(aw.start)));
        updated += 1;
        ai += 1;
    }

    if updated == 0 {
        return Err("could not match alignment words to transcript".into());
    }
    Ok(updated)
}

fn round4(n: f64) -> f64 {
    (n * 10_000.0).round() / 10_000.0
}

fn parse_align_file(path: &Path) -> Result<Vec<AlignWord>, String> {
    let raw = std::fs::read_to_string(path).map_err(|e| e.to_string())?;
    let file: AlignFile = serde_json::from_str(&raw).map_err(|e| format!("align json: {e}"))?;
    Ok(file.words)
}

/// Run `cadence align` on a project VO clip.
pub fn align_vo_audio(
    project: impl AsRef<Path>,
    vo_rel: &str,
    script: &str,
) -> Result<Vec<AlignWord>, String> {
    let project = project.as_ref();
    let script = script.trim();
    if script.is_empty() {
        return Err("align: empty script".into());
    }
    let audio = project.join(vo_rel);
    if !audio.is_file() {
        return Err(format!("align: audio missing at {}", audio.display()));
    }

    let align_path = std::env::temp_dir().join(format!(
        "cadence-align-{}-{}.json",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));

    let bin = ellua_bin()?;
    let output = Command::new(&bin)
        .arg("align")
        .arg("--file")
        .arg(&audio)
        .arg("--text")
        .arg(script)
        .arg("--out")
        .arg(&align_path)
        .current_dir(project)
        .output()
        .map_err(|e| e.to_string())?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        return Err(format!("cadence align failed: {stderr}{stdout}"));
    }

    let words = parse_align_file(&align_path)?;
    let _ = std::fs::remove_file(&align_path);
    Ok(words)
}

fn resolve_vo_rel(
    project: &Path,
    doc: &serde_json::Value,
    script: &str,
    provider: &str,
    force: bool,
    key: &str,
) -> Result<(String, bool), String> {
    if !force {
        if let Some(rel) = doc.get("voPath").and_then(|v| v.as_str()) {
            if doc.get("voKey").and_then(|v| v.as_str()) == Some(key) && vo_file_exists(project, rel)
            {
                return Ok((rel.to_string(), false));
            }
        }
    }
    let rel = generate_local_vo(project, script, Some(provider))?;
    Ok((rel, true))
}

/// Prerender VO + align word timings when script/provider changed. Persists doc.json.
pub fn ensure_editor_vo(project: impl AsRef<Path>, force: bool) -> Result<EnsureVoResult, String> {
    let project = project.as_ref();
    let raw = open_editor_doc(project)?;
    let mut doc: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| format!("invalid doc.json: {e}"))?;
    let script = transcript_script_from_doc(&doc);
    if script.is_empty() {
        return Ok(EnsureVoResult {
            regenerated: false,
            aligned: false,
            vo_path: None,
            vo_key: None,
            script,
        });
    }

    let provider = doc
        .get("voProvider")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .unwrap_or(DEFAULT_VO_PROVIDER)
        .to_string();
    let key = vo_cache_key(&script, &provider);
    let align_key = vo_align_cache_key(&script, &provider);

    let (vo_rel, regenerated) = resolve_vo_rel(project, &doc, &script, &provider, force, &key)?;

    let needs_align = force
        || doc.get("voAlignKey").and_then(|v| v.as_str()) != Some(align_key.as_str());
    let mut aligned = false;

    if needs_align {
        let words = align_vo_audio(project, &vo_rel, &script)?;
        apply_word_timings(&mut doc, &words)?;
        aligned = true;
    }

    if regenerated || aligned {
        if let Some(obj) = doc.as_object_mut() {
            obj.insert("voPath".into(), serde_json::Value::String(vo_rel.clone()));
            obj.insert("voKey".into(), serde_json::Value::String(key.clone()));
            obj.insert(
                "voProvider".into(),
                serde_json::Value::String(provider.clone()),
            );
            obj.insert("voAlignKey".into(), serde_json::Value::String(align_key.clone()));
        }
        let updated = serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?;
        save_editor_doc(project, &updated)?;
    }

    Ok(EnsureVoResult {
        regenerated,
        aligned,
        vo_path: Some(vo_rel),
        vo_key: Some(key),
        script,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn vo_cache_key_changes_with_script() {
        let a = vo_cache_key("hello world", DEFAULT_VO_PROVIDER);
        let b = vo_cache_key("hello cadence", DEFAULT_VO_PROVIDER);
        assert_ne!(a, b);
    }

    #[test]
    fn apply_word_timings_updates_doc_words() {
        let mut doc = serde_json::json!({
            "lines": [{
                "id": "l1",
                "words": [
                    { "id": "w1", "text": "Meet", "start": 0, "end": 0.1 },
                    { "id": "w2", "text": "Cadence.", "start": 0.1, "end": 0.2 }
                ]
            }]
        });
        let aligned = vec![
            AlignWord {
                text: "Meet".into(),
                start: 0.12,
                end: 0.45,
            },
            AlignWord {
                text: "Cadence.".into(),
                start: 0.45,
                end: 1.02,
            },
        ];
        let n = apply_word_timings(&mut doc, &aligned).unwrap();
        assert_eq!(n, 2);
        assert_eq!(doc["lines"][0]["words"][0]["start"], 0.12);
        assert_eq!(doc["lines"][0]["words"][1]["end"], 1.02);
    }
}
