"""Find things in footage once, then follow them.

Per-frame detection loses anything the detector is unsure of — a dark silhouette, a blurred
foreground object — and stitching those fragments into tracks yields a second of coverage for
something that is on screen for eight. So: detect once at the most confident frame, then propagate
with SAM 2 forward and backward. Recall stops depending on the detector's worst frame.

Two seeders, in order:
  * an open-vocabulary detector (YOLO-World) on concept prompts;
  * failing that, ask vision models to ground the thing and keep the box only when two of them
    agree. The agreement *is* the confidence, which is a stronger number than any one model's
    self-reported score, and it finds objects no detector will (an out-of-focus camera on a
    tripod was found this way at IoU 0.98 after every prompt failed).
"""

from __future__ import annotations

import hashlib
import json
import pickle
import re
import subprocess
import tempfile
from pathlib import Path

import cv2
import numpy as np

from .profile import stage
from . import CACHE

SAMPLE_FPS = 10
SAM_WEIGHTS = "sam2.1_s.pt"
DETECTOR = "yolov8s-worldv2.pt"
MODELS = CACHE / "models"        # ultralytics fetches into the cwd otherwise, i.e. the repo root
CACHE_VERSION = 5                # bump when a change alters the tracks, so stale pickles retire
MASK_W = 160
DEVICE = "mps"


def weights(name: str) -> str:
    """A weight file kept beside the other caches, not dropped wherever the process was started."""
    MODELS.mkdir(parents=True, exist_ok=True)
    p = MODELS / name
    return str(p) if p.exists() else name


def _iou(a, b) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def detections(clip: Path, prompts: list[str], conf: float = 0.1, imgsz: int = 960,
               vid_stride: int = 3) -> list[dict]:
    from ultralytics import YOLO
    m = YOLO(weights(DETECTOR))
    m.set_classes(prompts)
    cap = cv2.VideoCapture(str(clip))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.release()
    out, n = [], 0
    for r in _timed(m.predict(str(clip), stream=True, vid_stride=vid_stride, imgsz=imgsz, conf=conf,
                              verbose=False, device=DEVICE), "yolo_world.frame"):
        t = n * vid_stride / fps
        n += 1
        if r.boxes is None:
            continue
        for b, c, cf in zip(r.boxes.xyxyn.tolist(), r.boxes.cls.tolist(), r.boxes.conf.tolist()):
            out.append({"cls": m.names[int(c)], "conf": float(cf), "t": t, "box": b})
    return sorted(out, key=lambda d: -d["conf"])


# ----------------------------------------------------------------------------- vlm grounding

def _boxes_from_reply(txt: str) -> list[dict]:
    m = re.search(r"\[.*\]", txt or "", re.S)
    if not m:
        return []
    try:
        rows = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for d in rows:
        # A model asked for [{"label":…, "box_2d":…}] sometimes answers with the bare box instead,
        # and `[x0, y0, x1, y1]` iterates to ints. Reading that as a dict used to raise TypeError
        # out of here, and because `track` catches grounding as one block, a single malformed reply
        # cost every *other* class its seed too. One unusable row is one unusable row.
        if isinstance(d, (list, tuple)) and len(d) == 4:
            box = d
        elif isinstance(d, dict) and "box_2d" in d:
            box = d["box_2d"]
        else:
            continue
        try:
            b = [float(v) / 1000 for v in box]           # models answer on a 0-1000 grid
        except (TypeError, ValueError):
            continue
        if len(b) != 4:
            continue
        cands = [(b[0], b[1], b[2], b[3]), (b[1], b[0], b[3], b[2])]   # xyxy or yxyx, by convention
        label = d.get("label", "") if isinstance(d, dict) else ""
        out.append({"label": label, "cands": [c for c in cands if c[2] > c[0] and c[3] > c[1]]})
    return out


GROUND_TIMEOUT = 60      # a fallback seeder must not be able to stall the pipeline behind it
GROUND_RETRIES = 2


