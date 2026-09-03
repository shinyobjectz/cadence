use std::collections::HashMap;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use serde::Serialize;
use tauri::Emitter;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum JobStatus {
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

#[derive(Clone, Debug, Serialize)]
pub struct JobSnapshot {
    pub id: String,
    pub status: JobStatus,
    pub stdout: String,
    pub stderr: String,
    pub output_rel: Option<String>,
    pub error: Option<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct JobLog {
    pub id: String,
    pub stream: String,
    pub line: String,
}

#[derive(Debug)]
pub struct SpawnSpec {
    pub command: Command,
    pub temp_out: Option<PathBuf>,
    pub final_out: Option<PathBuf>,
    pub output_rel: Option<String>,
}

pub type LogSink = Arc<dyn Fn(JobLog) + Send + Sync>;
pub type DoneSink = Arc<dyn Fn(JobSnapshot) + Send + Sync>;

struct LiveJob {
    child: Arc<Mutex<Option<Child>>>,
    cancelled: Arc<AtomicBool>,
    snapshot: Arc<Mutex<JobSnapshot>>,
    pid: u32,
}

const MAX_LOG_LINES: usize = 200;
const MAX_FINISHED_JOBS: usize = 20;

pub struct JobRegistry {
    jobs: Mutex<HashMap<String, LiveJob>>,
    next_id: AtomicU64,
}

impl JobRegistry {
    pub fn new() -> Self {
        Self {
            jobs: Mutex::new(HashMap::new()),
            next_id: AtomicU64::new(1),
        }
    }

    pub fn spawn_command(&self, command: Command) -> Result<String, String> {
        self.spawn(
            SpawnSpec {
                command,
                temp_out: None,
                final_out: None,
                output_rel: None,
            },
            None,
            None,
        )
    }

    pub fn spawn(
        &self,
        mut spec: SpawnSpec,
        on_log: Option<LogSink>,
        on_done: Option<DoneSink>,
    ) -> Result<String, String> {
        spec.command.stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            spec.command.process_group(0);
        }
        let mut child = spec.command.spawn().map_err(|e| format!("spawn failed: {e}"))?;
        let pid = child.id();
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let id = format!("job-{}", self.next_id.fetch_add(1, Ordering::Relaxed));
        let cancelled = Arc::new(AtomicBool::new(false));
        let snapshot = Arc::new(Mutex::new(JobSnapshot {
            id: id.clone(),
            status: JobStatus::Running,
            stdout: String::new(),
            stderr: String::new(),
            output_rel: spec.output_rel.clone(),
            error: None,
        }));
        let child_slot = Arc::new(Mutex::new(Some(child)));
        let live = LiveJob {
            child: Arc::clone(&child_slot),
            cancelled: Arc::clone(&cancelled),
            snapshot: Arc::clone(&snapshot),
            pid,
        };
        self.jobs.lock().map_err(|e| e.to_string()).map(|mut jobs| {
            jobs.insert(id.clone(), live);
            prune_finished(&mut jobs);
        })?;

        let stdout_snap = Arc::clone(&snapshot);
        let stdout_log = on_log.clone();
        let stdout_id = id.clone();
        let stdout_thread = thread::spawn(move || {
            if let Some(pipe) = stdout {
                pump_lines(pipe, "stdout", &stdout_id, &stdout_snap, stdout_log.as_ref());
            }
        });
        let stderr_snap = Arc::clone(&snapshot);
        let stderr_log = on_log;
        let stderr_id = id.clone();
        let stderr_thread = thread::spawn(move || {
            if let Some(pipe) = stderr {
                pump_lines(pipe, "stderr", &stderr_id, &stderr_snap, stderr_log.as_ref());
            }
        });

        let wait_snap = Arc::clone(&snapshot);
        thread::spawn(move || {
            let status = wait_for_child(&child_slot);
            let _ = stdout_thread.join();
            let _ = stderr_thread.join();
            let cancelled_now = cancelled.load(Ordering::SeqCst);
            let ok = matches!(status, Ok(ref s) if s.success()) && !cancelled_now;
            if let Err(err) = finalize_output(ok, spec.temp_out.as_deref(), spec.final_out.as_deref())
            {
                let mut snap = wait_snap.lock().unwrap_or_else(|e| e.into_inner());
                snap.status = JobStatus::Failed;
                snap.error = Some(err);
                let done = snap.clone();
                drop(snap);
                if let Some(cb) = on_done {
                    cb(done);
                }
                return;
            }
            let mut snap = wait_snap.lock().unwrap_or_else(|e| e.into_inner());
            if cancelled_now {
                snap.status = JobStatus::Cancelled;
                snap.error = Some("cancelled".into());
            } else if ok {
                snap.status = JobStatus::Succeeded;
                snap.error = None;
            } else {
                snap.status = JobStatus::Failed;
                let code = status
                    .as_ref()
                    .ok()
                    .and_then(|s| s.code())
                    .map(|c| format!("exit {c}"))
                    .unwrap_or_else(|| "killed".into());
                if snap.stderr.trim().is_empty() {
                    snap.error = Some(code);
                } else {
                    snap.error = Some(snap.stderr.clone());
                }
            }
            let done = snap.clone();
            drop(snap);
            if let Some(cb) = on_done {
                cb(done);
            }
        });

        Ok(id)
    }

    pub fn cancel(&self, id: &str) -> Result<(), String> {
        let jobs = self.jobs.lock().map_err(|e| e.to_string())?;
        let job = jobs.get(id).ok_or_else(|| format!("unknown job {id}"))?;
        job.cancelled.store(true, Ordering::SeqCst);
        terminate_then_kill(job.pid, &job.child);
        Ok(())
    }

    pub fn cancel_all(&self) -> Result<(), String> {
        let targets: Vec<(u32, Arc<Mutex<Option<Child>>>, Arc<AtomicBool>)> = {
            let jobs = self.jobs.lock().map_err(|e| e.to_string())?;
            jobs.values()
                .map(|job| {
                    (
                        job.pid,
                        Arc::clone(&job.child),
                        Arc::clone(&job.cancelled),
                    )
                })
                .collect()
        };
        for (pid, child, cancelled) in targets {
            cancelled.store(true, Ordering::SeqCst);
            terminate_then_kill(pid, &child);
        }
        Ok(())
    }

    pub fn snapshot(&self, id: &str) -> Result<JobSnapshot, String> {
        let jobs = self.jobs.lock().map_err(|e| e.to_string())?;
        let job = jobs.get(id).ok_or_else(|| format!("unknown job {id}"))?;
        job.snapshot
            .lock()
            .map(|s| s.clone())
            .map_err(|e| e.to_string())
    }

    pub fn list(&self) -> Result<Vec<JobSnapshot>, String> {
        let mut jobs = self.jobs.lock().map_err(|e| e.to_string())?;
        prune_finished(&mut jobs);
        let mut out = Vec::with_capacity(jobs.len());
        for job in jobs.values() {
            out.push(
                job.snapshot
                    .lock()
                    .map(|s| s.clone())
                    .map_err(|e| e.to_string())?,
            );
        }
        out.sort_by(|a, b| a.id.cmp(&b.id));
        Ok(out)
    }

    pub fn pid(&self, id: &str) -> Result<u32, String> {
        let jobs = self.jobs.lock().map_err(|e| e.to_string())?;
        let job = jobs.get(id).ok_or_else(|| format!("unknown job {id}"))?;
        Ok(job.pid)
    }
}

impl Drop for JobRegistry {
    fn drop(&mut self) {
        let _ = self.cancel_all();
    }
}

impl Drop for LiveJob {
    fn drop(&mut self) {
        terminate_then_kill(self.pid, &self.child);
    }
}

fn pump_lines<R: std::io::Read>(
    pipe: R,
    stream: &str,
    id: &str,
    snapshot: &Mutex<JobSnapshot>,
    on_log: Option<&LogSink>,
) {
    let reader = BufReader::new(pipe);
    for line in reader.lines() {
        let Ok(line) = line else { break };
        {
            let mut snap = snapshot.lock().unwrap_or_else(|e| e.into_inner());
            let buf = if stream == "stderr" {
                &mut snap.stderr
            } else {
                &mut snap.stdout
            };
            append_capped(buf, &line, MAX_LOG_LINES);
        }
        if let Some(cb) = on_log {
            cb(JobLog {
                id: id.to_string(),
                stream: stream.to_string(),
                line,
            });
        }
    }
}

fn append_capped(buf: &mut String, line: &str, max_lines: usize) {
    if !buf.is_empty() {
        buf.push('\n');
    }
    buf.push_str(line);
    if max_lines == 0 {
        buf.clear();
        return;
    }
    let extra = buf.bytes().filter(|b| *b == b'\n').count().saturating_add(1);
    if extra <= max_lines {
        return;
    }
    let drop_lines = extra - max_lines;
    let mut seen = 0;
    if let Some(idx) = buf.bytes().enumerate().find_map(|(i, b)| {
        if b == b'\n' {
            seen += 1;
            if seen == drop_lines {
                return Some(i + 1);
            }
        }
        None
    }) {
        buf.replace_range(..idx, "");
    }
}

fn job_seq(id: &str) -> u64 {
    id.strip_prefix("job-")
        .and_then(|s| s.parse().ok())
        .unwrap_or(0)
}

fn prune_finished(jobs: &mut HashMap<String, LiveJob>) {
    let mut finished: Vec<String> = jobs
        .iter()
        .filter_map(|(id, job)| {
            let status = job.snapshot.lock().ok()?.status;
            if status == JobStatus::Running {
                None
            } else {
                Some(id.clone())
            }
        })
        .collect();
    if finished.len() <= MAX_FINISHED_JOBS {
        return;
    }
    finished.sort_by_key(|id| job_seq(id));
    let extra = finished.len() - MAX_FINISHED_JOBS;
    for id in finished.into_iter().take(extra) {
        jobs.remove(&id);
    }
}

fn wait_for_child(child_slot: &Mutex<Option<Child>>) -> std::io::Result<ExitStatus> {
    loop {
        let mut guard = child_slot.lock().unwrap_or_else(|e| e.into_inner());
        match guard.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(Some(status)) => {
                    let _ = guard.take();
                    return Ok(status);
                }
                Ok(None) => {
                    drop(guard);
                    thread::sleep(Duration::from_millis(25));
                }
                Err(err) => return Err(err),
            },
            None => {
                return Err(std::io::Error::other("child already reaped"));
            }
        }
    }
}

