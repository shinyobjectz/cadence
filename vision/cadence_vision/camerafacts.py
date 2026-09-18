"""Camera motion as facts: still / pan / tilt / dolly / zoom / handheld, or an honest `unknown`.

Three layers, because no single one is trustworthy on real footage:

  * a RANSAC similarity fit between consecutive samples on *background* features, so a moving
    subject is rejected as outliers rather than voted in (a median-flow estimate reads a cat's
    jump as a handheld camera);
  * phase correlation of every sample against a median-background model, which gives an absolute
    position. Two confident registrations at the same position bracket an interval in which the
    camera did not move, whatever the subject did in between;
  * Depth Anything 3's per-frame intrinsics, which is the only way to separate a dolly from a
    zoom — in 2D they are the same transform.

When the background is too sparse or too clustered to separate camera from subject, the answer is
`unknown`. Guessing is worse than silence: a wrong camera fact is inherited by everything that
reads the log.
"""

from __future__ import annotations

import cv2
import numpy as np

STILL_FRAC = 0.003          # translation per step, as a fraction of frame width
JITTER_FRAC = 0.0025
DOLLY_LOG_SCALE = 0.04      # ~4 %/s of scale change
MIN_COVERAGE = 0.55         # fraction of a 4x4 grid the RANSAC inliers must touch
MIN_FEATURES = 150


def background_model(path, w: int = 320, k: int = 24) -> np.ndarray:
    """Median of k frames spread over the clip: on a locked camera this erases moving objects."""
    cap = cv2.VideoCapture(str(path))
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    frames = []
    for i in np.linspace(0, max(0, n - 1), k).astype(int):
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, fr = cap.read()
        if ok:
            h = int(fr.shape[0] * w / fr.shape[1])
            frames.append(cv2.cvtColor(cv2.resize(fr, (w, h)), cv2.COLOR_BGR2GRAY))
    cap.release()
    return np.median(np.stack(frames), 0).astype(np.uint8) if frames else np.zeros((1, w), np.uint8)


def phase_offset(bg: np.ndarray, g: np.ndarray) -> tuple[tuple[float, float], float]:
    """(shift, response) of g against the background model. Works on edges, needs no texture."""
    win = cv2.createHanningWindow((bg.shape[1], bg.shape[0]), cv2.CV_32F)
    (dx, dy), resp = cv2.phaseCorrelate(np.float32(bg), np.float32(g), win)
    return (float(dx), float(dy)), float(resp)


def steps(path, ents=(), fps_s: float = 5.0, w: int = 320) -> tuple[list[dict], float]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps / fps_s))
    bg = background_model(path, w)
    prev, out = None, []
    for i in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            break
        h = int(fr.shape[0] * w / fr.shape[1])
        g = cv2.cvtColor(cv2.resize(fr, (w, h)), cv2.COLOR_BGR2GRAY)
        t = i / fps
        pos, resp = phase_offset(bg, g) if bg.shape == g.shape else ((0.0, 0.0), 0.0)
        if prev is not None:
            mask = np.full(g.shape, 255, np.uint8)
            for e in ents:                       # tracked entities are not background
                for o in e["obs"]:
                    if abs(o[0] - t) < 1.0 / fps_s / 2:
                        b = o[1]
                        mask[max(0, int((b[1] - .04) * h)):int((b[3] + .04) * h),
                             max(0, int((b[0] - .04) * w)):int((b[2] + .04) * w)] = 0
            if mask.mean() < 40:
                mask[:] = 255
            rec = {"t": t, "dx": 0.0, "dy": 0.0, "ls": 0.0, "n": 0, "cov": 0.0,
                   "pos": pos if resp > 0.5 else None}
            p0 = cv2.goodFeaturesToTrack(prev, 300, 0.01, 6, mask=mask)
            if p0 is not None and len(p0) >= 12:
                p1, st, _ = cv2.calcOpticalFlowPyrLK(prev, g, p0, None, winSize=(21, 21), maxLevel=3)
                good = st.reshape(-1) == 1
                if good.sum() >= 12:
                    M, inl = cv2.estimateAffinePartial2D(p0[good], p1[good], method=cv2.RANSAC,
                                                         ransacReprojThreshold=1.5)
                    if M is not None:
                        pts = p0[good][inl.reshape(-1) == 1].reshape(-1, 2)
                        cells = {(int(x * 4 / w), int(y * 4 / h)) for x, y in pts}
                        c = M @ np.array([w / 2, h / 2, 1.0])   # measure at the centre so scale does not leak in
                        rec.update(dx=float(c[0] - w / 2), dy=float(c[1] - h / 2),
                                   ls=float(np.log(max(np.hypot(M[0, 0], M[0, 1]), 1e-6))),
                                   n=int(good.sum()), cov=len(cells) / 16)
            out.append(rec)
        prev = g
    cap.release()
    return out, fps_s


ZOOM_FOCAL_RISE = 0.10   # relative focal growth over the window above which the lens, not the
                         # camera, is what moved. Measured: +0.19 zoom, -0.00 still, -0.08 dolly.


