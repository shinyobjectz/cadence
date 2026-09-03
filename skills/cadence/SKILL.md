---
name: cadence
description: Author, edit, preview, and render programmatic videos with Cadence — Lua + LÖVE2D, browser-free alternative to Remotion/HyperFrames. A composition is a pure-Lua file; `cadence render comps/foo.lua -o out.mp4` produces the video. Load when creating, editing, animating, debugging, or rendering motion graphics, captions, title cards, or vertical shorts with Cadence/ellua Lua comps. Covers the comp API, CLI (render, verify, lint, check), determinism rules, and the mandatory verification loop.
---

# Cadence — agent authoring guide

Cadence renders video from Lua. No browser, no React, no project scaffolding: one
`.lua` file returns a composition; a ~10MB LÖVE engine renders it; ffmpeg encodes.

> **Note:** Cadence is the project name. The Lua module is still `require("cadence")`
> (alias `require("ellua")`). CLI: `bin/cadence`.

Reference files — read the one that matches your task before improvising:
- `references/api.md` — read before writing any non-trivial comp: every node prop
  with defaults, exact timeline/recorder semantics, evaluation rules, resolve phase.
- `references/love2d.md` — read before using `s:draw` or touching `runtime/`:
  what LÖVE is, why comps never call `love.*`, the escape-hatch purity contract.
- `references/elevenlabs.md` — read whenever the task involves narration, voiceover,
  captions, sound effects, or music: what works today (generate via the vendored
  ElevenLabs skills, mux with ffmpeg) vs. what is future resolve-phase work.
- `references/capture.md` — read before any brand/company video: `bin/ellua-capture`
  pulls real fonts (TTF), colors, component HTML/CSS, and logos from a URL, and
  maps each output to the node prop that consumes it. Never approximate a brand's
  type or palette when capture can fetch the real thing.
- `references/demo.md` — read for any product/screen demo: live-page recording
  (CDP virtual time, real hover/modal states), authenticated sessions (login
  procedure), ScreenStudio framing, camera/cursor grammar, aspect intentionality.
- `references/motion.md` — **read before animating anything beyond a basic fade**:
  the full motion vocabulary (springs, stagger, wiggle, paths, kinetic per-char
  text, closed-form vector effects) plus named recipes and the style-cohesion
  rules that make a piece read as designed rather than defaulted.
- `references/effects.md` — read before using surfaces, masks, blend modes,
  shadows, or native raster adjustment effects.

## Mental model (the invariants — violating these is a compile error or a bug)

1. **Composition = pure function of time.** Rendering *seeks*: any frame, any
   order. Nothing may depend on previous frames or wall clock.
2. **Seconds, not frames.** All times are seconds. fps is a render parameter
   (`--fps`), never an authoring concern.
3. **Duration is static.** Declared in the comp header. A script that animates
   past `duration` is a **compile error**.
4. **Determinism by construction.** The renderer sandboxes the VM:
   `os.time/os.clock/os.date` and `io.open/io.popen/io.read` are **banned in
   comps** (calling them errors), RNG is seeded to 0. All I/O (downloads,
   ffprobe, SVG raster, decode setup) happens in the *resolve* phase before
   render.
5. **Audio never touches the engine.** The renderer is video-only. Audio is
   mixed by ffmpeg `filter_complex` at encode time (future built-in phase; today
   you mux manually — see `references/elevenlabs.md`).

## Comp file anatomy

```lua
local e = require("ellua")

return e.comp {
  width = 1080, height = 1920,   -- pixels (required)
  duration = 6,                  -- seconds (required, static)
  fps = 30,                      -- default fps (default 30; --fps overrides)
  background = "#101018",        -- default "#000000"

  scene = function(s)
    -- 1) declare nodes (z-order = declaration order; later draws on top)
    local box   = s:rect  { x = 100, y = 100, w = 200, h = 120, rx = 12, color = "#4fa8f2" }
    local title = s:text  { x = 540, y = 400, text = "hello", size = 120,
                            color = "#ffffff", opacity = 0, anchor = "center" }
    -- 2) choreograph (executed ONCE at compile; records absolute-time segments)
    s:script(function(t)
      t:tween(box, 1.0, { x = 800 }, "cubicInOut")
      t:wait(0.3)
      t:parallel(
        function() t:tween(title, 0.8, { opacity = 1 }, "sineOut") end,
        function() t:tween(title, 0.8, { size = 140 }, "backOut") end)
    end)
  end,
}
```

