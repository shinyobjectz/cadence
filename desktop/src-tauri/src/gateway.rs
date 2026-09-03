//! Vercel AI Gateway HTTP from Rust. Keys stay in the sidecar; the webview never fetch()es.

use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::Duration;

use base64::Engine;
use futures_util::StreamExt;
use serde::Serialize;
use serde_json::Value;
use tauri::Emitter;

use crate::jobs::{JobSnapshot, JobStatus};
use crate::project::save_asset_bytes;
use crate::secrets;

pub const GATEWAY_V1: &str = "https://ai-gateway.vercel.sh/v1";
const PLACEHOLDER_VIDEO: &str = "placeholder-video";

static NEXT_GEN_JOB: AtomicU64 = AtomicU64::new(1);

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct GenTextEvent {
    job_id: String,
    delta: String,
}

fn gateway_model(id: &str) -> String {
    if id.contains('/') {
        id.to_string()
    } else {
        format!("openai/{id}")
    }
}

fn next_job_id() -> String {
    format!("gen-{}", NEXT_GEN_JOB.fetch_add(1, Ordering::Relaxed))
}

fn http_error(status: reqwest::StatusCode, body: &str) -> String {
    let snippet: String = body.chars().take(280).collect();
    if snippet.is_empty() {
        format!("AI Gateway error {status}")
    } else {
        format!("AI Gateway error {status}: {snippet}")
    }
}

async fn post_json(
    client: &reqwest::Client,
    url: &str,
    api_key: &str,
    body: &Value,
) -> Result<reqwest::Response, String> {
    client
        .post(url)
        .header("Authorization", format!("Bearer {api_key}"))
        .header("Content-Type", "application/json")
        .json(body)
        .send()
        .await
        .map_err(|e| e.to_string())
}

fn parse_image_b64(value: &Value) -> Result<String, String> {
    let data = value
        .get("data")
        .and_then(|d| d.as_array())
        .and_then(|arr| arr.first())
        .ok_or_else(|| "AI Gateway image response missing data".to_string())?;
    if let Some(b64) = data.get("b64_json").and_then(|v| v.as_str()) {
        if !b64.is_empty() {
            return Ok(b64.to_string());
        }
    }
    Err("AI Gateway image response missing b64_json".into())
}

fn parse_image_url(value: &Value) -> Option<String> {
    value
        .get("data")
        .and_then(|d| d.as_array())
        .and_then(|arr| arr.first())
        .and_then(|item| item.get("url"))
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty())
        .map(str::to_string)
}

async fn decode_image_bytes(
    client: &reqwest::Client,
    api_key: &str,
    json: &Value,
) -> Result<Vec<u8>, String> {
    if let Ok(b64) = parse_image_b64(json) {
        return base64::engine::general_purpose::STANDARD
            .decode(b64.trim())
            .map_err(|e| format!("invalid image base64: {e}"));
    }
    let url = parse_image_url(json).ok_or_else(|| parse_image_b64(json).unwrap_err())?;
    let resp = client
        .get(&url)
        .header("Authorization", format!("Bearer {api_key}"))
        .send()
        .await
        .map_err(|e| e.to_string())?;
    if !resp.status().is_success() {
        let status = resp.status();
        let body = resp.text().await.unwrap_or_default();
        return Err(http_error(status, &body));
    }
    resp.bytes().await.map(|b| b.to_vec()).map_err(|e| e.to_string())
}

pub async fn generate_image_with(
    project: &Path,
    prompt: &str,
    model: &str,
    client: &reqwest::Client,
    base_url: &str,
    api_key: &str,
) -> Result<String, String> {
    if prompt.trim().is_empty() {
        return Err("prompt is empty".into());
    }
    let url = format!("{}/images/generations", base_url.trim_end_matches('/'));
    let body = serde_json::json!({
        "model": gateway_model(model),
        "prompt": prompt,
        "n": 1,
        "response_format": "b64_json",
        "size": "1024x1024",
    });
    let resp = post_json(client, &url, api_key, &body).await?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(http_error(status, &text));
    }
    let json: Value = resp.json().await.map_err(|e| e.to_string())?;
    let bytes = decode_image_bytes(client, api_key, &json).await?;
    save_asset_bytes(project, "image", "png", &bytes)
}

