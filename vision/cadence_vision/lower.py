"""Lowering: apply an edit expressed against a fact log back to the comp that produced it.

A fact log is deliberately lossy — "the left third", not x=80 — so a comp is never *regenerated*
from facts. Instead an edit names facts, and the lowering rewrites the source that produced them.
The exactness claim is therefore about the edit, not about the log:

    lower(comp, [])            is the identity (byte-for-byte, so every frame hash holds)
    lower(comp, [edit])        changes exactly what the edit named, and re-lifting proves it

Node ids in a lifted log are Cadence's own (`kind .. count`, assigned in construction order), so
they map back to constructor calls by counting them in source order — no source annotations needed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CTOR = re.compile(r"(\w+)\s*:\s*(\w+)\s*(\{)")
TWEEN = re.compile(r"\bt\s*:\s*tween\s*\(")


def _match_brace(src: str, i: int) -> int:
    """Index just past the '}' matching the '{' at i, skipping strings and comments."""
    depth, j, n = 0, i, len(src)
    while j < n:
        ch = src[j]
        if ch == "-" and src[j:j + 2] == "--":
            j = src.find("\n", j)
            if j < 0:
                return n
        elif ch in "\"'":
            q, j = ch, j + 1
            while j < n and src[j] != q:
                j += 2 if src[j] == "\\" else 1
        elif src[j:j + 2] == "[[":
            k = src.find("]]", j)
            j = n if k < 0 else k + 1
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return n


@dataclass
class Span:
    id: str
    kind: str
    start: int          # index of the '{'
    end: int            # index just past the matching '}'
    var: str | None     # local name it was assigned to, if any


def node_spans(src: str, ids: list[str] | None = None) -> dict[str, Span]:
    """{node_id: Span} for every constructor call in the source.

    `ids` is the authoritative ordered id list from props(); constructors and nodes are aligned
    positionally, which is what sugar like `s:captions{}` needs — it builds a *text* node, so its
    id is text6, not captions6. Without `ids` the fallback replays Cadence's `kind .. count` rule,
    which is right only when every constructor names its own kind."""
    out: dict[str, Span] = {}
    count = 0
    seq: list[Span] = []
    for m in CTOR.finditer(src):
        recv, kind, _ = m.groups()
        if recv in ("t", "e", "math", "string", "table"):      # timeline / library calls, not nodes
            continue
        start = m.start(3)
        end = _match_brace(src, start)
        count += 1
        body = src[start:end]
        idm = re.search(r"\bid\s*=\s*\"([^\"]+)\"", body)
        nid = idm.group(1) if idm else f"{kind}{count}"
        line_start = src.rfind("\n", 0, m.start()) + 1
        var = re.match(r"\s*local\s+(\w+)\s*=", src[line_start:m.start()])
        seq.append(Span(nid, kind, start, end, var.group(1) if var else None))
    if ids is not None:
        if len(ids) != len(seq):
            raise ValueError(f"source has {len(seq)} constructor calls but the comp has {len(ids)} nodes; "
                             "one of them does not build exactly one node, so ids cannot be aligned")
        seq = [Span(nid, sp.kind, sp.start, sp.end, sp.var) for nid, sp in zip(ids, seq)]
    for sp in seq:
        out[sp.id] = sp
    return out


def _set_field(body: str, key: str, value: str) -> tuple[str, bool]:
    """Replace `key = <value>` inside a constructor body, at depth 0 only."""
    for m in re.finditer(rf"\b{re.escape(key)}\s*=\s*", body):
        head = body[:m.start()]
        if head.count("{") - head.count("}") != 1:            # nested table, not this node's field
            continue
        rest = body[m.end():]
        if rest[:1] in "\"'":
            q = rest[0]
            j = 1
            while j < len(rest) and rest[j] != q:
                j += 2 if rest[j] == "\\" else 1
            j += 1
        else:
            j = 0
            depth = 0
            while j < len(rest):
                c = rest[j]
                if c in "{(":
                    depth += 1
                elif c in "})":
                    if depth == 0:
                        break
                    depth -= 1
                elif c == "," and depth == 0:
                    break
                j += 1
        return body[:m.end()] + value + rest[j:], True
    return body, False


# ----------------------------------------------------------------------------- edit verbs

def comp_ids(comp: Path) -> list[str]:
    """Node ids in construction order, from the comp itself."""
    from .annotate import props
    return [n["id"] for n in props(Path(comp), [0.0])["nodes"]]


def set_prop(src: str, node: str, key: str, value: str, ids: list[str] | None = None) -> str:
    """`entity.key = value` on the node's constructor; inserts the field when absent."""
    spans = node_spans(src, ids)
    if node not in spans:
        raise KeyError(f"no node {node!r} in source (have {', '.join(sorted(spans))})")
    sp = spans[node]
    body = src[sp.start:sp.end]
    new, hit = _set_field(body, key, value)
    if not hit:
        new = body[:1] + f" {key} = {value}," + body[1:]
    return src[:sp.start] + new + src[sp.end:]


