"""When one thing lets go of another.

A handoff, a pick-up, a put-down: all of them turn on the instant two things stop touching. The
tracker cannot see it. Its masks are stored at 160 px wide against a 960 px frame, so the fingers
holding a bottle are a handful of cells and the moment the grip opens is below its resolution. Its
boxes are worse — they are dominated by the forearm, which keeps moving through the release.

So this re-segments a *window* around the contact at native resolution and asks one question of it:
when does background first appear between the two? Measured against a comp whose release time is
exact by construction, that is right to a single frame, where a peak-of-frame-change estimator on
the same comp is 12 frames out. Frame change peaks where motion is fastest, which is after the hand
has gone; the release is the onset of a divergence, not its maximum.

The producers this replaces, on the handoff clip and its 8.42 s reference:

    box separation (glove x0 - bottle x1)   largest jump 8.52-8.62    0.22-0.32 s out
    mask contact area at 10 fps             422 -> 380 px, no break   no event at all
    ROI frame change at 25 fps              peak 8.48                 0.18 s out
    this, at 84x160 masks                   8.56                      3.5 frames out
    this, at native resolution              8.50                      2.0 frames out

The window matters as much as the resolution. The first attempt handed SAM box prompts that fell
outside the crop — a glove box reaching x=1395 in a 1110-wide window, and negative y on both — and
got incoherent masks back. `window()` exists so that cannot happen again.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import numpy as np

SAMPLE_FPS = 25.0        # native rate: the whole point is not to sample below the event
MARGIN = 0.06            # of frame size, added around the union of the two boxes
LONG_EDGE = 1120         # the window is scaled to about this before segmentation
GAP_STEPS = (1, 2, 4)    # source-pixel steps the onset is read at
MAX_SPREAD = 2           # frames those steps may disagree by before the separation is too gradual
RIGHT_PART = 0.55        # columns of A's span, from this fraction rightward, are the contact zone
MIN_PIX = 200            # a mask smaller than this is not the thing


def window(boxes: list[list[float]], w: int, h: int, margin: float = MARGIN) -> tuple[int, int, int, int]:
    """A crop holding every box, with margin, clamped to the frame and never empty."""
    if not boxes:
        return 0, 0, w, h
    a = np.asarray(boxes, float)
    x0, y0 = a[:, 0].min(), a[:, 1].min()
    x1, y1 = a[:, 2].max(), a[:, 3].max()
    mx, my = margin * w, margin * h
    X0 = int(max(0, np.floor(x0 * w - mx)))
    Y0 = int(max(0, np.floor(y0 * h - my)))
    X1 = int(min(w, np.ceil(x1 * w + mx)))
    Y1 = int(min(h, np.ceil(y1 * h + my)))
    if X1 <= X0:
        X0, X1 = 0, w
    if Y1 <= Y0:
        Y0, Y1 = 0, h
    return X0, Y0, X1, Y1


def clamp(box: list[float], w: int, h: int) -> list[float] | None:
    """A prompt outside the image is not a prompt. Returns None if nothing survives."""
    x0, y0, x1, y1 = (float(v) for v in box)
    x0, x1 = min(max(x0, 0), w), min(max(x1, 0), w)
    y0, y1 = min(max(y0, 0), h), min(max(y1, 0), h)
    return None if x1 - x0 < 2 or y1 - y0 < 2 else [x0, y0, x1, y1]


def gap(a: np.ndarray, b: np.ndarray, right_part: float = RIGHT_PART) -> float | None:
    """Smallest vertical clearance under A's lower edge to B, over A's contact columns."""
    if a.sum() < MIN_PIX or b.sum() < MIN_PIX:
        return None
    cols = np.where(a.any(0))[0]
    if len(cols) == 0:
        return None
    out = []
    for c in cols[int(len(cols) * right_part):]:
        ar = np.where(a[:, c])[0]
        br = np.where(b[:, c])[0]
        below = br[br > ar.max()]
        if len(below):
            out.append(float(below.min() - ar.max()))
    return min(out) if out else None


def onset(gaps: list[float | None], scale: float = 1.0,
          steps: tuple[int, ...] = GAP_STEPS) -> tuple[int, float] | None:
    """First index where the clearance steps above its resting value, and how sure that is.

    The break is where the gap *starts* opening, so the smallest threshold is the one nearest it
    and the larger ones are there to confirm the growth is real rather than a flicker. They cannot
    be required to agree exactly: on a separation opening at a few pixels a frame, a 1-px and a
    4-px threshold are legitimately a frame or two apart. They are required to agree *closely*.

    X1: the confidence is that agreement, not a score anything reported about itself. A spread
    wider than MAX_SPREAD frames means the two are drifting apart rather than letting go, and the
    honest answer is then None.
    """
    known = [(i, g) for i, g in enumerate(gaps) if g is not None]
    if len(known) < 4:
        return None
    base = float(np.median([g for _, g in known[:max(4, len(known) // 4)]]))
    hits = []
    for st in steps:
        over = [i for i, g in known if g > base + st * scale]
        if not over:
            return None
        hits.append(over[0])
    spread = max(hits) - min(hits)
    if spread > MAX_SPREAD:
        return None
    return min(hits), round(1.0 - spread / (MAX_SPREAD + 1), 2)


def _masks(video: Path, box: list[float], imgsz: int = 1024, tracker: str | None = None) -> dict[int, np.ndarray]:
    """The tracker through the window, at the window's own resolution (segtrack: EdgeTAM by default)."""
    from . import segtrack
    return {fi: m for fi, m in segtrack.masks(video, box, tracker=tracker) if m.any()}


