use anyhow::{Context, Result};
use clap::{Parser, Subcommand};
use std::path::{Path, PathBuf};

use cadence_model::backend::{self, Device, GenParams};
use cadence_model::{agent, eval, mutate, repo_root, tasks, validate};

#[derive(Parser)]
#[command(name = "cadence-model", about = "Cadence editor model: promptable editing agent + validation harness")]
struct Cli {
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(clap::Args, Clone)]
struct BackendArgs {
    /// mock | candle | ort | mlx
    #[arg(long, default_value = "mock")]
    backend: String,
    /// model directory (config.json, tokenizer.json, safetensors / model.onnx)
    #[arg(long)]
    model: Option<String>,
    /// cpu | metal | cuda | cuda:N
    #[arg(long, default_value = "cpu")]
    device: String,
    #[arg(long, default_value_t = 1024)]
    max_new_tokens: usize,
    #[arg(long, default_value_t = 0.0)]
    temperature: f64,
    #[arg(long, default_value_t = 0)]
    seed: u64,
}

impl BackendArgs {
    fn params(&self) -> GenParams {
        GenParams { max_new_tokens: self.max_new_tokens, temperature: self.temperature, seed: self.seed, ..Default::default() }
    }
    fn open(&self) -> Result<Box<dyn backend::Backend>> {
        backend::open(&self.backend, self.model.as_deref(), Device::parse(&self.device)?)
    }
}

