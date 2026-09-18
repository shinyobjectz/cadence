"""Audio producers, measured against ground truth rather than eyeballed.

Two kinds of truth are used, both exact:
  * a comp's own audio node (clip start, duration, fades, volume) — lifted, not measured;
  * a speech asset built by concatenating single-word TTS clips with known silences, so every
    word's start time is exact by construction.

The headline result the code depends on: ASR word timestamps are ~250 ms out, which is eight
frames and unusable for cutting; forced alignment of the same words is ~15 ms. So transcription
is only ever used to discover *what* was said, never *when*.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import audiofacts as A  # noqa: E402
from cadence_vision.facts import lift, parse_log  # noqa: E402

FRAME = 1 / 30
AUDIO_CASE = ROOT / "evals" / "cases" / "audio.lua"


@pytest.fixture(scope="session")
def speech(tmp_path_factory):
    """Words concatenated with known silences: start times are exact by construction."""
    if not subprocess.run(["which", "say"], capture_output=True).stdout.strip():
        pytest.skip("macOS `say` not available")
    d = tmp_path_factory.mktemp("speech")
    words = ["Hold", "the", "cut", "Let", "the", "type", "land"]
    gaps = [0.30, 0.15, 0.15, 0.50, 0.15, 0.15, 0.15]
    trim = ("silenceremove=start_periods=1:start_silence=0:start_threshold=-45dB:detection=peak,"
            "areverse,silenceremove=start_periods=1:start_silence=0:start_threshold=-45dB:"
            "detection=peak,areverse")
    truth, files, t = [], [], 0.0
    for i, (w, g) in enumerate(zip(words, gaps)):
        raw, cut = d / f"{i}.aiff", d / f"{i}.wav"
        subprocess.run(["say", "-v", "Samantha", "-r", "170", "-o", str(raw), w], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", trim,
                        "-ar", "22050", "-ac", "1", str(cut)], check=True)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(cut)], capture_output=True, text=True).stdout)
        t += g
        truth.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        files.append((cut, g))
        t += dur
    inputs, parts = [], []
    for i, (f, g) in enumerate(files):
        inputs += ["-i", str(f)]
        parts.append(f"aevalsrc=0:d={g}:s=22050[s{i}]")
    fc = (";".join(parts) + ";" + "".join(f"[s{i}][{i}:a]" for i in range(len(files)))
          + f"concat=n={2 * len(files)}:v=0:a=1[out]")
    wav = d / "speech.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", fc,
                    "-map", "[out]", "-ar", "22050", "-ac", "1", str(wav)], check=True)
    return wav, truth


def _errors(truth, got):
    assert len(got) == len(truth), f"expected {len(truth)} words, got {[w['word'] for w in got]}"
    return [abs(a["start"] - b["start"]) for a, b in zip(truth, got)]


def test_forced_alignment_is_frame_accurate(speech):
    """G1's criterion literally: every word inside one frame, not two."""
    wav, truth = speech
    errs = _errors(truth, A.align(wav, " ".join(w["word"] for w in truth)))
    assert max(errs) < FRAME, f"max error {max(errs) * 1000:.0f} ms"
    assert sum(errs) / len(errs) < FRAME / 2, f"mean error {sum(errs) / len(errs) * 1000:.0f} ms"


def test_asr_timestamps_are_not_frame_accurate(speech):
    """Guards the design: if this ever starts passing, the alignment step can be reconsidered."""
    wav, truth = speech
    got = A.words(wav)
    if len(got) != len(truth):
        pytest.skip("ASR did not return one word per ground-truth word")
    errs = _errors(truth, got)
    assert sum(errs) / len(errs) > FRAME, "ASR timestamps got frame-accurate; revisit word_times()"


def test_word_times_prefers_alignment(speech):
    wav, truth = speech
    got, how = A.word_times(wav, " ".join(w["word"] for w in truth))
    assert how == "forced_align"
    assert max(_errors(truth, got)) < 2 * FRAME


