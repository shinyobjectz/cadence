"""The perception layer, measured on constructed truth rather than on footage.

X4: a producer is measured where the answer is known before it is trusted where it is not. Each
test here fabricates the inputs a producer would have received — track observations, camera steps,
depth readings — so the expected fact is a matter of arithmetic, and a regression names itself.

The two behaviours worth guarding are the ones real clips broke:
  * G3, camera-relative vs world-relative motion: a parked car is not moving because the camera
    dollied past it;
  * G10, defocus mistaken for distance: a blurred foreground object is not far away.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import perceive as P  # noqa: E402
from cadence_vision.facts import Log, parse_log  # noqa: E402

FPS = P.TR.SAMPLE_FPS
DT = 1.0 / FPS
W, H = 1280, 720


def _obs(traj, area=0.02):
    """Track observations from a centroid trajectory: [(t, box, area, centroid, mask), ...]"""
    out = []
    for i, (cx, cy) in enumerate(traj):
        r = np.sqrt(area) / 2
        out.append((round(i * DT, 2), [cx - r, cy - r, cx + r, cy + r], area, [cx, cy],
                    np.zeros((4, 4), bool)))
    return out


CAM_FPS = 5.0
BG_W = 320


def _cam(n_obs, dx=0.0, dy=0.0):
    """Camera steps as camerafacts.steps returns them, in 320px background-model units.

    A step's dx is the background's displacement over the interval *ending* at its timestamp, and
    the first sample has no predecessor, so records start one step in rather than at zero."""
    n = max(1, int(round(n_obs * DT * CAM_FPS)))
    return [{"t": (i + 1) / CAM_FPS, "dx": dx, "dy": dy, "ls": 0.0, "n": 300, "cov": 1.0,
             "pos": None} for i in range(n)]


def _per_obs(px_per_cam_step):
    """The per-observation frame fraction a world-static point drifts for a given camera step."""
    return px_per_cam_step / BG_W * DT * CAM_FPS


def _emit(ents, cam_recs, dur):
    L = Log()
    P._entities(L, ents, dur, DT, W, H, P._camera_frame(cam_recs, W, H))
    return L.text()


def _ent(eid, traj, cls="car", area=0.02):
    return {"id": eid, "cls": cls, "conf": 0.9, "seed_t": 0.0, "how": "detector",
            "obs": _obs(traj, area)}


# ----------------------------------------------------------------------------- G3

def test_camera_translation_is_frame_fractions_per_axis():
    """dy is a fraction of frame *height*; the background model is 320px wide, not square."""
    ts, S, tx, ty = P._camera_frame(_cam(8, dx=3.2, dy=3.2), W, H)
    assert S[-1] == pytest.approx(1.0), "no scale change means a pure translation"
    assert tx[-1] == pytest.approx(4 * 3.2 / 320)
    assert ty[-1] == pytest.approx(4 * 3.2 / (320 * H / W))
    assert ty[-1] > tx[-1], "a pixel is a larger fraction of the short axis"


def test_no_camera_records_is_no_correction():
    ts, S, tx, ty = P._camera_frame([], W, H)
    assert S.tolist() == [1.0] and tx.tolist() == [0.0] and ty.tolist() == [0.0]


def _push_in(n, per_step=0.02):
    """Camera records for a slow dolly: no translation, a steady scale about the frame centre."""
    recs = _cam(n)
    for r in recs:
        r["ls"] = per_step
    cam = P._camera_frame(recs, W, H)
    ts = np.array([round(i * DT, 2) for i in range(n)])
    return recs, cam, ts, np.interp(ts, cam[0], cam[1])


def test_a_dolly_is_undone_as_a_scaling_not_a_slide():
    """The regression that made this a similarity rather than an offset: under a dolly the
    background expands about a point, so a parked car off-centre appears to travel. Subtracting a
    translation cannot undo that, and the version that tried promoted three parked cars to moving
    subjects on the man clip."""
    n = 40
    _recs, cam, ts, S = _push_in(n)
    static = np.stack([0.5 + 0.3 * S, 0.5 - 0.2 * S], 1)      # a point fixed in the world
    ws, war = P._unmap(cam, ts, static, np.full(n, 0.02) * S ** 2)
    assert np.allclose(ws[:, 0], 0.8, atol=0.01) and np.allclose(ws[:, 1], 0.3, atol=0.01)
    assert np.allclose(war, 0.02, rtol=0.03), "a static object's area grows as the square of scale"


def test_a_dollying_camera_leaves_parked_cars_as_scenery():
    n = 40
    recs, _c, ts, S = _push_in(n)
    car = _ent("e1", [(0.5 + 0.3 * s, 0.5 - 0.2 * s) for s in S])
    car["obs"] = [(o[0], o[1], 0.02 * S[i] ** 2, o[3], o[4]) for i, o in enumerate(car["obs"])]
    out = _emit([car], recs, 4.0)
    assert "holds(still(e1), 0.000, 4.000)" in out, out
    assert car["subject"] is False
    assert "approaching" not in out, "the camera moved closer, the car did not"


def test_world_static_entity_under_a_pan_is_still():
    """G3, the parked-car case. The camera pans right, so the background — and everything fixed to
    it — slides left across the frame at the same rate. That is the camera moving, not the car."""
    n, drift = 40, 6.0                       # 6 px per camera step of background motion
    per = _per_obs(drift)
    traj = [(0.7 - i * per, 0.5) for i in range(n)]
    out = _emit([_ent("e1", traj)], _cam(n, dx=-drift), 4.0)
    assert "holds(still(e1)" in out
    assert "motion(e1" not in out, f"camera motion leaked into the entity:\n{out}"


def test_the_same_trajectory_with_a_locked_camera_is_motion():
    """Same pixels, different cause. Without the camera record the drift is real motion — which is
    what makes the previous test a measurement and not a tautology."""
    n, per = 40, _per_obs(6.0)
    traj = [(0.7 - i * per, 0.5) for i in range(n)]
    out = _emit([_ent("e1", traj)], _cam(n, dx=0.0), 4.0)
    assert "motion(e1, dir(left)" in out, out
    assert "holds(still(e1), 0.000, 4.000)" not in out


def test_entity_moving_against_a_pan_keeps_its_world_direction():
    """The subject walks right while the camera pans right faster: in frame it slides left."""
    n = 40
    cam_per, walk_per = _per_obs(6.0), _per_obs(9.0)
    traj = [(0.2 - i * cam_per + i * walk_per, 0.5) for i in range(n)]
    out = _emit([_ent("e1", traj, cls="man with backpack")], _cam(n, dx=-6.0), 4.0)
    assert "motion(e1, dir(right)" in out, out
    assert "dir(left)" not in out


def test_a_still_entity_is_scenery_and_emits_no_relations():
    """Scenery is why this matters: a still entity is excluded from the n^2 relation sweep."""
    n = 40
    still = _ent("e1", [(0.3, 0.5)] * n)
    mover = _ent("e2", [(0.1 + i * 0.02, 0.5) for i in range(n)], cls="man with backpack")
    _emit([still, mover], _cam(n), 4.0)
    assert still["subject"] is False and mover["subject"] is True
    L = Log()
    P._relations(L, [still, mover], DT, 4.0)
    assert not [ln for ln in L.text().splitlines() if ln.startswith("holds(touching")]


# ----------------------------------------------------------------------------- G9

@pytest.mark.parametrize("frac,want", [(0.98, "ecu"), (0.8, "cu"), (0.6, "mcu"), (0.45, "ms"),
                                       (0.3, "mls"), (0.15, "ls"), (0.05, "els")])
def test_shot_scale_ladder(frac, want):
    assert P._shot_scale(frac) == want


def test_shot_scale_is_only_claimed_for_people():
    """The ladder is defined by a human body against the frame; a car at 60 % is not an MCU."""
    n = 30
    person = _ent("e1", [(0.5, 0.5)] * n, cls="person", area=0.36)   # 0.6 of frame height
    car = _ent("e2", [(0.5, 0.5)] * n, cls="car", area=0.36)
    out = _emit([person, car], _cam(n), 3.0)
    assert "holds(shot_scale(e1, mcu)" in out, out
    assert "shot_scale(e2" not in out


# ----------------------------------------------------------------------------- G10

def _rel(a_vals, b_vals):
    ts = [round(i * 0.5, 2) for i in range(len(a_vals))]
    return {"e1": list(zip(ts, a_vals)), "e2": list(zip(ts, b_vals))}


def test_depth_order_is_emitted_when_both_are_in_focus():
    got = P.depth_pairs(["e1", "e2"], _rel([0.8] * 6, [0.3] * 6), soft=set(), subj={"e1"})
    assert [(a, b) for a, b, *_ in got] == [("e1", "e2")]
    assert got[0][4] == pytest.approx(1.0)


def test_defocus_suppresses_the_depth_order():
    """The measured failure: relief said the soft tripod camera (0.29) was behind the sharp woman
    (0.81) when it was plainly in front of her. Focus vetoes the reading."""
    rel = _rel([0.81] * 6, [0.29] * 6)
    assert P.depth_pairs(["e1", "e2"], rel, soft=set(), subj={"e1"})      # unguarded: wrong fact
    assert P.depth_pairs(["e1", "e2"], rel, soft={"e2"}, subj={"e1"}) == []


def test_disagreement_across_time_emits_nothing():
    got = P.depth_pairs(["e1", "e2"], _rel([0.8, 0.2, 0.8, 0.2], [0.5] * 4), set(), {"e1"})
    assert got == []


def test_scenery_pairs_are_not_ordered():
    got = P.depth_pairs(["e1", "e2"], _rel([0.8] * 6, [0.3] * 6), soft=set(), subj=set())
    assert got == []


# ----------------------------------------------------------------------------- entrances

def test_a_track_starting_at_an_edge_is_an_entrance():
    n = 30
    e = _ent("e1", [(0.01 + i * 0.03, 0.5) for i in range(n)], cls="man with backpack")
    e["obs"] = [(round(t + 1.0, 2), b, a, c, m) for t, b, a, c, m in e["obs"]]
    out = _emit([e], _cam(n), 5.0)
    assert "happens(enter(e1, from(left)), 1.000)" in out, out


def test_a_track_starting_mid_frame_is_only_first_seen():
    """A detector noticing something late is not the thing arriving; the grammar keeps them apart."""
    n = 30
    e = _ent("e1", [(0.5, 0.5)] * n)
    e["obs"] = [(round(t + 1.0, 2), b, a, c, m) for t, b, a, c, m in e["obs"]]
    out = _emit([e], _cam(n), 5.0)
    assert "happens(first_seen(e1), 1.000)" in out, out
    assert "enter(e1" not in out


# ----------------------------------------------------------------------------- grammar

def test_every_perceived_fact_carries_provenance():
    """X2. The lifter omits src to mean 'exact'; a perceived log may never do that."""
    n = 40
    out = _emit([_ent("e1", [(0.2 + i * 0.015, 0.5) for i in range(n)],
                      cls="man with backpack")], _cam(n), 4.0)
    facts = parse_log(out)
    claims = [f for f in facts if f[0] in ("holds", "happens")]
    have = {f[1] for f in facts if f[0] == "src"}
    assert claims
    for c in claims:
        assert c in have, f"no src for {c}"


# ----------------------------------------------------------------------------- optional producers

def test_a_producer_that_throws_costs_only_its_own_facts(capsys):
    """These run after minutes of SAM propagation. A model that falls over must not take the log
    with it, and the failure must be visible rather than looking like a quiet clip."""
    said = []
    L = Log()
    L.fact("clip(\"x.mp4\")", "ffprobe", 0.99)

    def boom(_m):
        raise RuntimeError("metal delegate unavailable")

    P._optional(L, said.append, "boundaries", "action boundaries", boom)
    assert 'clip("x.mp4")' in L.text(), "the facts gathered before it survive"
    assert said and "producer failed" in said[0] and "RuntimeError" in said[0]


def test_a_missing_library_is_reported_as_missing():
    said = []
    P._optional(Log(), said.append, "no_such_module", "on-screen text", lambda m: None)
    assert said and "not available" in said[0]


def test_a_producer_that_succeeds_is_not_reported_as_a_failure():
    said = []
    L = Log()
    P._optional(L, said.append, "facts", "grammar", lambda m: m.T(1.0))
    assert said == []
