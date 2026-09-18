#!/usr/bin/env python3
"""Generate comps/lesson/how-cadence-works.lua.

Timings are baked rather than read at render time for two reasons: comp duration is
static (DESIGN.md s1.3), and TTS output length varies per call, so a comp that
synthesised at render time would drift out of sync with its own visuals on a re-render.
Narration comes from `bin/cadence tts --provider openrouter`; caption cues come from
word timings measured by vision/cadence_vision/audiofacts.align.
"""
import json
from pathlib import Path

N = json.loads(Path('comps/lesson/narration.json').read_text())
W, H, FPS = 1920, 1080, 30
LEAD, GAP, TAIL = 0.6, 0.8, 1.4
IN_D, IN_EACH, OUT_D, OUT_EACH = 0.5, 0.06, 0.32, 0.025

# absolute audio start per chapter
at, cur = {}, LEAD
for m in N:
    at[m['id']] = cur
    cur += m['dur'] + GAP
TOTAL = round(cur - GAP + TAIL, 3)

def q(x: str) -> str:
    """Escape for a Lua double-quoted literal -- the fact and code lines this comp
    displays contain quotes of their own."""
    return x.replace('\\', '\\\\').replace('"', '\\"')


L = []                                   # lua lines
def w(s=''): L.append(s)

w('-- How Cadence Works -- an educational composition, rendered by the system it explains.')
w('--')
w('-- Narration : bin/cadence tts --provider openrouter   (openai/gpt-audio-mini)')
w('-- Cue times : vision/cadence_vision/audiofacts.align  (word timings, frame-accurate)')
w('-- Generated : tools/make-lesson.py -- do not hand-edit, regenerate instead.')
w('--')
w('-- One script, not one per chapter. Every s:script runs, but they share a single')
w('-- recorder cursor, so a second one starts where the first ended rather than at zero.')
w('-- Keeping it to one timeline means the generator owns absolute time outright.')
w('local e = require("ellua")')
w('')
w('local MONO = "evals/assets/fonts/JetBrainsMono-Regular.ttf"')
w('local SANS = "evals/assets/fonts/Roboto-Regular.ttf"')
w('local BOLD = "evals/assets/fonts/Roboto-Bold.ttf"')
w('')
w('local TEAL, BLUE, AMBER, PINK = "#3ee0c6", "#4f8cff", "#f2b33d", "#e85aa8"')
w('local INK, DIM, FAINT, PANEL = "#e8edf7", "#8593a8", "#818ea6", "#141b26"')
w('')
w('local unpack = table.unpack or unpack')
w('')
w('return e.comp {')
w(f'  width = {W}, height = {H}, duration = {TOTAL}, fps = {FPS},')
w('  -- Acknowledged, not hidden: lint counts these and reports the count. A generated')
w('  -- lesson comp reuses one ease and keeps several things moving at once by construction.')
w('  lint_allow = { "ease_monoculture", "motion_density", "competing_beats",')
w('    -- 22-26px mono annotation at 1920x1080. The floor is tuned for captions, and')
w('    -- these are labels and code lines read on a large frame, not subtitles.')
w('    "text_min_size" },')
w('  background = "#0a0e14",')
w('')
w('  scene = function(s)')
w('    -- helpers ---------------------------------------------------------------')
w('    local function fade(t, nodes, d, each, to)')
w('      local fns = {}')
w('      for i, n in ipairs(nodes) do')
w('        fns[i] = function() t:wait((i - 1) * each); t:tween(n, d, { opacity = to }, "sineOut") end')
w('      end')
w('      t:parallel(unpack(fns))')
w('    end')
w('')
w('    -- narration: one clip per chapter, placed at its baked time ---------------')
for m in N:
    # `text` is the clip's own transcript. It costs nothing at render time and makes the
    # comp queryable by what is said: lift(want_words=True) aligns it into word facts.
    say = m['say'].replace('"', '\\"')
    w(f'    s:audio {{ src = "{m["file"]}", at = {at[m["id"]]:.3f}, volume = 1.0,')
    w(f'      text = "{say}" }}')
