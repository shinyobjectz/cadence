"""Sources: a Cadence comp (.lua), a video file, or a still image. Comps are
rendered once through bin/cadence into vision/cache and then treated as video.
Frames come out of ffmpeg as RGB numpy arrays."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np
from PIL import Image

from . import ROOT, CACHE

VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v", ".avi", ".ogv"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 600) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=str(cwd or ROOT), capture_output=True, text=True, timeout=timeout)


def resolve_path(p: str | os.PathLike) -> Path:
    q = Path(p).expanduser()
    if not q.is_absolute():
        q = (ROOT / q).resolve() if (ROOT / q).exists() else (Path.cwd() / q).resolve()
    return q


def _key(path: Path, extra: str = "") -> str:
    st = path.stat()
    return hashlib.sha1(f"{path}|{st.st_mtime_ns}|{st.st_size}|{extra}".encode()).hexdigest()[:16]


@dataclass
class Source:
    path: Path                 # what the caller named
    media: Path                # the file frames come from (rendered mp4 for comps)
    kind: str                  # comp | video | image
    width: int
    height: int
    duration: float
    fps: float
    comp: Optional[Path] = None

    def info(self) -> dict:
        return {"path": str(self.path), "media": str(self.media), "kind": "image" if self.kind == "image" else "video",
                "comp": str(self.comp) if self.comp else None, "width": self.width, "height": self.height,
                "duration": self.duration, "fps": self.fps}


def ffprobe(media: Path) -> dict:
    r = _run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
              "stream=width,height,r_frame_rate,duration,nb_frames:format=duration", "-of", "json", str(media)])
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed on {media}: {r.stderr.strip()[:300]}")
    d = json.loads(r.stdout)
    s = d["streams"][0]
    num, den = s.get("r_frame_rate", "30/1").split("/")
    fps = float(num) / float(den or 1)
    dur = s.get("duration") or d.get("format", {}).get("duration") or 0
    return {"width": int(s["width"]), "height": int(s["height"]), "fps": fps, "duration": float(dur)}


def render_comp(comp: Path) -> Path:
    """Render a comp to a cached mp4 via bin/cadence (cwd = repo root so relative asset paths resolve)."""
    out = CACHE / f"comp-{_key(comp)}.mp4"
    if out.exists():
        return out
    rel = os.path.relpath(comp, ROOT)
    r = _run([str(ROOT / "bin" / "cadence"), "render", rel, "-o", str(out)], cwd=ROOT, timeout=1800)
    if r.returncode != 0 or not out.exists():
        raise RuntimeError(f"cadence render failed for {comp}:\n{r.stderr[-800:]}\n{r.stdout[-400:]}")
    return out


def open_source(p: str | os.PathLike) -> Source:
    path = resolve_path(p)
    if not path.exists():
        raise FileNotFoundError(path)
    ext = path.suffix.lower()
    if ext == ".lua":
        media = render_comp(path)
        pr = ffprobe(media)
        return Source(path, media, "comp", pr["width"], pr["height"], pr["duration"], pr["fps"], comp=path)
    if ext in IMAGE_EXT:
        im = Image.open(path)
        return Source(path, path, "image", im.width, im.height, 0.0, 0.0)
    if ext in VIDEO_EXT:
        pr = ffprobe(path)
        return Source(path, path, "video", pr["width"], pr["height"], pr["duration"], pr["fps"])
    raise ValueError(f"unsupported source {path}")


def frame_at(src: Source, t: float, max_edge: Optional[int] = None) -> np.ndarray:
    """Exact frame at time t (seconds) as RGB uint8. For images t is ignored."""
    if src.kind == "image":
        im = Image.open(src.media).convert("RGB")
    else:
        t = float(min(max(0.0, t), max(0.0, src.duration - 1.0 / max(src.fps, 1))))
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.4f}", "-i", str(src.media), "-frames:v", "1",
                            "-f", "image2pipe", "-pix_fmt", "rgb24", "-vcodec", "rawvideo", "-"],
                           capture_output=True, timeout=120)
        if r.returncode != 0 or not r.stdout:
            raise RuntimeError(f"ffmpeg frame extraction failed at t={t}: {r.stderr.decode()[-300:]}")
        arr = np.frombuffer(r.stdout, np.uint8)
        arr = arr[: src.width * src.height * 3].reshape(src.height, src.width, 3)
        im = Image.fromarray(arr)
    if max_edge and max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
    return np.asarray(im)


def scan(src: Source, sample_fps: float = 4.0, edge: int = 160) -> tuple[np.ndarray, np.ndarray]:
    """Low-resolution strip of the whole source for keyframe scoring: (times, frames[N,h,w,3])."""
    if src.kind == "image":
        f = frame_at(src, 0, edge)
        return np.array([0.0]), f[None]
    key = _key(src.media, f"scan{sample_fps}-{edge}")
    cached = CACHE / f"scan-{key}.npz"
    if cached.exists():
        d = np.load(cached)
        return d["t"], d["f"]
    scale = f"scale={edge}:-2" if src.width >= src.height else f"scale=-2:{edge}"
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(src.media), "-vf", f"fps={sample_fps},{scale}",
                        "-f", "image2pipe", "-pix_fmt", "rgb24", "-vcodec", "rawvideo", "-"],
                       capture_output=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg scan failed: {r.stderr.decode()[-300:]}")
    # ffmpeg's -2 rounding: recompute the output size
    if src.width >= src.height:
        w = edge; h = int(round(src.height * edge / src.width / 2) * 2)
    else:
        h = edge; w = int(round(src.width * edge / src.height / 2) * 2)
    buf = np.frombuffer(r.stdout, np.uint8)
    n = len(buf) // (w * h * 3)
    frames = buf[: n * w * h * 3].reshape(n, h, w, 3)
    times = np.arange(n) / sample_fps
    np.savez_compressed(cached, t=times, f=frames)
    return times, frames


def save_png(arr: np.ndarray | Image.Image, name: str) -> Path:
    p = CACHE / name
    im = arr if isinstance(arr, Image.Image) else Image.fromarray(arr)
    im.save(p)
    return p


def fit(im: Image.Image, long_edge: Optional[int]) -> Image.Image:
    if long_edge and max(im.size) > long_edge:
        im = im.copy()
        im.thumbnail((long_edge, long_edge), Image.LANCZOS)
    return im


def mmss(t: float) -> str:
    m, s = divmod(float(t), 60)
    return f"{int(m):02d}:{s:05.2f}"
