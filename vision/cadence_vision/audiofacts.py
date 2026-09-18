"""Audio producers for the fact log: words, speech spans, beats, loudness.

Editing is half an audio problem — a cut lands on a word or a beat, not on a timestamp — so these
emit the anchors the grammar is built around:

    happens(word(w3, "there"), 1.310).      holds(speech(a1), 1.180, 1.640).
    happens(beat(b12), 2.004).              holds(silent(a1), 0.000, 0.300).

Every fact carries provenance, because all of it is measured. A comp's own audio nodes are exact
and are lifted by facts.lift instead, which is what these producers are measured against.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import numpy as np
from .profile import timed

_WHISPER = {}


def _samples(media: Path, sr: int = 16000) -> np.ndarray:
    """Mono float32 at sr, straight from ffmpeg so any container works."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-f", "f32le",
                        "-ac", "1", "-ar", str(sr), "-"], capture_output=True, timeout=600)
    if r.returncode != 0 or not r.stdout:
        return np.zeros(0, np.float32)
    return np.frombuffer(r.stdout, np.float32)


def has_audio(media: Path) -> bool:
    r = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
                        "stream=codec_name", "-of", "csv=p=0", str(media)],
                       capture_output=True, text=True, timeout=120)
    return bool(r.stdout.strip())


@timed("whisper.transcribe")
def words(media: Path, model: str = "small.en", language: str | None = "en") -> list[dict]:
    """Word-level timestamps. faster-whisper on CPU; the model is cached per process."""
    from faster_whisper import WhisperModel
    if model not in _WHISPER:
        _WHISPER[model] = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _ = _WHISPER[model].transcribe(str(media), word_timestamps=True, language=language,
                                         vad_filter=False, beam_size=5)
    out = []
    for s in segs:
        for w in (s.words or []):
            out.append({"word": w.word.strip(), "start": round(float(w.start), 3),
                        "end": round(float(w.end), 3), "conf": round(float(w.probability), 3)})
    return out


def speech_spans(ws: list[dict], gap: float = 0.35) -> list[tuple[float, float]]:
    """Merge words into speaking intervals; a gap longer than `gap` ends one."""
    spans: list[list[float]] = []
    for w in ws:
        if spans and w["start"] - spans[-1][1] <= gap:
            spans[-1][1] = w["end"]
        else:
            spans.append([w["start"], w["end"]])
    return [(round(a, 3), round(b, 3)) for a, b in spans]


@timed("librosa.beats")
def beats(media: Path) -> dict:
    """Beat grid and onsets. Returns {tempo, beats[], onsets[]}; tempo is None when unpitched."""
    import librosa
    y = _samples(media, 22050)
    if y.size < 2048:
        return {"tempo": None, "beats": [], "onsets": []}
    tempo, bt = librosa.beat.beat_track(y=y, sr=22050, units="time")
    on = librosa.onset.onset_detect(y=y, sr=22050, units="time", backtrack=True)
    t = float(np.atleast_1d(tempo)[0])
    return {"tempo": round(t, 1) if t > 0 else None,
            "beats": [round(float(x), 3) for x in np.atleast_1d(bt)],
            "onsets": [round(float(x), 3) for x in np.atleast_1d(on)]}


