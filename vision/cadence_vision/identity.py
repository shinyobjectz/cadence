"""G7. The same thing, seen again after a cut.

A tracker stops at a cut. Everything after it is a new entity with a new id, so a log of a
three-shot sequence says three people were present when there was one, and an edit that means
"hold on her until she looks away" has nothing to hold on to. This producer puts the pieces back
together:

    holds(same_as(e4, e1), 6.200, 9.800).
    src(holds(same_as(e4, e1), 6.200, 9.800), clip_appearance, 0.82).

`same_as(Later, Earlier)` points backwards, so the earliest sighting is the canonical one and a
reader can fold a chain without sorting.

**Why the confidence is not the cosine.** CLIP similarity between two crops of the same person and
between two crops of different people in similar clothes are both "high"; the number is compressed,
uncalibrated, and means nothing on its own. What can be measured is how that pair compares with
pairs known to be *different* — two entities visible in the same frame cannot be one thing, so the
similarities between them are a null distribution sampled from this clip, this lighting, this lens.
A pair's confidence is its percentile against that null. On a clip with no such pair there is
nothing to calibrate against and nothing is claimed, which is the correct answer rather than a
threshold carried in from someone else's footage.

This is appearance only. A face recognizer or a person re-identifier would be stronger where the
subject is a person, and slots in here as another opinion whose agreement with this one is itself a
measurement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import embed
from .facts import T

SAMPLES = 5              # crops per track, spread over its life
PAD = 0.06               # context around the mask box: a crop with no surroundings is ambiguous
MIN_SIDE = 24
MIN_NULL = 6             # fewer known-different pairs than this and there is nothing to calibrate
MIN_CONF = 0.6


def crops(src, tr: dict, n: int = SAMPLES, pad: float = PAD) -> list[np.ndarray]:
    """`n` crops of one track, spread over the interval it was visible."""
    from .sources import frame_at
    obs = tr["obs"]
    if not obs:
        return []
    out = []
    for i in np.linspace(0, len(obs) - 1, min(n, len(obs))).astype(int):
        t, box = obs[i][0], obs[i][1]
        try:
            fr = frame_at(src, float(t), 1024)
        except Exception:                               # noqa: BLE001
            continue
        H, W = fr.shape[:2]
        x0 = max(0, int((box[0] - pad) * W)); x1 = min(W, int((box[2] + pad) * W))
        y0 = max(0, int((box[1] - pad) * H)); y1 = min(H, int((box[3] + pad) * H))
        if x1 - x0 >= MIN_SIDE and y1 - y0 >= MIN_SIDE:
            out.append(fr[y0:y1, x0:x1])
    return out


def appearance(src, tracks: list[dict], n: int = SAMPLES) -> dict[str, np.ndarray]:
    """One unit vector per track: the mean of its crops' CLIP embeddings."""
    vecs: dict[str, np.ndarray] = {}
    for tr in tracks:
        cs = crops(src, tr, n)
        if not cs:
            continue
        side = max(max(c.shape[:2]) for c in cs)
        pad = np.stack([_fit(c, side) for c in cs])
        e = embed.embed_frames(pad)
        if len(e):
            v = e.mean(0)
            vecs[tr["id"]] = v / (np.linalg.norm(v) + 1e-9)
    return vecs


def _fit(c: np.ndarray, side: int) -> np.ndarray:
    """Letterbox a crop into a square so the batch stacks without distorting aspect."""
    import cv2
    h, w = c.shape[:2]
    s = side / max(h, w)
    r = cv2.resize(c, (max(1, int(w * s)), max(1, int(h * s))))
    out = np.zeros((side, side, 3), np.uint8)
    out[: r.shape[0], : r.shape[1]] = r
    return out


def overlap(a: dict, b: dict, tol: float = 0.15) -> bool:
    """Were these two on screen at the same time? If so they are different things, by construction.

    The spans are compared rather than the samples. A track with a gap in the middle counts as
    present across it, which refuses a merge that might have been right — the safe direction, since
    the cost of a wrong `same_as` is every later fact about one entity attached to another."""
    if not a["obs"] or not b["obs"]:
        return False
    a0, a1 = a["obs"][0][0], a["obs"][-1][0]
    b0, b1 = b["obs"][0][0], b["obs"][-1][0]
    return a0 <= b1 + tol and b0 <= a1 + tol


