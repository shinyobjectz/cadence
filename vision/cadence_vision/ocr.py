"""G6. On-screen text in footage, as the same fluent the comp lifter emits for a text node.

    holds(text(e, "SALE ENDS FRIDAY"), 2.400, 6.100).
    happens(text_change(e), 6.100).

A caption is not a property of a frame, it is something that holds over an interval and then
changes, and the grammar already says so for comps. Reading footage the same way means a title card
burned into a stock clip and a title Cadence drew are the same kind of thing to whatever reads the
log — which is the point of one grammar for both sides.

X1, on confidence. Every OCR engine returns a score, and every one of them is self-reported and
badly calibrated; a confident misread of a blurred word scores as high as a clean one. The number
emitted here is measured instead: a string read identically across k of the n samples a region is
on screen has confidence k/n. A word the engine is sure of but re-reads differently every frame
scores low, which is the correct answer about a word that cannot be read.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np

from .facts import T, agreed_runs, band, third
from .profile import timed

SAMPLE_FPS = 2.0
MIN_CONF = 0.5           # the engine's own score, used only to discard garbage before measuring
MIN_STABLE = 0.6         # a string must survive this fraction of a region's samples to be claimed
MIN_SAMPLES = 2
IOU_MATCH = 0.35
_ENGINE = None


@timed("rapidocr.load")
def engine():
    global _ENGINE
    if _ENGINE is None:
        from rapidocr_onnxruntime import RapidOCR
        _ENGINE = RapidOCR()
    return _ENGINE


@timed("rapidocr.read")
def read(frame: np.ndarray) -> list[dict]:
    """[{box: (x0,y0,x1,y1) normalized, text, conf}] for one RGB frame."""
    res, _ = engine()(frame)
    H, W = frame.shape[:2]
    out = []
    for poly, txt, conf in res or []:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        t = " ".join(str(txt).split())
        if not t or float(conf) < MIN_CONF:
            continue
        out.append({"box": (min(xs) / W, min(ys) / H, max(xs) / W, max(ys) / H),
                    "text": t, "conf": float(conf)})
    return out


def _iou(a, b) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def regions(samples: list[tuple[float, list[dict]]], iou: float = IOU_MATCH) -> list[dict]:
    """Group per-frame detections into regions that persist, by box overlap between samples.

    Grouping on position rather than on the string is deliberate: a region whose text *changes* —
    a counter, a lower third swapping names — is one region with two intervals, not two regions.
    That distinction is what `text_change` means."""
    live: list[dict] = []
    done: list[dict] = []
    for t, dets in samples:
        taken = set()
        for r in live:
            best, bi = 0.0, None
            for i, d in enumerate(dets):
                if i in taken:
                    continue
                v = _iou(r["obs"][-1][1], d["box"])
                if v > best:
                    best, bi = v, i
            if bi is not None and best >= iou:
                taken.add(bi)
                r["obs"].append((t, dets[bi]["box"], dets[bi]["text"], dets[bi]["conf"]))
        for i, d in enumerate(dets):
            if i not in taken:
                live.append({"obs": [(t, d["box"], d["text"], d["conf"])]})
        gap = 1.5 / SAMPLE_FPS
        keep = []
        for r in live:
            (done if t - r["obs"][-1][0] > gap else keep).append(r)
        live = keep
    return done + live


def _clean(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def stable_runs(obs: list[tuple], min_stable: float = MIN_STABLE) -> list[tuple[str, float, float, float]]:
    """(text, t0, t1, agreement) for each string the region settles on.

    A run breaks when the reading changes; how much of the run read the same way is the confidence,
    which is a measurement of the text's legibility rather than a claim about it. `facts.agreed_runs`
    does the counting, told that two readings differing only in quality are the same string."""
    seq = [(t, _clean(txt)) for t, _b, txt, _c in obs]
    return [(str(v), a, b, agree) for v, a, b, agree in
            agreed_runs(seq, same=_same, min_samples=MIN_SAMPLES) if agree >= min_stable]


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _same(a: str, b: str) -> bool:
    """Two readings are the same text when they differ only in how well they were read."""
    if a == b:
        return True
    ca, cb = _key(a), _key(b)
    if not ca or not cb:
        return False
    if ca == cb:
        return True
    n = max(len(ca), len(cb))
    return n >= 6 and _edit(ca, cb) <= max(1, n // 8)


def _edit(a: str, b: str) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def read_box(src, t: float, box, pad: float = 0.02) -> str:
    """The text inside one region at one instant. Cropping keeps a neighbouring caption from
    being read instead, and makes the refinement below cheap enough to run per boundary."""
    from .sources import frame_at
    fr = frame_at(src, float(t), 1280)
    H, W = fr.shape[:2]
    x0 = max(0, int((box[0] - pad) * W)); x1 = min(W, int((box[2] + pad) * W))
    y0 = max(0, int((box[1] - pad) * H)); y1 = min(H, int((box[3] + pad) * H))
    if x1 - x0 < 8 or y1 - y0 < 8:
        return ""
    got = read(fr[y0:y1, x0:x1])
    return _clean(" ".join(d["text"] for d in sorted(got, key=lambda d: d["box"][0])))


def refine(src, box, before: str | None, after: str | None, lo: float, hi: float,
           steps: int = 4) -> float:
    """Bisect for the instant the text changed. Either side may be None, meaning "nothing here",
    which is how an appearance and a disappearance are refined as well as a swap.

    Sampling at 2 fps finds a caption change up to half a second after it happened, and half a
    second is fifteen frames — useless for cutting, and the same mistake that makes ASR timestamps
    unusable. Each halving costs one OCR call on a small crop, so four of them bring a 0.5 s window
    to about 30 ms."""
    for _ in range(steps):
        mid = (lo + hi) / 2
        got = read_box(src, mid, box)
        if _is(got, before):
            lo = mid
        elif _is(got, after):
            hi = mid
        else:
            # A partial read of a line still says which line it is. Only a reading that is no
            # nearer one side than the other — a wipe, a cross-fade — defeats the bisection, and
            # then the coarse sample time stands rather than a finer time that is made up.
            db, da = _near(got, before), _near(got, after)
            if db == da:
                return round(hi, 3)
            lo, hi = (mid, hi) if db < da else (lo, mid)
    return round((lo + hi) / 2, 3)


def _is(got: str, want: str | None) -> bool:
    return (not got) if want is None else _same(got, want)


def _near(got: str, want: str | None) -> int:
    return len(_key(got)) if want is None else _edit(_key(got), _key(want))


def emit(clip, L, duration: float | None = None, fps_s: float = SAMPLE_FPS,
         start: int = 1, log=lambda s: None) -> int:
    """Append text facts for `clip`. Returns how many text entities were emitted."""
    from .sources import frame_at, open_source
    src = open_source(Path(clip))
    dur = duration or src.duration
    samples = []
    for t in np.arange(0.2, dur, 1.0 / fps_s):
        try:
            samples.append((round(float(t), 3), read(frame_at(src, float(t), 1280))))
        except Exception as e:                       # a bad frame is not a reason to lose the rest
            log(f"ocr failed at {t:.2f}s ({type(e).__name__})")
    regs = [r for r in regions(samples) if stable_runs(r["obs"])]
    if not regs:
        return 0
    L.c("producer: rapidocr, confidence = agreement across the samples a string is on screen")
    n = 0
    for r in sorted(regs, key=lambda r: r["obs"][0][0]):
        eid = f"x{start + n}"
        n += 1
        obs = r["obs"]
        runs = stable_runs(obs)
        best = max(a for *_, a in runs)
        bx = np.array([o[1] for o in obs], float)
        # The union, not the mean: a caption box grows with the line, and a crop sized to the
        # average truncates the longest line into something that matches neither side.
        union = (float(bx[:, 0].min()), float(bx[:, 1].min()),
                 float(bx[:, 2].max()), float(bx[:, 3].max()))
        cx, cy = float(bx[:, [0, 2]].mean()), float(bx[:, [1, 3]].mean())
        step = 1.0 / fps_s
        first = runs[0][1]
        edges = [refine(src, union, None, runs[0][0], max(0.0, first - step), first)
                 if first > step else first]
        for k in range(1, len(runs)):
            prev_txt, prev_end = runs[k - 1][0], runs[k - 1][2]
            edges.append(refine(src, union, prev_txt, runs[k][0], prev_end, runs[k][1]))
        last = runs[-1][2]
        edges.append(refine(src, union, runs[-1][0], None, last, min(last + step, dur))
                     if last + step < dur else min(last + step, dur))
        t0, t1 = edges[0], min(edges[-1], dur)     # the refined edges, not the sample grid
        L.fact(f"entity({eid}, \"on_screen_text\", seed({T(t0)}))", "rapidocr", best)
        L.fact(f"holds(visible({eid}), {T(t0)}, {T(t1)})", "rapidocr", best)
        L.fact(f"holds(in_third({eid}, {third(cx)}), {T(t0)}, {T(t1)})", "rapidocr", best)
        L.fact(f"holds(in_band({eid}, {band(cy)}), {T(t0)}, {T(t1)})", "rapidocr", best)
        for k, (txt, _a, _b, agree) in enumerate(runs):
            esc = txt.replace('\\', '\\\\').replace('"', '\\"')
            L.fact(f'holds(text({eid}, "{esc}"), {T(edges[k])}, {T(min(edges[k + 1], dur))})',
                   "rapidocr", agree)
        for k in range(1, len(runs)):
            L.fact(f"happens(text_change({eid}), {T(edges[k])})", "rapidocr", runs[k][3])
    return n


if __name__ == "__main__":
    from .facts import Log
    L = Log()
    emit(sys.argv[1], L, log=lambda s: print(f"% {s}", file=sys.stderr))
    print(L.text(), end="")
