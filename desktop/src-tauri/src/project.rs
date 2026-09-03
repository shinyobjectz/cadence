use std::collections::HashMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Viewport {
    pub x: f64,
    pub y: f64,
    pub zoom: f64,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct StudioFile {
    pub version: u32,
    pub name: String,
    pub viewport: Viewport,
    pub nodes: Vec<serde_json::Value>,
    pub edges: Vec<serde_json::Value>,
    #[serde(default, rename = "lastRunHashes")]
    pub last_run_hashes: HashMap<String, String>,
}

const PROJECT_DIRS: &[&str] = &[
    "comps",
    "assets/in",
    "assets/capture",
    "renders",
    "thumbs",
];

fn default_studio(path: &Path) -> StudioFile {
    let name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("Untitled")
        .to_string();
    StudioFile {
        version: 1,
        name,
        viewport: Viewport {
            x: 0.0,
            y: 0.0,
            zoom: 1.0,
        },
        nodes: vec![],
        edges: vec![],
        last_run_hashes: HashMap::new(),
    }
}

fn require_dir(path: &Path) -> Result<(), String> {
    if path.is_dir() {
        Ok(())
    } else {
        Err(format!("not a directory: {}", path.display()))
    }
}

fn ensure_project_dirs(path: &Path) -> Result<(), String> {
    for rel in PROJECT_DIRS {
        std::fs::create_dir_all(path.join(rel)).map_err(|e| e.to_string())?;
    }
    Ok(())
}

pub fn open_project(path: impl AsRef<Path>) -> Result<StudioFile, String> {
    let path = path.as_ref();
    require_dir(path)?;
    ensure_project_dirs(path)?;
    let studio_path = path.join("studio.json");
    if studio_path.exists() {
        let contents = std::fs::read_to_string(&studio_path).map_err(|e| e.to_string())?;
        serde_json::from_str(&contents).map_err(|e| e.to_string())
    } else {
        let studio = default_studio(path);
        save_studio(path, &studio)?;
        Ok(studio)
    }
}

fn atomic_write(dest: &Path, contents: &str) -> Result<(), String> {
    let file_name = dest
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("write");
    let tmp = dest.with_file_name(format!(".{file_name}.tmp"));
    std::fs::write(&tmp, contents).map_err(|e| e.to_string())?;
    std::fs::rename(&tmp, dest).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        e.to_string()
    })
}

pub fn save_studio(path: impl AsRef<Path>, json: &StudioFile) -> Result<(), String> {
    let path = path.as_ref();
    require_dir(path)?;
    let contents = serde_json::to_string_pretty(json).map_err(|e| e.to_string())?;
    atomic_write(&path.join("studio.json"), &contents)
}

fn sha256_hex_prefix(bytes: &[u8], n: usize) -> String {
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(bytes);
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    hex.chars().take(n).collect()
}

fn sanitize_stem(stem: &str) -> String {
    let cleaned: String = stem
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '_'
            }
        })
        .collect();
    if cleaned.is_empty() {
        "file".into()
    } else {
        cleaned
    }
}

fn normalize_rel(rel: &str) -> Result<String, String> {
    let rel = rel.replace('\\', "/");
    if rel.starts_with('/') || rel.contains("://") {
        return Err("path must be project-relative".into());
    }
    let mut parts = Vec::new();
    for part in rel.split('/') {
        if part.is_empty() || part == "." {
            continue;
        }
        if part == ".." {
            return Err("path must not contain ..".into());
        }
        parts.push(part);
    }
    if parts.is_empty() {
        return Err("empty relative path".into());
    }
    Ok(parts.join("/"))
}

fn to_forward_slash(path: &Path) -> String {
    path.to_string_lossy().replace('\\', "/")
}

pub fn import_file(project: impl AsRef<Path>, src: impl AsRef<Path>) -> Result<String, String> {
    let project = project.as_ref();
    require_dir(project)?;
    let src = src.as_ref();
    if !src.is_file() {
        return Err(format!("not a file: {}", src.display()));
    }
    let bytes = std::fs::read(src).map_err(|e| e.to_string())?;
    let stem = sanitize_stem(
        src.file_stem()
            .and_then(|s| s.to_str())
            .unwrap_or("file"),
    );
    let ext = src.extension().and_then(|s| s.to_str()).unwrap_or("");
    save_asset_bytes(project, &stem, ext, &bytes)
}

