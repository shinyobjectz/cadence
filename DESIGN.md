# ellua — canon design decisions

Locked decisions. Change only with a written reason here.

## 1. Core invariants

1. **Seek, not playback** — composition = pure function of time `t`; any frame, any order.
2. **Seconds, not frames** — fps is a render parameter.
3. **Duration static** — declared in comp header, immutable at render time.
4. **Determinism by construction** — renderer owns the VM: clock stubbed, RNG seeded,
   zero I/O during render. All network/asset work happens in the resolve phase.
5. **Audio never touches the engine** — resolved clips + volume envelopes → ffmpeg
   `filter_complex` at encode; mux with `-c copy`.

## 2. Hosts

- **ellua host**: a source-pinned LÖVE fork at `vendor/ellua-love`, built as the
  `ellua-love` sidecar. The Rust `ellua` CLI launches it with `ELLUA_HEADLESS=1`
  for `render`, `hash`, `lint`, and `check`. macOS Metal uses a virtual main
  backbuffer with no SDL window or drawable presentation; Vulkan must gain the same
  surface-free virtual-backbuffer implementation before Linux and Windows release
  bundles ship. `preview` is the only interactive windowed mode. Custom `love.run`
  evaluates fixed times, renders a canvas, reads back ordered RGBA frames, then
  writes those frames to the bundled ffmpeg sidecar. Golden hashes are scoped to an
  Ellua renderer build and platform.
- **web host** (preview only): Lua 5.4 via wasmoon (official Lua compiled to
  WebAssembly) plus a Canvas2D painter. Same host-free `lib/ellua` compositions;
  seek is still `evaluate(t)`. This is not the encode path — ffmpeg and native
  helpers stay on ellua-love. Reason: shipping the Metal/LuaJIT fork through
  Emscripten would throw away FFI dylibs and the virtual backbuffer; the painter
  contract already exists so a second host can be small.
- **Scene rasterizer (decision 2026-09-16, supersedes the line below):** the
  evaluated node tree paints into ONE rasterizer, `scene/` (`cadence-scene`,
  vello_cpu + parley), streamed as a flat command list over the C ABI. One
  antialiaser, one font stack, one gamma. LÖVE shrinks to a frame loop and the
  ffmpeg pipe; when every node in a comp is scene-owned the renderer skips the
  canvas and GPU readback entirely (`PROF direct=1`). Node kinds move over one at
  a time behind `CADENCE_SCENE=1`; kinds the crate cannot paint yet fall back to
  love per node, with a z-order-preserving flush. See `docs/SCENE.md` for
  coverage, opcodes and the measurements that justified the direction.
- **Default renderer (decision 2026-09-16, gate of docs/SCENE.md item 7):**
  the scene rasterizer is the default; `CADENCE_SCENE=0` is the fallback. Gate
  met: 42/42 renderable evals green and eyeballed in scene mode, scene goldens
  captured (`drop` needs the LÖVE 12 physics API and is the one case this Mac
  cannot run under Homebrew 11.5). Love now does three things for `render`:
  the frame loop + ffmpeg pipe, the GLSL escape hatches (`shadertoy`,
  `worley`, `s:draw`, perspective homography, world/wgpu) painted into slots,
  and `preview`.
- **LÖVE-as-shell vs mlua (decided 2026-09-16): LÖVE stays the shell for now.**
  Reason: the escape hatches above still need `love.graphics` canvases, and
  they are used by shipped evals (fx, camera, perspective_*, world3d, draw).
  An mlua host for `render`/`hash` becomes worth it only when those hatches
  are either CPU-ported (perspective: a projective warp in Rust; world: wgpu
  already, needs only a slot without love) or declared preview-only. Until
  then a second host would be a second painter to keep in parity. Revisit when
  a release needs to drop the LÖVE dependency (Linux/Windows bundles).
- ~~A Rust renderer is not a planned replacement host.~~ Rust owns distribution,
  the native helpers, and the rasterizer. The LÖVE-compatible `love.*` surface
  remains for `s:draw` escape hatches, `shadertoy`/`worley`, perspective
  surfaces and the 3D world layer, all composited through image slots.