pub async fn generate_video_with(
    project: &Path,
    prompt: &str,
    model: &str,
    client: &reqwest::Client,
    base_url: &str,
    api_key: &str,
) -> Result<String, String> {
    if model == PLACEHOLDER_VIDEO || model.contains("placeholder") {
        return Err("Video generation is not available on AI Gateway".into());
    }
    if prompt.trim().is_empty() {
        return Err("prompt is empty".into());
    }
    let url = format!("{}/videos/generations", base_url.trim_end_matches('/'));
    let body = serde_json::json!({
        "model": gateway_model(model),
        "prompt": prompt,
    });
    let resp = post_json(client, &url, api_key, &body).await?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(format!(
            "Video generation is not available on AI Gateway ({})",
            http_error(status, &text)
        ));
    }
    let bytes = resp.bytes().await.map_err(|e| e.to_string())?;
    if bytes.is_empty() {
        return Err("Video generation is not available on AI Gateway".into());
    }
    save_asset_bytes(project, "video", "mp4", &bytes)
}

async fn stream_chat_text(
    client: &reqwest::Client,
    base_url: &str,
    api_key: &str,
    prompt: &str,
    model: &str,
    mut on_delta: impl FnMut(&str),
) -> Result<String, String> {
    if prompt.trim().is_empty() {
        return Err("prompt is empty".into());
    }
    let url = format!("{}/chat/completions", base_url.trim_end_matches('/'));
    let body = serde_json::json!({
        "model": gateway_model(model),
        "messages": [{ "role": "user", "content": prompt }],
        "stream": true,
    });
    let resp = post_json(client, &url, api_key, &body).await?;
    if !resp.status().is_success() {
        let status = resp.status();
        let text = resp.text().await.unwrap_or_default();
        return Err(http_error(status, &text));
    }
    let mut stream = resp.bytes_stream();
    let mut buffer = String::new();
    let mut full = String::new();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|e| e.to_string())?;
        buffer.push_str(&String::from_utf8_lossy(&chunk));
        buffer = buffer.replace("\r\n", "\n");
        while let Some(idx) = buffer.find("\n\n") {
            let event = buffer[..idx].to_string();
            buffer.drain(..idx + 2);
            for line in event.lines() {
                let line = line.trim();
                let Some(data) = line.strip_prefix("data:") else {
                    continue;
                };
                let data = data.trim();
                if data.is_empty() || data == "[DONE]" {
                    continue;
                }
                let Ok(value) = serde_json::from_str::<Value>(data) else {
                    continue;
                };
                if let Some(delta) = value
                    .pointer("/choices/0/delta/content")
                    .and_then(|v| v.as_str())
                {
                    if !delta.is_empty() {
                        full.push_str(delta);
                        on_delta(delta);
                    }
                }
            }
        }
    }
    Ok(full)
}

fn emit_job_done(app: &tauri::AppHandle, id: &str, result: Result<String, String>) {
    let (status, stdout, stderr, error) = match result {
        Ok(text) => (JobStatus::Succeeded, text, String::new(), None),
        Err(err) => (JobStatus::Failed, String::new(), err.clone(), Some(err)),
    };
    let snap = JobSnapshot {
        id: id.to_string(),
        status,
        stdout,
        stderr,
        output_rel: None,
        error,
    };
    let _ = app.emit("job-done", &snap);
}

