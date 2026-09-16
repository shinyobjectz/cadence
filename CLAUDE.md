# Cadence — project instructions for agents

Programmatic video from Lua. A composition is a pure function of time; the
renderer seeks any frame in any order. Canon decisions live in `DESIGN.md`;
change them only with a written reason there.

## Build & test

```bash
bin/build-native                  # cargo build --release + stage dylibs into native/release/
bin/cadence doctor                # love, ffmpeg, ffprobe, wasmoon, project layout
bin/cadence render comps/x.lua -o out.mp4
CADENCE_SCENE=1 bin/cadence render …   # vello_cpu rasterizer (docs/SCENE.md)
bin/cadence lint|check|verify comps/x.lua --json
bin/golden capture|compare [case…]     # per-frame md5 for evals/cases (evals/golden/)
bin/eval --open                        # render the public eval suite to evals/out/eval.html
CADENCE_PROFILE=1 …                    # per-frame draw/read/out + scene timings
```

## Architecture

- `lib/cadence/` — host-free authoring API: scene graph, recorder (overlap
  checking), signals, `waitUntil`, lint. Never touches love or Rust.
- `runtime/` — LÖVE offline host. `painter.lua` walks the evaluated tree;
  `scene.lua` streams scene-owned nodes to the Rust rasterizer; `main.lua`
  owns the frame loop, ffmpeg pipe and the no-readback direct path.
- `scene/` — `cadence-scene` (vello_cpu + parley). One rasterizer for text,
  shapes, images, html, vector, masks, blends, shadows, blur. Opcodes at the top
  of `scene/src/lib.rs`. Coverage and gaps: `docs/SCENE.md`.
- `decode/ html/ layout/ vector/ effects/ scene3d/` — native helpers over a
  C ABI, loaded by LuaJIT FFI. `vendor/ellua-love` — pinned LÖVE fork.
- `evals/cases/` — public visual suite; `evals/golden/` — hashes per case.

## Conventions

- Comps are pure `f(t)`: no clocks, no I/O, seeded RNG. All fetching happens in
  the resolve phase (`runtime/resolve.lua`).
- Every renderer change: capture goldens before, compare after, eyeball
  `bin/eval --open`, then recapture deliberately.
- Determinism is scoped to platform + `CADENCE_SCENE_THREADS`; do not compare
  hashes across thread counts.
- Use non-interactive flags (`cp -f`, `rm -rf`, `ssh -o BatchMode=yes`).