fn terminate_then_kill(pid: u32, child_slot: &Mutex<Option<Child>>) {
    {
        let guard = child_slot.lock().unwrap_or_else(|e| e.into_inner());
        if guard.is_none() {
            return;
        }
    }
    #[cfg(unix)]
    unsafe {
        let p = pid as i32;
        libc::kill(p, libc::SIGTERM);
        libc::kill(-p, libc::SIGTERM);
    }
    let deadline = std::time::Instant::now() + Duration::from_millis(400);
    while std::time::Instant::now() < deadline {
        if let Ok(mut guard) = child_slot.lock() {
            if let Some(child) = guard.as_mut() {
                if let Ok(Some(_)) = child.try_wait() {
                    let _ = guard.take();
                    return;
                }
            } else {
                return;
            }
        }
        thread::sleep(Duration::from_millis(20));
    }
    #[cfg(unix)]
    unsafe {
        let p = pid as i32;
        libc::kill(p, libc::SIGKILL);
        libc::kill(-p, libc::SIGKILL);
    }
    if let Ok(mut guard) = child_slot.lock() {
        if let Some(mut child) = guard.take() {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

fn finalize_output(ok: bool, temp: Option<&Path>, dest: Option<&Path>) -> Result<(), String> {
    match (ok, temp, dest) {
        (true, Some(tmp), Some(dest)) => {
            if !tmp.exists() {
                return Err("render succeeded but output is missing".into());
            }
            if let Some(parent) = dest.parent() {
                std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
            }
            std::fs::rename(tmp, dest).map_err(|e| e.to_string())
        }
        (false, Some(tmp), _) => {
            if tmp.exists() {
                let _ = std::fs::remove_file(tmp);
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

pub fn ellua_bin() -> Result<PathBuf, String> {
    let cadence = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../bin/cadence");
    if cadence.is_file() {
        return Ok(cadence);
    }
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../bin/ellua");
    if !path.is_file() {
        return Err(format!("cadence/ellua launcher not found at {}", cadence.display()));
    }
    Ok(path)
}

pub fn capture_bin() -> Result<PathBuf, String> {
    let cadence = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../bin/cadence-capture");
    if cadence.is_file() {
        return Ok(cadence);
    }
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../bin/ellua-capture");
    if !path.is_file() {
        return Err(format!("cadence/ellua-capture not found at {}", cadence.display()));
    }
    Ok(path)
}

#[derive(Clone, Debug, Serialize)]
pub struct CaptureAsset {
    pub id: String,
    pub kind: String,
    pub rel: String,
}

pub fn normalize_capture_url(url: &str) -> Result<String, String> {
    let url = url.trim();
    if url.is_empty() {
        return Err("url is required".into());
    }
    if url.starts_with("https://") || url.starts_with("http://") {
        Ok(url.to_string())
    } else if url.contains("://") {
        Err("url must be http:// or https://".into())
    } else {
        Ok(format!("https://{url}"))
    }
}

pub fn capture_host_from_url(url: &str) -> Result<String, String> {
    let url = normalize_capture_url(url)?;
    let after_scheme = url
        .split_once("://")
        .map(|(_, rest)| rest)
        .ok_or_else(|| "invalid url".to_string())?;
    let authority = after_scheme.split('/').next().unwrap_or("");
    let hostport = authority.rsplit('@').next().unwrap_or("");
    if hostport.is_empty() {
        return Err("url is missing a host".into());
    }
    let host = hostport.split(':').next().unwrap_or(hostport);
    let sanitized: String = host
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '.' || c == '-' {
                c.to_ascii_lowercase()
            } else {
                '_'
            }
        })
        .collect();
    if sanitized.is_empty() || sanitized == "." || sanitized == ".." {
        return Err("invalid host".into());
    }
    if sanitized.contains("..") {
        return Err("invalid host".into());
    }
    Ok(sanitized)
}

fn project_rel(rel: &str, prefix: &str) -> Result<String, String> {
    let rel = rel.replace('\\', "/");
    if rel.starts_with('/') || rel.contains("://") || rel.contains('\0') {
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
    let joined = parts.join("/");
    if !joined.starts_with(prefix) {
        return Err(format!("expected {prefix}..., got {rel}"));
    }
    Ok(joined)
}

fn sanitize_node_id(id: &str) -> String {
    let cleaned: String = id
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
        "node".into()
    } else {
        cleaned
    }
}

fn lua_quote(s: &str) -> String {
    let mut out = String::from("\"");
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\0' => out.push_str("\\000"),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Must match `runtime/resolve.lua` `tts_cache_key` / sfx / music naming for
/// the default studio calls (`s:tts{text=}`, `s:sfx{prompt=}`, `s:music{prompt=}`).
const DEFAULT_TTS_VOICE_ID: &str = "EXAVITQu4vr4xnSDxMaL"; // sarah
const DEFAULT_TTS_MODEL: &str = "eleven_multilingual_v2";
const DEFAULT_TTS_DRAFT_MODEL: &str = "eleven_flash_v2_5";
const DEFAULT_MUSIC_MODEL: &str = "music_v1";

fn sha1_hex(s: &str) -> String {
    use sha1::{Digest, Sha1};
    Sha1::digest(s.as_bytes())
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect()
}

pub fn studio_tts_cache_filename(kind: &str, text: &str) -> Result<String, String> {
    let kind = kind.trim();
    let text = text.trim();
    if text.is_empty() {
        return Err("text is required".into());
    }
    let key = match kind {
        "tts" => {
            let model = if std::env::var("ELLUA_DRAFT").ok().as_deref() == Some("1") {
                DEFAULT_TTS_DRAFT_MODEL
            } else {
                DEFAULT_TTS_MODEL
            };
            format!("{text}|{DEFAULT_TTS_VOICE_ID}|{model}|||||||")
        }
        "sfx" => format!("{text}|"),
        "music" => format!("{text}|30|{DEFAULT_MUSIC_MODEL}"),
        other => return Err(format!("kind must be tts|sfx|music, got {other}")),
    };
    Ok(format!("{}.mp3", sha1_hex(&key)))
}

fn handle_id(prefix: &str, filename: &str) -> String {
    let cleaned: String = filename
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || c == '-' || c == '_' {
                c
            } else {
                '-'
            }
        })
        .collect();
    format!("{prefix}-{cleaned}")
}

pub fn build_capture_spec(
    project: impl AsRef<Path>,
    url: &str,
) -> Result<SpawnSpec, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let url = normalize_capture_url(url)?;
    let host = capture_host_from_url(&url)?;
    let output_rel = format!("assets/capture/{host}");
    let outdir = project.join(&output_rel);
    let bin = capture_bin()?;
    let mut command = Command::new(&bin);
    command.arg(&url).arg(&outdir).current_dir(project);
    Ok(SpawnSpec {
        command,
        temp_out: None,
        final_out: None,
        output_rel: Some(output_rel),
    })
}

pub fn list_capture_assets(
    project: impl AsRef<Path>,
    rel: &str,
) -> Result<Vec<CaptureAsset>, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let rel = project_rel(rel, "assets/capture/")?;
    let root = project.join(&rel);
    if !root.is_dir() {
        return Err(format!("capture output missing: {rel}"));
    }
    let mut out = Vec::new();
    let page = root.join("page.png");
    if page.is_file() {
        out.push(CaptureAsset {
            id: "page".into(),
            kind: "file".into(),
            rel: format!("{rel}/page.png"),
        });
    }
    let brand = root.join("brand.json");
    if brand.is_file() {
        out.push(CaptureAsset {
            id: "brand".into(),
            kind: "brand".into(),
            rel: format!("{rel}/brand.json"),
        });
    }
    push_dir_assets(&mut out, &root, &rel, "fonts", "font", &["ttf", "otf"]);
    push_dir_assets(&mut out, &root, &rel, "logos", "image", &[]);
    Ok(out)
}

fn push_dir_assets(
    out: &mut Vec<CaptureAsset>,
    root: &Path,
    rel: &str,
    folder: &str,
    kind: &str,
    exts: &[&str],
) {
    let dir = root.join(folder);
    let Ok(entries) = std::fs::read_dir(&dir) else {
        return;
    };
    let mut files: Vec<String> = entries
        .flatten()
        .filter(|e| e.path().is_file())
        .filter_map(|e| e.file_name().into_string().ok())
        .filter(|name| {
            if name.starts_with('.') {
                return false;
            }
            if exts.is_empty() {
                return true;
            }
            let ext = name.rsplit('.').next().unwrap_or("").to_ascii_lowercase();
            exts.iter().any(|want| *want == ext)
        })
        .collect();
    files.sort();
    for name in files {
        out.push(CaptureAsset {
            id: handle_id(folder.trim_end_matches('s'), &name),
            kind: kind.into(),
            rel: format!("{rel}/{folder}/{name}"),
        });
    }
}

pub fn studio_tts_lua(kind: &str, text: &str) -> Result<String, String> {
    let kind = kind.trim();
    if !matches!(kind, "tts" | "sfx" | "music") {
        return Err(format!("kind must be tts|sfx|music, got {kind}"));
    }
    let text = text.trim();
    if text.is_empty() {
        return Err("text is required".into());
    }
    let quoted = lua_quote(text);
    let call = match kind {
        "tts" => format!("s:tts{{ text = {quoted} }}"),
        "sfx" => format!("s:sfx{{ prompt = {quoted} }}"),
        "music" => format!("s:music{{ prompt = {quoted} }}"),
        _ => unreachable!(),
    };
    Ok(format!(
        "local c = require(\"cadence\")\n\
         return c.comp {{\n\
           width = 64, height = 64, duration = 1, fps = 1,\n\
           scene = function(s)\n\
             {call}\n\
             s:rect{{ x = 0, y = 0, w = 64, h = 64, color = \"#000\" }}\n\
           end,\n\
         }}\n"
    ))
}

pub fn write_studio_tts_comp(
    project: impl AsRef<Path>,
    node_id: &str,
    kind: &str,
    text: &str,
) -> Result<String, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let body = studio_tts_lua(kind, text)?;
    let id = sanitize_node_id(node_id);
    let rel = format!("comps/.studio-tts-{id}.lua");
    let dest = project.join(&rel);
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    std::fs::write(&dest, body).map_err(|e| e.to_string())?;
    Ok(rel)
}

fn elevenlabs_api_key() -> Result<String, String> {
    match crate::secrets::require_elevenlabs_key() {
        Ok(key) => Ok(key),
        Err(err) => std::env::var("ELEVENLABS_API_KEY")
            .ok()
            .filter(|s| !s.is_empty())
            .ok_or(err),
    }
}

pub fn build_tts_hash_spec(
    project: impl AsRef<Path>,
    lua_rel: &str,
    api_key: Option<&str>,
) -> Result<SpawnSpec, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let lua_rel = normalize_lua_rel(lua_rel)?;
    let bin = ellua_bin()?;
    let mut command = Command::new(&bin);
    command
        .arg("hash")
        .arg(&lua_rel)
        .current_dir(project)
        .env("CADENCE_CWD", project)
        .env("ELLUA_CWD", project);
    if let Some(key) = api_key {
        command.env("ELEVENLABS_API_KEY", key);
    }
    Ok(SpawnSpec {
        command,
        temp_out: None,
        final_out: None,
        output_rel: None,
    })
}

