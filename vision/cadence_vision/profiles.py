"""Model input contracts. A profile says how many images a model takes, how
large each may be, whether it ingests video natively and whether it can be
addressed by timestamp. plan_view() turns a source + profile into a list of
tool calls that fit the budget."""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from typing import Optional


@dataclass
class Video:
    native: bool = False
    fps: float = 1.0
    max_seconds: float = 0.0
    tokens_per_frame: int = 0


@dataclass
class Profile:
    name: str
    max_images: int
    long_edge_px: int
    tokens_per_image: int
    video: Video = field(default_factory=Video)
    timestamps: bool = False          # can the model be told / tell "MM:SS"?
    labels: str = "image_n"           # image_n | mm_ss | none
    notes: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


BUILTIN: dict[str, Profile] = {
    "claude": Profile(
        "claude", max_images=20, long_edge_px=1568, tokens_per_image=1568,
        labels="image_n",
        notes="No native video. Label every image ('Image 1:'); >20 images forces <=2000 px edges. "
              "Contact sheets at <=1568 px long edge are the cheapest way to show many frames.",
    ),
    "claude-hires": Profile(
        "claude-hires", max_images=20, long_edge_px=2576, tokens_per_image=4784,
        labels="image_n",
        notes="Claude 4.7+ high-resolution tier: 2576 px long edge, ~4784 tokens per image. Use for dense spatial views.",
    ),
    "claude-api": Profile(
        "claude-api", max_images=100, long_edge_px=1568, tokens_per_image=1568,
        labels="image_n",
        notes="API limit is 100 images on 200k-context models (600 on larger); keep frames as labeled sequences or sheets.",
    ),
    "gpt": Profile(
        "gpt", max_images=50, long_edge_px=1536, tokens_per_image=1105,
        labels="image_n",
        notes="GPT-4o/5.x: detail=high tiles at 512 px; 'original' detail exists for dense spatial images. No native video.",
    ),
    "gemini": Profile(
        "gemini", max_images=300, long_edge_px=1120, tokens_per_image=560,
        video=Video(native=True, fps=1.0, max_seconds=3600, tokens_per_frame=258),
        timestamps=True, labels="mm_ss",
        notes="Send the clip natively with fps/start_offset/end_offset and ask for MM:SS answers. "
              "Frames cost 258 tokens (2.5) or 70 (3, low/medium media_resolution).",
    ),
    "qwen3-vl": Profile(
        "qwen3-vl", max_images=64, long_edge_px=1024, tokens_per_image=1024,
        video=Video(native=True, fps=2.0, max_seconds=600, tokens_per_frame=256),
        timestamps=True, labels="mm_ss",
        notes="Dynamic resolution; total pixel budget 24576*32*32; 2 fps default with max_frames cap; timestamps interleaved as text.",
    ),
    "local-64f": Profile(
        "local-64f", max_images=64, long_edge_px=448, tokens_per_image=196,
        video=Video(native=True, fps=1.0, max_seconds=64, tokens_per_frame=196),
        timestamps=False, labels="image_n",
        notes="Open video LLMs (LLaVA-Video, InternVL3, VideoLLaMA3): ~1 fps, 64-180 frames, small tiles, no timestamp grounding.",
    ),
}


def get_profile(name_or_dict) -> Profile:
    if isinstance(name_or_dict, Profile):
        return name_or_dict
    if isinstance(name_or_dict, dict):
        base = BUILTIN.get(name_or_dict.get("name", ""), BUILTIN["claude"])
        d = base.to_dict()
        vid = d.pop("video")
        vid.update(name_or_dict.get("video", {}) or {})
        d.update({k: v for k, v in name_or_dict.items() if k != "video"})
        return Profile(video=Video(**vid), **d)
    if name_or_dict in BUILTIN:
        return BUILTIN[name_or_dict]
    raise KeyError(f"unknown profile {name_or_dict!r}; builtins: {sorted(BUILTIN)}")


def describe(profile) -> dict:
    p = get_profile(profile)
    return p.to_dict()


def plan(source_info: dict, profile, question: Optional[str] = None, image_budget: Optional[int] = None) -> dict:
    """Return an ordered list of tool calls. `source_info` is sources.probe() output.

    The plan keeps ~25% of the image budget for follow-ups (depth, crops) and
    fills the rest with keyframes, tiled into sheets when the model would
    otherwise run out of images."""
    p = get_profile(profile)
    dur = float(source_info.get("duration") or 0.0)
    kind = source_info.get("kind")
    q = (question or "").lower()
    wants_depth = any(w in q for w in ("depth", "3d", "space", "distance", "layer", "near", "far", "behind", "front", "parallax", "camera"))
    wants_motion = any(w in q for w in ("motion", "move", "cut", "pace", "timing", "when", "speed"))
    budget = image_budget or p.max_images
    steps: list[dict] = []
    src = source_info["path"]

    if kind == "image":
        steps.append({"tool": "annotate", "args": {"source": src, "t": 0, "style": "som" if source_info.get("comp") else "none"}, "why": "the frame itself, sized to the profile"})
        if wants_depth or budget >= 3:
            steps.append({"tool": "depth", "args": {"source": src, "t": 0, "side_by_side": True}, "why": "relief and a flatness score"})
        return {"profile": p.name, "budget_images": budget, "steps": steps}

    if p.video.native and dur > 0:
        fps = p.video.fps
        if dur * fps > budget * 8:
            fps = max(0.1, round(budget * 8 / dur, 2))
        steps.append({"tool": "native_video", "args": {"source": src, "fps": fps, "start": 0, "end": min(dur, p.video.max_seconds or dur)},
                      "why": f"{p.name} ingests video natively; ask for {'MM:SS' if p.timestamps else 'frame'} references"})
        n_supp = min(6, max(0, budget - 1))
    else:
        n_supp = budget

    frames_wanted = min(max(4, int(dur * 2)), max(1, int(n_supp * 0.75)))
    if not p.video.native:
        # sheets: 6 cells per image at the profile's edge keeps each cell readable (>=400 px)
        cells = 6 if p.long_edge_px >= 1400 else 4
        n_frames = min(frames_wanted * 2, cells * max(1, int(budget * 0.5)))
        strategy = "scene" if wants_motion else "diverse"
        steps.append({"tool": "keyframes", "args": {"source": src, "n": n_frames, "strategy": strategy}, "why": f"{n_frames} frames chosen by {strategy}"})
        steps.append({"tool": "contact_sheet", "args": {"source": src, "n": n_frames, "strategy": strategy, "cols": 3 if cells == 6 else 2, "labels": "timestamp"},
                      "why": "tiled frames with timestamps beat sequences for image-only models"})
    if source_info.get("comp"):
        steps.append({"tool": "annotate", "args": {"source": src, "t": round(dur / 2, 2), "style": "som"}, "why": "node ids as marks so answers bind to real nodes"})
        steps.append({"tool": "scene_text", "args": {"source": src, "t": round(dur / 2, 2)}, "why": "exact node boxes, z-order and motion as text"})
    if wants_depth or n_supp >= 4:
        steps.append({"tool": "depth", "args": {"source": src, "t": round(dur / 2, 2), "side_by_side": True}, "why": "relief on the middle frame"})
    if wants_depth and dur > 0:
        steps.append({"tool": "geometry", "args": {"source": src, "n": 6}, "why": "camera poses, point cloud, near-to-far layers"})
        steps.append({"tool": "render_view", "args": {"geometry_id": "<from the geometry step>", "camera": "iso"}, "why": "a third-person view of the reconstructed scene"})
    return {"profile": p.name, "budget_images": budget, "steps": steps}
