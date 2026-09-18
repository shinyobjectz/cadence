"""Regressions for friction repairs (2026-09-18).

Each of these encodes a bug that actually happened while building with Cadence, not a
hypothetical. They are grouped by the thing that was wrong, and every one of them failed
before the corresponding fix.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import facts as FA
from cadence_vision.query import Log


def comp(tmp_path: Path, body: str, dur: float = 4.0, bg: str = "#0c1018") -> Path:
    p = tmp_path / "r.lua"
    p.write_text(
        'local e = require("ellua")\nreturn e.comp {\n'
        f'  width = 320, height = 180, duration = {dur}, fps = 10, background = "{bg}",\n'
        f"  scene = function(s)\n{body}\n  end,\n}}\n")
    return p


# ---------------------------------------------------------------- captions t1

def test_a_cue_stops_at_its_own_t1(tmp_path):
    """A cue used to hold until the next one started, and the last one to the end of the
    comp -- so a readout from chapter 01 was still on screen in chapter 05."""
    lg = Log.parse(FA.lift(comp(tmp_path,
        '    s:captions { x = 160, y = 90, size = 28, color = "#e8edf7", anchor = "center",\n'
        '      cues = { { 0.5, 1.5, "ONE" } } }')))
    spans = [(h.bind["S"], h.bind["T0"], h.bind["T1"]) for h in lg.match("holds(text(_, S), T0, T1)")]
    assert spans == [("ONE", 0.5, 1.5)]
    assert [h.fact for h in lg.at(1.0) if h.fact[1][0] == "text"]
    assert not [h.fact for h in lg.at(3.0) if h.fact[1][0] == "text"]


def test_contiguous_cues_hand_over_with_no_gap(tmp_path):
    """The fix must not insert a blank frame between cues that already abutted -- that is
    what every shipped captions comp relies on, and what the goldens pin."""
    lg = Log.parse(FA.lift(comp(tmp_path,
        '    s:captions { x = 160, y = 90, size = 28, color = "#e8edf7", anchor = "center",\n'
        '      cues = { { 0.5, 1.5, "ONE" }, { 1.5, 2.5, "TWO" } } }')))
    seen = {h.bind["S"]: (h.bind["T0"], h.bind["T1"]) for h in lg.match("holds(text(_, S), T0, T1)")}
    assert seen["ONE"][1] == seen["TWO"][0] == 1.5, "no blank between abutting cues"
    assert [h.fact[1][2] for h in lg.at(1.5) if h.fact[1][0] == "text"] == ["TWO"]


def test_explicit_blank_cue_still_works(tmp_path):
    """The old manual workaround must keep working for comps that already use it."""
    lg = Log.parse(FA.lift(comp(tmp_path,
        '    s:captions { x = 160, y = 90, size = 28, color = "#e8edf7", anchor = "center",\n'
        '      cues = { { 0.5, 1.5, "ONE" }, { 1.5, 4.0, "" } } }')))
    assert not [h.fact for h in lg.at(2.0) if h.fact[1][0] == "text"]


# ---------------------------------------------------------------- colour shorthand

@pytest.mark.parametrize("short,long", [("#abc", "#aabbcc"), ("#0c1", "#00cc11")])
def test_three_digit_hex_matches_the_long_form(tmp_path, short, long):
    """#000 used to fail in color.parse with a traceback naming neither the colour nor
    the node. Every other tool in the ecosystem takes the short form."""
    a = FA.lift(comp(tmp_path, '    s:rect { x = 0, y = 0, w = 10, h = 10, color = "%s" }' % short))
    b = FA.lift(comp(tmp_path, '    s:rect { x = 0, y = 0, w = 10, h = 10, color = "%s" }' % long))
    assert "color(rect1" in a
    assert [l for l in a.splitlines() if l.startswith("color(")] == \
           [l for l in b.splitlines() if l.startswith("color(")]


def test_four_digit_hex_carries_alpha(tmp_path):
    p = comp(tmp_path, '    s:rect { x = 0, y = 0, w = 10, h = 10, color = "#0c18" }')
    assert "color(rect1" in FA.lift(p)


# ---------------------------------------------------------------- long comps

def test_long_comps_are_sampled_coarsely_and_say_so(tmp_path):
    """A 160s comp at 30fps is 4814 evaluations behind a 120s subprocess timeout."""
    long_body = '    s:rect { x = 0, y = 0, w = 10, h = 10, color = "#3ee0c6" }'
    p = tmp_path / "long.lua"
    p.write_text(
        'local e = require("ellua")\nreturn e.comp {\n'
        '  width = 320, height = 180, duration = 300, fps = 30, background = "#0c1018",\n'
        f"  scene = function(s)\n{long_body}\n  end,\n}}\n")
    head = FA.lift(p).splitlines()[0]
    assert "sampled at" in head, head


def test_short_comps_keep_per_frame_sampling(tmp_path):
    head = FA.lift(comp(tmp_path, '    s:rect { x = 0, y = 0, w = 10, h = 10, color = "#3ee0c6" }')).splitlines()[0]
    assert "sampled at" not in head


# ---------------------------------------------------------------- edit errors

def test_a_missing_node_error_lists_the_nodes_that_exist(tmp_path):
    """Node ids cannot be guessed: s:captions builds a *text* node, and the index counts
    nodes rather than constructors of that kind."""
    from cadence_vision import edits as E
    p = comp(tmp_path,
        '    s:rect { x = 0, y = 0, w = 10, h = 10, color = "#3ee0c6" }\n'
        '    s:captions { x = 160, y = 90, size = 28, color = "#e8edf7",\n'
        '      cues = { { 0.5, 1.5, "ONE" } } }')
    problems = E.validate(p, [{"verb": "set_cue", "node": "text1", "index": 0, "t0": 1.0}])
    assert problems and "rect1" in problems[0] and "text2" in problems[0], problems


# ---------------------------------------------------------------- quote-aware parsing

from cadence_vision.facts import parse_term
from cadence_vision.query import parse_pattern, unify


@pytest.mark.parametrize("src,arity", [
    ('says(a1, "one, two and three")', 3),
    ('says(a1, "no commas here")', 3),
    ('happens(word(w1, "hello,"), 1.0)', 3),
    ('holds(text(t6, "a, b, c"), 0.5, 1.5)', 4),
    ('entity(e1, "beer bottle", seed(8.5))', 4),
])
def test_a_comma_inside_a_string_is_not_an_argument_separator(src, arity):
    """`parse_term` split on commas without tracking quotes, so any fact carrying prose
    parsed with the wrong arity and silently stopped matching every pattern. On the lesson
    comp that hid 10 of 11 says() facts and 35 of 414 word facts -- and the missing words
    looked exactly like an aligner that drops 8.5% of what it hears."""
    assert len(parse_term(src)) == arity
    assert len(parse_pattern(src)) == arity


def test_prose_survives_the_round_trip():
    t = parse_term('says(a1, "Most video tools store a timeline: a list of clips, end to end.")')
    assert t[2] == "Most video tools store a timeline: a list of clips, end to end."


def test_escaped_quote_inside_a_string():
    t = parse_term(r'says(a1, "she said \"go\", then left")')
    assert len(t) == 3 and t[2].count("go") == 1


def test_patterns_still_match_facts_that_contain_commas():
    fact = parse_term('says(audio1, "one, two")')
    assert unify(parse_pattern("says(E, S)"), fact) == {"E": "audio1", "S": "one, two"}
    assert unify(parse_pattern('says(E, "one, two")'), fact) == {"E": "audio1"}
    assert unify(parse_pattern('says(E, "other")'), fact) is None


# --- unknown anchors are reported, not silently placed top-left
import json as _json
import subprocess as _sp


def test_an_unknown_anchor_is_a_lint_warning(tmp_path):
    comp = tmp_path / "anch.lua"
    comp.write_text('local e = require("ellua")\nreturn e.comp {\n'
                    '  width = 640, height = 360, duration = 1, fps = 10, background = "#101418",\n'
                    '  scene = function(s)\n'
                    '    s:text { x = 300, y = 100, text = "r", size = 40, color = "#ffffff", anchor = "topright" }\n'
                    '    s:text { x = 300, y = 200, text = "c", size = 40, color = "#ffffff", anchor = "center" }\n'
                    '  end,\n}\n')
    out = _sp.run([str(ROOT / "bin/cadence"), "lint", str(comp), "--json"],
                  capture_output=True, text=True, cwd=ROOT, timeout=120)
    got = [f for f in _json.loads(out.stdout)["findings"] if f["code"] == "unknown_anchor"]
    assert [f["node"] for f in got] == ["text1"]
