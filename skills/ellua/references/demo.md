# Demo kit — ScreenStudio-style product demos

## Aspect is intentional — and recorded, not cropped

`e.comp{width, height}` IS the aspect declaration. Desktop product demo = 1920×1080.
Social vertical = 1080×1920. Never default by habit — choose per deliverable.

**Never letterbox or squeeze a widescreen capture into a square/tall frame.**
Record the page at a viewport that is ALREADY the target shape:

```
node tools/record.mjs script.json outdir --aspect=4:5
```

| `--aspect` | viewport (CSS) | comp     |
|------------|----------------|----------|
| `16:9`     | 1600×1000      | 1920×1080 |
| `1:1`      | 1000×1000      | 1080×1080 |
| `4:5`      | 1000×1250      | 1080×1350 |
| `9:16`     | 800×1422       | 1080×1920 |

Presets live in `ASPECTS` at the top of `record.mjs`; `width`/`height` in
script.json still override. Viewport widths are chosen against **measured app
breakpoints**, not taste — verify with `tools/aspect_probe.mjs` before adding a
preset for a new app. For the ElevenLabs app the sidebar is `hidden lg:block`,
so anything under 1024 CSS px collapses it to a hamburger by itself. That is the
whole "minimize the sidebar for square/tall" step: pick a width under the app's
breakpoint and the app does it. Do not hack CSS to hide chrome.

The same `steps` array drives every aspect — steps are selector-based, so they
survive the reflow. Marker times will differ per aspect (different cursor
travel); that is expected, and is why narration should be placed by alignment
(`align_at`, see `elevenlabs.md`) rather than typed offsets.

### Retargeting a comp

Key the layout off the aspect and let `demo.live` fit itself:

```lua
local LAY = {
  ["16:9"] = { rec = "...", W = 1920, H = 1080, tabs = "inline" },
  ["4:5"]  = { rec = "...", W = 1080, H = 1350, tabs = "below"  },
}
local L = LAY[os.getenv("ELLUA_ASPECT") or "16:9"]
```

Wide frames can overlay UI on the video (`tabs = "inline"`). Square and vertical
cannot — put the video full-bleed and move controls (and the cursor that drives
them) **below** it. See `examples/demos/dub_demo.lua`.

## Recording a live page (real hover/modal/typed states)

```
node tools/record.mjs script.json outdir [--profile=dir]
```

CDP virtual time: page clock advances exactly 1/fps per captured frame; mouse
events dispatch per tick (`:hover`, dropdowns, JS animation render truthfully);
frames are lossless PNG → `capture.mp4` (x264 `-qp 0` 4:4:4) + `cursor.json`
(per-frame css-px track) + `manifest.json`.

script.json: `{ url, aspect, width, height, dpr, fps, steps: [...] }`; steps:
`{hold: s}`, `{move: {sel|x,y, dur}}`, `{hover: s}` (dwell),
`{click: {settle}}`, `{type: {sel, text, cps}}`, `{scroll: {dy, dur}}`,
`{upload: {sel, file, settle}}`, `{pwclick: {sel, settle}}`, `{press: {key}}`,
`{select: {nth, value}}`, `{waitfor: {sel, timeout_s}}`.
Any step also takes `"marker": "name"` — comps then read `markers.lua` by NAME
(`mark("upload")`) instead of by index, so inserting a step doesn't shift cues.
Selectors are Playwright locators (`text=...`, css). Requires system Chrome;
no browser download.

Outputs `frames/`, `capture.mp4`, `cursor.json`/`markers.json`/`manifest.json`
**and Lua sidecars** `meta.lua`, `cursor.lua`, `markers.lua` — comps `dofile`
these directly; there is no JSON conversion step.

## Authenticated recording (in-app demos)

Procedure — the human signs in once, recordings reuse the session:
1. `node tools/login.mjs https://elevenlabs.io/app/sign-in` — opens a HEADED
   browser on the persistent profile `~/.cache/ellua/chrome-profile`.
2. Human signs in, closes the browser window. Session cookies persist.
3. Record with `--profile=$HOME/.cache/ellua/chrome-profile` (or `"profile"` in
   script.json). Never commit or copy the profile dir — it holds live cookies.

## Composing (demo.live)

```lua
local d = demo.live(s, {
  dir = "recdir", track = dofile("recdir/cursor.lua"),
  comp_w = W, comp_h = H,          -- optional: defaults to the recorded aspect
  cursor = "assets/cursors/pointer_b.png",
  frame = { pad = 80, rx = 16, backdrop = false },
})
```

`demo.live` reads `recdir/meta.lua` and self-fits: viewport, dpr and fps come
from the recording, and base scale fits **both** axes
(`min((comp_w−2·pad)/cap_w, (comp_h−2·pad)/cap_h)`) so a tall capture in a tall
comp cannot overflow. Pass `css_w`/`css_h`/`dpr`/`comp_w`/`comp_h` only to
override. `d:track_at(frame)` returns the recorded cursor's screen position at a
frame — use it to hand a synthesized drag off to the real track on the exact
pixel, so the cursor never blinks out.

