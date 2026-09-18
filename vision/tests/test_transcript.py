"""Transcripts and audio events in the grammar: editing by what was said, or by the beat.

The comp side is the part that was missing. A perceived log has carried word/beat facts since
the audio producers landed; a lifted log carried only says(), the whole sentence, which is too
coarse to anchor an edit to. These tests pin the three things that make word lifting safe:
the times are absolute, the lines carry src() because a word timing is *measured* even inside
an otherwise exact log, and a failure produces silence rather than a plausible wrong time.

The aligner is stubbed for the deterministic cases -- the repo ships no speech asset, and these
are testing the lifting, not the acoustics (audiofacts has its own measured tests).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import audiofacts as AF
from cadence_vision import facts as FA
from cadence_vision.query import Log

SAID = "one two three"
WORDS = [{"word": "one", "start": 0.0, "end": 0.3, "conf": 0.95},
         {"word": "two", "start": 0.5, "end": 0.8, "conf": 0.95},
         {"word": "three.", "start": 1.0, "end": 1.4, "conf": 0.6}]


def comp_at(tmp_path: Path, at: float, text: str | None = SAID, src: str = "evals/assets/piano.ogg") -> Path:
    body = f'src = "{src}", at = {at}, volume = 1.0'
    if text is not None:
        body += f', text = "{text}"'
    p = tmp_path / "t.lua"
    p.write_text(
        'local e = require("ellua")\n'
        "return e.comp {\n"
        '  width = 320, height = 180, duration = 4, fps = 10, background = "#0c1018",\n'
        "  scene = function(s)\n"
        f"    s:audio {{ {body} }}\n"
        '    s:text { x = 10, y = 10, text = "x", size = 20, color = "#e8edf7" }\n'
        "  end,\n}\n")
    return p


@pytest.fixture
def aligned(monkeypatch):
    monkeypatch.setattr(AF, "align", lambda media, text: list(WORDS))


def test_words_are_emitted_with_provenance(tmp_path, aligned):
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0), want_words=True))
    hits = lg.match("happens(word(_, W), T)")
    assert [h.bind["W"] for h in hits] == ["one", "two", "three."]
    assert all(h.producer == "forced_align" for h in hits), "a word timing is measured"
    assert hits[2].conf == 0.6, "per-word confidence must survive, not be flattened"


def test_word_times_are_absolute(tmp_path, aligned):
    """The aligner works in clip time; the log is in comp time. Forgetting the clip's
    `at` would put every anchor early by exactly the clip offset."""
    lg = Log.parse(FA.lift(comp_at(tmp_path, 1.5), want_words=True))
    assert [h.bind["T"] for h in lg.match("happens(word(_, W), T)")] == [1.5, 2.0, 2.5]


def test_words_are_off_by_default(tmp_path, aligned):
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0)))
    assert lg.match("happens(word(_, W), T)") == []
    assert lg.match("says(_, S)"), "the sentence is still exact and still lifted"


def test_header_stops_claiming_no_src_when_words_are_on(tmp_path, aligned):
    with_words = FA.lift(comp_at(tmp_path, 0.0), want_words=True)
    without = FA.lift(comp_at(tmp_path, 0.0))
    assert "no src lines" in without.splitlines()[0]
    assert "no src lines" not in with_words.splitlines()[0]
    assert "force-aligned" in with_words.splitlines()[0]


def test_no_text_means_no_words(tmp_path, aligned):
    """Without a declared transcript there is nothing to align against."""
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0, text=None), want_words=True))
    assert lg.match("happens(word(_, W), T)") == []


def test_missing_media_is_silence_not_a_guess(tmp_path, monkeypatch):
    called = []
    monkeypatch.setattr(AF, "align", lambda m, t: called.append(m) or list(WORDS))
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0, src="evals/assets/nope.ogg"), want_words=True))
    assert lg.match("happens(word(_, W), T)") == []
    assert called == [], "the aligner must not even be asked about a file that is not there"


def test_aligner_failure_is_silence_not_a_guess(tmp_path, monkeypatch):
    def boom(media, text):
        raise RuntimeError("no model")
    monkeypatch.setattr(AF, "align", boom)
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0), want_words=True))
    assert lg.match("happens(word(_, W), T)") == []


# ---------------------------------------------------------------- anchors

def test_when_ignores_trailing_punctuation(tmp_path, aligned):
    """An aligner emits "three." for a sentence-final word; an agent asks for "three"."""
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0), want_words=True))
    assert lg.when(word="three") == [1.0]
    assert lg.when(word="three.") == [1.0]
    assert lg.when(word="THREE") == [1.0]
    assert lg.when(word="thre") == []


def test_when_returns_every_occurrence(monkeypatch, tmp_path):
    monkeypatch.setattr(AF, "align", lambda m, t: [
        {"word": "go", "start": 0.2, "end": 0.4, "conf": 0.9},
        {"word": "go", "start": 1.2, "end": 1.4, "conf": 0.9}])
    lg = Log.parse(FA.lift(comp_at(tmp_path, 0.0, text="go go"), want_words=True))
    assert lg.when(word="go") == [0.2, 1.2]


def test_transcript_prefers_words_and_falls_back_to_says(tmp_path, aligned):
    with_words = Log.parse(FA.lift(comp_at(tmp_path, 0.0), want_words=True))
    assert with_words.transcript() == "one two three."
    without = Log.parse(FA.lift(comp_at(tmp_path, 0.0)))
    assert without.transcript() == SAID


def test_beats_anchor_to_music(tmp_path, monkeypatch):
    monkeypatch.setattr(AF, "beats", lambda m: {"tempo": 120.0, "beats": [0.0, 0.5, 1.0],
                                                "onsets": [0.02]})
    lg = Log.parse(FA.lift(comp_at(tmp_path, 1.0), want_beats=True))
    assert lg.when(event="beat") == [1.0, 1.5, 2.0], "beats are offset by the clip's at"
    assert lg.match("tempo(_, _)")[0].producer == "librosa"
    assert lg.when(event="onset") == [1.02]


def test_perceived_and_lifted_word_facts_have_the_same_shape():
    """One grammar, both directions -- the same query must work on either log."""
    perceived = Log.parse('happens(word(w1, "hello"), 1.000).\n'
                          'src(happens(word(w1, "hello"), 1.000), mms_fa, 0.95).\n')
    lifted = Log.parse('happens(word(audio1_w1, "hello"), 1.000).\n'
                       'src(happens(word(audio1_w1, "hello"), 1.000), forced_align, 0.95).\n')
    for lg in (perceived, lifted):
        assert lg.when(word="hello") == [1.0]
        assert lg.match("happens(word(_, W), T)")[0].bind["W"] == "hello"


# ---------------------------------------------------------------- clip windows

@pytest.fixture
def tone(tmp_path):
    """A 2s tone. The repo's only audio asset is 176s, which is longer than any test comp,
    so it can only ever exercise the clamp-to-comp branch, never the media-length one."""
    import subprocess
    p = tmp_path / "tone.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=2", str(p)], check=True)
    return p


def comp_with(tmp_path: Path, src: Path, at: float, comp_dur: float, media_start: float = 0.0) -> Path:
    p = tmp_path / "w.lua"
    ms = f", media_start = {media_start}" if media_start else ""
    p.write_text(
        'local e = require("ellua")\nreturn e.comp {\n'
        f'  width = 320, height = 180, duration = {comp_dur}, fps = 10, background = "#0c1018",\n'
        "  scene = function(s)\n"
        f'    s:audio {{ src = "{src}", at = {at}{ms} }}\n'
        '    s:text { x = 10, y = 10, text = "x", size = 20, color = "#e8edf7" }\n'
        "  end,\n}\n")
    return p


def test_clip_without_declared_duration_uses_the_media_length(tmp_path, tone):
    """props.lua evaluates the comp *without* the resolve phase, so an audio node that did not
    declare `duration` has none -- resolve is what fills it in from the media. Falling back to
    the comp's duration made every narration clip claim to play until the end of the comp."""
    lg = Log.parse(FA.lift(comp_with(tmp_path, tone, at=1.0, comp_dur=10)))
    h = lg.match("holds(playing(_), T0, T1)")[0].bind
    assert h["T0"] == 1.0
    assert abs(h["T1"] - 3.0) < 0.05, "1.0 + 2s of media, not the comp's 10s"


def test_media_start_shortens_the_window(tmp_path, tone):
    lg = Log.parse(FA.lift(comp_with(tmp_path, tone, at=0.0, comp_dur=10, media_start=0.5)))
    t1 = lg.match("holds(playing(_), T0, T1)")[0].bind["T1"]
    assert abs(t1 - 1.5) < 0.05, "media_start must come off the clip window"


def test_clip_longer_than_the_comp_is_still_clamped(tmp_path, tone):
    """The media length is a ceiling, not an override -- nothing may sound past the end."""
    lg = Log.parse(FA.lift(comp_with(tmp_path, tone, at=0.5, comp_dur=1.5)))
    assert lg.match("holds(playing(_), T0, T1)")[0].bind["T1"] == 1.5
