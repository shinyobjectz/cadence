"""Stage timings for the perception pipeline, on when CADENCE_PROFILE=1 (the renderer's switch).

Each stage records wall time, how many times it ran, and the process's peak resident memory
when it finished. On Apple silicon the GPU shares that memory, so MPS allocations are read
separately: `mps` is what the Metal driver holds for this process at the end of the stage.
Nested stages are recorded too, so `track` can be split into detection and propagation.

    with stage("sam2.propagate"):
        ...
    print(report())
"""
from __future__ import annotations

import os
import resource
import sys
import time
from contextlib import contextmanager

ON = os.environ.get("CADENCE_PROFILE") == "1"
_T: dict[str, list[float]] = {}           # name -> [seconds, calls, peak_rss_mb, mps_mb]
_ORDER: list[str] = []


def _rss_mb() -> float:
    r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return r / 2**20 if sys.platform == "darwin" else r / 2**10     # bytes on macOS, KiB on Linux


def _mps_mb() -> float:
    t = sys.modules.get("torch")
    try:
        return t.mps.driver_allocated_memory() / 2**20 if t is not None and t.backends.mps.is_available() else 0.0
    except Exception:                                   # noqa: BLE001
        return 0.0


@contextmanager
def stage(name: str):
    if not ON:
        yield
        return
    t0 = time.perf_counter()
    try:
        yield
    finally:
        dt = time.perf_counter() - t0
        rec = _T.get(name)
        if rec is None:
            rec = _T[name] = [0.0, 0, 0.0, 0.0]
            _ORDER.append(name)
        rec[0] += dt
        rec[1] += 1
        rec[2] = max(rec[2], _rss_mb())
        rec[3] = max(rec[3], _mps_mb())


def timings() -> list[dict]:
    return [{"stage": n, "seconds": round(_T[n][0], 3), "calls": _T[n][1],
             "peak_rss_mb": round(_T[n][2]), "mps_mb": round(_T[n][3])} for n in _ORDER]


def reset() -> None:
    _T.clear()
    _ORDER.clear()


def report() -> str:
    rows = timings()
    if not rows:
        return ""
    w = max(len(r["stage"]) for r in rows)
    out = [f"{'stage':<{w}}  {'sec':>8}  {'calls':>5}  {'rss MB':>7}  {'mps MB':>7}"]
    out += [f"{r['stage']:<{w}}  {r['seconds']:>8.2f}  {r['calls']:>5}  {r['peak_rss_mb']:>7}  {r['mps_mb']:>7}"
            for r in rows]
    return "\n".join(out)


def timed(name: str):
    """Decorator form of `stage`, for a model's entry point."""
    def wrap(fn):
        import functools

        @functools.wraps(fn)
        def inner(*a, **k):
            with stage(name):
                return fn(*a, **k)
        return inner
    return wrap