/// Generate VO via `cadence-audio` (PocketTTS / Kokoro ONNX, no cloud key required).
pub fn generate_local_vo(
    project: impl AsRef<Path>,
    text: &str,
    provider: Option<&str>,
) -> Result<String, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let text = text.trim();
    if text.is_empty() {
        return Err("text is required".into());
    }

    if let Ok(home) = std::env::var("HOME") {
        let cadence_cache = PathBuf::from(&home).join(".cache/cadence/tts");
        if cadence_cache.is_dir() {
            if let Ok(rel) = copy_cached_audio_from(project, &cadence_cache, "tts", text) {
                return Ok(rel);
            }
        }
        let ellua_cache = PathBuf::from(home).join(".cache/ellua/tts");
        if ellua_cache.is_dir() {
            if let Ok(rel) = copy_cached_audio_from(project, ellua_cache, "tts", text) {
                return Ok(rel);
            }
        }
    }

    let provider = provider.unwrap_or("pocket-tts");
    let out_path = std::env::temp_dir().join(format!(
        "cadence-vo-{}-{}.mp3",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0)
    ));
    let align_path = out_path.with_extension("align.json");

    let bin = ellua_bin()?;
    let mut command = Command::new(&bin);
    command
        .arg("tts")
        .arg("--text")
        .arg(text)
        .arg("--out")
        .arg(&out_path)
        .arg("--provider")
        .arg(provider)
        .arg("--model")
        .arg(provider)
        .arg("--align-out")
        .arg(&align_path)
        .env("CADENCE_TTS_PROVIDER", provider)
        .current_dir(project);
    if let Ok(key) = elevenlabs_api_key() {
        command.env("ELEVENLABS_API_KEY", key);
    }

    let output = command.output().map_err(|e| e.to_string())?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        let stdout = String::from_utf8_lossy(&output.stdout);
        return Err(format!("local TTS ({provider}) failed: {stderr}{stdout}"));
    }
    if !out_path.is_file() {
        return Err(format!(
            "local TTS ({provider}) produced no output at {}",
            out_path.display()
        ));
    }

    let bytes = std::fs::read(&out_path).map_err(|e| e.to_string())?;
    let rel = crate::project::save_asset_bytes(project, "vo", "mp3", &bytes)?;
    let _ = std::fs::remove_file(&out_path);
    let _ = std::fs::remove_file(&align_path);
    Ok(rel)
}

