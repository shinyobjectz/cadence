"""Fact logs: one grammar for what a comp *is* and for what a clip *appears to be*.

    holds(Fluent, T0, T1).      fluent true on [T0, T1)
    happens(Event, T).          event at T
    src(Fact, Producer, Conf).  provenance — perceived facts only; a lifted fact has none,
                                and that absence is the claim that it is exact.

`lift(comp)` reads Cadence's own node state (vision/lua/props.lua, host-free) and emits the
grammar with no src lines. The same predicates come out of the perception producers for footage,
so a reader — human, rule or model — cannot tell from the syntax which source a fact came from,
only from whether it carries provenance.

Sampling is at the comp's own frame rate, so every time in a lifted log is a real frame boundary.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np

from . import ROOT
from .annotate import props, boxes_at, _tkey

AUDIO_KINDS = ("audio", "tts", "sfx", "music")

# Above this, lift() coarsens its sampling grid rather than sampling per frame.
MAX_SAMPLES = 1200
NON_VISUAL = AUDIO_KINDS + ("script", "world", "light")

# Cadence's own ease curves (lib/cadence/ease.lua), so a lifted ease name lowers straight back
# into a tween call. Sampled on a fixed grid and matched by shape.
def _bounce_out(x):
    n1, d1 = 7.5625, 2.75
    if x < 1 / d1: return n1 * x * x
    if x < 2 / d1: x -= 1.5 / d1; return n1 * x * x + 0.75
    if x < 2.5 / d1: x -= 2.25 / d1; return n1 * x * x + 0.9375
    x -= 2.625 / d1
    return n1 * x * x + 0.984375


_C1 = 1.70158
_C3 = _C1 + 1
_C4 = (2 * np.pi) / 3
EASES = {
    "linear": lambda x: x,
    "quadIn": lambda x: x * x,
    "quadOut": lambda x: 1 - (1 - x) ** 2,
    "quadInOut": lambda x: 2 * x * x if x < 0.5 else 1 - (-2 * x + 2) ** 2 / 2,
    "cubicIn": lambda x: x ** 3,
    "cubicOut": lambda x: 1 - (1 - x) ** 3,
    "cubicInOut": lambda x: 4 * x ** 3 if x < 0.5 else 1 - (-2 * x + 2) ** 3 / 2,
    "sineIn": lambda x: 1 - np.cos(x * np.pi / 2),
    "sineOut": lambda x: np.sin(x * np.pi / 2),
    "sineInOut": lambda x: -(np.cos(np.pi * x) - 1) / 2,
    "expoIn": lambda x: 0.0 if x == 0 else 2 ** (10 * x - 10),
    "expoOut": lambda x: 1.0 if x == 1 else 1 - 2 ** (-10 * x),
    "expoInOut": lambda x: 0.0 if x == 0 else 1.0 if x == 1 else (2 ** (20 * x - 10) / 2 if x < 0.5 else (2 - 2 ** (-20 * x + 10)) / 2),
    "backIn": lambda x: _C3 * x ** 3 - _C1 * x * x,
    "backOut": lambda x: 1 + _C3 * (x - 1) ** 3 + _C1 * (x - 1) ** 2,
    "elasticOut": lambda x: 0.0 if x == 0 else 1.0 if x == 1 else 2 ** (-10 * x) * np.sin((x * 10 - 0.75) * _C4) + 1,
    "elasticIn": lambda x: 0.0 if x == 0 else 1.0 if x == 1 else -(2 ** (10 * x - 10)) * np.sin((x * 10 - 10.75) * _C4),
    "bounceOut": _bounce_out,
    "bounceIn": lambda x: 1 - _bounce_out(1 - x),
}
_EASE_GRID = np.linspace(0, 1, 13)
EASE_TEMPLATES = {k: np.array([f(float(x)) for x in _EASE_GRID]) for k, f in EASES.items()}


def T(x: float) -> str:
    return f"{x:.3f}"


def third(x: float) -> str:
    return "left" if x < 1 / 3 else ("right" if x > 2 / 3 else "center")


def band(y: float) -> str:
    return "upper" if y < 1 / 3 else ("lower" if y > 2 / 3 else "middle")


def runs(samples: list[tuple[float, object]], min_len: float) -> list[tuple[object, float, float]]:
    """[(t, value)] sorted -> [(value, t0, t1)] merging equal neighbours, dropping runs shorter than min_len."""
    out: list[list] = []
    for t, v in samples:
        if out and out[-1][0] == v:
            out[-1][2] = t
        else:
            out.append([v, t, t])
    return [(v, a, b) for v, a, b in out if b - a >= min_len]


def agreed_runs(samples: list[tuple[float, object]], min_len: float = 0.0, same=None,
                min_samples: int = 2) -> list[tuple[object, float, float, float]]:
    """[(t, value)] -> [(value, t0, t1, agreement)], where agreement is the confidence.

    X1, in one place, because every perceived fluent needs the same thing. A producer's own score
    says how sure a model is about a frame; what a reader of the log needs is how sure the *clip*
    is about an interval, and that is measurable: the fraction of the interval's samples that read
    the same way. A caption re-read differently every other frame, a head that flickers between
    facing left and facing the camera — both are states that were not read, and the number says so.

    Two details matter, and both were found by getting them wrong. A single deviating sample
    between two that agree is a misread rather than a change, so it is smoothed into the run it
    interrupts instead of splitting it in three. And a group too short to claim as an interval of
    its own is folded into its neighbour rather than dropped, because dropping it reports a word
    misread once, or a pose lost on one frame, as read perfectly.

    `same` compares two values when equality is too strict — OCR passes one that ignores the
    difference between a clean reading and a scuffed one."""
    eq = same or (lambda a, b: a == b)
    if not samples:
        return []
    vals = [v for _t, v in samples]
    lab = list(vals)
    for i in range(1, len(lab) - 1):
        if not eq(lab[i], lab[i - 1]) and eq(lab[i - 1], lab[i + 1]):
            lab[i] = lab[i - 1]
    groups: list[list] = []
    for (t, v), g in zip(samples, lab):
        if groups and eq(groups[-1][3], g):
            groups[-1][0].append(v)
            groups[-1][2] = t
        else:
            groups.append([[v], t, t, g])
    while len(groups) > 1:
        short = next((k for k, g in enumerate(groups) if len(g[0]) < min_samples), None)
        if short is None:
            break
        lo = short - 1 if short else 0
        groups[lo] = [groups[lo][0] + groups[lo + 1][0], groups[lo][1], groups[lo + 1][2], groups[lo][3]]
        del groups[lo + 1]
    out = []
    for members, a, b, _g in groups:
        if b - a < min_len or len(members) < min_samples:
            continue
        best = max(members, key=lambda v: sum(eq(v, x) for x in members))
        out.append((best, a, b, round(sum(eq(best, x) for x in members) / len(members), 2)))
    return out


class Log:
    """Accumulates fact lines. A producer passes its name and confidence; a lifter passes neither."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def c(self, s: str) -> None:
        self.lines.append(f"% {s}")

    def fact(self, term: str, producer: str | None = None, conf: float | None = None) -> None:
        self.lines.append(f"{term}.")
        if producer is not None:
            self.lines.append(f"src({term}, {producer}, {conf:.2f}).")

    def blank(self) -> None:
        self.lines.append("")

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