def ground(clip: Path, t: float, targets: list[str], models=("qwen", "gemini"),
           min_agree: float = 0.5, timeout: int = GROUND_TIMEOUT,
           retries: int = GROUND_RETRIES) -> list[dict]:
    """Ask each model for one target at a time; keep a box only when the models agree.

    One target per call on purpose: asking for a list makes the reply *order* model-dependent, and
    pairing by index then silently swaps two objects' boxes."""
    from PIL import Image
    from . import evalrun as E
    from .sources import frame_at, open_source
    src = open_source(clip)
    im = Image.fromarray(frame_at(src, t))
    W, H = im.size
    seeds = []
    for tgt in targets:
        q = (f"This is a {W}x{H} video frame. Return ONLY JSON: a list with exactly one object, "
             f'keys "label" and "box_2d" as [x0,y0,x1,y1] in absolute pixel coordinates of this '
             f"{W}x{H} image, for: {tgt}. No other text.")
        parts = [{"type": "text", "text": q}, {"type": "image_url", "image_url": {"url": E._b64(im)}}]
        # Bounded on purpose. This ran for the better part of an hour on one clip when a provider
        # accepted the request and then hung up mid-response: the socket sat in CLOSE_WAIT, the
        # default 3 × 180 s budget applied per model per target, and an optional seeder held a
        # perception run hostage. Losing a seed costs that entity's facts; hanging costs all of them.
        # The cached reply depends on (clip, time, target, model), so all four are in the key.
        # It used to be the target's *index* in `targets`, which collides: grounding ["panel"]
        # caches under index 0, then grounding ["cat", "panel"] asks for "cat" at index 0 and is
        # handed the panel's answer. Measured, not hypothesised — asking for both targets returned
        # `cat` carrying the panel's box, while asking for either alone was correct.
        tag = re.sub(r"[^a-z0-9]+", "-", tgt.lower()).strip("-")[:40] or "target"
        reps = {mk: _boxes_from_reply(
            E.call(E.MODELS[mk], parts, f"ground-{clip.stem}-{t:.3f}-{tag}-{mk}",
                   timeout=timeout, retries=retries)["text"]) for mk in models}
        a = reps[models[0]][0]["cands"] if reps[models[0]] else []
        b = reps[models[1]][0]["cands"] if reps[models[1]] else []
        best = max(((x, y, _iou(x, y)) for x in a for y in b), key=lambda z: z[2], default=None)
        if best and best[2] > min_agree:
            seeds.append({"cls": tgt, "conf": round(best[2], 2), "t": t,
                          "box": [float(np.mean([best[0][k], best[1][k]])) for k in range(4)],
                          "how": "vlm_agreement"})
    return seeds


# ----------------------------------------------------------------------------- propagation

def _segment(clip: Path, t0: float, t1: float, reverse: bool, out: Path) -> None:
    vf = f"fps={SAMPLE_FPS}" + (",reverse" if reverse else "")
    with stage("ffmpeg.segment"):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}",
                        "-i", str(clip), "-vf", vf, "-an", "-c:v", "libx264", "-preset", "ultrafast",
                        "-crf", "18", str(out)], check=True)


def _timed(gen, name: str):
    """Time each step of a streaming predictor: the model runs inside next(), not in the loop body."""
    it = iter(gen)
    while True:
        with stage(name):
            try:
                r = next(it)
            except StopIteration:
                return
        yield r


def _propagate(video: Path, box_px: list[float], imgsz: int = 1024) -> list[tuple]:
    """Masks through `video` from a box on its first frame, reduced to observations.

    The tracker is `segtrack.masks` (EdgeTAM unless CADENCE_TRACKER=sam2); `imgsz` is kept for the
    SAM 2 fallback's signature and callers that pass it."""
    from . import segtrack
    obs = []
    for fi, mk in segtrack.masks(video, box_px):
        ys, xs = np.nonzero(mk)
        if len(xs) < 20:
            continue
        mh, mw = mk.shape
        small = cv2.resize(mk.astype(np.uint8), (MASK_W, max(1, int(mh * MASK_W / mw))),
                           interpolation=cv2.INTER_NEAREST).astype(bool)
        obs.append((fi, [xs.min() / mw, ys.min() / mh, xs.max() / mw, ys.max() / mh],
                    len(xs) / (mw * mh), [xs.mean() / mw, ys.mean() / mh], small))
    return obs


def shot_of(t: float, bounds: list[float] | None, duration: float) -> tuple[float, float]:
    """The shot containing `t`, as (start, end). Without cut times the whole clip is one shot."""
    if not bounds or len(bounds) < 2:
        return 0.0, duration
    for a, b in zip(bounds, bounds[1:]):
        if a <= t < b:
            return a, b
    return bounds[-2], bounds[-1]


