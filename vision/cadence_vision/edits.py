"""The edit surface an agent drives: propose, check, apply, prove.

`lower.py` already rewrites a comp from an edit. What is missing for an agent is the
loop around it -- knowing whether the edit is legal *before* writing, and knowing what
it actually did *after*. Both matter more here than in a normal editor, because the
agent cannot look at the result.

Three verification levels, cheapest first:
  none    -- rewrite only.
  facts   -- lift before and after, diff the logs. Says what changed *meaningfully*.
  frames  -- also render both and hash every frame. Says what changed *on screen*, and
             is the only level that can prove an edit touched nothing else.

`write` is False by default. An agent proposes and inspects; committing is a separate,
explicit act.
"""

from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path

from . import facts as F
from . import lower as LO

ROOT = Path(__file__).resolve().parents[2]


def _hashes(comp: Path) -> list[str]:
    r = subprocess.run(["bin/cadence", "hash", str(comp)], cwd=ROOT,
                       capture_output=True, text=True)
    return [ln.split()[2] for ln in r.stdout.splitlines() if ln.startswith("FRAME")]


def _fps(comp: Path) -> float:
    m = re.search(r"\bfps\s*=\s*([0-9.]+)", comp.read_text())
    return float(m.group(1)) if m else 30.0


def validate(comp, edits: list[dict]) -> list[str]:
    """Problems with these edits against this comp. Empty list means they will apply."""
    comp = Path(comp)
    problems = []
    if not comp.exists():
        return [f"no such comp: {comp}"]
    for i, e in enumerate(edits):
        if "verb" not in e:
            problems.append(f"edit {i}: no verb (have {', '.join(sorted(LO.VERBS))})")
        elif e["verb"] not in LO.VERBS:
            problems.append(f"edit {i}: unknown verb {e['verb']!r} "
                            f"(have {', '.join(sorted(LO.VERBS))})")
    if problems:
        return problems
    # Dry run against the real lowering rather than re-deriving its rules here, so the
    # two can never drift apart. Nothing is written: lower() returns a string.
    try:
        LO.lower(comp, edits)
    except Exception as ex:                                   # noqa: BLE001 -- reported, not raised
        problems.append(f"{type(ex).__name__}: {ex}")
    return problems


def _fact_diff(before: str, after: str) -> list[dict]:
    b, a = before.splitlines(), after.splitlines()
    out = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, b, a).get_opcodes():
        if tag == "equal":
            continue
        out.append({"op": tag,
                    "before": [x for x in b[i1:i2] if x.strip()],
                    "after": [x for x in a[j1:j2] if x.strip()]})
    return out


def apply(comp, edits: list[dict], verify: str = "facts", write: bool = False) -> dict:
    """Apply edits and report what they did. See the module docstring for `verify`."""
    comp = Path(comp)
    problems = validate(comp, edits)
    if problems:
        return {"ok": False, "problems": problems}

    src_before = comp.read_text()
    src_after = LO.lower(comp, edits)
    res = {
        "ok": True,
        "comp": str(comp),
        "edits": edits,
        "written": False,
        "lua_diff": [ln for ln in difflib.unified_diff(
            src_before.splitlines(), src_after.splitlines(),
            "before", "after", lineterm="", n=2)],
    }

    if verify in ("facts", "frames"):
        # The edited comp is hashed and lifted beside the original so every relative
        # asset path resolves identically; a temp dir elsewhere would not.
        tmp = comp.with_name(comp.stem + ".__edit__.lua")
        try:
            tmp.write_text(src_after)
            fb, fa = F.lift(comp), F.lift(tmp)
            # the lifter stamps the filename into its header, which is not a change
            fa = fa.replace(tmp.name, comp.name).replace(tmp.stem, comp.stem)
            res["fact_diff"] = _fact_diff(fb, fa)
            if verify == "frames":
                hb, ha = _hashes(comp), _hashes(tmp)
                if not hb or len(hb) != len(ha):
                    res["frames"] = {"error": f"hash mismatch: {len(hb)} vs {len(ha)} frames"}
                else:
                    fps = _fps(comp)
                    ch = [i for i, (x, y) in enumerate(zip(hb, ha)) if x != y]
                    res["frames"] = {
                        "n": len(hb), "changed": ch, "identical": len(hb) - len(ch),
                        "span": [round(ch[0] / fps, 3), round(ch[-1] / fps, 3)] if ch else None,
                        "contiguous": bool(ch) and ch == list(range(ch[0], ch[-1] + 1)),
                    }
        finally:
            tmp.unlink(missing_ok=True)

    if write:
        comp.write_text(src_after)
        res["written"] = True
    return res