w('')

# ---------------------------------------------------------------- captions
cues = []
for m in N:
    for c in m['cues']:
        t0 = at[m['id']] + c['t0']
        t1 = at[m['id']] + c['t1']
        cues.append((t0, t1, c['text']))
w('    -- captions: word-aligned, so the line turns over when the voice does -----')
w(f'    s:captions {{ x = {W//2}, y = 946, size = 38, font = SANS, color = INK,')
w('      anchor = "center", cues = {')
for t0, t1, tx in cues:
    w(f'        {{ {t0:.3f}, {t1:.3f}, "{tx}" }},')
w('      } }')
w('')
Path('/tmp/_lesson_head.lua').write_text('\n'.join(L))
print('head lines:', len(L), 'total duration:', TOTAL, 'chapters:', len(N))
print('audio starts:', {k: round(v, 2) for k, v in at.items()})

# ---------------------------------------------------------------- chapters
CX = W // 2
# node, x-from, x-to for the accent that keeps each chapter in motion
SCAN = {'open': ('N.op_p', 240, 1620), 'fn': ('N.fn_s', 120, 424),
        'seek': ('N.sk_s', 120, 424), 'declare': ('N.dc_s', 120, 424),
        'det': ('N.dt_s', 120, 424), 'hash': ('N.hs_s', 120, 424),
        'lift': ('N.lf_s', 120, 424), 'perceive': ('N.pc_s', 120, 424),
        'lower': ('N.lw_s', 120, 424), 'why': ('N.wy_s', 120, 424),
        'close': ('N.cl_p', 770, 1150)}
V = []          # node-creation lua
A = []          # per-chapter (in_nodes, motion_lua, motion_dur, all_nodes)

_seen = set()

def nd(var, kind, props):
    # A duplicate name silently rebinds the table slot, so the node lands in a fade
    # list twice and the recorder rejects the overlapping tweens. Catch it here.
    assert var not in _seen, f"duplicate node name {var!r}"
    _seen.add(var)
    # One table, not 250 locals: a Lua function may declare at most 200.
    V.append(f'    N.{var} = s:{kind} {{ {props}, opacity = 0 }}')
    return f'N.{var}'

def cap(var, x, y, size, color, cues, font='MONO', anchor='center'):
    V.append(f'    s:captions {{ x = {x}, y = {y}, size = {size}, font = {font},   -- {var}')
    V.append(f'      color = {color}, anchor = "{anchor}", cues = {{')
    for t0, t1, tx in cues:
        V.append(f'        {{ {t0:.3f}, {t1:.3f}, "{q(tx)}" }},')
    V.append('      } }')
    return var

def header(tag, num, title):
    a = nd(f'{tag}_n', 'text', f'x = 120, y = 112, text = "{num}", size = 24, font = MONO, color = TEAL')
    b = nd(f'{tag}_t', 'text', f'x = 120, y = 150, text = "{title}", size = 50, font = BOLD, color = INK')
    c = nd(f'{tag}_r', 'rect', 'x = 120, y = 228, w = 380, h = 3, color = PANEL')
    d = nd(f'{tag}_s', 'rect', 'x = 120, y = 227, w = 76, h = 5, rx = 2, color = TEAL')
    return [a, b, c, d]

def chapter(cid, build):
    m = next(x for x in N if x['id'] == cid)
    V.append('')
    V.append(f'    -- {cid} ' + '-' * (68 - len(cid)))
    ins, motion, mdur, extra = build(m, at[cid])
    scan = SCAN.get(cid, (f'N.{cid[:2]}_s', 120, 424))
    A.append(dict(id=cid, at=at[cid], dur=m['dur'], ins=ins, motion=motion,
                  mdur=mdur, all=ins + (extra or []), scan=scan))

