"""Lift -> lower -> re-lift round trips.

The identity case is the strong one: lowering with no edits must return the source byte for byte,
so every frame hash holds. Each edit case asserts the *fact-level* consequence — the log changed
exactly where the edit pointed and nowhere else — which is a sharper check than a pixel diff and
does not need a renderer.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision.facts import lift, parse_log, provenance  # noqa: E402
from cadence_vision.lower import comp_ids, lower, node_spans  # noqa: E402

CASES = sorted(p.name for p in (ROOT / "evals" / "cases").glob("*.lua"))
CAPTIONS = ROOT / "evals" / "cases" / "captions.lua"


def facts_of(comp: Path, src: str | None = None) -> list[tuple]:
    """Facts for a comp, or for an edited copy of it. Facts naming the file are dropped so an
    edited copy under a temp name stays comparable with the original."""
    if src is None:
        facts = parse_log(lift(comp))
    else:
        tmp = comp.parent / f".roundtrip_{comp.name}"
        tmp.write_text(src)
        try:
            facts = parse_log(lift(tmp))
        finally:
            tmp.unlink(missing_ok=True)
    return [f for f in facts if f[0] not in ("comp", "shot")]


@pytest.mark.parametrize("name", CASES)
def test_lift_parses_and_is_exact(name):
    """Every case lifts, re-parses, and carries no provenance — a lifted fact claims to be exact."""
    facts = parse_log(lift(ROOT / "evals" / "cases" / name))
    assert facts, f"{name} lifted to nothing"
    assert provenance(facts) == {}, f"{name} has src() lines; a lifted fact must be exact"
    assert any(f[0] == "entity" for f in facts)
    for f in facts:
        if f[0] == "holds":
            assert len(f) == 4, f"holds/{len(f) - 1} breaks the grammar: {f}"
            assert f[2] <= f[3], f"interval runs backwards: {f}"
        if f[0] == "happens":
            assert len(f) == 3, f"happens/{len(f) - 1} breaks the grammar: {f}"


@pytest.mark.parametrize("name", CASES)
def test_lower_identity_is_byte_exact(name):
    comp = ROOT / "evals" / "cases" / name
    assert lower(comp, []) == comp.read_text()


def test_node_ids_align_with_source_constructors():
    """Sugar constructors build nodes of another kind, so ids come from the comp, not the source."""
    spans = node_spans(CAPTIONS.read_text(), comp_ids(CAPTIONS))
    assert set(spans) == {"text1", "rect2", "text3", "rect4", "text5", "text6"}
    assert spans["text6"].kind == "captions"      # s:captions{} builds a text node
    assert spans["rect4"].var == "bar"


def test_set_ease_changes_only_that_ease():
    before = facts_of(CAPTIONS)
    after = facts_of(CAPTIONS, lower(CAPTIONS, [{"verb": "set_ease", "node": "rect4", "ease": "linear"}]))
    gone = [f for f in before if f not in after]
    came = [f for f in after if f not in before]
    assert [f[1][0] for f in gone] == ["growing"], gone
    assert [f[1][0] for f in came] == ["growing"], came
    assert gone[0][1][1] == "rect4" and gone[0][1][2] == ("ease", "expoOut")
    assert came[0][1][2] == ("ease", "linear")


def test_set_cue_retimes_exactly_one_caption():
    before = facts_of(CAPTIONS)
    after = facts_of(CAPTIONS, lower(CAPTIONS, [{"verb": "set_cue", "node": "text6", "index": 0, "t0": 0.85}]))
    text_of = lambda fs: {f[1][2]: (f[2], f[3]) for f in fs if f[0] == "holds" and f[1][0] == "text"}
    b, a = text_of(before), text_of(after)
    assert set(b) == set(a), "retiming a cue must not change what any caption says"
    assert b["Hold the cut."][0] == pytest.approx(0.567, abs=0.04)
    assert a["Hold the cut."][0] == pytest.approx(0.867, abs=0.04)
    for k in b:
        if k != "Hold the cut.":
            assert a[k] == b[k], f"cue {k!r} moved but was not named by the edit"


def test_set_prop_moves_one_node_only():
    before = facts_of(CAPTIONS)
    after = facts_of(CAPTIONS, lower(CAPTIONS, [{"verb": "set_prop", "node": "text1", "key": "x", "value": "1100"}]))
    thirds = lambda fs, n: [f[1][2] for f in fs if f[0] == "holds" and f[1][0] == "in_third" and f[1][1] == n]
    assert thirds(before, "text1") == ["left"]
    assert thirds(after, "text1") == ["right"]
    for n in ("rect2", "text3", "rect4", "text5", "text6"):
        assert thirds(after, n) == thirds(before, n), f"{n} moved but was not named by the edit"


def test_unknown_node_is_refused():
    with pytest.raises(KeyError):
        lower(CAPTIONS, [{"verb": "set_prop", "node": "nope9", "key": "x", "value": "0"}])


# The renderer is slow, so the pixel-level proof is opt-in: CADENCE_RENDER_TESTS=1
render_tests = pytest.mark.skipif(os.environ.get("CADENCE_RENDER_TESTS") != "1",
                                  reason="set CADENCE_RENDER_TESTS=1 to render")


def _hashes(comp: Path) -> list[str]:
    r = subprocess.run(["bin/cadence", "hash", str(comp)], cwd=ROOT,
                       capture_output=True, text=True, timeout=900)
    return [l.split()[2] for l in r.stdout.splitlines() if l.startswith("FRAME")]


@render_tests
def test_identity_reproduces_every_frame_hash():
    tmp = CAPTIONS.parent / ".rt_hash.lua"
    tmp.write_text(lower(CAPTIONS, []))
    try:
        assert _hashes(tmp) == _hashes(CAPTIONS)
    finally:
        tmp.unlink(missing_ok=True)


@render_tests
def test_edit_changes_only_the_frames_it_named():
    """Retiming cue 0 from 0.55 s to 0.85 s may only disturb frames between those two times."""
    base = _hashes(CAPTIONS)
    tmp = CAPTIONS.parent / ".rt_hash.lua"
    tmp.write_text(lower(CAPTIONS, [{"verb": "set_cue", "node": "text6", "index": 0, "t0": 0.85}]))
    try:
        after = _hashes(tmp)
    finally:
        tmp.unlink(missing_ok=True)
    assert len(after) == len(base)
    diff = [i for i, (a, b) in enumerate(zip(base, after)) if a != b]
    assert diff, "the edit changed nothing"
    assert min(diff) >= round(0.55 * 30) - 1 and max(diff) <= round(0.85 * 30)
