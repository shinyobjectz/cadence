"""The real-clip harness: the comp it generates, and the two scores it reports.

The harness is the thing everything else is measured with, so it is measured first. Scoring is
checked against hand-computed cases, and the generated comp is checked to be a comp — parsed by
Cadence itself, with the cuts landing where the truth file says they do.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import clipeval as CE  # noqa: E402

ASSETS = sorted((ROOT / "evals" / "assets").glob("*.webm"))


# ----------------------------------------------------------------------------- cut scoring

def test_exact_cuts_are_zero_frames_off():
    r = CE.score_cuts([3.0, 6.0], [3.0, 6.0])
    assert r["matched"] == 2 and r["missed"] == 0 and r["false_positives"] == 0
    assert r["within"]["<=0f"] == 2 and r["max_frames"] == 0.0


def test_one_frame_late_is_reported_as_one_frame_late():
    """The reason this is a frame histogram and not tIoU: at 3s out of 9s, a cut one frame late
    still overlaps 0.996 of the shot. Overlap cannot see the error that ruins an edit."""
    r = CE.score_cuts([3.0], [3.0 + 1 / 30])
    assert r["within"]["<=0f"] == 0 and r["within"]["<=1f"] == 1
    assert r["median_frames"] == pytest.approx(1.0)


def test_a_cut_nowhere_near_the_truth_is_a_miss_and_a_false_positive():
    r = CE.score_cuts([3.0], [7.5])
    assert r["matched"] == 0 and r["missed"] == 1 and r["false_positives"] == 1


def test_each_detected_cut_matches_at_most_one_truth_cut():
    """Two truth cuts must not both claim the same detection and score as a hit apiece."""
    r = CE.score_cuts([3.0, 3.2], [3.1])
    assert r["matched"] == 1 and r["missed"] == 1 and r["false_positives"] == 0


def test_extra_detections_are_counted_as_false_positives():
    r = CE.score_cuts([3.0, 6.0], [3.0, 4.4, 6.0, 8.1])
    assert r["matched"] == 2 and r["false_positives"] == 2


# ----------------------------------------------------------------------------- inset scoring

def _truth(boxes, t0=1.8, t1=3.8):
    return {"t0": t0, "t1": t1, "box_at": {f"{t:.4f}": b for t, b in boxes}}


def _track(obs, cls="video panel"):
    return {"cls": cls, "how": "detector", "seed_t": obs[0][0], "conf": 0.9,
            "obs": [(t, b, 0.05, [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2], None) for t, b in obs]}


def test_a_perfect_track_scores_one():
    boxes = [(round(1.8 + i / 10, 4), [0.1, 0.6, 0.4, 0.9]) for i in range(20)]
    r = CE.score_inset(_truth(boxes), [_track(boxes)])
    assert r["iou_mean"] == 1.0 and r["tiou"] == pytest.approx(1.0, abs=0.06)


def test_a_track_covering_half_the_interval_halves_the_temporal_iou():
    boxes = [(round(1.8 + i / 10, 4), [0.1, 0.6, 0.4, 0.9]) for i in range(21)]
    r = CE.score_inset(_truth(boxes), [_track(boxes[:11])])
    assert r["iou_mean"] == 1.0, "spatial accuracy is unaffected by stopping early"
    assert r["tiou"] == pytest.approx(0.5, abs=0.06)


def test_the_best_matching_track_wins():
    boxes = [(round(1.8 + i / 10, 4), [0.1, 0.6, 0.4, 0.9]) for i in range(20)]
    off = [(t, [b[0] + 0.25, b[1], b[2] + 0.25, b[3]]) for t, b in boxes]
    r = CE.score_inset(_truth(boxes), [_track(off, "person"), _track(boxes)])
    assert r["cls"] == "video panel" and r["iou_mean"] == 1.0


def test_never_seeding_the_inset_scores_zero_rather_than_erroring():
    boxes = [(round(1.8 + i / 10, 4), [0.1, 0.6, 0.4, 0.9]) for i in range(20)]
    r = CE.score_inset(_truth(boxes), [])
    assert r["iou_mean"] == 0.0 and r["tiou"] == 0.0 and "never seeded" in r["note"]


def test_a_track_outside_the_inset_window_contributes_no_samples():
    boxes = [(round(1.8 + i / 10, 4), [0.1, 0.6, 0.4, 0.9]) for i in range(20)]
    far = [(round(6.0 + i / 10, 4), [0.1, 0.6, 0.4, 0.9]) for i in range(20)]
    assert CE.score_inset(_truth(boxes), [_track(far)])["samples"] == 0


# ----------------------------------------------------------------------------- generated comp

@pytest.mark.skipif(len(ASSETS) < 2, reason="needs two clips in evals/assets")
def test_build_writes_truth_that_matches_the_comp(tmp_path):
    truth = CE.build(ASSETS[:2], tmp_path / "e.lua", seg=2.0)
    assert truth["cuts"] == [2.0] and truth["duration"] == 4.0
    lua = (tmp_path / "e.lua").read_text()
    assert lua.count("s:video") == 3          # two plates plus the inset
    assert 'from = 2.0' in lua
    side = json.loads((tmp_path / "e.truth.json").read_text())
    assert side["cuts"] == truth["cuts"]


@pytest.mark.skipif(len(ASSETS) < 2, reason="needs two clips in evals/assets")
def test_the_inset_truth_track_is_monotonic_and_inside_the_frame(tmp_path):
    ins = CE.build(ASSETS[:2], tmp_path / "e.lua", seg=2.0)["inset"]
    xs = [b[0] for _, b in sorted(ins["box_at"].items(), key=lambda kv: float(kv[0]))]
    assert xs == sorted(xs) and xs[-1] > xs[0], "the inset travels left to right"
    for b in ins["box_at"].values():
        assert 0 <= b[0] < b[2] <= 1 and 0 <= b[1] < b[3] <= 1


@pytest.mark.skipif(not (ROOT / "bin" / "cadence").exists(), reason="renderer not available")
@pytest.mark.skipif(len(ASSETS) < 2, reason="needs two clips in evals/assets")
def test_the_generated_comp_passes_cadence_check(tmp_path):
    """A harness that generates invalid Lua would score the perception stack on nothing."""
    CE.build(ASSETS[:2], tmp_path / "e.lua", seg=2.0)
    r = subprocess.run([str(ROOT / "bin" / "cadence"), "check", str(tmp_path / "e.lua")],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr


def test_a_track_that_never_half_overlaps_is_not_the_inset():
    """X2 for the harness: a coincidental interval match is not a localization."""
    truth = {"t0": 0.0, "t1": 1.0,
             "box_at": {f"{t:.1f}": (0.0, 0.0, 0.2, 0.2) for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)}}
    # elsewhere in the frame, but present for exactly the same seconds
    elsewhere = {"cls": "person", "how": "detector_agreement", "seed_t": 0.5,
                 "obs": [(t, (0.7, 0.7, 0.9, 0.9)) for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]}
    got = CE.score_inset(truth, [elsewhere])
    assert got["frac_over_50"] == 0
    assert got["matched"] is False
    out = CE.report({"clips": [1], "duration": 1, "facts": 0, "secs": 0,
                    "cuts": {"truth": 0, "found": 0, "matched": 0, "missed": 0,
                             "false_positives": 0, "within": {}, "median_frames": 0, "max_frames": 0},
                    "inset": got})
    assert "not matched" in out
    assert "temporal IoU |" not in out, "a tIoU was reported for the wrong object"


def test_a_real_match_still_reports_every_metric():
    truth = {"t0": 0.0, "t1": 1.0,
             "box_at": {f"{t:.1f}": (0.0, 0.0, 0.2, 0.2) for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)}}
    onit = {"cls": "video panel", "how": "vlm_agreement", "seed_t": 0.5,
            "obs": [(t, (0.01, 0.01, 0.21, 0.21)) for t in (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)]}
    got = CE.score_inset(truth, [onit])
    assert got["matched"] is True and got["frac_over_50"] > 0
    out = CE.report({"clips": [1], "duration": 1, "facts": 0, "secs": 0,
                    "cuts": {"truth": 0, "found": 0, "matched": 0, "missed": 0,
                             "false_positives": 0, "within": {}, "median_frames": 0, "max_frames": 0},
                    "inset": got})
    assert "temporal IoU |" in out