# 00 -- open ------------------------------------------------------------------
def b_open(m, t0):
    ti = nd('op_t', 'text', f'x = {CX}, y = 432, text = "How Cadence Works", size = 104, font = BOLD, color = INK, anchor = "center"')
    su = nd('op_s', 'text', f'x = {CX}, y = 520, text = "made by the system it describes", size = 34, font = SANS, color = DIM, anchor = "center"')
    cells = []
    for i in range(24):
        cells.append(nd(f'op_c{i}', 'rect', f'x = {240 + i * 60}, y = 640, w = 52, h = 34, rx = 3, color = PANEL'))
    ph = nd('op_p', 'rect', 'x = 240, y = 640, w = 52, h = 34, rx = 3, color = TEAL')
    return [ti, su] + cells + [ph], [], 0.0, []
chapter('open', b_open)

# 01 -- fn --------------------------------------------------------------------
def b_fn(m, t0):
    h = header('fn', '01', 'A composition is a function')
    pl = nd('fn_pl', 'rect', 'x = 180, y = 392, w = 520, h = 264, rx = 10, color = PANEL')
    ll = nd('fn_ll', 'text', 'x = 214, y = 424, text = "time", size = 22, font = MONO, color = FAINT')
    ar = nd('fn_ar', 'rect', 'x = 736, y = 520, w = 150, h = 4, color = FAINT')
    ah = nd('fn_ah', 'circle', 'x = 898, y = 522, r = 8, color = FAINT')
    pr = nd('fn_pr', 'rect', 'x = 940, y = 352, w = 800, h = 344, rx = 10, color = PANEL')
    lr = nd('fn_lr', 'text', 'x = 974, y = 384, text = "frame", size = 22, font = MONO, color = FAINT')
    dot = nd('fn_d', 'circle', 'x = 1050, y = 560, r = 44, color = TEAL')
    cc = nd('fn_c', 'text', f'x = {CX}, y = 740, text = "the frame is computed, never stored", size = 28, font = SANS, color = DIM, anchor = "center"')
    # readout: counts with the dot, so the two are visibly one function
    ms, md = t0 + 5.4, 6.4
    cues = []
    for i in range(32):
        a = ms + i * (md / 32)
        cues.append((a, a + md / 32, f"t = {i * (6.0 / 32):.2f}"))
    cap('fn_ro', 440, 540, 58, 'TEAL', cues)
    mot = ['      t:wait(%.3f)' % 1.2,
           '      t:tween(N.fn_d, 6.4, { x = 1630 }, "linear")']
    return h + [pl, ll, ar, ah, pr, lr, dot, cc], mot, 1.2 + 6.4, []
chapter('fn', b_fn)

# 02 -- seek ------------------------------------------------------------------
def b_seek(m, t0):
    h = header('sk', '02', 'Seek, not playback')
    cells = [nd(f'sk_c{i}', 'rect', f'x = {168 + i * 44}, y = 462, w = 38, h = 58, rx = 3, color = PANEL') for i in range(36)]
    ph = nd('sk_p', 'rect', 'x = 168, y = 462, w = 38, h = 58, rx = 3, color = TEAL')
    lb = nd('sk_l', 'text', f'x = {CX}, y = 700, text = "any frame, any order, same answer", size = 28, font = SANS, color = DIM, anchor = "center"')
    order = [12, 31, 4, 22, 9, 35, 17]
    ms, mot, cues, clk = t0 + 2.2, [], [], 0.0
    mot.append('      t:wait(2.200)')
    for k in order:
        mot.append(f'      t:tween(N.sk_p, 0.14, {{ x = {168 + k * 44} }}, "expoOut")')
        mot.append('      t:wait(0.720)')
        cues.append((ms + clk, ms + clk + 0.86, f"frame {k}"))
        clk += 0.86
    cap('sk_ro', CX, 600, 42, 'TEAL', cues)
    return h + cells + [ph, lb], mot, 2.2 + len(order) * 0.86, []
chapter('seek', b_seek)

