"""Seeding and propagation, in the parts that do not need a GPU.

The seeder and SAM 2 are measured end to end by the clip eval; what is unit-tested here is the
bookkeeping around them, because that is where a silent wrong answer comes from: a track that
crosses a cut, a duplicate seed counted as a second object, a reply parsed into the wrong box.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import tracking as TR  # noqa: E402

BOUNDS = [0.0, 3.0, 6.0, 9.0]


# ----------------------------------------------------------------------------- shots

@pytest.mark.parametrize("t,want", [(0.0, (0.0, 3.0)), (2.99, (0.0, 3.0)), (3.0, (3.0, 6.0)),
                                    (5.5, (3.0, 6.0)), (8.9, (6.0, 9.0))])
def test_shot_of_finds_the_containing_shot(t, want):
    assert TR.shot_of(t, BOUNDS, 9.0) == want


def test_a_seed_past_the_last_cut_lands_in_the_last_shot():
    assert TR.shot_of(9.5, BOUNDS, 9.0) == (6.0, 9.0)


def test_without_cut_times_the_clip_is_one_shot():
    assert TR.shot_of(4.0, None, 9.0) == (0.0, 9.0)
    assert TR.shot_of(4.0, [], 9.0) == (0.0, 9.0)


def test_propagation_is_asked_for_the_shot_not_the_clip(monkeypatch):
    """SAM 2 does not know what a cut is: given the whole clip it drags the mask through one and
    reports a three-shot assembly as a single track of nonsense."""
    spans = []

    def fake_segment(clip, t0, t1, reverse, out):
        spans.append((round(t0, 2), round(t1, 2), reverse))

    monkeypatch.setattr(TR, "_segment", fake_segment)
    monkeypatch.setattr(TR, "_propagate", lambda v, b, imgsz=1024: [])
    det = {"cls": "person", "conf": 0.9, "t": 4.5, "box": [0.2, 0.2, 0.5, 0.8]}
    TR.propagate_one(Path("x.mp4"), det, 9.0, 1280, 720, shot=(3.0, 6.0))
    assert (4.5, 6.0, False) in spans, spans
    assert (3.0, 4.5, True) in spans, spans
    assert not any(t1 > 6.0 or t0 < 3.0 for t0, t1, _ in spans)


def test_a_seed_at_the_start_of_its_shot_is_not_propagated_backwards(monkeypatch):
    spans = []
    monkeypatch.setattr(TR, "_segment",
                        lambda c, a, b, r, o: spans.append((round(a, 2), round(b, 2), r)))
    monkeypatch.setattr(TR, "_propagate", lambda v, b, imgsz=1024: [])
    det = {"cls": "person", "conf": 0.9, "t": 3.05, "box": [0.2, 0.2, 0.5, 0.8]}
    TR.propagate_one(Path("x.mp4"), det, 9.0, 1280, 720, shot=(3.0, 6.0))
    assert [s for s in spans if s[2]] == [], "nothing to reach backwards into"


# ----------------------------------------------------------------------------- bookkeeping

def _track(cls, t0, n, box, mask=None):
    m = np.ones((8, 8), bool) if mask is None else mask
    return {"cls": cls, "conf": 0.9, "seed_t": t0, "how": "detector",
            "obs": [(round(t0 + i / 10, 2), box, 0.05, [0.35, 0.5], m) for i in range(n)]}


def test_a_detection_already_inside_a_track_is_not_seeded_again():
    tr = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    assert TR._covered({"cls": "person", "t": 1.0, "box": [0.21, 0.21, 0.49, 0.79]}, [tr])


def test_a_detection_elsewhere_in_frame_is_a_new_object():
    tr = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    assert not TR._covered({"cls": "person", "t": 1.0, "box": [0.6, 0.2, 0.9, 0.8]}, [tr])


def test_two_seeds_on_one_object_collapse_to_one_track():
    a = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    b = _track("person", 0.5, 20, (0.21, 0.21, 0.49, 0.79))
    assert len(TR._dedupe([a, b], lambda s: None)) == 1


def test_tracks_in_different_shots_are_never_merged():
    """They share no timestamps, so there is no evidence they are the same thing — that question
    belongs to `identity`, which has a calibrated way to answer it."""
    a = _track("person", 0.0, 20, (0.2, 0.2, 0.5, 0.8))
    b = _track("person", 6.0, 20, (0.2, 0.2, 0.5, 0.8))
    assert len(TR._dedupe([a, b], lambda s: None)) == 2


def test_two_people_side_by_side_stay_two_tracks():
    a = _track("person", 0.0, 30, (0.1, 0.2, 0.4, 0.8), np.zeros((8, 8), bool))
    m = np.zeros((8, 8), bool)
    m[:, 4:] = True
    b = _track("person", 0.0, 30, (0.6, 0.2, 0.9, 0.8), m)
    assert len(TR._dedupe([a, b], lambda s: None)) == 2


# ----------------------------------------------------------------------------- vlm replies

def test_a_reply_is_read_in_both_coordinate_orders():
    """Models disagree on whether box_2d is xyxy or yxyx, so both readings are kept and the one
    that agrees with the other model wins."""
    got = TR._boxes_from_reply('[{"label": "camera", "box_2d": [100, 200, 300, 500]}]')
    assert len(got) == 1 and len(got[0]["cands"]) == 2
    assert (0.1, 0.2, 0.3, 0.5) in got[0]["cands"]
    assert (0.2, 0.1, 0.5, 0.3) in got[0]["cands"]


def test_a_box_that_is_inside_out_in_both_orders_offers_no_candidate():
    got = TR._boxes_from_reply('[{"label": "x", "box_2d": [300, 200, 100, 500]}]')
    assert got[0]["cands"] == [], "neither reading gives a box with positive width and height"


def test_prose_around_the_json_does_not_defeat_the_parse():
    got = TR._boxes_from_reply('Sure! Here it is:\n[{"label": "a", "box_2d": [1, 2, 3, 4]}]\nHope that helps.')
    assert got and got[0]["label"] == "a"


def test_a_reply_with_no_json_yields_nothing():
    assert TR._boxes_from_reply("I cannot see a camera in this frame.") == []
    assert TR._boxes_from_reply("") == []


# ----------------------------------------------------------------------------- time budget

def test_grounding_calls_are_bounded(monkeypatch):
    """An optional seeder must not be able to hold a perception run hostage. One clip spent most
    of an hour here when a provider accepted the request and hung up mid-response: the socket sat
    in CLOSE_WAIT and the harness default of three 180-second attempts applied per model, per
    target. Losing a seed costs one entity's facts; hanging costs every producer behind it."""
    seen = []

    class _E:
        MODELS = {"qwen": "q", "gemini": "g"}

        @staticmethod
        def call(model, parts, key, timeout=180, retries=3):
            seen.append((timeout, retries))
            return {"text": ""}

        @staticmethod
        def _b64(im):
            return "data:image/png;base64,"

    import sys as _sys
    import types
    monkeypatch.setitem(_sys.modules, "cadence_vision.evalrun", _E)
    fake = types.ModuleType("cadence_vision.sources")
    fake.open_source = lambda p: object()
    fake.frame_at = lambda src, t: np.zeros((32, 32, 3), np.uint8)
    monkeypatch.setitem(_sys.modules, "cadence_vision.sources", fake)

    TR.ground(Path("x.mp4"), 1.0, ["steering wheel"])
    assert seen, "the seeder should have asked both models"
    assert all(t <= TR.GROUND_TIMEOUT and r <= TR.GROUND_RETRIES for t, r in seen), seen
    assert TR.GROUND_TIMEOUT * TR.GROUND_RETRIES * len(seen) < 600, "worst case stays under 10 min"


