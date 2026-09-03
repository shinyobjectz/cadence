# Ellua web host

Lua 5.4 compiled to WebAssembly ([wasmoon](https://github.com/ceifa/wasmoon))
plus a Canvas2D painter. Compositions are the same `lib/ellua` files the native
host runs. This is **preview/seek**, not encode.

```bash
# from the ellua/ root
bin/web
# open http://127.0.0.1:8765/web/
```

Must be served over HTTP so `fetch('../lib/ellua/…')` works. No npm build;
wasmoon and lottie-web load from jsDelivr.

Browser-capable: primitives, type, fonts, images, SVG, spritesheets, particles,
charts, ornaments, OKHSL palettes, bounce, captions, spine pose, pulse follow,
video/WebM (HTML `<video>` seek), Lottie (lottie-web), and the audio-mix plate
(HTML `<audio>` under the NASA clip). Native-only: HTML/Blitz, vello, mesh
displace, Box2D bake, `s:draw` / `s:fx` shader chains, SDF outline type,
perspective camera, `s:world` / `s:mesh` (wgpu/glTF).

## Shipping

`lib/ellua` is the portable package. Copy or vendor that tree:

- LuaJIT host: `package.path` includes `lib/?.lua;lib/?/init.lua`, then `require("ellua")`
- wasmoon: mount the same files (see `web/host.js`) and `require("bridge")` for snapshot JSON
- Rust/mlua (later): load those files into a Lua 5.4 or LuaJIT state; do not reimplement nodes

Wasmoon 1.16.0 is loaded from jsDelivr in the preview page. Pin that version if you
bundle; do not compile ellua-love through Emscripten.
