"""Does a view help a model answer correctly? Questions are generated from
comp state (ground truth is exact), each is asked under several view
conditions to several models through OpenRouter, and the report is an
accuracy table model × condition.

  bin/cadence-vision eval [--models a,b] [--conditions x,y] [--comps c1,c2] [--limit N] [--run NAME]

Question kinds
  leftmost   which of these elements is leftmost at t (multiple choice)
  count      how many visible elements at t (number)
  appears    when does a text first become visible (seconds, ±0.45 s)
  direction  does an element move left/right/up/down between t0 and t1
  flat       is the frame flat 2D graphics or does it show depth (yes/no)
  nearer     hand-written: which of two elements is nearer the camera

Conditions
  raw        the frame
  annotated  frame with numbered marks + legend text
  text       scene_text only (no pixels)
  frame_text frame + scene_text
  sheet      6-frame labeled contact sheet (time questions)
  pair       two labeled frames (motion questions)
  depth      RGB | depth side-by-side (spatial questions)
  video      native clip at 2 fps (Gemini only)
"""

from __future__ import annotations

import base64
import concurrent.futures as cf
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

from . import ROOT, CACHE
from . import annotate as AN, sheet as SH, scene_text as ST
from .sources import open_source, frame_at, fit, mmss

OUT = ROOT / "vision" / "eval" / "out"
MODELS = {
    "claude": "anthropic/claude-sonnet-5",
    "gemini": "google/gemini-3.8-flash",
    "gpt": "openai/gpt-5.4",
    "qwen": "qwen/qwen3-vl-235b-a22b-instruct",
}
COMPS = ["primitives", "camera", "image", "bounce", "motion", "blend_modes", "pulse", "captions", "type"]
LONG_EDGE = 1280

# hand-written spatial questions (ground truth by construction of the comp)
HAND = [
    {"comp": "camera", "t": 1.5, "kind": "nearer", "q": "Two rounded rectangles are shown in perspective. Which one is nearer to the camera: A) the blue one on the left, B) the teal one on the right? Answer with the letter only.", "answer": "B", "type": "letter"},
    {"comp": "camera", "t": 1.5, "kind": "flat", "q": "Is this frame flat 2D graphics with no depth, or does it show elements at different depths? Answer FLAT or DEPTH only.", "answer": "DEPTH", "type": "word"},
    {"comp": "blend_modes", "t": 1.0, "kind": "flat", "q": "Is this frame flat 2D graphics with no depth, or does it show elements at different depths? Answer FLAT or DEPTH only.", "answer": "FLAT", "type": "word"},
    {"comp": "image", "t": 1.5, "kind": "flat", "q": "Is this frame flat 2D graphics with no depth, or does it show a photographed object with depth? Answer FLAT or DEPTH only.", "answer": "DEPTH", "type": "word"},
]


# ----------------------------------------------------------------------------- descriptions

def color_name(rgb) -> str:
    if not rgb or len(rgb) < 3:
        return ""
    r, g, b = [float(c) for c in rgb[:3]]
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 0.2:
        return "black"
    if mx - mn < 0.12:
        return "white" if mx > 0.75 else "grey"
    h = 0.0
    if mx == r:
        h = (60 * ((g - b) / (mx - mn)) + 360) % 360
    elif mx == g:
        h = 60 * ((b - r) / (mx - mn)) + 120
    else:
        h = 60 * ((r - g) / (mx - mn)) + 240
    for lim, name in ((15, "red"), (45, "orange"), (70, "yellow"), (160, "green"), (200, "teal"), (260, "blue"), (300, "purple"), (335, "pink"), (361, "red")):
        if h < lim:
            return name
    return "coloured"


def describe(m: dict) -> str:
    k = m["kind"]
    if k == "text":
        t = str(m.get("text", "")).strip().replace("\n", " ")
        return f'the text "{t[:24]}"'
    c = color_name(m.get("color"))
    if k == "circle":
        return f"the {c} circle"
    if k == "rect":
        return f"the {c} rectangle"
    if k == "image":
        return "the photo"
    if k == "video":
        return "the video clip"
    return f"the {c} {k}"


