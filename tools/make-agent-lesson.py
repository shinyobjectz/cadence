#!/usr/bin/env python3
"""Generate comps/agent/facts-on-footage.lua: real footage, the tracker boxes the facts were
derived from, the facts lighting up while they hold, and an edit that lands on the picture.

Every number drawn comes out of the fact layer, not out of this file:
  * boxes      -- the cached SAM2 tracks the perceiver used (vision/cache/clips/.*.tracks-*.pkl)
  * facts      -- the perceived log, read through cadence_vision.query
  * the edit   -- cadence_vision.edits.apply(verify="frames") on comps/demo/handoff.lua, run now

The clip is 14 s and the lesson is ~105 s, so chapters replay parts of it. Overlays therefore
follow *media* time, not comp time: every overlay property is baked over the whole comp from a
media_at(t) map, one sample per output frame, so each frame lands exactly on a sample and a
replay cut never smears a box across the jump.

    vision/.venv/bin/python tools/make-agent-lesson.py
"""
from __future__ import annotations

import glob
import json
import pickle
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))
from cadence_vision import edits as ED            # noqa: E402
from cadence_vision.query import Log              # noqa: E402

CLIP = "vision/cache/clips/person_7876232.mp4"
LOGJ = ROOT / "vision/cache/clips/person_7876232.fact_log.json"
NARR = json.loads((ROOT / "comps/agent/narration.json").read_text())
OUT = ROOT / "comps/agent/facts-on-footage.lua"

W, H, FPS = 1920, 1080, 25
SRC_W, SRC_H, CLIPDUR = 960, 506, 14.28
VS, VY = 2.0, 34                                  # clip drawn at 2x, letterboxed
LEAD, GAP, TAIL = 0.6, 0.8, 1.2
MEDIA = {"open": 0.0, "track": 0.0, "thirds": 1.8, "motion": 5.0, "event": 5.0,
         "edit": 5.5, "proof": 0.0, "close": 2.0}
BOXES_ON = {"track", "thirds", "motion", "event", "edit"}


def q(x: str) -> str:
    return x.replace("\\", "\\\\").replace('"', '\\"')


def num(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".") if v % 1 else f"{v:.1f}"


# ------------------------------------------------------------------ timing
at, cur = {}, LEAD
for m in NARR:
    at[m["id"]] = cur
    cur += m["dur"] + GAP
TOTAL = round((cur - GAP + TAIL) * FPS) / FPS           # a whole number of frames
NFR = int(round(TOTAL * FPS)) + 1
win = {}
ids = [m["id"] for m in NARR]
for i, m in enumerate(NARR):
    a = 0.0 if i == 0 else at[m["id"]] - 0.35
    b = TOTAL if i == len(NARR) - 1 else at[m["id"]] + m["dur"] + 0.45
    win[m["id"]] = (round(a, 3), round(b, 3))

# video shots: each chapter window replays the clip from its media start, looping if needed
shots = []
for cid in ids:
    a, b = win[cid]
    s = MEDIA[cid]
    t = a
    while t < b - 1e-6:
        L = min(b - t, CLIPDUR - s)
        shots.append((round(t, 3), round(L, 3), s))
        t += L


def media_at(t: float) -> float | None:
    for fr, L, s in shots:
        if fr <= t < fr + L + 1e-9:
            return s + (t - fr)
    return None


def chapter_at(t: float) -> str:
    for cid in ids:
        a, b = win[cid]
        if a <= t < b:
            return cid
    return ids[-1]


def env(t: float, cid: str, ramp: float = 0.3) -> float:
    a, b = win[cid]
    return max(0.0, min(1.0, (t - a) / ramp, (b - t) / ramp))


FT = [i / FPS for i in range(NFR)]
FM = [media_at(t) for t in FT]
FC = [chapter_at(t) for t in FT]


def runs(pred) -> list[tuple[float, float]]:
    """Comp-time windows, at frame resolution, where pred(t, media, chapter) holds."""
    out, start = [], None
    for t, m, c in zip(FT, FM, FC):
        ok = m is not None and pred(t, m, c)
        if ok and start is None:
            start = t
        elif not ok and start is not None:
            out.append((start, t))
            start = None
    if start is not None:
        out.append((start, TOTAL))
    return out


