#!/usr/bin/env python3
"""Fetch public eval media, render the suite, write self-contained eval.html."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native" / "release"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fetch_assets() -> None:
    catalog = load_json(ROOT / "evals" / "assets.json")
    ua = catalog["user_agent"]
    for asset in catalog["assets"]:
        dest = ROOT / asset["path"]
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and dest.stat().st_size > 0:
            print(f"asset  have  {asset['id']} ({dest.stat().st_size} bytes)")
            continue
        print(f"asset  get   {asset['id']}")
        urls = [asset["url"]] + ([asset["fallback"]] if asset.get("fallback") else [])
        last_error: Exception | None = None
        done = False
        for url in urls:
            req = urllib.request.Request(url, headers={"User-Agent": ua, "Accept": "*/*"})
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(req, timeout=120) as response:
                        dest.write_bytes(response.read())
                    print(f"asset  wrote {asset['id']} ({dest.stat().st_size} bytes)")
                    last_error = None
                    done = True
                    break
                except (urllib.error.URLError, TimeoutError) as err:
                    last_error = err
                    wait = 3 * (attempt + 1)
                    print(f"asset  retry {asset['id']} in {wait}s ({err})")
                    time.sleep(wait)
            if done:
                break
        if not done:
            raise SystemExit(f"failed to fetch {asset['id']}: {last_error}")
        time.sleep(1.0)


def find_love() -> str:
    env = os.environ.get("LOVE_BIN")
    if env:
        return env
    candidates = [
        ROOT / "build" / "ellua-love-macos-mega" / "love" / "ellua-love",
        ROOT / "vendor" / "love12.app" / "Contents" / "MacOS" / "love",
        Path("/Applications/love.app/Contents/MacOS/love"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    found = shutil.which("love")
    if found:
        return found
    raise SystemExit("ellua: renderer not found (set LOVE_BIN)")


def have_requirement(name: str) -> bool:
    ext = ".dylib" if sys.platform == "darwin" else ".dll" if sys.platform == "win32" else ".so"
    mapping = {
        "native-vector": NATIVE / f"libellua_vector{ext}",
        "native-html": NATIVE / f"libellua_html{ext}",
        "native-layout": NATIVE / f"libellua_layout{ext}",
        "native-effects": NATIVE / f"libellua_effects{ext}",
        "native-scene3d": NATIVE / f"libellua_scene3d{ext}",
        "resvg": shutil.which("resvg"),
        "thorvg": Path("/opt/homebrew/lib/libthorvg-1.dylib") if sys.platform == "darwin" else shutil.which("libthorvg"),
    }
    needed = mapping.get(name)
    if needed is None:
        return True
    if isinstance(needed, Path):
        return needed.exists()
    return bool(needed)


def render_case(love: str, case: dict, out_dir: Path) -> dict:
    case_id = case["id"]
    missing = [req for req in case.get("requires", []) if not have_requirement(req)]
    result = {
        "id": case_id,
        "title": case["title"],
        "tests": case.get("tests", []),
        "description": case.get("description", ""),
        "file": case["file"],
        "output": f"{case_id}.mp4",
    }
    if missing:
        result.update(status="skip", reason="missing " + ", ".join(missing))
        print(f"SKIP {case_id} ({result['reason']})")
        return result

    dest = out_dir / f"{case_id}.mp4"
    env = os.environ.copy()
    env["LOVE_BIN"] = love
    env["ELLUA_HEADLESS"] = "1"
    started = time.time()
    proc = subprocess.run(
        [str(ROOT / "bin" / "ellua"), "render", case["file"], "-o", str(dest)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    elapsed = round(time.time() - started, 2)
    if proc.returncode != 0 or not dest.exists():
        tail = (proc.stderr or proc.stdout or "").strip()[-1200:]
        result.update(status="fail", reason=tail or f"exit {proc.returncode}", seconds=elapsed)
        print(f"FAIL {case_id}\n{tail}")
        return result
    result.update(status="pass", seconds=elapsed, bytes=dest.stat().st_size)
    print(f"PASS {case_id} ({elapsed}s, {dest.stat().st_size} bytes)")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the public Ellua renderer eval suite")
    parser.add_argument("--skip-fetch", action="store_true")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()

    if not args.skip_fetch:
        fetch_assets()

    love = find_love()
    out_dir = ROOT / "evals" / "out"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)

    manifest = load_json(ROOT / "evals" / "manifest.json")
    results = [render_case(love, case, out_dir) for case in manifest["cases"]]
    status = {
        "title": manifest["title"],
        "subtitle": manifest["subtitle"],
        "renderer": love,
        "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "pass": sum(1 for item in results if item["status"] == "pass"),
        "fail": sum(1 for item in results if item["status"] == "fail"),
        "skip": sum(1 for item in results if item["status"] == "skip"),
        "cases": results,
    }
    (out_dir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")

    subprocess.check_call(
        [
            sys.executable,
            str(ROOT / "bin" / "write-eval.py"),
            str(out_dir),
            "--manifest",
            str(ROOT / "evals" / "manifest.json"),
            "--status",
            str(out_dir / "status.json"),
        ]
    )
    report = out_dir / "eval.html"
    print(f"eval  {report}")
    if args.open:
        subprocess.run(["open", str(report)], check=False)
    return 0 if status["fail"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
