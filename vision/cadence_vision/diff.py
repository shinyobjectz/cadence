"""Two frames compared: pixel heatmap, depth-relief difference, or flow arrows."""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw


def _same_size(a: np.ndarray, b: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if a.shape == b.shape:
        return a, b
    h, w = a.shape[:2]
    b = np.asarray(Image.fromarray(b).resize((w, h), Image.LANCZOS))
    return a, b


def pixel(a: np.ndarray, b: np.ndarray, thresh: int = 24) -> tuple[Image.Image, dict]:
    a, b = _same_size(a, b)
    d = np.abs(a.astype(int) - b.astype(int)).max(-1)
    mask = d > thresh
    heat = np.zeros_like(a); heat[..., 0] = np.clip(d * 2, 0, 255)
    base = (a * 0.35).astype(np.uint8)
    out = np.where(mask[..., None], np.maximum(base, heat), base).astype(np.uint8)
    ys, xs = np.where(mask)
    stats = {"changed_fraction": round(float(mask.mean()), 4), "mean_abs_diff": round(float(d.mean()), 2)}
    if len(xs):
        H, W = mask.shape
        stats["changed_bbox_norm"] = [round(xs.min() / W, 3), round(ys.min() / H, 3), round(xs.max() / W, 3), round(ys.max() / H, 3)]
    return Image.fromarray(out), stats


def depth(ra: dict, rb: dict) -> tuple[Image.Image, dict]:
    from .depth import to_relief, _colormap
    a, b = to_relief(ra["depth"]), to_relief(rb["depth"])
    if a.shape != b.shape:
        b = np.asarray(Image.fromarray((b * 255).astype(np.uint8)).resize((a.shape[1], a.shape[0]))) / 255.0
    d = b - a
    img = _colormap(np.clip(d * 0.5 + 0.5, 0, 1), "coolwarm")
    return Image.fromarray(img), {"mean_relief_change": round(float(d.mean()), 4), "abs_change_p95": round(float(np.percentile(np.abs(d), 95)), 4),
                                  "reading": "red = came nearer, blue = receded"}


def flow(a: np.ndarray, b: np.ndarray, step: int = 24) -> tuple[Image.Image, dict]:
    import cv2
    a, b = _same_size(a, b)
    g0 = cv2.cvtColor(a, cv2.COLOR_RGB2GRAY); g1 = cv2.cvtColor(b, cv2.COLOR_RGB2GRAY)
    f = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 3, 21, 3, 5, 1.2, 0)
    im = Image.fromarray((a * 0.6).astype(np.uint8)); d = ImageDraw.Draw(im)
    H, W = g0.shape
    for y in range(step // 2, H, step):
        for x in range(step // 2, W, step):
            dx, dy = f[y, x]
            if np.hypot(dx, dy) < 0.8:
                continue
            d.line([x, y, x + dx * 2, y + dy * 2], fill=(90, 255, 120), width=2)
            d.ellipse([x - 1.5, y - 1.5, x + 1.5, y + 1.5], fill=(255, 255, 255))
    mag = np.hypot(f[..., 0], f[..., 1])
    return im, {"mean_flow_px": round(float(mag.mean()), 2), "p95_flow_px": round(float(np.percentile(mag, 95)), 2),
                "dominant_dx": round(float(f[..., 0].mean()), 2), "dominant_dy": round(float(f[..., 1].mean()), 2)}
