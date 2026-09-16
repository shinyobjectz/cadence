# ellua API reference (v0, love host)

Ground truth: `lib/ellua/init.lua` (comp/scene/recorder), `lib/ellua/timeline.lua`
(segments + evaluation), `lib/ellua/ease.lua`, `lib/ellua/color.lua`,
`runtime/painter.lua` (draw semantics), `runtime/resolve.lua` (asset phase).

## `e.comp{}`

| field | type | required | default | notes |
|---|---|---|---|---|
| `width`, `height` | number > 0 | yes | — | pixels |
| `duration` | number > 0 | yes | — | seconds; static, immutable at render |
| `fps` | number | no | 30 | render parameter; `--fps` on the CLI overrides |
| `background` | color | no | `"#000000"` | cleared each frame (alpha ignored) |
| `scene` | function(s) | yes | — | builds nodes + scripts |
| `inputs` | table | no | — | optional named bindings; see below |

The comp file must `return e.comp{...}`. Compile order: `s.input` is bound
from defaults + host map → `scene(s)` runs → structural defaults are filled
in → each `s:script` fn runs once against a recorder → timeline validated
(overlap + duration checks happen during recording).

### Optional `inputs`

Declare media the host (Studio canvas, CLI) binds without rewriting the Lua:

```lua
e.comp {
  inputs = {
    bg   = { kind = "video" },
    logo = { kind = "image", default = "assets/logo.png" },
  },
  scene = function(s)
    s:video { src = s.input.bg }
    s:image { src = s.input.logo }
  end,
}
```

`s.input.<name>` is a string path. `s:video` / `s:image` still assert
`type(src) == "string"` — bind before `scene()`. Hardcoded `src = "…"` remains
valid; comps with no `inputs` table are unchanged.

A required input (no `default`, not in the host map) errors at compile:

```
ellua: input "bg" is not bound
```

JSON shape (sidecar or `--inputs`): `{ "bg": "relative/or/absolute/path" }`.
Relative paths follow the same `ELLUA_CWD` localization as other `src` values.

Merge order (later wins, key-wise): `default` in `e.comp` < sibling
`<comp>.inputs.json` < `--inputs FILE.json` < `--input KEY=PATH` (repeatable).
The love host reads JSON and injects a string table as `Comp:compile(hooks, inputs_map)`.
`lib/ellua` never opens files.

## Node model

`s:<kind>{...}` returns a node handle. A node holds `initial` (constructor
props + defaults) and `state` (timeline overlay written by `evaluate(t)`).
Value at t = state if a segment governs it, else initial.

Defaults for every node: `opacity = 1`, `rotation = 0`, `scale = 1`,
`anchor = "topleft"`. Adjustment defaults are identity: `effect_blur = 0`,
`effect_brightness/contrast/saturate/opacity = 1`, and remaining effect
amounts = 0. `id` defaults to `kind .. index` (e.g. `"rect2"`) — set it for
readable compile errors.

Draw transform per node (painter): `translate(x, y)` → `rotate(rotation)` →
`scale(scale, scale)` → anchor offset applied to the drawn quad. So (x, y) is
always the pivot; `anchor = "center"` shifts the artwork so (x, y) is its
center (i.e. rotation/scale pivot the visual center). `anchor = "topleft"`
puts (x, y) at the top-left and pivots there. Circles ignore anchor (always
centered on x, y).

Nodes draw in declaration order. `opacity <= 0` skips the node entirely.

### Structural defaults filled at compile

For `video`, `image`, `svg`, `lottie`: `x, y` default 0; `w, h` default to the
full comp size. For `video` and `lottie`: `from` defaults 0, `duration`
defaults `comp.duration - from`, `media_start` defaults 0. For `lottie`:
`loop` defaults true.

### Node specifics

- **rect** — `w, h` required in practice (no default); `rx` corner radius
  (also animatable).
- **surface** — named rectangular compositing layer/backplate. Requires `w,h`;
  otherwise renders as a rect. Use it as the `parent` for a grouped UI
  assembly, with `shadow` and `blend`. It is currently a transform parent plus
  painted backplate, not an offscreen subtree renderer; effects and masks apply
  to individual child nodes.
