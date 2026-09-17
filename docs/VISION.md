# Vision MCP — giving vision and video models the best view of a comp

Status: built 2026-09-16 (phases 1–3 below), `vision/` + `bin/cadence-vision`,
registered in `.mcp.json`. This replaces the editor-model direction (`model/`, scrapped the same day: a
timeline-only model cannot read pixels, and a pixel-reading editor model is
out of reach). The bet is the opposite one: keep the frontier VLM/video model
as the editor, and build **one MCP server** that turns Cadence frames and
source clips into whatever that model reads best, including real 3D data.

## What we measured

Depth Anything 3 (ByteDance Seed, Nov 2025) runs on the Mac today. The
`depth-anything-3` PyPI package hard-depends on xformers, which does not build
on macOS; installing it with `--no-deps` on top of system torch 2.8 (MPS) and
adding the light deps by hand works. All numbers below are M-series Metal,
process resolution 504.

| model | task | input | time |
|---|---|---|---|
| DA3-SMALL (Apache) | mono depth + intrinsics | 1280×720 Cadence frame | 0.12 s |
| DA3-SMALL | mono depth | 1920×1080 NASA clip frame | 0.26 s |
| DA3METRIC-LARGE (Apache) | metric depth + sky mask | 960×540 real footage | 1.1 s |
| DA3-BASE (Apache) | 8-view geometry: depth, conf, poses, K, GLB point cloud | 8 × 960×540 | 8.6 s |

What the outputs looked like on our material:

- `evals/out/camera.mp4` (two perspective planes): the tilted planes come out
  depth-ordered, the nearer plane brighter. DA3 recovers the 3D that the comp
  rendered.
- `evals/out/blend_modes.mp4` (flat 2D graphics): near-constant depth, which is
  the right answer, so a "this frame is flat" signal is free.
- NASA globes (`galileo.webm`, `earth_night.webm`, `apollo17.jpg`): a near disc on
  a far background. Space footage has no ground plane, and the model still
  gives a usable relief.
- Real talking-head footage: metric depth puts the speaker at ~0.7 m and the
  wall at ~3.7 m, a clean person silhouette, sky mask present. Multi-view on a
  static camera returns near-zero camera translation, which is correct.
- A Reactable stage render (webcam PIP on a dark slide) collapses to one flat
  slab in the point cloud: DA3 reads screen content as the plane it is.

Verdict: DA3 gives finite 3D data (depth, confidence, intrinsics, camera
poses, points) for both rendered frames and source clips, fast enough to be a
tool call rather than a batch job.

## What the models can take

No frontier model is trained on depth maps; they read a colormapped depth
image as a picture, and they cannot map text coordinates back onto pixels.
Marks drawn on the image work; grids alone barely help (Set-of-Mark, MOKA,
"Can VLMs See Squares"). For off-the-shelf models, tiled frame grids beat
frame sequences (IG-VLM beat video LLMs on 9/10 zero-shot benchmarks); for
Gemini, send the clip natively with timestamps instead.

| model | images / request | per-image budget | video | timestamps |
|---|---|---|---|---|
| Claude (Opus/Sonnet 4.x, Fable/Mythos 5.x) | 600 (100 on 200k ctx), 20 on claude.ai; >20 images forces ≤2000 px edge | ≤1568 px long edge ≈ 1568 tok; high-res 2576 px ≈ 4784 tok | none, send frames | only if we label them ("Image 1:") |
| GPT-4o / 5.x | 1500 | low fixed; high = 512 px tiles / 32 px patches; `original` for dense spatial | none | label them |
| Gemini 2.5 / 3 | 10 videos | 280/560/1120 tok per image | native, 1 fps default, `fps`, `start/end_offset`, 70 tok/frame (3), 258 (2.5) | yes, `MM:SS` in and out |
| Qwen3-VL | dynamic | 16 px patches, 2×2 merge, total pixel budget `24576·32²` | native, 2 fps default, `max_frames` | yes, interleaved text |
| InternVL3 / LLaVA-Video / VideoLLaMA3 / Apollo | — | tiles | 1 fps ≤120 / ≤64 frames / 1 fps ≤180 / 2 fps | no |

Sources and the full survey are in the research log at the end.

## Design

Cadence already knows the truth about its own output: node ids, boxes,
z-order, the timeline, clip in/out points (`bin/cadence verify --json`,
`model/lua/props.lua`). Source clips and images are the only pixels it does not
understand. The MCP fuses both: exact data from the comp, inferred 3D from
DA3, rendered in the shape the target model reads.

### Profiles

