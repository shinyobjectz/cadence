"""Querying a fact log, in the grammar the log is already written in.

A query is a fact with holes in it. `happens(release(A, B), T)` reads every release
event out of a log and tells you what A, B and T were; `holds(visible(E), T0, T1)`
reads every visibility interval. There is no second query language to learn, which is
the point -- an agent that can read the log can query it by pattern-matching on what
it just read.

Three rules:
  * a variable is an unquoted atom starting with a capital, or `_` for "don't care";
  * a quoted string is always a literal, so text(E, "TYPE") matches the word TYPE and
    does not bind a variable named TYPE;
  * a perceived fact carries its `src` line, so every hit knows its producer and
    confidence, and `min_conf` filters on it. An exact fact has conf None and is never
    filtered out -- silence about confidence means certainty, not absence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .facts import parse_log, provenance

VARNAME = re.compile(r"^[A-Z_]\w*$")


class Lit(str):
    """A string that was quoted in the pattern, so never a variable."""


def parse_pattern(s: str):
    """Like facts.parse_term, but keeps quoting so "TYPE" is a literal, not a variable."""
    s = s.strip()
    if s.startswith('"') and s.endswith('"') and len(s) >= 2:
        return Lit(s[1:-1])
    head, sep, rest = s.partition("(")
    if not sep or not s.endswith(")"):
        try:
            return float(s)
        except ValueError:
            return s
    args, depth, cur, q, esc = [], 0, "", False, False
    for ch in rest[:-1]:
        # Quote-aware: a comma inside "a, b" is part of the string, not an argument
        # separator. Without this, any fact carrying prose -- says(), OCR text, a word
        # ending in a comma -- parsed with the wrong arity and silently stopped matching.
        if esc:
            cur += ch
            esc = False
            continue
        if ch == "\\" and q:
            cur += ch
            esc = True
            continue
        if ch == '"':
            q = not q
            cur += ch
            continue
        if not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            if ch == "," and depth == 0:
                args.append(cur)
                cur = ""
                continue
        cur += ch
    if cur.strip():
        args.append(cur)
    return (head.strip(), *[parse_pattern(a) for a in args])


def is_var(x) -> bool:
    return isinstance(x, str) and not isinstance(x, Lit) and bool(VARNAME.match(x))


def unify(pat, fact, bind: dict | None = None):
    """Structural match. Returns bindings, or None if the pattern does not fit."""
    if bind is None:
        bind = {}
    if is_var(pat):
        if pat == "_":
            return bind
        if pat in bind:
            return bind if bind[pat] == fact else None
        out = dict(bind)
        out[pat] = fact
        return out
    if isinstance(pat, tuple):
        if not isinstance(fact, tuple) or len(pat) != len(fact):
            return None
        for p, f in zip(pat, fact):
            bind = unify(p, f, bind)
            if bind is None:
                return None
        return bind
    if isinstance(pat, float) and isinstance(fact, float):
        return bind if abs(pat - fact) < 1e-9 else None
    return bind if pat == fact else None


def term_str(t) -> str:
    """Render a parsed term back to its log form."""
    if isinstance(t, tuple):
        return f"{t[0]}({', '.join(term_str(x) for x in t[1:])})"
    if isinstance(t, float):
        return f"{t:.3f}".rstrip("0").rstrip(".") if t % 1 else f"{t:.3f}"
    if isinstance(t, str):
        return f'"{t}"' if " " in t else t
    return str(t)


@dataclass
class Hit:
    fact: tuple
    bind: dict
    clip: str | None = None
    producer: str | None = None
    conf: float | None = None

    @property
    def exact(self) -> bool:
        return self.producer is None

    def __repr__(self) -> str:
        p = "exact" if self.exact else f"{self.producer} {self.conf:.2f}"
        return f"<{self.clip or '-'} {term_str(self.fact)}. [{p}]>"

    def as_dict(self) -> dict:
        return {"clip": self.clip, "fact": term_str(self.fact) + ".",
                "bind": {k: (v if not isinstance(v, tuple) else term_str(v))
                         for k, v in self.bind.items()},
                "producer": self.producer, "conf": self.conf}


@dataclass
class Log:
    """One parsed fact log -- a comp's or a clip's, they query identically."""
    name: str
    facts: list = field(default_factory=list)
    prov: dict = field(default_factory=dict)

    @classmethod
    def load(cls, path, name: str | None = None) -> "Log":
        p = Path(path)
        return cls.parse(p.read_text(), name or p.stem)

    @classmethod
    def parse(cls, text: str, name: str = "log") -> "Log":
        facts = parse_log(text)
        return cls(name=name, facts=facts, prov=provenance(facts))

    @property
    def body(self) -> list:
        """Every fact except the src lines, which are metadata about the others."""
        return [f for f in self.facts if not (isinstance(f, tuple) and f[0] == "src")]

    def match(self, pattern: str, min_conf: float = 0.0) -> list[Hit]:
        pat = parse_pattern(pattern)
        out = []
        for f in self.body:
            b = unify(pat, f)
            if b is None:
                continue
            prod, conf = self.prov.get(f, (None, None))
            if conf is not None and conf < min_conf:
                continue
            out.append(Hit(fact=f, bind=b, clip=self.name, producer=prod, conf=conf))
        return out

    def at(self, t: float, eps: float = 0.04) -> list[Hit]:
        """Everything true at t: holds intervals containing it, happens within eps."""
        out = []
        for f in self.body:
            if not isinstance(f, tuple):
                continue
            if f[0] == "holds" and len(f) == 4 and f[2] <= t < f[3]:
                pass
            elif f[0] == "happens" and len(f) == 3 and abs(f[2] - t) <= eps:
                pass
            else:
                continue
            prod, conf = self.prov.get(f, (None, None))
            out.append(Hit(fact=f, bind={}, clip=self.name, producer=prod, conf=conf))
        return out

    def when(self, word: str | None = None, event: str | None = None) -> list[float]:
        """Times at which something was said or happened, in log order.

        `when(word="determinism")` is the anchor an agent needs to edit by speech, and
        `when(event="beat")` the one it needs to edit to music. Matching on the word is
        exact after stripping trailing punctuation, because an aligner emits "Lua." for
        the last word of a sentence and an agent asks for "Lua"."""
        out = []
        if word is not None:
            want = word.strip().strip('.,;:!?').lower()
            for h in self.match("happens(word(_, W), T)"):
                got = str(h.bind["W"]).strip('.,;:!?').lower()
                if got == want:
                    out.append(h.bind["T"])
        if event is not None:
            # any arity: release(e1, e2), beat(b3) and a bare `cut` are all events named by
            # their head. Matching a fixed one-argument pattern silently missed every
            # two-party event -- which is most of the interesting ones.
            for f in self.body:
                if (isinstance(f, tuple) and f[0] == "happens" and len(f) == 3
                        and (f[1] == event or (isinstance(f[1], tuple) and f[1][0] == event))):
                    out.append(f[2])
        return out

    def transcript(self) -> str:
        """Everything the log says is spoken, in time order."""
        hits = sorted(self.match("happens(word(_, W), T)"), key=lambda h: h.bind["T"])
        if hits:
            return " ".join(str(h.bind["W"]) for h in hits)
        return " ".join(str(h.bind["S"]) for h in self.match("says(_, S)"))

    def entities(self) -> list[str]:
        return [h.bind["E"] for h in self.match("entity(E, _, _)")] or \
               [h.bind["E"] for h in self.match("entity(E, _)")]

    def span(self) -> tuple[float, float]:
        ts = [x for f in self.body if isinstance(f, tuple)
              for x in (f[2:] if f[0] in ("holds", "happens") else ())
              if isinstance(x, float)]
        return (min(ts), max(ts)) if ts else (0.0, 0.0)


@dataclass
class Corpus:
    """Several logs queried as one, so `which clip shows X` is a single call."""
    logs: list[Log] = field(default_factory=list)

    @classmethod
    def load(cls, paths) -> "Corpus":
        return cls([Log.load(p) for p in paths])

    def match(self, pattern: str, min_conf: float = 0.0) -> list[Hit]:
        return [h for lg in self.logs for h in lg.match(pattern, min_conf)]

    def clips(self, pattern: str, min_conf: float = 0.0) -> list[str]:
        seen, out = set(), []
        for h in self.match(pattern, min_conf):
            if h.clip not in seen:
                seen.add(h.clip)
                out.append(h.clip)
        return out