# ------------------------------------------------------------------ the fact layer
lj = json.loads(LOGJ.read_text())
LOG = Log.parse(lj["log"], "person_7876232")
ENT = {h.bind["E"]: (str(h.bind["C"]), h.conf) for h in LOG.match("entity(E, C, _)")}
by_cls = {c: e for e, (c, _) in ENT.items()}
rel = LOG.match("happens(release(A, B), T)")
assert rel, "the perceived log has no release event -- was it perceived with contact=true?"
REL = rel[0]
REL_T = REL.bind["T"]
E1 = by_cls.get("beer bottle", "e1")
E2 = by_cls.get("gloved hand", "e2")
THIRDS = [(h.bind["P"], h.bind["T0"], h.bind["T1"], h.conf)
          for h in LOG.match(f"holds(in_third({E1}, P), T0, T1)")]
MOTION = [(h.fact, h.bind["T0"], h.bind["T1"], h.conf)
          for h in LOG.match(f"holds(motion({E1}, D, S), T0, T1)")]


def fact_line(f) -> str:
    from cadence_vision.query import term_str
    # term_str keeps whole floats as 7.000 (a float, not an int); on screen 7.0 reads the same as 2.4
    return re.sub(r"\b(\d+)\.000\b", r"\1.0", term_str(f)) + "."


# tracks: the pickle written by the same perceive run
pk = sorted(glob.glob(str(ROOT / "vision/cache/clips/.person_7876232.tracks-*.pkl")),
            key=lambda p: Path(p).stat().st_mtime)
TRACKS = pickle.loads(Path(pk[-1]).read_bytes())
TR = {tr["cls"]: sorted(tr["obs"], key=lambda o: o[0]) for tr in TRACKS}


def box_at(cls: str, m: float):
    obs = TR.get(cls)
    if not obs or m < obs[0][0] - 0.12 or m > obs[-1][0] + 0.12:
        return None
    ts = [o[0] for o in obs]
    j = max(0, min(len(obs) - 1, sum(1 for x in ts if x <= m) - 1))
    if j + 1 < len(obs) and ts[j + 1] > ts[j]:
        f = max(0.0, min(1.0, (m - ts[j]) / (ts[j + 1] - ts[j])))
        a, b = obs[j][1], obs[j + 1][1]
        bx = [a[k] + (b[k] - a[k]) * f for k in range(4)]
    else:
        bx = list(obs[j][1])
    return (bx[0] * SRC_W * VS, bx[1] * SRC_H * VS + VY, bx[2] * SRC_W * VS, bx[3] * SRC_H * VS + VY)


# the edit, proved now, on the comp that composites this same clip
EDIT = [{"verb": "set_cue", "node": "text3", "index": 0, "t0": REL_T, "t1": REL_T + 2.0,
         "text": "the bottle changes hands"}]
PROOF = ED.apply(ROOT / "comps/demo/handoff.lua", EDIT, verify="frames", write=False)
assert PROOF["ok"], PROOF
PF = PROOF["frames"]
CHANGED = set(PF["changed"])
wins, run = [], []
for i in sorted(CHANGED):
    if run and i == run[-1] + 1:
        run.append(i)
    else:
        if run:
            wins.append(run)
        run = [i]
if run:
    wins.append(run)
WIN_TXT = "  ·  ".join(f"{r[0] / 25:.2f}–{(r[-1] + 1) / 25:.2f}s" for r in wins)
# one space after the +/-: the comp's own indentation would push the line past the card
LUA_DIFF = [re.sub(r"^([+-])\s+", r"\1 ", l.strip()).rstrip(",") for l in PROOF["lua_diff"]
            if l.startswith(("+", "-")) and not l.startswith(("+++", "---"))]

# ------------------------------------------------------------------ emit
L: list[str] = []
V: list[str] = []            # node constructors
B: list[str] = []            # bakes (cursor 0, run alongside everything)
S: list[str] = []            # the chapter timeline
seen: set[str] = set()


def w(s=""):
    L.append(s)


def nd(var, kind, props, op=0):
    assert var not in seen, var
    seen.add(var)
    V.append(f"    N.{var} = s:{kind} {{ {props}, opacity = {op} }}")
    return f"N.{var}"


def bake(node, prop, vals, places=1):
    B.append(f'      t:follow({node}, "{prop}", {{{",".join(f"{v:.{places}f}" for v in vals)}}},'
             f" {{ base = 0, gain = 1, duration = {TOTAL} }})")