def null_distribution(tracks: list[dict], vecs: dict[str, np.ndarray]) -> list[float]:
    """Similarities between entities seen at the same moment — pairs that cannot be one thing."""
    out = []
    for i in range(len(tracks)):
        for j in range(i + 1, len(tracks)):
            a, b = tracks[i], tracks[j]
            if a["id"] in vecs and b["id"] in vecs and overlap(a, b):
                out.append(float(vecs[a["id"]] @ vecs[b["id"]]))
    return out


def match(tracks: list[dict], vecs: dict[str, np.ndarray], null: list[float],
          min_conf: float = MIN_CONF) -> list[tuple[str, str, float]]:
    """[(later, earlier, confidence)] for tracks that look alike and were never on screen together.

    Confidence is the pair's percentile against `null`: the share of known-different pairs it beats.

    Pairs are then merged into groups strongest first, under one constraint — **a group may not
    contain two tracks that were visible at the same moment**, because one thing cannot be in two
    places. That constraint does the work a mutual-best-match rule was doing, without its failure:
    mutual-best links only one pair out of three identical sightings, so a subject appearing in
    three shots came back as two people. Grouping chains them, and still cannot collapse a crowd,
    since any two people in one frame are barred from ever joining."""
    if len(null) < MIN_NULL:
        return []
    cand = [t for t in tracks if t["id"] in vecs]
    by_id = {t["id"]: t for t in cand}
    first = {t["id"]: t["obs"][0][0] for t in cand}
    nul = np.asarray(null, float)

    pairs = []
    for i in range(len(cand)):
        for j in range(i + 1, len(cand)):
            a, b = cand[i]["id"], cand[j]["id"]
            if overlap(cand[i], cand[j]):
                continue
            v = float(vecs[a] @ vecs[b])
            conf = round(float((nul < v).mean()), 2)
            if conf >= min_conf:
                pairs.append((v, conf, a, b))
    pairs.sort(reverse=True)

    group = {t["id"]: t["id"] for t in cand}
    members = {t["id"]: [t["id"]] for t in cand}
    linked: dict[str, float] = {}
    for _v, conf, a, b in pairs:
        ga, gb = group[a], group[b]
        if ga == gb:
            continue
        if any(overlap(by_id[x], by_id[y]) for x in members[ga] for y in members[gb]):
            continue
        keep, drop = (ga, gb) if first[ga] <= first[gb] else (gb, ga)
        for m in members[drop]:
            group[m] = keep
            linked.setdefault(m, conf)
        members[keep] += members[drop]
        del members[drop]

    out = []
    for root, ms in members.items():
        for m in sorted(ms, key=lambda x: first[x]):
            if m != root:
                out.append((m, root, linked.get(m, min_conf)))
    return sorted(out)


def emit(clip, L, tracks: list[dict], duration: float | None = None, log=lambda s: None) -> int:
    """Append `same_as` facts. Returns how many identities were rejoined."""
    from .sources import open_source
    if not embed.available():
        log("open_clip not installed; no cross-shot identity facts")
        return 0
    src = open_source(Path(clip))
    dur = duration or src.duration
    vecs = appearance(src, tracks)
    null = null_distribution(tracks, vecs)
    if len(null) < MIN_NULL:
        log(f"only {len(null)} known-different pairs; nothing to calibrate identity against")
        return 0
    pairs = match(tracks, vecs, null)
    if not pairs:
        return 0
    by_id = {t["id"]: t for t in tracks}
    L.c(f"producer: clip appearance over {len(null)} known-different pairs as the null distribution")
    for a, b, conf in pairs:
        obs = by_id[a]["obs"]
        L.fact(f"holds(same_as({a}, {b}), {T(obs[0][0])}, {T(min(obs[-1][0], dur))})",
               "clip_appearance", conf)
    return len(pairs)


if __name__ == "__main__":
    from .facts import Log
    from . import tracking as TR
    from .sources import open_source
    src = open_source(Path(sys.argv[1]))
    tracks = TR.track(Path(sys.argv[1]), sys.argv[2].split(","), {}, src.duration, log=lambda s: None)
    L = Log()
    emit(sys.argv[1], L, tracks, log=lambda s: print(f"% {s}", file=sys.stderr))
    print(L.text(), end="")
