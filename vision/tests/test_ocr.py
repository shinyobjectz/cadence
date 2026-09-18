"""G6, measured against a comp that already knows the answer.

`captions.lua` declares three cues at 0.55, 1.45 and 2.45 with exact strings, so the lifter's text
facts are ground truth for the OCR producer reading the render of the same comp. That is the X4
discipline in its cleanest form: the same grammar on both sides, one side exact.

The grouping and boundary logic is unit-tested on fabricated detections; only the end-to-end
accuracy check needs the renderer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import ocr as O  # noqa: E402
from cadence_vision.facts import Log, lift, parse_log  # noqa: E402

CAPTIONS = ROOT / "evals" / "cases" / "captions.lua"
FRAME = 1 / 30


def _det(box, text, conf=0.9):
    return {"box": box, "text": text, "conf": conf}


BOX = (0.06, 0.78, 0.55, 0.86)


# ----------------------------------------------------------------------------- grouping

def test_a_region_that_changes_line_stays_one_region():
    """The distinction `text_change` exists to make: a lower third swapping names is one region
    with two intervals, not two regions that happen to share a position."""
    samples = [(0.2, [_det(BOX, "Hold the cut.")]), (0.7, [_det(BOX, "Hold the cut.")]),
               (1.2, [_det(BOX, "Let the type land.")]), (1.7, [_det(BOX, "Let the type land.")])]
    regs = O.regions(samples)
    assert len(regs) == 1
    assert [t for t, *_ in O.stable_runs(regs[0]["obs"])] == ["Hold the cut.", "Let the type land."]


def test_text_in_a_different_place_is_a_different_region():
    far = (0.06, 0.05, 0.4, 0.12)
    regs = O.regions([(0.2, [_det(BOX, "EDITOR"), _det(far, "CAPTIONS")])])
    assert len(regs) == 2


def test_a_region_that_leaves_and_returns_is_not_bridged():
    samples = [(0.2, [_det(BOX, "ONE")]), (0.7, [_det(BOX, "ONE")]), (1.2, []), (1.7, []),
               (2.2, [_det(BOX, "TWO")]), (2.7, [_det(BOX, "TWO")])]
    assert len(O.regions(samples)) == 2


def test_low_engine_scores_are_dropped_before_anything_is_measured():
    fr = np.zeros((40, 200, 3), np.uint8)
    assert O.read(fr) == []


# ----------------------------------------------------------------------------- derived confidence

def test_confidence_is_agreement_not_the_engines_own_score():
    """X1. The engine says 0.99 every time and reads the word differently every time; the number
    that reaches the log is how often the readings agreed, which is about the text, not the model."""
    obs = [(0.2 + i * 0.5, BOX, t, 0.99) for i, t in
           enumerate(["SALE", "SALE", "SALE", "SALE", "5ALE"])]
    runs = O.stable_runs(obs)
    assert len(runs) == 1
    txt, _a, _b, agree = runs[0]
    assert txt == "SALE" and agree == pytest.approx(0.8)


def test_a_string_that_never_settles_is_not_claimed():
    obs = [(0.2 + i * 0.5, BOX, t, 0.99) for i, t in enumerate(["QW", "ZX", "MN", "PL"])]
    assert O.stable_runs(obs) == []


def test_a_single_sighting_is_not_claimed():
    assert O.stable_runs([(0.2, BOX, "FLASH", 0.99)]) == []


def test_readings_that_differ_only_in_quality_are_the_same_string():
    assert O._same("Let the type land.", "Let the type Iand.")
    assert O._same("cut 04", "cut04")
    assert not O._same("Hold the cut.", "Then get out.")
    assert not O._same("ON", "OFF"), "short strings get no edit-distance slack"


# ----------------------------------------------------------------------------- boundary refinement

class _FakeSrc:
    """A clip whose text changes at exactly `at`, so the bisection has a known answer."""

    def __init__(self, at, before, after):
        self.at, self.before, self.after = at, before, after
        self.calls = 0


def _patched(monkeypatch, src):
    def read_box(s, t, box, pad=0.02):
        s.calls += 1
        return s.after if t >= s.at else s.before
    monkeypatch.setattr(O, "read_box", read_box)


def test_refine_finds_the_change_inside_a_sample_gap(monkeypatch):
    src = _FakeSrc(1.45, "Hold the cut.", "Let the type land.")
    _patched(monkeypatch, src)
    got = O.refine(src, BOX, "Hold the cut.", "Let the type land.", 1.2, 1.7, steps=6)
    assert abs(got - 1.45) < FRAME
    assert src.calls == 6, "six OCR calls on a crop, not a re-scan of the clip"


def test_refine_handles_an_appearance(monkeypatch):
    src = _FakeSrc(0.55, "", "Hold the cut.")
    _patched(monkeypatch, src)
    assert abs(O.refine(src, BOX, None, "Hold the cut.", 0.2, 0.7, steps=6) - 0.55) < FRAME


def test_refine_handles_a_disappearance(monkeypatch):
    src = _FakeSrc(3.1, "Then get out.", "")
    _patched(monkeypatch, src)
    assert abs(O.refine(src, BOX, "Then get out.", None, 2.9, 3.4, steps=6) - 3.1) < FRAME


def test_an_unreadable_transition_keeps_the_coarse_time(monkeypatch):
    """A cross-fade reads as neither line. Returning the sample time is worse localization and an
    honest one; inventing a bisected time would be a precise wrong answer."""
    monkeypatch.setattr(O, "read_box", lambda s, t, box, pad=0.02: "")
    assert O.refine(None, BOX, "A", "B", 1.2, 1.7, steps=6) == 1.7


# ----------------------------------------------------------------------------- against the comp

@pytest.mark.skipif(not (ROOT / "bin" / "cadence").exists(), reason="renderer not available")
def test_ocr_recovers_the_comps_own_captions():
    truth = [(f[1][2], f[2], f[3]) for f in parse_log(lift(CAPTIONS))
             if f[0] == "holds" and isinstance(f[1], tuple) and f[1][0] == "text"
             and f[1][1] == "text6"]
    assert len(truth) == 3, "captions.lua is expected to declare three cues"

    L = Log()
    try:
        O.emit(CAPTIONS, L)
    except RuntimeError as e:                       # render unavailable in this environment
        pytest.skip(str(e).splitlines()[0][:120])
    got = [(f[1][2], f[2], f[3]) for f in parse_log(L.text())
           if f[0] == "holds" and isinstance(f[1], tuple) and f[1][0] == "text"]
    cues = [g for g in got if any(O._same(g[0], t[0]) for t in truth)]
    assert [c[0] for c in cues] == [t[0] for t in truth], f"strings differ: {cues}"
    for (_txt, a, _b), (_t, ta, _tb) in zip(cues, truth):
        assert abs(a - ta) <= 2 * FRAME, f"cue at {a} vs {ta}"


@pytest.mark.skipif(not (ROOT / "bin" / "cadence").exists(), reason="renderer not available")
def test_the_caption_is_one_entity_with_two_changes():
    L = Log()
    try:
        O.emit(CAPTIONS, L)
    except RuntimeError as e:
        pytest.skip(str(e).splitlines()[0][:120])
    facts = parse_log(L.text())
    by_ent: dict[str, int] = {}
    for f in facts:
        if f[0] == "holds" and isinstance(f[1], tuple) and f[1][0] == "text":
            by_ent[f[1][1]] = by_ent.get(f[1][1], 0) + 1
    caption = max(by_ent, key=by_ent.get)
    assert by_ent[caption] == 3
    changes = [f for f in facts if f[0] == "happens" and f[1] == ("text_change", caption)]
    assert len(changes) == 2, "three lines in one region means two changes"
    have = {g[1] for g in facts if g[0] == "src"}
    assert all(f in have for f in facts if f[0] in ("holds", "happens"))
