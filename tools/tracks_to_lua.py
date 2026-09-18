#!/usr/bin/env python3
"""Turn cached SAM2 tracks into baked sample arrays a Cadence comp can follow.

The tracker emits observations at 10 fps with gaps wherever the mask was lost; a bake wants
one evenly-spaced array per property. So every track is resampled onto a uniform grid over the
whole clip, held at the nearest observation inside its range and marked invisible outside it.
The boxes drawn from this are the tracker's own output, not a re-detection: what the overlay
shows is exactly what the facts were derived from.
"""
from __future__ import annotations

import pickle
from pathlib import Path


def load(pkl: Path) -> list[dict]:
    return pickle.loads(Path(pkl).read_bytes())


def resample(track: dict, dur: float, fps: float, W: int, H: int) -> dict:
    """{x0,y0,x1,y1,on} -> arrays in pixels on a uniform grid of n samples."""
    obs = sorted(track["obs"], key=lambda o: o[0])
    ts = [o[0] for o in obs]
    n = int(round(dur * fps)) + 1
    out = {k: [] for k in ("x0", "y0", "x1", "y1", "cx", "cy", "on")}
    j = 0
    for i in range(n):
        t = i / fps
        while j + 1 < len(obs) and ts[j + 1] <= t:
            j += 1
        inside = ts[0] - 0.12 <= t <= ts[-1] + 0.12
        # hold the nearest observation; linear between neighbours reads smoother than a step
        if j + 1 < len(obs) and ts[j + 1] > ts[j]:
            f = max(0.0, min(1.0, (t - ts[j]) / (ts[j + 1] - ts[j])))
            a, b = obs[j][1], obs[j + 1][1]
            box = [a[k] + (b[k] - a[k]) * f for k in range(4)]
        else:
            box = list(obs[j][1])
        out["x0"].append(box[0] * W)
        out["y0"].append(box[1] * H)
        out["x1"].append(box[2] * W)
        out["y1"].append(box[3] * H)
        out["cx"].append((box[0] + box[2]) / 2 * W)
        out["cy"].append(box[1] * H)
        out["on"].append(1.0 if inside else 0.0)
    return out


def arr(vals: list[float], places: int = 1) -> str:
    return "{" + ",".join(f"{v:.{places}f}" for v in vals) + "}"
