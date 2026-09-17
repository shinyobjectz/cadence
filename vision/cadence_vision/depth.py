"""Depth Anything 3 behind a lazy loader. Mono depth for one frame (with a
flatness score and, for comps, per-node depth), multi-view geometry for
several frames (poses, intrinsics, points, near-to-far layers)."""

from __future__ import annotations

import contextlib
import logging
import os
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image

from . import CACHE

os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")   # torch + open3d each ship an OpenMP runtime on macOS
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


@contextlib.contextmanager
def quiet():
    """Model libraries log to stdout; stdout is the MCP wire, so route everything to stderr."""
    logging.getLogger().setLevel(logging.ERROR)
    for name in ("depth_anything_3", "huggingface_hub", "torch", "urllib3", "httpx"):
        logging.getLogger(name).setLevel(logging.ERROR)
    with contextlib.redirect_stdout(sys.stderr):
        yield

MODELS = {
    "small": "depth-anything/DA3-SMALL",            # Apache 2.0, any-view
    "base": "depth-anything/DA3-BASE",              # Apache 2.0, any-view
    "metric": "depth-anything/DA3METRIC-LARGE",     # Apache 2.0, metres + sky mask, single view
    "mono": "depth-anything/DA3MONO-LARGE",         # Apache 2.0, relative mono depth
    "large": "depth-anything/DA3-LARGE-1.1",        # CC BY-NC 4.0: research only
}


def device() -> str:
    import torch
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@lru_cache(maxsize=2)
def load(name: str = "small"):
    from depth_anything_3.api import DepthAnything3
    repo = MODELS.get(name, name)
    t0 = time.time()
    with quiet():
        m = DepthAnything3.from_pretrained(repo).to(device()).eval()
    m._cv_load_secs = time.time() - t0
    return m


def _colormap(x: np.ndarray, name: str = "turbo") -> np.ndarray:
    import matplotlib
    cm = matplotlib.colormaps.get_cmap(name)
    return (cm(np.clip(x, 0, 1))[..., :3] * 255).astype(np.uint8)


def to_relief(depth: np.ndarray) -> np.ndarray:
    """0..1 with near = 1, from inverse depth, robust to outliers."""
    inv = 1.0 / np.clip(depth, 1e-6, None)
    lo, hi = np.percentile(inv, 1), np.percentile(inv, 99)
    return np.clip((inv - lo) / max(hi - lo, 1e-9), 0, 1)


def mono(frame: np.ndarray, model: str = "small", process_res: int = 504) -> dict:
    import torch
    m = load(model)
    t0 = time.time()
    with quiet(), torch.no_grad():
        pred = m.inference([frame], process_res=process_res)
    d = pred.depth[0].astype(np.float32)
    conf = None if pred.conf is None else pred.conf[0].astype(np.float32)
    sky = None if pred.sky is None else pred.sky[0]
    K = None if pred.intrinsics is None else np.asarray(pred.intrinsics[0])
    med = float(np.median(d))
    p5, p95 = np.percentile(d, 5), np.percentile(d, 95)
    flat = float((p95 - p5) / max(med, 1e-6))
    return {"depth": d, "conf": conf, "sky": sky, "K": K, "metric": bool(pred.is_metric) if not isinstance(pred.is_metric, dict) else False,
            "secs": time.time() - t0, "model": model,
            "stats": {"near": float(p5), "median": med, "far": float(p95), "relative_range": round(flat, 4),
                      "flat": flat < 0.08, "sky_fraction": None if sky is None else round(float((sky > 0.5).mean()), 4),
                      "focal_px": None if K is None else round(float(K[0, 0]), 1)}}


def depth_image(res: dict, colormap: str = "turbo") -> Image.Image:
    return Image.fromarray(_colormap(to_relief(res["depth"]), colormap))


