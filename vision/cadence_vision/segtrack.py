"""Box-prompted video object tracking: one mask per frame, at the video's own resolution.

EdgeTAM is the default. It is SAM 2 with the memory attention replaced by a small perceiver, and
that memory attention is exactly where SAM 2 spent its time here: on this M4 the ultralytics SAM 2.1
small ran 1.2-2.1 s/frame at imgsz 1024 (barely ahead of the CPU), EdgeTAM 0.21-0.29 s/frame on
MPS. It is also the more accurate of the two on hand-labelled masks: DAVIS-2017 val, 8 sequences x
40 frames, prompted with the true box on frame 0 -- J 0.946 against 0.924, boundary F at 2 px 0.892
against 0.865, ahead on 7 of 8 (`vision/cache/bench/davis_eval.py`, docs/FACTS.md).

`CADENCE_TRACKER=sam2` restores the old path, for comparing or if transformers is missing.

Depth was tried as an edge refiner on these masks (snap the outline to DA3 depth edges, gated on
the depth difference, on the tracker's own uncertainty, at 504 and 756 px) and lowered boundary F
in every variant on DAVIS. Depth belongs to z-order, not to outlines.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from .profile import stage

TRACKER = os.environ.get("CADENCE_TRACKER", "edgetam")
EDGETAM = "yonigozlan/EdgeTAM-hf"
POOL_CAP = int(float(os.environ.get("CADENCE_MPS_POOL_GB", "3")) * 2**30)


def _device() -> str:
    import torch
    return "mps" if torch.backends.mps.is_available() else "cuda" if torch.cuda.is_available() else "cpu"


@lru_cache(maxsize=1)
def _edgetam():
    # fp32: fp16 measured no faster on MPS (0.196 vs 0.209 s/frame) and is one more thing to doubt.
    import torch
    from transformers import EdgeTamVideoModel, Sam2VideoProcessor
    with stage("edgetam.load"):
        dev = _device()
        model = EdgeTamVideoModel.from_pretrained(EDGETAM, dtype=torch.float32).to(dev).eval()
        return model, Sam2VideoProcessor.from_pretrained(EDGETAM), dev


def _frames(video: Path) -> list[np.ndarray]:
    cap = cv2.VideoCapture(str(video))
    out = []
    while True:
        ok, f = cap.read()
        if not ok:
            break
        out.append(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
    cap.release()
    return out


def masks(video: Path, box_px: list[float], tracker: str | None = None) -> Iterator[tuple[int, np.ndarray]]:
    """Yield (frame index, bool mask HxW) for every frame, the box prompting frame 0.

    `tracker` overrides CADENCE_TRACKER for one call, for comparing the two on the same input."""
    if (tracker or TRACKER) == "sam2":
        yield from _sam2(video, box_px)
        return
    import torch
    frames = _frames(video)
    if not frames:
        return
    model, proc, dev = _edgetam()
    H, W = frames[0].shape[:2]
    sess = proc.init_video_session(video=frames, inference_device=dev, dtype=torch.float32)
    proc.add_inputs_to_inference_session(inference_session=sess, frame_idx=0, obj_ids=1,
                                         input_boxes=[[[float(v) for v in box_px]]])
    try:
        with torch.inference_mode():
            with stage("edgetam.frame"):
                model(inference_session=sess, frame_idx=0)      # condition on the prompt before propagating
            it = model.propagate_in_video_iterator(sess)
            while True:
                with stage("edgetam.frame"):
                    out = next(it, None)
                    if out is None:
                        return
                    lg = proc.post_process_masks([out.pred_masks], original_sizes=[[H, W]], binarize=False)[0]
                    m = (lg[0, 0] > 0).cpu().numpy()
                yield out.frame_idx, m
    finally:
        # Live memory holds at ~1 GB through a 70-frame session, but MPS keeps what a session freed in
        # its pool (2.85 GB after one, 7.7 GB across a clip's worth); on 16 GB of shared memory that
        # pool is taken from everything else. Emptying it after every session capped the pool at
        # 4.5 GB but made frames ~30% slower, since each session then re-grows it; so it is handed
        # back only once it passes POOL_CAP.
        del sess
        if dev == "mps" and torch.mps.driver_allocated_memory() > POOL_CAP:
            torch.mps.empty_cache()


def _sam2(video: Path, box_px: list[float], imgsz: int = 1024) -> Iterator[tuple[int, np.ndarray]]:
    from ultralytics.models.sam import SAM2VideoPredictor

    from .tracking import DEVICE, SAM_WEIGHTS, _timed, weights
    pred = SAM2VideoPredictor(overrides=dict(conf=0.25, task="segment", mode="predict", imgsz=imgsz,
                                             model=weights(SAM_WEIGHTS), verbose=False, save=False,
                                             device=DEVICE))
    for fi, r in enumerate(_timed(pred(source=str(video), bboxes=[box_px], stream=True), "sam2.frame")):
        if r.masks is not None and len(r.masks.data):
            m = r.masks.data[0].cpu().numpy().astype(np.uint8)
            h, w = r.orig_shape
            if m.shape != (h, w):
                m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
            yield fi, m.astype(bool)
