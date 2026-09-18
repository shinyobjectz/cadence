"""G5, measured on keypoint layouts whose answer is arithmetic.

A pose is a set of points, so a test can simply build the points for a body that is sitting, or one
that has turned around, and check the word that comes out. That keeps the geometry honest without a
model in the loop; the model's own behaviour is checked separately against real clips, where the
expected answer comes from what the clip is of.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import landmarks as LM  # noqa: E402
from cadence_vision.facts import Log, parse_log  # noqa: E402

PEXELS = Path("/private/tmp/claude-501") / "-Users-shinyobjectz-cadence"


def body(*, seated=False, crouching=False, nose=1.0, l_eye=1.0, r_eye=1.0,
         l_ear=1.0, r_ear=1.0, nose_x=0.5) -> np.ndarray:
    """A COCO-17 body standing at x=0.5, with the head evidence dialled in per test."""
    P = np.zeros((17, 3))
    P[:, 2] = 1.0
    P[LM.NOSE] = [nose_x, 0.10, nose]
    P[LM.L_EYE] = [0.48, 0.09, l_eye]
    P[LM.R_EYE] = [0.52, 0.09, r_eye]
    P[LM.L_EAR] = [0.46, 0.10, l_ear]
    P[LM.R_EAR] = [0.54, 0.10, r_ear]
    P[LM.L_SH], P[LM.R_SH] = [0.42, 0.22, 1.0], [0.58, 0.22, 1.0]
    P[LM.L_HIP], P[LM.R_HIP] = [0.45, 0.50, 1.0], [0.55, 0.50, 1.0]
    knee_y = 0.60 if seated else 0.75            # torso is 0.28; a thigh under 0.154 reads as seated
    P[LM.L_KNEE], P[LM.R_KNEE] = [0.45, knee_y, 1.0], [0.55, knee_y, 1.0]
    ank_y = knee_y + (0.08 if crouching else 0.22)
    P[LM.L_ANK], P[LM.R_ANK] = [0.45, ank_y, 1.0], [0.55, ank_y, 1.0]
    return P


# ----------------------------------------------------------------------------- posture

def test_standing_and_seated_differ_by_thigh_over_torso():
    assert LM.posture(body()) == "standing"
    assert LM.posture(body(seated=True)) == "seated"


def test_crouching_is_a_folded_shin_not_a_folded_thigh():
    assert LM.posture(body(crouching=True)) == "crouching"


def test_posture_scales_with_the_person():
    """The ratio is the whole point: the same pose half as tall must give the same word."""
    for scale in (0.25, 0.5, 2.0):
        P = body()
        P[:, :2] = 0.5 + (P[:, :2] - 0.5) * scale
        assert LM.posture(P) == "standing", f"scale {scale}"


def test_a_body_with_no_legs_in_frame_says_nothing_about_posture():
    """A medium close-up has no knees. Silence beats guessing 'standing' for every interview."""
    P = body()
    P[[LM.L_KNEE, LM.R_KNEE], 2] = 0.1
    assert LM.posture(P) == "unknown"


def test_ankles_out_of_frame_still_allow_standing():
    P = body()
    P[[LM.L_ANK, LM.R_ANK], 2] = 0.1
    assert LM.posture(P) == "standing"


# ----------------------------------------------------------------------------- facing

def test_a_face_in_view_is_facing_camera():
    assert LM.facing(body()) == "camera"


def test_two_ears_and_no_face_is_the_back_of_a_head():
    """The measured failure. On the man clip the model put both ears at 0.90 and 0.80 on the back of
    his head while the nose sat at 0.08 and both eyes at 0.04; reading 'two ears' as 'face on' made
    a man walking away from the lens a man looking into it."""
    assert LM.facing(body(nose=0.08, l_eye=0.05, r_eye=0.03, l_ear=0.90, r_ear=0.80)) == "away"


def test_one_ear_is_a_profile_towards_the_side_on_show():
    assert LM.facing(body(l_ear=0.9, r_ear=0.1)) == "right"
    assert LM.facing(body(l_ear=0.1, r_ear=0.9)) == "left"


def test_a_turned_head_with_both_ears_is_read_from_the_nose_offset():
    assert LM.facing(body(nose_x=0.40)) == "left"
    assert LM.facing(body(nose_x=0.60)) == "right"
    assert LM.facing(body(nose_x=0.52)) == "camera"


def test_no_shoulders_means_no_answer():
    P = body()
    P[[LM.L_SH, LM.R_SH], 2] = 0.1
    assert LM.facing(P) == "unknown"


# ----------------------------------------------------------------------------- hands

def hand(spread: float, thumb_gap: float = 1.0) -> np.ndarray:
    """A hand with the wrist at the origin, fingers reaching `spread` knuckle-lengths out."""
    P = np.zeros((21, 3))
    for i in LM.MCPS:
        P[i] = [0.0, -0.10, 0.0]
    for i in LM.TIPS:
        P[i] = [0.0, -0.10 * spread, 0.0]
    P[4] = [0.10 * thumb_gap, -0.10 * spread, 0.0]      # thumb tip, offset sideways
    P[8] = [0.0, -0.10 * spread, 0.0]
    return P


def test_hand_state_reads_the_fingertip_reach():
    assert LM.hand_state(hand(2.0)) == "open"
    assert LM.hand_state(hand(1.4)) == "curled"
    assert LM.hand_state(hand(1.0)) == "closed"


def test_hand_state_is_scale_free():
    for scale in (0.2, 5.0):
        assert LM.hand_state(hand(2.0) * scale) == "open", f"scale {scale}"


def test_pinch_is_the_thumb_meeting_the_index():
    assert LM.pinching(hand(1.8, thumb_gap=0.2))
    assert not LM.pinching(hand(1.8, thumb_gap=1.5))


def test_hands_are_skipped_rather_than_crashing_when_unavailable(monkeypatch):
    """MediaPipe aborts the process on some builds, so the code must never reach it blindly."""
    monkeypatch.setitem(LM._CACHE, "hands_ok", False)
    assert LM.hands(np.zeros((64, 64, 3), np.uint8)) == []


# ----------------------------------------------------------------------------- attachment

def _ent(eid, cls, box):
    return {"id": eid, "cls": cls, "conf": 0.9, "seed_t": 0.0, "how": "detector",
            "obs": [(round(i / 10, 2), box, 0.05, [0.5, 0.5], None) for i in range(30)]}


def test_a_pose_attaches_to_the_overlapping_entity():
    ents = [_ent("e1", "woman", (0.1, 0.1, 0.5, 0.9)), _ent("e2", "camera", (0.6, 0.3, 0.9, 0.7))]
    assert LM.assign(ents, 1.0, (0.12, 0.12, 0.48, 0.88), LM.PERSONISH) == "e1"


def test_a_pose_over_nothing_tracked_is_dropped():
    """A frame of the handoff clip holding only a gloved hand produced a confident seated person.
    Nothing was tracked there as a person, so nothing reaches the log — which is why the gate is
    the entity list and not the model's own score."""
    ents = [_ent("e1", "bottle", (0.55, 0.2, 0.73, 0.68))]
    assert LM.assign(ents, 1.0, (0.55, 0.2, 0.73, 0.68), LM.PERSONISH) is None


