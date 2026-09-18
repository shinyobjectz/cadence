"""G11, measured on embedding sequences with a boundary built into them.

Novelty detection is arithmetic over a similarity matrix, so a sequence that is one thing and then
another has a known answer, and a sequence that drifts steadily has the answer "nothing here".
Both cases matter: the second is what stops a slow pan from being reported as a run of events.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import boundaries as B  # noqa: E402
from cadence_vision.facts import Log, parse_log  # noqa: E402

FPS = B.SAMPLE_FPS


def seq(*blocks) -> tuple[np.ndarray, np.ndarray]:
    """Embeddings made of constant blocks: [(n, axis), ...] -> (times, unit vectors)."""
    rows = []
    for n, axis in blocks:
        v = np.zeros(16)
        v[axis] = 1.0
        rows += [v] * n
    V = np.array(rows)
    return np.arange(len(V)) / FPS, V


def drift(n: int, turns: float = 1.0) -> tuple[np.ndarray, np.ndarray]:
    """A steady rotation: every frame differs a little from the last, with no moment of change."""
    a = np.linspace(0, turns * np.pi / 2, n)
    V = np.zeros((n, 16))
    V[:, 0], V[:, 1] = np.cos(a), np.sin(a)
    return np.arange(n) / FPS, V


# ----------------------------------------------------------------------------- the kernel

def test_the_checkerboard_is_antisymmetric_across_its_quadrants():
    K = B.checkerboard(4)
    n = K.shape[0]
    assert K[0, 0] > 0 and K[-1, -1] > 0, "the two self-similarity quadrants are positive"
    assert K[0, -1] < 0 and K[-1, 0] < 0, "the cross quadrants are negative"
    assert K.shape == (n, n) and abs(float(K.sum())) < 1e-9


def test_novelty_peaks_where_the_content_turns_over():
    ts, V = seq((20, 0), (20, 1))
    n = B.novelty(V)
    assert int(np.argmax(n)) == pytest.approx(20, abs=1), n.round(2)


def test_a_sequence_shorter_than_the_kernel_scores_zero():
    _ts, V = seq((4, 0))
    assert not B.novelty(V).any()


# ----------------------------------------------------------------------------- peaks

def test_one_boundary_is_reported_once():
    ts, V = seq((20, 0), (20, 1))
    got = B.peaks(ts, B.novelty(V))
    assert len(got) == 1
    assert got[0][0] == pytest.approx(20 / FPS, abs=2 / FPS)


def test_two_boundaries_are_both_reported():
    ts, V = seq((16, 0), (16, 1), (16, 2))
    got = B.peaks(ts, B.novelty(V))
    assert len(got) == 2, got
    assert [round(t * FPS) for t, _ in got] == [16, 32]


def test_steady_drift_has_no_boundary():
    """A slow pan changes every frame and turns over at no frame. Reporting a boundary here would
    fill the log with moments an editor cannot use."""
    ts, V = drift(60)
    assert B.peaks(ts, B.novelty(V)) == []


def test_a_static_shot_has_no_boundary():
    ts, V = seq((60, 0))
    assert B.peaks(ts, B.novelty(V)) == []


def test_peaks_closer_than_the_minimum_gap_keep_the_stronger_one():
    ts = np.arange(40) / FPS
    nov = np.zeros(40)
    nov[B.KERNEL:40 - B.KERNEL] = 0.1
    nov[20], nov[21] = 5.0, 4.0
    got = B.peaks(ts, nov, min_gap=1.0)
    assert len(got) == 1 and got[0][0] == pytest.approx(20 / FPS)


def test_confidence_is_the_percentile_within_the_sequence():
    ts, V = seq((20, 0), (20, 1))
    _t, conf = B.peaks(ts, B.novelty(V))[0]
    assert conf >= B.MIN_CONF


# ----------------------------------------------------------------------------- emitted facts

class _Src:
    duration = 15.0
    media = "x.mp4"
    kind = "video"


def _wire(monkeypatch, ts, V):
    import cadence_vision.sources as S
    monkeypatch.setattr(B.embed, "available", lambda: True)
    monkeypatch.setattr(B.embed, "strip_embeddings", lambda key, frames: V)
    monkeypatch.setattr(S, "open_source", lambda p: _Src())
    monkeypatch.setattr(S, "scan", lambda src, fps: (ts, np.zeros((len(ts), 8, 8, 3), np.uint8)))


def test_a_boundary_is_emitted_with_provenance(monkeypatch):
    ts, V = seq((20, 0), (20, 1))
    _wire(monkeypatch, ts, V)
    L = Log()
    assert B.emit("x.mp4", L) == 1
    facts = parse_log(L.text())
    ev = [f for f in facts if f[0] == "happens"]
    assert ev and ev[0][1] == ("action_boundary", "s1")
    have = {f[1] for f in facts if f[0] == "src"}
    assert all(f in have for f in facts if f[0] in ("holds", "happens"))


def test_a_boundary_at_a_known_cut_is_not_repeated(monkeypatch):
    """The cut is already in the log. Two names for one frame would have a reader cut twice."""
    ts, V = seq((20, 0), (20, 1))
    _wire(monkeypatch, ts, V)
    L = Log()
    assert B.emit("x.mp4", L, cuts=[20 / FPS]) == 0
    assert L.text().strip() == ""


def test_boundaries_are_attributed_to_the_shot_they_fall_in(monkeypatch):
    ts, V = seq((16, 0), (16, 1), (16, 2))
    _wire(monkeypatch, ts, V)
    L = Log()
    shots = [("s1", 0.0, 16 / FPS), ("s2", 16 / FPS, 48 / FPS)]
    B.emit("x.mp4", L, shots=shots, cuts=[16 / FPS])
    names = {f[1][1] for f in parse_log(L.text()) if f[0] == "happens"}
    assert names <= {"s2"}, "the first block is too short to score, the second carries the boundary"


def test_no_embeddings_available_is_silent(monkeypatch):
    monkeypatch.setattr(B.embed, "available", lambda: False)
    L = Log()
    assert B.emit("x.mp4", L) == 0
    assert L.text().strip() == ""
