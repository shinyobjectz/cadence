"""G1. Speaker turns, including the speaker nobody can see.

`audiofacts.emit_speaking` already answers the question an edit asks most often — *which entity on
screen is talking* — by correlating each turn's audio against each tracked entity's own motion. It
is the better producer when it fires, because it returns a name the rest of the log already uses.
It cannot fire when the speaker is off frame, turned away, or holding still, and those are common:
a voice-over, a reply from outside the frame, the second half of a two-hander after the cut.

This fills that gap. Speech is cut into overlapping windows, each window is embedded with ECAPA,
the embeddings are clustered, and contiguous windows sharing a cluster become a turn:

    holds(speech_turn(e2), 0.500, 2.300).
    src(holds(speech_turn(e2), 0.500, 2.300), ecapa_cluster, 0.82).
    happens(speaker_change(e2, spk2), 2.300).

A cluster is *named* wherever it can be. If most of a cluster's speech lands inside intervals that
`emit_speaking` already attributed to an entity, the cluster is that entity, and the log says so in
the vocabulary it already has — which is what makes "the voice in the second half is the man from
shot one, now off screen" a statement an editor can act on. A cluster that matches no entity keeps
an anonymous `spk<n>`, which is still the useful half: it says the voice changed and where.

ECAPA rather than pyannote, and the reason is not preference. `pyannote/speaker-diarization-3.1`
and its segmentation model are gated on Hugging Face; both return 403 here until someone accepts
their conditions in a browser, which is not something this can do on its own. `speechbrain/
spkrec-ecapa-voxceleb` is ungated and is the same family of embedding. What is lost is pyannote's
overlap-aware segmentation: two people talking at once come out as one turn, usually labelled with
whoever dominates. That is a real gap and it is listed as one in docs/FACTS.md.

Both numbers above are measured rather than chosen, and the measurement is in docs/FACTS.md:
ECAPA puts two utterances of one real speaker 0.176 apart and two different speakers 0.89-1.13
apart, but that margin closes as the window shortens, because a short window embeds as much room
as voice. At 1.5 s one speaker's own windows reach 0.795 while two speakers start at 0.888 — a
margin of 0.09, which is no margin. At 2.0 s it is 0.652 against 0.943. The window is 2.0 s for
that reason and not for a rounder one, and the threshold sits in the middle of the gap it opens.

**Confidence is separation, measured against this clip's own scatter.** A clustering algorithm has
no score worth repeating — it returns labels whatever the data looks like, and the same threshold
that cleanly splits two distinct voices will happily split one person's voice into "two speakers"
across a change of loudness. So each turn is scored by how much closer its windows sit to their own
cluster than to the nearest other one, ranked against the spread *within* clusters on this clip. A
turn that stands apart no more than two windows of one speaker normally do scores near zero and is
dropped. When no split survives that test the clip has one speaker, and one speaker needs no
diarization: the producer emits nothing rather than a turn structure it cannot support.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
from .profile import timed

WIN = 2.0            # seconds of speech per embedding
HOP = 1.0
MIN_WIN = 1.0        # a window shorter than this is too little voice to embed
MIN_TURN = 0.5
THRESH = 0.80        # cosine distance at which two windows stop being the same voice
MIN_CONF = 0.35
MIN_WINDOWS = 4      # fewer than this and there is nothing to cluster against
SR = 16000

_MODEL = {}


def available() -> bool:
    try:
        import speechbrain.inference  # noqa: F401
    except ImportError:
        return False
    return True


def windows(spans, win: float = WIN, hop: float = HOP,
            min_win: float = MIN_WIN) -> list[tuple[float, float]]:
    """Overlapping windows lying wholly inside speech.

    They do not cross a silence, because an embedding averaged over two voices with a pause between
    them belongs to neither. A span too short to fill one window still yields one, shortened, as
    long as it holds `min_win` of voice — short replies are exactly where a turn changes."""
    out = []
    for a, b in spans:
        if b - a < min_win:
            continue
        if b - a <= win:
            out.append((round(a, 3), round(b, 3)))
            continue
        t = a
        while t + win <= b + 1e-9:
            out.append((round(t, 3), round(t + win, 3)))
            t += hop
        if out and b - out[-1][1] > 1e-6 and b - win >= a - 1e-6:
            # Any speech the stride leaves over gets one last, overlapping window. Requiring a
            # window's worth of leftover instead would put a blind spot at the end of every span,
            # and the end of a span is where a turn changes.
            out.append((round(b - win, 3), round(b, 3)))
    return out


def _model():
    if "m" not in _MODEL:
        from speechbrain.inference import EncoderClassifier
        from .tracking import MODELS
        # MODELS, not tracking.weights(): that returns the bare name for a file it has not seen
        # before, which is right for a checkpoint ultralytics will download and wrong for a
        # directory speechbrain is about to create — it would land in the working directory.
        savedir = MODELS / "spkrec-ecapa-voxceleb"
        savedir.mkdir(parents=True, exist_ok=True)
        _MODEL["m"] = EncoderClassifier.from_hparams(
            source="speechbrain/spkrec-ecapa-voxceleb",
            savedir=str(savedir), run_opts={"device": "cpu"})
    return _MODEL["m"]


@timed("ecapa.embed")
def embed(media: Path, wins, sr: int = SR) -> np.ndarray:
    """L2-normalised ECAPA embeddings, one per window."""
    import torch

    from .audiofacts import _samples
    x = _samples(Path(media), sr)
    if len(x) == 0 or not wins:
        return np.zeros((0, 192), np.float32)
    m = _model()
    out = []
    for a, b in wins:
        seg = x[int(a * sr):int(b * sr)]
        if len(seg) < int(MIN_WIN * sr):
            seg = np.pad(seg, (0, int(MIN_WIN * sr) - len(seg)))
        with torch.no_grad():
            v = m.encode_batch(torch.from_numpy(seg.copy()).unsqueeze(0)).squeeze().numpy()
        out.append(v.astype(np.float32))
    V = np.stack(out)
    return V / np.maximum(np.linalg.norm(V, axis=1, keepdims=True), 1e-9)


def cluster(V: np.ndarray, thresh: float = THRESH) -> np.ndarray:
    """Agglomerative clustering on cosine distance; the speaker count is not known in advance."""
    from sklearn.cluster import AgglomerativeClustering
    if len(V) < 2:
        return np.zeros(len(V), int)
    lab = AgglomerativeClustering(n_clusters=None, distance_threshold=thresh,
                                  metric="cosine", linkage="average").fit_predict(V)
    return np.asarray(lab, int)


def separation(V: np.ndarray, labels: np.ndarray) -> np.ndarray:
    """Per window: how far it sits from the nearest other cluster relative to its own, ranked
    against the distances *inside* clusters on this clip.

    X1. The raw margin is not comparable between clips — a phone recording and a studio mix have
    different scatter — so what is reported is the share of within-cluster pair distances the
    window's own separation beats. One cluster means no separation to measure and scores zero."""
    n = len(V)
    if n == 0:
        return np.zeros(0)
    uniq = np.unique(labels)
    if len(uniq) < 2:
        return np.zeros(n)
    D = 1.0 - V @ V.T
    np.fill_diagonal(D, np.nan)
    inside = np.array([D[i, j] for i in range(n) for j in range(i + 1, n)
                       if labels[i] == labels[j]])
    own = np.array([np.nanmean(D[i][labels == labels[i]]) if (labels == labels[i]).sum() > 1
                    else 0.0 for i in range(n)])
    other = np.array([min(np.nanmean(D[i][labels == u]) for u in uniq if u != labels[i])
                      for i in range(n)])
    gap = other - own
    if len(inside) < 3:
        return np.clip(gap / max(float(np.max(gap)), 1e-9), 0.0, 1.0)
    return np.array([float((inside < g).mean()) for g in gap])


