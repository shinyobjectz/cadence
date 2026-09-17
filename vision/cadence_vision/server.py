"""The MCP surface. Every image tool returns [text summary (JSON), image] with
the image already sized to the profile's long edge, and writes the same PNG
to vision/cache so a CLI caller can open it."""

from __future__ import annotations

import hashlib
import io
import json
from typing import Optional

import numpy as np
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.utilities.types import Image as McpImage
from PIL import Image

from . import CACHE, ROOT
from . import profiles as P
from . import keyframes as KF
from . import sheet as SH
from . import annotate as AN
from . import diff as DF
from . import scene_text as ST
from .sources import open_source, frame_at, fit, mmss

mcp = MCPServer("cadence-vision", instructions=(
    "Renders Cadence comps (.lua), video files and images into views a vision/video model reads well. "
    "Start with describe_model(profile) and plan_view(source, profile, question); then call the planned tools. "
    "Images come back sized for the profile. Marks drawn on frames are numbered; the legend maps numbers to "
    "Cadence node ids so answers can name real nodes."))


def _png(im: Image.Image, name: str) -> McpImage:
    p = CACHE / name
    im.save(p, format="PNG", optimize=True)
    buf = io.BytesIO(); im.save(buf, format="PNG", optimize=True)
    return McpImage(data=buf.getvalue(), format="png")


def _name(*parts) -> str:
    return hashlib.sha1("|".join(str(p) for p in parts).encode()).hexdigest()[:12]


def _j(d: dict) -> str:
    return json.dumps(d, ensure_ascii=False)


# ----------------------------------------------------------------------------- profiles

@mcp.tool()
def describe_model(profile: str = "claude") -> str:
    """What a model can take: max images, long edge px, tokens per image, native video (fps, max seconds), timestamps.
    Builtins: claude, claude-hires, claude-api, gpt, gemini, qwen3-vl, local-64f."""
    return _j(P.describe(profile))


@mcp.tool()
def plan_view(source: str, profile: str = "claude", question: str = "", image_budget: int = 0) -> str:
    """Ordered tool calls that show `source` (comp .lua, video, or image) to `profile` within its budget.
    `question` steers it (mentions of depth/3d/motion add depth, geometry or scene-cut steps)."""
    src = open_source(source)
    return _j({"source": src.info(), **P.plan(src.info(), profile, question or None, image_budget or None)})


@mcp.tool()
def probe(source: str) -> str:
    """Source facts: kind, size, duration, fps; for a comp also its node list (ids, kinds, z-order, media srcs)."""
    src = open_source(source)
    info = src.info()
    if src.comp:
        d = AN.props(src.comp, [0.0])
        info["nodes"] = d["nodes"]
        info["canvas"] = [d.get("width"), d.get("height")]
    return _j(info)


# ----------------------------------------------------------------------------- frames

@mcp.tool()
def keyframes(source: str, n: int = 6, strategy: str = "diverse", t0: float = -1, t1: float = -1, query: str = "") -> str:
    """Pick n times from a source. strategy: uniform | scene (after cuts) | motion (energy-weighted) | diverse (coverage)
    | query (relevance to `query` + coverage, CLIP). Optional window [t0, t1] in seconds."""
    src = open_source(source)
    win = (t0, t1) if t0 >= 0 and t1 > t0 else None
    r = KF.pick(src, n, strategy, win, query or None)
    r["timestamps"] = [mmss(t) for t in r["times"]]
    return _j(r)


@mcp.tool()
def frame(source: str, t: float = 0.0, profile: str = "claude") -> list:
    """One frame at time t, sized to the profile. Returns [json, image]."""
    src = open_source(source)
    pr = P.get_profile(profile)
    im = fit(Image.fromarray(frame_at(src, t)), pr.long_edge_px)
    return [_j({"t": t, "timestamp": mmss(t), "size": im.size, "source": str(src.path)}), _png(im, f"frame-{_name(src.media, t)}.png")]


@mcp.tool()
def contact_sheet(source: str, n: int = 6, strategy: str = "diverse", cols: int = 3, labels: str = "timestamp",
                  profile: str = "claude", times: str = "", query: str = "") -> list:
    """n frames tiled into one labeled image at the profile's long edge. labels: timestamp | index | seconds | none.
    `times` (comma-separated seconds) overrides keyframe selection; `query` with strategy=query picks by relevance."""
    src = open_source(source)
    pr = P.get_profile(profile)
    ts = [float(x) for x in times.split(",") if x.strip()] if times else KF.pick(src, n, strategy, None, query or None)["times"]
    im = SH.from_source(src, ts, cols, labels, pr.long_edge_px)
    return [_j({"times": ts, "timestamps": [mmss(t) for t in ts], "cols": cols, "size": im.size,
                "reading": "cells are numbered left-to-right, top-to-bottom; the label shows cell number and time"}),
            _png(im, f"sheet-{_name(src.media, ts, cols, labels, pr.long_edge_px)}.png")]


