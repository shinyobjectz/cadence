"""G1. Speaker turns from clustered embeddings — the parts that do not need the model."""

import numpy as np
import pytest

from cadence_vision import diarize


def _unit(v):
    v = np.asarray(v, np.float32)
    return v / np.linalg.norm(v)


# ---------------------------------------------------------------- windows

def test_a_window_never_spans_a_silence():
    got = diarize.windows([(0.0, 2.0), (5.0, 7.0)], win=1.5, hop=0.75)
    assert all(not (a < 2.0 < b) and not (a < 5.0 < b) for a, b in got)


def test_a_short_reply_still_gets_one_window():
    assert diarize.windows([(0.0, 1.0)], win=1.5, min_win=0.8) == [(0.0, 1.0)]


def test_a_span_too_short_to_hold_a_voice_is_dropped():
    assert diarize.windows([(0.0, 0.3)], min_win=0.8) == []


def test_the_tail_of_a_long_span_is_not_lost():
    got = diarize.windows([(0.0, 4.0)], win=1.5, hop=0.75, min_win=0.8)
    assert got[-1][1] == pytest.approx(4.0)


def test_windows_overlap_by_the_hop():
    got = diarize.windows([(0.0, 4.0)], win=1.5, hop=0.75)
    assert got[1][0] - got[0][0] == pytest.approx(0.75)


# ---------------------------------------------------------------- cluster

def test_two_voices_come_out_as_two_clusters():
    V = np.stack([_unit([1, 0, 0]), _unit([0.99, 0.1, 0]), _unit([0, 1, 0]), _unit([0, 0.99, 0.1])])
    assert len(np.unique(diarize.cluster(V))) == 2


def test_one_voice_stays_one_cluster():
    V = np.stack([_unit([1, 0.02 * i, 0]) for i in range(5)])
    assert len(np.unique(diarize.cluster(V))) == 1


def test_a_single_window_clusters_without_complaint():
    assert diarize.cluster(np.stack([_unit([1, 0, 0])])).tolist() == [0]


# ------------------------------------------------------------- separation

def test_one_cluster_has_no_separation_to_report():
    V = np.stack([_unit([1, 0.01 * i, 0]) for i in range(4)])
    assert diarize.separation(V, np.zeros(4, int)).tolist() == [0.0] * 4


def test_well_separated_voices_score_high():
    V = np.stack([_unit([1, 0.01 * i, 0]) for i in range(5)]
                 + [_unit([0.01 * i, 1, 0]) for i in range(5)])
    sep = diarize.separation(V, np.array([0] * 5 + [1] * 5))
    assert sep.min() > 0.9


def test_separation_is_ranked_against_this_clip_not_an_absolute():
    """X1. The same geometric gap scores differently depending on how tight the clusters are,
    which is the point: a clip whose speakers scatter widely needs a wider gap to mean anything."""
    tight = np.stack([_unit([1, 0.01, 0]), _unit([1, -0.01, 0]),
                      _unit([0.7, 0.71, 0]), _unit([0.71, 0.7, 0])])
    loose = np.stack([_unit([1, 0.4, 0]), _unit([1, -0.4, 0]),
                      _unit([0.7, 0.71, 0]), _unit([0.71, 0.7, 0])])
    lab = np.array([0, 0, 1, 1])
    assert diarize.separation(tight, lab).mean() >= diarize.separation(loose, lab).mean()


# ------------------------------------------------------------------ turns

def test_contiguous_windows_of_one_voice_become_one_turn():
    wins = [(0.0, 1.5), (0.75, 2.25), (1.5, 3.0)]
    got = diarize.turns(wins, np.array([0, 0, 0]), np.ones(3))
    assert len(got) == 1 and got[0][:2] == (0.0, 3.0)


def test_a_turn_is_only_as_good_as_its_weakest_moment():
    wins = [(0.0, 1.5), (0.75, 2.25)]
    got = diarize.turns(wins, np.array([0, 0]), np.array([0.9, 0.4]))
    assert got[0][3] == pytest.approx(0.4)


def test_a_change_of_voice_splits_the_turn():
    wins = [(0.0, 1.5), (0.75, 2.25), (1.5, 3.0)]
    got = diarize.turns(wins, np.array([0, 1, 1]), np.ones(3))
    assert [t[2] for t in got] == [0, 1]


def test_a_turn_shorter_than_a_syllable_is_not_a_turn():
    assert diarize.turns([(0.0, 0.2)], np.array([0]), np.ones(1), min_turn=0.5) == []


def test_a_gap_between_windows_starts_a_new_turn():
    got = diarize.turns([(0.0, 1.5), (5.0, 6.5)], np.array([0, 0]), np.ones(2))
    assert len(got) == 2


# ----------------------------------------------------------- naming