def propagate_one(clip: Path, det: dict, duration: float, W: int, H: int,
                  shot: tuple[float, float] | None = None) -> dict:
    """Propagate a seed forward and backward, stopping at the cuts either side of it.

    SAM 2 does not know what a cut is. Given the whole clip it keeps emitting masks straight
    through one, latching the mask onto whatever happens to be under it in the next shot, so a
    three-shot assembly comes back as one track of nonsense instead of three honest ones. Bounding
    propagation by the shot is also what makes cross-shot identity a question worth asking: the
    pieces have to exist separately before anything can rejoin them."""
    ts = det["t"]
    lo, hi = shot if shot else (0.0, duration)
    b = det["box"]
    box_px = [b[0] * W, b[1] * H, b[2] * W, b[3] * H]
    with tempfile.TemporaryDirectory() as td:
        fwd = Path(td) / "f.mp4"
        _segment(clip, ts, hi, False, fwd)
        of = _propagate(fwd, box_px)
        ob = []
        if ts > lo + 0.2:
            bwd = Path(td) / "b.mp4"
            _segment(clip, lo, ts, True, bwd)
            ob = _propagate(bwd, box_px)
    obs = ([(ts + fi / SAMPLE_FPS, *rest) for fi, *rest in of]
           + [(ts - fi / SAMPLE_FPS, *rest) for fi, *rest in ob if fi > 0])
    obs.sort(key=lambda o: o[0])
    if obs:                                   # drop slivers while entering or leaving frame
        med = float(np.median([o[2] for o in obs]))
        obs = [o for o in obs if o[2] >= 0.15 * med]
    return {"cls": det["cls"], "conf": det["conf"], "seed_t": ts, "how": det.get("how", "detector"),
            "obs": obs}


def extend_across_cuts(clip: Path, tr: dict, bounds: list[float] | None, duration: float,
                       W: int, H: int, max_shots: int = 4, log=lambda s: None) -> dict:
    """Carry a track past a cut, but only as far as its mask keeps ignoring the cut.

    Bounding propagation by the shot is right for content in the shot and wrong for anything
    composited over the cut. The clip eval measured the cost: a picture-in-picture inset that is on
    screen for 5.4 s across two cuts was tracked for 2.9 s — one shot — scoring 0.957 mean IoU and
    a temporal IoU of 0.519. Spatially perfect, temporally half there.

    So each boundary is a question rather than a wall: propagate one shot further, and keep the
    extension only if the mask survived the crossing. Content that belongs to the shot fails the
    test and still stops at the cut, which is what lets `identity` rejoin it as a separate sighting."""
    if not bounds or len(bounds) < 3:
        return tr
    from .sources import open_source
    src = open_source(clip)
    for back in (False, True):      # an overlay usually starts before the shot its seed landed in
        for _ in range(max_shots):
            if not tr["obs"]:
                break
            edge = tr["obs"][0] if back else tr["obs"][-1]
            # A shot-bounded track starts *on* its opening boundary, so going backwards the cut to
            # cross is the one at or just before the first sample, not the one before that.
            cut = (max((b for b in bounds if b <= edge[0] + 1e-6), default=None) if back
                   else next((b for b in bounds if b > edge[0] + 1e-6), None))
            if cut is None or abs(edge[0] - cut) > CUT_REACH:
                break
            if back:
                if cut <= 1e-6:
                    break                       # already at the head of the clip
                lo = max((b for b in bounds if b < cut - 1e-6), default=0.0)
                hi = cut
            else:
                if cut >= duration - 1e-6:
                    break
                lo, hi = cut, next((b for b in bounds if b > cut + 1e-6), duration)
            b = edge[1]
            with tempfile.TemporaryDirectory() as td:
                seg = Path(td) / "x.mp4"
                _segment(clip, lo, hi, back, seg)
                got = _propagate(seg, [b[0] * W, b[1] * H, b[2] * W, b[3] * H])
            if not got:
                break
            obs = ([(hi - fi / SAMPLE_FPS, *rest) for fi, *rest in got] if back
                   else [(lo + fi / SAMPLE_FPS, *rest) for fi, *rest in got])
            obs.sort(key=lambda o: o[0])
            near = obs[-1] if back else obs[0]
            ok, why = ignores_the_cut(src, edge[1], cut)
            if not ok:
                log(f"{tr['cls']}: stops at the cut at {cut:.2f}s — {why} "
                    f"[{survival(edge, near)[1]}]")
                break
            log(f"{tr['cls']}: carries across the cut at {cut:.2f}s — {why} "
                f"[{survival(edge, near)[1]}]")
            tr["obs"] = sorted(obs + tr["obs"], key=lambda o: o[0])
    return tr