- **circle** — radius `r`; animatable.
- **text** — `size` is font px (default 32 if unset at draw). `font` = path to
  a TTF/OTF (default NotoSans). `wrap` = column width px, `leading` = line
  height multiple, `reveal` 0..1 types on, `{c:#hex}{b}{i}` tags colour/style
  runs, `outline`/`weight`/`outline_color` for stroked type. `tracking` =
  letter-spacing px, animatable (cadence-scene renderer, the default; the
  `CADENCE_SCENE=0` love path ignores it). Center anchor measures the
  *current* string with the *current* size and tracking.
- **video** — `src` local path or `http(s)://` URL (URL is curl-downloaded to
  `~/.cache/ellua/dl/` at resolve, content-addressed). Active window is
  `[from, from + duration)` in comp time; frame shown at comp-time t is source
  time `media_start + (t - from)`. Cover-cropped and scaled to w×h (centered
  crop). Fast path: in-process FFI decoder (frame-exact by PTS, YUV planes +
  GPU shader). Fallback path (decoder lib absent): resolve pre-extracts JPEG
  frames at comp fps via ffmpeg into `~/.cache/ellua/frames/`.
- **image** — any format LÖVE reads (png/jpg); drawn scaled to w×h
  (**stretch**, not cover). URL src works (resolve localizes).
- **svg** — rasterized once at resolve by the `resvg` CLI to exactly w×h,
  cached by (src, w, h) hash. Requires `resvg` on PATH.
- **lottie** — rendered per-frame by ThorVG at the requested time:
  `render((media_start + (t - from)) * speed, loop)`. `speed` default 1;
  `loop` wraps past the animation's end, otherwise it holds the last frame.
  Drawn premultiplied-alpha. (Note: `media_start` is also multiplied by
  `speed` in v0 — if you use both, account for it.)
- **draw** — `s:draw(fn)`; painter calls `fn(t, g)` inside the node transform
  with `g = love.graphics` and color preset to white × opacity. The fn MUST be
  a pure function of `t`: no upvalue mutation across calls, no io/os, no
  `love.timer`/randomness, no creating GPU resources per call (cache via
  closure upvalue created once is tolerated but must not affect output
  ordering). The draw node has no x/y initial by default, so pass none and
  position within the fn, or wrap content in animated parent props you set at
  construction. Draw-node props cannot be tweened unless given initial values.
- **group** — transform parent; children use `parent = group`. Painted nodes
  (including `surface`) may set `blend` to `alpha`, `add`, `subtract`,
  `multiply`, `lighten`, `darken`, `screen`, or `replace`. Use a node's
  `clip_node` (and optional `clip_invert`) as a live rounded-rect mask.
  `shadow = {blur=, dy=, alpha=}` draws a cached soft shadow beneath any w×h
  node.
- **html** — fragment rung. Inline HTML/CSS (or `src=` file inlined at resolve)
  painted by Blitz to `w×h`. No network. Tween `progress` to rewrite
  `{{progress}}` / `{{progress_int}}` and re-raster. Requires `bin/build-native`.
- **page** — document rung. `src=` local HTML or `http(s)` URL, or inline
  `html=` plus optional `base=`. Linked CSS/images/fonts fetch at resolve;
  woff2 `@font-face` decodes to sfnt for Blitz. Classic `<script>` bakes once
  (`bake=true` default) against a tiny document, then the dumped HTML is
  painted. `bake=false` skips scripts. Never JS in `evaluate(t)`. Not Chrome:
  no layout/style APIs, no modules, no React. `w,h` default to the canvas.
  Requires `bin/build-native`.
- **html / vector effects** — raster-backed HTML and vector nodes accept
  `effects = { blur=, brightness=, contrast=, saturate=, grayscale=, sepia=,
  invert=, opacity=, hue_rotate= }`. These run in the native `ellua-effects`
  cdylib after rasterization; build native helpers first with
  `bin/build-native`.
- **camera** — shared plane camera. Perspective nodes set `camera = cam` and
  keep their own `dolly`. Tween `yaw`/`pitch`/`fov` on the camera. Look-at:
  `look_x/y/z` + `cam_x/y/z` replaces euler. Far planes (smaller dolly) draw
  first. `perspective = true` now also works on `rect`/`surface`.
