"""cadence-vision: one MCP that renders Cadence frames and source clips into
what a vision or video language model reads best (docs/VISION.md).

Pure-Python pieces (sources, keyframes, sheets, annotation, diff) need only
ffmpeg, numpy, Pillow and OpenCV. Depth and geometry load Depth Anything 3
lazily on first use."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "vision" / "cache"
CACHE.mkdir(parents=True, exist_ok=True)

__all__ = ["ROOT", "CACHE"]