def side_by_side(frame: np.ndarray, dimg: Image.Image) -> Image.Image:
    h, w = dimg.height, dimg.width
    rgb = Image.fromarray(frame).resize((w, h), Image.LANCZOS)
    out = Image.new("RGB", (w * 2 + 4, h), (18, 18, 20))
    out.paste(rgb, (0, 0)); out.paste(dimg, (w + 4, 0))
    return out


def depth_at_boxes(res: dict, boxes: dict, W: int, H: int) -> dict:
    """Median depth (and relief 0..1) inside each node box; boxes in comp pixels, depth at its own res."""
    d = res["depth"]; rel = to_relief(d)
    sy, sx = d.shape[0] / H, d.shape[1] / W
    out = {}
    for nid, b in boxes.items():
        x0, y0 = int(max(0, b["x"] * sx)), int(max(0, b["y"] * sy))
        x1, y1 = int(min(d.shape[1], (b["x"] + b["w"]) * sx)), int(min(d.shape[0], (b["y"] + b["h"]) * sy))
        if x1 <= x0 or y1 <= y0:
            continue
        out[nid] = {"depth": round(float(np.median(d[y0:y1, x0:x1])), 4), "relief": round(float(np.median(rel[y0:y1, x0:x1])), 3)}
    return out


def layers(depth: np.ndarray, conf: np.ndarray | None, k: int = 4, min_frac: float = 0.02) -> list[dict]:
    """Near-to-far bands by inverse-depth quantiles: id, depth range, pixel fraction, bbox (in depth-map pixels)."""
    rel = to_relief(depth)
    edges = np.linspace(1, 0, k + 1)   # near = 1
    out = []
    H, W = depth.shape
    for i in range(k):
        hi, lo = edges[i], edges[i + 1]
        m = (rel <= hi) & (rel > lo) if i < k - 1 else (rel <= hi) & (rel >= lo)
        frac = float(m.mean())
        if frac < min_frac:
            continue
        ys, xs = np.where(m)
        out.append({"layer": len(out) + 1, "order": "near" if i == 0 else ("far" if i == k - 1 else "mid"),
                    "depth_min": round(float(depth[m].min()), 3), "depth_max": round(float(depth[m].max()), 3),
                    "fraction": round(frac, 3),
                    "bbox_norm": [round(xs.min() / W, 3), round(ys.min() / H, 3), round(xs.max() / W, 3), round(ys.max() / H, 3)],
                    "centroid_norm": [round(float(xs.mean() / W), 3), round(float(ys.mean() / H), 3)]})
    return out


def multiview(frames: list[np.ndarray], model: str = "base", process_res: int = 504, conf_percentile: float = 40.0,
              max_points: int = 400_000) -> dict:
    """Any-view geometry: depth, conf, w2c extrinsics (3x4), intrinsics per view, and a fused world point cloud."""
    import torch
    m = load(model)
    t0 = time.time()
    with quiet(), torch.no_grad():
        pred = m.inference(frames, process_res=process_res)
    depth = pred.depth.astype(np.float32)
    conf = None if pred.conf is None else pred.conf.astype(np.float32)
    E = np.asarray(pred.extrinsics, np.float32)
    K = np.asarray(pred.intrinsics, np.float32)
    N, H, W = depth.shape
    pts, cols = [], []
    for i in range(N):
        z = depth[i]; ys, xs = np.mgrid[0:H, 0:W]
        x = (xs - K[i][0, 2]) / K[i][0, 0] * z; y = (ys - K[i][1, 2]) / K[i][1, 1] * z
        P = np.stack([x, y, z], -1).reshape(-1, 3)
        R = E[i][:3, :3]; t = E[i][:3, 3]
        Pw = (P - t) @ R
        im = np.asarray(Image.fromarray(frames[i]).convert("RGB").resize((W, H))).reshape(-1, 3)
        keep = np.ones(len(P), bool) if conf is None else conf[i].reshape(-1) >= np.percentile(conf[i], conf_percentile)
        pts.append(Pw[keep]); cols.append(im[keep])
    P = np.concatenate(pts); C = np.concatenate(cols)
    if len(P) > max_points:
        sel = np.random.default_rng(0).choice(len(P), max_points, replace=False)
        P, C = P[sel], C[sel]
    centers = np.array([-(E[i][:3, :3].T @ E[i][:3, 3]) for i in range(N)])
    return {"depth": depth, "conf": conf, "extrinsics": E, "intrinsics": K, "points": P, "colors": C,
            "camera_centers": centers, "secs": time.time() - t0, "model": model}