# 03 -- declare ---------------------------------------------------------------
def b_declare(m, t0):
    h = header('dc', '03', "Describe, don't draw")
    code = ['s:rect   { x = 1180, y = 392, w = 300, h = 170 }',
            's:circle { x = 1610, y = 478, r = 84 }',
            's:text   { text = "TYPE", size = 64 }',
            't:tween(box, 0.9, { x = 1060 })']
    cl = [nd(f'dc_l{i}', 'text', f'x = 180, y = {404 + i * 52}, text = "{q(c)}", size = 25, font = MONO, color = DIM')
          for i, c in enumerate(code)]
    box = nd('dc_b', 'rect', 'x = 1180, y = 392, w = 300, h = 170, rx = 8, color = BLUE')
    dsc = nd('dc_d', 'circle', 'x = 1610, y = 478, r = 84, color = TEAL')
    typ = nd('dc_y', 'text', 'x = 1180, y = 610, text = "TYPE", size = 64, font = BOLD, color = INK')
    lb = nd('dc_c', 'text', f'x = 180, y = 700, text = "the scene graph is data, evaluated fresh", size = 26, font = SANS, color = FAINT')
    mot = ['      t:wait(2.400)', '      t:tween(N.dc_b, 0.9, { x = 1060 }, "cubicInOut")']
    return h + cl + [box, dsc, typ, lb], mot, 3.3, []
chapter('declare', b_declare)

# 04 -- det -------------------------------------------------------------------
def b_det(m, t0):
    h = header('dt', '04', 'Determinism by construction')
    ns = []
    for k, (ox, nm) in enumerate(((240, 'render 1'), (1080, 'render 2'))):
        ns.append(nd(f'dt_p{k}', 'rect', f'x = {ox}, y = 372, w = 600, h = 300, rx = 10, color = PANEL'))
        ns.append(nd(f'dt_h{k}', 'text', f'x = {ox + 32}, y = 402, text = "{nm}", size = 22, font = MONO, color = FAINT'))
        ns.append(nd(f'dt_c{k}', 'circle', f'x = {ox + 170}, y = 530, r = 54, color = TEAL'))
        ns.append(nd(f'dt_r{k}', 'rect', f'x = {ox + 300}, y = 486, w = 180, h = 92, rx = 8, color = BLUE'))
        ns.append(nd(f'dt_s{k}', 'text', f'x = {ox + 300}, y = 712, text = "15ca0cde58d07a47", size = 26, font = MONO, color = AMBER, anchor = "center"'))
    vd = nd('dt_v', 'text', f'x = {CX}, y = 790, text = "same bytes, every time", size = 34, font = BOLD, color = TEAL, anchor = "center"')
    lb = nd('dt_l', 'text', f'x = {CX}, y = 848, text = "clock stubbed  ·  seed fixed  ·  no I/O while drawing", size = 24, font = SANS, color = FAINT, anchor = "center"')
    return h + ns + [vd, lb], [], 0.0, []
chapter('det', b_det)

# 05 -- hash ------------------------------------------------------------------
DIFF = set(range(17, 26))            # measured: frames 17..25 of 108, see docs/FACTS.md
def b_hash(m, t0):
    h = header('hs', '05', 'A hash is a test')
    cells, hot = [], []
    for i in range(108):
        r, c = divmod(i, 30)
        v = nd(f'hs_c{i}', 'rect', f'x = {180 + c * 52}, y = {420 + r * 34}, w = 46, h = 20, rx = 2, color = PANEL')
        cells.append(v)
        if i in DIFF:
            hot.append(v)
    lb = nd('hs_l', 'text', 'x = 180, y = 382, text = "108 frames  ·  one caption moved 0.3s later", size = 24, font = MONO, color = FAINT')
    vd = nd('hs_v', 'text', f'x = 180, y = 596, text = "9 differ   99 identical", size = 40, font = BOLD, color = TEAL')
    mot = ['      t:wait(4.200)',
           '      t:parallel(' + ', '.join(f'function() t:tween({v}, 0.55, {{ color = TEAL }}, "sineOut") end' for v in hot) + ')']
    return h + cells + [lb], mot, 4.75, [vd]