MIN_SUPPORT = 3          # fewer independent looks than this and there is nothing to measure


def support(tr: dict, dets: list[dict], iou_ok: float = 0.5, tol: float = 0.06) -> float | None:
    """X1. How often an independent detection of the same class agrees with the propagated mask.

    A seed's score is the detector's opinion of one frame, and passing it along as the track's
    confidence says nothing about the track — a lucky 0.95 on the frame the object was clearest
    travels with a mask that slid off it two seconds later. What can be measured is whether the
    track keeps landing where the detector independently puts that class at times the tracker was
    never told about. The seed's own frame is excluded, since it would agree by construction.

    None when there were too few independent looks to measure, which is the usual case for a
    VLM-grounded seed: no detector found that class at all, so its cross-model agreement stands."""
    same = [d for d in dets if d["cls"] == tr["cls"] and abs(d["t"] - tr["seed_t"]) > tol]
    by_t: dict[float, list] = {}
    for d in same:
        by_t.setdefault(round(d["t"], 3), []).append(d["box"])
    checked = hit = 0
    for t, boxes in by_t.items():
        near = [o for o in tr["obs"] if abs(o[0] - t) < tol]
        if not near:
            continue
        checked += 1
        # Any box of that class will do: two cars in frame means this track only has to match one.
        hit += any(_iou(near[0][1], b) >= iou_ok for b in boxes)
    return hit / checked if checked >= MIN_SUPPORT else None


CUT_SURVIVE_IOU = 0.7    # mask overlap across a cut that says "this did not care about the cut"
CUT_SURVIVE_AREA = (0.7, 1.4)
CUT_REACH = 0.25         # how close to a boundary a track must get before extension is tried


CUT_IGNORED = 0.75       # change inside the box, as a share of the change everywhere else
CUT_EPS = 0.05           # seconds either side of the boundary
CUT_MAX_AREA = 0.5       # a box larger than this leaves too little frame to compare against
CUT_MIN_CHANGE = 4.0     # mean 0-255 change outside the box below which the cut is not a cut


def ignores_the_cut(src, box, cut: float, eps: float = CUT_EPS,
                    limit: float = CUT_IGNORED) -> tuple[bool, str]:
    """Does the content inside this box carry on across the cut while the frame around it does not?

    This replaced a mask-overlap test, and the reason is a measurement rather than a preference.
    The premise of the old one — that an overlay sits in the same place at the same size on both
    sides of a cut — does not hold for the thing the clip eval actually contains: a moving
    picture-in-picture, tweened across the frame while it plays. Re-seeding SAM 2 past the boundary
    returned a mask covering part of the panel (area x0.65 and x0.48 either side), and replaying the
    inset's two crossings against four in-shot ones, neither mask IoU (accepts 0.41-0.59, rejects
    0.24-0.63), box IoU (0.91-0.92 against 0.74-0.92) nor a crop-histogram test separated the cases.
    Tuning any of them would have been fitting the noise.

    What does separate them is what a picture-in-picture physically *is*: a rectangle of foreign
    footage. At a cut the plate changes completely and the panel does not, so the pixels inside the
    box change far less than the pixels outside it. Measured on the same seven crossings: 0.59 for
    both directions of the inset, 0.98 to 1.18 for content belonging to the shot. The one crossing
    that scored like an overlay without being labelled one was a box lying wholly inside the
    picture-in-picture — a man detected in the panel, which is overlay content and does carry
    across. The test was right there too; only the name was ambiguous.

    It is a ratio against the same frame's own change, not an absolute, for the reason every
    threshold here is comparative: how much a cut changes depends on the two shots.

    Two situations it refuses to answer rather than guess. A box covering most of the frame leaves
    only a border to compare against, and a boundary where the frame barely changes is not a cut
    this can measure — a matched cut between two similar shots would read as an overlay everywhere."""
    from .sources import frame_at
    if (box[2] - box[0]) * (box[3] - box[1]) > CUT_MAX_AREA:
        return False, "box covers too much of the frame to compare against the rest"
    try:
        a = frame_at(src, max(0.0, cut - eps), 640).astype(np.float32)
        b = frame_at(src, cut + eps, 640).astype(np.float32)
    except Exception as e:                              # noqa: BLE001
        return False, f"could not read across the cut ({type(e).__name__})"
    if a is None or b is None or a.shape != b.shape:
        return False, "no comparable frames across the cut"
    d = np.abs(a - b).mean(axis=2)
    h, w = d.shape
    x0, y0 = max(0, int(box[0] * w)), max(0, int(box[1] * h))
    x1, y1 = min(w, max(x0 + 1, int(box[2] * w))), min(h, max(y0 + 1, int(box[3] * h)))
    keep = np.ones((h, w), bool)
    keep[y0:y1, x0:x1] = False
    inside = float(d[y0:y1, x0:x1].mean())
    outside = float(d[keep].mean()) if keep.any() else 0.0
    if outside < CUT_MIN_CHANGE:
        return False, f"the frame barely changes here ({outside:.1f}); not a cut this can measure"
    ratio = inside / outside
    if ratio > limit:
        return False, f"inside changed {ratio:.2f}x the rest — it belongs to the shot"
    return True, f"inside changed {ratio:.2f}x the rest"


