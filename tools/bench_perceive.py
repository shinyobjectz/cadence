"""Time the perception pipeline stage by stage on one clip.

    CADENCE_PROFILE=1 vision/.venv/bin/python tools/bench_perceive.py CLIP "prompt,prompt" [--contact] [--script TEXT]

Prints the stage table and writes <out>.json with it, the clip's duration and the machine, so
numbers from different Macs or backends can be compared. Every run is cold: models load inside
the timed stages, which is the cost an agent pays for the first clip of a session.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "vision"))
os.environ.setdefault("CADENCE_PROFILE", "1")

from cadence_vision import perceive as PC          # noqa: E402
from cadence_vision import profile as PF           # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("clip")
ap.add_argument("prompts")
ap.add_argument("--contact", action="store_true")
ap.add_argument("--script", default=None)
ap.add_argument("--out", default=None)
a = ap.parse_args()

clip = Path(a.clip)
dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
                            str(clip)], capture_output=True, text=True).stdout)
t0 = time.perf_counter()
text = PC.perceive(clip, [p.strip() for p in a.prompts.split(",") if p.strip()],
                   want_contact=a.contact, script=a.script, log=lambda s: print("  log:", s, file=sys.stderr))
wall = time.perf_counter() - t0
chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
res = {"clip": str(clip), "clip_seconds": round(dur, 2), "wall_seconds": round(wall, 1),
       "x_realtime": round(wall / dur, 1), "facts": text.count("\n"), "machine": chip or platform.machine(),
       "stages": PF.timings()}
out = Path(a.out or ROOT / "vision/cache" / f"bench-{clip.stem}.json")
out.write_text(json.dumps(res, indent=1))
out.with_suffix(".facts").write_text(text)
print(PF.report())
print(f"\n{clip.name}: {dur:.1f}s of video took {wall:.0f}s ({wall / dur:.1f}x realtime) on {res['machine']}")
print(f"wrote {out}")