- Authoring core = Lua-native scene graph + signals + coroutine timeline
  (`waitUntil` events). React model (`react-ellua` via react-lua/react-luau host
  config) = later optional skin, never the core.
  Optional `e.comp { inputs = { bg = { kind = "video" } } }` lets a host bind
  media without rewriting Lua: `scene(s)` reads `s.input.bg` (a string path).
  Studio writes a sibling `<comp>.inputs.json`; the CLI accepts `--inputs FILE.json`
  and repeatable `--input KEY=PATH`. Merge is key-wise: defaults < sibling JSON <
  `--inputs` file < `--input` flags. Hardcoded `src=` remains valid. Missing
  required inputs error at compile (`ellua: input "bg" is not bound`). The host
  injects the resolved string table into `Comp:compile`; `lib/ellua` stays disk-free.

## 3. Encode (canon)

Bundled ffmpeg subprocess only — never linked, never GStreamer. Three invocations per render:
raw-RGBA video pass → `filter_complex` audio mix → `-c copy` mux. `ffprobe` at
resolve. Pin and ship the ffmpeg build inside each Ellua distribution.
Formats: mp4/h264 default; webm/vp9 + mov/prores4444 for alpha; png-sequence; gif.

## 4. Decode (canon) — `ellua-decode`

Lib-first Rust crate, zero IPC assumptions in core.

- **rsmpeg + vendored FFmpeg 8** shared libs (only stack covering
  h264/h265/vp9/av1/prores across mp4/mov/webm/mkv).
- **BestSource-style verified index**: first-open linear pass → frame→(PTS, keyframe,
  position) table (+ opt-in per-frame 8-byte hash, "paranoid" mode); index cached on
  disk. Seek = keyframe-back + decode-forward + verify landed PTS; anomaly → linear
  fallback from known-good point. FFI-linking BestSource itself is an approved
  alternative to reimplementation.
- **Hw decode via FFmpeg hwaccel layer** (one code path): VideoToolbox on macOS
  (ProRes hw on M1 Pro+), NVDEC on 4070 (sessions unlimited; 10-bit h264 = sw
  fallback), VAAPI on Linux, transparent sw fallback.
- **Determinism**: decoded YUV is bit-exact by spec across conformant decoders (incl.
  hw). Only post-decode conversion diverges → ONE pinned YUV→RGB path we own;
  fixed-point/integer path when cross-machine hash-exactness required. Never hw
  scalers/CSC on the deterministic path.
- **Cache**: per-stream sequential decode state machine (forward fast path within a
  render chunk), small per-stream NV12 LRU (never RGBA in cache), global cap
  256MB–1GB, mpv-style packet cache for back-seeks as needed.
- **Linkage — one crate, three consumers** (lib-first, zero IPC assumptions in core):
  1. **love host (primary): cdylib with C ABI, loaded via LuaJIT FFI** (`ffi.load`)
     — in-process, no IPC; decoded frames land in memory LuaJIT wraps as ImageData.
     (mlua is the inverse direction — Rust hosting Lua — and applies only to the
     rust host; it cannot be injected into love, which already owns its LuaJIT VM.)
  2. rust host: plain rlib in-process; mlua embeds LuaJIT/Luau to run comps.
  3. isolation fallback: daemon + msgpack unix socket + shm ring buffer (crash
     isolation for hostile files; shm sustains multiple 4K60 streams). macOS
     IOSurface zero-copy = profiling-gated later optimization.
- Rejected: GStreamer (random access fights pipeline model), libmpv render API
  (unsuited by design), vk-video (unmaintained), pure-Rust hevc/prores (doesn't exist).

## 5. Perceptual layer (canon) — `ellua probe`

Mac-first stack (locked 2026-08-01):

| role | model | note |
|------|-------|------|
| frames / text-query | **MobileCLIP2-S2** | CoreML/ANE, single-digit ms/frame |
| spatial / patch lint | **C-RADIOv3-B** | commercial-OK license; DINOv3 runs hot locally |
| temporal | **X-CLIP-B** | vanilla transformers; V-JEPA2 too heavy locally |
| audio | **CLAP** | same stack as 11l media-DNA pipeline |

Probe → embedding sidecar (stride frames + patch grids + audio windows + derived:
motion energy, cut/freeze/black via embedding deltas) → `ellua query` timestamped
hits; comp asserts (`assert.visible("logo", 2, 4)`) compile to calibrated
probability thresholds at check time. License tripwires: VideoCLIP-XL (NC),
RADIOv2.5/E-RADIO (NC — C-RADIO only).

## 6. Resolve phase + ElevenLabs

Pre-render, network allowed, content-hash cached. ElevenLabs first-class: TTS
(`eleven_v3`), SFX, Music → clip durations known before render; Scribe word
timestamps → `waitUntil('word:…')` sync + karaoke captions for free.