def set_ease(src: str, node: str, ease: str, occurrence: int = 0, ids: list[str] | None = None) -> str:
    """Replace the ease name of the tween that drives `node` (its local variable)."""
    spans = node_spans(src, ids)
    sp = spans.get(node)
    if sp is None:
        raise KeyError(f"no node {node!r} (have {', '.join(sorted(spans))})")
    if not sp.var:
        raise KeyError(f"node {node!r} is not held in a local, so no tween can name it")
    hits = [m for m in TWEEN.finditer(src)
            if re.match(rf"\s*{re.escape(sp.var)}\s*,", src[m.end():])]
    if occurrence >= len(hits):
        raise IndexError(f"{sp.var} has {len(hits)} tween(s), asked for #{occurrence}")
    m = hits[occurrence]
    end = _match_brace(src, src.index("{", m.end()))
    tail = src[end:src.index(")", end) + 1]
    newtail, hit = (re.sub(r"\"[A-Za-z]+\"", f'"{ease}"', tail, count=1), '"' in tail)
    if not hit:
        newtail = tail[:-1].rstrip().rstrip(",") + f', "{ease}")'
    return src[:end] + newtail + src[src.index(")", end) + 1:]


def set_tween_duration(src: str, node: str, seconds: float, occurrence: int = 0,
                       ids: list[str] | None = None) -> str:
    spans = node_spans(src, ids)
    sp = spans.get(node)
    if sp is None:
        raise KeyError(f"no node {node!r} (have {', '.join(sorted(spans))})")
    if not sp.var:
        raise KeyError(f"node {node!r} is not held in a local")
    hits = [m for m in TWEEN.finditer(src)
            if re.match(rf"\s*{re.escape(sp.var)}\s*,", src[m.end():])]
    m = hits[occurrence]
    arg = re.match(rf"(\s*{re.escape(sp.var)}\s*,\s*)([0-9.]+)", src[m.end():])
    if not arg:
        raise ValueError("tween duration is not a literal, refusing to guess")
    a, b = m.end() + arg.start(2), m.end() + arg.end(2)
    return src[:a] + f"{seconds:g}" + src[b:]


def set_cue(src: str, node: str, index: int, t0: float | None = None, t1: float | None = None,
            text: str | None = None, ids: list[str] | None = None) -> str:
    """Retime or reword one cue of a captions node: `{ 0.55, 1.45, "Hold the cut." }`."""
    spans = node_spans(src, ids)
    sp = spans.get(node)
    if sp is None:
        raise KeyError(f"no node {node!r} (have {', '.join(sorted(spans))})")
    body = src[sp.start:sp.end]
    cues = [m for m in re.finditer(r"\{\s*([0-9.]+)\s*,\s*([0-9.]+)\s*,\s*\"((?:[^\"\\]|\\.)*)\"\s*\}", body)]
    if index >= len(cues):
        raise IndexError(f"{node} has {len(cues)} cues, asked for #{index}")
    m = cues[index]
    a = t0 if t0 is not None else float(m.group(1))
    b = t1 if t1 is not None else float(m.group(2))
    s = text if text is not None else m.group(3)
    new = body[:m.start()] + f'{{ {a:g}, {b:g}, "{s}" }}' + body[m.end():]
    return src[:sp.start] + new + src[sp.end:]


VERBS = {"set_prop": set_prop, "set_ease": set_ease,
         "set_tween_duration": set_tween_duration, "set_cue": set_cue}


def lower(comp: Path, edits: list[dict]) -> str:
    """Apply edits in order. With no edits this returns the source unchanged, byte for byte."""
    src = Path(comp).read_text()
    if not edits:
        return src
    ids = comp_ids(comp)
    for e in edits:
        verb = dict(e)
        fn = VERBS[verb.pop("verb")]
        src = fn(src, ids=ids, **verb)
    return src