chapter('hash', b_hash)

# 06 -- lift ------------------------------------------------------------------
def b_lift(m, t0):
    h = header('lf', '06', 'The comp describes itself')
    pn = nd('lf_p', 'rect', 'x = 180, y = 372, w = 600, h = 368, rx = 10, color = PANEL')
    bx = nd('lf_b', 'rect', 'x = 232, y = 432, w = 210, h = 116, rx = 6, color = BLUE')
    dc = nd('lf_d', 'circle', 'x = 636, y = 556, r = 58, color = TEAL')
    tx = nd('lf_type', 'text', 'x = 236, y = 596, text = "TYPE", size = 44, font = BOLD, color = INK')
    ar = nd('lf_a', 'rect', 'x = 820, y = 552, w = 110, h = 4, color = FAINT')
    facts = ['entity(text6, "text").',
             'holds(visible(text6), 0.000, 3.600).',
             'holds(in_third(text6, left), 0.000, 3.600).',
             'holds(text(text6, "Hold the cut."), 0.567, 1.467).',
             'happens(text_change(text6), 0.567).']
    fl = [nd(f'lf_f{i}', 'text', f'x = 976, y = {404 + i * 46}, text = "{q(f)}", size = 25, font = MONO, color = INK')
          for i, f in enumerate(facts)]
    nt = nd('lf_note', 'text', 'x = 976, y = 664, text = "no src line -- these are exact", size = 26, font = MONO, color = TEAL')
    return h + [pn, bx, dc, tx, ar] + fl + [nt], [], 0.0, []
chapter('lift', b_lift)

# 07 -- perceive --------------------------------------------------------------
def b_perceive(m, t0):
    h = header('pc', '07', 'The same grammar, from footage')
    pn = nd('pc_p', 'rect', 'x = 180, y = 372, w = 600, h = 368, rx = 10, color = PANEL')
    fr = nd('pc_f', 'rect', 'x = 212, y = 404, w = 536, h = 304, rx = 6, color = "#1d2735"')
    # a tracked box, drawn as four thin edges so it reads as an outline
    bx = [nd('pc_b0', 'rect', 'x = 330, y = 452, w = 220, h = 3, color = AMBER'),
          nd('pc_b1', 'rect', 'x = 330, y = 646, w = 220, h = 3, color = AMBER'),
          nd('pc_b2', 'rect', 'x = 330, y = 452, w = 3, h = 197, color = AMBER'),
          nd('pc_b3', 'rect', 'x = 547, y = 452, w = 3, h = 197, color = AMBER')]
    lb = nd('pc_e', 'text', 'x = 330, y = 418, text = "e1", size = 24, font = MONO, color = AMBER')
    rows = [('entity(e1, "beer bottle", seed(8.520)).', 'src(.., detector_agreement, 0.71).'),
            ('holds(visible(e1), 2.100, 10.800).', 'src(.., sam2, 0.71).'),
            ('happens(release(e1, e2), 8.500).', 'src(.., contact_gap, 1.00).')]
    fl = []
    for i, (f, sv) in enumerate(rows):
        y = 404 + i * 96
        fl.append(nd(f'pc_f{i}', 'text', f'x = 900, y = {y}, text = "{q(f)}", size = 25, font = MONO, color = INK'))
        fl.append(nd(f'pc_s{i}', 'text', f'x = 928, y = {y + 40}, text = "{q(sv)}", size = 21, font = MONO, color = FAINT'))
    nt = nd('pc_note', 'text', 'x = 900, y = 700, text = "measured, so every line names its source", size = 26, font = MONO, color = AMBER')
    return h + [pn, fr] + bx + [lb] + fl + [nt], [], 0.0, []
chapter('perceive', b_perceive)