- **world / mesh / light** — real 3D as an offscreen layer (Lottie contract).
  `s:world{ w, h, fov, cam_*, look_* }` rasterizes children each frame via
  `ellua_scene3d`. `s:mesh{ parent=world, src=*.gltf|primitive="cube"|"sphere" }`
  and `s:light{ parent=world, dir={dx,dy,dz} }`. Pose from `t`; no engine loop.
  Requires `bin/build-native`. Web Canvas2D skips these nodes.

## Recorder (`t` inside `s:script`)

The script runs ONCE at compile; `t` records segments at an absolute cursor
(seconds from comp start). All `s:script`s in a comp share ONE recorder run in
declaration order — a second script's cursor continues where the first ended,
and overlap/duration checks apply across all of them together.

- `t:tween(node, dur, props, ease)` — `dur > 0`. For each prop: from-value =
  last simulated value for that node.prop (what a previous tween/set left it
  at), else `node.initial[prop]`; missing initial = compile error. Records
  `[cursor, cursor + dur]`, then cursor += dur. One tween with several props
  records one segment per prop over the same window.
- `t:set(node, props)` — zero-duration segment (step); cursor unchanged.
- `t:wait(d)` — `d >= 0`; cursor += d.
- `t:parallel(f1, f2, ...)` — each branch starts at the same cursor; final
  cursor = max branch end. Branches may nest `parallel`/`wait`.

Validation at record time:
- prop not in the animatable allowlist → error `"<prop>" is not animatable`.
- overlap on (node.id, prop): windows `[t0,t1)` intersecting → error
  `overlapping tweens on <id>.<prop>`. Zero-duration sets at a boundary don't
  overlap. Note the key is **node.id** — two nodes sharing an explicit `id`
  would falsely collide; keep ids unique.
- after all scripts: cursor > duration (+1e-9) → error.

## Evaluation semantics

Per (node, prop) segment group, at time t: before the first segment → initial
value; inside a segment → eased interpolation; between/after segments → the
previous segment's `to` (hold). Numbers lerp linearly post-ease; colors lerp
per-channel RGBA.

## Animatable allowlist

`x y w h r rx rotation scale opacity color size progress effect_blur
effect_brightness effect_contrast effect_saturate effect_grayscale effect_sepia
effect_invert effect_opacity effect_hue_rotate`. Structural props (`src from
duration media_start text anchor loop speed fn id`) are fixed at construction.
The `effects={...}` table is shorthand only; animate its flattened
`effect_*` properties with `t:tween`.

## Colors

`"#RRGGBB"`, `"#RRGGBBAA"`, or table `{r, g, b, a}` / `{r=..., g=..., b=...,
a=...}` with floats 0–1. Internal form is `{r,g,b,a}` floats. Bad strings
error at parse.

## Eases (`lib/ellua/ease.lua`)

`linear` — constant rate.
`quadIn/quadOut/quadInOut`, `cubicIn/cubicOut/cubicInOut`,
`sineIn/sineOut/sineInOut`, `expoIn/expoOut/expoInOut` — standard families,
gentler → sharper: sine < quad < cubic < expo.
`backIn/backOut/backInOut` — overshoot (c1 = 1.70158). `backOut` is the go-to
"pop in" entrance.
`elasticOut` — springy settle, for playful emphasis.
No bounce family, no `elasticIn/elasticInOut` (planned, INTEGRATIONS.md A3).
Unknown name errors at compile.

## Determinism sandbox (runtime/main.lua)

Before the comp file loads: `math.randomseed(0)`, `love.math.setRandomSeed(0)`;
`os.time/os.clock/os.date/io.open/io.popen/io.read` replaced with erroring
stubs. `math.random` technically works (seeded) but comp code should treat any
frame-order-dependent state as a bug — a seeded generator consumed during
*draw* still breaks seek-order invariance. Generate random-ish values only at
scene-build/compile time.

## Resolve phase (what happens before render)

Runs after comp compile, before first frame; network allowed here only:
- video: FFI decoder opened (or ffmpeg frame pre-extraction fallback).
- URLs (any node src): curl download → `~/.cache/ellua/dl/` (content-addressed).
- svg: `resvg` raster → `~/.cache/ellua/svg/`.
- image/lottie: existence check + localize.
Cache root: `~/.cache/ellua`. Delete it to force re-resolve.