def test_a_pose_at_a_time_the_entity_was_not_seen_is_dropped():
    ents = [_ent("e1", "woman", (0.1, 0.1, 0.5, 0.9))]
    assert LM.assign(ents, 9.0, (0.1, 0.1, 0.5, 0.9), LM.PERSONISH) is None


# ----------------------------------------------------------------------------- derived confidence

def test_confidence_is_how_much_the_run_agreed():
    L = Log()
    seq = [(i * 0.2, "camera") for i in range(8)]
    seq[3] = (0.6, "left")
    LM._emit_runs(L, "e1", "facing", seq, 2.0, 0.2)
    srcs = [f for f in parse_log(L.text()) if f[0] == "src"]
    assert srcs and all(f[2] == "yolo_pose" for f in srcs)
    assert any(f[3] == pytest.approx(0.88, abs=0.02) for f in srcs), L.text()


def test_unknown_states_are_not_claimed():
    L = Log()
    LM._emit_runs(L, "e1", "posture", [(i * 0.2, "unknown") for i in range(8)], 2.0, 0.2)
    assert L.text().strip() == ""


# ----------------------------------------------------------------------------- real clips

CLIPS = sorted(PEXELS.glob("*/scratchpad/pexels/*.mp4"))


@pytest.mark.skipif(not CLIPS, reason="no local footage")
@pytest.mark.parametrize("stem,want", [("man_6002525", "away"), ("woman_9032610", "camera")])
def test_facing_on_real_clips(stem, want):
    """The man clip is a back view; the woman clip is a piece to camera. Both are unambiguous."""
    clip = next((c for c in CLIPS if c.stem == stem), None)
    if clip is None:
        pytest.skip(f"{stem} not present")
    from cadence_vision.sources import frame_at, open_source
    src = open_source(clip)
    got = [b["facing"] for t in (1.0, src.duration / 2, src.duration - 1.0)
           for b in LM.bodies(frame_at(src, t, 960))]
    assert got and all(g == want for g in got), got


