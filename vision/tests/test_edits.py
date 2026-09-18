"""The edit surface: propose, refuse, apply, prove.

The properties worth pinning are the safety ones. A malformed edit must be refused with a
reason rather than half-applied; proposing must never touch the file; and the identity edit
must be byte-exact, because every frame-hash claim downstream rests on it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import edits as E
from cadence_vision import lower as LO

COMP = ROOT / "evals" / "cases" / "captions.lua"
GOOD = [{"verb": "set_cue", "node": "text6", "index": 0, "t0": 0.85}]


def _renderer() -> bool:
    r = subprocess.run(["bin/cadence", "hash", str(COMP)], cwd=ROOT,
                       capture_output=True, text=True)
    return any(ln.startswith("FRAME") for ln in r.stdout.splitlines())


needs_renderer = pytest.mark.skipif(not _renderer(), reason="needs the LOVE renderer")


# ---------------------------------------------------------------- refusing

def test_unknown_verb_is_refused_and_lists_the_real_ones():
    problems = E.validate(COMP, [{"verb": "teleport", "node": "text6"}])
    assert len(problems) == 1
    assert "teleport" in problems[0]
    for verb in LO.VERBS:
        assert verb in problems[0], "the refusal should say what is available"


def test_missing_verb_is_refused():
    assert E.validate(COMP, [{"node": "text6"}])


def test_unknown_node_is_refused():
    problems = E.validate(COMP, [{"verb": "set_cue", "node": "nope", "index": 0, "t0": 1.0}])
    assert problems and "nope" in problems[0]


def test_out_of_range_cue_is_refused_with_the_real_count():
    problems = E.validate(COMP, [{"verb": "set_cue", "node": "text6", "index": 99, "t0": 1.0}])
    assert problems and "3 cues" in problems[0]


def test_cue_edit_on_a_node_with_no_cues_is_refused():
    problems = E.validate(COMP, [{"verb": "set_cue", "node": "rect4", "index": 0, "t0": 1.0}])
    assert problems and "0 cues" in problems[0]


def test_a_refused_edit_reports_not_ok_and_writes_nothing(tmp_path):
    scratch = tmp_path / "c.lua"
    shutil.copy(COMP, scratch)
    before = scratch.read_bytes()
    r = E.apply(scratch, [{"verb": "teleport", "node": "text6"}], verify="none", write=True)
    assert r["ok"] is False and "problems" in r
    assert scratch.read_bytes() == before, "a refused edit must not touch the file"


def test_good_edit_validates_clean():
    assert E.validate(COMP, GOOD) == []


# ---------------------------------------------------------------- applying

def test_proposing_does_not_write():
    before = COMP.read_bytes()
    r = E.apply(COMP, GOOD, verify="facts", write=False)
    assert r["ok"] and r["written"] is False
    assert COMP.read_bytes() == before


def test_write_true_actually_writes(tmp_path):
    scratch = tmp_path / "c.lua"
    shutil.copy(COMP, scratch)
    r = E.apply(scratch, GOOD, verify="none", write=True)
    assert r["written"] is True
    assert '{ 0.85, 1.45, "Hold the cut." }' in scratch.read_text()


def test_identity_edit_is_byte_exact(tmp_path):
    """Everything downstream rests on this: no edits must mean no change at all."""
    scratch = tmp_path / "c.lua"
    shutil.copy(COMP, scratch)
    r = E.apply(scratch, [], verify="none", write=True)
    assert r["ok"]
    assert scratch.read_bytes() == COMP.read_bytes()


def test_lua_diff_touches_exactly_one_line():
    r = E.apply(COMP, GOOD, verify="none")
    changed = [l for l in r["lua_diff"]
               if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]
    assert len(changed) == 2, changed          # one removed, one added
    assert "0.55" in changed[0] and "0.85" in changed[1]


def test_fact_diff_names_only_the_facts_the_edit_moved():
    r = E.apply(COMP, GOOD, verify="facts")
    moved = [ln for d in r["fact_diff"] for ln in d["before"]]
    assert all("text6" in ln for ln in moved), moved
    assert any("0.567" in ln for ln in moved)


# ---------------------------------------------------------------- proving

@needs_renderer
def test_frames_verification_is_contiguous_and_bounded():
    r = E.apply(COMP, GOOD, verify="frames")
    f = r["frames"]
    assert f["n"] == 108
    assert f["changed"] == list(range(17, 26))
    assert f["identical"] == 99
    assert f["contiguous"] is True
    assert f["span"] == [0.567, 0.833]


@needs_renderer
def test_an_edit_that_changes_nothing_visible_changes_no_frames():
    """Rewording a cue to itself is a real edit to the source and no edit to the picture."""
    same = [{"verb": "set_cue", "node": "text6", "index": 0, "text": "Hold the cut."}]
    r = E.apply(COMP, same, verify="frames")
    assert r["ok"] and r["frames"]["changed"] == []