fn sanitize_ext(ext: &str) -> Result<String, String> {
    let ext = ext.trim().trim_start_matches('.');
    if ext.is_empty() {
        return Ok(String::new());
    }
    if !ext.chars().all(|c| c.is_ascii_alphanumeric()) {
        return Err(format!("invalid extension: {ext}"));
    }
    Ok(ext.to_ascii_lowercase())
}

/// Write bytes into `assets/in/<stem>-<sha12>.<ext>` (same naming as import_file).
pub fn save_asset_bytes(
    project: impl AsRef<Path>,
    stem: &str,
    ext: &str,
    bytes: &[u8],
) -> Result<String, String> {
    let project = project.as_ref();
    require_dir(project)?;
    let stem = sanitize_stem(stem);
    let ext = sanitize_ext(ext)?;
    let hash = sha256_hex_prefix(bytes, 12);
    let filename = if ext.is_empty() {
        format!("{stem}-{hash}")
    } else {
        format!("{stem}-{hash}.{ext}")
    };
    let dest_dir = project.join("assets/in");
    std::fs::create_dir_all(&dest_dir).map_err(|e| e.to_string())?;
    std::fs::write(dest_dir.join(&filename), bytes).map_err(|e| e.to_string())?;
    Ok(format!("assets/in/{filename}"))
}

pub fn import_comp(project: impl AsRef<Path>, src: impl AsRef<Path>) -> Result<String, String> {
    let project = project.as_ref();
    require_dir(project)?;
    let src = src.as_ref();
    if !src.is_file() {
        return Err(format!("not a file: {}", src.display()));
    }
    let project_canon = project.canonicalize().map_err(|e| e.to_string())?;
    let src_canon = src.canonicalize().map_err(|e| e.to_string())?;
    if let Ok(rel) = src_canon.strip_prefix(&project_canon) {
        let rel = to_forward_slash(rel);
        if rel.starts_with("comps/") {
            return Ok(rel);
        }
    }
    let name = src
        .file_name()
        .ok_or_else(|| "missing filename".to_string())?;
    let dest_dir = project.join("comps");
    std::fs::create_dir_all(&dest_dir).map_err(|e| e.to_string())?;
    let dest = dest_dir.join(name);
    std::fs::copy(src, &dest).map_err(|e| e.to_string())?;
    Ok(format!("comps/{}", name.to_string_lossy().replace('\\', "/")))
}

const ELLUA_LIB: &[&str] = &[
    "init.lua",
    "timeline.lua",
    "color.lua",
    "ease.lua",
    "hsluv.lua",
    "json.lua",
    "okhsl.lua",
    "rough.lua",
    "chart.lua",
    "ornament.lua",
    "captions.lua",
    "spine.lua",
    "audio.lua",
];

fn ellua_root() -> Result<PathBuf, String> {
    if let Ok(dir) = std::env::var("CADENCE_ROOT").or_else(|_| std::env::var("ELLUA_ROOT")) {
        return PathBuf::from(dir).canonicalize().map_err(|e| e.to_string());
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|e| e.to_string())
}

/// Read an allowlisted cadence/ellua library file (`lib/cadence/*.lua`, `lib/ellua/*.lua`) or `web/bridge.lua`.
/// `name` must be a basename from the host.js LIB list, or `bridge.lua`.
pub fn read_ellua_lib(name: &str) -> Result<String, String> {
    if name.is_empty()
        || name.contains('/')
        || name.contains('\\')
        || name.contains("..")
        || name.contains('\0')
    {
        return Err("invalid lib name".into());
    }
    let root = ellua_root()?;
    let path = if name == "bridge.lua" {
        root.join("web/bridge.lua")
    } else if ELLUA_LIB.contains(&name) {
        let cand = root.join("lib/cadence").join(name);
        if cand.is_file() {
            cand
        } else {
            root.join("lib/ellua").join(name)
        }
    } else {
        return Err(format!("unknown cadence lib: {name}"));
    };
    let canon = path.canonicalize().map_err(|e| e.to_string())?;
    if !canon.starts_with(&root) {
        return Err("path escapes cadence root".into());
    }
    std::fs::read_to_string(canon).map_err(|e| e.to_string())
}