def _focal_trend(path, dur: float, samples: int = 8) -> dict | None:
    """Per-frame focal length and scene depth from DA3.

    A dolly and a zoom are the same 2D transform; they differ in 3D. Moving the camera forward
    leaves the lens alone and brings the scene nearer, while zooming changes the focal length and
    leaves the scene where it is."""
    try:
        from . import depth as DP
        from .sources import frame_at, open_source
    except Exception:
        return None
    try:
        src = open_source(path)
        f, d = [], []
        for t in np.linspace(0.3, max(0.4, dur - 0.3), samples):
            r = DP.mono(frame_at(src, float(t), 1024))
            if r["stats"]["focal_px"] is None:
                return None
            f.append(r["stats"]["focal_px"])
            d.append(r["stats"]["median"])
    except Exception:
        return None
    f, d = np.array(f, float), np.array(d, float)
    slope = float(np.polyfit(range(len(f)), f, 1)[0])
    return {"focal_spread": float(f.std() / max(f.mean(), 1e-9)),
            "focal_slope": slope,
            # The fitted focal change across the whole window, relative to the mean focal — i.e.
            # "the lens got this much longer". Signed, unlike the spread, which is the point.
            "focal_rise": float(slope * (len(f) - 1) / max(f.mean(), 1e-9)),
            "depth_slope": float(np.polyfit(range(len(d)), d, 1)[0]),
            "depth_range": float(np.ptp(d) / max(d.mean(), 1e-9))}


def classify(recs: list[dict], fps_s: float, w: int = 320, bridge_s: float = 10.0,
             focal: dict | None = None) -> list[tuple[float, str]]:
    anchors = [i for i, r in enumerate(recs) if r["pos"] is not None]
    for r in recs:
        r["locked"] = False
    for a, b in zip(anchors, anchors[1:]):
        pa, pb = recs[a]["pos"], recs[b]["pos"]
        if recs[b]["t"] - recs[a]["t"] <= bridge_s and np.hypot(pb[0] - pa[0], pb[1] - pa[1]) < 0.004 * w:
            for i in range(a, b + 1):
                recs[i]["locked"] = True
    # A scale change is a dolly unless the lens is what moved, and what says the lens moved is the
    # *direction* of the focal trend. This used to test `focal_spread`, a std/mean that cannot tell
    # a rise from a fall, together with a near-flat median depth. Measured against a true zoom built
    # by cropping real footage (1.00 -> 1.85, camera stationary), both clauses failed: the spread
    # read 0.053, under its own 0.08 bar, and the depth slope read -0.028, outside its 0.02 window.
    #
    # The depth clause was wrong in principle as well. Zooming in crops the frame to nearer content,
    # so the median depth of what is *visible* falls even though nothing in the scene moved. Depth
    # cannot arbitrate a zoom; the lens can. Signed focal rise across the window separates the three
    # cases with room to spare — zoom +0.19, static control -0.00, real dolly -0.08 — where DA3's
    # focal ratio was 1.22, 1.00 and 0.92 for a zoom that was 1.85 by construction. It underestimates
    # the magnitude badly and gets the sign right, so only the sign is trusted here.
    zoom = bool(focal and focal["focal_rise"] > ZOOM_FOCAL_RISE)
    out = []
    for r in recs:
        win = [q for q in recs if abs(q["t"] - r["t"]) <= 0.5]
        dx = np.mean([q["dx"] for q in win])
        dy = np.mean([q["dy"] for q in win])
        jit = np.std([np.hypot(q["dx"], q["dy"]) for q in win])
        ls = np.sum([q["ls"] for q in win]) / max(len(win) / fps_s, 1e-6)
        mag = np.hypot(dx, dy)
        cov = np.mean([q["cov"] for q in win])
        n = np.mean([q["n"] for q in win])
        anch = [q for q in win if q["pos"] is not None]
        if np.mean([q["locked"] for q in win]) >= 0.6:
            lab = "still"
        elif len(anch) >= 3:                      # absolute positions: read the drift directly
            (x0, y0), (x1, y1) = anch[0]["pos"], anch[-1]["pos"]
            ddx, ddy = x1 - x0, y1 - y0
            if np.hypot(ddx, ddy) < STILL_FRAC * w * 1.5:
                lab = "still"
            else:
                lab = (f"pan({'right' if ddx < 0 else 'left'})" if abs(ddx) >= abs(ddy)
                       else f"tilt({'down' if ddy < 0 else 'up'})")
        elif cov < MIN_COVERAGE or n < MIN_FEATURES:
            lab = "unknown"
        elif abs(ls) > DOLLY_LOG_SCALE:
            # G2's other half: in 2D a dolly and a zoom are the same transform, so without the
            # intrinsics that tell them apart the honest answer is that the scale changed and we
            # do not know which. Saying "dolly" here would be the 2D estimator claiming 3D.
            if focal is None:
                lab = "unknown"
            else:
                move = "zoom" if zoom else "dolly"
                lab = f"{move}({'in' if ls > 0 else 'out'})"
        elif mag < STILL_FRAC * w and jit < JITTER_FRAC * w:
            lab = "still"
        elif mag >= STILL_FRAC * w and mag > 1.5 * jit:
            lab = (f"pan({'right' if dx < 0 else 'left'})" if abs(dx) >= abs(dy)
                   else f"tilt({'down' if dy < 0 else 'up'})")
        else:
            lab = "handheld"
        out.append((r["t"], lab))
    return out


def emit(path, L, ents=(), duration: float = 0.0, use_depth: bool = True, min_run: float = 0.4) -> None:
    from .facts import T
    recs, fps_s = steps(path, ents)
    if not recs:
        return
    focal = _focal_trend(path, duration or recs[-1]["t"]) if use_depth else None
    L.c("producer: ransac on background features + background registration"
        + (" + da3 intrinsics (dolly vs zoom)" if focal else ""))
    if focal:
        L.fact(f"focal_stability({focal['focal_spread']:.3f})", "da3_small", 0.6)
    merged: list[list] = []
    for t, lab in classify(recs, fps_s, focal=focal):
        if merged and merged[-1][0] == lab:
            merged[-1][2] = t
        else:
            merged.append([lab, t, t])
    for lab, a, b in merged:
        if b - a < min_run:
            continue
        conf = 0.3 if lab == "unknown" else 0.85 if lab == "still" else 0.7
        L.fact(f"holds(camera({lab}), {T(a)}, {T(b + 1 / fps_s)})", "camera2", conf)
