"""Pattern matching over a fact log.

The rules worth pinning: a capitalised atom binds, a quoted string never does, a repeated
variable must agree with itself, and confidence filtering must not silently drop exact facts
(which have no confidence at all, because they are not measurements).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision.query import Corpus, Hit, Lit, Log, is_var, parse_pattern, term_str, unify

PERCEIVED = """
% fact log for a.mp4 — perceived
clip("a.mp4").
src(clip("a.mp4"), ffprobe, 0.99).
entity(e1, "beer bottle", seed(8.520)).
src(entity(e1, "beer bottle", seed(8.520)), detector_agreement, 0.71).
holds(visible(e1), 2.100, 10.800).
src(holds(visible(e1), 2.100, 10.800), sam2, 0.71).
holds(visible(e2), 1.000, 4.000).
src(holds(visible(e2), 1.000, 4.000), sam2, 0.30).
happens(release(e1, e2), 8.500).
src(happens(release(e1, e2), 8.500), contact_gap, 1.00).
"""

EXACT = """
% fact log for b.lua — lifted from comp state, exact (no src lines)
entity(text6, "text", node("text6")).
holds(text(text6, "TYPE"), 0.567, 1.467).
holds(visible(text6), 0.000, 3.600).
happens(text_change(text6), 0.567).
"""


@pytest.fixture
def perceived():
    return Log.parse(PERCEIVED, "a")


@pytest.fixture
def exact():
    return Log.parse(EXACT, "b")


def test_variable_binds(perceived):
    hits = perceived.match("happens(release(A, B), T)")
    assert len(hits) == 1
    assert hits[0].bind == {"A": "e1", "B": "e2", "T": 8.5}


def test_wildcard_binds_nothing(perceived):
    hits = perceived.match("holds(visible(_), _, _)")
    assert len(hits) == 2
    assert all(h.bind == {} for h in hits)


def test_repeated_variable_must_agree():
    # same(X, X) may only match a fact whose two arguments are identical
    lg = Log.parse("same(e1, e1).\nsame(e1, e2).\n", "t")
    assert len(lg.match("same(X, X)")) == 1
    assert len(lg.match("same(X, Y)")) == 2


def test_quoted_string_is_a_literal_not_a_variable(exact):
    """"TYPE" is capitalised, so a naive reader would treat it as a variable."""
    assert not is_var(parse_pattern('"TYPE"'))
    assert isinstance(parse_pattern('"TYPE"'), Lit)
    assert len(exact.match('holds(text(E, "TYPE"), T0, T1)')) == 1
    assert len(exact.match('holds(text(E, "OTHER"), T0, T1)')) == 0


def test_src_lines_are_metadata_not_results(perceived):
    """A src line describes another fact; it must never come back as a hit of its own."""
    assert perceived.match("src(_, _, _)") == []
    assert all(h.fact[0] != "src" for h in perceived.match("_"))


def test_provenance_attaches_to_hits(perceived):
    h = perceived.match("happens(release(A, B), T)")[0]
    assert (h.producer, h.conf) == ("contact_gap", 1.0)
    assert not h.exact


def test_min_conf_filters_perceived(perceived):
    assert len(perceived.match("holds(visible(E), _, _)")) == 2
    assert len(perceived.match("holds(visible(E), _, _)", min_conf=0.5)) == 1


def test_min_conf_never_drops_exact_facts(exact):
    """An exact fact has no confidence because it is not a measurement. Filtering on
    confidence must not be a way to accidentally hide the facts that are certain."""
    hits = exact.match("holds(visible(E), _, _)", min_conf=0.99)
    assert len(hits) == 1
    assert hits[0].exact and hits[0].conf is None


def test_at_is_half_open(perceived):
    """holds(F, T0, T1) covers T0 and not T1, so abutting intervals never both match."""
    assert [h.fact for h in perceived.at(2.100) if h.fact[1] == ("visible", "e1")]
    assert not [h.fact for h in perceived.at(10.800) if h.fact[1] == ("visible", "e1")]
    assert [h.fact for h in perceived.at(10.799) if h.fact[1] == ("visible", "e1")]


def test_at_catches_instants_within_eps(perceived):
    assert [h for h in perceived.at(8.500) if h.fact[0] == "happens"]
    assert [h for h in perceived.at(8.52, eps=0.04) if h.fact[0] == "happens"]
    assert not [h for h in perceived.at(8.70, eps=0.04) if h.fact[0] == "happens"]


def test_entities(perceived, exact):
    assert perceived.entities() == ["e1"]
    assert exact.entities() == ["text6"]


def test_corpus_reports_which_clip(perceived, exact):
    c = Corpus([perceived, exact])
    assert c.clips("happens(release(A, B), T)") == ["a"]
    assert c.clips("holds(visible(E), _, _)") == ["a", "b"]
    assert c.clips("holds(nonsense(E), _, _)") == []


def test_pattern_arity_must_match(perceived):
    """holds/4 and holds/3 are different facts; a short pattern must not match a long fact."""
    assert perceived.match("holds(visible(E), T0)") == []


def test_unify_is_pure():
    b = {"A": "e1"}
    assert unify(parse_pattern("f(A, B)"), ("f", "e1", "e2"), b) == {"A": "e1", "B": "e2"}
    assert b == {"A": "e1"}, "bindings passed in must not be mutated"


def test_term_str_round_trips(perceived):
    for h in perceived.match("_"):
        assert parse_pattern(term_str(h.fact)) == h.fact


def test_when_event_matches_any_arity():
    # Regression: when(event=) used a one-argument pattern and missed release(e1, e2).
    lg = Log.parse(
        "happens(release(e1, e2), 8.5).\nhappens(beat(b1), 1.0).\nhappens(cut, 2.0).\n"
        "src(happens(release(e1, e2), 8.5), contact_gap, 1.00).\n", "t")
    assert lg.when(event="release") == [8.5]
    assert lg.when(event="beat") == [1.0]
    assert lg.when(event="cut") == [2.0]
    assert lg.when(event="nope") == []
