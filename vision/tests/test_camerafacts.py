"""G2: dolly against zoom, and the refusal to guess between them.

In 2D a dolly and a zoom are the same transform. Only the lens can tell them apart, so these
cover two things: that the signed focal trend actually separates the pair, and that with no
intrinsics the producer says `unknown` rather than picking one.

The measurements behind the constants, all on real pixels:

    case                         DA3 focal ratio   focal_rise
    zoom  (crop 1.00 -> 1.85)            1.22        +0.14
    still (same source, no zoom)         1.00        +0.01
    dolly (real camera move)             0.92        -0.06

DA3 badly underestimates the magnitude — 1.22 for a zoom that is 1.85 by construction — so only
the sign is used. The earlier rule tested `focal_spread`, a std/mean that cannot tell a rise from
a fall, and scored the true zoom at 0.053 against its own 0.08 bar: it never fired.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "vision"))

from cadence_vision import camerafacts as C  # noqa: E402


def _scaling(n=12, ls=0.12):
    """Samples that register as a scale change and nothing else."""
    return [{"t": i / 5.0, "dx": 0.0, "dy": 0.0, "ls": ls, "pos": None,
             "cov": 1.0, "n": 4000} for i in range(n)]


def _focal(rise):
    return {"focal_spread": 0.05, "focal_slope": rise, "focal_rise": rise,
            "depth_slope": -0.03, "depth_range": 0.1}


def _labels(recs, focal):
    return {lab for _, lab in C.classify(recs, fps_s=5.0, focal=focal)}


def test_a_scale_change_with_no_intrinsics_is_unknown_never_dolly():
    """G2's second clause. Saying `dolly` here is the 2D estimator claiming 3D."""
    labs = _labels(_scaling(), None)
    assert labs == {"unknown"}, labs
    assert not any("dolly" in l for l in labs)


def test_a_rising_focal_makes_the_scale_change_a_zoom():
    labs = _labels(_scaling(), _focal(+0.19))
    assert any(l.startswith("zoom(") for l in labs), labs


def test_a_falling_focal_makes_the_scale_change_a_dolly():
    labs = _labels(_scaling(), _focal(-0.08))
    assert any(l.startswith("dolly(") for l in labs), labs


def test_a_flat_focal_is_a_dolly_not_a_zoom():
    """A static lens with the scene growing is the camera moving."""
    labs = _labels(_scaling(), _focal(0.0))
    assert any(l.startswith("dolly(") for l in labs), labs
    assert not any(l.startswith("zoom(") for l in labs), labs


def test_zoom_and_dolly_sit_either_side_of_the_threshold():
    """The measured cases are not near the bar: +0.19 and -0.08 against 0.10."""
    assert 0.0 < C.ZOOM_FOCAL_RISE < 0.19
    assert _labels(_scaling(), _focal(C.ZOOM_FOCAL_RISE + 0.01)) != \
           _labels(_scaling(), _focal(C.ZOOM_FOCAL_RISE - 0.01))


def test_focal_rise_is_signed_where_spread_is_not():
    """The bug in one line: spread cannot tell a rise from a fall."""
    up = np.array([400.0, 450, 500, 550])
    down = up[::-1]
    def trend(f):
        slope = float(np.polyfit(range(len(f)), f, 1)[0])
        return slope * (len(f) - 1) / f.mean(), float(f.std() / f.mean())
    rise_up, spread_up = trend(up)
    rise_dn, spread_dn = trend(down)
    assert rise_up > 0 > rise_dn
    assert spread_up == pytest.approx(spread_dn), "spread is sign-blind, which was the bug"