#[tauri::command]
pub fn generate_text(
    app: tauri::AppHandle,
    prompt: String,
    model: String,
) -> Result<String, String> {
    let api_key = secrets::require_ai_gateway_key()?;
    let job_id = next_job_id();
    let app_emit = app.clone();
    let job_for_task = job_id.clone();
    tauri::async_runtime::spawn(async move {
        // Let the webview attach gen-text / job-done listeners before the first delta.
        thread::sleep(Duration::from_millis(80));
        let client = reqwest::Client::builder()
            .timeout(Duration::from_secs(120))
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());
        let job_for_delta = job_for_task.clone();
        let result = stream_chat_text(
            &client,
            GATEWAY_V1,
            &api_key,
            &prompt,
            &model,
            |delta| {
                let _ = app_emit.emit(
                    "gen-text",
                    &GenTextEvent {
                        job_id: job_for_delta.clone(),
                        delta: delta.to_string(),
                    },
                );
            },
        )
        .await;
        emit_job_done(&app_emit, &job_for_task, result);
    });
    Ok(job_id)
}

#[tauri::command]
pub async fn generate_image(
    project: String,
    prompt: String,
    model: String,
) -> Result<String, String> {
    let api_key = secrets::require_ai_gateway_key()?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;
    generate_image_with(
        Path::new(&project),
        &prompt,
        &model,
        &client,
        GATEWAY_V1,
        &api_key,
    )
    .await
}

#[tauri::command]
pub async fn generate_video(
    project: String,
    prompt: String,
    model: String,
) -> Result<String, String> {
    let api_key = secrets::require_ai_gateway_key()?;
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(120))
        .build()
        .map_err(|e| e.to_string())?;
    generate_video_with(
        Path::new(&project),
        &prompt,
        &model,
        &client,
        GATEWAY_V1,
        &api_key,
    )
    .await
}