@timed("loudness")
def loudness(media: Path, hop: float = 0.02, floor_db: float = -45.0) -> dict:
    """Short-term RMS in dBFS, plus the intervals below `floor_db` and the first/last sound."""
    sr = 16000
    y = _samples(media, sr)
    if y.size == 0:
        return {"times": [], "db": [], "silent": [], "first_sound": None, "last_sound": None}
    n = max(1, int(hop * sr))
    frames = y[: (y.size // n) * n].reshape(-1, n)
    rms = np.sqrt((frames.astype(np.float64) ** 2).mean(1) + 1e-12)
    db = 20 * np.log10(rms)
    times = np.arange(len(db)) * hop
    loud = db > floor_db
    silent, i = [], 0
    while i < len(loud):
        if not loud[i]:
            j = i
            while j < len(loud) and not loud[j]:
                j += 1
            silent.append((round(float(times[i]), 3), round(float(times[min(j, len(times) - 1)]), 3)))
            i = j
        else:
            i += 1
    idx = np.nonzero(loud)[0]
    return {"times": [round(float(t), 3) for t in times], "db": [round(float(v), 1) for v in db],
            "silent": silent,
            "first_sound": round(float(times[idx[0]]), 3) if idx.size else None,
            "last_sound": round(float(times[idx[-1]] + hop), 3) if idx.size else None}


def envelope_changes(lo: dict, window: float = 0.1, min_db: float = 6.0,
                     edges_only: bool = True, edge: float = 0.15) -> list[dict]:
    """Sustained level changes as {dir, t0, t1, db}.

    RMS cannot tell a mix fade from the material's own dynamics: a piano roll produces a dozen
    monotonic runs of 6-18 dB that are music, not fades. So by default only changes touching the
    first or last sound are reported, which is where a fade actually lives. Everything else is
    dynamics and gets no fact — being silent is better than being confidently wrong."""
    db = np.array(lo["db"])
    ts = np.array(lo["times"])
    if db.size < 8:
        return []
    k = max(1, int(window / max(ts[1] - ts[0], 1e-6)))
    sm = np.convolve(db, np.ones(k) / k, mode="same")
    d = np.diff(sm)
    out, i = [], 0
    while i < len(d):
        s = np.sign(d[i])
        if s == 0:
            i += 1
            continue
        j = i
        while j < len(d) and np.sign(d[j]) == s:
            j += 1
        if abs(sm[min(j, len(sm) - 1)] - sm[i]) >= min_db and (ts[min(j, len(ts) - 1)] - ts[i]) >= window:
            out.append({"dir": "in" if s > 0 else "out", "t0": round(float(ts[i]), 3),
                        "t1": round(float(ts[min(j, len(ts) - 1)]), 3),
                        "db": round(float(sm[min(j, len(sm) - 1)] - sm[i]), 1)})
        i = j
    if edges_only:
        f, l = lo.get("first_sound"), lo.get("last_sound")
        out = [e for e in out
               if (e["dir"] == "in" and f is not None and abs(e["t0"] - f) <= edge)
               or (e["dir"] == "out" and l is not None and abs(e["t1"] - l) <= edge)]
    return out


def emit(media: Path, L, entity: str = "a1", want_words: bool = True,
         text: str | None = None) -> list[dict]:
    """Append audio facts for `media` to a facts.Log, and return the words it found.

    The words come back because transcription and forced alignment are the expensive part, and the
    caller needs the same speech spans to work out who was speaking."""
    from .facts import T
    if not has_audio(media):
        L.fact("audio(none)", "ffprobe", 0.99)
        return []
    lo = loudness(media)
    if lo["first_sound"] is None:
        L.fact("audio(silent)", "rms", 0.95)
        return []
    L.fact("audio(present)", "ffprobe", 0.99)
    L.fact(f'entity({entity}, "audio", stream(0))', "ffprobe", 0.99)
    L.fact(f"happens(first_sound({entity}), {T(lo['first_sound'])})", "rms", 0.9)
    L.fact(f"happens(last_sound({entity}), {T(lo['last_sound'])})", "rms", 0.9)
    for a, b in lo["silent"]:
        if b - a >= 0.15:
            L.fact(f"holds(silent({entity}), {T(a)}, {T(b)})", "rms", 0.85)
    for e in envelope_changes(lo):
        L.fact(f"holds(fading_{e['dir']}({entity}), {T(e['t0'])}, {T(e['t1'])})", "rms_envelope", 0.7)
    b = beats(media)
    if b["tempo"]:
        L.fact(f"tempo({entity}, {b['tempo']})", "librosa", 0.6)
        for i, t in enumerate(b["beats"]):
            L.fact(f"happens(beat(b{i + 1}), {T(t)})", "librosa", 0.6)
    if not want_words:
        return []
    ws, how = word_times(media, text)
    for i, w in enumerate(ws):
        safe = w["word"].replace('"', "'")
        L.fact(f'happens(word(w{i + 1}, "{safe}"), {T(w["start"])})', how, w["conf"])
    for a, bb in speech_spans(ws):
        # `speech(a1)` is "the audio has speech here"; `speaking(e)` is "this entity is the one
        # talking". Two different claims, so two names — one predicate meaning both, told apart
        # only by whether its argument happened to be an audio stream, is a trap for any reader.
        L.fact(f"holds(speech({entity}), {T(a)}, {T(bb)})", how, 0.8)
    return ws


# ----------------------------------------------------------------------------- forced alignment

MIN_ALIGN_SAMPLES = 400     # the feature extractor's first kernel needs more than this
_ALIGNER = {}



# ---------------------------------------------------------------- onset refinement for word times
#
# CTC acoustic models emit late. The peak for a word-initial sonorant (/l/, /m/, /n/, /r/, /w/, /j/)
# lands near the following vowel rather than at the consonant, so forced alignment puts such a word
# tens of milliseconds after it starts. Measured on two constructed comps: MMS_FA is out by up to
# 50 ms, and it is not a model-quality problem — wav2vec2 base/large, wav2vec2-lv60k and HuBERT all
# put the same word within 0-5 ms of each other, and the largest model was the worst. Every one of
# them is CTC.
#
# The fix is a second, independent estimator: an energy onset. Walk back from the CTC boundary to
# where the energy rises out of the preceding valley. Stop/fricative onsets barely move; sonorants
# move by 20-50 ms, which is exactly the bias. Worst word error over both comps falls from 50.0 ms
# to 16.0 ms, i.e. from 15/17 words inside a video frame to 17/17.

ENV_HOP, ENV_WIN = 0.005, 0.010
ONSET_BACK = 0.080       # how far before the CTC boundary an onset may be
ONSET_RELS = (0.10, 0.15, 0.30)   # the rise thresholds the onset has to agree across
ONSET_MIN_RISE = 1.5     # peak/floor below this is not a rise, so there is nothing to snap to
ONSET_MAX_SPREAD = 1 / 30  # thresholds disagreeing by over a frame means the onset is ambiguous
# Two confidences, because two regimes are all the measurements support. Where an onset is found
# and is unambiguous, 17 of 17 words over two comps landed inside a frame; Laplace-smoothing that
# gives 0.95. Where none is found, the time is the raw CTC boundary, measured out by up to 50 ms.
# Grading more finely than this was tried and dropped: the threshold spread turned out to be
# *anti*-correlated with error (mean 2.4 ms for the low-spread words against 7.8 ms for the rest),
# so a graded score would have been confident in the wrong places.
CONF_REFINED = 0.95
CTC_ONLY_CONF = 0.4


def _envelope(x: np.ndarray, sr: int, hop: float = ENV_HOP, win: float = ENV_WIN) -> np.ndarray:
    """Short-time RMS. Plain energy, deliberately: it shares no model with the aligner."""
    h, w = int(sr * hop), int(sr * win)
    if h <= 0 or len(x) < w:
        return np.zeros(0, np.float32)
    n = (len(x) - w) // h + 1
    f = np.lib.stride_tricks.sliding_window_view(x, w)[::h][:n]
    return np.sqrt((f.astype(np.float64) ** 2).mean(axis=1) + 1e-12)


def _onset(e: np.ndarray, t_ctc: float, t_end: float, floor_t: float,
           hop: float = ENV_HOP) -> tuple[float, bool]:
    """Snap a CTC word boundary back to the energy rise before it.

    Returns (time, refined). `refined` is False when there is no onset to snap to, or when varying
    the rise threshold over ONSET_RELS moves the crossing by more than a frame — an onset that
    ambiguous is not evidence. The CTC time is then returned unchanged, and the caller must not
    treat it as frame-accurate.
    """
    if len(e) == 0:
        return t_ctc, False
    i_c = int(t_ctc / hop)
    i_e = min(int(t_end / hop), len(e) - 1)
    if i_c <= 0 or i_c >= len(e):
        return t_ctc, False
    # never walk back past the end of the previous word: in continuous speech the valley we want
    # may not exist at all, and the neighbour's energy is not this word's onset
    lo = max(0, i_c - int(ONSET_BACK / hop), int(floor_t / hop))
    if lo >= i_c:
        return t_ctc, False
    peak = float(e[i_c:i_e + 1].max()) if i_e >= i_c else float(e[i_c])
    floor = float(e[lo:i_c + 1].min())
    if peak <= floor * ONSET_MIN_RISE:
        return t_ctc, False
    hits = []
    for rel in ONSET_RELS:
        thr = floor + rel * (peak - floor)
        j = i_c
        while j > lo and e[j] > thr:
            j -= 1
        hits.append(j * hop)
    if max(hits) - min(hits) > ONSET_MAX_SPREAD:
        return t_ctc, False
    return float(np.median(hits)), True

@timed("mms_fa.align")
def align(media: Path, text: str) -> list[dict]:
    """Word times for *known* text, by forced alignment (torchaudio MMS_FA).

    ASR word timestamps come from attention alignment and are only good to a couple of hundred
    milliseconds — too coarse to cut on. When the words are already known, which is the usual case
    for a comp (its TTS carries the script) and for footage with a transcript, alignment against
    the audio is an order of magnitude tighter."""
    import torch
    from torchaudio.pipelines import MMS_FA as BUNDLE
    if "m" not in _ALIGNER:
        _ALIGNER["m"] = (BUNDLE.get_model(), BUNDLE.get_tokenizer(), BUNDLE.get_aligner())
    model, tokenizer, aligner = _ALIGNER["m"]
    x = _samples(media, BUNDLE.sample_rate)
    # Too little audio to convolve over comes out of torch as "Kernel size can't be greater than
    # actual input size", which says nothing about the clip. Media with no audio track reaches here
    # whenever `align` is called directly rather than behind `perceive`'s `has_audio` guard.
    if len(x) < MIN_ALIGN_SAMPLES:
        return []
    wav = torch.from_numpy(x.copy()).unsqueeze(0)
    words_ = [w for w in (t.strip() for t in text.split()) if w]
    norm = ["".join(ch for ch in w.lower() if ch.isalpha() or ch == "'") or w.lower() for w in words_]
    with torch.inference_mode():
        emission, _ = model(wav)
        spans = aligner(emission[0], tokenizer(norm))
    ratio = wav.shape[1] / emission.shape[1] / BUNDLE.sample_rate
    env = _envelope(x, BUNDLE.sample_rate)
    out: list[dict] = []
    prev_end = 0.0
    for w, sp in zip(words_, spans):
        t0, t1 = sp[0].start * ratio, sp[-1].end * ratio
        start, refined = _onset(env, t0, t1, prev_end)
        # X1: the confidence comes from whether a second, independent estimator could place this
        # word, never from the model's own posterior. The CTC score says nothing about *where* a
        # word is — it was above 0.9 on the words that were 50 ms late — so it is not used at all.
        conf = CONF_REFINED if refined else CTC_ONLY_CONF
        out.append({"word": w, "start": round(start, 3), "end": round(t1, 3),
                    "conf": round(float(conf), 3), "ctc_start": round(t0, 3)})
        prev_end = t1
    return out


def word_times(media: Path, text: str | None = None) -> tuple[list[dict], str]:
    """Word times, as accurately as the situation allows. Returns (words, producer name).

    Measured against a constructed ground truth (words concatenated with known silences):
    ASR timestamps alone are ~250 ms out, forced alignment ~15 ms. So transcription is used only
    to *discover* the words; the times always come from alignment. A comp that carries its own
    script should pass it as `text` and skip transcription entirely.
    """
    if text is None:
        ws = words(media)
        if not ws:
            return [], "faster_whisper"
        text = " ".join(w["word"] for w in ws)
        try:
            return align(media, text), "whisper_then_align"
        except Exception:                      # alignment is the better path, ASR is the fallback
            return ws, "faster_whisper"
    return align(media, text), "forced_align"


if __name__ == "__main__":
    from .facts import Log
    for p in sys.argv[1:]:
        L = Log()
        L.c(f"audio facts for {Path(p).name}")
        emit(Path(p), L)
        print(L.text(), end="")


# ----------------------------------------------------------------------------- audio meets vision

def visual_energy(media: Path, boxes: dict[str, list[tuple]], fps: float = 30.0) -> dict:
    """Per-entity frame-to-frame change inside its box, sampled at `fps`.

    `boxes` maps an entity id to [(t, (x0, y0, x1, y1) normalised), ...]. The signal is mean
    absolute luma difference, which is what a moving mouth, a struck drum or a slamming door all
    produce — so this is not face-specific."""
    import cv2
    cap = cv2.VideoCapture(str(media))
    src_fps = cap.get(cv2.CAP_PROP_FPS) or fps
    n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(src_fps / fps))
    prev, times, sig = None, [], {k: [] for k in boxes}
    for i in range(0, n, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, i)
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY).astype(np.float32)
        t = i / src_fps
        if prev is not None:
            d = np.abs(g - prev)
            H, W = g.shape
            times.append(t)
            for k, obs in boxes.items():
                near = min(obs, key=lambda o: abs(o[0] - t), default=None)
                if near is None or abs(near[0] - t) > 2 / fps:
                    sig[k].append(0.0)
                    continue
                x0, y0, x1, y1 = near[1]
                a, b = int(max(0, y0) * H), int(min(1, y1) * H)
                c, e = int(max(0, x0) * W), int(min(1, x1) * W)
                sig[k].append(float(d[a:b, c:e].mean()) if b > a and e > c else 0.0)
        prev = g
    cap.release()
    return {"times": times, "energy": sig}


