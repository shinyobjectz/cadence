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
| text, kinetic chars | ✅ | wrap, leading (metrics-relative like love), tags, reveal, outline, weight |
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

## Next

1. Colour-matrix filter in `scene/` (closes the effects gap; the ellua-effects
   crate already has the per-pixel math).
2. Video frames into image slots (yuv → rgb conversion stays in decode).
3. Retire the love canvas path once direct mode covers the eval suite; LÖVE
   becomes preview-only, and the mlua question becomes a packaging call.
4. Then the quality work the rasterizer unlocks: tweenable letter-spacing,
   gradient stops, mask paths, blur; typography lint rules (measure, leading,
   hierarchy ratios) now that text is measurable in Rust.