#[cfg(test)]
pub(crate) fn spawn_json_server(status: u16, body: &'static str) -> (String, thread::JoinHandle<()>) {
    use std::io::{Read, Write};
    use std::net::TcpListener;
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind mock gateway");
    let addr = listener.local_addr().expect("local addr");
    let handle = thread::spawn(move || {
        let Ok((mut stream, _)) = listener.accept() else {
            return;
        };
        stream
            .set_read_timeout(Some(Duration::from_secs(2)))
            .ok();
        let mut buf = Vec::new();
        let mut tmp = [0u8; 2048];
        loop {
            match stream.read(&mut tmp) {
                Ok(0) => break,
                Ok(n) => {
                    buf.extend_from_slice(&tmp[..n]);
                    if let Some(header_end) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                        let headers = &buf[..header_end];
                        let content_length = headers
                            .split(|&b| b == b'\n')
                            .find_map(|line| {
                                let line = line.strip_suffix(b"\r").unwrap_or(line);
                                let lower = String::from_utf8_lossy(line).to_ascii_lowercase();
                                lower
                                    .strip_prefix("content-length:")
                                    .and_then(|v| v.trim().parse::<usize>().ok())
                            })
                            .unwrap_or(0);
                        if buf.len() >= header_end + 4 + content_length {
                            break;
                        }
                    }
                }
                Err(_) => break,
            }
        }
        let reason = if status == 200 { "OK" } else { "Error" };
        let resp = format!(
            "HTTP/1.1 {status} {reason}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
            body.len()
        );
        let _ = stream.write_all(resp.as_bytes());
        let _ = stream.flush();
    });
    (format!("http://{addr}/v1"), handle)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::project::{open_project, save_asset_bytes};

    const TINY_PNG: &[u8] = &[
        0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44,
        0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90,
        0x77, 0x53, 0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41, 0x54, 0x08, 0xD7, 0x63, 0xF8,
        0xCF, 0xC0, 0x00, 0x00, 0x00, 0x03, 0x00, 0x01, 0x18, 0xD8, 0x5E, 0xED, 0x00, 0x00, 0x00,
        0x00, 0x49, 0x45, 0x4E, 0x44, 0xAE, 0x42, 0x60, 0x82,
    ];

    fn block_on<T>(fut: impl std::future::Future<Output = T>) -> T {
        tauri::async_runtime::block_on(fut)
    }

    fn client() -> reqwest::Client {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .no_proxy()
            .build()
            .unwrap()
    }

    fn list_in(project: &Path) -> Vec<String> {
        let mut names: Vec<String> = std::fs::read_dir(project.join("assets/in"))
            .unwrap()
            .filter_map(|e| e.ok())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .collect();
        names.sort();
        names
    }

    #[test]
    fn generate_image_http_error_does_not_overwrite_output() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        let previous = save_asset_bytes(project.path(), "image", "png", TINY_PNG).unwrap();
        let previous_bytes = std::fs::read(project.path().join(&previous)).unwrap();
        let before = list_in(project.path());

        let (base, server) = spawn_json_server(500, r#"{"error":{"message":"boom"}}"#);
        let err = block_on(generate_image_with(
            project.path(),
            "a red square",
            "gpt-image-1",
            &client(),
            &base,
            "test-key",
        ))
        .unwrap_err();
        let _ = server.join();

        assert!(
            err.to_lowercase().contains("500") || err.to_lowercase().contains("boom"),
            "expected HTTP error, got {err}"
        );
        assert_eq!(list_in(project.path()), before);
        assert_eq!(
            std::fs::read(project.path().join(&previous)).unwrap(),
            previous_bytes
        );
    }

    #[test]
    fn generate_image_success_writes_png_under_assets_in() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        let b64 = base64::engine::general_purpose::STANDARD.encode(TINY_PNG);
        let body = format!(r#"{{"data":[{{"b64_json":"{b64}"}}]}}"#);
        let body_static: &'static str = Box::leak(body.into_boxed_str());
        let (base, server) = spawn_json_server(200, body_static);
        let rel = block_on(generate_image_with(
            project.path(),
            "a red square",
            "gpt-image-1",
            &client(),
            &base,
            "test-key",
        ))
        .unwrap();
        let _ = server.join();
        assert!(
            rel.starts_with("assets/in/image-") && rel.ends_with(".png"),
            "got {rel}"
        );
        assert_eq!(std::fs::read(project.path().join(&rel)).unwrap(), TINY_PNG);
    }

    #[test]
    fn generate_video_placeholder_does_not_write() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        let previous = save_asset_bytes(project.path(), "video", "mp4", b"FAKE-MP4").unwrap();
        let before = list_in(project.path());
        let err = block_on(generate_video_with(
            project.path(),
            "a walking cat",
            PLACEHOLDER_VIDEO,
            &client(),
            "http://127.0.0.1:1/v1",
            "test-key",
        ))
        .unwrap_err();
        assert!(
            err.to_lowercase().contains("not available"),
            "got {err}"
        );
        assert_eq!(list_in(project.path()), before);
        assert_eq!(
            std::fs::read(project.path().join(&previous)).unwrap(),
            b"FAKE-MP4"
        );
    }

    #[test]
    fn generate_video_http_error_keeps_previous_asset() {
        let project = tempfile::tempdir().unwrap();
        open_project(project.path()).unwrap();
        let previous = save_asset_bytes(project.path(), "video", "mp4", b"FAKE-MP4").unwrap();
        let before = list_in(project.path());
        let (base, server) = spawn_json_server(404, r#"{"error":"no video"}"#);
        let err = block_on(generate_video_with(
            project.path(),
            "a walking cat",
            "sora",
            &client(),
            &base,
            "test-key",
        ))
        .unwrap_err();
        let _ = server.join();
        assert!(
            err.to_lowercase().contains("not available"),
            "got {err}"
        );
        assert_eq!(list_in(project.path()), before);
        assert_eq!(
            std::fs::read(project.path().join(&previous)).unwrap(),
            b"FAKE-MP4"
        );
    }
}