def content_marks(src, t: float, W: int, H: int, slate: bool = False) -> list[dict]:
    """Visible content nodes: drop full-frame backgrounds and (unless slate=True) the small top-left slate label."""
    marks, _ = AN.comp_marks(src, t)
    keep = []
    for m in marks:
        if m["w"] >= 0.85 * W and m["h"] >= 0.85 * H:
            continue
        if m["kind"] not in ("text", "circle", "rect", "image", "video"):
            continue
        if not slate and m["kind"] == "text" and m["y"] < 0.08 * H and m["h"] < 0.06 * H:
            continue          # the slate label every eval case carries top-left
        if m["kind"] == "text" and len(str(m.get("text", "")).strip()) < 3:
            continue
        keep.append(m)
    return keep


# ----------------------------------------------------------------------------- question generation

def gen_questions(comps: list[str]) -> list[dict]:
    qs: list[dict] = []
    for name in comps:
        comp = ROOT / "evals" / "cases" / f"{name}.lua"
        if not comp.exists():
            continue
        try:
            src = open_source(comp)
        except Exception as e:  # noqa: BLE001
            print(f"eval: skipping {name}: {str(e).splitlines()[0][:120]}", file=sys.stderr)
            continue
        W, H = src.width, src.height
        dur = src.duration
        tm = round(dur * 0.55, 2)
        marks = content_marks(src, tm, W, H)
        descs = [describe(m) for m in marks]
        if len(marks) >= 3 and len(set(descs)) == len(descs):
            order = sorted(range(len(marks)), key=lambda i: marks[i]["x"] + marks[i]["w"] / 2)
            left = order[0]
            gap = (marks[order[1]]["x"] + marks[order[1]]["w"] / 2) - (marks[left]["x"] + marks[left]["w"] / 2)
            if gap > 0.06 * W:
                opts = [(chr(65 + i), descs[i]) for i in range(min(4, len(marks)))]
                if any(l == chr(65 + left) for l, _ in opts):
                    qs.append({"comp": name, "t": tm, "kind": "leftmost",
                               "q": "Which of these elements is the leftmost (its centre is furthest left)? " + " ".join(f"{l}) {d}" for l, d in opts) + " Answer with the letter only.",
                               "answer": chr(65 + left), "type": "letter", "marks": marks})
        # count: everything visible including the slate label (the model sees it, so it counts)
        all_marks = content_marks(src, tm, W, H, slate=True)
        if 2 <= len(all_marks) <= 6:
            qs.append({"comp": name, "t": tm, "kind": "count",
                       "q": "How many distinct visible elements are there (count each shape, each text block including small labels, and each picture once; ignore the plain background)? Answer with a number only.",
                       "answer": str(len(all_marks)), "type": "number", "marks": all_marks})
        # appears: text that is invisible at t=0 and later fully on
        ts = [round(x * 0.1, 1) for x in range(int(dur * 10) + 1)]
        d = AN.props(comp, ts)
        for n in d["nodes"]:
            if n["kind"] != "text":
                continue
            ops = [float(d["at"][f"{t:.4f}"].get(n["id"], {}).get("opacity", 0) or 0) for t in ts]
            if ops[0] < 0.1 and max(ops) > 0.9 and len(str(d["at"][f"{ts[-1]:.4f}"][n["id"]].get("text", "")).strip()) >= 3:
                t_on = ts[next(i for i, o in enumerate(ops) if o > 0.5)]
                if 0.3 < t_on < dur - 0.3:
                    txt = str(d["at"][f"{ts[-1]:.4f}"][n["id"]].get("text", "")).strip()[:24]
                    qs.append({"comp": name, "t": t_on, "kind": "appears",
                               "q": f'At what time in seconds does the text "{txt}" first become clearly visible? Answer with a number in seconds only (e.g. 1.2).',
                               "answer": t_on, "type": "seconds", "tol": 0.45})
                    break
        # direction: an element whose centre moves > 60 px between 25% and 75%
        t0, t1 = round(dur * 0.25, 2), round(dur * 0.75, 2)
        m0 = {m["id"]: m for m in content_marks(src, t0, W, H)}
        m1 = {m["id"]: m for m in content_marks(src, t1, W, H)}
        for nid in m0:
            if nid not in m1:
                continue
            dx = (m1[nid]["x"] + m1[nid]["w"] / 2) - (m0[nid]["x"] + m0[nid]["w"] / 2)
            dy = (m1[nid]["y"] + m1[nid]["h"] / 2) - (m0[nid]["y"] + m0[nid]["h"] / 2)
            if max(abs(dx), abs(dy)) > 60 and abs(abs(dx) - abs(dy)) > 40:
                horiz = abs(dx) > abs(dy)
                ans = ("RIGHT" if dx > 0 else "LEFT") if horiz else ("DOWN" if dy > 0 else "UP")
                axis = "LEFT or RIGHT" if horiz else "UP or DOWN"
                qs.append({"comp": name, "t": t0, "t1": t1, "kind": "direction",
                           "q": f"Between the first and second frame, does {describe(m0[nid])} move {axis}? Answer with one word only.",
                           "answer": ans, "type": "word"})
                break
    qs += [dict(h) for h in HAND]
    for i, q in enumerate(qs):
        q["id"] = f"{q['comp']}-{q['kind']}-{i}"
    return qs


