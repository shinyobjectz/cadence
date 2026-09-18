"""G11. Where something changes without the shot changing.

A cut is easy to find and is not where most edits go. Inside one continuous shot there are moments
where what is happening turns over — a hand arrives, a subject settles, a gesture finishes — and
those are the frames an editor reaches for. Generic event boundary detection is the task; this is
the cheap, honest version of it:

    happens(action_boundary(s1), 4.267).
    src(happens(action_boundary(s1), 4.267), clip_novelty, 0.94).

The method is Foote's novelty score over a self-similarity matrix, with CLIP embeddings instead of
audio features. Embed a low-resolution strip of the clip, take the cosine self-similarity, and
correlate a checkerboard kernel along its diagonal: the score peaks where the frames before a moment
resemble each other, the frames after resemble each other, and the two halves do not resemble each
other. That is exactly the shape of a boundary and it needs no training.

**Confidence is the peak's percentile within its own shot.** An absolute novelty threshold is
meaningless across material — a locked-off interview has a tenth the novelty of a handheld street
shot, and the same number would flood one log and empty the other. What is comparable is how far a
moment stands out from the rest of *this* shot.

Boundaries within half a second of a known cut are dropped. The cut is already in the log and
saying it twice under a second name would have a reader cutting at the same frame for two reasons.

This is not a V-JEPA-class predictive model, which is what the benchmark leaders use; it is what
runs in a second on the machine we have. The interface is the fact, so a better producer replaces
it without anything downstream noticing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import embed
from .facts import T

SAMPLE_FPS = 4.0
KERNEL = 6               # half-width, in samples: 1.5 s either side at 4 fps
MIN_CONF = 0.9           # a boundary must stand out from ~9 in 10 moments of its shot
MIN_GAP = 0.6            # seconds between reported boundaries
NEAR_CUT = 0.5
MIN_SAMPLES = 4 * KERNEL


def checkerboard(w: int) -> np.ndarray:
    """A Gaussian-tapered checkerboard: positive on the two self-similar quadrants, negative on the
    cross ones, and summing to zero so a uniform stretch of clip scores zero rather than a baseline
    that depends on how similar the material happens to be to itself."""
    idx = np.arange(-w, w + 1)
    g = np.exp(-0.5 * (idx / (w / 2.0)) ** 2)
    sign = np.sign(np.outer(np.sign(idx), np.sign(idx)))     # the centre row and column are zeroed
    return np.outer(g, g) * sign


def novelty(vecs: np.ndarray, w: int = KERNEL) -> np.ndarray:
    """Foote novelty along the diagonal of the self-similarity matrix, one value per sample."""
    n = len(vecs)
    out = np.zeros(n)
    if n < 2 * w + 1:
        return out
    S = vecs @ vecs.T
    K = checkerboard(w)
    for i in range(w, n - w):
        out[i] = float((S[i - w:i + w + 1, i - w:i + w + 1] * K).sum())
    out[:w] = out[w]
    out[n - w:] = out[n - w - 1]
    return out


def peaks(ts: np.ndarray, nov: np.ndarray, min_conf: float = MIN_CONF,
          min_gap: float = MIN_GAP, w: int = KERNEL) -> list[tuple[float, float]]:
    """[(t, confidence)] for local maxima that stand out from the ordinary moments of the shot.

    The confidence is the share of *baseline* samples the peak beats, where the baseline excludes
    the neighbourhood of every candidate. Scoring against all samples looks equivalent and is not:
    with k equally strong boundaries no peak can beat more than (n-k)/n of the sequence, so a fixed
    threshold silently admits a clip with one boundary and rejects the same clip with three. What
    the question actually means is how far a moment stands out from the shot's quiet stretches."""
    n = len(nov)
    if n < 3:
        return []
    inner = np.arange(w, max(w + 1, n - w))
    if len(inner) < 3 or float(np.ptp(nov[inner])) < 1e-9:
        return []
    # A change between two samples makes both of them score alike, so the peak is a plateau. Take
    # its right edge: a cut goes on the first frame of the new thing, not the last of the old one,
    # which is also where ffmpeg reports a shot cut.
    cand = [i for i in range(1, n - 1) if nov[i] >= nov[i - 1] and nov[i] > nov[i + 1]]
    if not cand:
        return []
    keep = np.ones(n, bool)
    for i in cand:
        keep[max(0, i - w):i + w + 1] = False
    base = nov[inner[keep[inner]]]
    if len(base) < 5:
        base = nov[inner]
    out = [(float(ts[i]), round(float((base < nov[i]).mean()), 2)) for i in cand]
    out = [(t, c) for t, c in out if c >= min_conf]
    out.sort(key=lambda p: -p[1])
    kept: list[tuple[float, float]] = []
    for t, c in out:
        if all(abs(t - u) >= min_gap for u, _ in kept):
            kept.append((t, c))
    return sorted(kept)


def emit(clip, L, shots: list[tuple[str, float, float]] | None = None,
         cuts: list[float] | None = None, min_conf: float = MIN_CONF, log=lambda s: None) -> int:
    """Append `action_boundary` facts. Returns how many were emitted."""
    from .sources import open_source, scan
    if not embed.available():
        log("open_clip not installed; no action boundaries")
        return 0
    src = open_source(Path(clip))
    ts, frames = scan(src, SAMPLE_FPS)
    if len(frames) < MIN_SAMPLES:
        log(f"clip too short for a {KERNEL}-sample kernel; no action boundaries")
        return 0
    vecs = embed.strip_embeddings(f"{src.media}-{SAMPLE_FPS}", frames)
    shots = shots or [("s1", 0.0, src.duration)]
    cuts = cuts or []

    found = []
    for name, a, b in shots:
        m = (ts >= a) & (ts < b)
        if m.sum() < MIN_SAMPLES:
            continue
        # Scored per shot, because novelty is only comparable against the material it came from.
        for t, conf in peaks(ts[m], novelty(vecs[m]), min_conf):
            if any(abs(t - c) < NEAR_CUT for c in cuts):
                continue                     # already in the log as a cut
            found.append((t, name, conf))
    if not found:
        return 0
    L.c("producer: clip self-similarity novelty, confidence = the peak's percentile in its shot")
    for t, name, conf in sorted(found):
        L.fact(f"happens(action_boundary({name}), {T(t)})", "clip_novelty", conf)
    return len(found)


if __name__ == "__main__":
    from .facts import Log
    L = Log()
    emit(sys.argv[1], L, log=lambda s: print(f"% {s}", file=sys.stderr))
    print(L.text(), end="")
