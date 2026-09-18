"""Perceive a clip into the same grammar `facts.lift` produces for a comp.

    holds(F, T0, T1).   happens(E, T).   src(Fact, Producer, Conf).

Everything here carries provenance, because all of it is measured. Where a producer cannot separate
two explanations, it emits nothing: a reader can act on a missing fact, but a confident wrong one
propagates. Two cases on real footage set that rule. A monocular depth model ranked an out-of-focus
foreground object as the farthest thing in frame, so depth ordering is now vetoed wherever focus
says one of the pair is off the focal plane. And a man walking while the camera dollies after him
is still in the frame and still in the compensated world, indistinguishable from a static object at
the centre of the dolly's expansion — so nothing is said about it, rather than the wrong thing.

Each producer contributes its own facts and nothing else depends on it; `_optional` keeps a missing
library or a model that throws from costing the whole log after minutes of propagation.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from .profile import stage
from . import audiofacts as AF
from . import camerafacts as CF
from . import depth as DP
from . import tracking as TR
from .facts import Log, T, band, runs, third
from .sources import frame_at, open_source

# Shot scale from the tallest person-like subject, on the standard ladder. No model needed: the
# ratio of a body to the frame is what the terms mean.
SHOT_SCALE = [(0.95, "ecu"), (0.75, "cu"), (0.55, "mcu"), (0.4, "ms"), (0.25, "mls"), (0.1, "ls")]
# Matched as substrings, because the class is whatever concept prompt found the thing: "man with
# backpack" and "woman recording" are both people and neither is a fixed label.
PERSONISH = ("person", "man", "woman", "driver", "speaker", "face")


def _shot_scale(frac: float) -> str:
    for lo, name in SHOT_SCALE:
        if frac >= lo:
            return name
    return "els"


def _edge(box) -> list[str]:
    x0, y0, x1, y1 = box
    return [s for s, c in (("left", x0 < 0.03), ("right", x1 > 0.97),
                           ("top", y0 < 0.03), ("bottom", y1 > 0.97)) if c]


def _camera_frame(cam_recs: list[dict], W: int, H: int, w: int = 320) -> tuple:
    """The cumulative similarity the camera applies to anything standing still in the world.

    G3. A tracked box that holds still in the world still slides across the frame when the camera
    pans, and a naive reading calls that motion. But a pan is not the only thing a camera does: on a
    dolly the background expands about a point rather than sliding, and subtracting a translation
    there *adds* drift to parked cars and promotes them to subjects — which is exactly what a first
    translation-only version of this did to the man clip.

    So the correction is the same similarity camerafacts already measures on the background, with
    subjects masked out: a per-step scale about the frame centre plus a translation, composed into
    the map a static point follows. Steps the RANSAC fit could not trust contribute the identity,
    which under-corrects rather than inventing a correction."""
    if not cam_recs:
        return np.zeros(1), np.ones(1), np.zeros(1), np.zeros(1)
    h = max(1.0, w * H / max(W, 1))
    S, tx, ty = 1.0, 0.0, 0.0
    Ss, Txs, Tys = [], [], []
    for r in cam_recs:
        sc = float(np.exp(r["ls"]))
        tx = sc * tx + r["dx"] / w          # p_{k+1} = s(p_k - c) + c + t, composed from the start
        ty = sc * ty + r["dy"] / h
        S = float(np.clip(sc * S, 0.2, 5.0))
        Ss.append(S); Txs.append(tx); Tys.append(ty)
    return (np.array([r["t"] for r in cam_recs], float),
            np.array(Ss), np.array(Txs), np.array(Tys))


def _unmap(cam: tuple, ts: np.ndarray, cs: np.ndarray, ar: np.ndarray) -> tuple:
    """Undo the camera's similarity: where each centroid and area would be with a locked camera."""
    ct, cS, cTx, cTy = cam
    S = np.interp(ts, ct, cS)
    tx, ty = np.interp(ts, ct, cTx), np.interp(ts, ct, cTy)
    ws = np.stack([0.5 + (cs[:, 0] - 0.5 - tx) / S, 0.5 + (cs[:, 1] - 0.5 - ty) / S], 1)
    return ws, ar / S ** 2                  # a dolly grows a static object as the square of scale


