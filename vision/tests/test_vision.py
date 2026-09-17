"""Smoke tests for cadence-vision. The model-free tools run always; depth and
geometry run only when Depth Anything 3 weights are already cached
(CADENCE_VISION_MODELS=1 forces them and downloads if needed).

  bin/cadence-vision test
"""

import json
import os
import shutil
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from cadence_vision import ROOT, CACHE, server, profiles, keyframes as KF, sheet as SH, annotate as AN, diff as DF
from cadence_vision.sources import open_source, frame_at, scan

COMP = ROOT / "evals" / "cases" / "primitives.lua"
CAMERA = ROOT / "evals" / "cases" / "camera.lua"
NEED_TOOLCHAIN = pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("luajit")), reason="ffmpeg + luajit required")


def _models_available() -> bool:
    if os.environ.get("CADENCE_VISION_MODELS") == "1":
        return True
    hub = Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"
    return (hub / "models--depth-anything--DA3-SMALL").exists()


NEED_MODELS = pytest.mark.skipif(not _models_available(), reason="DA3 weights not cached (set CADENCE_VISION_MODELS=1)")


def test_profiles_roundtrip():
    for name in profiles.BUILTIN:
        d = profiles.describe(name)
        assert d["max_images"] > 0 and d["long_edge_px"] > 0
    custom = profiles.get_profile({"name": "claude", "max_images": 5, "video": {"native": True, "fps": 0.5}})
    assert custom.max_images == 5 and custom.video.native and custom.video.fps == 0.5


def test_plan_fits_budget():
    info = {"path": "x.mp4", "kind": "video", "duration": 30.0, "comp": None}
    for name in profiles.BUILTIN:
        plan = profiles.plan(info, name, "where does the camera move")
        tools = [s["tool"] for s in plan["steps"]]
        assert "depth" in tools and "geometry" in tools
        if profiles.BUILTIN[name].video.native:
            assert tools[0] == "native_video"
        else:
            assert "contact_sheet" in tools
            n = next(s["args"]["n"] for s in plan["steps"] if s["tool"] == "keyframes")
            assert n <= 6 * plan["budget_images"]


def test_keyframe_strategies_synthetic(tmp_path):
    # 3 "shots": 40 frames black, 40 white, 40 grey, with a moving dot in the middle shot
    frames = np.zeros((120, 36, 64, 3), np.uint8)
    frames[40:80] = 255
    frames[80:] = 128
    for i in range(40, 80):
        frames[i, 10:20, (i - 40):(i - 40) + 8] = 0
    times = np.arange(120) / 4.0

    class S:  # minimal Source stand-in
        kind = "video"; duration = 30.0; fps = 4.0; media = tmp_path / "x.mp4"; width = 64; height = 36

    import cadence_vision.keyframes as kf
    kf.scan = lambda src, sample_fps=4.0, edge=160: (times, frames)   # monkeypatch the strip
    r = kf.pick(S(), 3, "scene")
    assert r["scores"]["cuts_at"] == [10.0, 20.0]
    assert r["times"][:3] == [0.0, 10.0, 20.0]
    r = kf.pick(S(), 4, "motion")
    assert sum(10 <= t < 20 for t in r["times"]) >= 2, "motion strategy should favour the moving shot"
    r = kf.pick(S(), 3, "diverse")
    assert len(set(int(t // 10) for t in r["times"])) == 3, "diverse should cover all three looks"
    r = kf.pick(S(), 5, "uniform")
    assert r["times"] == [3.0, 9.0, 15.0, 21.0, 27.0]


def test_sheet_and_marks_pure():
    frames = [np.full((90, 160, 3), c, np.uint8) for c in (30, 90, 150, 210)]
    im = SH.build(frames, [0, 1, 2, 3], cols=2, labels="timestamp", long_edge=800)
    assert max(im.size) == 800 and im.size[0] == 800
    marked = AN.draw_marks(Image.fromarray(frames[0]), [{"id": "a", "x": 10, "y": 10, "w": 50, "h": 30}], "som", 1.0)
    assert np.asarray(marked).std() > 0
    heat, stats = DF.pixel(frames[0], frames[3])
    assert stats["changed_fraction"] == 1.0
    heat, stats = DF.pixel(frames[0], frames[0])
    assert stats["changed_fraction"] == 0.0


@NEED_TOOLCHAIN
def test_comp_marks_bind_to_nodes():
    src = open_source(COMP)
    assert src.kind == "comp" and src.duration > 0
    marks, nb = AN.comp_marks(src, 1.5)
    ids = {m["id"] for m in marks}
    assert {"text1", "circle2", "rect3", "text4"} <= ids
    c = next(m for m in marks if m["id"] == "circle2")
    assert abs(c["w"] - c["h"]) < 1e-6 and c["w"] > 0
    fr = frame_at(src, 1.5)
    assert fr.shape == (720, 1280, 3)


@NEED_TOOLCHAIN
def test_server_tools_end_to_end():
    out = json.loads(server.probe(str(COMP)))
    assert out["kind"] == "video" and any(n["id"] == "circle2" for n in out["nodes"])
    plan = json.loads(server.plan_view(str(COMP), "claude"))
    assert plan["steps"][0]["tool"] == "keyframes"
    kf = json.loads(server.keyframes(str(COMP), 4, "uniform"))
    assert len(kf["times"]) == 4
    txt, img = server.contact_sheet(str(COMP), 4, "uniform", 2)
    assert json.loads(txt)["cols"] == 2 and img.data[:8] == b"\x89PNG\r\n\x1a\n"
    txt, img = server.annotate(str(COMP), 1.5)
    legend = json.loads(txt)["legend"]
    assert {"mark", "id", "kind", "box"} <= set(legend[0])
    st = json.loads(server.scene_text(str(COMP), 1.5, 1.9))
    assert st["nodes"]["nodes"] and "motion" in st
    txt, img = server.diff(str(CAMERA), 0.5, 2.5, "flow")
    assert json.loads(txt)["mean_flow_px"] > 0


@NEED_TOOLCHAIN
@NEED_MODELS
def test_depth_and_geometry():
    txt, img = server.depth(str(CAMERA), 1.5)
    d = json.loads(txt)
    assert d["nodes"]["rect5"]["relief"] > d["nodes"]["rect4"]["relief"], "the nearer perspective plane must read nearer"
    flat = json.loads(server.depth(str(ROOT / "evals" / "cases" / "blend_modes.lua"), 1.0)[0])
    assert flat["flat"] is True
    g = json.loads(server.geometry(str(CAMERA), times="0.5,1.5,2.5"))
    assert g["views"] == 3 and g["points"] > 1000
    txt, img = server.render_view(g["geometry_id"], "top")
    assert img.data[:4] == b"\x89PNG"