def cap(x, y, size, color, cues, font="MONO", anchor="center"):
    if not cues:
        return
    V.append(f"    s:captions {{ x = {x}, y = {y}, size = {size}, font = {font}, color = {color},"
             f' anchor = "{anchor}", cues = {{')
    for a, b, tx in cues:
        V.append(f'      {{ {a:.3f}, {b:.3f}, "{q(tx)}" }},')
    V.append("    } }")


# --- footage, one video node per shot
for i, (fr, L_, s) in enumerate(shots):
    V.append(f'    s:video {{ src = CLIP, x = 0, y = {VY}, w = {W}, h = {int(SRC_H * VS)},'
             f" from = {fr}, duration = {L_}, media_start = {s} }}")

# --- overlays: tracker boxes and labels, baked in media time
ECOL = {E1: "TEAL", E2: "AMBER"}
GEOM = {}
for e, cls in ((E1, "beer bottle"), (E2, "gloved hand")):
    tag = e
    col = ECOL[e]
    parts = {k: nd(f"{tag}_{k}", "rect", f"x = 0, y = 0, w = 3, h = 3, color = {col}")
             for k in ("top", "bot", "lef", "rig")}
    X0, Y0, X1, Y1, ON = [], [], [], [], []
    for t, m, c in zip(FT, FM, FC):
        bx = box_at(cls, m) if (m is not None and c in BOXES_ON) else None
        if bx is None:
            # hold the last position while invisible so a fade never slides in from 0,0
            X0.append(X0[-1] if X0 else 0); Y0.append(Y0[-1] if Y0 else 0)
            X1.append(X1[-1] if X1 else 3); Y1.append(Y1[-1] if Y1 else 3); ON.append(0)
        else:
            X0.append(bx[0]); Y0.append(bx[1]); X1.append(bx[2]); Y1.append(bx[3])
            ON.append(env(t, c))
    Wd = [max(3, b - a) for a, b in zip(X0, X1)]
    Hd = [max(3, b - a) for a, b in zip(Y0, Y1)]
    bake(parts["top"], "x", X0); bake(parts["top"], "y", Y0); bake(parts["top"], "w", Wd)
    bake(parts["bot"], "x", X0); bake(parts["bot"], "y", [v - 3 for v in Y1]); bake(parts["bot"], "w", Wd)
    bake(parts["lef"], "x", X0); bake(parts["lef"], "y", Y0); bake(parts["lef"], "h", Hd)
    bake(parts["rig"], "x", [v - 3 for v in X1]); bake(parts["rig"], "y", Y0); bake(parts["rig"], "h", Hd)
    for p in parts.values():
        bake(p, "opacity", ON, 2)
    GEOM[e] = (cls, X0, Y0, X1, Y1, ON)
    if e == E1:
        E1X0, E1Y0, E1X1, E1Y1 = X0, Y0, X1, Y1

# labels last, so no box edge is ever drawn across one; each sits on a dark chip because a
# bright label over a lit column (or a white backdrop) otherwise disappears
for e, (cls, X0, Y0, X1, Y1, ON) in GEOM.items():
    col = ECOL[e]
    text = f"{e}  {cls}  {ENT[e][1]:.2f}"
    cw = int(len(text) * 14.6 + 22)
    if e == E1:
        lx, ly = X0, [max(VY + 4, v - 40) for v in Y0]
    else:
        lx, ly = [v + 8 for v in X0], [v - 44 for v in Y1]
    chip = nd(f"{e}_chip", "rect", f'x = 0, y = 0, w = {cw}, h = 36, rx = 4, color = "#0a0e14e0"')
    lab = nd(f"{e}_lab", "text", f'x = 0, y = 0, text = "{text}", size = 24, font = MONO, color = {col}')
    bake(chip, "x", [v - 10 for v in lx]); bake(chip, "y", [v - 4 for v in ly]); bake(chip, "opacity", ON, 2)
    bake(lab, "x", lx); bake(lab, "y", ly); bake(lab, "opacity", ON, 2)

# --- thirds: guides, and the column the bottle is in, driven by the in_third facts
colx, colop = [], []
for t, m, c in zip(FT, FM, FC):
    hit = None
    if c == "thirds" and m is not None:
        for P, t0, t1, _ in THIRDS:
            if t0 <= m < t1:
                hit = P
    colx.append({"left": 0, "center": 640, "right": 1280}.get(hit, colx[-1] if colx else 640))
    colop.append(0.16 * env(t, "thirds") if hit else 0)