pub fn copy_cached_audio_from(
    project: impl AsRef<Path>,
    cache_dir: impl AsRef<Path>,
    stem: &str,
    text: &str,
) -> Result<String, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let filename = studio_tts_cache_filename(stem, text)?;
    let src = cache_dir.as_ref().join(&filename);
    if !src.is_file() {
        return Err(format!("audio cache missing: {}", src.display()));
    }
    let bytes = std::fs::read(&src).map_err(|e| e.to_string())?;
    crate::project::save_asset_bytes(project, stem, "mp3", &bytes)
}

pub fn build_render_spec(
    project: impl AsRef<Path>,
    lua_rel: &str,
    quality: &str,
) -> Result<SpawnSpec, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    if !matches!(quality, "draft" | "standard" | "high") {
        return Err(format!("quality must be draft|standard|high, got {quality}"));
    }
    let lua_rel = normalize_lua_rel(lua_rel)?;
    let stem = Path::new(&lua_rel)
        .file_stem()
        .and_then(|s| s.to_str())
        .ok_or_else(|| "missing lua stem".to_string())?
        .to_string();
    let renders = project.join("renders");
    std::fs::create_dir_all(&renders).map_err(|e| e.to_string())?;
    let output_rel = format!("renders/{stem}.mp4");
    let final_out = project.join(&output_rel);
    let temp_out = renders.join(format!(".{stem}.partial.mp4"));
    let bin = ellua_bin()?;
    let mut command = Command::new(&bin);
    command
        .arg("render")
        .arg(&lua_rel)
        .arg("-o")
        .arg(&temp_out)
        .current_dir(project)
        .env("CADENCE_QUALITY", quality)
        .env("ELLUA_QUALITY", quality)
        .env("CADENCE_CWD", project)
        .env("ELLUA_CWD", project);
    let inputs_rel = format!("{}.inputs.json", lua_rel.trim_end_matches(".lua"));
    if project.join(&inputs_rel).is_file() {
        command.arg("--inputs").arg(&inputs_rel);
    }
    Ok(SpawnSpec {
        command,
        temp_out: Some(temp_out),
        final_out: Some(final_out),
        output_rel: Some(output_rel),
    })
}

pub fn build_check_spec(
    project: impl AsRef<Path>,
    lua_rel: &str,
    mode: &str,
) -> Result<SpawnSpec, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    if !matches!(mode, "lint" | "check" | "hash") {
        return Err(format!("mode must be lint|check|hash, got {mode}"));
    }
    let lua_rel = normalize_lua_rel(lua_rel)?;
    let bin = ellua_bin()?;
    let mut command = Command::new(&bin);
    command
        .arg(mode)
        .arg(&lua_rel)
        .current_dir(project)
        .env("CADENCE_CWD", project)
        .env("ELLUA_CWD", project);
    if mode == "lint" || mode == "check" {
        command.arg("--json");
    }
    let inputs_rel = format!("{}.inputs.json", lua_rel.trim_end_matches(".lua"));
    if project.join(&inputs_rel).is_file() {
        command.arg("--inputs").arg(&inputs_rel);
    }
    Ok(SpawnSpec {
        command,
        temp_out: None,
        final_out: None,
        output_rel: None,
    })
}

fn cadence_root() -> Result<PathBuf, String> {
    if let Ok(dir) = std::env::var("CADENCE_ROOT").or_else(|_| std::env::var("ELLUA_ROOT")) {
        return PathBuf::from(dir).canonicalize().map_err(|e| e.to_string());
    }
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|e| e.to_string())
}

pub fn build_verify_spec(
    project: impl AsRef<Path>,
    lua_rel: &str,
    skip_check: bool,
    skip_lint: bool,
    wasm_only: bool,
) -> Result<SpawnSpec, String> {
    let project = project.as_ref();
    if !project.is_dir() {
        return Err(format!("not a directory: {}", project.display()));
    }
    let lua_rel = normalize_lua_rel(lua_rel)?;
    let root = cadence_root()?;
    let script = root.join("scripts/cadence-verify.mjs");
    if !script.is_file() {
        return Err(format!("verify script missing: {}", script.display()));
    }
    let mut command = Command::new("node");
    command
        .arg(script)
        .arg(&lua_rel)
        .arg("--json")
        .current_dir(project)
        .env("CADENCE_CWD", project)
        .env("ELLUA_CWD", project);
    if skip_check {
        command.arg("--skip-check");
    }
    if skip_lint {
        command.arg("--skip-lint");
    }
    if wasm_only {
        command.arg("--wasm-only");
    }
    Ok(SpawnSpec {
        command,
        temp_out: None,
        final_out: None,
        output_rel: None,
    })
}

#[tauri::command]
pub fn verify_comp(
    project: String,
    lua_rel: String,
    skip_check: Option<bool>,
    skip_lint: Option<bool>,
    wasm_only: Option<bool>,
) -> Result<String, String> {
    let mut spec = build_verify_spec(
        &project,
        &lua_rel,
        skip_check.unwrap_or(false),
        skip_lint.unwrap_or(false),
        wasm_only.unwrap_or(false),
    )?;
    let output = spec
        .command
        .output()
        .map_err(|e| format!("verify failed to start: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    if stdout.trim().is_empty() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(if stderr.is_empty() {
            "verify produced no output".into()
        } else {
            stderr.into_owned()
        });
    }
    Ok(stdout)
}

#[tauri::command]
pub fn start_verify(
    app: tauri::AppHandle,
    jobs: tauri::State<JobRegistry>,
    project: String,
    lua_rel: String,
    skip_check: Option<bool>,
    skip_lint: Option<bool>,
    wasm_only: Option<bool>,
) -> Result<String, String> {
    let spec = build_verify_spec(
        &project,
        &lua_rel,
        skip_check.unwrap_or(false),
        skip_lint.unwrap_or(false),
        wasm_only.unwrap_or(false),
    )?;
    let (on_log, on_done) = log_and_done_sinks(app);
    jobs.spawn(spec, Some(on_log), Some(on_done))
}

#[tauri::command]
pub fn doctor_cadence(project: Option<String>) -> Result<String, String> {
    let root = cadence_root()?;
    let script = root.join("scripts/cadence-doctor.mjs");
    if !script.is_file() {
        return Err(format!("doctor script missing: {}", script.display()));
    }
    let cwd = project
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .unwrap_or_else(|| std::env::current_dir().unwrap_or_else(|_| PathBuf::from(".")));
    let output = Command::new("node")
        .arg(script)
        .arg("--json")
        .arg("--project")
        .arg(&cwd)
        .output()
        .map_err(|e| format!("doctor failed to start: {e}"))?;
    let stdout = String::from_utf8_lossy(&output.stdout).into_owned();
    if stdout.trim().is_empty() {
        return Err(String::from_utf8_lossy(&output.stderr).into_owned());
    }
    Ok(stdout)
}