# ----------------------------------------------------------------------------- parsing

def parse_term(s: str):
    """'holds(motion(e1, dir(left), slow), 1.0, 2.0)' -> nested tuples; numbers and strings as values."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"'):
        return s[1:-1]
    head, sep, rest = s.partition("(")
    if not sep or not s.endswith(")"):
        try:
            return float(s)
        except ValueError:
            return s
    args, depth, cur, q, esc = [], 0, "", False, False
    for ch in rest[:-1]:
        # Quote-aware: a comma inside "a, b" is part of the string, not an argument
        # separator. Without this, any fact carrying prose -- says(), OCR text, a word
        # ending in a comma -- parsed with the wrong arity and silently stopped matching.
        if esc:
            cur += ch
            esc = False
            continue
        if ch == "\\" and q:
            cur += ch
            esc = True
            continue
        if ch == '"':
            q = not q
            cur += ch
            continue
        if not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                args.append(cur)
                cur = ""
                continue
        cur += ch
    if cur.strip():
        args.append(cur)
    return (head.strip(), *[parse_term(a) for a in args])


def parse_log(text: str) -> list[tuple]:
    facts = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("%"):
            continue
        if not line.endswith("."):
            raise ValueError(f"fact line does not end with '.': {line!r}")
        facts.append(parse_term(line[:-1]))
    return facts


def provenance(facts: list[tuple]) -> dict:
    """{fact: (producer, conf)} for perceived facts. A fact absent from this map is exact."""
    return {f[1]: (f[2], f[3]) for f in facts if f[0] == "src"}


# ----------------------------------------------------------------------------- the lifter

def _fit_ease(ts: np.ndarray, vals: np.ndarray, step: float) -> tuple[str, float, float]:
    """Recover (ease name, start, end) of a tween from sampled values.

    A tween rarely starts on a frame boundary, and assuming it does biases the curve toward the
    'out' eases. So the start and end are fitted too, over the sub-frame window the samples allow,
    which makes the returned interval more exact than the sample grid."""
    if len(ts) < 3 or abs(vals[-1] - vals[0]) < 1e-9:
        return "linear", float(ts[0]), float(ts[-1])
    v0, v1 = float(vals[0]), float(vals[-1])
    best = ("linear", float(ts[0]), float(ts[-1]), np.inf)
    for t0 in np.linspace(ts[0] - step, ts[0] + 2 * step, 13):
        for t1 in np.linspace(ts[-1] - 2 * step, ts[-1] + step, 13):
            if t1 - t0 < step:
                continue
            x = np.clip((ts - t0) / (t1 - t0), 0.0, 1.0)
            for name, f in EASES.items():
                pred = v0 + (v1 - v0) * np.array([f(float(xx)) for xx in x])
                sse = float(np.sum((pred - vals) ** 2)) / max(abs(v1 - v0) ** 2, 1e-12)
                if sse < best[3]:
                    best = (name, float(t0), float(t1), sse)
    return best[0], best[1], best[2]


def _visible(box, opacity: float, W: int, H: int) -> bool:
    x, y, w, h = box
    return opacity > 0.02 and w > 0 and h > 0 and x + w >= 0 and y + h >= 0 and x <= W and y <= H


_MEDIA_DUR: dict[str, float | None] = {}


def _media_duration(media: Path) -> float | None:
    """Length of an audio/video file, or None if it cannot be read.

    Needed because props.lua evaluates the comp *without* the resolve phase, so an audio node
    that did not declare `duration` has none: resolve is what fills it in from the media. Without
    this every such clip lifts as playing until the end of the comp, and a log with eleven
    narration clips says all eleven sound at once for its whole length."""
    key = str(media)
    if key not in _MEDIA_DUR:
        try:
            r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "default=nw=1:nk=1", key], capture_output=True, text=True)
            _MEDIA_DUR[key] = float(r.stdout.strip())
        except (ValueError, OSError):
            _MEDIA_DUR[key] = None
    return _MEDIA_DUR[key]


def _words_for(media: Path, text: str) -> list[dict]:
    """Word timings for a comp's own audio clip, relative to the clip's start.

    Silence is the right answer when the media is missing or the aligner cannot run:
    a wrong word time is worse than no word time, because an edit would be anchored to it."""
    try:
        if not Path(media).exists():
            return []
        from . import audiofacts as AF
        return AF.align(Path(media), text)
    except Exception:                                      # noqa: BLE001 -- see docstring
        return []


def lift(comp: Path, sample_fps: float | None = None, want_words: bool = False,
         want_beats: bool = False) -> str:
    """Exact fact log for a comp. Sampled at the comp's frame rate unless sample_fps says otherwise.

    `want_words` additionally force-aligns every audio node that declares both a media file
    and its own text, emitting happens(word(...)) so an edit can be anchored to something
    that was *said* rather than to a raw time. Those lines carry src(): a word timing is
    measured by the aligner even when the sentence it came from is exact, and the rule that
    a fact without src is exact outranks the convention that a lifted log has no src at all.
    It costs an alignment pass per clip, so it is off by default.

    `want_beats` does the same for rhythm: tempo, beat grid and onsets for every audio node,
    so a cut or a reveal can be anchored to the music rather than to a number. Also measured,
    also src-carrying, also off by default."""
    comp = Path(comp)
    head = props(comp, [0.0])
    fps = float(head["fps"])
    dur = float(head["duration"])
    W, H = int(head["width"]), int(head["height"])
    # Sampling at the comp's frame rate is right for a 3s case and absurd for a 160s one:
    # 4 814 evaluations in a single luajit call behind a 120s timeout, producing a log no
    # reader wants. Long comps drop to a coarser grid unless the caller insists.
    if sample_fps is None and dur * fps > MAX_SAMPLES:
        sample_fps = max(2.0, MAX_SAMPLES / dur)
    step = 1.0 / (sample_fps or fps)
    times = [round(i * step, 4) for i in range(int(dur / step) + 1)]
    d = props(comp, times)
    meta = {n["id"]: n for n in d["nodes"]}

    L = Log()
    if sample_fps:
        L.c(f"sampled at {sample_fps:g} fps (comp is {fps:g} fps, {dur:g}s)")
    L.c(f"fact log for {comp.name} — lifted from comp state, exact"
        + ("; word timings are force-aligned and say so" if want_words else " (no src lines)"))
    L.c("grammar: holds(F,T0,T1) happens(E,T) src(Fact,Producer,Conf)")
    L.fact(f'comp("{comp.name}")')
    L.fact(f"fps({fps:g})")
    L.fact(f"duration({T(dur)})")
    L.fact(f"frame_size({W}, {H})")
    audio_nodes = [n for n in d["nodes"] if n.get("kind") in AUDIO_KINDS]
    L.fact(f"audio({'present' if audio_nodes else 'none'})")
    L.fact(f'shot(s1, {T(0.0)}, {T(dur)}, comp("{comp.name}"))')
    L.fact("holds(camera(still), " + T(0.0) + ", " + T(dur) + ")")
    L.blank()

    # gather per-node observations
    obs: dict[str, list[tuple]] = {}
    for t in times:
        row = d["at"].get(_tkey(t), {})
        for nid, n in boxes_at(d, t)["nodes"].items():      # boxes are parent-composed
            b = (n["x"], n["y"], n["w"], n["h"])
            op = float(n.get("opacity") or 0)
            if not _visible(b, op, W, H):
                continue
            obs.setdefault(nid, []).append((t, b, op, row.get(nid, {}), n))

    L.c("entities: one per comp node, bound by node id")
    order = sorted(obs, key=lambda nid: (obs[nid][0][0], meta[nid].get("z") or 0))
    for nid in order:
        o = obs[nid]
        m = meta[nid]
        kind = m.get("kind", "?")
        L.fact(f'entity({nid}, "{kind}", node("{nid}"))')
        L.fact(f"z({nid}, {m.get('z') or 0})")
        col = o[0][3].get("color")
        if isinstance(col, list) and len(col) >= 3:
            L.fact(f'color({nid}, "#{"".join(f"{int(round(c * 255)):02x}" for c in col[:3])}")')

        ts = np.array([x[0] for x in o])
        segs = [[ts[0], ts[0]]]
        for t in ts[1:]:
            if t - segs[-1][1] > 1.5 * step:
                segs.append([t, t])
            else:
                segs[-1][1] = t
        for a, b in segs:
            L.fact(f"holds(visible({nid}), {T(a)}, {T(min(b + step, dur))})")
        if ts[0] > step / 2:
            L.fact(f"happens(appear({nid}), {T(ts[0])})")
        if ts[-1] < dur - 1.5 * step:
            L.fact(f"happens(disappear({nid}), {T(ts[-1] + step)})")

        pos = np.array([[x[1][0], x[1][1]] for x in o])   # world top-left: a parented node moves with its parent
        cs = np.array([[x[1][0] + x[1][2] / 2, x[1][1] + x[1][3] / 2] for x in o])
        ws = np.array([max(x[1][2], 1e-6) for x in o])
        hs = np.array([max(x[1][3], 1e-6) for x in o])
        ar = ws * hs
        ops = np.array([x[2] for x in o])

        for v, a, b in runs([(t, third(c[0] / W)) for t, c in zip(ts, cs)], 0.0):
            L.fact(f"holds(in_third({nid}, {v}), {T(a)}, {T(min(b + step, dur))})")
        for v, a, b in runs([(t, band(c[1] / H)) for t, c in zip(ts, cs)], 0.0):
            L.fact(f"holds(in_band({nid}, {v}), {T(a)}, {T(min(b + step, dur))})")

        # motion is a change of the node's transform, not of its box: a caption whose text
        # changes, or a bar whose width grows, has not moved.
        moving = []
        for i in range(len(ts)):
            if i == 0:
                moving.append((ts[i], ("still",)))
                continue
            vx = (pos[i][0] - pos[i - 1][0]) / W / step
            vy = (pos[i][1] - pos[i - 1][1]) / H / step
            sp = float(np.hypot(vx, vy))
            if sp < 1e-4:
                moving.append((ts[i], ("still",)))
            else:
                dirn = ("right" if vx > 0 else "left") if abs(vx) >= abs(vy) else ("down" if vy > 0 else "up")
                moving.append((ts[i], ("move", dirn, "slow" if sp < 0.12 else "medium" if sp < 0.3 else "fast")))
        merged: list[list] = []
        for v, a, b in runs(moving, 0.0):
            if merged and merged[-1][0][0] == "move" and v[0] == "move" and merged[-1][0][1] == v[1]:
                merged[-1][2] = b                      # one gesture, whatever its speed profile
                merged[-1][0] = ("move", v[1], max(merged[-1][0][2], v[2], key=("slow", "medium", "fast").index))
            else:
                merged.append([v, a, b])
        for v, a, b in merged:
            if b - a < 2 * step:
                continue
            a = max(0.0, a - step)
            if v[0] == "still":
                L.fact(f"holds(still({nid}), {T(max(a, ts[0]))}, {T(min(b + step, dur))})")
                continue
            sel = (ts >= a) & (ts <= b + step)
            path = np.cumsum(np.r_[0.0, np.hypot(*np.diff(pos[sel], axis=0).T)])
            ez, s0, s1 = _fit_ease(ts[sel], path, step)
            L.fact(f"holds(motion({nid}, dir({v[1]}), {v[2]}, ease({ez})), {T(s0)}, {T(min(s1, dur))})")

        for v, a, b in runs([(t, "grow" if g > 1.0005 else "shrink" if g < 0.9995 else "same")
                             for t, g in zip(ts[1:], ar[1:] / ar[:-1])], 2 * step):
            if v != "same":
                sel = (ts >= a - step) & (ts <= b + step)
                # Fit the dimension that actually changes. Area is the *square* of a tween only when
                # w and h move together (a scale); when just one of them is tweened, sqrt(area) turns
                # a linear tween into quadOut, which is a real ease name and so fails silently.
                rw = float(np.ptp(ws[sel]) / max(np.mean(ws[sel]), 1e-9))
                rh = float(np.ptp(hs[sel]) / max(np.mean(hs[sel]), 1e-9))
                if min(rw, rh) > 0.9 * max(rw, rh):
                    track = np.sqrt(ar[sel])       # both dimensions: a scale
                else:
                    track = ws[sel] if rw >= rh else hs[sel]
                ez, s0, s1 = _fit_ease(ts[sel], track, step)
                L.fact(f"holds({'growing' if v == 'grow' else 'shrinking'}({nid}, ease({ez})), {T(s0)}, {T(min(s1, dur))})")

        for v, a, b in runs([(t, "in" if dop > 1e-4 else "out" if dop < -1e-4 else "same")
                             for t, dop in zip(ts[1:], np.diff(ops))], 2 * step):
            if v != "same":
                sel = (ts >= a - step) & (ts <= b + step)
                ez, s0, s1 = _fit_ease(ts[sel], ops[sel], step)
                L.fact(f"holds(fading_{v}({nid}, ease({ez})), {T(s0)}, {T(min(s1, dur))})")

        # text as a fluent: a caption that changes line is a sequence of intervals, not one string
        txts = [(t, str(x[3].get("text") or "")) for t, x in zip(ts, o)]
        if any(v for _, v in txts):
            tr = runs(txts, 0.0)
            for v, a, b in tr:
                if v:
                    L.fact(f'holds(text({nid}, "{v}"), {T(a)}, {T(min(b + step, dur))})')
            for v, a, b in tr[1:]:
                if v:
                    L.fact(f"happens(text_change({nid}), {T(a)})")
    L.blank()

    # Relations: exact, but only where they carry information. A full-frame backdrop overlaps
    # everything and says nothing, and a one-pixel clip is not an occlusion, so both are filtered —
    # the same salience discipline the perception producers need for the same reason (n^2 noise).
    L.c("relations: real occlusion only (backdrops excluded); depth order from z, later z paints over")
    frame_area = float(W * H)
    backdrops = set()
    for nid in order:
        bx = obs[nid][0][1]
        if bx[2] * bx[3] >= 0.9 * frame_area:
            backdrops.add(nid)
            L.fact(f"backdrop({nid})")
    ids = [n for n in order if n not in backdrops]
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            A = {x[0]: x[1] for x in obs[a]}
            B = {x[0]: x[1] for x in obs[b]}
            common = sorted(set(A) & set(B))
            if not common:
                continue
            ov = []
            for t in common:
                ax, ay, aw, ah = A[t]
                bx, by, bw, bh = B[t]
                iw = max(0.0, min(ax + aw, bx + bw) - max(ax, bx))
                ih = max(0.0, min(ay + ah, by + bh) - max(ay, by))
                ov.append((t, iw * ih >= 0.15 * min(aw * ah, bw * bh)))
            hit = False
            for v, s_, e_ in runs(ov, 0.0):
                if v:
                    hit = True
                    L.fact(f"holds(overlaps({a}, {b}), {T(s_)}, {T(min(e_ + step, dur))})")
            if hit:
                za, zb = meta[a].get("z") or 0, meta[b].get("z") or 0
                near, far = (a, b) if za > zb else (b, a)
                L.fact(f"holds(nearer({near}, {far}), {T(common[0])}, {T(min(common[-1] + step, dur))})")
    L.blank()

    if audio_nodes:
        L.c("audio: comp-owned clips and envelopes, exact (ground truth for the perceived audio producers)")
        for n in audio_nodes:
            nid = n["id"]
            rows = [(t, d["at"].get(_tkey(t), {}).get(nid)) for t in times]
            live = [(t, st) for t, st in rows if st is not None]
            if not live:
                continue
            L.fact(f'entity({nid}, "{n["kind"]}", node("{nid}"))')
            if n.get("src"):
                L.fact(f'clip({nid}, "{n["src"]}", media_start({n.get("media_start") or 0:g}))')
            said = live[0][1].get("text")
            if said:
                L.fact(f'says({nid}, "{said}")')
            at = float(n.get("at") or 0.0)
            clip_dur = n.get("dur")
            if clip_dur is None and n.get("src"):
                md = _media_duration(ROOT / n["src"])
                if md is not None:
                    clip_dur = md - float(n.get("media_start") or 0)
            end = at + float(clip_dur if clip_dur is not None else (dur - at))
            L.fact(f"holds(playing({nid}), {T(at)}, {T(min(end, dur))})")
            if n.get("fade_in"):
                L.fact(f"holds(fading_in({nid}, ease(linear)), {T(at)}, {T(at + float(n['fade_in']))})")
            if n.get("fade_out"):
                L.fact(f"holds(fading_out({nid}, ease(linear)), {T(end - float(n['fade_out']))}, {T(min(end, dur))})")
            # The sampler reports an audio node at every time, not just while it
            # sounds, so clamp the envelope to the clip's own window — otherwise a
            # clip with a non-zero `at` claims a volume over stretches where
            # `playing` is false, and the two exact facts contradict each other.
            stop = min(end, dur)
            vols = [(t, round(float(st.get("volume", 1) or 1), 2))
                    for t, st in live if at <= t <= stop]
            for v, a, b in runs(vols, 0.0):
                lo, hi = max(a, at), min(b + step, stop)
                if hi > lo:
                    L.fact(f"holds(volume({nid}, {v}), {T(lo)}, {T(hi)})")
            if want_beats and n.get("src"):
                media = ROOT / n["src"]
                if Path(media).exists():
                    try:
                        from . import audiofacts as AF
                        bt = AF.beats(Path(media))
                    except Exception:                      # noqa: BLE001 -- silence beats a guess
                        bt = {"tempo": None, "beats": [], "onsets": []}
                    if bt.get("tempo"):
                        L.fact(f"tempo({nid}, {bt['tempo']:g})", "librosa", 0.6)
                    for i, bts in enumerate(bt.get("beats") or []):
                        if at + bts <= stop:
                            L.fact(f"happens(beat({nid}_b{i + 1}), {T(at + bts)})", "librosa", 0.6)
                    for i, on in enumerate(bt.get("onsets") or []):
                        if at + on <= stop:
                            L.fact(f"happens(onset({nid}_o{i + 1}), {T(at + on)})", "librosa", 0.5)
            if want_words and said and n.get("src"):
                for i, wd in enumerate(_words_for(ROOT / n["src"], said)):
                    safe = wd["word"].replace('"', "'")
                    L.fact(f'happens(word({nid}_w{i + 1}, "{safe}"), {T(at + wd["start"])})',
                           "forced_align", wd.get("conf", 0.9))
        L.blank()

    return L.text()


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(lift(Path(p)), end="")