def turns(wins, labels: np.ndarray, sep: np.ndarray,
          min_turn: float = MIN_TURN) -> list[tuple[float, float, int, float]]:
    """Contiguous windows of one cluster become one turn, [(t0, t1, label, confidence)].

    The confidence is the turn's weakest window, not its average: a turn is a claim that the voice
    did not change anywhere inside it, so it is only as good as its least separated moment."""
    out: list[list] = []
    for (a, b), lab, s in zip(wins, labels, sep):
        if out and out[-1][2] == lab and a <= out[-1][1] + 1e-6:
            out[-1][1] = max(out[-1][1], b)
            out[-1][3] = min(out[-1][3], float(s))
        else:
            out.append([a, b, int(lab), float(s)])
    return [(round(a, 3), round(b, 3), lab, round(c, 2)) for a, b, lab, c in out if b - a >= min_turn]


def name_clusters(tns, known: dict | None, min_share: float = 0.5) -> dict[int, tuple[str, float]]:
    """{label: (name, confidence)} — an entity's name where the cluster's speech mostly lies inside
    intervals already attributed to that entity, an anonymous `spk<n>` otherwise.

    The confidence is the share of the cluster's duration that entity covers, which is also what
    decides the match: a cluster split between two entities names neither."""
    order = {}
    for _a, _b, lab, _c in tns:
        order.setdefault(lab, len(order))
    out = {lab: (f"spk{i + 1}", 0.0) for lab, i in order.items()}
    if not known:
        return out
    for lab in order:
        mine = [(a, b) for a, b, l, _c in tns if l == lab]
        total = sum(b - a for a, b in mine)
        if total <= 0:
            continue
        best, share = None, 0.0
        for ent, ivs in known.items():
            cov = sum(max(0.0, min(b, d) - max(a, c)) for a, b in mine for c, d in ivs)
            if cov / total > share:
                best, share = ent, cov / total
        if best and share >= min_share:
            out[lab] = (best, round(min(0.95, share), 2))
    return out