# ----------------------------------------------------------------------------- hands, ONNX path

class _Det:
    """Stands in for the palm detector: returns whatever palms it was given."""

    def __init__(self, palms):
        self.palms = palms

    def infer(self, img):
        return self.palms


class _Pose:
    """Stands in for the landmarker, emitting the upstream layout: 4 box values, then 21 screen
    points of (x, y, z) in pixels, then 63 metric values, handedness, confidence."""

    def __init__(self, pts_px, handedness=0.9):
        self.pts_px, self.handedness = pts_px, handedness

    def infer(self, img, palm):
        pts = np.zeros((21, 3))
        pts[:, :2] = self.pts_px
        return np.concatenate([[0, 0, 1, 1], pts.reshape(-1), np.zeros(63),
                               [self.handedness, 0.95]])


def _wire_hands(monkeypatch, pts_px, palms=(1,), handedness=0.9):
    monkeypatch.setattr(LM, "hands_available", lambda: True)
    monkeypatch.setattr(LM, "hand_model", lambda: (_Det(list(palms)), _Pose(pts_px, handedness)))


def _fist(cx, cy, scale):
    """21 points shaped like a hand, placed at (cx, cy) in pixels."""
    P = np.zeros((21, 2))
    P[0] = (0.0, 0.0)                                   # wrist
    for i, k in enumerate(LM.MCPS):
        P[k] = (0.2 + 0.1 * i, 0.5)
    for i, k in enumerate(LM.TIPS):
        P[k] = (0.2 + 0.1 * i, 1.4)
    P[4], P[8] = (0.6, 1.0), (0.2, 1.4)
    return P * scale + np.array([cx, cy])


def test_hand_points_are_normalised_to_the_frame(monkeypatch):
    """The contract the rest of the module depends on: every reading below is a ratio, and a ratio
    that changes with the frame size is not a fact about the hand."""
    frame = np.zeros((480, 640, 3), np.uint8)
    _wire_hands(monkeypatch, _fist(100, 200, 80))
    got = LM.hands(frame)
    assert len(got) == 1
    P = got[0]["pts"]
    assert P[:, 0].max() <= 1.0 and P[:, 1].max() <= 1.0
    assert 0.1 < P[LM.WRIST, 0] < 0.3 and 0.3 < P[LM.WRIST, 1] < 0.5


def test_the_same_hand_reads_the_same_at_any_frame_size(monkeypatch):
    states = []
    for w, h in ((640, 480), (1920, 1440)):
        _wire_hands(monkeypatch, _fist(0.15 * w, 0.4 * h, 0.12 * w))
        states.append(LM.hands(np.zeros((h, w, 3), np.uint8))[0]["state"])
    assert states[0] == states[1], states


def test_handedness_follows_the_upstream_convention(monkeypatch):
    frame = np.zeros((480, 640, 3), np.uint8)
    _wire_hands(monkeypatch, _fist(100, 200, 80), handedness=0.9)
    assert LM.hands(frame)[0]["side"] == "right"
    _wire_hands(monkeypatch, _fist(100, 200, 80), handedness=0.1)
    assert LM.hands(frame)[0]["side"] == "left"


def test_no_palm_means_no_hand_rather_than_a_guess(monkeypatch):
    """X2. The handoff clip is two rubber gloves backlit against a blown-out rectangle and the palm
    detector returns nothing on it at any usable threshold. Nothing is the right answer."""
    _wire_hands(monkeypatch, _fist(100, 200, 80), palms=())
    assert LM.hands(np.zeros((480, 640, 3), np.uint8)) == []


