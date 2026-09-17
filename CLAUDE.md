# Cadence — project instructions for agents

Programmatic video from Lua. A composition is a pure function of time; the
renderer seeks any frame in any order. Canon decisions live in `DESIGN.md`;
change them only with a written reason there.

## Build & test

```bash
bin/build-native                  # cargo build --release + stage dylibs into native/release/
bin/cadence doctor                # love, ffmpeg, ffprobe, wasmoon, project layout
bin/cadence render comps/x.lua -o out.mp4   # cadence-scene rasterizer (default, docs/SCENE.md)
CADENCE_SCENE=0 bin/cadence render …        # love canvas path (fallback)
bin/cadence lint|check|verify comps/x.lua --json
bin/golden capture|compare [case…]     # per-frame md5 (evals/golden/<case>.scene.md5; =0 → <case>.md5)
bin/eval --open                        # render the public eval suite to evals/out/eval.html
bin/vision-setup                       # one-time: vision/.venv (uv, py3.12, torch, mcp, Depth Anything 3)
bin/cadence-vision call contact_sheet source=evals/cases/camera.lua n=6   # any vision tool from the shell
bin/cadence-vision test                # vision smoke tests (depth/geometry run when DA3 weights are cached)
CADENCE_PROFILE=1 …                    # per-frame draw/read/out + scene timings
```

## Architecture

- `lib/cadence/` — host-free authoring API: scene graph, recorder (overlap
  checking), signals, `waitUntil`, lint. Never touches love or Rust.
- `runtime/` — LÖVE offline host. `painter.lua` walks the evaluated tree;
  `scene.lua` streams scene-owned nodes to the Rust rasterizer; `main.lua`
  owns the frame loop, ffmpeg pipe and the no-readback direct path.
- `scene/` — `cadence-scene` (vello_cpu + parley), the default renderer. One
  rasterizer for text, shapes, images, html, vector, masks, blends, shadows,
  effects, the fx chain and video; world, perspective and GLSL escape hatches
  land in image slots. Opcodes at the top of `scene/src/lib.rs`. Coverage:
  `docs/SCENE.md`.
- `vision/` — `cadence-vision`, the visualization MCP (`.mcp.json` registers
  it): renders comps, clips and images into what a vision/video model reads
  best. Profiles per model, keyframes, contact sheets, Set-of-Mark annotation
  from the comp's own nodes, scene text, diffs, Depth Anything 3 depth and
  multi-view geometry with rendered views. Design and measurements:
  `docs/VISION.md`.
- `model/` — `cadence-model`, the earlier editor-model harness. Direction
  scrapped 2026-09-16 (a timeline-only model cannot read pixels); kept for
  its validation code, not developed further.
- `decode/ html/ layout/ vector/ effects/ scene3d/` — native helpers over a
  C ABI, loaded by LuaJIT FFI. `vendor/ellua-love` — pinned LÖVE fork.
- `evals/cases/` — public visual suite; `evals/golden/` — hashes per case.

## Conventions

- Comps are pure `f(t)`: no clocks, no I/O, seeded RNG. All fetching happens in
  the resolve phase (`runtime/resolve.lua`).
- Every renderer change: capture goldens before, compare after, eyeball
  `bin/eval --open`, then recapture deliberately.
- Determinism is scoped to platform + `CADENCE_SCENE_THREADS`; do not compare
  hashes across thread counts. Love-path (`CADENCE_SCENE=0`) hashes also depend
  on the LÖVE build: the checked-in `<case>.md5` need the vendored LÖVE 12 fork.
- Use non-interactive flags (`cp -f`, `rm -rf`, `ssh -o BatchMode=yes`).
