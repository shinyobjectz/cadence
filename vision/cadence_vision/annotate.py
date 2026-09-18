"""Marks on a frame. For a comp the marks come from Cadence's own node state
(vision/lua/props.lua, luajit, host-free) so an answer like "3 is too close to
2" binds to real node ids. For plain video/images the caller passes boxes or
points, or asks for a coordinate grid."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

from . import ROOT
from .sheet import _font
from .sources import Source

PALETTE = [(255, 82, 82), (82, 196, 255), (255, 214, 82), (120, 255, 140), (255, 140, 255),
           (255, 160, 60), (150, 150, 255), (90, 230, 220)]


def props(comp: Path, times: list[float]) -> dict:
    cmd = ["luajit", str(ROOT / "vision" / "lua" / "props.lua"), str(ROOT), str(comp)] + [f"{t:.4f}" for t in times]
    r = subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"props.lua failed: {r.stderr[-400:]}")
    return json.loads(r.stdout)


def _tkey(t: float) -> str:
    return f"{t:.4f}"


def _parent_frame(nid: str, row: dict, meta: dict, cache: dict) -> tuple[float, float, float, float]:
    """(ox, oy, scale, opacity) the parent chain contributes to `nid`.

    A child's x/y are relative to its parent's origin and are scaled by it, so a node whose parent
    moves moves with it. Without this a parented node's box is in parent-local space, which puts
    every mark and every region in the wrong place."""
    if nid in cache:
        return cache[nid]
    pid = (meta.get(nid) or {}).get("parent")
    if not pid or pid not in row:
        cache[nid] = (0.0, 0.0, 1.0, 1.0)
        return cache[nid]
    pox, poy, psc, pop = _parent_frame(pid, row, meta, cache)
    pst, pm = row[pid], meta.get(pid, {})
    px, py = float(pst.get("x", 0) or 0), float(pst.get("y", 0) or 0)
    sc = float(pst.get("scale", 1) or 1)
    ox, oy = pox + px * psc, poy + py * psc
    if pm.get("anchor") == "center":          # a centred parent's origin is its top-left corner
        ox -= float(pst.get("w", 0) or 0) * psc * sc / 2
        oy -= float(pst.get("h", 0) or 0) * psc * sc / 2
    cache[nid] = (ox, oy, psc * sc, pop * float(pst.get("opacity", 1) or 1))
    return cache[nid]


def node_boxes(comp: Path, t: float) -> dict:
    """Approximate screen boxes {id: {x,y,w,h,kind,z,opacity,...}} at time t, comp pixel space."""
    d = props(comp, [t])
    return boxes_at(d, t)


def boxes_at(d: dict, t: float) -> dict:
    """node_boxes for an already-fetched props dict (so a caller sampling many times pays once)."""
    row = d["at"][_tkey(t)]
    meta = {n["id"]: n for n in d["nodes"]}
    cache: dict = {}
    out = {}
    for nid, st in row.items():
        m = meta.get(nid, {})
        kind = m.get("kind", "?")
        if kind in ("audio", "tts", "sfx", "music", "script", "world", "light"):
            continue
        ox, oy, psc, pop = _parent_frame(nid, row, meta, cache)
        x = ox + float(st.get("x", 0) or 0) * psc
        y = oy + float(st.get("y", 0) or 0) * psc
        sc = float(st.get("scale", 1) or 1) * psc
        if kind == "circle":
            r = float(st.get("r", 0) or 0) * sc
            box = (x - r, y - r, 2 * r, 2 * r)
        elif kind == "text":
            size = float(st.get("size", 24) or 24) * sc
            txt = str(st.get("text", "") or "")
            lines = txt.split("\n")
            if st.get("tw") is not None:      # measured by the scene rasterizer's shaper (props.lua)
                w, h = float(st["tw"]) * sc, float(st["th"]) * sc
            else:
                w = max((len(l) for l in lines), default=0) * size * 0.56
                h = size * 1.2 * max(1, len(lines))
            if m.get("anchor") == "center":
                box = (x - w / 2, y - h / 2, w, h)
            else:
                box = (x, y, w, h)
        else:
            w = float(st.get("w", 0) or 0) * sc
            h = float(st.get("h", 0) or 0) * sc
            if m.get("anchor") == "center":
                box = (x - w / 2, y - h / 2, w, h)
            else:
                box = (x, y, w, h)
        entry = {"kind": kind, "z": m.get("z"), "parent": m.get("parent"), "src": m.get("src"),
                 "x": round(box[0], 1), "y": round(box[1], 1), "w": round(box[2], 1), "h": round(box[3], 1),
                 "opacity": float(st.get("opacity", 1) or 1) * pop, "rotation": st.get("rotation", 0),
                 "scale": sc}
        if "text" in st:
            entry["text"] = st["text"]
        if "color" in st:
            entry["color"] = st["color"]
        out[nid] = entry
    return {"width": d.get("width"), "height": d.get("height"), "fps": d.get("fps"), "duration": d.get("duration"), "t": t, "nodes": out}


def visible(nodes: dict, W: int, H: int) -> list[tuple[str, dict]]:
    """Nodes with opacity > 0.02 and a box that intersects the frame, in z order."""
    out = []
    for nid, n in nodes.items():
        if float(n.get("opacity") or 0) <= 0.02 or n["w"] <= 0 or n["h"] <= 0:
            continue
        if n["x"] + n["w"] < 0 or n["y"] + n["h"] < 0 or n["x"] > W or n["y"] > H:
            continue
        out.append((nid, n))
    out.sort(key=lambda kv: kv[1].get("z") or 0)
    return out


def draw_marks(im: Image.Image, marks: list[dict], style: str = "som", scale: float = 1.0) -> Image.Image:
    """marks: [{id, x, y, w, h}] in source pixels (scale maps them onto `im`). Set-of-Mark style:
    a thin box plus a numbered tag with a solid background at the top-left corner."""
    im = im.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    fs = max(14, int(min(im.size) * 0.028))
    font = _font(fs)
    for i, m in enumerate(marks):
        col = PALETTE[i % len(PALETTE)]
        x, y, w, h = (m["x"] * scale, m["y"] * scale, m["w"] * scale, m["h"] * scale)
        if style in ("som", "boxes"):
            d.rectangle([x, y, x + w, y + h], outline=col, width=max(2, fs // 7))
        tag = str(m.get("label", i + 1))
        tw = d.textlength(tag, font=font)
        tx, ty = max(0, min(x, im.width - tw - 8)), max(0, y - fs - 6) if y - fs - 6 >= 0 else y
        d.rectangle([tx, ty, tx + tw + 8, ty + fs + 6], fill=col)
        d.text((tx + 4, ty + 2), tag, fill=(0, 0, 0), font=font)
    return im


def draw_grid(im: Image.Image, step_px: int = 100, scale: float = 1.0) -> Image.Image:
    """Labeled coordinate grid in source pixels (labels every step); a weak aid on its own, useful with marks."""
    im = im.convert("RGB").copy()
    d = ImageDraw.Draw(im)
    font = _font(max(11, int(min(im.size) * 0.018)))
    W, H = im.size
    sw = W / scale; sh = H / scale
    x = 0.0
    while x <= sw:
        px = x * scale
        d.line([px, 0, px, H], fill=(255, 255, 255, 90), width=1)
        d.text((px + 2, 2), str(int(x)), fill=(255, 255, 0), font=font)
        x += step_px
    y = 0.0
    while y <= sh:
        py = y * scale
        d.line([0, py, W, py], fill=(255, 255, 255, 90), width=1)
        d.text((2, py + 2), str(int(y)), fill=(255, 255, 0), font=font)
        y += step_px
    return im


def legend(marks: list[dict]) -> list[dict]:
    return [{"mark": m.get("label", i + 1), "id": m.get("id"), "kind": m.get("kind"),
             "box": [round(m["x"]), round(m["y"]), round(m["w"]), round(m["h"])],
             **({"text": m["text"]} if m.get("text") else {})} for i, m in enumerate(marks)]


def comp_marks(src: Source, t: float) -> tuple[list[dict], dict]:
    nb = node_boxes(src.comp, t)
    vis = visible(nb["nodes"], nb["width"] or src.width, nb["height"] or src.height)
    marks = [{"id": nid, **n} for nid, n in vis]
    return marks, nb