#[derive(Subcommand)]
enum Cmd {
    /// What this build can do, and whether the toolchain is around
    Doctor,
    /// Edit one comp from an instruction, validated through bin/cadence verify
    Edit {
        comp: PathBuf,
        instruction: String,
        #[command(flatten)]
        be: BackendArgs,
        /// write the edited comp here (default: print to stdout)
        #[arg(short, long)]
        out: Option<PathBuf>,
        #[arg(long, default_value_t = 2)]
        attempts: usize,
        /// only print the model reply (no apply/verify) — for the GPU box
        #[arg(long)]
        raw: bool,
    },
    /// Run a task suite: generate replies and validate them
    Eval {
        #[arg(long, default_value = "model/tasks")]
        tasks: PathBuf,
        #[arg(long, default_value = "model/out/last")]
        out: PathBuf,
        /// only tasks whose id contains this
        #[arg(long)]
        filter: Option<String>,
        /// phase: run (default) | generate | validate
        #[arg(long, default_value = "run")]
        phase: String,
        #[command(flatten)]
        be: BackendArgs,
    },
    /// Synthesise tasks from comps by mutating tweens (render-and-mutate)
    GenTasks {
        /// comps or directories of comps (relative to the repo root)
        #[arg(long, default_value = "evals/cases", num_args = 1..)]
        comps: Vec<PathBuf>,
        #[arg(long, default_value = "model/tasks/generated")]
        out: PathBuf,
        /// tasks per comp
        #[arg(long, default_value_t = 3)]
        per_comp: usize,
        #[arg(long, default_value_t = 1)]
        seed: u64,
    },
    /// Tokens per second on a fixed prompt
    Bench {
        #[command(flatten)]
        be: BackendArgs,
        #[arg(long, default_value_t = 128)]
        tokens: usize,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    let root = repo_root();
    match cli.cmd {
        Cmd::Doctor => {
            println!("cadence-model {}", env!("CARGO_PKG_VERSION"));
            println!("root      {}", root.display());
            println!("backends  {}", backend::compiled().join(", "));
            let has = |p: &str| if which(p) { "ok" } else { "missing" };
            println!("luajit    {}", has("luajit"));
            println!("love      {}", has("love"));
            println!("ffmpeg    {}", has("ffmpeg"));
            println!("bin/cadence {}", if root.join("bin/cadence").exists() { "ok" } else { "missing" });
            match validate::props(&root.join("evals/cases/primitives.lua"), &[0.0]) {
                Ok((f, _)) => println!("props     ok ({} nodes)", f.nodes.len()),
                Err(e) => println!("props     failed: {e}"),
            }
            let m = std::env::var("CADENCE_MODEL_DIR").ok();
            println!("CADENCE_MODEL_DIR {}", m.unwrap_or_else(|| "unset".into()));
        }
        Cmd::Edit { comp, instruction, be, out, attempts, raw } => {
            let mut b = be.open()?;
            let params = be.params();
            if raw {
                let source = std::fs::read_to_string(&comp)?;
                let facts = validate::props(&comp, &[]).ok().map(|(f, _)| f);
                let (_p, g) = agent::generate_once(b.as_mut(), &instruction, &source, facts.as_ref(), &params)?;
                eprintln!("{} tokens in {:.1}s", g.gen_tokens, g.secs);
                println!("{}", g.text);
                return Ok(());
            }
            let r = agent::edit(b.as_mut(), &comp, &instruction, &params, attempts, None)?;
            for a in &r.attempts {
                eprintln!("attempt {}: {} tok {:.1}s {}", a.n, a.generation.gen_tokens, a.generation.secs,
                    a.failure.clone().unwrap_or_else(|| "ok".into()));
            }
            if !r.ok {
                anyhow::bail!("edit did not validate after {} attempts", r.attempts.len());
            }
            match out {
                Some(p) => { std::fs::write(&p, &r.source)?; eprintln!("wrote {}", p.display()); }
                None => print!("{}", r.source),
            }
        }
        Cmd::Eval { tasks: tdir, out, filter, phase, be } => {
            let tdir = if tdir.is_absolute() { tdir } else { root.join(tdir) };
            let out = if out.is_absolute() { out } else { root.join(out) };
            let mut ts = tasks::load_dir(&tdir).with_context(|| tdir.display().to_string())?;
            if let Some(f) = &filter { ts.retain(|t| t.id.contains(f.as_str())); }
            if ts.is_empty() { anyhow::bail!("no tasks under {}", tdir.display()); }
            let params = be.params();
            let report = match phase.as_str() {
                "generate" => {
                    let mut b = open_with_refs(&be, &ts)?;
                    eval::generate(b.as_mut(), &ts, &params, &out, &root)?;
                    eprintln!("replies in {}", eval::replies_dir(&out).display());
                    return Ok(());
                }
                "validate" => eval::validate_replies(&ts, &out, &root, &be.backend, be.model.clone(), &params)?,
                _ => {
                    let mut b = open_with_refs(&be, &ts)?;
                    eval::run(b.as_mut(), &ts, &params, &out, &root, be.model.clone())?
                }
            };
            println!("{}", eval::markdown(&report));
            println!("report: {}", out.join("report.json").display());
            if report.passed < report.tasks { std::process::exit(1); }
        }
        Cmd::GenTasks { comps, out, per_comp, seed } => {
            let out = if out.is_absolute() { out } else { root.join(out) };
            let mut files = Vec::new();
            for c in comps {
                let p = if c.is_absolute() { c } else { root.join(c) };
                if p.is_dir() {
                    let mut e: Vec<_> = std::fs::read_dir(&p)?.filter_map(|e| e.ok()).map(|e| e.path())
                        .filter(|p| p.extension().map(|x| x == "lua").unwrap_or(false)).collect();
                    e.sort();
                    files.extend(e);
                } else { files.push(p); }
            }
            // every candidate is pushed through the harness with its own reference
            // edit; only self-certified tasks (compile, lint, frames) are kept
            let (mut n, mut dropped) = (0, 0);
            for (i, f) in files.iter().enumerate() {
                let src = std::fs::read_to_string(f)?;
                let rel = f.strip_prefix(&root).unwrap_or(f).display().to_string();
                let ts = mutate::from_comp(&rel, &src, per_comp * 2, seed + i as u64, true);
                let mut kept = 0;
                for mut t in ts {
                    if kept >= per_comp { break; }
                    match eval::validate_one(&t, &t.reference.clone(), &root) {
                        Ok((true, _, _)) => {
                            kept += 1;
                            t.id = format!("{}_m{:02}", Path::new(&rel).file_stem().and_then(|s| s.to_str()).unwrap_or("comp"), kept);
                            tasks::save(&t, &out.join(format!("{}.json", t.id)))?;
                            n += 1;
                            eprintln!("keep {:<28} {}", t.id, t.instruction);
                        }
                        Ok((false, stage, _)) => { dropped += 1; eprintln!("drop ({stage}) {}", t.instruction); }
                        Err(e) => { dropped += 1; eprintln!("drop (error {e}) {}", t.instruction); }
                    }
                }
            }
            println!("wrote {n} tasks to {} ({dropped} candidates dropped by the harness)", out.display());
        }
        Cmd::Bench { be, tokens } => {
            let mut b = be.open()?;
            let mut params = be.params();
            params.max_new_tokens = tokens;
            params.stop.clear();
            let prompt = cadence_model::prompt::chat_format(cadence_model::prompt::SYSTEM,
                &[("Write a Cadence comp with three rects that stagger in from the left.".to_string(), String::new())]);
            let g = b.generate(&prompt, &params)?;
            println!("{} prompt tok, {} gen tok in {:.2}s = {:.1} tok/s", g.prompt_tokens, g.gen_tokens, g.secs, g.gen_tokens as f64 / g.secs.max(1e-9));
        }
    }
    Ok(())
}

/// The mock backend replies with each task's reference edit.
fn open_with_refs(be: &BackendArgs, ts: &[tasks::Task]) -> Result<Box<dyn backend::Backend>> {
    if be.backend == "mock" {
        let mut m = backend::mock::MockBackend::from_env()?;
        for t in ts { m.set_reference(&t.id, &t.reference); }
        return Ok(Box::new(m));
    }
    be.open()
}

fn which(bin: &str) -> bool {
    std::process::Command::new("sh").arg("-c").arg(format!("command -v {bin}")).output().map(|o| o.status.success()).unwrap_or(false)
}