A profile is the model's input contract. The server ships built-ins
(`claude`, `claude-hires`, `gpt`, `gemini`, `qwen3-vl`, `local-64f`) and takes
overrides.

```
profile = { max_images, long_edge_px, tokens_per_image,
            video = { native: bool, fps, max_seconds, tokens_per_frame },
            timestamps: bool, labels: "image_n" | "mm_ss" | "none" }
```

### Tool surface

```
describe_model(profile)                       -> the contract above
plan_view(source, profile, question?)         -> ordered tool calls that fit the budget
keyframes(source, n, strategy, window?)       -> [{t, path}]   strategy: uniform | scene | motion | clip_diverse
contact_sheet(frames, cols, labels, cell_px)  -> one image, cells labeled index / timestamp / node id
depth(frame | clip, model, colormap, side_by_side) -> image + npz (depth, conf, K)
geometry(frames, model)                       -> { points, poses, intrinsics, conf, layers: [{id, depth_range, mask, bbox}] }
render_view(geometry, camera, marks)          -> image   camera: top | iso | front | side | source
annotate(frame, marks | boxes | cadence_nodes, style) -> image (Set-of-Mark ids)
scene_text(frame | clip)                      -> { layers near→far, per-layer bbox, motion, cadence node map }
diff(frameA, frameB, mode)                    -> image + stats   mode: pixel | depth | flow
```

`plan_view` is the adapter. For Claude it emits contact sheets at ≤1568 px
labeled `Image N:`; for Gemini it passes the clip with `fps` and offsets and
asks for `MM:SS`; for Qwen it caps total pixels. `annotate(cadence_nodes)`
draws the comp's own node boxes as numbered marks so the model's answers bind
to real node ids. `geometry.layers` fuses DA3 depth with Cadence z-order into
an explicit near-to-far list, which sidesteps the models' weak native depth
ordering. `scene_text` is the text fallback for models with tiny image budgets.

### Where it runs

| piece | Mac M-series | cuda-box 4070 |
|---|---|---|
| keyframes, contact_sheet, annotate, diff, scene_text scaffolding | yes (ffmpeg, PIL, open-clip) | — |
| depth: DA3-SMALL / BASE / METRIC-LARGE (all Apache) | yes, 0.1–1.1 s | yes |
| MoGe-2 metric point maps (MIT) | MPS, slower | 60 ms |
| geometry: DA3-BASE multi-view, 8 views | 8.6 s | faster |
| DA3-LARGE / GIANT (CC BY-NC), 3DGS branch | Large usable, Giant slow | yes |
| DA3-Streaming over whole clips | no | marginal at 12 GB |

License note: SMALL, BASE, MONO-LARGE and METRIC-LARGE are Apache 2.0.
LARGE-1.1 and GIANT-1.1 are CC BY-NC 4.0 and stay out of anything shipped.

### What is built (vision/)

```
bin/vision-setup                         # uv venv (py3.12) + torch + mcp + Depth Anything 3
bin/cadence-vision                       # stdio MCP server (what .mcp.json launches)
bin/cadence-vision call TOOL k=v ...     # same tools from the shell; PNGs land in vision/cache/
bin/cadence-vision test                  # pytest smoke suite
```

Tools as registered: `describe_model`, `plan_view`, `probe`, `keyframes`,
`frame`, `contact_sheet`, `annotate`, `diff`, `scene_text`, `depth`,
`geometry`, `render_view`. Image tools return `[json, png]`, sized to the
profile's long edge. Comps are rendered once through `bin/cadence render`
into `vision/cache/` (keyed by path + mtime) and then treated as video;
node boxes come from `vision/lua/props.lua` (luajit, host-free), which also
reports z-order, anchor, media `src` and parent.

Measured on the Mac through the tools (M-series, DA3 warm): `depth` on a
1280×720 comp frame 0.24 s, `geometry` over 6 views 1.1 s, comp render +
first `contact_sheet` a few seconds, everything else sub-second. The
`camera.lua` check is in the tests: the nearer perspective plane (`rect5`)
must get higher relief than the farther one (`rect4`), and `blend_modes.lua`
must read as flat.

Layout: `vision/cadence_vision/{profiles,sources,keyframes,sheet,annotate,
depth,scene_text,diff,server}.py`, `vision/cli.py`, `vision/lua/props.lua`,
`vision/tests/`. Python because DA3 is torch; the venv is `vision/.venv`
(gitignored, built by `bin/vision-setup` with uv; the `depth-anything-3`
wheel is installed `--no-deps` because its xformers pin has no macOS build).

### Phases