def perceive(clip: Path, prompts: list[str], k: dict[str, int] | None = None,
             want_audio: bool = True, want_depth: bool = True, want_text: bool = True,
             want_pose: bool = True, want_contact: bool = False, script: str | None = None,
             log=lambda s: None) -> str:
    clip = Path(clip)
    src = open_source(clip)
    dur, W, H = src.duration, src.width, src.height
    dt = 1.0 / TR.SAMPLE_FPS

    L = Log()
    L.c(f"fact log for {clip.name} — perceived, every fact carries provenance")
    L.c("grammar: holds(F,T0,T1) happens(E,T) src(Fact,Producer,Conf)")
    L.fact(f'clip("{clip.name}")', "ffprobe", 0.99)
    L.fact(f"fps({src.fps:g})", "ffprobe", 0.99)
    L.fact(f"duration({T(dur)})", "ffprobe", 0.99)
    L.fact(f"frame_size({W}, {H})", "ffprobe", 0.99)
    L.blank()

    with stage("cuts (ffmpeg scene)"):
        cuts = _cuts(clip)
    bounds = [0.0] + cuts + [dur]
    for i in range(len(bounds) - 1):
        L.fact(f'shot(s{i + 1}, {T(bounds[i])}, {T(bounds[i + 1])}, clip("{clip.name}"))',
               "ffmpeg_scene", 0.9 if cuts else 0.97)
    for i, t in enumerate(cuts):
        L.fact(f"happens(cut(s{i + 1}, s{i + 2}, hard), {T(t)})", "ffmpeg_scene", 0.8)
    L.blank()

    # Propagation is bounded by the cuts, so each shot yields its own tracks and `identity` has
    # separate pieces to rejoin rather than one mask dragged through every cut.
    with stage("track (detect + sam2)"):
        ents = TR.track(clip, prompts, k or {}, dur, bounds=bounds, log=log)
    for tr in ents:                             # one shared time grid so tracks can be compared
        seen = {}
        for o in tr["obs"]:
            key = round(round(o[0] / dt) * dt, 2)
            seen.setdefault(key, (key, *o[1:]))
        tr["obs"] = [seen[key] for key in sorted(seen)]
    ents = [t for t in ents if len(t["obs"]) >= 3]

    with stage("camera steps"):
        cam_recs, _fps = CF.steps(clip, ents)
    _entities(L, ents, dur, dt, W, H, _camera_frame(cam_recs, W, H))
    touch = _relations(L, ents, dt, dur)
    if want_depth:
        with stage("depth (da3)"):
            _depth(L, src, ents, dur)
    L.blank()

    with stage("camera classify"):
        CF.emit(clip, L, ents, duration=dur, use_depth=want_depth)
    L.blank()

    if want_contact:
        _optional(L, log, "contact", "release times",
                  lambda m: emit_releases(clip, ents, touch, L, W, H, dur, log=log))

    shots = [(f"s{i + 1}", bounds[i], bounds[i + 1]) for i in range(len(bounds) - 1)]
    _optional(L, log, "boundaries", "action boundaries",
              lambda m: m.emit(clip, L, shots=shots, cuts=cuts, log=log))
    _optional(L, log, "identity", "cross-shot identity",
              lambda m: m.emit(clip, L, ents, duration=dur, log=log))
    if want_pose:
        _optional(L, log, "landmarks", "posture and facing",
                  lambda m: m.emit(clip, L, ents, duration=dur, log=log))
    if want_text:
        _optional(L, log, "ocr", "on-screen text",
                  lambda m: m.emit(clip, L, duration=dur, start=len(ents) + 1, log=log))

    if want_audio and AF.has_audio(clip):
        L.c("producer: audio")
        with stage("audio: words + beats + loudness"):
            words = AF.emit(clip, L, want_words=True, text=script)
        boxes = {e["id"]: [(o[0], tuple(o[1])) for o in e["obs"]] for e in ents}
        if boxes:
            with stage("audio: sound source"):
                AF.emit_sound_source(clip, boxes, L)
            spans = AF.speech_spans(words)     # reused: alignment is the expensive part
            if spans:
                L.blank()
                with stage("audio: who is speaking"):
                    turns = AF.emit_speaking(clip, boxes, L, spans)
                # Diarization runs after, and is told what the correlation already named, so a
                # cluster it recognises comes back as that entity rather than an anonymous `spk1`.
                known: dict[str, list] = {}
                for a, b, who, _r in turns:
                    known.setdefault(who, []).append((a, b))
                _optional(L, log, "diarize", "speaker turns",
                          lambda m: m.emit(clip, L, spans, known=known, log=log))
    elif want_audio:
        L.fact("audio(none)", "ffprobe", 0.99)
    return L.text()