def test_a_cluster_inside_one_entity_s_speech_takes_its_name():
    tns = [(0.0, 2.0, 0, 0.8)]
    assert diarize.name_clusters(tns, {"e2": [(0.0, 2.0)]})[0][0] == "e2"


def test_a_cluster_nobody_on_screen_accounts_for_stays_anonymous():
    tns = [(0.0, 2.0, 0, 0.8)]
    assert diarize.name_clusters(tns, {"e2": [(8.0, 9.0)]})[0][0] == "spk1"


def test_a_cluster_split_between_two_entities_names_neither():
    tns = [(0.0, 2.0, 0, 0.8)]
    got = diarize.name_clusters(tns, {"e2": [(0.0, 0.9)], "e3": [(1.1, 2.0)]})
    assert got[0][0] == "spk1"


def test_naming_confidence_is_the_share_of_the_cluster_that_entity_covers():
    tns = [(0.0, 2.0, 0, 0.8)]
    assert diarize.name_clusters(tns, {"e2": [(0.0, 1.6)]})[0][1] == pytest.approx(0.8)


def test_without_any_known_speaker_every_cluster_is_anonymous():
    tns = [(0.0, 2.0, 0, 0.8), (2.0, 4.0, 1, 0.8)]
    got = diarize.name_clusters(tns, None)
    assert sorted(n for n, _ in got.values()) == ["spk1", "spk2"]


# ------------------------------------------------------------------- emit

class _Log:
    def __init__(self):
        self.facts, self.comments = [], []

    def c(self, s):
        self.comments.append(s)

    def fact(self, f, producer, conf):
        self.facts.append((f, producer, conf))


def _wire(monkeypatch, V):
    monkeypatch.setattr(diarize, "available", lambda: True)
    monkeypatch.setattr(diarize, "embed", lambda media, wins, sr=diarize.SR: V)


def test_one_voice_emits_nothing(monkeypatch):
    """X2. A clip with one speaker has no turn structure, and inventing one is worse than silence."""
    V = np.stack([_unit([1, 0.01 * i, 0]) for i in range(8)])
    _wire(monkeypatch, V)
    L = _Log()
    assert diarize.emit("x.mp4", L, [(0.0, 6.0)]) == 0
    assert L.facts == []


def test_too_little_speech_to_cluster_emits_nothing(monkeypatch):
    _wire(monkeypatch, np.zeros((0, 192), np.float32))
    L = _Log()
    assert diarize.emit("x.mp4", L, [(0.0, 1.2)]) == 0


def test_two_voices_emit_turns_and_a_change(monkeypatch):
    wins = diarize.windows([(0.0, 6.0)])
    half = len(wins) // 2
    V = np.stack([_unit([1, 0.01 * i, 0]) for i in range(half)]
                 + [_unit([0.01 * i, 1, 0]) for i in range(len(wins) - half)])
    _wire(monkeypatch, V)
    L = _Log()
    n = diarize.emit("x.mp4", L, [(0.0, 6.0)])
    assert n == 2
    assert sum("speech_turn" in f for f, _p, _c in L.facts) == 2
    assert sum("speaker_change" in f for f, _p, _c in L.facts) == 1


def test_emitted_turns_carry_the_entity_name_when_one_is_known(monkeypatch):
    wins = diarize.windows([(0.0, 6.0)])
    half = len(wins) // 2
    V = np.stack([_unit([1, 0.01 * i, 0]) for i in range(half)]
                 + [_unit([0.01 * i, 1, 0]) for i in range(len(wins) - half)])
    _wire(monkeypatch, V)
    L = _Log()
    diarize.emit("x.mp4", L, [(0.0, 6.0)], known={"e2": [(0.0, 3.0)]})
    assert any("speech_turn(e2)" in f for f, _p, _c in L.facts)
    assert any("speech_turn(spk" in f for f, _p, _c in L.facts)


def test_every_emitted_fact_has_a_producer_and_a_confidence(monkeypatch):
    """X2. Perceived facts without provenance would read as exact."""
    wins = diarize.windows([(0.0, 6.0)])
    half = len(wins) // 2
    V = np.stack([_unit([1, 0.01 * i, 0]) for i in range(half)]
                 + [_unit([0.01 * i, 1, 0]) for i in range(len(wins) - half)])
    _wire(monkeypatch, V)
    L = _Log()
    diarize.emit("x.mp4", L, [(0.0, 6.0)])
    assert all(p == "ecapa_cluster" and 0.0 < c <= 1.0 for _f, p, c in L.facts)


def test_without_speechbrain_the_producer_is_silent(monkeypatch):
    monkeypatch.setattr(diarize, "available", lambda: False)
    L = _Log()
    said = []
    assert diarize.emit("x.mp4", L, [(0.0, 6.0)], log=said.append) == 0
    assert said and "speechbrain" in said[0]