def test_a_landmarker_that_declines_is_not_a_hand(monkeypatch):
    monkeypatch.setattr(LM, "hands_available", lambda: True)

    class _Empty:
        def infer(self, img, palm):
            return None

    monkeypatch.setattr(LM, "hand_model", lambda: (_Det([1]), _Empty()))
    assert LM.hands(np.zeros((480, 640, 3), np.uint8)) == []


def test_without_the_models_the_producer_is_silent(monkeypatch):
    monkeypatch.setattr(LM, "hands_available", lambda: False)
    assert LM.hands(np.zeros((480, 640, 3), np.uint8)) == []


# ----------------------------------------------------------------------------- palm orientation

def _palm(index_dx, little_dx, up=-1.0):
    """A hand at the origin with its two knuckle vectors leaving the wrist.

    Screen y grows downward, so `up=-1` is fingers-up. Which side the index knuckle sits on is the
    whole question: on a right hand seen palm-on the index is to screen-left of the little finger,
    and on the back of that same hand it is to the right.
    """
    P = np.zeros((21, 3))
    P[LM.WRIST] = (0.0, 0.0, 0.0)
    P[LM.INDEX_MCP] = (index_dx, up, 0.0)
    P[LM.LITTLE_MCP] = (little_dx, up, 0.0)
    return P


def test_a_right_palm_held_up_to_the_lens_reads_as_camera():
    assert LM.palm_facing(_palm(-0.3, 0.3), "right") == "camera"


def test_the_back_of_that_same_right_hand_reads_as_away():
    assert LM.palm_facing(_palm(0.3, -0.3), "right") == "away"


def test_a_left_hand_is_the_mirror_of_a_right_one():
    """Handedness flips the sign, because a left hand is a mirrored right one. Reading the winding
    without knowing which hand it is would call every left palm a knuckle."""
    P = _palm(-0.3, 0.3)
    assert LM.palm_facing(P, "right") == "camera"
    assert LM.palm_facing(P, "left") == "away"


def test_an_edge_on_hand_has_no_facing_to_report():
    """X2. Seen edge-on the two knuckle vectors are nearly parallel and the winding says nothing."""
    assert LM.palm_facing(_palm(0.0, 0.001), "right") == "edge"


def test_a_collapsed_hand_is_edge_rather_than_a_guess():
    assert LM.palm_facing(np.zeros((21, 3)), "right") == "edge"


def test_palm_facing_survives_the_hand_being_upside_down():
    """Fingers down is still a palm; only the winding decides, and it is unchanged by rotation."""
    assert LM.palm_facing(_palm(0.3, -0.3, up=1.0), "right") == "camera"


def test_an_open_palm_to_the_lens_is_two_separate_readings(monkeypatch):
    """G5's gesture, as the grammar has it: `hand_state(e, open)` and `palm(e, camera)` stay
    orthogonal fluents, so a rule can ask for either or both rather than a fused word."""
    frame = np.zeros((480, 640, 3), np.uint8)
    P = _fist(100, 200, 80)
    P[LM.INDEX_MCP] = (100 - 24, 200 - 80)
    P[LM.LITTLE_MCP] = (100 + 24, 200 - 80)
    _wire_hands(monkeypatch, P, handedness=0.9)
    h = LM.hands(frame)[0]
    assert h["side"] == "right" and h["palm"] == "camera"
    assert h["state"] in ("open", "curled", "closed")


def test_each_fluent_is_attributed_to_the_model_that_saw_it():
    """X2: provenance has to name the right producer, or cross-checking it means nothing.

    `posture`/`facing` are body keypoints; `hand_state`/`palm` are the hand landmarker, which is a
    different model with different failure modes — it declines gloved hands outright while the pose
    model handles them. They were all being stamped `yolo_pose`.
    """
    from cadence_vision.facts import Log
    L = Log()
    seq = [(t / 10, "open") for t in range(12)]
    for fluent in ("posture", "facing", "hand_state", "palm"):
        LM._emit_runs(L, "e1", fluent, seq, dur=2.0, step=0.1)
    lines = L.text().splitlines()
    got = {}
    for line in lines:
        if line.startswith("src(holds("):
            fluent = line.split("holds(")[1].split("(")[0]
            got[fluent] = line.rsplit(", ", 2)[-2]
    assert got["posture"] == "yolo_pose" and got["facing"] == "yolo_pose"
    assert got["hand_state"] == "mediapipe_hands" and got["palm"] == "mediapipe_hands", got