# ----------------------------------------------------------------------------- views per condition

CONDITIONS_FOR = {
    "leftmost": ["raw", "annotated", "text", "frame_text"],
    "count": ["raw", "annotated", "text", "frame_text"],
    "appears": ["sheet", "video"],
    "direction": ["pair", "video"],
    "flat": ["raw", "depth"],
    "nearer": ["raw", "depth", "frame_text"],
}


def _b64(im: Image.Image) -> str:
    buf = io.BytesIO(); fit(im, LONG_EDGE).save(buf, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_view(q: dict, cond: str) -> list[dict] | None:
    """OpenAI-style content parts for the question under a condition, or None if not applicable."""
    src = open_source(ROOT / "evals" / "cases" / f"{q['comp']}.lua")
    t = q["t"]
    parts: list[dict] = []
    if cond == "raw":
        parts.append({"type": "image_url", "image_url": {"url": _b64(Image.fromarray(frame_at(src, t)))}})
    elif cond == "annotated":
        marks, _ = AN.comp_marks(src, t)
        im = AN.draw_marks(Image.fromarray(frame_at(src, t)), marks, "som", 1.0)
        parts.append({"type": "text", "text": "Legend for the numbered marks: " + json.dumps(AN.legend(marks))})
        parts.append({"type": "image_url", "image_url": {"url": _b64(im)}})
    elif cond == "text":
        parts.append({"type": "text", "text": "Scene description (exact data from the renderer): " + json.dumps(ST.comp_nodes_text(src, t))})
    elif cond == "frame_text":
        parts.append({"type": "text", "text": "Scene description (exact data from the renderer): " + json.dumps(ST.comp_nodes_text(src, t))})
        parts.append({"type": "image_url", "image_url": {"url": _b64(Image.fromarray(frame_at(src, t)))}})
    elif cond == "sheet":
        ts = [round(src.duration * (i + 0.5) / 6, 2) for i in range(6)]
        im = SH.from_source(src, ts, 3, "seconds", LONG_EDGE)
        parts.append({"type": "text", "text": "Six frames from the clip, labeled with their time in seconds (t=)."})
        parts.append({"type": "image_url", "image_url": {"url": _b64(im)}})
    elif cond == "pair":
        parts.append({"type": "text", "text": f"First frame (t={q['t']}s):"})
        parts.append({"type": "image_url", "image_url": {"url": _b64(Image.fromarray(frame_at(src, q["t"])))}})
        parts.append({"type": "text", "text": f"Second frame (t={q['t1']}s):"})
        parts.append({"type": "image_url", "image_url": {"url": _b64(Image.fromarray(frame_at(src, q["t1"])))}})
    elif cond == "depth":
        from . import depth as DP
        fr = frame_at(src, t, 1024)
        res = DP.mono(fr)
        im = DP.side_by_side(fr, DP.depth_image(res, "turbo"))
        parts.append({"type": "text", "text": "Left: the frame. Right: a depth map from a monocular depth model (turbo colormap: warm/red = near the camera, cool/blue = far). Depth stats: " + json.dumps(res["stats"])})
        parts.append({"type": "image_url", "image_url": {"url": _b64(im)}})
    elif cond == "video":
        from . import native as NV
        from .profiles import get_profile
        info = NV.prepare(src, get_profile("gemini"), 0.0, src.duration, 2.0, 720, True, 20.0)
        if "data_url" not in info:
            return None
        parts.append({"type": "text", "text": "The clip is attached (sampled at 2 frames per second, starting at t=0)."})
        parts.append({"type": "video_url", "video_url": {"url": info["data_url"]}})
    else:
        return None
    parts.append({"type": "text", "text": q["q"]})
    return parts


# ----------------------------------------------------------------------------- calling models

def call(model: str, parts: list[dict], cache_key: str) -> dict:
    cp = CACHE / "eval" / f"{cache_key}.json"
    cp.parent.mkdir(exist_ok=True)
    if cp.exists():
        return json.loads(cp.read_text())
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY not set")
    body = {"model": model, "messages": [{"role": "user", "content": parts}], "max_tokens": 400, "temperature": 0,
            "reasoning": {"effort": "low"}}
    req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode(),
                                 headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json",
                                          "HTTP-Referer": "https://github.com/shinyobjectz/cadence", "X-Title": "cadence-vision eval"})
    t0 = time.time()
    last = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                d = json.loads(r.read())
            break
        except Exception as e:  # noqa: BLE001
            last = e; time.sleep(2 + 3 * attempt)
    else:
        return {"text": "", "error": str(last)[:200], "secs": time.time() - t0}
    txt = ""
    try:
        txt = d["choices"][0]["message"]["content"] or ""
    except Exception:  # noqa: BLE001
        txt = ""
    out = {"text": txt, "usage": d.get("usage"), "secs": round(time.time() - t0, 2), "error": d.get("error")}
    if txt.strip():
        cp.write_text(json.dumps(out))
    return out


