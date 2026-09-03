#!/usr/bin/env python3
"""Write a self-contained visual evaluation report from rendered MP4 artifacts."""

from __future__ import annotations

import argparse
import base64
import html
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def probe(video: Path) -> dict[str, str]:
    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_name,codec_type,width,height,nb_frames",
        "-of",
        "json",
        str(video),
    ]
    try:
        payload = json.loads(subprocess.check_output(command, text=True))
        stream = next((s for s in payload.get("streams", []) if "width" in s), {})
        audio = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), None)
        return {
            "codec": stream.get("codec_name", "unknown"),
            "dimensions": f'{stream.get("width", "?")}×{stream.get("height", "?")}',
            "frames": stream.get("nb_frames", "?"),
            "duration": f'{float(payload.get("format", {}).get("duration", 0)):.2f}s',
            "audio": (audio or {}).get("codec_name", "none"),
        }
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError):
        return {
            "codec": "unknown",
            "dimensions": "unknown",
            "frames": "?",
            "duration": "?",
            "audio": "none",
        }


def pills(items: list[str]) -> str:
    return "".join(f'<span class="tag">{html.escape(item)}</span>' for item in items)


def case_card(case: dict, video: Path | None) -> str:
    status = case.get("status", "pass")
    title = html.escape(case.get("title") or case.get("id", "case"))
    description = html.escape(case.get("description", ""))
    tests = pills(case.get("tests", []))
    if status == "skip":
        return f"""
      <article class="card skip">
        <div class="placeholder">skipped</div>
        <div class="detail">
          <div class="kicker">{html.escape(status)} · {html.escape(case.get("id", ""))}</div>
          <h2>{title}</h2>
          <p>{description}</p>
          <div class="tags">{tests}</div>
          <p class="reason">{html.escape(case.get("reason", ""))}</p>
        </div>
      </article>"""
    if status == "fail" or video is None:
        reason = html.escape((case.get("reason") or "missing mp4")[:400])
        return f"""
      <article class="card fail">
        <div class="placeholder">failed</div>
        <div class="detail">
          <div class="kicker">{html.escape(status)} · {html.escape(case.get("id", ""))}</div>
          <h2>{title}</h2>
          <p>{description}</p>
          <div class="tags">{tests}</div>
          <p class="reason">{reason}</p>
        </div>
      </article>"""

    metadata = probe(video)
    payload = base64.b64encode(video.read_bytes()).decode("ascii")
    size_mib = video.stat().st_size / (1024 * 1024)
    label = html.escape(case.get("id", video.name))
    return f"""
      <article class="card">
        <video controls muted loop preload="metadata" aria-label="{label}">
          <source src="data:video/mp4;base64,{payload}" type="video/mp4">
          Your browser does not support embedded MP4 playback.
        </video>
        <div class="detail">
          <div class="kicker">pass · {label}</div>
          <h2>{title}</h2>
          <p>{description}</p>
          <div class="tags">{tests}</div>
          <dl>
            <div><dt>Format</dt><dd>{html.escape(metadata["codec"])} · {html.escape(metadata["dimensions"])}</dd></div>
            <div><dt>Timeline</dt><dd>{html.escape(metadata["duration"])} · {html.escape(metadata["frames"])} frames</dd></div>
            <div><dt>Audio</dt><dd>{html.escape(metadata["audio"])}</dd></div>
            <div><dt>Asset</dt><dd>{size_mib:.2f} MiB · inline</dd></div>
          </dl>
        </div>
      </article>"""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", nargs="?", default="evals/out")
    parser.add_argument("--manifest")
    parser.add_argument("--status")
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    report = output_dir / "eval.html"
    status_path = Path(args.status).resolve() if args.status else output_dir / "status.json"
    manifest_path = Path(args.manifest).resolve() if args.manifest else None

    if status_path.exists():
        status = json.loads(status_path.read_text(encoding="utf-8"))
        cases = status["cases"]
        title = status.get("title", "Ellua Renderer Eval")
        subtitle = status.get("subtitle", "")
        generated = status.get("generated", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
        renderer = status.get("renderer", "")
        counts = (status.get("pass", 0), status.get("fail", 0), status.get("skip", 0))
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path else {}
        videos = {path.stem: path for path in sorted(output_dir.glob("*.mp4"))}
        cases = [{"id": name, "title": name, "status": "pass"} for name in videos]
        title = manifest.get("title", "Ellua Renderer Eval")
        subtitle = manifest.get("subtitle", "Self-contained visual review of renderer fixtures.")
        generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        renderer = ""
        counts = (len(cases), 0, 0)

    cards = []
    for case in cases:
        video = output_dir / case.get("output", f"{case['id']}.mp4")
        cards.append(case_card(case, video if video.is_file() else None))
    if not cards:
        raise SystemExit(f"no eval cases found in {output_dir}")

    body = "\n".join(cards)
    report.write_text(
        f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, sans-serif; background: #090c12; color: #f4f6fb; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: radial-gradient(circle at 20% -10%, #1a2744, #090c12 42rem); }}
    main {{ max-width: 1680px; margin: 0 auto; padding: 48px 28px 88px; }}
    h1 {{ font-size: clamp(2.1rem, 5vw, 4rem); line-height: .95; letter-spacing: -.05em; margin: 0 0 16px; }}
    .summary {{ color: #b7c0d6; font-size: 1.05rem; max-width: 78ch; line-height: 1.55; }}
    .pill, .tag {{ display: inline-block; border: 1px solid #3b4663; background: #151c2e; color: #d5def6; border-radius: 999px; padding: 5px 10px; font-size: .78rem; margin: 10px 6px 0 0; }}
    .tag {{ margin: 0 6px 6px 0; background: #10182a; color: #9eabc8; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(340px, 1fr)); gap: 20px; margin-top: 28px; }}
    .card {{ overflow: hidden; border-radius: 18px; border: 1px solid #2a334c; background: #10151f; box-shadow: 0 16px 50px #0006; }}
    .card.fail {{ border-color: #7a3344; }}
    .card.skip {{ border-color: #3a4458; opacity: .86; }}
    video, .placeholder {{ display: block; width: 100%; aspect-ratio: 16/9; background: #03050a; }}
    .placeholder {{ display: grid; place-items: center; color: #74809b; letter-spacing: .18em; text-transform: uppercase; font-size: .78rem; }}
    .detail {{ padding: 16px 16px 18px; }}
    .kicker {{ color: #7d8bab; font-size: .75rem; letter-spacing: .12em; text-transform: uppercase; margin-bottom: 8px; }}
    h2 {{ margin: 0 0 8px; font-size: 1.12rem; }}
    p {{ margin: 0 0 10px; color: #aeb8ce; font-size: .92rem; line-height: 1.45; }}
    .reason {{ color: #d7a3b0; font-family: ui-monospace, SFMono-Regular, monospace; font-size: .78rem; white-space: pre-wrap; }}
    dl {{ margin: 10px 0 0; display: grid; gap: 6px; color: #aeb8ce; font-size: .82rem; }}
    dl div {{ display: flex; justify-content: space-between; gap: 12px; }}
    dt {{ color: #74809b; }} dd {{ margin: 0; text-align: right; }}
    footer {{ margin-top: 32px; color: #74809b; font-size: .85rem; line-height: 1.5; }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>{html.escape(title)}</h1>
      <p class="summary">{html.escape(subtitle)}</p>
      <span class="pill">{counts[0]} pass</span>
      <span class="pill">{counts[1]} fail</span>
      <span class="pill">{counts[2]} skip</span>
      <span class="pill">{len(cases)} cases</span>
      <span class="pill">generated {html.escape(generated)}</span>
    </header>
    <section class="grid">{body}
    </section>
    <footer>
      Renderer {html.escape(renderer or "unknown")}. Overwrite-safe output from evals/out/eval.html.
      Media sources are Wikimedia Commons / NASA public domain, CC Wikimedia SVGs, and Apache-2.0 Lottie samples.
    </footer>
  </main>
</body>
</html>
""",
        encoding="utf-8",
    )
    print(f"wrote {report} with {len(cases)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