def _optional(L: Log, log, module: str, what: str, run) -> None:
    """Run a producer that may not be installed, or may fall over.

    A producer's job is to contribute facts, and losing all of them is the worst thing it can do.
    These run at the end of a pipeline that has already spent minutes on SAM propagation, so a
    missing library or a model that throws must cost that producer's facts and nothing else. The
    failure is logged rather than swallowed, because a silent gap looks exactly like a clip with
    nothing to say."""
    import importlib
    try:
        with stage(what):
            run(importlib.import_module(f".{module}", __package__))
    except ImportError as e:
        log(f"{what}: not available ({e})")
    except Exception as e:                              # noqa: BLE001
        log(f"{what}: producer failed ({type(e).__name__}: {e}); continuing without it")
    else:
        if L.lines and L.lines[-1]:
            L.blank()


def _cuts(clip: Path) -> list[float]:
    import re
    import subprocess
    r = subprocess.run(["ffmpeg", "-v", "info", "-i", str(clip), "-vf",
                        "select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
                       capture_output=True, text=True, timeout=900).stderr
    return [float(x) for x in re.findall(r"pts_time:([\d.]+)", r)]


def _entities(L: Log, ents: list[dict], dur: float, dt: float, W: int, H: int,
              cam: tuple) -> None:
    L.c(f"producer: seeded detection + sam2 propagation, {TR.SAMPLE_FPS} fps samples")
    for i, tr in enumerate(ents):
        e = tr["id"] = f"e{i + 1}"
        obs, c0 = tr["obs"], tr["conf"]
        L.fact(f'entity({e}, "{tr["cls"]}", seed({T(tr["seed_t"])}))', tr["how"], c0)

        segs = [[obs[0][0], obs[0][0]]]
        for o in obs[1:]:
            if o[0] - segs[-1][1] > 0.5:
                segs.append([o[0], o[0]])
            else:
                segs[-1][1] = o[0]
        for a, b in segs:
            L.fact(f"holds(visible({e}), {T(a)}, {T(min(b + dt, dur))})", "sam2", c0)

        t0, b0 = obs[0][0], obs[0][1]
        t1, b1 = obs[-1][0], obs[-1][1]
        if t0 > dt:
            ed = _edge(b0)
            # a track that starts mid-frame was *noticed* there; only an edge crossing is an entrance
            L.fact(f"happens(enter({e}, from({ed[0]})), {T(t0)})" if ed
                   else f"happens(first_seen({e}), {T(t0)})", "sam2", c0)
        if t1 < dur - 2 * dt:
            ed = _edge(b1)
            L.fact(f"happens(exit({e}, to({ed[0]})), {T(min(t1 + dt, dur))})" if ed
                   else f"happens(last_seen({e}), {T(min(t1 + dt, dur))})", "sam2", c0)

        ts = np.array([o[0] for o in obs])
        cs = np.array([o[3] for o in obs])
        ar = np.array([o[2] for o in obs])
        hs = np.array([o[1][3] - o[1][1] for o in obs])
        ws, war = _unmap(cam, ts, cs, ar)      # world-relative: the camera's own move removed

        for v, a, b in runs([(t, third(c[0])) for t, c in zip(ts, cs)], 0.4):
            L.fact(f"holds(in_third({e}, {v}), {T(a)}, {T(min(b + dt, dur))})", "sam2", c0)
        for v, a, b in runs([(t, band(c[1])) for t, c in zip(ts, cs)], 0.4):
            L.fact(f"holds(in_band({e}, {v}), {T(a)}, {T(min(b + dt, dur))})", "sam2", c0)
        if any(w in tr["cls"] for w in PERSONISH):
            for v, a, b in runs([(t, _shot_scale(float(h))) for t, h in zip(ts, hs)], 0.6):
                L.fact(f"holds(shot_scale({e}, {v}), {T(a)}, {T(min(b + dt, dur))})", "geometry", 0.7)

        def cover(a: float, b: float) -> float:
            return min(1.0, ((ts >= a) & (ts <= b)).sum() / max(1, round((b - a) / dt) + 1))

        def label(xy: np.ndarray, i_: int):
            m = (ts >= ts[i_] - 0.3) & (ts <= ts[i_] + 0.3)
            if m.sum() < 3:
                return ("still",)
            vx = float(np.polyfit(ts[m], xy[m, 0], 1)[0])
            vy = float(np.polyfit(ts[m], xy[m, 1], 1)[0])
            sp = float(np.hypot(vx, vy))
            if sp < 0.04:
                return ("still",)
            d = ("right" if vx > 0 else "left") if abs(vx) >= abs(vy) else ("down" if vy > 0 else "up")
            return ("move", d, "slow" if sp < 0.12 else "medium" if sp < 0.3 else "fast")

        lab = [(ts[i_], label(ws, i_)) for i_ in range(len(ts))]      # world-relative
        # A known blind spot, left silent on purpose. The man in the man clip is walking while the
        # camera dollies after him; he sits near the centre of the dolly's expansion, so he is still
        # in the frame and still in the compensated world, and the walk leaves no trace in either.
        # A static object at that same spot produces the identical evidence, so nothing here can
        # separate "the camera follows him" from "it is scenery" — a tried `followed_by_camera`
        # fluent fired on the three parked cars and not on the man. Gait is a body-pose fact
        # (landmarks.py) or an event-boundary one, not something a box centroid can recover.

        mruns = runs(lab, 0.4)
        sm = np.array([ws[max(0, i_ - 2):i_ + 3].mean(0) for i_ in range(len(ws))]) if len(ws) > 4 else ws
        # subject vs scenery: net travel, not accumulated path. A mask that wobbles because
        # something occludes it accumulates distance while going nowhere.
        rng = float(np.ptp(sm, axis=0).max()) if len(sm) > 1 else 0.0
        tr["subject"] = rng >= 0.1 or any(v[0] == "move" and v[2] in ("medium", "fast") and b - a >= 0.6
                                          for v, a, b in mruns)
        merged: list[list] = []
        for v, a, b in mruns:
            if merged and merged[-1][0][0] == "move" and v[0] == "move" and merged[-1][0][1] == v[1]:
                merged[-1][2] = b
                merged[-1][0] = ("move", v[1], max(merged[-1][0][2], v[2],
                                                   key=("slow", "medium", "fast").index))
            else:
                merged.append([v, a, b])
        if tr["subject"]:
            for v, a, b in merged:
                if v[0] == "still":
                    L.fact(f"holds(still({e}), {T(a)}, {T(min(b + dt, dur))})", "sam2", c0 * cover(a, b))
                else:
                    L.fact(f"holds(motion({e}, dir({v[1]}), {v[2]}), {T(a)}, {T(min(b + dt, dur))})",
                           "sam2", c0 * cover(a, b))
            size = []
            for i_ in range(len(ts)):
                m = (ts >= ts[i_] - 0.5) & (ts <= ts[i_] + 0.5)
                g = float(np.polyfit(ts[m], np.log(war[m] + 1e-6), 1)[0]) if m.sum() >= 3 else 0.0
                size.append((ts[i_], "approaching" if g > 0.25 else "receding" if g < -0.25 else "same"))
            for v, a, b in runs(size, 0.6):
                if v != "same":
                    L.fact(f"holds({v}({e}), {T(a)}, {T(min(b + dt, dur))})", "sam2", c0 * cover(a, b))
        else:
            L.fact(f"holds(still({e}), {T(ts[0])}, {T(min(ts[-1] + dt, dur))})", "sam2", c0 * cover(ts[0], ts[-1]))

    for cls, n in Counter(e["cls"] for e in ents).items():
        if n >= 3:
            L.fact(f'count("{cls}", {n})', "sam2", 0.6)
    L.blank()


def _relations(L: Log, ents: list[dict], dt: float, dur: float) -> dict:
    kern = np.ones((5, 5), np.uint8)
    touch: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for i in range(len(ents)):
        for j in range(len(ents)):
            if i == j or not (ents[i].get("subject") or ents[j].get("subject")):
                continue
            A = {round(o[0], 2): o for o in ents[i]["obs"]}
            B = {round(o[0], 2): o for o in ents[j]["obs"]}
            common = sorted(set(A) & set(B))
            if not common:
                continue
            sam = []
            for t in common:
                ma, mb = A[t][4], B[t][4]
                sam.append((t, ma.shape == mb.shape
                            and bool((cv2.dilate(ma.astype(np.uint8), kern).astype(bool) & mb).any())))
            if i < j:
                for v, a, b in runs(sam, 0.3):
                    if v:
                        touch.setdefault((ents[i]["id"], ents[j]["id"]), []).append((a, b + dt))
                        L.fact(f"holds(touching({ents[i]['id']}, {ents[j]['id']}), {T(a)}, "
                               f"{T(min(b + dt, dur))})", "sam2_masks", 0.7)
                        if a > common[0] + 0.3:
                            L.fact(f"happens(contact({ents[i]['id']}, {ents[j]['id']}), {T(a)})",
                                   "sam2_masks", 0.6)
            on = []
            for t, tv in sam:
                oa, ob = A[t], B[t]
                on.append((t, tv and oa[3][1] < ob[3][1] and ob[1][1] <= oa[1][3] <= ob[1][3]))
            for v, a, b in runs(on, 0.5):
                if v and not ents[j].get("subject"):     # supported by scenery, not held by a subject
                    L.fact(f"holds(on({ents[i]['id']}, {ents[j]['id']}), {T(a)}, {T(min(b + dt, dur))})",
                           "sam2_masks", 0.6)
    hands = [e["id"] for e in ents if e["cls"] in ("hand", "gloved hand")]
    for obj in [e["id"] for e in ents if e["cls"] not in ("hand", "gloved hand")]:
        segs = []
        for (a, b), ivs in touch.items():
            h = b if a == obj else a if b == obj else None
            if h in hands:
                segs += [(t0, t1, h) for t0, t1 in ivs]
        segs.sort()
        for (a0, a1, ha), (b0, b1, hb) in zip(segs, segs[1:]):
            if ha != hb and b0 <= a1 + 0.5:
                L.fact(f"happens(handoff({obj}, from({ha}), to({hb})), {T(max(b0, a1 - 0.5))})",
                       "rule_handoff", 0.6)
    L.blank()
    return touch


def _depth(L: Log, src, ents: list[dict], dur: float, fps_s: float = 2.0) -> None:
    L.c("producer: da3 depth over entity masks, with a focus cross-check")
    rel = {e["id"]: [] for e in ents}
    sharp = {e["id"]: [] for e in ents}
    for t in np.arange(0.25, dur, 1.0 / fps_s):
        fr = frame_at(src, float(t), 1024)
        H, W = fr.shape[:2]
        res = DP.mono(fr)
        relief = DP.to_relief(res["depth"])
        RH, RW = relief.shape
        gray = cv2.cvtColor(fr, cv2.COLOR_RGB2GRAY)
        fvar = cv2.Laplacian(gray, cv2.CV_64F).var() + 1e-6
        for e in ents:
            near = [o for o in e["obs"] if abs(o[0] - t) < 0.06]
            if not near:
                continue
            mk = cv2.resize(near[0][4].astype(np.uint8), (RW, RH),
                            interpolation=cv2.INTER_NEAREST).astype(bool)
            if mk.sum() < 30:
                continue
            rel[e["id"]].append((t, float(np.median(relief[mk]))))
            x0, y0, x1, y1 = near[0][1]
            gx0, gy0, gx1, gy1 = int(x0 * W), int(y0 * H), int(x1 * W), int(y1 * H)
            if gx1 > gx0 + 4 and gy1 > gy0 + 4:
                sharp[e["id"]].append(cv2.Laplacian(gray[gy0:gy1, gx0:gx1], cv2.CV_64F).var() / fvar)

    soft = set()
    for e in ents:
        if sharp[e["id"]]:
            s = float(np.median(sharp[e["id"]]))
            name = "low" if s < 0.35 else "high" if s > 1.5 else "normal"
            L.fact(f"sharpness({e['id']}, {name})", "laplacian", 0.8)
            if name == "low":
                soft.add(e["id"])
                L.fact(f"holds(out_of_focus({e['id']}), {T(0.0)}, {T(dur)})", "laplacian", 0.8)

    subj = {e["id"] for e in ents if e.get("subject")}
    for a, b, t0, t1, conf in depth_pairs([e["id"] for e in ents], rel, soft, subj):
        L.fact(f"holds(nearer({a}, {b}), {T(t0)}, {T(min(t1, dur))})", "da3_small", conf)


def emit_releases(clip: Path, ents: list[dict], touch: dict, L, W: int, H: int, dur: float,
                  log=lambda s: None) -> None:
    """`release(a, b)` — the frame one thing stops resting on another.

    Off by default, and the cost is why: the break is only legible in masks at native resolution,
    so this re-segments a tight window per step across each pair's contact interval. One pair over
    eight and a half seconds is about fifteen minutes. Everything cheaper was measured and does not
    contain the event — see `contact.sweep` for the four that failed.
    """
    from . import contact as K
    by_id = {e["id"]: e for e in ents}
    for (a, b), spans in touch.items():
        ea, eb = by_id.get(a), by_id.get(b)
        if not ea or not eb:
            continue
        oa = {round(o[0], 3): o[1] for o in ea.get("obs", [])}
        ob = {round(o[0], 3): o[1] for o in eb.get("obs", [])}
        for t0, t1 in spans:
            if t1 - t0 < K.WIN:
                continue
            log(f"{a}/{b}: looking for a release between {t0:.2f} and {t1:.2f}")
            got = K.sweep(clip, oa, ob, t0, min(t1, dur), W, H)
            if got is None:
                log(f"{a}/{b}: no release found; they do not visibly let go")
                continue
            t, conf = got
            L.fact(f"happens(release({a}, {b}), {T(t)})", "contact_gap", conf)
            break


def depth_pairs(ids: list[str], rel: dict[str, list], soft: set[str], subj: set[str],
                agree: float = 0.7) -> list[tuple]:
    """Which entity is in front, where the measurement means anything.

    G10. Monocular depth reads blur as distance. On a shallow-depth-of-field shot it placed an
    out-of-focus camera in the near foreground at 0.29 relief against a sharp subject at 0.81 —
    exactly backwards, and confidently so. Sharpness is the cross-check: when either entity is off
    the focal plane the depth model is not measuring depth, so no ordering is emitted. Silence is
    the correct output; the reader can fall back on the focus fact, which is right."""
    out = []
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if ids[i] not in subj and ids[j] not in subj:
                continue
            if ids[i] in soft or ids[j] in soft:
                continue
            A, B = dict(rel.get(ids[i], [])), dict(rel.get(ids[j], []))
            common = sorted(set(A) & set(B))
            if len(common) < 2:
                continue
            frac = float(np.mean([A[t] > B[t] for t in common]))
            if frac >= agree or frac <= 1 - agree:
                a, b = (ids[i], ids[j]) if frac >= agree else (ids[j], ids[i])
                out.append((a, b, common[0], common[-1], max(frac, 1 - frac)))
    return out


if __name__ == "__main__":
    prompts = sys.argv[2].split(",") if len(sys.argv) > 2 else ["person"]
    print(perceive(Path(sys.argv[1]), prompts, log=lambda s: print(f"% {s}", file=sys.stderr)), end="")