# 08 -- lower -----------------------------------------------------------------
def b_lower(m, t0):
    h = header('lw', '08', 'Editing by assertion')
    a1 = nd('lw_a1', 'rect', f'x = {CX - 2}, y = 462, w = 4, h = 52, color = FAINT')
    a2 = nd('lw_a2', 'rect', f'x = {CX - 2}, y = 596, w = 4, h = 52, color = FAINT')
    k1 = nd('lw_k1', 'text', f'x = {CX + 24}, y = 472, text = "lower()", size = 22, font = MONO, color = FAINT')
    k2 = nd('lw_k2', 'text', f'x = {CX + 24}, y = 606, text = "render + hash", size = 22, font = MONO, color = FAINT')
    cells, hot = [], []
    for i in range(36):
        v = nd(f'lw_c{i}', 'rect', f'x = {642 + i * 18}, y = 684, w = 14, h = 34, rx = 2, color = PANEL')
        cells.append(v)
        if 5 <= i < 14:
            hot.append(v)
    vd = nd('lw_v', 'text', f'x = {CX}, y = 760, text = "9 frames changed   99 untouched", size = 30, font = BOLD, color = TEAL, anchor = "center"')
    # the two lines that change, driven by cues so the edit lands on the word
    sw = t0 + 5.1
    cap('lw_r1', CX, 418, 30, 'INK',
        [(t0 - 0.2, sw, 'holds(text(text6, "Hold the cut."), 0.567, 1.467).'),
         (sw, t0 + m['dur'] + 0.45, 'holds(text(text6, "Hold the cut."), 0.867, 1.467).')])
    cap('lw_r2', CX, 552, 30, 'AMBER',
        [(t0 - 0.2, sw, '{ 0.55, 1.45, "Hold the cut." }'),
         (sw, t0 + m['dur'] + 0.45, '{ 0.85, 1.45, "Hold the cut." }')])
    mot = ['      t:wait(5.500)',
           '      t:parallel(' + ', '.join(f'function() t:tween({v}, 0.45, {{ color = TEAL }}, "sineOut") end' for v in hot) + ')']
    return h + [a1, a2, k1, k2] + cells, mot, 5.95, [vd]
chapter('lower', b_lower)

# 09 -- why -------------------------------------------------------------------
def b_why(m, t0):
    h = header('wy', '09', 'Why it is built this way')
    dv = nd('wy_d', 'rect', f'x = {CX - 1}, y = 372, w = 3, h = 340, color = PANEL')
    ns = []
    cols = [(200, 'FROM PIXELS', FAINT_ := 'FAINT', 'DIM',
             ['describe the frame', 'guess a timestamp', 'hope it landed'], 'tIoU 0.00 - 0.11', 'AMBER'),
            (1030, 'FROM FACTS', 'TEAL', 'INK',
             ['the node has an id', 'the fact carries the time', 'hashes prove the edit'], 'exact', 'TEAL')]
    for k, (x, head, hc, bc, lines, metric, mc) in enumerate(cols):
        ns.append(nd(f'wy_h{k}', 'text', f'x = {x}, y = 392, text = "{head}", size = 26, font = MONO, color = {hc}'))
        for j, ln in enumerate(lines):
            ns.append(nd(f'wy_l{k}{j}', 'text', f'x = {x}, y = {462 + j * 54}, text = "{ln}", size = 30, font = SANS, color = {bc}'))
        ns.append(nd(f'wy_m{k}', 'text', f'x = {x}, y = 648, text = "{metric}", size = 36, font = BOLD, color = {mc}'))
    return h + [dv] + ns, [], 0.0, []
chapter('why', b_why)

# 10 -- close -----------------------------------------------------------------
def b_close(m, t0):
    ti = nd('cl_t', 'text', f'x = {CX}, y = 468, text = "Cadence", size = 128, font = BOLD, color = INK, anchor = "center"')
    su = nd('cl_s', 'text', f'x = {CX}, y = 576, text = "Programmatic video from Lua.", size = 36, font = SANS, color = DIM, anchor = "center"')
    rl = nd('cl_r', 'rect', f'x = {CX - 190}, y = 632, w = 380, h = 3, color = PANEL')
    ph = nd('cl_p', 'rect', f'x = 770, y = 631, w = 76, h = 5, rx = 2, color = TEAL')
    return [ti, su, rl, ph], [], 0.0, []