1. **Done:** profiles + `plan_view`, `keyframes` (uniform, scene, motion,
   diverse), `contact_sheet`, `annotate` from the comp's node state, `diff`
   (pixel, flow, depth), `scene_text` (nodes, motion, depth layers).
2. **Done:** `depth` via DA3 small/base/metric/mono on MPS, turbo colormap,
   side-by-side, sky fraction, the "frame is flat" signal, per-node depth.
3. **Done (first cut):** `geometry` + `render_view` + layers with DA3-BASE
   multi-view, confidence filtering, back-projection, iso/top/side/front.
   Still to do: real-footage keyframe validation with Claude and Gemini on
   the eval suite; a `native_video` helper that actually clips and
   re-encodes for Gemini/Qwen; text-box widths from parley instead of the
   0.56·size estimate; CLIP-based `diverse` when a text query is given.
4. **Month, cuda-box:** streaming SLAM over long clips, 3DGS novel views,
   learned keyframe selectors (AKS, Q-Frame).

The server itself: Python (FastMCP) is the pragmatic choice because DA3 is
torch; a Rust front could come later once the tool surface is settled. It
lives beside `bin/cadence` and shells out to it for the exact data.

## Research log

Landscape checked 2026-09-16. Depth Anything 3
([repo](https://github.com/ByteDance-Seed/Depth-Anything-3),
[paper](https://arxiv.org/abs/2511.10647), [API](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/main/docs/API.md),
[streaming](https://github.com/ByteDance-Seed/Depth-Anything-3/blob/main/da3_streaming/README.md)):
one DINO transformer, any number of views, depth + ray maps → poses, points,
confidence, sky, optional 3D Gaussians. Claims +35.7% pose and +23.6% geometry
over VGGT. A100: Giant 37.6 fps, Large 78, Base 126. Mac: upstream is
CUDA-only; `awesome-depth-anything-3` ([repo](https://github.com/Aedelon/awesome-depth-anything-3))
is an MPS fork, or do what this doc did.

The "one frame → 3D map" system Shane remembered is most likely Microsoft's
**MoGe-2** ([repo](https://github.com/microsoft/moge), [paper](https://arxiv.org/abs/2507.02546),
MIT): one image → metric point map, depth, normals, FOV; own DINOv2 model, not
built on Depth Anything. Tencent HunyuanWorld 1.0 ([repo](https://github.com/Tencent-Hunyuan/HunyuanWorld-1.0))
is the closest "3D-like world from one frame" (panorama → semantic layers →
per-layer depth → layered meshes) and leans on MoGe. Meta SAM 3D Objects
accepts any mono-depth point map for layout. TRELLIS (Microsoft) is
object-only. World Labs Marble, Apple Matrix3D, Meta WorldGen are closed or
self-contained. None build on DA3 itself.

DA3 in the wild: ComfyUI nodes and official tutorial (video-consistent depth,
point-cloud export, depth-conditioned generation), Blender addon, ROS2, WebXR
viewer, TensorRT builds, aescripts Depth Scanner 2 (After Effects depth-to-3D,
DOF, relighting), feed-forward 3DGS, monocular SLAM via DA3-Streaming,
Roboflow Workflows for AR occlusion, layered-parallax patterns (HunyuanWorld,
DepthScape).

Presentation evidence: IG-VLM ([arXiv](https://arxiv.org/abs/2403.18406)),
Set-of-Mark ([arXiv](https://arxiv.org/html/2310.11441v2)), Coarse
Correspondences ([arXiv](https://arxiv.org/abs/2408.00754), +20.5% ScanQA
from drawing matched object ids across frames), Agent3D-Zero (bird's-eye
renders with a grid, model picks viewpoints), DepthLM / SpatialBot (untuned
VLMs are bad at metric depth and at text→pixel coordinates), "VLMs are Biased"
(mid resolution often beats max). Keyframe selectors: AKS, AdaRD-Key, Q-Frame,
QCA. Model docs: [Claude vision](https://platform.claude.com/docs/en/build-with-claude/vision),
[OpenAI vision](https://developers.openai.com/api/docs/guides/images-vision),
[Gemini video](https://ai.google.dev/gemini-api/docs/video-understanding),
[Gemini media_resolution](https://ai.google.dev/gemini-api/docs/media-resolution),
[Qwen3-VL](https://github.com/QwenLM/Qwen3-VL).

Session artifacts (scratch, not committed): `/tmp/da3venv` (working DA3 env),
`/tmp/da3/{run,mv,metric,views}.py` (single-frame, multi-view, metric, and
point-cloud view scripts), outputs in `/tmp/da3/out/`.