# ----------------------------------------------------------------------------- derived confidence

def _det(cls, t, box, conf=0.9):
    return {"cls": cls, "conf": conf, "t": t, "box": box}


def test_support_measures_agreement_with_independent_detections():
    """X1. A seed's score is the detector's opinion of one frame; a track that slid off its object
    two seconds later carries that lucky 0.95 with it. Agreement at times the tracker was never
    told about is a measurement of the track."""
    tr = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    dets = [_det("person", t, [0.21, 0.21, 0.49, 0.79]) for t in (0.5, 1.0, 1.5, 2.0)]
    assert TR.support(tr, dets) == pytest.approx(1.0)


def test_a_track_the_detector_never_confirms_scores_zero():
    tr = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    dets = [_det("person", t, [0.7, 0.2, 0.95, 0.8]) for t in (0.5, 1.0, 1.5, 2.0)]
    assert TR.support(tr, dets) == pytest.approx(0.0)


def test_the_seed_frame_is_excluded_from_its_own_score():
    """It would agree by construction, so counting it inflates every track by one look."""
    tr = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    tr["seed_t"] = 1.0
    dets = ([_det("person", 1.0, [0.2, 0.2, 0.5, 0.8])]
            + [_det("person", t, [0.7, 0.2, 0.95, 0.8]) for t in (0.5, 1.5, 2.0)])
    assert TR.support(tr, dets) == pytest.approx(0.0)