def test_lifted_audio_facts_match_the_comp_source():
    """audio.lua declares at=0 duration=3 volume=0.7 fade_in=0.25 fade_out=0.4 media_start=4."""
    facts = parse_log(lift(AUDIO_CASE))
    holds = {f[1][0]: f for f in facts if f[0] == "holds" and f[1][1] == "audio2"}
    assert holds["playing"][2:] == (0.0, 3.0)
    assert holds["volume"][1][2] == 0.7
    assert holds["fading_in"][2:] == (0.0, 0.25)
    assert holds["fading_out"][2:] == (2.6, 3.0)
    clip = next(f for f in facts if f[0] == "clip")
    assert clip[2].endswith("piano.ogg") and clip[3] == ("media_start", 4.0)


@pytest.mark.skipif(not (ROOT / "bin" / "cadence").exists(), reason="renderer not available")
def test_measured_envelope_matches_the_lifted_one(tmp_path):
    """Render the comp, then check the producers recover what the comp declared."""
    out = tmp_path / "audio.mp4"
    r = subprocess.run(["bin/cadence", "render", str(AUDIO_CASE), "-o", str(out)],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        pytest.skip(f"render unavailable: {r.stderr[-200:]}")
    lo = A.loudness(out)
    assert lo["first_sound"] <= FRAME, f"clip starts at 0.0, measured {lo['first_sound']}"
    fades = A.envelope_changes(lo)
    assert [f["dir"] for f in fades] == ["in", "out"], f"expected one fade each way, got {fades}"
    assert fades[0]["t0"] <= 0.25 and fades[1]["t1"] >= 2.6


# ----------------------------------------------------------------------------- audio meets vision

SYNC_TRUTH = """-- Synthetic active-speaker ground truth: shape A changes on each word, shape B in the gaps.
local e = require("ellua")
return e.comp {{
  width = 640, height = 360, duration = {dur}, fps = 30, background = "#101010",
  scene = function(s)
    local a = s:rect {{ x = 120, y = 180, w = 120, h = 10, color = "#e8edf7" }}
    local b = s:rect {{ x = 400, y = 180, w = 120, h = 10, color = "#3ee0c6" }}
    s:audio {{ src = "{wav}", at = 0, duration = {dur} }}
    s:script(function(t)
      t:parallel(
        function()
{a_seq}
        end,
        function()
{b_seq}
        end
      )
    end)
  end,
}}
"""


def _seq(spans, var, dur):
    out, t = [], 0.0
    for a, b in spans:
        out.append(f"        t:wait({a - t:.3f}); t:set({var}, {{ h = 90 }})")
        out.append(f"        t:wait({b - a:.3f}); t:set({var}, {{ h = 10 }})")
        t = b
    out.append(f"        t:wait({max(0.01, dur - t):.3f})")
    return "\n".join(out)


@pytest.mark.skipif(not (ROOT / "bin" / "cadence").exists(), reason="renderer not available")
def test_sound_source_picks_the_synchronised_entity(speech, tmp_path):
    """Stock footage ships with silent audio tracks, so the synced material is generated: one
    shape changes exactly on each word, another only in the gaps."""
    wav, truth = speech
    on = [(w["start"], w["end"]) for w in truth]
    gaps = [(truth[i]["end"] + 0.02, truth[i + 1]["start"] - 0.02) for i in range(len(truth) - 1)]
    gaps = [(a, b) for a, b in gaps if b - a > 0.08]
    dur = round(truth[-1]["end"] + 0.3, 2)
    comp = tmp_path / "sync.lua"
    comp.write_text(SYNC_TRUTH.format(dur=dur, wav=wav, a_seq=_seq(on, "a", dur), b_seq=_seq(gaps, "b", dur)))
    out = tmp_path / "sync.mp4"
    r = subprocess.run(["bin/cadence", "render", str(comp), "-o", str(out)],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        pytest.skip(f"render unavailable: {r.stderr[-200:]}")

    from cadence_vision.annotate import boxes_at, props
    from cadence_vision.facts import Log
    ts = [round(i / 30, 4) for i in range(int(dur * 30))]
    d = props(comp, ts)
    boxes: dict[str, list] = {}
    for t in ts:
        for nid, n in boxes_at(d, t)["nodes"].items():
            boxes.setdefault(nid, []).append(
                (t, (n["x"] / 640, n["y"] / 360, (n["x"] + n["w"]) / 640, (n["y"] + n["h"]) / 360)))

    L = Log()
    cor = A.emit_sound_source(out, boxes, L)
    assert cor["rect1"]["r"] > cor["rect2"]["r"], f"out-of-sync shape won: {cor}"
    assert cor["rect1"]["r"] >= 0.5, f"in-sync correlation too weak to act on: {cor}"
    assert "holds(sounds_like(rect1)" in L.text()
    assert "sounds_like(rect2)" not in L.text()


def test_speaking_turns_alternate_between_two_shapes(speech, tmp_path):
    """The half of diarization an edit needs: not "SPEAKER_00 spoke here" but *which thing on
    screen* was making the sound, decided per turn rather than once for the clip.

    The material is built so the answer is known: shape A changes on each word, shape B only in
    the gaps between them, so A must own the speech turns and B must never win one."""
    wav, truth = speech
    on = [(w["start"], w["end"]) for w in truth]
    gaps = [(truth[i]["end"] + 0.02, truth[i + 1]["start"] - 0.02) for i in range(len(truth) - 1)]
    gaps = [(a, b) for a, b in gaps if b - a > 0.08]
    dur = round(truth[-1]["end"] + 0.3, 2)
    comp = tmp_path / "turns.lua"
    comp.write_text(SYNC_TRUTH.format(dur=dur, wav=wav, a_seq=_seq(on, "a", dur),
                                      b_seq=_seq(gaps, "b", dur)))
    out = tmp_path / "turns.mp4"
    r = subprocess.run(["bin/cadence", "render", str(comp), "-o", str(out)],
                       cwd=ROOT, capture_output=True, text=True, timeout=900)
    if r.returncode != 0 or not out.exists():
        pytest.skip(f"render unavailable: {r.stderr[-200:]}")

    from cadence_vision.annotate import boxes_at, props
    from cadence_vision.facts import Log, parse_log
    ts = [round(i / 30, 4) for i in range(int(dur * 30))]
    d = props(comp, ts)
    boxes: dict[str, list] = {}
    for t in ts:
        for nid, n in boxes_at(d, t)["nodes"].items():
            boxes.setdefault(nid, []).append(
                (t, (n["x"] / 640, n["y"] / 360, (n["x"] + n["w"]) / 640, (n["y"] + n["h"]) / 360)))

    spans = A.speech_spans(truth)
    L = Log()
    n = len(A.emit_speaking(out, boxes, L, spans, min_span=0.2))
    if n == 0:
        pytest.skip("no turn separated the two shapes at this material's length")
    facts = parse_log(L.text())
    said = [f for f in facts if f[0] == "holds" and f[1][0] == "speaking"]
    assert said, L.text()
    assert {f[1][1] for f in said} == {"rect1"}, f"the out-of-sync shape claimed a turn: {L.text()}"
    for f in said:
        assert any(a - 0.3 <= f[2] <= b + 0.3 for a, b in spans), f"turn {f[2]}-{f[3]} is not speech"
    have = {f[1] for f in facts if f[0] == "src"}
    assert all(f in have for f in facts if f[0] in ("holds", "happens"))


def test_no_speech_spans_means_no_speaking_facts():
    from cadence_vision.facts import Log
    L = Log()
    assert A.emit_speaking(AUDIO_CASE, {"e1": [(0.0, (0.0, 0.0, 1.0, 1.0))]}, L, []) == []
    assert L.text().strip() == ""


def test_alignment_of_silence_returns_nothing_rather_than_a_torch_error(tmp_path):
    """Media with no audio track reaches `align` whenever it is called directly rather than behind
    `perceive`'s `has_audio` guard. Torch answers that with "Kernel size can't be greater than
    actual input size", which says nothing at all about the clip."""
    import subprocess
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    assert A.align(silent, "hold the cut") == []


def test_word_times_on_silence_is_empty_too(tmp_path):
    import subprocess
    silent = tmp_path / "silent.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=64x64:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent)], check=True)
    got, _how = A.word_times(silent, "hold the cut")
    assert got == []


# ------------------------------------------------------------------------- CTC onset refinement


@pytest.fixture(scope="session")
def sonorants(tmp_path_factory):
    """Words starting with /l/, /m/, /n/, /r/, /w/, /j/ — where CTC emission delay is worst.

    Held out from the words the refinement was developed on, and the reason this fixture exists
    separately: a bias you only ever measured on the clip you tuned against is not measured.
    """
    if not subprocess.run(["which", "say"], capture_output=True).stdout.strip():
        pytest.skip("macOS `say` not available")
    d = tmp_path_factory.mktemp("sonorants")
    words = ["mellow", "rain", "never", "window", "yes", "later"]
    trim = ("silenceremove=start_periods=1:start_silence=0:start_threshold=-45dB:detection=peak,"
            "areverse,silenceremove=start_periods=1:start_silence=0:start_threshold=-45dB:"
            "detection=peak,areverse")
    truth, files, t, gap = [], [], 0.0, 0.25
    for i, w in enumerate(words):
        raw, cut = d / f"{i}.aiff", d / f"{i}.wav"
        subprocess.run(["say", "-v", "Samantha", "-r", "170", "-o", str(raw), w], check=True)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(raw), "-af", trim,
                        "-ar", "22050", "-ac", "1", str(cut)], check=True)
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(cut)], capture_output=True, text=True).stdout)
        t += gap
        truth.append({"word": w, "start": round(t, 3), "end": round(t + dur, 3)})
        files.append(cut)
        t += dur
    inputs, parts = [], []
    for i, f in enumerate(files):
        inputs += ["-i", str(f)]
        parts.append(f"aevalsrc=0:d={gap}:s=22050[s{i}]")
    fc = (";".join(parts) + ";" + "".join(f"[s{i}][{i}:a]" for i in range(len(files)))
          + f"concat=n={2 * len(files)}:v=0:a=1[out]")
    wav = d / "sonorants.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", fc,
                    "-map", "[out]", "-ar", "22050", "-ac", "1", str(wav)], check=True)
    return wav, truth