colr = nd("th_col", "rect", f"x = 640, y = {VY}, w = 640, h = {int(SRC_H * VS)}, color = TEAL")
bake(colr, "x", colx); bake(colr, "opacity", colop, 3)

# --- motion: a readout that follows the bottle while each motion fact holds
for k, (f, t0, t1, cf) in enumerate(MOTION):
    d = f[1][2]
    dirn = d[1] if isinstance(d, tuple) else str(d)
    speed = f[1][3]
    arrow = {"left": "<-", "right": "->", "up": "^", "down": "v"}.get(dirn, "")
    tx = nd(f"mo_{k}", "text", f'x = 0, y = 0, text = "{arrow} {dirn}, {speed}", size = 30,'
            f" font = BOLD, color = INK")
    op = [env(t, "motion") if (c == "motion" and m is not None and t0 <= m < t1) else 0
          for t, m, c in zip(FT, FM, FC)]
    bake(tx, "x", E1X0); bake(tx, "y", [v + 12 for v in E1Y1]); bake(tx, "opacity", op, 2)

# --- the release: a tag at the bottle and a pulse around the frame, at every replay of 8.5s
pulse = []
for t, m, c in zip(FT, FM, FC):
    v = 0.0
    if c == "event" and m is not None and REL_T - 0.05 <= m < REL_T + 1.4:
        v = max(0.0, 1.0 - (m - REL_T) / 1.4) if m >= REL_T else 1.0
    pulse.append(v)
for k, (x, y, ww, hh) in enumerate(((0, VY, W, 6), (0, VY + int(SRC_H * VS) - 6, W, 6),
                                    (0, VY, 6, int(SRC_H * VS)), (W - 6, VY, 6, int(SRC_H * VS)))):
    r = nd(f"ev_f{k}", "rect", f"x = {x}, y = {y}, w = {ww}, h = {hh}, color = TEAL")
    bake(r, "opacity", pulse, 2)
tag_text = f"happens(release({E1}, {E2}), {num(REL_T)})"
tchip = nd("ev_tchip", "rect", f'x = 0, y = 0, w = {int(len(tag_text) * 17) + 20}, h = 42, rx = 4,'
           f' color = "#0a0e14e0"')
bake(tchip, "x", [v - 10 for v in E1X0]); bake(tchip, "y", [v + 10 for v in E1Y1])
bake(tchip, "opacity", pulse, 2)
tagn = nd("ev_tag", "text", f'x = 0, y = 0, text = "{tag_text}",'
          f" size = 28, font = MONO, color = INK")
bake(tagn, "x", E1X0); bake(tagn, "y", [v + 14 for v in E1Y1]); bake(tagn, "opacity", pulse, 2)

# --- the edit, landing on the footage: a lower third exactly while media is in its new window
edit_cues = [(a, b, "the bottle changes hands")
             for a, b in runs(lambda t, m, c: c == "edit" and REL_T <= m < REL_T + 2.0)]