def _norm(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, float)
    s = x.std()
    return (x - x.mean()) / s if s > 1e-9 else np.zeros_like(x)


def _signals(media: Path, boxes: dict[str, list[tuple]], fps: float, smooth: float):
    """The smoothed per-entity visual energy and the audio envelope on one shared time base.

    Decoding is the expensive part, so it happens once here and every window is correlated against
    these arrays rather than re-reading the clip per speech turn."""
    ve = visual_energy(media, boxes, fps)
    if not ve["times"]:
        return None
    lo = loudness(media, hop=1.0 / fps)
    db, ts = np.array(lo["db"]), np.array(lo["times"])
    if db.size < 4:
        return None
    times = np.array(ve["times"], float)
    amp = np.interp(times, ts, np.clip(db, -60, None))
    # Frame difference is an impulse at each change while a spoken word is a plateau, so the visual
    # signal is smoothed to make the two comparable. Without it the right entity wins by a hair:
    # the margin between an in-sync and an out-of-sync shape went from 0.054 to 0.356 at 0.25 s.
    k = max(1, int(smooth * fps))
    box = np.ones(k) / k
    vis = {e: np.convolve(np.array(v, float), box, mode="same") for e, v in ve["energy"].items()}
    return times, amp, vis


def _corr(times, amp, vis, fps: float, max_lag: float, window=None) -> dict:
    """{entity: {r, lag}} over `window` (or the whole clip), with the lag searched.

    A recording is rarely sample-aligned, and a fixed-lag correlation scores merely-offset material
    as uncorrelated."""
    m = np.ones(len(times), bool) if window is None else (times >= window[0]) & (times <= window[1])
    if m.sum() < 8:
        return {}
    a = _norm(amp[m])
    lags = range(-int(max_lag * fps), int(max_lag * fps) + 1)
    out = {}
    for k, sig in vis.items():
        v = _norm(sig[m])
        if v.size < 4 or np.allclose(v, 0):
            out[k] = {"r": 0.0, "lag": 0.0}
            continue
        best = max(((float(np.corrcoef(v[max(0, l):v.size + min(0, l)],
                                       a[max(0, -l):a.size + min(0, -l)])[0, 1]), l)
                    for l in lags if abs(l) < v.size - 3), default=(0.0, 0))
        out[k] = {"r": round(0.0 if np.isnan(best[0]) else best[0], 3), "lag": round(best[1] / fps, 3)}
    return out