def grade(q: dict, text: str) -> bool:
    lines = [l for l in (text or "").strip().splitlines() if l.strip()]
    s = (lines[-1] if lines else "").strip().upper()      # the answer is asked for alone; take the last line
    if q["type"] == "letter":
        m = re.search(r"\b([A-D])\b", s)
        return bool(m) and m.group(1) == q["answer"]
    if q["type"] == "word":
        return q["answer"] in re.findall(r"[A-Z]+", s)
    if q["type"] == "number":
        m = re.search(r"\d+", s)
        return bool(m) and m.group(0) == q["answer"]
    if q["type"] == "seconds":
        m = re.search(r"\d+(?:\.\d+)?", s)
        return bool(m) and abs(float(m.group(0)) - float(q["answer"])) <= q.get("tol", 0.45)
    return False


# ----------------------------------------------------------------------------- run + report

def run(models: list[str], conditions: list[str] | None, comps: list[str], limit: int, run_name: str, workers: int = 6) -> Path:
    qs = gen_questions(comps)
    if limit:
        qs = qs[:limit]
    jobs = []
    for q in qs:
        for cond in CONDITIONS_FOR[q["kind"]]:
            if conditions and cond not in conditions:
                continue
            for mk in models:
                if cond == "video" and "gemini" not in MODELS.get(mk, mk):
                    continue
                jobs.append((q, cond, mk))
    print(f"eval: {len(qs)} questions, {len(jobs)} calls", file=sys.stderr)
    views: dict[tuple, list | None] = {}
    for q, cond, _ in jobs:
        k = (q["id"], cond)
        if k not in views:
            views[k] = build_view(q, cond)
    results = []

    def one(job):
        q, cond, mk = job
        parts = views[(q["id"], cond)]
        if parts is None:
            return None
        model = MODELS.get(mk, mk)
        key = hashlib.sha1(json.dumps([model, parts], sort_keys=True).encode()).hexdigest()[:20]
        r = call(model, parts, key)
        ok = grade(q, r.get("text", ""))
        return {"id": q["id"], "comp": q["comp"], "kind": q["kind"], "condition": cond, "model": mk, "answer": q["answer"],
                "reply": (r.get("text") or "")[:80], "correct": ok, "error": r.get("error"), "secs": r.get("secs"),
                "tokens": (r.get("usage") or {}).get("prompt_tokens")}

    with cf.ThreadPoolExecutor(workers) as ex:
        for res in ex.map(one, jobs):
            if res:
                results.append(res)
                print(f"{'ok ' if res['correct'] else 'BAD'} {res['model']:7s} {res['condition']:10s} {res['id']:28s} -> {res['reply']!r}", file=sys.stderr)
    out = OUT / run_name
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps({"questions": [{k: v for k, v in q.items() if k != "marks"} for q in qs], "results": results}, indent=1))
    (out / "report.md").write_text(report(qs, results, models))
    return out


