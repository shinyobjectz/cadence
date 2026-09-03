# LÖVE2D context for ellua agents

## What LÖVE is

LÖVE (love2d, https://love2d.org) is a Lua game framework: an SDL + OpenGL/Metal
runtime exposing `love.graphics`, `love.image`, `love.timer`, etc., normally
driven by a realtime game loop (`love.run` calling `love.update`/`love.draw`
per display frame). ellua uses it purely as a fast, tiny (~10MB) 2D rasterizer
with LuaJIT — no game loop, no realtime anything.

## How ellua uses LÖVE (and why comps never see it)

- `runtime/main.lua` **replaces `love.run`** with an offline loop: for each
  frame index i, evaluate the timeline at `t = i / fps`, draw the scene to a
  canvas, read the pixels back, pipe raw RGBA to ffmpeg's stdin. Fixed dt,
  seek-not-playback — the loop can (and in `--shuffle` mode does) render
  frames out of order and must produce identical output.
- `runtime/painter.lua` is the **only** drawing code that touches `love.*`. It
  walks the node list and maps each node kind to `love.graphics` calls (plus a
  GPU YUV→RGB shader for video planes). This "painter contract" exists so a
  future Rust host can implement the same interface — comps must not depend on
  LÖVE.
- `lib/ellua/*` (everything a comp `require`s) is **host-free pure Lua**: no
  `love.*`, no I/O, no clocks. That is what makes comps portable and provable.
- True headless is impossible in LÖVE (GL needs a window); render/hash use a
  hidden background window (`SDL_MAC_BACKGROUND_APP=1`) on macOS, xvfb on
  Linux CI. `love.errorhandler` is overridden to print + exit 1 so failed
  renders never hang in an error window.
- Engine binary: vendored LÖVE 12 nightly (`vendor/love12.app`, Metal + async
  readback) preferred by `bin/ellua`; system LÖVE 11.5 works as fallback.
  Override with `LOVE_BIN`.

## The rule for agents

**Never write `love.*` code in a composition.** Comps are pure ellua API:
`e.comp`, `s:rect/circle/text/video/image/svg/lottie`, `s:script`, tweens.
If you find yourself reaching for `love.graphics` in a comp, you are either
missing an ellua node that does it, or you want the escape hatch below.

`love.*` IS allowed in exactly two places:

1. **`s:draw(fn)` escape hatch** — `fn(t, g)` receives the time and
   `g = love.graphics`. It runs inside the node's transform (translate/rotate/
   scale already applied, color = white × node opacity). It must remain a pure
   function of `t`:
   - output depends on `t` and nothing else — no accumulating upvalues, no
     `love.timer`, no `math.random` at draw time, no I/O;
   - same `t` → same pixels, in any frame order;
   - don't allocate GPU objects (fonts, canvases, meshes) per call — if you
     must create one, do it lazily once and reuse; never let creation order
     change output.
   Example:
   ```lua
   s:draw(function(t, g)
     g.setColor(1, 1, 1, 0.6)
     for i = 0, 9 do
       local phase = t * 2 + i * 0.6
       g.circle("line", 540 + math.cos(phase) * 200, 400 + math.sin(phase) * 200, 8 + i)
     end
   end)
   ```
2. **Runtime/painter work** — editing `runtime/painter.lua`, `runtime/main.lua`,
   or other host files (adding a node kind, a shader, a readback optimization).
   That's engine development, not comp authoring; keep `lib/` host-free and
   put all `love.*` in the painter.

## Debugging renders

- Compile/comp errors print to stderr (`ellua error: ...` + traceback), exit 1.
- `ELLUA_PROFILE=1 bin/ellua render ...` prints draw/readback/encode ms per
  frame — check it before "optimizing" anything (encode is usually the wall).
- `bin/ellua preview comp.lua` opens a realtime looping window (ESC quits) —
  fastest visual iteration; final judgment still requires rendering and
  looking at extracted frames (preview timing is wall-clock, render is exact).
- `bin/ellua hash comp.lua` prints `FRAME <i> <md5>` per frame — run twice and
  diff for determinism; `--shuffle` + sort to prove seek-safety.
