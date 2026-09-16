# cadence-scene — one rasterizer under the timeline

Status 2026-09-16: opt-in (`CADENCE_SCENE=1`). Text, shapes, images, html
textures, vector layers, clip masks, blend modes, shadows and blur all paint
through vello_cpu. Everything else falls back to love per node.

## Why

The authoring layer (`lib/cadence`: scene graph, recorder, overlap checking,
signals, `waitUntil`) is the asset. What it painted into was five engines:
love.graphics for shapes and text, Blitz for html, vello_cpu for vector nodes,
Taffy for flex, wgpu for 3D. Five antialiasers, three font stacks, no shared
compositing model. That is where "assembled from parts" comes from, and it is
why rich visual properties (letter-spacing, gradient stops, mask paths, blur)
were not tweenable: the timeline could express them but the pixels could not.

Measured before deciding (M-series Mac, 1080×1920):

| path | scene | per frame |
|---|---|---|
| love draw + GPU readback (render mode) | 24 text lines + 40 shapes | 0.3 + 2.6 ms |
| cadence-scene direct, 1 thread | same | 2.6 ms, no readback |
| cadence-scene direct, 4 threads | same | 1.8 ms |
| vello_cpu 0.2, 1 thread | 2500 glyph paths + 40 gradient cards + 3 radial washes + 600 strokes | 16.4 ms |
| vello_cpu 0.2, 8 threads | same | 6.2 ms |

vello_cpu is not the bottleneck; the readback and the ffmpeg pipe were.

## Shape

```
lib/cadence  (pure Lua)  ──evaluate(t)──▶  runtime/painter.lua
                                              │  scene_owns(node)?
                                              ├─ yes → runtime/scene.lua builder (f32 ops + string table)
                                              └─ no  → love.graphics (flush scene layer first, z-order kept)
                                              ▼
                                   scene/src/lib.rs  cs_render()  → premultiplied RGBA
```

- `runtime/scene.lua` — FFI bridge + command builder. Same verbs as the vector
  builder (`rect circle move line curve fill stroke gradient radial grain`) plus
  `text image clip_push blend_push filter_push opacity_push pop transform clear`.