## Scene nodes (exact prop names)

Common to all: `id` (optional, for error messages), `x`, `y`, `opacity` (0–1,
default 1; 0 skips drawing), `rotation` (**radians**, default 0), `scale`
(uniform, default 1), `anchor` (`"topleft"` default | `"center"`).

| node | call | notes |
|---|---|---|
| rect | `s:rect{ x, y, w, h, rx, color }` | `rx` = corner radius |
| circle | `s:circle{ x, y, r, color }` | always centered on (x, y); anchor irrelevant |
| text | `s:text{ x, y, text, size, color, anchor }` | single line, no wrapping — one node per line |
| video | `s:video{ src, from, duration, media_start, w, h, anchor }` | `src` = local path **or URL** (downloaded+cached at resolve). Cover-cropped to w×h. Decoded frame-exact via FFI sidecar. `from` = comp-time start (default 0), `duration` defaults to rest of comp, `media_start` = seek into source (default 0). w/h default to full comp. |
| image | `s:image{ src, w, h }` | **stretched** to w×h (not cover-cropped) — match the aspect ratio |
| svg | `s:svg{ src, w, h }` | **w/h required.** Rasterized by resvg at resolve to exactly w×h; `scale > 1` blurs — author at final size |
| lottie | `s:lottie{ src, w, h, from, duration, loop, speed }` | **w/h required.** ThorVG, frame-addressed (seek-safe). `loop` default true |
| draw | `s:draw(fn)` | escape hatch: `fn(t, g)` with `g = love.graphics`. Must stay a pure function of `t` — no state, no I/O, no clocks. Only place love.* is allowed in a comp. |

`src`/structural props (src, from, duration, media_start, text, anchor, loop,
speed) are **not** animatable.

## Timeline scripts

`s:script(function(t) ... end)` — reads top-to-bottom like a screenplay but
executes **once at compile time**, recording absolute-time segments. `t` is a
recorder, not the clock.

- `t:tween(node, dur, { props }, ease)` — animate over `dur` seconds from the
  node's current simulated value; advances the cursor by `dur`. `ease` optional
  (default `"linear"`).
- `t:set(node, { props })` — instant set at the cursor (zero-duration step).
- `t:wait(d)` — advance the cursor `d` seconds.
- `t:parallel(fn1, fn2, ...)` — run branches from the same start time; cursor
  ends at the **longest** branch. `t:wait` inside a branch offsets that branch.
- **Overlapping tweens on the same node.prop = compile error** (including
  across parallel branches and across separate scripts). Sequence or offset them.
- Cursor past `comp.duration` at the end of any script = compile error.
- Tweening a prop the node has no initial value for = compile error — declare
  the starting value in the node constructor.

**Animatable props (allowlist):** `x, y, w, h, r, rx, rotation, scale, opacity,
color, size`. Anything else in a tween = compile error.

**Colors:** `"#RRGGBB"` or `"#RRGGBBAA"` (or `{r,g,b,a}` floats 0–1). Color
tweens lerp RGBA — fade a scrim in with `"#00000000"` → `"#000000aa"`.

**Eases** (exact names; unknown name = compile error):
`linear`,
`quadIn quadOut quadInOut`,
`cubicIn cubicOut cubicInOut`,
`sineIn sineOut sineInOut`,
`expoIn expoOut expoInOut`,
`backIn backOut backInOut` (overshoot),
`elasticOut` (spring settle).
There is no bounce family and no `elasticIn/InOut` yet.

## CLI

Run from the project directory containing `comps/*.lua` (paths resolve via
`CADENCE_CWD` / `ELLUA_CWD`).

```bash
bin/cadence render  comps/foo.lua -o out.mp4 [--fps N] [--shuffle]
bin/cadence preview comps/foo.lua
bin/cadence hash    comps/foo.lua [--fps N] [--shuffle]
bin/cadence lint    comps/foo.lua --json [--strict]
bin/cadence check   comps/foo.lua --json [--strict]
```

### Agent verification pipeline (run before declaring success)

Agents should use the unified JSON envelope (`cadence.result/v1`) — every
tier returns `{ schema, status, exit_code, findings[], steps[] }`.