- Framing: gradient backdrop + center glow + 3-layer soft shadow + rounded
  screen (video `rx` = stencil mask). `pad` sets screen inset; base scale =
  (comp_w − 2·pad)/capture_w.
- `d:play_track(t, f0, f1)` — cursor sprite follows the recorded track;
  click-ripples fire on recorded mouse-down transitions.
- `d:zoom(t, {x,y,w,h}, dur)` — css-px rect punch-in (spring camera; screen and
  shadows move together). `d:overview(t, dur)` returns to the framed fit.
- Constraint: camera must be parked while `play_track` runs (cursor coords are
  transformed at authoring time). Punch-ins go on held states.
- cursor.lua is emitted by the recorder; no manual conversion.

## 3D camera (`perspective = true`) — beyond what ScreenStudio can do

A screen capture is a flat surface, so its 3D transform collapses to a 3x3
homography. `runtime/persp.lua` runs that homography BACKWARDS in a fragment
shader (screen px -> page px): exact perspective at any angle, no mesh, no
subdivision, no texture swim — and it yields the camera-space **depth of every
pixel** for free. That depth drives a real thin-lens defocus, so a tilted page
genuinely goes soft as it recedes. A 2D tool cannot fake this; it only ever
moves a rectangle.

All post — no re-recording, no second capture pass. Set `perspective = true` on
a `video` or `image` node. Animatable camera props:

| prop | meaning |
|------|---------|
| `yaw`, `pitch`, `roll` | orbit, radians |
| `dolly` | 1 = plane fills w×h; >1 pushes in |
| `fov` | vertical FOV, radians. Wide fov = wide lens = stronger perspective |
| `aperture` | blur px per unit relative depth error; 0 = deep focus |
| `maxcoc` | circle-of-confusion clamp |
| `focus_u`, `focus_v` | focus point in normalized page coords (0..1) |

The cursor must be composited INTO the page before the warp, or it will not tilt
or defocus with the surface and the shot falls apart. Set `cursor_src` on the
node (plus `cursor_w` in page px, and animatable `cursor_u`/`cursor_v` in
page-normalized coords) and ellua draws it onto the flat surface first.

Keep any focus lag behind the cursor SMALL (~0.1s). A focus-puller-style 0.3s
trail looks plausible until the cursor traverses the page, at which point focus
is left behind and the demo reads as broken. Note also that focusing "on the
cursor" on a tilted plane makes a BAND sharp — every point at that depth — not
a spot. That is correct optics, and it reads well.

`persp_margin` (default 1.45) sizes the offscreen stage; it must exceed peak
`dolly` plus the rotation swing or the plane clips at the stage edge.

Share one camera across planes with `s:camera{ yaw, pitch, fov }` and
`camera = cam` on each `perspective = true` node. The plane keeps its own
`dolly` (Z). Tween the camera, not four copies of yaw. Look-at (`look_x/y/z`,
`cam_x/y/z`) replaces euler when those props are authored.

Real 3D (glTF, lit meshes) is a separate layer: `s:world` + `s:mesh` + `s:light`.
That path is seek-safe the same way Lottie is — pose from `t`, pixels into
ImageData — and does not embed Bevy or Godot.

Focus follows the cursor by reading the recorded track and tweening
`focus_u/focus_v` to it — the focus pull is driven by the recording rather than
hand-keyed. Working sample: `examples/demos/screen3d.lua`.

Rendering cost is two cached canvases per node (flat compose + warped stage) and
a 20-tap spiral only where CoC exceeds 0.75px. Perspective composes with the
normal draw path, so rounded corners, opacity, parents and every decode path
(yuv / rgba / extracted frames) work unchanged.

## Static mode (demo.new)

For pages without needed live state: `ellua-capture` full-page PNG + `d:zoom/
move/click/scroll` fully synthesized. Same grammar, no recording step.

## Not adopted (evaluated)

ffmpeg zoompan (dead easing), puppeteer screencast video (lossy, jittery),
OBS (non-deterministic). rrweb and browser-use (open-source browser agent) are
future layers: record human walkthroughs / agent-authored walkthroughs.

## Virtual-time CDP hardening (learned on the authenticated app)

- Never `await` an `Input.dispatchMouseEvent` (or any input) while virtual time
  is paused — the page can't process input without clock. Fire → grant budget →
  collect. Same for `Page.captureScreenshot`: request, grant a 1ms micro-budget
  (subtracted from the main tick so timing stays exact), collect.
- Launch with `--run-all-compositor-stages-before-draw
  --disable-new-content-rendering-timeout`.
- Every tick op runs under a 20s watchdog (`--tick-timeout=ms`) naming the stuck
  op/step/frame; failures save `failure.png` + partial manifest + keep frames.
- Recordings ALWAYS run under a Monitor filtering
  `STEP|FRAME|FAILED|watchdog|recorded|Error` — a hang must surface as an event,
  never as silence.
