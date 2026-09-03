# Effects and compositing

Ellua has two distinct layers:

1. **Native compositing** — `surface`, `group`, `shadow`, live
   `clip_node` masks, and `blend`.
2. **Raster adjustment** — CSS-style effects on `html` and `vector` nodes.
   These nodes own RGBA buffers, so their effects are deterministic and seekable.

Build the native pass before rendering a comp which uses `effects`:

```bash
cd ellua && bin/build-native
```

## Surface assembly

```lua
local panel = s:surface {
  x = 140, y = 100, w = 760, h = 460, rx = 36, color = "#151725",
  shadow = { blur = 44, dy = 24, alpha = 0.38 },
}
local sheen = s:rect {
  parent = panel, x = 28, y = 28, w = 704, h = 1, color = "#ffffff44",
  blend = "screen",
}
```

`parent` groups transforms and opacity. A child may set `clip_node = mask` to
use another node's live rounded rectangle as a mask; set `clip_invert = true`
for the inverse.

## Adjustment recipes

```lua
local art = s:vector {
  w = 1080, h = 1080,
  effects = { contrast = 1.08, saturate = 1.12, hue_rotate = -6 },
  draw = function(v) -- pure f(t)
    -- vector commands
  end,
}

s:script(function(t)
  t:tween(art, 0.8, { effect_blur = 10, effect_saturate = 0.45 }, "sineInOut")
  t:tween(art, 0.8, { effect_blur = 0, effect_saturate = 1.12 }, "sineInOut")
end)
```

Available keys: `blur`, `brightness`, `contrast`, `saturate`, `grayscale`,
`sepia`, `invert`, `opacity`, and `hue_rotate` (degrees). Effects execute in
this order: blur → brightness → contrast → saturation → grayscale → sepia →
invert → hue rotation → opacity.

Use a soft 0→peak→0 envelope for blur, color shifts, and other temporary
treatments. Permanent grades should be small: contrast/saturation in
`1.03..1.15` usually reads intentional; large values read as a social filter.

## Advanced shaders

Use `s:draw(fn)` only when the primitive cannot be expressed with a surface,
vector, or adjustment chain. The function must be pure in time: create shaders
once in a closure, pass `t`-derived uniforms each frame, and never retain
frame-to-frame state. Keep custom shaders to one visual decision per pass:
mask, grade, displacement, or composite—not an opaque all-in-one effect.

There is no general Lua shader DSL yet. Treat a custom `draw` shader as an
advanced escape hatch; it remains host-specific, while `effects` remains
portable to the planned Rust host.