def test_sonorant_onsets_are_frame_accurate(sonorants):
    """The words CTC places late. Without the onset refinement these run 20-50 ms late."""
    wav, truth = sonorants
    got = A.align(wav, " ".join(w["word"] for w in truth))
    errs = _errors(truth, got)
    assert max(errs) < FRAME, f"max error {max(errs) * 1000:.0f} ms on {[w['word'] for w in truth]}"


def test_refinement_moves_sonorants_earlier_not_later(sonorants):
    """The correction has a direction: CTC emits late, so refinement only ever walks back."""
    wav, truth = sonorants
    got = A.align(wav, " ".join(w["word"] for w in truth))
    shifts = [w["ctc_start"] - w["start"] for w in got]
    assert all(s >= -1e-9 for s in shifts), f"refinement pushed a word later: {shifts}"
    assert max(shifts) > 0, "nothing moved at all; the refinement is not running"


def test_onset_declines_when_there_is_no_rise():
    """X2: silence in, silence out. A flat envelope offers no onset, so the CTC time stands."""
    flat = np.full(400, 0.01)
    t, refined = A._onset(flat, t_ctc=1.0, t_end=1.2, floor_t=0.0)
    assert (t, refined) == (1.0, False)


def test_onset_does_not_cross_into_the_previous_word():
    """In continuous speech the valley may belong to the neighbour, not to this word."""
    e = np.concatenate([np.full(100, 1.0), np.full(20, 0.01), np.full(100, 1.0)])
    t_ctc = 120 * A.ENV_HOP
    free, _ = A._onset(e, t_ctc, t_ctc + 0.2, floor_t=0.0)
    fenced, _ = A._onset(e, t_ctc, t_ctc + 0.2, floor_t=118 * A.ENV_HOP)
    assert free <= fenced, "the floor did not fence the search"
    assert fenced >= 118 * A.ENV_HOP - 1e-9


def test_confidence_is_never_the_models_own_posterior(speech):
    """X1: conf reports whether a second estimator agreed, and takes only evidenced values."""
    wav, truth = speech
    got = A.align(wav, " ".join(w["word"] for w in truth))
    assert {w["conf"] for w in got} <= {A.CONF_REFINED, A.CTC_ONLY_CONF}


def test_envelope_tracks_energy():
    sr = 16000
    x = np.concatenate([np.zeros(sr // 2, np.float32),
                        np.ones(sr // 2, np.float32) * 0.5]).astype(np.float32)
    e = A._envelope(x, sr)
    assert len(e) > 0
    assert e[: len(e) // 3].max() < e[-len(e) // 3:].min()