chapter('close', b_close)

# ---------------------------------------------------------------- assemble
L.append('    -- nodes ------------------------------------------------------------------')
L.append('    local N = {}')
L.append('    -- Runs the whole comp, inside the parallel below. Without continuous motion')
L.append('    -- a chapter that fades in and holds is a frozen frame, which lint rejects.')
L.append(f'    N.rail_bg = s:rect {{ x = 120, y = 1032, w = 1680, h = 3, color = PANEL }}')
L.append(f'    N.rail = s:rect {{ x = 120, y = 1032, w = 6, h = 3, color = TEAL }}')
L.extend(V)
w('')
w('    -- one timeline; every wait below is derived from the narration timings ---')
w('    s:script(function(t)')
w('      t:parallel(function()')
w(f'        t:tween(N.rail, {TOTAL - 0.1:.3f}, {{ w = 1680 }}, "linear")')
w('      end, function()')
clock = 0.0
for c in A:
    vs = c['at'] - 0.35
    ve = c['at'] + c['dur'] + 0.45
    w(f"        -- {c['id']}: narration {c['at']:.2f}s .. {c['at'] + c['dur']:.2f}s")
    w(f'        t:wait({vs - clock:.3f})'); clock = vs
    ie = min(IN_EACH, 0.9 / max(1, len(c['ins']) - 1))
    w(f"        fade(t, {{ {', '.join(c['ins'])} }}, {IN_D}, {ie:.4f}, 1)")
    clock += (len(c['ins']) - 1) * ie + IN_D
    oe = min(OUT_EACH, 0.5 / max(1, len(c['all']) - 1))
    out_dur = (len(c['all']) - 1) * oe + OUT_D
    active = (c['at'] + c['dur'] + 0.45) - out_dur - clock
    assert active > 0, f"{c['id']} overruns by {-active:.2f}s"
    late = [x for x in c['all'] if x not in c['ins']]
    late_dur = ((len(late) - 1) * 0.04 + 0.45) if late else 0.0
    # branch A: the accent scans for the whole active window. It began as a way to
    # satisfy frozen_span, which used to call a narrated hold a static frame; that rule
    # now understands narration, so this stays because it reads well, not because it must.
    # branch B: the chapter's own motion, then the hold.
    w('        t:parallel(function()')
    node, x0, x1 = c['scan']
    rem, out = active, True
    while rem >= 1.2:
        w(f'          t:tween({node}, 1.2, {{ x = {x1 if out else x0} }}, "sineInOut")')
        rem -= 1.2
        out = not out
    if rem > 0.001:
        w(f'          t:wait({rem:.3f})')
    w('        end, function()')
    for line in c['motion']:
        w('  ' + line.strip().rjust(len(line.strip()) + 10))
    if late:
        w(f"          fade(t, {{ {', '.join(late)} }}, 0.45, 0.04, 1)")
    tail_wait = active - c['mdur'] - late_dur
    assert tail_wait > -1e-6, f"{c['id']} motion overruns by {-tail_wait:.2f}s"
    w(f'          t:wait({tail_wait:.3f})')
    w('        end)')
    clock += active
    w(f"        fade(t, {{ {', '.join(c['all'])} }}, {OUT_D}, {oe:.4f}, 0)")
    clock += out_dur
w('      end)')
w('    end)')
w('  end,')
w('}')

out = Path('comps/lesson/how-cadence-works.lua')
out.write_text('\n'.join(L) + '\n')
print('wrote', out, len(L), 'lines,', out.stat().st_size, 'bytes; script ends at %.2fs of %.2fs' % (clock, TOTAL))