pub fn read_cadence_lib(name: &str) -> Result<String, String> {
    read_ellua_lib(name)
}

fn read_under_ellua_root(rel: &str) -> Result<PathBuf, String> {
    let rel = normalize_rel(rel)?;
    let root = ellua_root()?;
    let path = root.join(&rel);
    let canon = path.canonicalize().map_err(|e| e.to_string())?;
    if !canon.starts_with(&root) {
        return Err("path escapes cadence root".into());
    }
    Ok(canon)
}

/// Read a file under `evals/` (e.g. `evals/cases/video.lua`).
pub fn read_ellua_eval(rel: &str) -> Result<String, String> {
    let rel = normalize_rel(rel)?;
    if !rel.starts_with("evals/") {
        return Err("path must start with evals/".into());
    }
    let canon = read_under_ellua_root(&rel)?;
    if !canon.is_file() {
        return Err(format!("not a file: {rel}"));
    }
    std::fs::read_to_string(canon).map_err(|e| e.to_string())
}

/// Resolve an eval asset path to an absolute filesystem path for `convertFileSrc`.
pub fn resolve_ellua_asset(rel: &str) -> Result<String, String> {
    let rel = normalize_rel(rel)?;
    if !rel.starts_with("evals/assets/") {
        return Err("asset path must start with evals/assets/".into());
    }
    let canon = read_under_ellua_root(&rel)?;
    if !canon.is_file() {
        return Err(format!("asset not found: {rel}"));
    }
    Ok(canon.to_string_lossy().into_owned())
}

pub fn resolve_cadence_path(rel: &str) -> Result<String, String> {
    let rel = normalize_rel(rel)?;
    let root = ellua_root()?;
    let path = root.join(&rel);
    let canon = path.canonicalize().map_err(|e| e.to_string())?;
    if !canon.starts_with(&root) {
        return Err("path escapes cadence root".into());
    }
    Ok(canon.to_string_lossy().into_owned())
}

/// Resolve a project-relative asset path to an absolute filesystem path.
pub fn resolve_project_asset(project: impl AsRef<Path>, rel: &str) -> Result<String, String> {
    let project = project.as_ref();
    require_dir(project)?;
    let rel = normalize_rel(rel)?;
    if !rel.starts_with("assets/") {
        return Err("asset path must start with assets/".into());
    }
    let path = project.join(&rel);
    let project_canon = project.canonicalize().map_err(|e| e.to_string())?;
    let canon = path.canonicalize().map_err(|e| e.to_string())?;
    if !canon.starts_with(&project_canon) {
        return Err("path escapes project".into());
    }
    if !canon.is_file() {
        return Err(format!("asset not found: {rel}"));
    }
    Ok(canon.to_string_lossy().into_owned())
}

const DOC_FILE: &str = "doc.json";

pub fn read_editor_doc(project: impl AsRef<Path>) -> Result<String, String> {
    read_project_file(project, DOC_FILE)
}

pub fn save_editor_doc(project: impl AsRef<Path>, json: &str) -> Result<(), String> {
    let project = project.as_ref();
    require_dir(project)?;
    let _: serde_json::Value =
        serde_json::from_str(json).map_err(|e| format!("invalid doc.json: {e}"))?;
    atomic_write(&project.join(DOC_FILE), json)
}

