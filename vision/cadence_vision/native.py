"""Native video for models that take a clip directly. ffmpeg trims, scales and
encodes H.264 (yuv420p, faststart) so Gemini, OpenRouter and vLLM all accept
it; the result carries the request fragment each provider wants."""

from __future__ import annotations

import base64
import hashlib
import subprocess

from . import CACHE
from .profiles import Profile
from .sources import Source, mmss


def prepare(src: Source, pr: Profile, t0: float, t1: float, fps: float, long_edge: int, presample: bool,
            max_inline_mb: float) -> dict:
    if src.kind == "image":
        raise ValueError("native_video needs a clip or comp, not a still image")
    t0 = max(0.0, t0); t1 = min(src.duration, t1)
    if t1 <= t0:
        raise ValueError(f"empty window {t0}..{t1}")
    key = hashlib.sha1(f"{src.media}|{src.media.stat().st_mtime_ns}|{t0}|{t1}|{fps}|{long_edge}|{presample}".encode()).hexdigest()[:12]
    out = CACHE / f"native-{key}.mp4"
    if not out.exists():
        # fit the long edge, keep both dimensions even for yuv420p
        vf = [f"scale='trunc(min(1,{long_edge}/max(iw,ih))*iw/2)*2':'trunc(min(1,{long_edge}/max(iw,ih))*ih/2)*2'"]
        if presample:
            vf.insert(0, f"fps={fps}")
        cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.3f}", "-to", f"{t1:.3f}", "-i", str(src.media),
               "-vf", ",".join(vf), "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
               "-movflags", "+faststart", "-an", str(out)]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg failed: {r.stderr[-400:]}")
    size = out.stat().st_size
    dur = t1 - t0
    info = {"path": str(out), "bytes": size, "mb": round(size / 1e6, 2), "duration": round(dur, 3), "window": [t0, t1],
            "sampled_fps": fps, "presampled": presample, "frames_seen": int(dur * fps),
            "est_tokens": {"gemini_low": int(dur * 100), "gemini_high": int(dur * 300), "qwen3_vl": int(dur * fps * pr.video.tokens_per_frame)},
            "ask_for": "timestamps as MM:SS" if pr.timestamps else "frame numbers",
            "window_timestamps": [mmss(t0), mmss(t1)]}
    if size <= max_inline_mb * 1e6:
        info["data_url"] = "data:video/mp4;base64," + base64.b64encode(out.read_bytes()).decode()
    info["requests"] = {
        "gemini_direct": {"contents": [{"parts": [
            {"inline_data": {"mime_type": "video/mp4", "data": "<base64 of path>"},
             "video_metadata": {"fps": fps, "start_offset": f"{0}s", "end_offset": f"{dur:.3f}s"}},
            {"text": "<question>; answer with MM:SS timestamps"}]}]},
        "openrouter": {"messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": "<data_url or hosted url>", "processing": "static"}},
            {"type": "text", "text": "<question>"}]}],
            "note": "no fps control on OpenRouter; use presample=true so the clip itself carries the frame rate"},
        "qwen_vllm": {"messages": [{"role": "user", "content": [
            {"type": "video_url", "video_url": {"url": "<data_url or file://path>"}},
            {"type": "text", "text": "<question>"}]}],
            "extra_body": {"mm_processor_kwargs": {"fps": fps}}},
    }
    return info
