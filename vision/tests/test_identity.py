"""G7, measured on vectors whose answer is arithmetic.

The matching rules — grouping strongest pair first, never joining two tracks that shared a frame,
confidence as a percentile against pairs known to be different — are pure functions of the
embeddings, so they are tested on embeddings built by hand. What CLIP actually puts in those
vectors is a separate question, measured on a rendered multi-shot assembly.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import identity as ID  # noqa: E402
from cadence_vision.facts import Log, parse_log  # noqa: E402


def track(eid, t0, t1, step=0.1):
    n = max(2, int(round((t1 - t0) / step)) + 1)
    return {"id": eid, "cls": "person", "conf": 0.9, "seed_t": t0, "how": "detector",
            "obs": [(round(t0 + i * step, 2), (0.2, 0.2, 0.5, 0.8), 0.06, [0.35, 0.5], None)
                    for i in range(n)]}


def vec(*parts) -> np.ndarray:
    v = np.zeros(8)
    for i, w in parts:
        v[i] = w
    return v / np.linalg.norm(v)


NULL = [0.10, 0.15, 0.20, 0.12, 0.18, 0.22, 0.14, 0.16]


# ----------------------------------------------------------------------------- overlap

def test_entities_seen_at_the_same_moment_overlap():
    assert ID.overlap(track("e1", 0.0, 3.0), track("e2", 2.0, 5.0))


def test_entities_in_different_shots_do_not_overlap():
    assert not ID.overlap(track("e1", 0.0, 3.0), track("e2", 6.0, 9.0))


def test_two_things_on_screen_together_are_never_claimed_to_be_one():
    """The constraint that makes the null distribution sound, and that stops the obvious error."""
    a, b = track("e1", 0.0, 5.0), track("e2", 0.0, 5.0)
    v = {"e1": vec((0, 1)), "e2": vec((0, 1))}          # identical appearance
    assert ID.match([a, b], v, NULL) == []


# ----------------------------------------------------------------------------- null calibration

def test_the_null_is_built_from_simultaneous_entities():
    tracks = [track("e1", 0.0, 3.0), track("e2", 0.0, 3.0), track("e3", 6.0, 9.0)]
    v = {"e1": vec((0, 1)), "e2": vec((1, 1)), "e3": vec((0, 1))}
    null = ID.null_distribution(tracks, v)
    assert len(null) == 1 and null[0] == pytest.approx(0.0), "only the simultaneous pair"


def test_nothing_is_claimed_without_enough_null_pairs():
    """A clip that never shows two things at once offers no way to calibrate, so it gets no
    identity facts rather than a threshold imported from someone else's footage."""
    tracks = [track("e1", 0.0, 3.0), track("e2", 6.0, 9.0)]
    v = {"e1": vec((0, 1)), "e2": vec((0, 1))}
    assert ID.match(tracks, v, ID.null_distribution(tracks, v)) == []
    assert ID.match(tracks, v, NULL[:2]) == []


def test_confidence_is_the_percentile_against_known_different_pairs():
    tracks = [track("e1", 0.0, 3.0), track("e2", 6.0, 9.0)]
    v = {"e1": vec((0, 1)), "e2": vec((0, 1))}          # cosine 1.0, beats every null pair
    got = ID.match(tracks, v, NULL)
    assert got == [("e2", "e1", 1.0)]


def test_a_pair_no_better_than_the_null_is_not_claimed():
    tracks = [track("e1", 0.0, 3.0), track("e2", 6.0, 9.0)]
    v = {"e1": vec((0, 1)), "e2": vec((0, 1), (1, 6.6))}   # cosine ~0.15, mid-null
    assert ID.match(tracks, v, NULL) == []


# ----------------------------------------------------------------------------- matching

def test_same_as_points_at_the_earlier_sighting():
    tracks = [track("e1", 6.0, 9.0), track("e2", 0.0, 3.0)]
    v = {"e1": vec((0, 1)), "e2": vec((0, 1))}
    later, earlier, _c = ID.match(tracks, v, NULL)[0]
    assert (later, earlier) == ("e1", "e2")


def test_two_lookalikes_in_one_frame_cannot_join_the_same_identity():
    """A crowd must not collapse into one person: e2 and e3 share a shot, so whichever of them
    joins e1, the other cannot follow it into that group."""
    tracks = [track("e1", 0.0, 3.0), track("e2", 6.0, 9.0), track("e3", 6.0, 9.0)]
    v = {"e1": vec((0, 1)), "e2": vec((0, 1)), "e3": vec((0, 10), (1, 1))}
    got = ID.match(tracks, v, NULL)
    assert [(a, b) for a, b, _ in got] == [("e2", "e1")], got


def test_three_shots_of_one_subject_rejoin_as_one_identity():
    """The case a mutual-best rule got wrong: it linked one pair out of three identical sightings
    and reported the subject as two people."""
    tracks = [track("e1", 0.0, 3.0), track("e2", 6.0, 9.0), track("e3", 12.0, 15.0)]
    v = {"e1": vec((0, 1)), "e2": vec((0, 1)), "e3": vec((0, 1))}
    got = ID.match(tracks, v, NULL)
    assert {(a, b) for a, b, _ in got} == {("e2", "e1"), ("e3", "e1")}, got


def test_tracks_with_no_embedding_are_skipped():
    tracks = [track("e1", 0.0, 3.0), track("e2", 6.0, 9.0)]
    assert ID.match(tracks, {"e1": vec((0, 1))}, NULL) == []


# ----------------------------------------------------------------------------- emitted facts

def test_emitted_facts_carry_provenance(monkeypatch):
    tracks = [track("e1", 0.0, 3.0), track("e2", 0.0, 3.0), track("e3", 6.0, 9.0)]
    monkeypatch.setattr(ID.embed, "available", lambda: True)
    monkeypatch.setattr(ID, "appearance",
                        lambda src, trs, n=ID.SAMPLES: {"e1": vec((0, 1)), "e2": vec((1, 1)),
                                                        "e3": vec((0, 1))})
    monkeypatch.setattr(ID, "null_distribution", lambda trs, v: NULL)
    import cadence_vision.sources as S
    monkeypatch.setattr(S, "open_source", lambda p: type("S", (), {"duration": 20.0})())
    L = Log()
    assert ID.emit("x.mp4", L, tracks, duration=20.0) == 1
    facts = parse_log(L.text())
    claims = [f for f in facts if f[0] in ("holds", "happens")]
    have = {f[1] for f in facts if f[0] == "src"}
    assert claims and all(c in have for c in claims)
    assert ("same_as", "e3", "e1") == claims[0][1]


def test_no_embeddings_available_is_silent(monkeypatch):
    monkeypatch.setattr(ID.embed, "available", lambda: False)
    L = Log()
    assert ID.emit("x.mp4", L, [track("e1", 0.0, 3.0)]) == 0
    assert L.text().strip() == ""