/// Load doc.json or seed a default from the project folder name.
pub fn open_editor_doc(project: impl AsRef<Path>) -> Result<String, String> {
    let project = project.as_ref();
    require_dir(project)?;
    ensure_project_dirs(project)?;
    let doc_path = project.join(DOC_FILE);
    if doc_path.is_file() {
        return read_editor_doc(project);
    }
    let default = serde_json::json!({
        "version": 1,
        "duration": 10.0,
        "fps": 30,
        "aspect": "16:9",
        "compPath": "comps/main.lua",
        "lines": [],
        "lineKeyframes": [],
        "scenes": [],
        "sceneParams": {}
    });
    let contents = serde_json::to_string_pretty(&default).map_err(|e| e.to_string())?;
    save_editor_doc(project, &contents)?;
    Ok(contents)
}

pub fn read_project_file(project: impl AsRef<Path>, rel: &str) -> Result<String, String> {
    let project = project.as_ref();
    require_dir(project)?;
    let rel = normalize_rel(rel)?;
    let path = project.join(&rel);
    let project_canon = project.canonicalize().map_err(|e| e.to_string())?;
    let canon = path.canonicalize().map_err(|e| e.to_string())?;
    if !canon.starts_with(&project_canon) {
        return Err("path escapes project".into());
    }
    std::fs::read_to_string(canon).map_err(|e| e.to_string())
}

