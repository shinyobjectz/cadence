use std::path::{Path, PathBuf};

fn stage_sidecar(name: &str, src_rel: &str) {
    let manifest = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    let src = manifest.join(src_rel);
    if !src.is_file() {
        println!("cargo:warning={name} launcher missing at {}", src.display());
        return;
    }
    let triple = std::env::var("TARGET").unwrap_or_else(|_| {
        std::env::var("HOST").unwrap_or_else(|_| "unknown".into())
    });
    let dest_dir = manifest.join("binaries");
    let _ = std::fs::create_dir_all(&dest_dir);
    let dest_name = if cfg!(windows) {
        format!("{name}-{triple}.exe")
    } else {
        format!("{name}-{triple}")
    };
    let dest = dest_dir.join(dest_name);
    if let Err(err) = std::fs::copy(&src, &dest) {
        println!(
            "cargo:warning=could not stage {name} sidecar {}: {err}",
            dest.display()
        );
        return;
    }
    set_executable(&dest);
}

fn set_executable(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if let Ok(meta) = std::fs::metadata(path) {
            let mut perms = meta.permissions();
            perms.set_mode(0o755);
            let _ = std::fs::set_permissions(path, perms);
        }
    }
}

fn main() {
    stage_sidecar("cadence", "../../bin/cadence");
    stage_sidecar("ellua", "../../bin/ellua");
    tauri_build::try_build(
        tauri_build::Attributes::new().app_manifest(
            tauri_build::AppManifest::new().commands(&[
                "open_project",
                "save_studio",
                "pick_folder",
                "pick_file",
                "import_file",
                "import_comp",
                "read_project_file",
                "read_ellua_lib",
                "read_ellua_eval",
                "resolve_ellua_asset",
                "resolve_cadence_path",
                "resolve_project_asset",
                "open_editor_doc",
                "ensure_editor_vo_cmd",
                "read_editor_doc",
                "save_editor_doc",
                "write_comp_inputs",
                "save_asset_bytes",
                "set_secret",
                "secret_is_set",
                "generate_text",
                "generate_image",
                "generate_video",
                "start_render",
                "start_check",
                "start_verify",
                "verify_comp",
                "doctor_cadence",
                "start_capture",
                "list_capture_outputs",
                "start_tts",
                "generate_local_vo_cmd",
                "import_tts_cache",
                "cancel_job",
                "job_status",
                "list_jobs",
            ]),
        ),
    )
    .expect("failed to run tauri-build");
}