@mcp.tool()
def annotate(source: str, t: float = 0.0, style: str = "som", marks: str = "", grid_px: int = 0, profile: str = "claude") -> list:
    """Frame at t with numbered marks. For a comp, marks are its visible nodes at t (legend maps numbers to node ids).
    Otherwise pass `marks` as JSON [{"id":..,"x":..,"y":..,"w":..,"h":..}] in source pixels. style: som | boxes | tags.
    grid_px > 0 also draws a labeled coordinate grid."""
    src = open_source(source)
    pr = P.get_profile(profile)
    fr = Image.fromarray(frame_at(src, t))
    im = fit(fr, pr.long_edge_px)
    scale = im.width / fr.width
    mk: list[dict] = []
    if marks:
        mk = json.loads(marks)
    elif src.comp:
        mk, _ = AN.comp_marks(src, t)
    if grid_px:
        im = AN.draw_grid(im, grid_px, scale)
    if mk and style != "none":
        im = AN.draw_marks(im, mk, style, scale)
    return [_j({"t": t, "timestamp": mmss(t), "size": im.size, "source_px": [fr.width, fr.height], "legend": AN.legend(mk)}),
            _png(im, f"ann-{_name(src.media, t, style, marks, grid_px, pr.long_edge_px)}.png")]


@mcp.tool()
def diff(source: str, t0: float, t1: float, mode: str = "pixel", source_b: str = "", profile: str = "claude") -> list:
    """Compare frame t0 with frame t1 (or with t1 of `source_b`). mode: pixel (heatmap) | flow (arrows) | depth (relief change)."""
    src = open_source(source)
    srcb = open_source(source_b) if source_b else src
    pr = P.get_profile(profile)
    a = frame_at(src, t0, pr.long_edge_px); b = frame_at(srcb, t1, pr.long_edge_px)
    if mode == "flow":
        im, stats = DF.flow(a, b)
    elif mode == "depth":
        from . import depth as DP
        im, stats = DF.depth(DP.mono(a), DP.mono(b))
    else:
        im, stats = DF.pixel(a, b)
    return [_j({"t0": t0, "t1": t1, "mode": mode, **stats}), _png(fit(im, pr.long_edge_px), f"diff-{_name(src.media, srcb.media, t0, t1, mode)}.png")]


@mcp.tool()
def native_video(source: str, profile: str = "gemini", t0: float = 0.0, t1: float = -1, fps: float = 0.0,
                 long_edge: int = 0, presample: bool = False, max_inline_mb: float = 20.0) -> str:
    """Trim + scale + H.264 a source for models that ingest video natively (Gemini, Qwen3-VL). Returns the mp4 path,
    size, a data URL when under max_inline_mb, and the request fragment for gemini (direct API), openrouter and
    qwen/vLLM. presample=true re-times the clip to `fps` so every provider sees exactly those frames."""
    from . import native as NV
    src = open_source(source)
    pr = P.get_profile(profile)
    return _j(NV.prepare(src, pr, t0, t1 if t1 > 0 else src.duration, fps or pr.video.fps, long_edge or pr.long_edge_px, presample, max_inline_mb))


# ----------------------------------------------------------------------------- text

@mcp.tool()
def scene_text(source: str, t: float = 0.0, t1: float = -1, with_depth: bool = False) -> str:
    """Structured text: for a comp, every visible node at t (id, kind, region, box, z, text, colour). With t1 > t,
    coarse motion between t and t1. with_depth adds DA3 relief stats, near-to-far layers and per-node depth."""
    src = open_source(source)
    out: dict = {"source": src.info()}
    if src.comp:
        out["nodes"] = ST.comp_nodes_text(src, t)
    if t1 > t and src.kind != "image":
        out["motion"] = ST.motion_text(src, t, t1)
    if with_depth:
        from . import depth as DP
        fr = frame_at(src, t, 1024)
        res = DP.mono(fr)
        per_node = None
        if src.comp:
            _, nb = AN.comp_marks(src, t)
            per_node = DP.depth_at_boxes(res, {k: v for k, v in nb["nodes"].items() if float(v.get("opacity") or 0) > 0.02},
                                         nb["width"] or src.width, nb["height"] or src.height)
        out.update(ST.depth_text(res["stats"], DP.layers(res["depth"], res["conf"]), per_node))
    return _j(out)