pub fn write_comp_inputs(
    project: impl AsRef<Path>,
    lua_rel: &str,
    name: &str,
    file_rel: Option<&str>,
) -> Result<(), String> {
    let project = project.as_ref();
    require_dir(project)?;
    let lua_rel = normalize_rel(lua_rel)?;
    if !lua_rel.starts_with("comps/") || !lua_rel.ends_with(".lua") {
        return Err(format!("expected comps/*.lua, got {lua_rel}"));
    }
    let json_rel = format!("{}.inputs.json", lua_rel.trim_end_matches(".lua"));
    let dest = project.join(&json_rel);
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    let mut map = if dest.exists() {
        let text = std::fs::read_to_string(&dest).map_err(|e| e.to_string())?;
        serde_json::from_str::<serde_json::Map<String, serde_json::Value>>(&text)
            .map_err(|e| format!("invalid inputs.json: {e}"))?
    } else {
        serde_json::Map::new()
    };
    match file_rel {
        Some(path) => {
            let path = normalize_rel(path)?;
            map.insert(name.to_string(), serde_json::Value::String(path));
        }
        None => {
            map.remove(name);
        }
    }
    let contents = serde_json::to_string_pretty(&serde_json::Value::Object(map))
        .map_err(|e| e.to_string())?;
    atomic_write(&dest, &contents)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loads_studio_json_without_last_run_hashes() {
        let dir = tempfile::tempdir().unwrap();
        for rel in PROJECT_DIRS {
            std::fs::create_dir_all(dir.path().join(rel)).unwrap();
        }
        std::fs::write(
            dir.path().join("studio.json"),
            r#"{
              "version": 1,
              "name": "old",
              "viewport": {"x": 0.0, "y": 0.0, "zoom": 1.0},
              "nodes": [],
              "edges": []
            }"#,
        )
        .unwrap();
        let loaded = open_project(dir.path()).unwrap();
        assert!(
            loaded.last_run_hashes.is_empty(),
            "missing lastRunHashes should default to empty, got {:?}",
            loaded.last_run_hashes
        );
    }

    #[test]
    fn loads_default_when_missing() {
        let dir = tempfile::tempdir().unwrap();
        let studio = open_project(dir.path()).unwrap();

        assert_eq!(studio.version, 1);
        assert_eq!(studio.viewport, Viewport { x: 0.0, y: 0.0, zoom: 1.0 });
        assert!(studio.nodes.is_empty());
        assert!(studio.edges.is_empty());

        let studio_json = dir.path().join("studio.json");
        assert!(studio_json.is_file(), "open_project should create studio.json");

        let on_disk: StudioFile =
            serde_json::from_str(&std::fs::read_to_string(&studio_json).unwrap()).unwrap();
        assert_eq!(on_disk.version, 1);

        for rel in ["comps", "assets/in", "assets/capture", "renders", "thumbs"] {
            assert!(
                dir.path().join(rel).is_dir(),
                "open_project should ensure {rel}/"
            );
        }
    }

    #[test]
    fn roundtrip() {
        let dir = tempfile::tempdir().unwrap();
        let original = StudioFile {
            version: 1,
            name: "demo".into(),
            viewport: Viewport {
                x: 12.0,
                y: -4.5,
                zoom: 1.25,
            },
            nodes: vec![],
            edges: vec![],
            last_run_hashes: HashMap::from([
                ("file-a".into(), r#"["assets/in/a.png","image"]"#.into()),
                ("render-1".into(), "renders/hello.mp4".into()),
            ]),
        };

        save_studio(dir.path(), &original).unwrap();
        assert!(dir.path().join("studio.json").is_file());
        assert!(!dir.path().join(".studio.json.tmp").exists());
        let raw = std::fs::read_to_string(dir.path().join("studio.json")).unwrap();
        assert!(
            raw.contains("lastRunHashes"),
            "persist must use camelCase lastRunHashes, got {raw}"
        );
        assert!(
            !raw.contains("last_run_hashes"),
            "must not write snake_case last_run_hashes, got {raw}"
        );
        let loaded = open_project(dir.path()).unwrap();
        assert_eq!(loaded, original);
        assert_eq!(
            loaded.last_run_hashes.get("render-1").map(String::as_str),
            Some("renders/hello.mp4")
        );
    }

    fn sample_studio() -> StudioFile {
        StudioFile {
            version: 1,
            name: "demo".into(),
            viewport: Viewport {
                x: 0.0,
                y: 0.0,
                zoom: 1.0,
            },
            nodes: vec![],
            edges: vec![],
            last_run_hashes: HashMap::new(),
        }
    }

    #[test]
    fn rejects_non_directory() {
        let dir = tempfile::tempdir().unwrap();
        let file = dir.path().join("not-a-dir.txt");
        std::fs::write(&file, "keep-me").unwrap();

        let open_err = open_project(&file).unwrap_err();
        assert!(
            open_err.to_lowercase().contains("not a directory"),
            "open_project error should say not a directory, got {open_err}"
        );

        let save_err = save_studio(&file, &sample_studio()).unwrap_err();
        assert!(
            save_err.to_lowercase().contains("not a directory"),
            "save_studio error should say not a directory, got {save_err}"
        );

        assert_eq!(std::fs::read_to_string(&file).unwrap(), "keep-me");
        assert!(!file.join("studio.json").exists());
        assert!(!dir.path().join(".studio.json.tmp").exists());
    }

    #[test]
    fn import_file_copies_into_assets_in_with_stem_and_hash() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();

        let src_dir = tempfile::tempdir().unwrap();
        let src = src_dir.path().join("hero.png");
        std::fs::write(&src, b"fake-png-bytes").unwrap();

        let rel = import_file(project.path(), &src).unwrap();
        assert!(
            rel.starts_with("assets/in/hero-") && rel.ends_with(".png"),
            "relative path should be assets/in/<stem>-<hash>.<ext>, got {rel}"
        );
        let dest = project.path().join(&rel);
        assert!(dest.is_file(), "copied file should exist at {}", dest.display());
        assert_eq!(std::fs::read(&dest).unwrap(), b"fake-png-bytes");
        assert_ne!(rel, "assets/in/hero.png");
    }

    const TINY_PNG: &[u8] = &[
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44,
        0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90,
        0x77, 0x53, 0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54, 0x08, 0xD7, 0x63, 0xF8,
        0xCF, 0xC0, 0x00, 0x00, 0x00, 0x03, 0x00, 0x01, 0x18, 0xD8, 0x5E, 0xED, 0x00, 0x00, 0x00,
        0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
    ];

    #[test]
    fn save_asset_bytes_writes_stem_hash_ext() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();

        let rel = save_asset_bytes(project.path(), "tiny", "png", TINY_PNG).unwrap();
        assert!(
            rel.starts_with("assets/in/tiny-") && rel.ends_with(".png"),
            "relative path should be assets/in/<stem>-<hash>.<ext>, got {rel}"
        );
        assert_eq!(rel.len(), "assets/in/tiny-".len() + 12 + ".png".len());
        let dest = project.path().join(&rel);
        assert!(dest.is_file(), "asset should exist at {}", dest.display());
        assert_eq!(std::fs::read(&dest).unwrap(), TINY_PNG);
        assert_eq!(
            save_asset_bytes(project.path(), "tiny", "png", TINY_PNG).unwrap(),
            rel,
            "same bytes should reuse the content-addressed name"
        );
    }

    #[test]
    fn import_file_requires_a_project_directory() {
        let src_dir = tempfile::tempdir().unwrap();
        let src = src_dir.path().join("hero.png");
        std::fs::write(&src, b"bytes").unwrap();
        let err = import_file(src.as_path(), &src).unwrap_err();
        assert!(
            err.to_lowercase().contains("not a directory"),
            "import_file should require an open project dir, got {err}"
        );
    }

    #[test]
    fn write_comp_inputs_merges_without_rewriting_lua() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        let lua = project.path().join("comps/bound.lua");
        let lua_body = "-- keep me\nreturn {}\n";
        std::fs::write(&lua, lua_body).unwrap();

        write_comp_inputs(
            project.path(),
            "comps/bound.lua",
            "bg",
            Some("assets/in/hero-aaa.png"),
        )
        .unwrap();
        write_comp_inputs(
            project.path(),
            "comps/bound.lua",
            "logo",
            Some("assets/in/logo-bbb.png"),
        )
        .unwrap();

        let json_path = project.path().join("comps/bound.inputs.json");
        assert!(
            !project.path().join("comps/.bound.inputs.json.tmp").exists(),
            "atomic write should not leave a tmp file"
        );
        let parsed: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(&json_path).unwrap()).unwrap();
        assert_eq!(parsed["bg"], "assets/in/hero-aaa.png");
        assert_eq!(parsed["logo"], "assets/in/logo-bbb.png");
        assert_eq!(std::fs::read_to_string(&lua).unwrap(), lua_body);
    }

    #[test]
    fn write_comp_inputs_rejects_invalid_json_without_wiping() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        std::fs::write(project.path().join("comps/bound.lua"), "return {}\n").unwrap();
        let json_path = project.path().join("comps/bound.inputs.json");
        std::fs::write(&json_path, "not-json{{{").unwrap();

        let err = write_comp_inputs(
            project.path(),
            "comps/bound.lua",
            "bg",
            Some("assets/in/hero-aaa.png"),
        )
        .unwrap_err();
        assert!(
            err.to_lowercase().contains("invalid") || err.to_lowercase().contains("json"),
            "parse error should mention invalid json, got {err}"
        );
        assert_eq!(std::fs::read_to_string(&json_path).unwrap(), "not-json{{{");
        assert!(!project.path().join("comps/.bound.inputs.json.tmp").exists());
    }

    #[test]
    fn write_comp_inputs_removes_a_key_when_file_rel_is_none() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        std::fs::write(project.path().join("comps/bound.lua"), "return {}\n").unwrap();
        write_comp_inputs(
            project.path(),
            "comps/bound.lua",
            "bg",
            Some("assets/in/hero-aaa.png"),
        )
        .unwrap();
        write_comp_inputs(
            project.path(),
            "comps/bound.lua",
            "logo",
            Some("assets/in/logo-bbb.png"),
        )
        .unwrap();

        write_comp_inputs(project.path(), "comps/bound.lua", "bg", None).unwrap();

        let parsed: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(project.path().join("comps/bound.inputs.json")).unwrap(),
        )
        .unwrap();
        assert!(parsed.get("bg").is_none(), "bg should be dropped, got {parsed}");
        assert_eq!(parsed["logo"], "assets/in/logo-bbb.png");
    }

    #[test]
    fn write_comp_inputs_normalizes_file_rel_and_rejects_escape() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        std::fs::write(project.path().join("comps/bound.lua"), "return {}\n").unwrap();

        write_comp_inputs(
            project.path(),
            "comps/bound.lua",
            "bg",
            Some("assets/in/foo.png"),
        )
        .unwrap();
        let parsed: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(project.path().join("comps/bound.inputs.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(parsed["bg"], "assets/in/foo.png");

        for bad in ["../etc/passwd", "/etc/passwd", "assets/in/../../etc/passwd"] {
            let err = write_comp_inputs(project.path(), "comps/bound.lua", "bg", Some(bad))
                .unwrap_err();
            assert!(
                err.contains("path") || err.contains(".."),
                "expected reject for {bad}, got {err}"
            );
        }
        let still: serde_json::Value = serde_json::from_str(
            &std::fs::read_to_string(project.path().join("comps/bound.inputs.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(still["bg"], "assets/in/foo.png");
    }

    #[test]
    fn import_comp_copies_outside_lua_into_comps() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();

        let src_dir = tempfile::tempdir().unwrap();
        let src = src_dir.path().join("intro.lua");
        std::fs::write(&src, "return {}\n").unwrap();

        let rel = import_comp(project.path(), &src).unwrap();
        assert_eq!(rel, "comps/intro.lua");
        assert_eq!(
            std::fs::read_to_string(project.path().join(&rel)).unwrap(),
            "return {}\n"
        );
    }

    #[test]
    fn import_comp_keeps_project_relative_path_when_already_in_comps() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        let lua = project.path().join("comps/local.lua");
        std::fs::write(&lua, "return 1\n").unwrap();

        let rel = import_comp(project.path(), &lua).unwrap();
        assert_eq!(rel, "comps/local.lua");
        assert_eq!(std::fs::read_to_string(&lua).unwrap(), "return 1\n");
    }

    #[test]
    fn read_ellua_lib_loads_allowlisted_init_and_bridge() {
        let init = read_ellua_lib("init.lua").unwrap();
        assert!(
            init.contains("function Comp:compile"),
            "init.lua should define Comp:compile"
        );
        let bridge = read_ellua_lib("bridge.lua").unwrap();
        assert!(bridge.contains("compile_comp"), "bridge.lua should export compile_comp");
        assert!(bridge.contains("function snapshot"), "bridge.lua should export snapshot");
    }

    #[test]
    fn read_ellua_lib_rejects_unknown_names_and_path_escape() {
        for name in [
            "../init.lua",
            "..",
            "secret.lua",
            "lint.lua",
            "ellua/init.lua",
            "/etc/passwd",
            "init.lua/../../web/bridge.lua",
        ] {
            let err = read_ellua_lib(name).unwrap_err();
            assert!(
                err.to_lowercase().contains("unknown")
                    || err.to_lowercase().contains("invalid"),
                "expected reject for {name}, got {err}"
            );
        }
    }

    #[test]
    fn open_editor_doc_seeds_default_doc_json() {
        let dir = tempfile::tempdir().unwrap();
        open_project(dir.path()).unwrap();
        let raw = open_editor_doc(dir.path()).unwrap();
        let parsed: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(parsed["version"], 1);
        assert_eq!(parsed["compPath"], "comps/main.lua");
        assert!(dir.path().join("doc.json").is_file());
    }

    #[test]
    fn save_editor_doc_roundtrips() {
        let dir = tempfile::tempdir().unwrap();
        open_project(dir.path()).unwrap();
        let body = r#"{"version":1,"duration":28,"fps":30,"aspect":"16:9","compPath":"comps/launch.lua","lines":[],"lineKeyframes":[],"scenes":[],"sceneParams":{}}"#;
        save_editor_doc(dir.path(), body).unwrap();
        let loaded = read_editor_doc(dir.path()).unwrap();
        assert_eq!(loaded, body);
    }

    #[test]
    fn resolve_project_asset_requires_assets_prefix() {
        let dir = tempfile::tempdir().unwrap();
        open_project(dir.path()).unwrap();
        let asset = dir.path().join("assets/in/hero.png");
        std::fs::create_dir_all(asset.parent().unwrap()).unwrap();
        std::fs::write(&asset, b"png").unwrap();
        let abs = resolve_project_asset(dir.path(), "assets/in/hero.png").unwrap();
        assert!(abs.ends_with("hero.png"));
        assert!(resolve_project_asset(dir.path(), "../etc/passwd").is_err());
    }
}
