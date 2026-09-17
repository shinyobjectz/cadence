"""Pick n times from a source under a fixed frame budget.

uniform  evenly spaced
scene    frames right after the strongest histogram cuts, then uniform fill
motion   equal slices of cumulative motion energy (busy passages get more frames)
diverse  farthest-point sampling on tiny thumbnails (coverage of distinct looks)
"""

from __future__ import annotations

import numpy as np

from .sources import Source, scan


def _gray(frames: np.ndarray) -> np.ndarray:
    return frames.astype(np.float32).mean(-1)


def _hist(frames: np.ndarray, bins: int = 8) -> np.ndarray:
    n = len(frames)
    q = (frames // (256 // bins)).astype(np.int32)
    idx = q[..., 0] * bins * bins + q[..., 1] * bins + q[..., 2]
    out = np.zeros((n, bins ** 3), np.float32)
    for i in range(n):
        out[i] = np.bincount(idx[i].ravel(), minlength=bins ** 3)
    return out / out.sum(1, keepdims=True)


def cut_scores(frames: np.ndarray) -> np.ndarray:
    """Per-frame histogram distance to the previous frame (0 for the first)."""
    h = _hist(frames)
    d = np.zeros(len(frames), np.float32)
    d[1:] = 0.5 * np.abs(h[1:] - h[:-1]).sum(1)
    return d


def motion_energy(frames: np.ndarray) -> np.ndarray:
    g = _gray(frames)
    e = np.zeros(len(frames), np.float32)
    e[1:] = np.abs(g[1:] - g[:-1]).mean((1, 2))
    return e


def _uniform(duration: float, n: int) -> list[float]:
    if n <= 1:
        return [duration / 2]
    return [duration * (i + 0.5) / n for i in range(n)]


def _fill_uniform(chosen: list[float], duration: float, n: int) -> list[float]:
    """Add evenly spaced times where the chosen set has gaps until n reached."""
    chosen = sorted(set(round(c, 3) for c in chosen))
    while len(chosen) < n:
        pts = [0.0] + chosen + [duration]
        gaps = [(pts[i + 1] - pts[i], i) for i in range(len(pts) - 1)]
        g, i = max(gaps)
        if g <= 0:
            break
        chosen.append(round(pts[i] + g / 2, 3))
        chosen.sort()
    return chosen[:n]


def pick(src: Source, n: int, strategy: str = "diverse", window: tuple[float, float] | None = None) -> dict:
    n = max(1, int(n))
    if src.kind == "image":
        return {"times": [0.0], "strategy": "image", "scores": {}}
    t0, t1 = window or (0.0, src.duration)
    dur = max(1e-3, t1 - t0)
    if strategy == "uniform" or src.duration <= 0:
        return {"times": [round(t0 + x, 3) for x in _uniform(dur, n)], "strategy": "uniform", "scores": {}}

    times, frames = scan(src)
    m = (times >= t0) & (times <= t1)
    times, frames = times[m], frames[m]
    if len(frames) == 0:
        return {"times": [round(t0 + x, 3) for x in _uniform(dur, n)], "strategy": "uniform", "scores": {}}

    if strategy == "scene":
        cs = cut_scores(frames)
        thr = max(0.25, float(cs.mean() + 2.5 * cs.std()))
        cuts = [int(i) for i in np.where(cs > thr)[0]]
        # merge cuts closer than 0.5 s
        merged = []
        for i in cuts:
            if not merged or times[i] - times[merged[-1]] > 0.5:
                merged.append(i)
        chosen = [float(times[0])] + [float(times[i]) for i in merged]
        chosen = chosen[:n]
        out = _fill_uniform([c - t0 for c in chosen], dur, n)
        return {"times": [round(t0 + c, 3) for c in out], "strategy": "scene",
                "scores": {"cuts_at": [round(float(times[i]), 2) for i in merged], "threshold": round(thr, 3)}}

    if strategy == "motion":
        e = motion_energy(frames)
        e = e + 1e-3 * e.max() + 1e-6          # keep a floor so static stretches still get frames
        c = np.cumsum(e); c /= c[-1]
        targets = (np.arange(n) + 0.5) / n
        idx = np.searchsorted(c, targets)
        idx = np.clip(idx, 0, len(times) - 1)
        chosen = sorted(set(float(times[i]) for i in idx))
        out = _fill_uniform([c_ - t0 for c_ in chosen], dur, n)
        return {"times": [round(t0 + c_, 3) for c_ in out], "strategy": "motion",
                "scores": {"energy_mean": round(float(e.mean()), 3), "energy_max": round(float(e.max()), 3)}}

    # diverse: farthest-point sampling on 16x9 thumbnails (colour + luminance)
    small = np.stack([np.asarray(_thumb(f)) for f in frames]).reshape(len(frames), -1).astype(np.float32) / 255
    chosen_idx = [0]
    d = np.linalg.norm(small - small[0], axis=1)
    while len(chosen_idx) < min(n, len(frames)):
        j = int(d.argmax())
        if d[j] <= 1e-6:
            break
        chosen_idx.append(j)
        d = np.minimum(d, np.linalg.norm(small - small[j], axis=1))
    chosen = sorted(float(times[i]) for i in chosen_idx)
    out = _fill_uniform([c - t0 for c in chosen], dur, n)
    return {"times": [round(t0 + c, 3) for c in out], "strategy": "diverse", "scores": {"picked_by_distance": len(chosen_idx)}}


def _thumb(f: np.ndarray):
    from PIL import Image
    return Image.fromarray(f).resize((16, 9), Image.BOX)
