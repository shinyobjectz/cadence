"""G5: when one thing stops resting on another.

Measured end to end, through `break_time`, on two pieces of material:

    release comp, truth 2.400 by construction   ->  2.44   1.0 frame
    handoff clip, reference 8.42                ->  8.50   2.0 frames

and against the alternatives on the same handoff clip: box separation 0.22-0.32 s out, mask contact
area at 10 fps has no event in it at all, frame-change peak 0.18 s out (it peaks where motion is
fastest, which is after the hand has gone). The rest of these cover the pure parts, which is where
the bugs were: a window that did not contain its own prompts, and a scale that made two thresholds
disagree by more frames than the event lasted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import contact as K  # noqa: E402


def test_the_window_contains_every_box_it_was_given():
    """The first attempt handed SAM a prompt at x=1395 in a 1110-wide window."""
    boxes = [[0.38, 0.19, 0.56, 0.60], [0.38, 0.19, 0.83, 0.72]]
    X0, Y0, X1, Y1 = K.window(boxes, 960, 506)
    for b in boxes:
        assert X0 <= b[0] * 960 and b[2] * 960 <= X1
        assert Y0 <= b[1] * 506 and b[3] * 506 <= Y1


def test_the_window_stays_inside_the_frame():
    X0, Y0, X1, Y1 = K.window([[-0.5, -0.5, 1.9, 1.9]], 640, 360)
    assert (X0, Y0, X1, Y1) == (0, 0, 640, 360)


def test_a_prompt_outside_the_image_is_refused_not_clipped_to_nothing():
    assert K.clamp([1200, -40, 1400, 50], 1110, 780) is None
    assert K.clamp([10, 10, 100, 100], 1110, 780) == [10.0, 10.0, 100.0, 100.0]


def test_gap_is_the_clearance_under_a_to_b():
    a = np.zeros((40, 20), bool); a[5:15, :] = True      # A occupies rows 5-14
    b = np.zeros((40, 20), bool); b[20:30, :] = True     # B starts at row 20
    assert K.gap(a, b) == 6.0                            # 20 - 14
    assert K.gap(a, np.zeros((40, 20), bool)) is None    # nothing to measure against


def test_a_clean_step_is_found_at_its_first_frame():
    gaps = [1.0] * 15 + [41.0, 58.0, 79.0, 100.0]
    i, conf = K.onset(gaps, scale=2.0)
    assert i == 15 and conf == 1.0


def test_a_gradual_drift_is_not_a_release():
    """X2: two things slowly parting never 'let go', so there is no time to report."""
    assert K.onset([float(i) for i in range(1, 19)], scale=1.0) is None


def test_the_thresholds_may_disagree_slightly_and_the_earliest_wins():
    """A gap opening a few px a frame puts a 1-px and a 4-px threshold frames apart by rights."""
    gaps = [3, 2, 2, 1, 2, 1, 2, 1, 2, 2, 2, 12, 26, 41, 58, 73, 88, 103, 119, 133, 148,
            163, 178, 193, 209]
    got = K.onset([float(g) for g in gaps], scale=5.0)
    assert got is not None
    i, conf = got
    assert i == 11, "should read the first frame the gap opens, not where it is widest"
    assert 0 < conf < 1, "a spread of one frame is less certain than none"


def test_too_few_readable_frames_is_silence():
    assert K.onset([1.0, None, 2.0], scale=1.0) is None
    assert K.onset([None] * 10, scale=1.0) is None