```bash
# 0. Environment probes (love, ffmpeg, wasmoon, project layout)
bin/cadence doctor --json

# 1. Full verify: tier-0 wasm compile → lint → check (cheap-first)
bin/cadence verify comps/foo.lua --json
bin/cadence verify comps/foo.lua --json --skip-check   # compile + lint only
bin/cadence verify comps/foo.lua --json --wasm-only    # instant syntax/compile

# 2. Agent-readable markdown summary (runs verify if needed)
bin/cadence feedback comps/foo.lua
bin/cadence feedback --from /tmp/result.json --json

# 3. Individual tiers
bin/cadence lint  comps/foo.lua --json
bin/cadence check comps/foo.lua --json
```

Exit codes: `0` = no errors, `1` = findings/errors, `2` = infrastructure
(doctor probe failed, love/node missing).

Desktop API (Tauri): `verify_comp`, `start_verify`, `doctor_cadence` return the
same JSON string. Preview loads run wasm compile + lint automatically.

Tier-3 (when probe sidecar exists): `bin/cadence query sidecar.npz "logo visible"`.

- `ELLUA_QUALITY=draft|standard|high` — draft = VideoToolbox hw encode
  (fastest), standard (default) = x264 veryfast crf18, high = x264 slow crf17.
- `LOVE_BIN=/path/to/love` overrides the engine binary (vendored LÖVE 12
  preferred automatically). `ELLUA_PROFILE=1` prints per-frame timing.
- `--shuffle` renders frames out of order — output must be identical (seek-safety check).
- Render/hash run windowless-ish (`SDL_MAC_BACKGROUND_APP=1`); errors print to
  stderr and exit 1 (no hanging error window). With `--json`, compile errors
  also emit structured `cadence.result/v1` on stdout.

## Render verification loop (mandatory before declaring success)

After `cadence verify` passes, still validate the actual mp4:

```bash
# 1. render
bin/cadence render comps/foo.lua -o out.mp4

# 2. probe: frame count MUST equal duration × fps exactly; check w/h/rate
ffprobe -v error -select_streams v:0 -count_frames \
  -show_entries stream=width,height,r_frame_rate,nb_read_frames \
  -of default=noprint_wrappers=1 out.mp4

# 3. extract sample frames and LOOK at them (Read the PNGs — check layout,
#    legibility, that animations actually happened, nothing off-frame/blank)
mkdir -p /tmp/cadence-verify
ffmpeg -y -v error -i out.mp4 -vf fps=2 /tmp/cadence-verify/f%03d.png
```

Extract more frames (`fps=4` or exact `-ss` timestamps) around any moment you
changed. For determinism checks: `bin/cadence hash comps/foo.lua` twice and `diff`;
add `--shuffle` (then `sort` both outputs) to verify seek-safety.

## Common pitfalls

1. **Rotation/scale pivot**: anchor defaults to `"topleft"`, so a rotating or
   scaling node pivots around its top-left corner. Anything that rotates,
   scales, or "floats" almost always wants `anchor = "center"`.
2. **Same-prop overlap in parallel branches** — two branches touching the same
   `node.prop` in overlapping windows is a compile error. Offset with
   `t:wait()` inside a branch, or merge into one tween.
3. **`media_start` beyond source duration** — resolve/decode fails or yields
   nothing. `ffprobe -v error -show_entries format=duration -of csv=p=0 src.mp4`
   first; ensure `media_start + duration ≤ source length`.
4. **w/h required on `s:svg` and `s:lottie`** — asserted at scene build.
5. **Rotation is radians** (`math.pi/4`, not 45). Subtle drifts use small
   values (0.03–0.15).
6. **Duration overrun** — total scripted time (tweens + waits) must fit inside
   `comp.duration`, or compile fails. Budget your waits.
7. **No clocks, no I/O in comps** — `os.time`/`io.open` etc. error by design.
   Don't reseed RNG; don't read files; all assets go through node `src`.
8. **z-order is declaration order** — declare background video first, scrims
   next, text last.
9. **Fade-ins need starting values** — declare `opacity = 0` (or off-screen
   x/y) in the constructor; tweens start from declared/last-simulated values.
10. **`s:image` stretches** (video cover-crops, image does not) — give image
    w/h matching its aspect, or use it full-frame at comp aspect.
