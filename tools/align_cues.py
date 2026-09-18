#!/usr/bin/env python3
"""Word-align a narration.json and add caption cues to it, in place.

    vision/.venv/bin/python tools/align_cues.py comps/agent/narration.json

Each chapter's clip is force-aligned against its own script (audiofacts.align, the aligner
measured to within one frame), then grouped into short phrases that turn over when the voice
does. A cue is held until the next one starts so a line never blinks out mid-sentence.
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))
from cadence_vision import audiofacts  # noqa: E402

MAXW, MAXC = 7, 46


def cues_for(words: list[dict], dur: float) -> list[dict]:
    cues, cur = [], []
    for w in words:
        cur.append(w)
        txt = " ".join(x["word"] for x in cur)
        if len(cur) >= MAXW or len(txt) >= MAXC or (re.search(r"[.,:;]$", w["word"]) and len(cur) >= 3):
            cues.append({"t0": round(cur[0]["start"], 3), "t1": round(cur[-1]["end"], 3), "text": txt})
            cur = []
    if cur:
        cues.append({"t0": round(cur[0]["start"], 3), "t1": round(cur[-1]["end"], 3),
                     "text": " ".join(x["word"] for x in cur)})
    for a, b in zip(cues, cues[1:]):
        a["t1"] = b["t0"]
    if cues:
        cues[-1]["t1"] = min(cues[-1]["t1"] + 0.35, dur)
    return cues


def main(path: str) -> None:
    p = Path(path)
    meta = json.loads(p.read_text())
    for m in meta:
        words = audiofacts.align(ROOT / m["file"], m["say"])
        m["cues"] = cues_for(words, m["dur"])
        m["words"] = [{"w": w["word"], "t": round(w["start"], 3)} for w in words]
        print(f"{m['id']:9s} {len(words):3d} words -> {len(m['cues']):2d} cues")
    p.write_text(json.dumps(meta, indent=1))


if __name__ == "__main__":
    main(sys.argv[1])