fn normalize_lua_rel(rel: &str) -> Result<String, String> {
    let rel = rel.replace('\\', "/");
    if rel.starts_with('/') || rel.contains("://") || rel.contains('\0') {
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
    let joined = parts.join("/");
    if !joined.starts_with("comps/") || !joined.ends_with(".lua") {
        return Err(format!("expected comps/*.lua, got {rel}"));
    }
    Ok(joined)
}

fn log_and_done_sinks(app: tauri::AppHandle) -> (LogSink, DoneSink) {
    let log_app = app.clone();
    let on_log: LogSink = Arc::new(move |log| {
        let _ = log_app.emit("job-log", &log);
    });
    let done_app = app;
    let on_done: DoneSink = Arc::new(move |snap| {
        let _ = done_app.emit("job-done", &snap);
    });
    (on_log, on_done)
}

#[tauri::command]
pub fn start_render(
    app: tauri::AppHandle,
    jobs: tauri::State<JobRegistry>,
    project: String,
    lua_rel: String,
    quality: String,
) -> Result<String, String> {
    let spec = build_render_spec(&project, &lua_rel, &quality)?;
    let (on_log, on_done) = log_and_done_sinks(app);
    jobs.spawn(spec, Some(on_log), Some(on_done))
}

#[tauri::command]
pub fn start_check(
    app: tauri::AppHandle,
    jobs: tauri::State<JobRegistry>,
    project: String,
    lua_rel: String,
    mode: String,
) -> Result<String, String> {
    let spec = build_check_spec(&project, &lua_rel, &mode)?;
    let (on_log, on_done) = log_and_done_sinks(app);
    jobs.spawn(spec, Some(on_log), Some(on_done))
}

#[tauri::command]
pub fn start_capture(
    app: tauri::AppHandle,
    jobs: tauri::State<JobRegistry>,
    project: String,
    url: String,
) -> Result<String, String> {
    let spec = build_capture_spec(&project, &url)?;
    let (on_log, on_done) = log_and_done_sinks(app);
    jobs.spawn(spec, Some(on_log), Some(on_done))
}

#[tauri::command]
pub fn list_capture_outputs(
    project: String,
    rel: String,
) -> Result<Vec<CaptureAsset>, String> {
    list_capture_assets(project, &rel)
}

#[tauri::command]
pub fn start_tts(
    app: tauri::AppHandle,
    jobs: tauri::State<JobRegistry>,
    project: String,
    node_id: String,
    kind: String,
    text: String,
) -> Result<String, String> {
    let lua_rel = write_studio_tts_comp(&project, &node_id, &kind, &text)?;
    let api_key = elevenlabs_api_key().ok();
    let spec = build_tts_hash_spec(&project, &lua_rel, api_key.as_deref())?;
    let (on_log, on_done) = log_and_done_sinks(app);
    jobs.spawn(spec, Some(on_log), Some(on_done))
}

#[tauri::command]
pub fn generate_local_vo_cmd(
    project: String,
    text: String,
    provider: Option<String>,
) -> Result<String, String> {
    generate_local_vo(&project, &text, provider.as_deref())
}

#[tauri::command]
pub fn import_tts_cache(project: String, kind: String, text: String) -> Result<String, String> {
    let sub = match kind.trim() {
        "tts" | "sfx" | "music" => kind.trim().to_string(),
        other => return Err(format!("kind must be tts|sfx|music, got {other}")),
    };
    let home = std::env::var("HOME").map_err(|_| "HOME not set".to_string())?;
    let cadence_dir = PathBuf::from(&home).join(".cache/cadence").join(&sub);
    if cadence_dir.is_dir() {
        if let Ok(res) = copy_cached_audio_from(&project, &cadence_dir, &sub, &text) {
            return Ok(res);
        }
    }
    let dir = PathBuf::from(home).join(".cache/ellua").join(&sub);
    copy_cached_audio_from(project, dir, &sub, &text)
}

#[tauri::command]
pub fn cancel_job(jobs: tauri::State<JobRegistry>, id: String) -> Result<(), String> {
    jobs.cancel(&id)
}

#[tauri::command]
pub fn job_status(jobs: tauri::State<JobRegistry>, id: String) -> Result<JobSnapshot, String> {
    jobs.snapshot(&id)
}

#[tauri::command]
pub fn list_jobs(jobs: tauri::State<JobRegistry>) -> Result<Vec<JobSnapshot>, String> {
    jobs.list()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::{Duration, Instant};

    fn pid_alive(pid: u32) -> bool {
        Command::new("kill")
            .args(["-0", &pid.to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map(|s| s.success())
            .unwrap_or(false)
    }

    fn wait_status(registry: &JobRegistry, id: &str, timeout: Duration) -> JobSnapshot {
        let deadline = Instant::now() + timeout;
        loop {
            let snap = registry.snapshot(id).unwrap();
            if snap.status != JobStatus::Running {
                return snap;
            }
            if Instant::now() > deadline {
                panic!("timed out waiting for job {id}: {snap:?}");
            }
            thread::sleep(Duration::from_millis(25));
        }
    }

    #[cfg(unix)]
    #[test]
    fn terminate_then_kill_skips_signal_when_child_already_reaped() {
        let mut child = Command::new("/bin/sleep");
        child.arg("30");
        let mut live = child.spawn().unwrap();
        let pid = live.id();
        assert!(pid_alive(pid), "sleep should be running");
        terminate_then_kill(pid, &std::sync::Mutex::new(None));
        assert!(
            pid_alive(pid),
            "reaped job must not SIGTERM a reused/unrelated pid {pid}"
        );
        let _ = live.kill();
        let _ = live.wait();
    }

    #[cfg(unix)]
    #[test]
    fn cancel_kills_a_long_running_child() {
        let registry = JobRegistry::new();
        let mut cmd = Command::new("/bin/sleep");
        cmd.arg("30");
        let id = registry.spawn_command(cmd).unwrap();

        let pid = registry.pid(&id).unwrap();
        assert!(pid_alive(pid), "sleep should be running before cancel");
        assert_eq!(registry.snapshot(&id).unwrap().status, JobStatus::Running);

        registry.cancel(&id).unwrap();

        let deadline = Instant::now() + Duration::from_secs(2);
        while pid_alive(pid) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(
            !pid_alive(pid),
            "cancel should kill the child (pid {pid} still alive)"
        );
        let snap = wait_status(&registry, &id, Duration::from_secs(2));
        assert_eq!(snap.status, JobStatus::Cancelled);
    }

    #[cfg(unix)]
    #[test]
    fn concurrent_jobs_cancel_independently() {
        let registry = JobRegistry::new();
        let mut first = Command::new("/bin/sleep");
        first.arg("30");
        let mut second = Command::new("/bin/sleep");
        second.arg("30");
        let id_a = registry.spawn_command(first).unwrap();
        let id_b = registry.spawn_command(second).unwrap();
        assert_ne!(id_a, id_b);
        let pid_b = registry.pid(&id_b).unwrap();
        assert!(pid_alive(pid_b));

        registry.cancel(&id_a).unwrap();
        let snap_a = wait_status(&registry, &id_a, Duration::from_secs(2));
        assert_eq!(snap_a.status, JobStatus::Cancelled);
        assert_eq!(registry.snapshot(&id_b).unwrap().status, JobStatus::Running);
        assert!(pid_alive(pid_b), "sibling job must keep running");

        registry.cancel(&id_b).unwrap();
        let snap_b = wait_status(&registry, &id_b, Duration::from_secs(2));
        assert_eq!(snap_b.status, JobStatus::Cancelled);
        assert!(!pid_alive(pid_b));
    }

    #[cfg(unix)]
    #[test]
    fn cancel_all_kills_live_children() {
        let registry = JobRegistry::new();
        let mut first = Command::new("/bin/sleep");
        first.arg("30");
        let mut second = Command::new("/bin/sleep");
        second.arg("30");
        let id_a = registry.spawn_command(first).unwrap();
        let id_b = registry.spawn_command(second).unwrap();
        let pid_a = registry.pid(&id_a).unwrap();
        let pid_b = registry.pid(&id_b).unwrap();
        assert!(pid_alive(pid_a) && pid_alive(pid_b));

        registry.cancel_all().unwrap();

        let snap_a = wait_status(&registry, &id_a, Duration::from_secs(2));
        let snap_b = wait_status(&registry, &id_b, Duration::from_secs(2));
        assert_eq!(snap_a.status, JobStatus::Cancelled);
        assert_eq!(snap_b.status, JobStatus::Cancelled);
        assert!(!pid_alive(pid_a) && !pid_alive(pid_b));
    }

    #[cfg(unix)]
    #[test]
    fn job_logs_keep_last_200_lines() {
        let registry = JobRegistry::new();
        let mut cmd = Command::new("seq");
        cmd.args(["1", "250"]);
        let id = registry.spawn_command(cmd).unwrap();
        let snap = wait_status(&registry, &id, Duration::from_secs(5));
        assert_eq!(snap.status, JobStatus::Succeeded);
        let lines: Vec<&str> = snap.stdout.lines().collect();
        assert_eq!(
            lines.len(),
            MAX_LOG_LINES,
            "expected {MAX_LOG_LINES} log lines, got {}",
            lines.len()
        );
        assert_eq!(lines.first().copied(), Some("51"));
        assert_eq!(lines.last().copied(), Some("250"));
    }

    #[cfg(unix)]
    #[test]
    fn registry_keeps_at_most_20_finished_jobs() {
        let registry = JobRegistry::new();
        let mut last_id = String::new();
        for _ in 0..25 {
            let mut cmd = Command::new("/bin/sh");
            cmd.args(["-c", "exit 0"]);
            last_id = registry.spawn_command(cmd).unwrap();
            let snap = wait_status(&registry, &last_id, Duration::from_secs(5));
            assert_ne!(snap.status, JobStatus::Running);
        }
        let listed = registry.list().unwrap();
        assert_eq!(
            listed.len(),
            MAX_FINISHED_JOBS,
            "finished jobs should cap at {MAX_FINISHED_JOBS}, got {}",
            listed.len()
        );
        assert!(
            listed.iter().any(|job| job.id == last_id),
            "newest finished job {last_id} should be retained"
        );
    }

    #[cfg(unix)]
    #[test]
    fn failed_job_leaves_last_good_output() {
        let dir = tempfile::tempdir().unwrap();
        let dest = dir.path().join("hello.mp4");
        let tmp = dir.path().join(".hello.mp4.tmp");
        std::fs::write(&dest, b"GOOD-BYTES").unwrap();

        let registry = JobRegistry::new();
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c")
            .arg("echo BAD > \"$1\"; echo boom >&2; exit 1")
            .arg("sh")
            .arg(tmp.as_os_str());

        let id = registry
            .spawn(
                SpawnSpec {
                    command: cmd,
                    temp_out: Some(tmp.clone()),
                    final_out: Some(dest.clone()),
                    output_rel: Some("renders/hello.mp4".into()),
                },
                None,
                None,
            )
            .unwrap();

        let snap = wait_status(&registry, &id, Duration::from_secs(5));
        assert_eq!(snap.status, JobStatus::Failed);
        assert!(
            snap.stderr.contains("boom"),
            "stderr should surface on the job, got {:?}",
            snap.stderr
        );
        assert_eq!(std::fs::read(&dest).unwrap(), b"GOOD-BYTES");
        assert!(!tmp.exists(), "failed render must not leave the temp file");
    }

    #[cfg(unix)]
    #[test]
    fn success_replaces_output() {
        let dir = tempfile::tempdir().unwrap();
        let dest = dir.path().join("hello.mp4");
        let tmp = dir.path().join(".hello.mp4.tmp");
        std::fs::write(&dest, b"GOOD-BYTES").unwrap();

        let registry = JobRegistry::new();
        let mut cmd = Command::new("/bin/sh");
        cmd.arg("-c")
            .arg("printf NEW > \"$1\"; exit 0")
            .arg("sh")
            .arg(tmp.as_os_str());

        let id = registry
            .spawn(
                SpawnSpec {
                    command: cmd,
                    temp_out: Some(tmp.clone()),
                    final_out: Some(dest.clone()),
                    output_rel: Some("renders/hello.mp4".into()),
                },
                None,
                None,
            )
            .unwrap();

        let snap = wait_status(&registry, &id, Duration::from_secs(5));
        assert_eq!(snap.status, JobStatus::Succeeded);
        assert_eq!(std::fs::read(&dest).unwrap(), b"NEW");
        assert!(!tmp.exists());
    }

    #[test]
    fn render_spec_passes_inputs_and_quality() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("comps")).unwrap();
        std::fs::write(dir.path().join("comps/hello.lua"), "return {}\n").unwrap();
        std::fs::write(dir.path().join("comps/hello.inputs.json"), r#"{"bg":"x"}"#).unwrap();

        let spec = build_render_spec(dir.path(), "comps/hello.lua", "draft").unwrap();
        let debug = format!("{:?}", spec.command);
        assert!(debug.contains("--inputs"), "command should pass --inputs: {debug}");
        assert!(
            debug.contains("hello.inputs.json"),
            "command should point at sidecar json: {debug}"
        );
        assert_eq!(spec.output_rel.as_deref(), Some("renders/hello.mp4"));
        let tmp = spec.temp_out.as_ref().unwrap();
        assert_ne!(tmp, spec.final_out.as_ref().unwrap());
        let name = tmp.file_name().unwrap().to_string_lossy();
        assert!(name.starts_with('.'), "temp should be hidden, got {name}");
        assert!(
            name.ends_with(".mp4"),
            "ffmpeg needs an .mp4 extension, got {name}"
        );
    }

    #[test]
    fn render_spec_rejects_bad_quality() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("comps")).unwrap();
        std::fs::write(dir.path().join("comps/hello.lua"), "return {}\n").unwrap();
        let err = build_render_spec(dir.path(), "comps/hello.lua", "ultra").unwrap_err();
        assert!(err.contains("quality"), "got {err}");
    }

    fn love_engine_present() -> bool {
        if std::env::var("LOVE_BIN")
            .ok()
            .filter(|s| !s.is_empty())
            .is_some()
        {
            return true;
        }
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../build/ellua-love-macos-mega/love/ellua-love")
            .is_file()
    }

    #[test]
    fn hello_lua_render_frame_count_is_duration_times_fps() {
        if !love_engine_present() || ellua_bin().is_err() {
            eprintln!(
                "skip hello.lua render+ffprobe: LOVE_BIN / ellua-love not present"
            );
            return;
        }
        let hello = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../examples/basics/hello.lua");
        assert!(hello.is_file(), "missing {}", hello.display());

        let dir = tempfile::tempdir().unwrap();
        crate::project::open_project(dir.path()).unwrap();
        std::fs::copy(&hello, dir.path().join("comps/hello.lua")).unwrap();

        let spec = build_render_spec(dir.path(), "comps/hello.lua", "draft").unwrap();
        let registry = JobRegistry::new();
        let id = registry.spawn(spec, None, None).unwrap();
        let snap = wait_status(&registry, &id, Duration::from_secs(180));
        assert_eq!(
            snap.status,
            JobStatus::Succeeded,
            "render failed: stderr={} error={:?}",
            snap.stderr,
            snap.error
        );

        let mp4 = dir.path().join("renders/hello.mp4");
        assert!(mp4.is_file(), "expected {}", mp4.display());

        let probe = Command::new("ffprobe")
            .args([
                "-v",
                "error",
                "-select_streams",
                "v",
                "-count_frames",
                "-show_entries",
                "stream=nb_read_frames",
                "-of",
                "csv=p=0",
            ])
            .arg(&mp4)
            .output()
            .expect("ffprobe must exist for encode verification; do not fake this");
        assert!(
            probe.status.success(),
            "ffprobe failed: {}",
            String::from_utf8_lossy(&probe.stderr)
        );
        let frames = String::from_utf8_lossy(&probe.stdout).trim().to_string();
        assert_eq!(
            frames, "120",
            "frame count must equal duration×fps (4×30); got {frames:?}"
        );
    }

    #[test]
    fn capture_spec_passes_url_and_outdir_without_network() {
        let dir = tempfile::tempdir().unwrap();
        crate::project::open_project(dir.path()).unwrap();

        let spec = build_capture_spec(dir.path(), "https://example.com/path").unwrap();
        assert_eq!(
            spec.output_rel.as_deref(),
            Some("assets/capture/example.com")
        );
        let debug = format!("{:?}", spec.command);
        assert!(
            debug.contains("cadence-capture") || debug.contains("ellua-capture"),
            "command should invoke cadence-capture or ellua-capture: {debug}"
        );
        assert!(
            debug.contains("https://example.com/path"),
            "command should pass the url: {debug}"
        );
        assert!(
            debug.contains("assets/capture/example.com"),
            "command should pass the host outdir: {debug}"
        );
        assert!(
            !debug.contains("curl"),
            "spec construction must not hit the network: {debug}"
        );
    }

    #[test]
    fn capture_host_is_sanitized_from_url() {
        assert_eq!(
            capture_host_from_url("https://Example.COM/foo").unwrap(),
            "example.com"
        );
        assert_eq!(
            capture_host_from_url("example.com").unwrap(),
            "example.com"
        );
        let err = capture_host_from_url("ftp://example.com").unwrap_err();
        assert!(err.contains("http"), "got {err}");
        assert_eq!(
            capture_host_from_url("https://evil.com/../../tmp").unwrap(),
            "evil.com"
        );
        let dots = capture_host_from_url("https://..").unwrap_err();
        assert!(dots.contains("invalid host"), "got {dots}");
    }

    #[test]
    fn tts_temp_lua_contains_the_prompt() {
        let tts = studio_tts_lua("tts", "Hello studio").unwrap();
        assert!(tts.contains("s:tts"), "got {tts}");
        assert!(tts.contains("Hello studio"), "got {tts}");
        let sfx = studio_tts_lua("sfx", "whoosh into impact").unwrap();
        assert!(sfx.contains("s:sfx"), "got {sfx}");
        assert!(sfx.contains("whoosh into impact"), "got {sfx}");
        let music = studio_tts_lua("music", "soft pad").unwrap();
        assert!(music.contains("s:music"), "got {music}");
        assert!(music.contains("soft pad"), "got {music}");
        let quoted = studio_tts_lua("tts", r#"say "hi""#).unwrap();
        assert!(quoted.contains(r#"say \"hi\""#), "got {quoted}");
        let nul = studio_tts_lua("tts", "ok\0done").unwrap();
        assert!(nul.contains(r#"ok\000done"#), "got {nul}");
        assert!(!nul.contains('\0'), "lua source must not contain a raw NUL");
    }

    #[test]
    fn studio_tts_cache_filename_matches_resolve_lua() {
        if std::env::var("ELLUA_DRAFT").ok().as_deref() != Some("1") {
            assert_eq!(
                studio_tts_cache_filename("tts", "Hello studio").unwrap(),
                "8fe63f3ec281c1b0d737dc2377553bcd3980975f.mp3"
            );
        }
        assert_eq!(
            studio_tts_cache_filename("sfx", "whoosh into impact").unwrap(),
            "19aa6c25fdb84e285b0625ba92518502429ddc87.mp3"
        );
        assert_eq!(
            studio_tts_cache_filename("music", "soft pad").unwrap(),
            "96a9166cfb1795b1ba95b4d7b33aad3ad82f5264.mp3"
        );
    }

    #[test]
    fn write_studio_tts_comp_lands_under_comps() {
        let dir = tempfile::tempdir().unwrap();
        crate::project::open_project(dir.path()).unwrap();
        let rel = write_studio_tts_comp(dir.path(), "tts-abc", "tts", "Narrate this").unwrap();
        assert_eq!(rel, "comps/.studio-tts-tts-abc.lua");
        let body = std::fs::read_to_string(dir.path().join(&rel)).unwrap();
        assert!(body.contains("Narrate this"), "got {body}");
        assert!(body.contains("s:tts"), "got {body}");

        let spec = build_tts_hash_spec(dir.path(), &rel, Some("test-key")).unwrap();
        let debug = format!("{:?}", spec.command);
        assert!(debug.contains("hash"), "command should be ellua hash: {debug}");
        assert!(
            debug.contains(".studio-tts-tts-abc.lua"),
            "command should hash the temp lua: {debug}"
        );
    }

    #[test]
    fn copies_cache_keyed_mp3_not_newest() {
        let project = tempfile::tempdir().unwrap();
        crate::project::open_project(project.path()).unwrap();
        let cache = tempfile::tempdir().unwrap();
        let keyed = studio_tts_cache_filename("tts", "Hello studio").unwrap();
        std::fs::write(cache.path().join(&keyed), b"RIGHT-AUDIO").unwrap();
        thread::sleep(Duration::from_millis(30));
        std::fs::write(cache.path().join("zzzz-newer.mp3"), b"WRONG-NEWEST").unwrap();
        std::fs::write(cache.path().join("ignore.align.json"), b"{}").unwrap();

        let rel = copy_cached_audio_from(project.path(), cache.path(), "tts", "Hello studio")
            .unwrap();
        assert!(
            rel.starts_with("assets/in/tts-") && rel.ends_with(".mp3"),
            "expected assets/in/tts-<sha12>.mp3, got {rel}"
        );
        assert_eq!(
            std::fs::read(project.path().join(&rel)).unwrap(),
            b"RIGHT-AUDIO"
        );
    }

    fn html_shot_present() -> bool {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        root.join("native/release/ellua-html-shot").is_file()
            || root.join("target/release/ellua-html-shot").is_file()
    }

    #[test]
    fn capture_example_com_writes_brand_and_fonts_dir() {
        if !html_shot_present() {
            eprintln!(
                "skip live capture: ellua-html-shot not present (run bin/build-native)"
            );
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        crate::project::open_project(dir.path()).unwrap();
        let spec = build_capture_spec(dir.path(), "https://example.com").unwrap();
        let registry = JobRegistry::new();
        let id = registry.spawn(spec, None, None).unwrap();
        let snap = wait_status(&registry, &id, Duration::from_secs(180));
        assert_eq!(
            snap.status,
            JobStatus::Succeeded,
            "capture failed: stderr={} error={:?}",
            snap.stderr,
            snap.error
        );
        let host = dir.path().join("assets/capture/example.com");
        assert!(
            host.join("brand.json").is_file(),
            "expected brand.json under {}",
            host.display()
        );
        assert!(
            host.join("fonts").is_dir(),
            "expected fonts/ under {}",
            host.display()
        );
        assert!(
            host.join("page.png").is_file(),
            "expected page.png under {}",
            host.display()
        );
        let listed = list_capture_assets(dir.path(), "assets/capture/example.com").unwrap();
        assert!(
            listed.iter().any(|a| a.id == "page" && a.kind == "file"),
            "page.png should be a file handle, got {listed:?}"
        );
        assert!(
            listed.iter().any(|a| a.id == "brand" && a.kind == "brand"),
            "brand.json should be a brand handle, got {listed:?}"
        );
    }

    #[test]
    fn tts_hash_lua_is_written_before_spawn() {
        if !love_engine_present() || ellua_bin().is_err() {
            eprintln!("skip live TTS: LOVE_BIN / ellua-love not present");
            return;
        }
        if elevenlabs_api_key().is_err() {
            eprintln!("skip live TTS: ElevenLabs key not set");
            return;
        }
        let dir = tempfile::tempdir().unwrap();
        crate::project::open_project(dir.path()).unwrap();
        let rel = write_studio_tts_comp(dir.path(), "live", "tts", "Ellua studio check").unwrap();
        let body = std::fs::read_to_string(dir.path().join(&rel)).unwrap();
        assert!(body.contains("Ellua studio check"), "got {body}");
        let key = elevenlabs_api_key().ok();
        let spec = build_tts_hash_spec(dir.path(), &rel, key.as_deref()).unwrap();
        let registry = JobRegistry::new();
        let id = registry.spawn(spec, None, None).unwrap();
        let snap = wait_status(&registry, &id, Duration::from_secs(180));
        assert_eq!(
            snap.status,
            JobStatus::Succeeded,
            "tts hash failed: stderr={} error={:?}",
            snap.stderr,
            snap.error
        );
        let cache = PathBuf::from(std::env::var("HOME").unwrap()).join(".cache/ellua/tts");
        let copied = copy_cached_audio_from(dir.path(), &cache, "tts", "Ellua studio check").unwrap();
        assert!(
            copied.starts_with("assets/in/tts-") && copied.ends_with(".mp3"),
            "got {copied}"
        );
        assert!(dir.path().join(&copied).is_file());
    }

    fn command_argv(cmd: &Command) -> Vec<String> {
        std::iter::once(cmd.get_program().to_string_lossy().into_owned())
            .chain(cmd.get_args().map(|s| s.to_string_lossy().into_owned()))
            .collect()
    }

    fn env_value(cmd: &Command, key: &str) -> Option<String> {
        cmd.get_envs().find_map(|(k, v)| {
            if k == std::ffi::OsStr::new(key) {
                v.map(|val| val.to_string_lossy().into_owned())
            } else {
                None
            }
        })
    }

    #[test]
    fn check_spec_lint_and_check_pass_json_hash_does_not() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("comps")).unwrap();
        std::fs::write(dir.path().join("comps/hello.lua"), "return {}\n").unwrap();
        std::fs::write(dir.path().join("comps/hello.inputs.json"), r#"{"bg":"x"}"#).unwrap();

        let lint = build_check_spec(dir.path(), "comps/hello.lua", "lint").unwrap();
        let lint_argv = command_argv(&lint.command);
        assert!(
            lint_argv.iter().any(|a| a == "lint"),
            "lint argv should contain lint: {lint_argv:?}"
        );
        assert!(
            lint_argv.iter().any(|a| a.ends_with("hello.lua") || a == "comps/hello.lua"),
            "lint argv should contain the lua path: {lint_argv:?}"
        );
        assert!(
            lint_argv.iter().any(|a| a == "--json"),
            "lint should pass --json: {lint_argv:?}"
        );
        assert!(
            lint_argv.iter().any(|a| a == "--inputs"),
            "lint should pass sibling --inputs: {lint_argv:?}"
        );
        assert_eq!(
            env_value(&lint.command, "ELLUA_CWD").as_deref(),
            Some(dir.path().to_str().unwrap())
        );

        let check = build_check_spec(dir.path(), "comps/hello.lua", "check").unwrap();
        let check_argv = command_argv(&check.command);
        assert!(check_argv.iter().any(|a| a == "check"), "got {check_argv:?}");
        assert!(check_argv.iter().any(|a| a == "--json"), "check should pass --json: {check_argv:?}");

        let hash = build_check_spec(dir.path(), "comps/hello.lua", "hash").unwrap();
        let hash_argv = command_argv(&hash.command);
        assert!(hash_argv.iter().any(|a| a == "hash"), "got {hash_argv:?}");
        assert!(
            !hash_argv.iter().any(|a| a == "--json"),
            "hash must not pass --json: {hash_argv:?}"
        );

        let debug = format!("{:?}", lint.command);
        assert!(
            !debug.to_lowercase().contains("curl") && !debug.contains("http"),
            "spec construction must not hit the network: {debug}"
        );
    }

    #[test]
    fn check_spec_rejects_bad_mode() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("comps")).unwrap();
        std::fs::write(dir.path().join("comps/hello.lua"), "return {}\n").unwrap();
        let err = build_check_spec(dir.path(), "comps/hello.lua", "probe").unwrap_err();
        assert!(err.contains("mode"), "got {err}");
    }

    #[test]
    fn normalize_lua_rel_rejects_traversal_and_non_comps() {
        for rel in [
            "../comps/hello.lua",
            "comps/../secret.lua",
            "comps/foo/../../outside.lua",
            "/tmp/hello.lua",
            "comps/hello.lua/../../etc/passwd.lua",
            "file://comps/hello.lua",
            "hello.lua",
            "renders/hello.lua",
            "comps/hello.lua\0../../x.lua",
        ] {
            let err = normalize_lua_rel(rel).unwrap_err();
            assert!(
                err.contains("path") || err.contains("expected comps"),
                "expected reject for {rel:?}, got {err}"
            );
        }
        assert_eq!(
            normalize_lua_rel("comps/hello.lua").unwrap(),
            "comps/hello.lua"
        );
        assert_eq!(
            normalize_lua_rel("comps\\nested\\hello.lua").unwrap(),
            "comps/nested/hello.lua"
        );
    }

    #[test]
    fn check_and_render_specs_reject_lua_path_traversal() {
        let dir = tempfile::tempdir().unwrap();
        let check_err =
            build_check_spec(dir.path(), "comps/../secret.lua", "lint").unwrap_err();
        assert!(
            check_err.contains("path") || check_err.contains("expected comps"),
            "got {check_err}"
        );
        let render_err =
            build_render_spec(dir.path(), "../comps/hello.lua", "draft").unwrap_err();
        assert!(
            render_err.contains("path") || render_err.contains("expected comps"),
            "got {render_err}"
        );
    }

    #[cfg(unix)]
    #[test]
    fn failed_check_leaves_sibling_job_running() {
        let registry = JobRegistry::new();
        let mut sleep = Command::new("/bin/sleep");
        sleep.arg("30");
        let live = registry.spawn_command(sleep).unwrap();
        let pid = registry.pid(&live).unwrap();
        assert!(pid_alive(pid));

        let mut fail = Command::new("/bin/sh");
        fail.args(["-c", "echo overlapping tweens on r.x >&2; exit 1"]);
        let dead = registry.spawn_command(fail).unwrap();
        let snap = wait_status(&registry, &dead, Duration::from_secs(5));
        assert_eq!(snap.status, JobStatus::Failed);
        assert!(
            snap.stderr.contains("overlap"),
            "stderr should surface on the failed job, got {:?}",
            snap.stderr
        );
        assert_eq!(registry.snapshot(&live).unwrap().status, JobStatus::Running);
        assert!(pid_alive(pid), "sibling job must keep running after check failure");
        registry.cancel(&live).unwrap();
    }

    #[test]
    fn overlap_lua_lint_exits_nonzero() {
        if !love_engine_present() || ellua_bin().is_err() {
            eprintln!("skip live overlap lint: LOVE_BIN / ellua-love not present");
            return;
        }
        let fixture =
            Path::new(env!("CARGO_MANIFEST_DIR")).join("../../tests/fixtures/overlap.lua");
        assert!(fixture.is_file(), "missing {}", fixture.display());

        let dir = tempfile::tempdir().unwrap();
        crate::project::open_project(dir.path()).unwrap();
        std::fs::copy(&fixture, dir.path().join("comps/overlap.lua")).unwrap();

        let spec = build_check_spec(dir.path(), "comps/overlap.lua", "lint").unwrap();
        let registry = JobRegistry::new();
        let id = registry.spawn(spec, None, None).unwrap();
        let snap = wait_status(&registry, &id, Duration::from_secs(60));
        assert_eq!(
            snap.status,
            JobStatus::Failed,
            "overlap.lua lint should fail: stdout={} stderr={} error={:?}",
            snap.stdout,
            snap.stderr,
            snap.error
        );
        let blob = format!(
            "{}{}",
            snap.stderr,
            snap.error.clone().unwrap_or_default()
        );
        assert!(
            blob.to_lowercase().contains("overlap"),
            "stderr should mention overlap, got {blob}"
        );
    }
}
