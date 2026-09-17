"""CLIP embeddings of the low-res scan strip (open_clip ViT-B-32, laion2b),
cached per source. Optional: keyframes falls back to pixel thumbnails when
open_clip is not installed."""

from __future__ import annotations

import hashlib
from functools import lru_cache

import numpy as np
from PIL import Image

from . import CACHE
from .depth import quiet, device

MODEL = ("ViT-B-32", "laion2b_s34b_b79k")


def available() -> bool:
    try:
        import open_clip  # noqa: F401
        return True
    except Exception:
        return False


@lru_cache(maxsize=1)
def _load():
    import open_clip
    import torch
    with quiet():
        model, _, pre = open_clip.create_model_and_transforms(MODEL[0], pretrained=MODEL[1])
        tok = open_clip.get_tokenizer(MODEL[0])
    dev = device()
    model = model.to(dev).eval()
    return model, pre, tok, dev, torch


def embed_frames(frames: np.ndarray, batch: int = 64) -> np.ndarray:
    """frames [N,h,w,3] uint8 -> unit vectors [N,512] float32."""
    model, pre, _, dev, torch = _load()
    out = []
    with quiet(), torch.no_grad():
        for i in range(0, len(frames), batch):
            x = torch.stack([pre(Image.fromarray(f)) for f in frames[i:i + batch]]).to(dev)
            e = model.encode_image(x).float()
            out.append((e / e.norm(dim=-1, keepdim=True)).cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 512), np.float32)


def embed_text(q: str) -> np.ndarray:
    model, _, tok, dev, torch = _load()
    with quiet(), torch.no_grad():
        e = model.encode_text(tok([q]).to(dev)).float()
        return (e / e.norm(dim=-1, keepdim=True)).cpu().numpy()[0]


def strip_embeddings(media_key: str, frames: np.ndarray) -> np.ndarray:
    p = CACHE / f"clip-{hashlib.sha1((media_key + MODEL[1]).encode()).hexdigest()[:16]}.npy"
    if p.exists():
        e = np.load(p)
        if len(e) == len(frames):
            return e
    e = embed_frames(frames)
    np.save(p, e)
    return e
