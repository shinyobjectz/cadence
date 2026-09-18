"""G5. Bodies and hands, read as states rather than as coordinates.

Seventeen points per person is not something a reader of the log can act on, and dumping them in
would be exactly the numeric noise the grammar exists to avoid. What an editor needs from a body is
which way it is turned and whether it is standing or sitting; what it needs from a hand is whether
it is holding something and when it let go. Those are the facts emitted here:

    holds(posture(e, seated), 0.000, 6.400).
    holds(facing(e, camera), 0.000, 3.100).
    holds(gripping(e), 1.200, 4.800).
    happens(release(e), 4.800).

The keypoints stay inside this module. Every decision is a ratio between two of them — thigh length
against torso length, nose offset against shoulder width — which says the same thing about a person
across the street as about one filling the frame, where a pixel threshold would not.

X1: the pose model reports a per-keypoint score, and it is not what reaches the log. A state claimed
over an interval is confident to the degree that the interval's samples agreed on it, which is a
measurement of how legible the pose was rather than a model's opinion of itself.

Hands come from MediaPipe's models without MediaPipe's runtime. The `mediapipe` package aborts the
process on this macOS build — `DrishtiMetalHelper ... Service is unavailable`, from a Metal-backed
calculator inside the detector subgraph, which neither the CPU delegate nor `MEDIAPIPE_DISABLE_GPU`
avoids, and an abort cannot be caught. The same two networks exported to ONNX run under OpenCV's
DNN backend with no Metal anywhere near them, so the palm detector and the 21-point landmarker work
here after all (`vendor/`). Grip state is what gives a handoff its timing.

What they do not solve is a hand the palm detector cannot see. On a bare hand it is confident —
0.91 and 0.99 on the woman clip, landmarks on the hand. On the handoff clip, which is two rubber
gloves backlit against a blown-out rectangle, it returns nothing at all down to a score threshold of
0.3, and that is the honest answer rather than a failure: a palm detector trained on skin has no
opinion about a green glove. Where no hand is found the log says nothing about hands.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np

from . import CACHE
from .facts import T, agreed_runs
from .profile import timed

SAMPLE_FPS = 5.0
POSE_WEIGHTS = "yolo11n-pose.pt"
DEVICE = "mps"
MIN_KP = 0.5                       # a keypoint below this is not where the model says it is
MIN_RUN = 0.4
MODELS = CACHE / "models"
PALM_URL = ("https://huggingface.co/opencv/palm_detection_mediapipe/resolve/main/"
            "palm_detection_mediapipe_2023feb.onnx")
HANDPOSE_URL = ("https://huggingface.co/opencv/handpose_estimation_mediapipe/resolve/main/"
                "handpose_estimation_mediapipe_2023feb.onnx")
PALM_MIN = 0.6           # below this the detector is guessing; measured against gloves, which it
                         # declines to see at any threshold worth using

# COCO-17, the layout every pose model in this family emits.
NOSE, L_EYE, R_EYE, L_EAR, R_EAR = 0, 1, 2, 3, 4
L_SH, R_SH, L_ELB, R_ELB, L_WR, R_WR = 5, 6, 7, 8, 9, 10
L_HIP, R_HIP, L_KNEE, R_KNEE, L_ANK, R_ANK = 11, 12, 13, 14, 15, 16

_CACHE: dict[str, object] = {}


# ----------------------------------------------------------------------------- bodies

@timed("yolo_pose.load")
def pose_model():
    if "pose" not in _CACHE:
        from ultralytics import YOLO
        MODELS.mkdir(parents=True, exist_ok=True)
        w = MODELS / POSE_WEIGHTS
        _CACHE["pose"] = YOLO(str(w) if w.exists() else POSE_WEIGHTS)
    return _CACHE["pose"]


@timed("yolo_pose.frame")
def bodies(frame: np.ndarray, conf: float = 0.35) -> list[dict]:
    """[{pts: (17,3) normalized x,y,score, posture, facing, box}] for one RGB frame.

    Nothing is filtered on how many keypoints look confident, tempting as it is. On a frame of the
    handoff clip holding only a gloved hand and its shadow, the model invented a whole seated person
    with 13 of 17 keypoints above 0.5, while the real man walking away in another clip had 4. The
    count measures how plausible the shape was, not whether a person was there. What keeps the
    invented body out of the log is `assign`: pose facts attach to entities the tracker already
    found, and a clip with no person entity gets no posture facts."""
    r = pose_model().predict(frame, verbose=False, device=DEVICE, conf=conf)[0]
    if r.keypoints is None or len(r.keypoints.data) == 0:
        return []
    H, W = frame.shape[:2]
    out = []
    for kp, box in zip(r.keypoints.data.cpu().numpy(), r.boxes.xyxyn.cpu().numpy()):
        P = kp.astype(float)
        P[:, 0] /= W
        P[:, 1] /= H
        out.append({"pts": P, "posture": posture(P), "facing": facing(P),
                    "box": tuple(float(v) for v in box)})
    return out


def _ok(P: np.ndarray, *idx: int) -> bool:
    return all(P[i, 2] >= MIN_KP for i in idx)


def _mid(P: np.ndarray, a: int, b: int) -> np.ndarray:
    return P[[a, b], :2].mean(0)


def posture(P: np.ndarray) -> str:
    """standing / seated / crouching, from how the thigh compares with the torso.

    Sitting foreshortens the hip-to-knee segment against the shoulder-to-hip one, and it does so
    whatever the camera height or the lens, where an absolute knee position would not."""
    if not _ok(P, L_SH, R_SH, L_HIP, R_HIP, L_KNEE, R_KNEE):
        return "unknown"
    hip, sh, knee = _mid(P, L_HIP, R_HIP), _mid(P, L_SH, R_SH), _mid(P, L_KNEE, R_KNEE)
    torso = float(np.linalg.norm(sh - hip))
    if torso < 1e-6:
        return "unknown"
    if float(np.linalg.norm(knee - hip)) / torso < 0.55:
        return "seated"
    if not _ok(P, L_ANK, R_ANK):
        return "standing"
    shin = float(np.linalg.norm(_mid(P, L_ANK, R_ANK) - knee))
    return "crouching" if shin / torso < 0.5 else "standing"


def facing(P: np.ndarray) -> str:
    """camera / left / right / away, from which side of the head the model can see.

    G8 in its cheap form. A gaze model reads where the eyes point; this reads where the head points,
    which is what "she turns away" means in an edit. Both ears in view is a face-on head; one ear is
    a profile, and the visible ear is the side being shown; no face at all is the back of a head."""
    if not _ok(P, L_SH, R_SH):
        return "unknown"
    le, re = P[L_EAR, 2] >= MIN_KP, P[R_EAR, 2] >= MIN_KP
    # The face, not the ears, says whether a head is turned towards the camera. A pose model places
    # both ears confidently on the *back* of a head — on the man clip, ears at 0.90 and 0.80 with
    # the nose at 0.08 and both eyes at 0.04 — so reading "two ears" as "face on" calls a man
    # walking away from the lens a man looking into it.
    if not (_ok(P, NOSE) or _ok(P, L_EYE) or _ok(P, R_EYE)):
        return "away"
    if le == re:                                  # both ears, or neither: a head squarely on
        sh = P[[L_SH, R_SH], :2]
        w = float(abs(sh[0, 0] - sh[1, 0]))
        if w < 1e-6 or not _ok(P, NOSE):
            return "camera"
        off = (float(P[NOSE, 0]) - sh.mean(0)[0]) / w
        return "camera" if abs(off) < 0.25 else ("left" if off < 0 else "right")
    return "right" if le else "left"      # seeing the left ear means the left side is presented


# ----------------------------------------------------------------------------- hands

def hands_available() -> bool:
    """Whether both networks are in hand.

    This used to run a detection in a throwaway subprocess, because the `mediapipe` package aborts
    rather than raising and takes the asking process with it. The ONNX pair cannot abort, so the
    question is only whether the weights are here and OpenCV can read them, which is a question a
    try/except can answer."""
    if "hands_ok" not in _CACHE:
        try:
            import cv2                                      # noqa: F401
            _weights(PALM_URL)
            _weights(HANDPOSE_URL)
            _CACHE["hands_ok"] = True
        except Exception:                                   # noqa: BLE001
            _CACHE["hands_ok"] = False
    return bool(_CACHE["hands_ok"])


def _weights(url: str) -> Path:
    p = MODELS / url.rsplit("/", 1)[1]
    if not p.exists():
        import urllib.request
        MODELS.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(url, p)
    return p


WRIST = 0
TIPS = (8, 12, 16, 20)             # index, middle, ring, little
MCPS = (5, 9, 13, 17)              # knuckles


@timed("hands.load")
def hand_model():
    if "hand" not in _CACHE:
        from .vendor.mp_handpose import MPHandPose
        from .vendor.mp_palmdet import MPPalmDet
        _CACHE["hand"] = (
            MPPalmDet(modelPath=str(_weights(PALM_URL)), nmsThreshold=0.3,
                      scoreThreshold=PALM_MIN),
            MPHandPose(modelPath=str(_weights(HANDPOSE_URL)), confThreshold=0.5))
    return _CACHE["hand"]


@timed("hands.frame")
def hands(frame: np.ndarray, n: int = 2) -> list[dict]:
    """Up to `n` hands as {pts, state, pinch, side, box}, `pts` normalised to the frame.

    Coordinates are normalised because every reading below is a ratio between two of them, and a
    ratio that changes with the frame size is not a fact about the hand."""
    if not hands_available():
        return []
    det, pose = hand_model()
    img = np.ascontiguousarray(frame[:, :, ::-1])           # both expect BGR
    H, W = img.shape[:2]
    palms = det.infer(img)
    palms = [] if palms is None or len(palms) == 0 else list(palms)
    out = []
    for palm in palms[:n]:
        got = pose.infer(img, palm)
        if got is None or not len(got):
            continue
        # 0:4 is the box and 67:130 the metric-space points; 4:67 is the 21 screen landmarks.
        P = np.asarray(got[4:67], float).reshape(21, 3)
        P[:, 0] /= max(W, 1)
        P[:, 1] /= max(H, 1)
        side = "left" if float(got[-2]) <= 0.5 else "right"
        out.append({"pts": P, "state": hand_state(P), "pinch": pinching(P),
                    "side": side, "palm": palm_facing(P, side),
                    "box": (float(P[:, 0].min()), float(P[:, 1].min()),
                            float(P[:, 0].max()), float(P[:, 1].max()))})
    return out


def _span(P: np.ndarray) -> float:
    return float(np.linalg.norm(P[list(MCPS), :2] - P[WRIST, :2], axis=1).mean())


def hand_state(P: np.ndarray) -> str:
    """open / curled / closed, from how far the fingertips sit from the wrist, over hand span.

    The ratio is scale-free, so it says the same thing about a hand across the room as about one
    filling the frame; a pixel threshold would call every distant hand a fist."""
    span = _span(P)
    if span < 1e-6:
        return "unknown"
    reach = float(np.linalg.norm(P[list(TIPS), :2] - P[WRIST, :2], axis=1).mean()) / span
    return "closed" if reach < 1.25 else "open" if reach > 1.6 else "curled"


INDEX_MCP, LITTLE_MCP = 5, 17
MIN_PALM = 0.15          # below this the hand is edge-on and its facing is not readable


def palm_facing(P: np.ndarray, side: str) -> str:
    """camera / away / edge — which face of the hand is turned to the lens.

    The two knuckle vectors leaving the wrist run index-ward and little-ward. Seen from the palm
    side they wind one way round, from the back the other, so the sign of their cross product says
    which face is showing — and it flips with handedness, because a left hand is a mirrored right
    one. Near zero the hand is edge-on, where there is no answer to give rather than a wrong one.

    This is the half of "an open palm toward the lens" that is about orientation; `hand_state` is
    the half about the fingers, and the two stay separate fluents so a rule can ask for either."""
    v1 = P[INDEX_MCP, :2] - P[WRIST, :2]
    v2 = P[LITTLE_MCP, :2] - P[WRIST, :2]
    scale = float(np.linalg.norm(v1) * np.linalg.norm(v2))
    if scale < 1e-9:
        return "edge"
    z = float(v1[0] * v2[1] - v1[1] * v2[0])
    if abs(z) / scale < MIN_PALM:
        return "edge"
    # Screen y grows downward, so for a right hand a positive cross product is the palm side.
    return "camera" if ((z > 0) == (side != "left")) else "away"


def pinching(P: np.ndarray) -> bool:
    """Thumb tip meeting the index tip: how a small object is actually held."""
    span = _span(P)
    return span > 1e-6 and float(np.linalg.norm(P[4, :2] - P[8, :2])) / span < 0.45


# ----------------------------------------------------------------------------- emit

PERSONISH = ("person", "man", "woman", "driver", "speaker")


def _iou(a, b) -> float:
    iw = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    ih = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = iw * ih
    u = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / u if u > 0 else 0.0


def assign(ents: list[dict], t: float, box, kinds: tuple[str, ...], min_iou: float = 0.2) -> str | None:
    """Which tracked entity this body or hand belongs to, by box overlap at the same instant.

    A pose that cannot be attached to an entity is dropped. A state with no subject is not something
    a reader can act on, and inventing an entity to carry it would put two names on one thing."""
    best, who = 0.0, None
    for e in ents:
        if kinds and not any(k in e["cls"] for k in kinds):
            continue
        near = [o for o in e["obs"] if abs(o[0] - t) < 0.12]
        if not near:
            continue
        v = _iou(near[0][1], box)
        if v > best:
            best, who = v, e["id"]
    return who if best > min_iou else None


# Which model actually saw each fluent. `posture` and `facing` are read off body keypoints;
# `hand_state` and `palm` come from the hand landmarker, which is a different model that fails in
# different places — it declines gloves entirely, where the pose model is fine. Labelling all four
# `yolo_pose` misattributes half of them, and provenance a reader cannot trust is worse than none.
PRODUCER = {"posture": "yolo_pose", "facing": "yolo_pose",
            "hand_state": "mediapipe_hands", "palm": "mediapipe_hands"}


def _emit_runs(L, eid: str, fluent: str, seq: list[tuple[float, str]], dur: float, step: float) -> None:
    for v, a, b, conf in agreed_runs(seq, MIN_RUN):
        if v == "unknown":
            continue
        L.fact(f"holds({fluent}({eid}, {v}), {T(a)}, {T(min(b + step, dur))})",
               PRODUCER.get(fluent, "yolo_pose"), conf)


def emit(clip, L, ents: list[dict], duration: float | None = None, fps_s: float = SAMPLE_FPS,
         want_hands: bool = True, log=lambda s: None) -> int:
    """Body and hand states for entities already tracked. Returns how many entities got facts."""
    from .sources import frame_at, open_source
    src = open_source(Path(clip))
    dur = duration or src.duration
    step = 1.0 / fps_s
    use_hands = want_hands and any("hand" in e["cls"] for e in ents) and hands_available()
    if want_hands and not use_hands:
        log("hand landmarks unavailable here; no grip facts")

    states: dict[tuple[str, str], list[tuple[float, str]]] = {}
    grips: dict[str, list[tuple[float, bool]]] = {}
    for t in np.arange(0.1, dur, step):
        t = round(float(t), 3)
        try:
            fr = frame_at(src, t, 960)
        except Exception as e:                              # noqa: BLE001
            log(f"landmarks: no frame at {t:.2f}s ({type(e).__name__})")
            continue
        for b in bodies(fr):
            who = assign(ents, t, b["box"], PERSONISH)
            if who:
                states.setdefault((who, "posture"), []).append((t, b["posture"]))
                states.setdefault((who, "facing"), []).append((t, b["facing"]))
        if use_hands:
            for h in hands(fr):
                who = assign(ents, t, h["box"], ("hand",))
                if who:
                    states.setdefault((who, "hand_state"), []).append((t, h["state"]))
                    states.setdefault((who, "palm"), []).append((t, h["palm"]))
                    grips.setdefault(who, []).append((t, h["state"] == "closed" or h["pinch"]))
    if not states:
        return 0
    L.c("producer: yolo pose keypoints read as states; confidence = agreement over the run")
    for (eid, fluent), seq in sorted(states.items()):
        _emit_runs(L, eid, fluent, seq, dur, step)
    for eid, seq in sorted(grips.items()):
        # A grip closing and opening is what a hand did, and it is the timing a handoff needs.
        # X1: how confident a grip is, is how consistently the samples over it agreed the hand was
        # closed — not a number chosen here. Its edges inherit it, because a grasp is only as well
        # timed as the run it opens.
        for v, a, b, conf in agreed_runs(seq, MIN_RUN):
            if not v:
                continue
            L.fact(f"holds(gripping({eid}), {T(a)}, {T(min(b + step, dur))})", "mediapipe_hands", conf)
            if a > seq[0][0] + step:
                L.fact(f"happens(grasp({eid}), {T(a)})", "mediapipe_hands", conf)
            if b + step < seq[-1][0]:
                L.fact(f"happens(release({eid}), {T(b + step)})", "mediapipe_hands", conf)
    return len({e for e, _f in states})


def _r(b) -> tuple:
    return tuple(round(v, 2) for v in b)


if __name__ == "__main__":
    from .sources import frame_at, open_source
    src = open_source(Path(sys.argv[1]))
    ts = [float(x) for x in sys.argv[2:]] or [src.duration / 2]
    print(f"hand landmarker available: {hands_available()}")
    for t in ts:
        fr = frame_at(src, t, 960)
        for b in bodies(fr):
            print(f"{t:6.2f} body posture={b['posture']:9s} facing={b['facing']:7s} box={_r(b['box'])}")
        for h in hands(fr):
            print(f"{t:6.2f} hand {h['side'] or '?':5s} {h['state']:7s} pinch={h['pinch']}")