def survives_cut(before, after) -> bool:
    """Did this mask ignore the cut?

    An overlay — a lower third, a logo, a picture-in-picture — is composited on top of the plate
    and is in the same place and the same size on both sides of a cut. In-shot content is not: the
    whole frame changes underneath the mask and it jumps or collapses. Comparing the two masks
    across the boundary separates the cases without needing to be told which one this is."""
    return survival(before, after)[0]


def survival(before, after) -> tuple[bool, str]:
    """(did it survive, why not) — the same test, with its numbers, because "did not survive" on
    its own is not a finding. A missing mask and a mask that moved are different failures and the
    first one is a bug in the caller, not evidence about the clip."""
    ma, mb = before[4], after[4]
    if ma is None or mb is None:
        return False, "no mask either side of the cut"
    if ma.shape != mb.shape:
        return False, f"mask shapes differ ({ma.shape} vs {mb.shape})"
    inter = float((ma & mb).sum())
    union = float((ma | mb).sum())
    if union <= 0:
        return False, "both masks empty"
    iou = inter / union
    ratio = (after[2] + 1e-9) / (before[2] + 1e-9)
    if iou < CUT_SURVIVE_IOU:
        return False, f"mask IoU {iou:.2f} < {CUT_SURVIVE_IOU} (area x{ratio:.2f})"
    if not CUT_SURVIVE_AREA[0] <= ratio <= CUT_SURVIVE_AREA[1]:
        return False, f"area x{ratio:.2f} outside {CUT_SURVIVE_AREA} (IoU {iou:.2f})"
    return True, f"IoU {iou:.2f}, area x{ratio:.2f}"


def _covered(det: dict, tracks: list[dict]) -> bool:
    for tr in tracks:
        if tr["cls"] != det["cls"]:
            continue
        near = [o for o in tr["obs"] if abs(o[0] - det["t"]) < 0.11]
        if near and _iou(near[0][1], det["box"]) > 0.3:
            return True
    return False


def _dedupe(tracks: list[dict], log) -> list[dict]:
    """Two tracks of one class whose masks agree are one object seeded twice."""
    keep: list[dict] = []
    for tr in sorted(tracks, key=lambda t: -t["conf"]):
        dup = False
        for k in keep:
            if k["cls"] != tr["cls"]:
                continue
            A = {o[0]: o[4] for o in k["obs"]}
            B = {o[0]: o[4] for o in tr["obs"]}
            common = [t for t in set(A) & set(B) if A[t].shape == B[t].shape]
            if len(common) < 5:
                continue
            ious = [float((A[t] & B[t]).sum()) / max(1, (A[t] | B[t]).sum()) for t in common]
            if np.mean(ious) > 0.6:
                log(f"dropped duplicate {tr['cls']} seeded @ {tr['seed_t']:.2f}s (mask IoU {np.mean(ious):.2f})")
                dup = True
                break
        if not dup:
            keep.append(tr)
    return sorted(keep, key=lambda t: t["obs"][0][0])


