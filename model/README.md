# cadence-model — the editor model, hosted from Rust

A promptable editing agent over Cadence comps plus the harness that decides
whether an edit is right. The model runs in-process through one of three
backends; the harness runs the real toolchain (`bin/cadence verify`, frame
hashes, node props at t), so "right" means *renders the way the instruction
said*, not *looks plausible*.

```
instruction + comp.lua + timeline facts ──► backend (candle | ort | mlx | mock)
        ▼                                            │
   SEARCH/REPLACE blocks (or full file)  ◄───────────┘
        ▼
   apply ─► bin/cadence verify (compile + lint + check)
         ─► luajit model/lua/props.lua   (node.prop at t)
         ─► bin/cadence hash             (frames must change / hold / match the reference)
        ▼
   model/out/<run>/report.{json,md}
```

## Layout

| path | what |
|---|---|
| `src/backend/` | `candle.rs` (Qwen2 safetensors: CPU, Metal, CUDA), `ort.rs` (optimum ONNX export with KV cache, CPU or CUDA EP), `mlx.rs` (Qwen2 forward pass on mlx-rs, Apple silicon), `mock.rs` (reference / echo / script) |
| `src/prompt.rs` | system rules + output contract + ChatML formatting |
| `src/apply.rs` | block parser and whitespace-tolerant applier |
| `src/validate.rs` | verify / props / frame hashes, shelled out to the toolchain |
| `src/tasks.rs` | task schema (`model/tasks/**.json`) |
| `src/mutate.rs` | render-and-mutate synthetic tasks (duration, ease, prop delta, remove tween) |
| `src/eval.rs` | generate → validate phases, report |
| `src/agent.rs` | the retry loop: findings feed the next attempt |
| `lua/props.lua` | host-free node-state probe (luajit) |
| `tasks/hand/` | authored tasks; `tasks/generated/` self-certified synthetic tasks |
| `scripts/mac-smoke.sh` | mock proof + Metal run; `scripts/cuda-box.sh` sync/build/bench/generate on the 4070 |

## Commands

```bash
cargo run -p cadence-model -- doctor
cargo run -p cadence-model -- eval --backend mock --tasks model/tasks/hand          # harness proof: must be 4/4
CADENCE_MOCK=echo cargo run -p cadence-model -- eval --backend mock --tasks model/tasks/hand   # baseline: must fail
cargo run -p cadence-model -- gen-tasks --comps evals/cases --per-comp 2            # synthetic tasks, harness-certified
cargo build --release -p cadence-model --features metal
./target/release/cadence-model bench --backend candle --device metal --model ~/.cache/cadence/models/Qwen2.5-Coder-0.5B-Instruct
./target/release/cadence-model edit comps/x.lua "make the title fade slower" --backend candle --device metal --model DIR -o comps/x.lua
./target/release/cadence-model eval --backend candle --device metal --model DIR --tasks model/tasks
```

Backends are cargo features: `metal` / `cuda` (candle), `ort` / `ort-cuda`, `mlx`.
`CADENCE_DTYPE=f32|f16|bf16` overrides the weight dtype (defaults: f32 CPU,
f16 Metal, bf16 CUDA — bf16 on Metal produces garbage with candle 0.11).

## Validation contract (what a task can assert)

- `compile` — `bin/cadence verify` tier 0 passes.
- `no_new_lint_errors` — error count ≤ the unedited comp's.
- `props[]` — `{node, t, prop, op, value}` evaluated host-free at t.
- `frames_change[] / frames_hold[]` — frame md5 at t must differ from / equal the original.
- `match_reference_frames` — fraction of frames identical to the reference edit's render (1.0 = exact).

Synthetic tasks use the last one: Cadence renders deterministically, so a
mutation is its own ground truth. `gen-tasks` pushes every candidate through
the harness with its reference edit and drops the ones that fail (a duration
overrun, a broken parallel block), so a generated task is always solvable.

## Two boxes, two phases

cuda-box (RTX 4070, WSL2) has no LÖVE, so it only generates; the Mac validates.

```bash
model/scripts/cuda-box.sh sync                       # ship the working tree to ~/cadence in WSL
model/scripts/cuda-box.sh build && model/scripts/cuda-box.sh log
model/scripts/cuda-box.sh model Qwen/Qwen2.5-Coder-7B-Instruct
model/scripts/cuda-box.sh bench Qwen2.5-Coder-7B-Instruct
model/scripts/cuda-box.sh generate Qwen2.5-Coder-7B-Instruct model/tasks run-7b
model/scripts/cuda-box.sh fetch run-7b && model/scripts/cuda-box.sh validate run-7b
```

`eval --phase generate|validate` is the same split without the scripts.

## Results log

| date | backend | model | tasks | pass | note |
|---|---|---|---|---|---|
| 2026-09-16 | mock:reference | — | hand 4 | 4/4 | harness proof |
| 2026-09-16 | mock:echo | — | hand 4 | 0/4 | baseline fails at props/frames as designed |
| 2026-09-16 | mock:reference | — | generated 7 | 7/7 | self-certified synthetic set |
| 2026-09-16 | candle Metal f16 | Qwen2.5-Coder-0.5B-Instruct | hand 4 | 0/4 | 30 tok/s; ignores the block contract and rewrites the file unchanged → fails at props/frames (the harness names the stage) |