# ----------------------------------------------------------------------------- depth & geometry

@mcp.tool()
def depth(source: str, t: float = 0.0, model: str = "small", colormap: str = "turbo", side_by_side: bool = True,
          profile: str = "claude") -> list:
    """Depth Anything 3 relief for the frame at t (near = warm). model: small | base | metric (metres + sky) | mono.
    Returns stats (near/median/far, relative_range, flat flag, sky fraction, focal px) and per-node depth for comps."""
    from . import depth as DP
    src = open_source(source)
    pr = P.get_profile(profile)
    fr = frame_at(src, t, 1024)
    res = DP.mono(fr, model)
    dimg = DP.depth_image(res, colormap)
    im = DP.side_by_side(fr, dimg) if side_by_side else dimg
    info = {"t": t, "timestamp": mmss(t), "model": DP.MODELS.get(model, model), "metric": res["metric"], "secs": round(res["secs"], 2),
            **res["stats"], "layers_near_to_far": DP.layers(res["depth"], res["conf"]),
            "reading": f"{colormap}: warm/bright = near, cool/dark = far" + ("; left RGB, right depth" if side_by_side else "")}
    if src.comp:
        _, nb = AN.comp_marks(src, t)
        info["nodes"] = DP.depth_at_boxes(res, {k: v for k, v in nb["nodes"].items() if float(v.get("opacity") or 0) > 0.02},
                                          nb["width"] or src.width, nb["height"] or src.height)
    np.savez_compressed(CACHE / f"depth-{_name(src.media, t, model)}.npz", depth=res["depth"],
                        conf=res["conf"] if res["conf"] is not None else np.zeros(0), K=res["K"] if res["K"] is not None else np.zeros(0))
    return [_j(info), _png(fit(im, pr.long_edge_px), f"depth-{_name(src.media, t, model, colormap, side_by_side)}.png")]


@mcp.tool()
def geometry(source: str, n: int = 6, strategy: str = "uniform", model: str = "base", times: str = "") -> str:
    """Any-view geometry from n frames: camera poses (centres relative to view 1), intrinsics, confidence, a fused
    point cloud (saved as npz; id returned for render_view) and near-to-far layers of the first view."""
    from . import depth as DP
    src = open_source(source)
    ts = [float(x) for x in times.split(",") if x.strip()] if times else KF.pick(src, n, strategy)["times"]
    frames = [frame_at(src, t, 1024) for t in ts]
    g = DP.multiview(frames, model)
    key = _name(src.media, ts, model)
    p = DP.save_geometry(g, key)
    c = g["camera_centers"] - g["camera_centers"][0]
    return _j({"geometry_id": key, "npz": str(p), "times": ts, "views": len(ts), "model": DP.MODELS.get(model, model),
               "secs": round(g["secs"], 2), "points": int(len(g["points"])),
               "focal_px": round(float(g["intrinsics"][0][0, 0]), 1),
               "camera_centers_rel": [[round(float(v), 4) for v in row] for row in c],
               "camera_travel": round(float(np.linalg.norm(c, axis=1).max()), 4),
               "scene_extent": [round(float(v), 3) for v in (g["points"].max(0) - g["points"].min(0))],
               "layers_view1_near_to_far": DP.layers(g["depth"][0], None if g["conf"] is None else g["conf"][0]),
               "reading": "units are DA3's relative scale unless model=metric; camera_travel≈0 means a static camera"})


@mcp.tool()
def render_view(geometry_id: str, camera: str = "iso", size: int = 900, show_cameras: bool = True, profile: str = "claude") -> list:
    """Orthographic render of a geometry() point cloud. camera: iso | top | side | front. Yellow circles are camera positions."""
    from . import depth as DP
    p = CACHE / f"geo-{geometry_id}.npz"
    if not p.exists():
        raise FileNotFoundError(f"no geometry {geometry_id}; call geometry() first")
    g = DP.load_geometry(p)
    pr = P.get_profile(profile)
    im = DP.render_points(g["points"], g["colors"], camera, min(size, pr.long_edge_px), g["camera_centers"] if show_cameras else None,
                          point_px=2 if len(g["points"]) < 200_000 else 1)
    return [_j({"geometry_id": geometry_id, "camera": camera, "size": im.size,
                "reading": {"top": "looking down: x right, depth (z) up the image", "side": "from the right: depth right, y down",
                            "front": "the source camera's view", "iso": "rotated 35° about y and 25° about x"}[camera if camera in ("top", "side", "front") else "iso"]}),
            _png(im, f"view-{geometry_id}-{camera}.png")]


def main():
    mcp.run()


if __name__ == "__main__":
    main()