def save_geometry(g: dict, key: str) -> Path:
    p = CACHE / f"geo-{key}.npz"
    np.savez_compressed(p, depth=g["depth"], conf=g["conf"] if g["conf"] is not None else np.zeros(0),
                        extrinsics=g["extrinsics"], intrinsics=g["intrinsics"], points=g["points"], colors=g["colors"],
                        camera_centers=g["camera_centers"])
    return p


def load_geometry(p: Path) -> dict:
    d = np.load(p)
    return {k: d[k] for k in d.files}


def _rot(P: np.ndarray, axis: str, deg: float) -> np.ndarray:
    r = np.deg2rad(deg); c, s = np.cos(r), np.sin(r)
    if axis == "y":
        R = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
    elif axis == "x":
        R = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
    else:
        R = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
    return P @ R.T


def render_points(P: np.ndarray, C: np.ndarray, camera: str = "iso", size: int = 900, cams: np.ndarray | None = None,
                  point_px: int = 1) -> Image.Image:
    """Orthographic projection of the point cloud. Camera frame: x right, y down, z forward (into the scene).
    front = source view, top = looking down (x vs z), side = from the right (z vs y), iso = rotated 35/25 deg."""
    P = P.astype(np.float32)
    if camera == "top":
        Q = P[:, [0, 2]]; Q[:, 1] = -Q[:, 1]          # z up the image
        cq = None if cams is None else np.stack([cams[:, 0], -cams[:, 2]], 1)
    elif camera == "side":
        Q = P[:, [2, 1]]
        cq = None if cams is None else np.stack([cams[:, 2], cams[:, 1]], 1)
    elif camera == "front":
        Q = P[:, [0, 1]]
        cq = None if cams is None else cams[:, [0, 1]]
    else:
        Pi = _rot(_rot(P, "y", 35), "x", -25)
        Q = Pi[:, [0, 1]]
        cq = None if cams is None else _rot(_rot(cams, "y", 35), "x", -25)[:, [0, 1]]
    lo = np.percentile(Q, 0.5, axis=0); hi = np.percentile(Q, 99.5, axis=0)
    span = float(max(hi - lo)) + 1e-6
    pad = 0.05 * span
    def to_px(q):
        u = (q[:, 0] - lo[0] + pad) / (span + 2 * pad); v = (q[:, 1] - lo[1] + pad) / (span + 2 * pad)
        return (np.clip(u, 0, 1) * (size - 1)).astype(int), (np.clip(v, 0, 1) * (size - 1)).astype(int)
    img = np.full((size, size, 3), 22, np.uint8)
    # draw far points first so near ones win: sort by the axis pointing at the viewer
    order = np.argsort(-P[:, 2]) if camera in ("front", "iso") else np.arange(len(P))
    ix, iy = to_px(Q[order])
    for dx in range(point_px):
        for dy in range(point_px):
            img[np.clip(iy + dy, 0, size - 1), np.clip(ix + dx, 0, size - 1)] = C[order]
    im = Image.fromarray(img)
    if cq is not None:
        from PIL import ImageDraw
        d = ImageDraw.Draw(im)
        cx, cy = to_px(cq)
        for i, (x, y) in enumerate(zip(cx, cy)):
            d.ellipse([x - 5, y - 5, x + 5, y + 5], outline=(255, 255, 0), width=2)
            d.text((x + 7, y - 7), f"cam{i + 1}", fill=(255, 255, 0))
    return im
