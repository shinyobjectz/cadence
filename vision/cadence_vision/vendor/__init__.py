"""Vendored, pinned third-party code. Apache-2.0.

`mp_palmdet.py` and `mp_handpose.py` come from the OpenCV Zoo's MediaPipe ports:

    opencv/palm_detection_mediapipe        @ 233e619dcea1 (2025-06-20)
    opencv/handpose_estimation_mediapipe   @ 4b2a0b446e5c (2025-06-20)

They are vendored rather than fetched at run time because they are *code*: the ONNX weights beside
them in `vision/cache/models` are data and are downloaded like every other model here, but
importing Python that was pulled off the network at run time is a different thing entirely.

One edit, in `mp_palmdet.py`: `_load_anchors` returned a 2000-line literal table, and it is now
generated. The two are identical to 7.5e-09 — the grid is 24x24 with 2 anchors per cell followed by
12x12 with 6, each anchor at the centre of its cell, which is what those 2016 rows spell out.
"""
