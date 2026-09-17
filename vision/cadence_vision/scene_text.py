"""Text views of a frame or a stretch of a source: exact node data when the
source is a comp, motion between two times from optical flow on the scan
strip, depth layers when a depth result is supplied."""

from __future__ import annotations

import numpy as np

from .annotate import comp_marks
from .sources import Source, scan, mmss


def _region(cx: float, cy: float) -> str:
    col = "left" if cx < 1 / 3 else ("right" if cx > 2 / 3 else "center")
    row = "top" if cy < 1 / 3 else ("bottom" if cy > 2 / 3 else "middle")
    return f"{row}-{col}" if row != "middle" or col != "center" else "center"


def comp_nodes_text(src: Source, t: float) -> dict:
    marks, nb = comp_marks(src, t)
    W, H = nb["width"] or src.width, nb["height"] or src.height
    rows = []
    for i, m in enumerate(marks):
        cx = (m["x"] + m["w"] / 2) / W; cy = (m["y"] + m["h"] / 2) / H
        row = {"mark": i + 1, "id": m["id"], "kind": m["kind"], "z": m["z"], "region": _region(cx, cy),
               "box": [round(m["x"]), round(m["y"]), round(m["w"]), round(m["h"])],
               "opacity": round(float(m.get("opacity") or 0), 3)}
        if m.get("text"):
            row["text"] = m["text"]
        if m.get("src"):
            row["src"] = m["src"]
        if m.get("parent"):
            row["parent"] = m["parent"]
        if m.get("color") and isinstance(m["color"], list) and len(m["color"]) >= 3:
            r, g, b = [int(round(c * 255)) for c in m["color"][:3]]
            row["color"] = f"#{r:02x}{g:02x}{b:02x}"
        rows.append(row)
    return {"t": t, "timestamp": mmss(t), "canvas": [W, H], "fps": nb["fps"], "duration": nb["duration"],
            "draw_order": "listed back-to-front (z ascending); later rows paint over earlier ones",
            "nodes": rows}


def motion_text(src: Source, t0: float, t1: float) -> dict:
    """Coarse motion between t0 and t1 from Farneback flow on the low-res scan strip, per 3x3 region."""
    import cv2
    times, frames = scan(src)
    i0 = int(np.abs(times - t0).argmin()); i1 = int(np.abs(times - t1).argmin())
    if i0 == i1:
        return {"t0": t0, "t1": t1, "motion": "none (same sample)"}
    g0 = cv2.cvtColor(frames[i0], cv2.COLOR_RGB2GRAY); g1 = cv2.cvtColor(frames[i1], cv2.COLOR_RGB2GRAY)
    flow = cv2.calcOpticalFlowFarneback(g0, g1, None, 0.5, 3, 15, 3, 5, 1.2, 0)
    h, w = g0.shape
    regions = []
    for r in range(3):
        for c in range(3):
            f = flow[r * h // 3:(r + 1) * h // 3, c * w // 3:(c + 1) * w // 3]
            dx, dy = float(f[..., 0].mean()), float(f[..., 1].mean())
            mag = float(np.hypot(f[..., 0], f[..., 1]).mean())
            if mag < 0.3:
                continue
            ang = np.degrees(np.arctan2(-dy, dx)) % 360
            dirn = ["right", "up-right", "up", "up-left", "left", "down-left", "down", "down-right"][int(((ang + 22.5) % 360) // 45)]
            regions.append({"region": _region((c + 0.5) / 3, (r + 0.5) / 3), "direction": dirn,
                            "px_per_frame_lowres": round(mag, 2)})
    changed = float((np.abs(g1.astype(int) - g0.astype(int)) > 24).mean())
    return {"t0": t0, "t1": t1, "changed_fraction": round(changed, 3), "moving_regions": regions,
            "note": "flow measured on a 160-px strip; magnitudes are relative"}


def depth_text(stats: dict, lay: list[dict], per_node: dict | None = None) -> dict:
    out = {"depth": {**stats, "reading": "flat frame, no usable relief" if stats.get("flat") else "has relief"},
           "layers_near_to_far": lay}
    if per_node:
        ordered = sorted(per_node.items(), key=lambda kv: -kv[1]["relief"])
        out["nodes_near_to_far"] = [{"id": k, **v} for k, v in ordered]
    return out