- `scene/src/lib.rs` — interpreter. Opcodes documented at the top of the file.
  Fonts: `cs_font_load(path)`; id 0 is the bundled NotoSans (love's default).
  Text layout is parley (shaping, line breaking, letter-spacing, weight/style
  synthesis, `{c:#hex}{b}{i}` runs), glyphs via vello_cpu `glyph_run`, outline
  text as a real vector stroke. Layouts are cached per (font,size,text,runs,…).
- Direct path: `painter.scene_direct_ok(comp)` is true when every drawn node is
  scene-owned; `runtime/main.lua` then renders straight into an ImageData and
  writes it to ffmpeg / md5. `CADENCE_SCENE_DIRECT=0` disables.

## Coverage

| node kind | scene | notes |
|---|---|---|
| text, kinetic chars | ✅ | wrap, leading (metrics-relative like love), tags, reveal, outline, weight, **`tracking` (letter-spacing, tweenable — scene only)** |
| rect, circle, flex(color) | ✅ | rx, anchor, group transforms |
| image, svg, page | ✅ | registered once per file; rx |
| html | ✅ | Blitz texture, re-uploaded when `progress` changes |
| vector | ✅ | node box clip, grain scoped to the box, opacity via layer |
| clip_node mask | ✅ | `clip_invert` falls back |
| blend | ✅ | alpha multiply screen darken lighten add; subtract/replace fall back |
| shadow | ✅ | blur layer over a rounded rect |
| effect_blur / effect_opacity | ✅ | |
| brightness contrast saturate grayscale sepia invert hue | ⏳ love | vello_cpu 0.2 ships only blur/drop-shadow/flood/offset filters; add a colour-matrix pass in `scene/` |
| video | ⏳ love | `cs_image_update` exists; wire the decode frame into a slot |
| fx (shader chain), perspective, world/mesh/camera/light, lottie, spritesheet, spine, chart, ornament, particles, draw | ⏳ love | fx is Moonshine/Shadertoy GLSL — port means CPU reimplementation; `s:draw` stays love by contract |

## Determinism

Same thread count ⇒ byte-identical frames (checked, `bin/golden`). Different
thread counts differ by ±1 in ~0.03% of bytes, so goldens are scoped by
`CADENCE_SCENE_THREADS` (default 0 = single). Any filter layer in a frame pins
that frame to single-threaded rendering (vello_cpu constraint).

## Tools

- `bin/golden capture|compare [case…]` — per-frame md5 for every eval case,
  tagged with platform/threads/scene. Goldens in `evals/golden/` were captured
  from the love path; compare in scene mode to see what a port changed, then
  eyeball `bin/eval --open` before recapturing.
- `CADENCE_PROFILE=1` prints `PROF scene=… flushes=…` and `PROF direct=1`.

## Plan for the remaining gaps (2026-09-16)

Ordered by leverage per hour. Each item names its proof so it can be closed
without opinion.

### 1. Lint blind spots (hours) — first, because they lie about every reel
- **reveal is motion.** Add `reveal` to the amplitude calculation in
  `lib/cadence/lint.lua`: Δreveal × visible glyph count, normalised like x/y.
- **outlined text has contrast.** `contrast_static` uses `outline_color` when
  `outline > 0`; fill colour alone is not what the eye sees.
- **vector draw callbacks are measurable.** Lint already samples the timeline
  per frame; give it a recording builder (pure Lua, same verbs as
  `runtime/scene.lua`) and call `draw(v, t)` at each sample. The diff between
  consecutive command streams is real motion amplitude. No FFI, host-free.
  Proof: both `examples/vv` reels lint with zero false frozen-span findings and
  a deliberately static vector node still trips one.

### 2. 3D world and shader-fx output into scene slots (hours)
`world` (wgpu) and `fx` (Moonshine GLSL) already produce an RGBA buffer per
frame. Feed it to `scene.image_slot` like html does, so one frame composites in
one rasterizer with correct z-order and no per-node flush. Proof: `world3d` and
`fx` evals render on the direct path (`PROF direct=1`) and match their goldens
within antialiasing.

### 3. Colour effects (half day)
vello_cpu 0.2 has blur/drop-shadow/flood/offset only. Add opcode `111
colour_push kind amount … 105 pop`: render the enclosed commands into a scratch
`Pixmap`, run the per-pixel maths that `effects/` already has (make it an rlib
dependency of `scene/`), composite back as an image paint. Covers brightness,
contrast, saturate, grayscale, sepia, invert, hue-rotate, and gives tonemap,
posterize, pixelate a home. Proof: `effects` eval hash-stable in scene mode,
side-by-side with love within ±2/255 per channel.

### 4. Video frames into slots (half day)
`painter.lua` video branch: the jpg-frames and rgba paths call
`scene.image_slot(node, imagedata, changed)` per frame; the yuv path goes
through `ed_frame_rgba` (the one pinned YUV→RGB path, DESIGN §4). Add an
opaque fast path to `cs_image_update` (skip premultiply when alpha is 255).
Proof: `video` and `video_layers` evals on the direct path at ≥ the love fps.
Blocker: Wikimedia assets 429; fetch with backoff or mirror to R2.

### 5. fx chain as layers (1–2 days)
Once 3 lands, most of the chain is expressible without GLSL: bloom/glow =
blur layer + `Plus` blend, vignette = radial gradient multiply, chroma =
three offset copies with channel masks, grain exists. Worley and shadertoy stay
GLSL and are declared an escape hatch like `s:draw` (lint marks them opaque).
Proof: per-effect A/B against love on the `fx` eval; agents get the same
`s:fx{}` API.

### 6. Perspective surfaces (later, low)
Projective transforms are outside vello (affine only). Keep the love homography
shader and route its output through a slot (item 2). A CPU warp with the same
depth-defocus is possible in Rust if love ever goes preview-only.

### 7. Retire the canvas path (decision, after 1–4)
Gate: all 42 evals green in scene mode, goldens recaptured, `bin/eval --open`
eyeballed. Then direct is the default and the canvas is the fallback. Only
then decide LÖVE-as-shell vs mlua hosting LuaJIT: render/hash would drop the
love dependency entirely; preview keeps it.