V.append(f'    N.ed_bar = s:rect {{ x = 0, y = 790, w = {W}, h = 96, color = "#0a0e14cc", opacity = 0 }}')
seen.add("ed_bar")
bar_op = [1.0 if any(a <= t < b for a, b, _ in edit_cues) else 0.0 for t in FT]
bake("N.ed_bar", "opacity", bar_op, 2)
cap(W // 2, 838, 46, "INK", edit_cues, font="BOLD")

# ------------------------------------------------------------------ chapter furniture
CARD_X, CARD_Y, CARD_W = 1170, 176, 716
chap = {}


def header(cid, n, title):
    ns = []
    if n:
        ns.append(nd(f"{cid}_n", "text", f'x = 64, y = 60, text = "{n}", size = 26, font = MONO, color = TEAL'))
    if title:
        ns.append(nd(f"{cid}_t", "text", f'x = 64, y = 94, text = "{q(title)}", size = 46,'
                     f" font = BOLD, color = INK"))
    return ns


def card(cid, rows, top=CARD_Y, note=None):
    """A fact card: fact lines in ink, their src lines faint beneath, an optional note last."""
    h = 36 + sum(76 if s else 46 for _, s in rows) + (44 if note else 0)
    ns = [nd(f"{cid}_card", "rect", f'x = {CARD_X}, y = {top}, w = {CARD_W}, h = {h}, rx = 10,'
             f' color = "#0a0e14e6"')]
    y = top + 24
    for k, (f, s) in enumerate(rows):
        ns.append(nd(f"{cid}_f{k}", "text", f'x = {CARD_X + 24}, y = {y}, text = "{q(f)}",'
                     f" size = 22, font = MONO, color = INK"))
        if s:
            ns.append(nd(f"{cid}_s{k}", "text", f'x = {CARD_X + 44}, y = {y + 32}, text = "{q(s)}",'
                         f" size = 19, font = MONO, color = FAINT"))
        y += 76 if s else 46
    if note:
        ns.append(nd(f"{cid}_note", "text", f'x = {CARD_X + 24}, y = {y + 4}, text = "{q(note)}",'
                     f" size = 21, font = SANS, color = INK"))
    return ns, y


def src_of(fact_text, prod, conf):
    return f"src(.., {prod}, {conf:.2f})."


def highlight(cid, rows_y, facts):
    """Re-draw a fact line in teal exactly while it holds in the media on screen."""
    for (fl, t0, t1), y in zip(facts, rows_y):
        cues = [(a, b, fl) for a, b in runs(lambda t, m, c, t0=t0, t1=t1: c == cid and t0 <= m < t1)]
        cap(CARD_X + 24, y, 22, "TEAL", cues, anchor="topleft")


# 00 open -- clean footage, a title
chap["open"] = [nd("op_t", "text", f'x = {W // 2}, y = 470, text = "Facts on real footage", size = 92,'
                   f' font = BOLD, color = INK, anchor = "center"'),
                nd("op_s", "text", f'x = {W // 2}, y = 560, text = "a real clip, and everything measured from it",'
                   f' size = 32, font = SANS, color = INK, anchor = "center"')]

# 01 tracker
rows = []
for e in (E1, E2):
    cls, cf = ENT[e]
    h = LOG.match(f'entity({e}, "{cls}", S)')[0]
    rows.append((fact_line(h.fact), src_of(None, h.producer, h.conf)))
    v = LOG.match(f"holds(visible({e}), T0, T1)")
    if v:
        rows.append((fact_line(v[0].fact), src_of(None, v[0].producer, v[0].conf)))
ns, _ = card("track", rows)
chap["track"] = header("track", "01", "The tracker") + ns

# 02 thirds
guides = [nd("th_g1", "rect", f"x = 639, y = {VY}, w = 3, h = {int(SRC_H * VS)}, color = INK"),
          nd("th_g2", "rect", f"x = 1279, y = {VY}, w = 3, h = {int(SRC_H * VS)}, color = INK")]
labels = [nd(f"th_l{k}", "text", f'x = {x}, y = {VY + 20}, text = "{t}", size = 22, font = MONO,'
             f' color = INK, anchor = "center"') for k, (x, t) in enumerate(((320, "LEFT"), (960, "CENTER"), (1600, "RIGHT")))]
rows = [(fact_line(("holds", ("in_third", E1, P), t0, t1)), f"src(.., sam2, {cf:.2f}).")
        for P, t0, t1, cf in THIRDS]
ns, _ = card("thirds", rows)
ys = [CARD_Y + 24 + 76 * k for k in range(len(rows))]
highlight("thirds", ys, [(r[0], t0, t1) for r, (_, t0, t1, _) in zip(rows, THIRDS)])
chap["thirds"] = header("thirds", "02", "A position becomes a word") + guides + labels + ns

# 03 motion
mrows = [(fact_line(f), f"src(.., sam2, {cf:.2f}).") for f, _, _, cf in MOTION]
ns, _ = card("motion", mrows)
highlight("motion", [CARD_Y + 24 + 76 * k for k in range(len(mrows))],
          [(r[0], t0, t1) for r, (_, t0, t1, _) in zip(mrows, MOTION)])
chap["motion"] = header("motion", "03", "So does a velocity") + ns

# 04 event
rows = [(fact_line(REL.fact), src_of(None, REL.producer, REL.conf))]
ns, y = card("event", rows, note="measured from the gap between the two masks")
highlight("event", [CARD_Y + 24], [(rows[0][0], REL_T - 0.05, REL_T + 1.4)])
chap["event"] = header("event", "04", "And the moment itself") + ns

# 05 edit -- the agent's three calls, revealed in order
blocks = [
    ("1  ask", [f'fact_when(event = "release")', f"-> {fact_line(REL.fact)}", f"-> T = {num(REL_T)}"]),
    ("2  assert", [f'set_cue(text3, 0, t0 = {num(REL_T)}, t1 = {num(REL_T + 2)},',
                   f'        text = "{EDIT[0]["text"]}")']),
    ("3  lowered", LUA_DIFF),
]
edit_nodes, beats, y = [], [], CARD_Y
for k, (head, lines) in enumerate(blocks):
    hh = 58 + 34 * len(lines)
    grp = [nd(f"ed_c{k}", "rect", f'x = {CARD_X}, y = {y}, w = {CARD_W}, h = {hh}, rx = 10, color = "#0a0e14e6"'),
           nd(f"ed_h{k}", "text", f'x = {CARD_X + 24}, y = {y + 16}, text = "{q(head)}", size = 20,'
              f" font = MONO, color = TEAL")]
    for j, ln in enumerate(lines):
        col = "AMBER" if ln.startswith("-") and k == 2 else ("TEAL" if ln.startswith("+") else "INK")
        grp.append(nd(f"ed_l{k}{j}", "text", f'x = {CARD_X + 24}, y = {y + 50 + 34 * j}, text = "{q(ln[:56])}",'
                      f" size = 21, font = MONO, color = {col}"))
    beats.append((1.0 + 2.6 * k, grp))
    edit_nodes += grp
    y += hh + 14
chap["edit"] = header("edit", "05", "The agent edits")

# 06 proof -- the real frame grid; the scrim is created first so it draws beneath the grid
dim = nd("dim", "rect", f"x = 0, y = 0, w = {W}, h = {H}, color = BG")
cells, hot = [], []
for i in range(PF["n"]):
    r_, c_ = divmod(i, 25)
    v = nd(f"pf_c{i}", "rect", f"x = {540 + c_ * 40}, y = {236 + r_ * 36}, w = 34, h = 26, rx = 3, color = PANEL")
    cells.append(v)
    if i in CHANGED:
        hot.append(v)
# text anchors are only "center" or top-left, so right-align by hand (mono 18 ~ 11 px/char)
rowlab = [nd(f"pf_r{r_}", "text", f'x = {528 - 11 * len(f"{r_}s")}, y = {239 + r_ * 36}, text = "{r_}s", size = 18,'
             f' font = MONO, color = FAINT') for r_ in range((PF["n"] + 24) // 25)]
verdict = [nd("pf_v", "text", f'x = 540, y = {236 + ((PF["n"] + 24) // 25) * 36 + 18},'
              f' text = "{len(CHANGED)} of {PF["n"]} frames changed", size = 40, font = BOLD, color = TEAL'),
           nd("pf_w", "text", f'x = 540, y = {236 + ((PF["n"] + 24) // 25) * 36 + 72},'
              f' text = "{len(wins)} windows:  {WIN_TXT}", size = 26, font = MONO, color = INK')]
chap["proof"] = header("proof", "06", "And checks its own work")

# 07 close
chap["close"] = [nd("cl_t", "text", f'x = {W // 2}, y = 468, text = "Cadence", size = 120, font = BOLD,'
                    f' color = INK, anchor = "center"'),
                 nd("cl_s", "text", f'x = {W // 2}, y = 574, text = "real pixels, measured into words, edited by assertion",'
                    f' size = 32, font = SANS, color = INK, anchor = "center"')]

# narration captions, over a scrim at the foot of the frame
cues = []
for m in NARR:
    for c in m["cues"]:
        cues.append((at[m["id"]] + c["t0"], at[m["id"]] + c["t1"], c["text"]))

# ------------------------------------------------------------------ the one timeline
clock = 0.0


def go(t):
    global clock
    assert t >= clock - 1e-6, (t, clock)
    if t - clock > 1e-4:
        S.append(f"      t:wait({t - clock:.3f})")
    clock = t


def fade_each(n, each):
    return min(each, 0.6 / max(1, n - 1))


def fade_len(n, d, each):
    """How long fade() takes -- the one formula, so a schedule can never disagree with it."""
    return (n - 1) * fade_each(n, each) + d if n else 0.0


def fade(nodes, to, d=0.45, each=0.03):
    global clock
    if not nodes:
        return
    each = fade_each(len(nodes), each)
    S.append(f"      fade(t, {{ {', '.join(nodes)} }}, {d}, {each:.4f}, {to})")
    clock += (len(nodes) - 1) * each + d


for cid in ids:
    a, b = win[cid]
    go(a)
    base = chap[cid]
    fade(base, 1)
    if cid == "edit":
        t0 = a
        for off, grp in beats:
            go(max(clock, t0 + off))          # a beat never starts before the last one finished
            fade(grp, 1)
        base = base + edit_nodes
    if cid == "proof":
        fade([dim], 0.82, d=0.6)
        fade(cells + rowlab, 1, d=0.4, each=0.002)
        go(max(clock, a + 5.0))
        S.append("      t:parallel(" + ", ".join(
            f'function() t:tween({v}, 0.5, {{ color = TEAL }}, "sineOut") end' for v in hot) + ")")
        clock += 0.5
        fade(verdict, 1)
        base = base + cells + rowlab + verdict
    if cid == "close":
        continue                                   # holds to the end
    out = fade_len(len(base), 0.3, 0.02)
    go(b - out)
    fade(base, 0, d=0.3, each=0.02)

# ------------------------------------------------------------------ write
w("-- Facts on real footage -- the tracker boxes the facts were derived from, the facts")
w("-- lighting up while they hold, and an edit landing on the picture.")
w("-- Generated by tools/make-agent-lesson.py from the perceived log, the cached SAM2 tracks")
w("-- and a live fact_edit proof. Do not hand-edit; regenerate.")
w('local e = require("ellua")')
w("")
w('local MONO = "evals/assets/fonts/JetBrainsMono-Regular.ttf"')
w('local SANS = "evals/assets/fonts/Roboto-Regular.ttf"')
w('local BOLD = "evals/assets/fonts/Roboto-Bold.ttf"')
w('local TEAL, AMBER, INK, FAINT = "#3ee0c6", "#f2b33d", "#eef2f8", "#a3aec0"')
w('local PANEL, BG = "#1b2432", "#0a0e14"')
w(f'local CLIP = "{CLIP}"')
w("local unpack = table.unpack or unpack")
w("")
w("return e.comp {")
w(f"  width = {W}, height = {H}, duration = {TOTAL}, fps = {FPS},")
w('  background = "#000000",')
w("  -- Acknowledged, not hidden: lint reports these as counts. Overlays are baked per frame,")
w("  -- which reads as dense motion; labels are mono annotation on a 1080p frame.")
w('  lint_allow = { "ease_monoculture", "motion_density", "competing_beats", "text_min_size",')
w('    "safe_area", "subpixel_jitter" },')
w("  scene = function(s)")
w("    local function fade(t, nodes, d, each, to)")
w("      local fns = {}")
w("      for i, n in ipairs(nodes) do")
w('        fns[i] = function() t:wait((i - 1) * each); t:tween(n, d, { opacity = to }, "sineOut") end')
w("      end")
w("      t:parallel(unpack(fns))")
w("    end")
w("    local N = {}")
w("")
for m in NARR:
    w(f'    s:audio {{ src = "{m["file"]}", at = {at[m["id"]]:.3f}, volume = 1.0,')
    w(f'      text = "{q(m["say"])}" }}')
L.extend(V)
w(f'    s:rect {{ x = 0, y = 930, w = {W}, h = 150, color = "#000000b3" }}')
w(f'    s:captions {{ x = {W // 2}, y = 978, size = 38, font = SANS, color = INK, anchor = "center", cues = {{')
for a, b, tx in cues:
    w(f'      {{ {a:.3f}, {b:.3f}, "{q(tx)}" }},')
w("    } }")
w("    s:script(function(t)")
L.extend(B)
L.extend(S)
w("    end)")
w("  end,")
w("}")
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text("\n".join(L) + "\n")
print(f"wrote {OUT.relative_to(ROOT)}: {len(L)} lines, {OUT.stat().st_size // 1024} KB, "
      f"{TOTAL}s, {len(shots)} shots, {NFR} samples/prop")
print(f"entities {ENT}  release {REL_T} ({REL.producer} {REL.conf})")
print(f"proof: {len(CHANGED)}/{PF['n']} frames, windows {WIN_TXT}")