def break_time(clip: Path, box_a: list[float], box_b: list[float], t0: float, t1: float,
               w: int, h: int, fps: float = SAMPLE_FPS, tracker: str | None = None) -> tuple[float, float] | None:
    """When A stops resting on B, in seconds, with a derived confidence. None if unreadable.

    The boxes are the two entities at `t0`, while they are still touching, and the window is sized
    from them. Widening it to cover where they travel afterwards was tried and is worse: SAM runs
    at a fixed `imgsz`, so a larger crop spends fewer of those pixels on the junction, and the
    measured break moved from 2 frames out to 4. Once the gap has opened, an entity wandering out
    of the window costs nothing — `gap` returns None for those frames and the onset has already
    been read.
    """
    X0, Y0, X1, Y1 = window([box_a, box_b], w, h)
    z = max(1, int(round(LONG_EDGE / max(X1 - X0, Y1 - Y0))))
    # h264 will not take an odd dimension, and a window that happens to scale to one aborts the
    # encode — which arrives here as an unreadable segment and so as silence, hiding a plain bug
    # behind a legitimate-looking "cannot tell". Round both edges down to even.
    W, H = ((X1 - X0) * z) & ~1, ((Y1 - Y0) * z) & ~1
    to_win = lambda bx: clamp([(bx[0] * w - X0) * z, (bx[1] * h - Y0) * z,
                               (bx[2] * w - X0) * z, (bx[3] * h - Y0) * z], W, H)
    pa, pb = to_win(box_a), to_win(box_b)
    if pa is None or pb is None:
        return None
    with tempfile.TemporaryDirectory() as d:
        seg = Path(d) / "win.mp4"
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0}", "-to", f"{t1}",
                            "-i", str(clip), "-vf",
                            f"crop={X1 - X0}:{Y1 - Y0}:{X0}:{Y0},scale={W}:{H}:flags=lanczos",
                            "-r", f"{fps}", "-an", str(seg)], capture_output=True, timeout=600)
        if r.returncode != 0 or not seg.exists():
            return None
        A, B = _masks(seg, pa, tracker=tracker), _masks(seg, pb, tracker=tracker)
    idx = sorted(set(A) & set(B))
    if not idx:
        return None
    gaps = [gap(A[i], B[i]) for i in idx]
    got = onset(gaps, scale=float(z))
    if got is None:
        return None
    i, conf = got
    return t0 + idx[i] / fps, conf


WIN, STEP = 1.4, 0.9     # the sweep's window and hop, in seconds
# The tracker (segtrack: EdgeTAM) reads this release at 8.58-8.60 whichever window it is given; SAM 2
# read 8.52-8.62 on the same windows. Its earlier 8.50 was a lucky window, so a SAM 2 re-measure
# around the hit was tried and dropped: 85 s for no reliable gain (vision/cache/bench/release_windows.py).


def sweep(clip: Path, obs_a: dict[float, list[float]], obs_b: dict[float, list[float]],
          t0: float, t1: float, w: int, h: int,
          win: float = WIN, step: float = STEP) -> tuple[float, float] | None:
    """Walk a pair's contact interval in tight windows and report the first release in it.

    There is no cheap bracket for this. Four were measured and all four failed: a frame-change peak
    in a fixed ROI lands 0.48 s out on a comp and picks the wrong second entirely over a long
    interval; box separation and centroid distance both rise smoothly through the release, because
    the two were already drifting apart before the grip opened; mask contact area at the tracker's
    10 fps has no event in it at all. The break is only legible at native resolution in a tight
    window, which is `break_time`, so the interval is tiled with those rather than guessed at.

    The tracks supply a box per window, which is what keeps each crop small while still following
    the pair across the interval — a single window sized to hold the whole journey spends SAM's
    fixed `imgsz` on everything except the junction, and the measured error doubles.
    """
    if not obs_a or not obs_b:
        return None
    ta, tb = np.array(sorted(obs_a)), np.array(sorted(obs_b))
    at = t0
    while at + win <= t1 + 1e-9:
        ka = float(ta[int(np.argmin(np.abs(ta - at)))])
        kb = float(tb[int(np.argmin(np.abs(tb - at)))])
        got = break_time(clip, obs_a[ka], obs_b[kb], at, at + win, w, h)
        if got is not None:
            return got
        at += step
    return None