def av_correlation(media: Path, boxes: dict[str, list[tuple]], fps: float = 30.0,
                   max_lag: float = 0.2, smooth: float = 0.25) -> dict:
    """Correlation of each entity's visual energy with the audio envelope, over the whole clip.

    Returns {entity: {"r": best correlation, "lag": seconds the picture leads the sound}}."""
    sig = _signals(media, boxes, fps, smooth)
    return _corr(*sig, fps, max_lag) if sig else {}


def _winner(cor: dict, min_r: float, min_margin: float):
    """The entity the sound belongs to, or None when the evidence does not separate two of them."""
    if not cor:
        return None
    ranked = sorted(cor.items(), key=lambda kv: -kv[1]["r"])
    runner = ranked[1][1]["r"] if len(ranked) > 1 else -1.0
    best, v = ranked[0]
    return (best, v["r"]) if v["r"] >= min_r and v["r"] - runner >= min_margin else None


def emit_speaking(media: Path, boxes: dict[str, list[tuple]], L, spans: list[tuple[float, float]],
                  fps: float = 30.0, max_lag: float = 0.2, smooth: float = 0.25,
                  min_r: float = 0.3, min_margin: float = 0.1, min_span: float = 0.4) -> int:
    """`speaking(e)` per speech turn, and `speaker_change` where the turn passes to someone else.

    Returns the turns it attributed, `[(t0, t1, entity, r)]`, so `diarize` can name its clusters
    after entities this already identified instead of leaving every voice anonymous.

    This is the half of diarization an edit actually needs. A diarizer labels turns `SPEAKER_00`
    and `SPEAKER_01`, which still has to be tied to something on screen before anyone can cut on
    it; correlating each turn's audio against each entity's own motion names the speaker as an
    entity the rest of the log already talks about. It also needs no gated model.

    Each turn is decided on its own evidence, so a two-hander comes out as alternating intervals
    rather than one winner for the clip. A turn where two entities correlate alike is left out."""
    from .facts import T
    sig = _signals(media, boxes, fps, smooth)
    if not sig:
        return []
    turns = []
    for a, b in spans:
        if b - a < min_span:
            continue
        got = _winner(_corr(*sig, fps, max_lag, window=(a, b)), min_r, min_margin)
        if got:
            turns.append((a, b, got[0], got[1]))
    if not turns:
        return []
    L.c("producer: per-turn audio-visual correlation — who is speaking, bound to a tracked entity")
    for a, b, who, r in turns:
        L.fact(f"holds(speaking({who}), {T(a)}, {T(b)})", "av_corr", round(min(0.95, r), 2))
    for (_a0, _b0, prev, _r0), (a1, _b1, nxt, r1) in zip(turns, turns[1:]):
        if prev != nxt:
            L.fact(f"happens(speaker_change({prev}, {nxt}), {T(a1)})", "av_corr",
                   round(min(0.9, r1), 2))
    return turns


def emit_sound_source(media: Path, boxes: dict[str, list[tuple]], L, min_r: float = 0.3,
                      min_margin: float = 0.1) -> dict:
    """`sounds_like(e)` for the entity whose motion tracks the audio — the active speaker, when the
    entities are faces.

    Only the best entity is claimed, and only when it beats both the absolute floor `min_r` and the
    runner-up by `min_margin`. Two faces in a scene both correlate with speech to some degree; a
    near-tie is a coin flip, and a coin flip does not belong in the log."""
    from .facts import T
    cor = av_correlation(media, boxes)
    if not cor:
        return {}
    for k, v in sorted(cor.items(), key=lambda kv: -kv[1]["r"]):
        L.fact(f"av_correlation({k}, {v['r']:.2f})", "av_corr", 0.6)
    got = _winner(cor, min_r, min_margin)
    if got:
        dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", str(media)], capture_output=True,
                                   text=True).stdout or 0)
        L.fact(f"holds(sounds_like({got[0]}), {T(0.0)}, {T(dur)})", "av_corr", min(0.95, got[1]))
    return cor
