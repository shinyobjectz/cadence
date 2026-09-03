mod editor_vo;
mod gateway;
mod jobs;
mod project;
mod secrets;

use editor_vo::ensure_editor_vo;
use gateway::{generate_image, generate_text, generate_video};
use jobs::{
    cancel_job, doctor_cadence, generate_local_vo_cmd, import_tts_cache, job_status,
    list_capture_outputs, list_jobs, start_capture, start_check, start_render, start_tts,
    start_verify, verify_comp, JobRegistry,
};
use project::StudioFile;
use secrets::{secret_is_set, set_secret};
use tauri::Manager;
use tauri_plugin_dialog::DialogExt;

#[tauri::command]
fn open_project(path: String) -> Result<StudioFile, String> {
    project::open_project(path)
}

#[tauri::command]
fn save_studio(path: String, json: StudioFile) -> Result<(), String> {
    project::save_studio(path, &json)
}

#[tauri::command]
fn ensure_editor_vo_cmd(project: String, force: Option<bool>) -> Result<editor_vo::EnsureVoResult, String> {
    ensure_editor_vo(project, force.unwrap_or(false))
}

#[tauri::command]
async fn pick_folder(app: tauri::AppHandle) -> Result<Option<String>, String> {
    match app.dialog().file().blocking_pick_folder() {
        None => Ok(None),
        Some(folder) => folder
            .into_path()
            .map(|path| Some(path.to_string_lossy().into_owned()))
            .map_err(|err| err.to_string()),
    }
}

#[tauri::command]
async fn pick_file(
    app: tauri::AppHandle,
    extensions: Option<Vec<String>>,
) -> Result<Option<String>, String> {
    let mut builder = app.dialog().file();
    if let Some(exts) = extensions.as_ref() {
        if !exts.is_empty() {
            let refs: Vec<&str> = exts.iter().map(String::as_str).collect();
            builder = builder.add_filter("Files", &refs);
        }
    }
    match builder.blocking_pick_file() {
        None => Ok(None),
        Some(file) => file
            .into_path()
            .map(|path| Some(path.to_string_lossy().into_owned()))
            .map_err(|err| err.to_string()),
    }
}

#[tauri::command]
fn import_file(project: String, src: String) -> Result<String, String> {
    project::import_file(project, src)
}

#[tauri::command]
fn import_comp(project: String, src: String) -> Result<String, String> {
    project::import_comp(project, src)
}

#[tauri::command]
fn read_project_file(project: String, rel: String) -> Result<String, String> {
    project::read_project_file(project, &rel)
}

#[tauri::command]
fn read_ellua_lib(name: String) -> Result<String, String> {
    project::read_ellua_lib(&name)
}

#[tauri::command]
fn read_ellua_eval(rel: String) -> Result<String, String> {
    project::read_ellua_eval(&rel)
}

#[tauri::command]
fn resolve_ellua_asset(rel: String) -> Result<String, String> {
    project::resolve_ellua_asset(&rel)
}

#[tauri::command]
fn resolve_cadence_path(rel: String) -> Result<String, String> {
    project::resolve_cadence_path(&rel)
}

#[tauri::command]
fn resolve_project_asset(project: String, rel: String) -> Result<String, String> {
    project::resolve_project_asset(project, &rel)
}

#[tauri::command]
fn open_editor_doc(project: String) -> Result<String, String> {
    project::open_editor_doc(project)
}

#[tauri::command]
fn read_editor_doc(project: String) -> Result<String, String> {
    project::read_editor_doc(project)
}

#[tauri::command]
fn save_editor_doc(project: String, json: String) -> Result<(), String> {
    project::save_editor_doc(project, &json)
}

#[tauri::command]
fn write_comp_inputs(
    project: String,
    lua_rel: String,
    name: String,
    file_rel: Option<String>,
) -> Result<(), String> {
    project::write_comp_inputs(project, &lua_rel, &name, file_rel.as_deref())
}

#[tauri::command]
fn save_asset_bytes(
    project: String,
    stem: String,
    ext: String,
    bytes: Vec<u8>,
) -> Result<String, String> {
    project::save_asset_bytes(project, &stem, &ext, &bytes)
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(JobRegistry::new())
        .invoke_handler(tauri::generate_handler![
            open_project,
            save_studio,
            pick_folder,
            pick_file,
            import_file,
            import_comp,
            read_project_file,
            read_ellua_lib,
            read_ellua_eval,
            resolve_ellua_asset,
            resolve_cadence_path,
            resolve_project_asset,
            open_editor_doc,
            ensure_editor_vo_cmd,
            read_editor_doc,
            save_editor_doc,
            write_comp_inputs,
            save_asset_bytes,
            set_secret,
            secret_is_set,
            generate_text,
            generate_image,
            generate_video,
            start_render,
            start_check,
            start_verify,
            verify_comp,
            doctor_cadence,
            start_capture,
            list_capture_outputs,
            start_tts,
            generate_local_vo_cmd,
            import_tts_cache,
            cancel_job,
            job_status,
            list_jobs
        ])
        .setup(|app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app, event| {
            if matches!(
                event,
                tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
            ) {
                if let Some(jobs) = app.try_state::<JobRegistry>() {
                    let _ = jobs.cancel_all();
                }
            }
        });
}