def emit(media: Path, L, spans, known: dict | None = None, min_conf: float = MIN_CONF,
         thresh: float = THRESH, log=lambda s: None) -> int:
    """Append `speech_turn` and `speaker_change` facts. Returns how many turns were emitted."""
    from .facts import T
    if not available():
        log("speechbrain not installed; no speaker turns")
        return 0
    wins = windows(spans)
    if len(wins) < MIN_WINDOWS:
        log(f"only {len(wins)} speech windows; too little to cluster")
        return 0
    V = embed(Path(media), wins)
    labels = cluster(V, thresh)
    if len(np.unique(labels)) < 2:
        log("one voice; no turn structure to report")     # X2
        return 0
    tns = [t for t in turns(wins, labels, separation(V, labels)) if t[3] >= min_conf]
    if len(tns) < 2 or len({t[2] for t in tns}) < 2:
        log("no speaker split survived the separation test")
        return 0
    names = name_clusters(tns, known)
    L.c("producer: ECAPA speaker embeddings clustered per clip, "
        "confidence = separation ranked against within-speaker scatter")
    for a, b, lab, conf in tns:
        who, nconf = names[lab]
        # `speech_turn(e2)` asserts two things at once — that one voice holds this whole interval,
        # and that the voice is e2 — so a named turn is worth the product of both. An anonymous
        # turn claims only the first and keeps the separation score on its own.
        L.fact(f"holds(speech_turn({who}), {T(a)}, {T(b)})", "ecapa_cluster",
               round(conf * nconf, 2) if nconf else conf)
    for (_a0, _b0, l0, _c0), (a1, _b1, l1, c1) in zip(tns, tns[1:]):
        if l0 != l1:
            L.fact(f"happens(speaker_change({names[l0][0]}, {names[l1][0]}), {T(a1)})",
                   "ecapa_cluster", round(min(0.9, c1), 2))
    return len(tns)


if __name__ == "__main__":
    from .audiofacts import speech_spans, word_times
    from .facts import Log
    L = Log()
    ws, _ = word_times(Path(sys.argv[1]))
    emit(Path(sys.argv[1]), L, speech_spans(ws), log=lambda s: print(f"% {s}", file=sys.stderr))
    print(L.text(), end="")