def test_two_objects_of_one_class_only_need_one_to_match():
    tr = _track("car", 0.0, 30, (0.1, 0.2, 0.4, 0.8))
    dets = []
    for t in (0.5, 1.0, 1.5):
        dets.append(_det("car", t, [0.6, 0.2, 0.9, 0.8]))     # the other car
        dets.append(_det("car", t, [0.11, 0.21, 0.39, 0.79]))  # this one
    assert TR.support(tr, dets) == pytest.approx(1.0)


def test_too_few_independent_looks_means_no_measurement():
    """A VLM-grounded seed is the usual case: no detector found that class at all, so its
    cross-model agreement has to stand rather than being overwritten by a fabricated number."""
    tr = _track("tripod camera", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    assert TR.support(tr, []) is None
    assert TR.support(tr, [_det("tripod camera", 0.5, [0.2, 0.2, 0.5, 0.8])]) is None


def test_detections_of_other_classes_are_not_evidence():
    tr = _track("person", 0.0, 30, (0.2, 0.2, 0.5, 0.8))
    dets = [_det("car", t, [0.2, 0.2, 0.5, 0.8]) for t in (0.5, 1.0, 1.5, 2.0)]
    assert TR.support(tr, dets) is None


# ----------------------------------------------------------------------------- crossing cuts

def _obs(t, box, area=0.05, mask=None):
    m = np.zeros((10, 10), bool) if mask is None else mask
    return (round(t, 2), box, area, [(box[0] + box[2]) / 2, (box[1] + box[3]) / 2], m)


def _mask(x0, x1):
    m = np.zeros((10, 10), bool)
    m[2:8, x0:x1] = True
    return m


def test_a_mask_unchanged_across_the_cut_survives_it():
    """An overlay is composited on top of the plate: same place, same size, both sides of a cut."""
    before = _obs(2.9, (0.1, 0.2, 0.4, 0.8), 0.05, _mask(2, 6))
    after = _obs(3.0, (0.1, 0.2, 0.4, 0.8), 0.051, _mask(2, 6))
    assert TR.survives_cut(before, after)


def test_a_mask_that_jumps_at_the_cut_does_not():
    before = _obs(2.9, (0.1, 0.2, 0.4, 0.8), 0.05, _mask(0, 4))
    after = _obs(3.0, (0.6, 0.2, 0.9, 0.8), 0.05, _mask(6, 10))
    assert not TR.survives_cut(before, after)


def test_a_mask_that_changes_size_at_the_cut_does_not():
    before = _obs(2.9, (0.1, 0.2, 0.4, 0.8), 0.05, _mask(2, 6))
    after = _obs(3.0, (0.1, 0.2, 0.4, 0.8), 0.20, _mask(2, 6))
    assert not TR.survives_cut(before, after)


def test_a_missing_mask_is_not_evidence_of_survival():
    before = _obs(2.9, (0.1, 0.2, 0.4, 0.8), 0.05, _mask(2, 6))
    assert not TR.survives_cut(before, (3.0, (0.1, 0.2, 0.4, 0.8), 0.05, [0.2, 0.5], None))


def _wire_extend(monkeypatch, produced, ignores_cut=True):
    """Make propagation return `produced`, and say whether the box ignored the cut."""
    calls = []

    def seg(clip, t0, t1, reverse, out):
        calls.append((round(t0, 2), round(t1, 2), reverse))

    import types
    fake = types.ModuleType("cadence_vision.sources")
    fake.open_source = lambda p: object()
    fake.frame_at = lambda src, t, edge=None: np.zeros((32, 32, 3), np.uint8)
    monkeypatch.setitem(sys.modules, "cadence_vision.sources", fake)
    monkeypatch.setattr(TR, "_segment", seg)
    monkeypatch.setattr(TR, "_propagate", lambda v, b, imgsz=1024: produced)
    monkeypatch.setattr(TR, "ignores_the_cut",
                        lambda src, box, cut, eps=TR.CUT_EPS, limit=TR.CUT_IGNORED:
                        (ignores_cut, "stubbed"))
    return calls


def test_an_overlay_is_carried_across_the_cut(monkeypatch):
    """The measured case: a picture-in-picture inset on screen for 5.4 s across two cuts came back
    as 2.9 s — one shot — at 0.957 mean IoU and 0.519 temporal IoU."""
    tr = {"cls": "video panel", "conf": 0.9, "seed_t": 4.5, "how": "vlm_agreement",
          "obs": [_obs(3.0 + i / 10, (0.1, 0.2, 0.4, 0.8), 0.05, _mask(2, 6)) for i in range(30)]}
    _wire_extend(monkeypatch, [(i, (0.1, 0.2, 0.4, 0.8), 0.05, [0.25, 0.5], _mask(2, 6))
                               for i in range(30)])
    TR.extend_across_cuts(Path("x.mp4"), tr, [0.0, 3.0, 6.0, 9.0], 9.0, 1280, 720, max_shots=1)
    span = (tr["obs"][0][0], tr["obs"][-1][0])
    assert span[0] < 3.0 and span[1] > 6.0, f"the overlay should reach into both neighbours: {span}"


def test_in_shot_content_still_stops_at_the_cut(monkeypatch):
    """What the shot bound was for: a subject in three shots must stay three sightings, so that
    `identity` can rejoin them with a calibrated score instead of the tracker assuming it."""
    tr = {"cls": "person", "conf": 0.9, "seed_t": 4.5, "how": "detector",
          "obs": [_obs(3.0 + i / 10, (0.1, 0.2, 0.4, 0.8), 0.05, _mask(0, 4)) for i in range(30)]}
    _wire_extend(monkeypatch, [(i, (0.6, 0.2, 0.9, 0.8), 0.05, [0.75, 0.5], _mask(6, 10))
                               for i in range(30)], ignores_cut=False)
    TR.extend_across_cuts(Path("x.mp4"), tr, [0.0, 3.0, 6.0, 9.0], 9.0, 1280, 720)
    assert tr["obs"][0][0] >= 3.0 - 1e-6 and tr["obs"][-1][0] <= 6.0, "it left its shot"


def test_a_track_far_from_a_boundary_is_not_extended(monkeypatch):
    calls = _wire_extend(monkeypatch, [])
    tr = {"cls": "person", "conf": 0.9, "seed_t": 4.0, "how": "detector",
          "obs": [_obs(3.5 + i / 10, (0.1, 0.2, 0.4, 0.8)) for i in range(10)]}
    TR.extend_across_cuts(Path("x.mp4"), tr, [0.0, 3.0, 6.0, 9.0], 9.0, 1280, 720)
    assert calls == [], "a track that never reaches a cut has no cut to cross"


def test_a_single_shot_clip_is_left_alone(monkeypatch):
    calls = _wire_extend(monkeypatch, [])
    tr = {"cls": "person", "conf": 0.9, "seed_t": 1.0, "how": "detector",
          "obs": [_obs(i / 10, (0.1, 0.2, 0.4, 0.8)) for i in range(30)]}
    TR.extend_across_cuts(Path("x.mp4"), tr, [0.0, 9.0], 9.0, 1280, 720)
    assert calls == []


BOX = (0.0, 0.0, 1.0, 1.0)


def _cut_frames(monkeypatch, inside_change, outside_change, w=64, h=64):
    """Two frames across a cut: the box region changes by `inside_change`, the rest by
    `outside_change`, so the ratio the test measures is set exactly."""
    import types
    a = np.zeros((h, w, 3), np.uint8)
    b = a.copy()
    b[:, :, :] = outside_change
    b[: h // 4, : w // 4, :] = inside_change          # BOX_Q covers the top-left quarter
    frames = {0: a, 1: b}
    fake = types.ModuleType("cadence_vision.sources")
    fake.open_source = lambda p: object()
    fake.frame_at = lambda src, t, edge=None: frames[0 if t < 5.0 else 1]
    monkeypatch.setitem(sys.modules, "cadence_vision.sources", fake)


BOX_Q = (0.0, 0.0, 0.25, 0.25)


def test_a_panel_whose_content_ignores_the_cut_is_carried(monkeypatch):
    """The measured case: a picture-in-picture on screen for 5.4 s across two cuts came back as
    2.9 s — one shot — at 0.957 mean IoU and 0.519 temporal IoU. Inside the panel the pixels
    changed 0.59x as much as the rest of the frame, in both directions."""
    _cut_frames(monkeypatch, inside_change=20, outside_change=100)
    ok, why = TR.ignores_the_cut(object(), BOX_Q, 5.0)
    assert ok and "0.20x" in why


def test_content_that_changes_with_the_frame_belongs_to_the_shot(monkeypatch):
    _cut_frames(monkeypatch, inside_change=100, outside_change=100)
    ok, why = TR.ignores_the_cut(object(), BOX_Q, 5.0)
    assert not ok and "belongs to the shot" in why


def test_the_test_is_a_ratio_not_an_absolute(monkeypatch):
    """A gentle cut and a violent one give the same verdict for the same overlay, which is the
    point: how much a cut changes depends on the two shots, not on the thing being tracked."""
    _cut_frames(monkeypatch, inside_change=4, outside_change=20)
    assert TR.ignores_the_cut(object(), BOX_Q, 5.0)[0]
    _cut_frames(monkeypatch, inside_change=40, outside_change=200)
    assert TR.ignores_the_cut(object(), BOX_Q, 5.0)[0]


def test_a_box_covering_the_frame_leaves_nothing_to_compare_against(monkeypatch):
    """X2. With only a border outside the box the ratio means nothing, so it declines to answer."""
    _cut_frames(monkeypatch, inside_change=20, outside_change=100)
    ok, why = TR.ignores_the_cut(object(), (0.0, 0.0, 0.95, 0.95), 5.0)
    assert not ok and "too much of the frame" in why


def test_a_boundary_where_nothing_changes_is_not_a_cut_it_can_measure(monkeypatch):
    """X2. A matched cut between two similar shots would read as an overlay everywhere."""
    _cut_frames(monkeypatch, inside_change=0, outside_change=1)
    ok, why = TR.ignores_the_cut(object(), BOX_Q, 5.0)
    assert not ok and "barely changes" in why


# ------------------------------------------------------- grounding fills shortfalls

def _wire_track(monkeypatch, dets, grounded, tmp_path):
    """A whole `track` run with the models replaced: `dets` is what the detector saw, `grounded`
    is what the vision models would return, and every seed propagates to a usable track."""
    asked = []
    monkeypatch.setattr(TR, "detections", lambda clip, prompts: list(dets))

    def _ground(clip, t, targets, **kw):
        asked.append(sorted(targets))
        return [g for g in grounded if g["cls"] in targets]

    monkeypatch.setattr(TR, "ground", _ground)
    monkeypatch.setattr(TR, "propagate_one", lambda clip, cand, dur, W, H, shot=None: {
        "cls": cand["cls"], "conf": cand["conf"], "seed_t": cand["t"], "how": "stub",
        "obs": [_obs(cand["t"] + i / 10, cand["box"], 0.05, _mask(2, 6)) for i in range(6)]})
    monkeypatch.setattr(TR, "extend_across_cuts",
                        lambda clip, tr, bounds, dur, W, H, **kw: tr)
    monkeypatch.setattr(TR, "support", lambda tr, dets, **kw: None)
    monkeypatch.setattr(TR, "_dedupe", lambda tracks, log: tracks)

    class _Cap:
        def get(self, i):
            return 1280 if i == 3 else 720

        def release(self):
            pass

    monkeypatch.setattr(TR.cv2, "VideoCapture", lambda p: _Cap())
    return asked


def _seed(cls, t, box, conf):
    return {"cls": cls, "t": t, "box": box, "conf": conf}


def test_a_class_that_came_up_short_is_grounded_not_only_a_missing_one(monkeypatch, tmp_path):
    """The handoff clip: the detector sees one gloved hand at 0.32 and never a second, so the rule
    for `handoff` — which needs two — could never fire. A class that asked for two and got one has
    been missed just as surely as one that got none."""
    dets = [_seed("gloved hand", 1.0, (0.1, 0.1, 0.2, 0.2), 0.32)]
    grounded = [_seed("gloved hand", 2.0, (0.7, 0.1, 0.8, 0.2), 0.9)]
    asked = _wire_track(monkeypatch, dets, grounded, tmp_path)
    got = TR.track(tmp_path / "x.mp4", ["gloved hand"], {"gloved hand": 2}, 5.0,
                   log=lambda s: None, cache_dir=tmp_path)
    assert asked == [["gloved hand"]], f"grounding was not asked for the shortfall: {asked}"
    assert sum(1 for t in got if t["cls"] == "gloved hand") == 2


def test_a_class_already_satisfied_is_not_grounded(monkeypatch, tmp_path):
    dets = [_seed("cat", 1.0, (0.1, 0.1, 0.2, 0.2), 0.9)]
    asked = _wire_track(monkeypatch, dets, [], tmp_path)
    TR.track(tmp_path / "x.mp4", ["cat"], {"cat": 1}, 5.0, log=lambda s: None, cache_dir=tmp_path)
    assert asked == [], f"grounding was asked for a class that was already filled: {asked}"


def test_grounding_does_not_re_seed_something_already_tracked(monkeypatch, tmp_path):
    """The models are asked for two hands and hand back the one already found. Taking it would
    report the same hand twice and let `handoff` fire between a thing and itself."""
    box = (0.1, 0.1, 0.2, 0.2)
    dets = [_seed("gloved hand", 1.0, box, 0.32)]
    grounded = [_seed("gloved hand", 1.0, box, 0.9)]      # the same hand, same place
    _wire_track(monkeypatch, dets, grounded, tmp_path)
    got = TR.track(tmp_path / "x.mp4", ["gloved hand"], {"gloved hand": 2}, 5.0,
                   log=lambda s: None, cache_dir=tmp_path)
    assert sum(1 for t in got if t["cls"] == "gloved hand") == 1


# ------------------------------------------------------- malformed grounding replies

def test_a_bare_box_reply_is_read_rather_than_thrown_on():
    """Asked for [{"label":…, "box_2d":…}], a model sometimes answers with the box alone. That
    used to raise out of the parser, and since `track` catches grounding as one block, one
    malformed reply cost every other class its seed."""
    got = TR._boxes_from_reply('[[100, 200, 300, 400]]')
    assert got and got[0]["cands"]


def test_an_unusable_row_costs_only_itself():
    got = TR._boxes_from_reply('[7, {"label": "cat", "box_2d": [100, 200, 300, 400]}]')
    assert len(got) == 1 and got[0]["label"] == "cat"


def test_a_box_that_is_not_numbers_is_skipped():
    assert TR._boxes_from_reply('[{"label": "x", "box_2d": ["a", "b", "c", "d"]}]') == []


def test_a_box_of_the_wrong_length_is_skipped():
    assert TR._boxes_from_reply('[{"label": "x", "box_2d": [1, 2, 3]}]') == []


def test_a_well_formed_reply_still_reads_the_same():
    got = TR._boxes_from_reply('[{"label": "cat", "box_2d": [100, 200, 300, 400]}]')
    assert got[0]["label"] == "cat" and (0.1, 0.2, 0.3, 0.4) in got[0]["cands"]


def test_grounding_cache_keys_identify_the_target_not_its_position():
    """Two targets asked in different orders must never share a cached reply.

    The key was the target's index, so grounding ["panel"] cached under 0 and a later
    ["cat", "panel"] asked for "cat" at 0 and got the panel's answer back — measured live, with
    `cat` carrying the panel's box. The key now carries clip, time, target and model.
    """
    seen = []

    class _FakeCall:
        def __call__(self, model, parts, cache_key, timeout=180, retries=3):
            seen.append(cache_key)
            return {"text": '[{"label": "x", "box_2d": [10, 10, 20, 20]}]'}

    import types
    fake = types.SimpleNamespace(MODELS={"qwen": "q", "gemini": "g"},
                                 call=_FakeCall(), _b64=lambda im: "data:,")
    import sys
    real = sys.modules.get("cadence_vision.evalrun")
    sys.modules["cadence_vision.evalrun"] = fake
    try:
        import numpy as _np
        from PIL import Image as _Image
        clip = Path("/tmp/does-not-matter.mp4")
        import cadence_vision.sources as SRC
        o_open, o_frame = SRC.open_source, SRC.frame_at
        SRC.open_source = lambda p: None
        SRC.frame_at = lambda src, t: _np.zeros((16, 16, 3), _np.uint8)
        try:
            TR.ground(clip, 1.0, ["cat", "video panel inset"])
        finally:
            SRC.open_source, SRC.frame_at = o_open, o_frame
    finally:
        if real is not None:
            sys.modules["cadence_vision.evalrun"] = real
        else:
            sys.modules.pop("cadence_vision.evalrun", None)

    assert len(seen) == len(set(seen)), f"cache keys collided: {seen}"
    assert any("cat" in k for k in seen) and any("video-panel-inset" in k for k in seen), seen
    assert all("1.000" in k for k in seen), f"time missing from key: {seen}"