def track(clip: Path, prompts: list[str], k: dict[str, int], duration: float,
          min_conf: float = 0.15, vlm_fallback: bool = True, bounds: list[float] | None = None,
          log=print, cache_dir: Path | None = None) -> list[dict]:
    """Greedy: seed the most confident uncovered detection of each class, propagate, repeat.

    `bounds` is the shot boundaries ([0, cut..., duration]); propagation never crosses one, so a
    subject appearing in three shots yields three tracks for `identity` to rejoin."""
    key = hashlib.md5(repr((CACHE_VERSION, clip.name, prompts, k, vlm_fallback,
                            bounds)).encode()).hexdigest()[:8]
    cache = (cache_dir or clip.parent) / f".{clip.stem}.tracks-{key}.pkl"
    if cache.exists():
        tracks = pickle.loads(cache.read_bytes())
        for tr in tracks:
            log(f"{tr['cls']}: cached {tr['obs'][0][0]:.2f}-{tr['obs'][-1][0]:.2f}s ({len(tr['obs'])} obs)")
        return tracks
    dets = detections(clip, prompts)
    cap = cv2.VideoCapture(str(clip))
    W, H = int(cap.get(3)), int(cap.get(4))
    cap.release()
    tracks: list[dict] = []
    want_per_shot = max(1, len(bounds) - 1) if bounds else 1
    for p in prompts:
        want = k.get(p, 1) * want_per_shot
        while sum(1 for t in tracks if t["cls"] == p) < want:
            cand = next((d for d in dets if d["cls"] == p and d["conf"] >= min_conf
                         and not _covered(d, tracks)), None)
            if cand is None:
                break
            tr = propagate_one(clip, cand, duration, W, H,
                               shot_of(cand['t'], bounds, duration))
            if len(tr["obs"]) >= 5:
                extend_across_cuts(clip, tr, bounds, duration, W, H, log=log)
                sup = support(tr, dets)
                if sup is not None:
                    tr["conf"], tr["how"] = round(sup, 2), "detector_agreement"
                tracks.append(tr)
                log(f"{p}: seed {cand['conf']:.2f} @ {cand['t']:.2f}s -> "
                    f"{tr['obs'][0][0]:.2f}-{tr['obs'][-1][0]:.2f}s ({len(tr['obs'])} obs), "
                    f"confidence {tr['conf']:.2f} from {tr['how']}")
            else:
                dets = [d for d in dets if d is not cand]
    if vlm_fallback:
        # A class that asked for two and got one has been missed just as surely as one that got
        # none, and the handoff clip is the case that shows it: the detector sees a gloved hand at
        # 0.32 and never a second, so `handoff`, which needs two hands, could not fire at all.
        # Grounding is asked for the shortfall, not only for the wholly absent.
        want_per_shot = max(1, len(bounds) - 1) if bounds else 1
        short = {p: k.get(p, 1) * want_per_shot - sum(1 for t in tracks if t["cls"] == p)
                 for p in prompts}
        short = {p: n for p, n in short.items() if n > 0}
        if short:
            log(f"{ {p: n for p, n in short.items()} } still wanted; asking vision models to ground them")
            try:
                for seed in ground(clip, duration / 2, sorted(short)):
                    if short.get(seed["cls"], 0) <= 0 or _covered(seed, tracks):
                        continue           # already have enough of this class, or this very one
                    tr = propagate_one(clip, seed, duration, W, H,
                                       shot_of(seed['t'], bounds, duration))
                    if len(tr["obs"]) >= 5:
                        extend_across_cuts(clip, tr, bounds, duration, W, H, log=log)
                        tracks.append(tr)
                        short[seed["cls"]] -= 1
                        log(f"{seed['cls']}: vlm agreement {seed['conf']:.2f} -> "
                            f"{tr['obs'][0][0]:.2f}-{tr['obs'][-1][0]:.2f}s ({len(tr['obs'])} obs)")
            except Exception as e:                        # no API key, no network: not fatal
                log(f"vlm grounding unavailable ({type(e).__name__})")
    tracks = _dedupe(tracks, log)
    try:
        cache.write_bytes(pickle.dumps(tracks))
    except OSError:
        pass
    return tracks
