"""Call any cadence-vision tool from the shell (same functions the MCP exposes).

  vision/.venv/bin/python vision/cli.py <tool> key=value ...
  bin/cadence-vision call contact_sheet source=evals/cases/camera.lua n=6

JSON text is printed; images are written to vision/cache and their paths printed."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from cadence_vision import server, CACHE  # noqa: E402


def _coerce(v: str):
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        return v


def main(argv: list[str]) -> int:
    if not argv or argv[0] in ("-h", "--help", "list"):
        print("tools:", ", ".join(sorted(t.name for t in server.mcp._tool_manager.list_tools())))
        return 0
    name, kv = argv[0], argv[1:]
    fn = getattr(server, name, None)
    if fn is None:
        print(f"unknown tool {name}", file=sys.stderr); return 2
    # the decorator returns the plain function; nothing to unwrap
    fn = getattr(fn, "fn", fn)
    kwargs = {}
    for a in kv:
        k, _, v = a.partition("=")
        kwargs[k] = _coerce(v)
    import contextlib, os
    real = os.fdopen(os.dup(1), "w")
    with contextlib.redirect_stdout(sys.stderr):
        out = fn(**kwargs)
    print = real.write and (lambda *a: (real.write(" ".join(str(x) for x in a) + "\n"), real.flush()))  # noqa: E731
    if isinstance(out, list):
        for item in out:
            if isinstance(item, str):
                print(item)
            else:
                # locate the PNG written beside the image payload: newest file in cache with matching size
                pngs = sorted(CACHE.glob("*.png"), key=lambda p: p.stat().st_mtime)
                print("image:", pngs[-1] if pngs else "<in-memory>")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