def report(qs, results, models) -> str:
    conds = []
    for q in qs:
        for c in CONDITIONS_FOR[q["kind"]]:
            if c not in conds:
                conds.append(c)
    def acc(rs):
        return f"{sum(r['correct'] for r in rs)}/{len(rs)}" if rs else "—"
    lines = ["# cadence-vision eval", "", f"questions {len(qs)}, calls {len(results)}", "",
             "## accuracy: model × condition", "", "| model | " + " | ".join(conds) + " | all |", "|---|" + "---|" * (len(conds) + 1)]
    for mk in models:
        rs = [r for r in results if r["model"] == mk]
        lines.append(f"| {mk} | " + " | ".join(acc([r for r in rs if r["condition"] == c]) for c in conds) + f" | {acc(rs)} |")
    lines += ["", "## accuracy: question kind × condition (all models)", "", "| kind | " + " | ".join(conds) + " |", "|---|" + "---|" * len(conds)]
    for kind in CONDITIONS_FOR:
        rs = [r for r in results if r["kind"] == kind]
        if rs:
            lines.append(f"| {kind} | " + " | ".join(acc([r for r in rs if r["condition"] == c]) for c in conds) + " |")
    lines += ["", "## per call", "", "| question | condition | model | answer | reply | ok |", "|---|---|---|---|---|---|"]
    for r in results:
        lines.append(f"| {r['id']} | {r['condition']} | {r['model']} | {r['answer']} | {r['reply'].replace('|', '/')} | {'✅' if r['correct'] else '❌'} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="cadence-vision eval")
    ap.add_argument("--models", default=",".join(MODELS))
    ap.add_argument("--conditions", default="")
    ap.add_argument("--comps", default=",".join(COMPS))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--run", default=time.strftime("%Y%m%d-%H%M"))
    ap.add_argument("--questions-only", action="store_true")
    a = ap.parse_args(argv)
    if a.questions_only:
        for q in gen_questions(a.comps.split(",")):
            print(json.dumps({k: v for k, v in q.items() if k != "marks"}))
        return 0
    out = run(a.models.split(","), a.conditions.split(",") if a.conditions else None, a.comps.split(","), a.limit, a.run)
    print((out / "report.md").read_text().split("## per call")[0])
    print(f"report: {out / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
