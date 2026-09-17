"""A role for every mathematical element, and a citable definition span for the ones that define.

The concept behind the discovery round: *every* ``<mml:math>`` element of a
paper has a place. Deterministically, from the discovery record alone:

* ``formula``    — a display equation, or an inline relation the model must
                   take as a constraint or objective;
* ``index``      — an index declaration in the prose (``i \\in I``, ``\\forall``);
* ``domain``     — a domain statement (``x \\in \\{0,1\\}``, ``t \\ge 0``);
* ``definition`` — a symbol mention that the surrounding sentence defines
                   ("Let :math:`x_{ij}` be ...", ":math:`E` is the set of ..."),
                   a notation-table row, or a definition-list item;
* ``mention``    — a symbol mention that only refers to the symbol;
* ``other``      — numbers, worked examples, captions, table data.

A ``definition`` carries a **span**: the verbatim sentence (paragraph index +
character offsets into the paragraph's plain text, see ``discover``) that
defines the symbol, and whether the defining words come *before* or *after*
the element. The span is what a Details pane cites; nothing is paraphrased.
The span boundaries are proposed here by sentence rules; a model may refine
them later (``assist`` stage d) and the human decides on the page. Provenance
travels with every span (``source``: rule / model / human).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from corpusbuilder.discover import DISCOVERY
from corpusbuilder.promote import CORPUS

ROLES = ("formula", "definition", "index", "domain", "mention", "other")
REPORT_JSON = CORPUS / "roles.json"
REPORT_MD = CORPUS / "roles.md"

#: Sentence enders: a period/question/exclamation, then space, then a capital,
#: a math placeholder, a bullet or a paragraph mark. Abbreviations and
#: decimals do not end a sentence.
_SENTENCE_END = re.compile(
    r"(?<!\b[Ee]\.g)(?<!\b[Ii]\.e)(?<!\bEq)(?<!\bFig)(?<!\bSec)(?<!\bcf)(?<!\bvs)(?<!\bNo)(?<!\bet al)(?<=[.!?;])\s+(?=[A-Z⟨•¶(\"“])"
)
_DEF_AFTER = re.compile(
    r"^[\s,)]*(?:\(\s*[^()]{0,40}\)\s*)?(?:is|are|be|denotes?|represents?|indicates?|stands? for|means?|gives?|refers? to|equals?|defines?|describes?|corresponds? to|expresses?|measures?|counts?)\b|^\s*(?::|=|—|-|–)\s*(?:the|a|an)\b",
    re.IGNORECASE,
)
_DEF_BEFORE = re.compile(
    r"(?:\blet\b|\bdefine[sd]?\b|\bdenoted?\s+(?:by|as)\b|\bwritten as\b|\brepresented by\b|\bwhere\b|\bwe (?:use|introduce|denote|define|write|let)\b|\bindicated by\b|\bcalled\b|\bnamely\b|\bi\.e\.|\bby\b|\bas\b|\bvariable[s]?\b|\bparameter[s]?\b|\bset[s]?\b|\bindex\b|\bindices\b|\bnumber of\b|\bconstant[s]?\b|\bcoefficient[s]?\b)\W*(?:\([^()]{0,40}\)\s*)?$",
    re.IGNORECASE,
)
_IN_PARENS = re.compile(r"\((?:i\.e\.|e\.g\.|see|cf\.|resp\.)?[^()]*⟨[^⟩]*⟩[^()]*\)$")
_INEQ = re.compile(r"\\(?:le|ge|leq|geq|neq|ne)(?![A-Za-z])|[<>≤≥≠]")
_MEMBERSHIP = re.compile(
    r"^\s*(?:\\forall\s*)?\(?[A-Za-z](?:\s*,\s*[A-Za-z])*\)?\s*(?:\\in|∈)\s*[^=<>≤≥]+$"
)
_DOMAIN = re.compile(
    r"\\in\s*\\?\{\s*0\s*,\s*1\s*\\?\}|∈\s*\{\s*0\s*,\s*1\s*\}|\\in\s*\\mathbb|∈\s*ℤ|∈\s*ℝ|∈\s*ℕ|\\(?:ge|geq)\s*0\s*$|≥\s*0\s*$|\\in\s*\\?\[\s*0\s*,\s*1\s*\\?\]|\\in\s*\\left\\?\{\s*0\s*,\s*1",
    re.IGNORECASE,
)


def sentences(plain: str) -> list[tuple[int, int]]:
    """Character spans of the sentences of a paragraph's plain text."""
    out: list[tuple[int, int]] = []
    start = 0
    for m in _SENTENCE_END.finditer(plain):
        out.append((start, m.start()))
        start = m.end()
    out.append((start, len(plain)))
    return [(a, b) for a, b in out if plain[a:b].strip()]


def sentence_of(plain: str, a: int, b: int) -> tuple[int, int]:
    """The sentence containing the element at ``[a, b)``."""
    for s, e in sentences(plain):
        if s <= a and b <= e:
            return s, e
    return 0, len(plain)


def definition_span(plain: str, a: int, b: int) -> dict | None:
    """The citable defining sentence of the element at ``[a, b)``, or None if the sentence does not define it."""
    s, e = sentence_of(plain, a, b)
    before, after = plain[s:a], plain[b:e]
    if (
        _IN_PARENS.search(plain[s:b] + ")")
        and "(" in before
        and ")" not in before.rsplit("(", 1)[-1]
    ):
        return None  # a parenthesised aside: "(i.e. ⟨c_e = 1⟩)"
    after_def = bool(_DEF_AFTER.search(after))
    before_def = bool(_DEF_BEFORE.search(before))
    if not (after_def or before_def):
        return None
    position = (
        "after"
        if after_def and not before_def
        else "before"
        if before_def and not after_def
        else "around"
    )
    text = plain[s:e].strip()
    lead = len(plain[s:e]) - len(plain[s:e].lstrip())
    return {
        "start": s + lead,
        "end": s + lead + len(text),
        "text": text,
        "position": position,
        "source": "rule",
    }


def _role_inline(rec: dict, plain: str, a: int, b: int) -> tuple[str, str, dict | None]:
    cls = rec.get("cls")
    latex = rec.get("latex") or ""
    if cls == "statement":
        if _MEMBERSHIP.match(latex):
            return "index", "membership statement", definition_span(plain, a, b)
        if _DOMAIN.search(latex):
            return "domain", "domain statement", definition_span(plain, a, b)
        if len(latex) > 80 or "matrix" in latex or _INEQ.search(latex):
            return (
                "formula",
                "inline relation",
                None,
            )  # a constraint set inline, not a value definition
        span = definition_span(plain, a, b)
        if span and "=" in latex:
            return "definition", "defining sentence", span
        return "formula", "inline relation", None
    if cls == "operator":
        return "formula", "inline operator", None
    if cls == "symbol":
        span = definition_span(plain, a, b)
        if span:
            return "definition", "defining sentence", span
        return "mention", "reference only", None
    return "other", "expression", None


def propose_roles(rec: dict) -> dict[str, dict]:
    """``{math id: {"role", "rule", "span"}}`` for every element of one discovery record."""
    paras = {p["i"]: p for p in rec.get("paras", [])}
    spans_by_id: dict[str, tuple[int, int, int]] = {}
    for p in rec.get("paras", []):
        pos = 0
        plain = p.get("plain") or ""
        for kind, val in p["segments"]:
            if kind == "t":
                pos += len(val)
            else:
                mid = val
                latex = next((m["latex"] for m in rec["maths"] if m["id"] == mid), "")
                length = len(f"⟨{latex}⟩")
                spans_by_id[mid] = (p["i"], pos, pos + length)
                pos += length
        assert pos == len(plain) or not plain, f"plain/segments mismatch in paragraph {p['i']}"
    out: dict[str, dict] = {}
    for m in rec.get("maths", []):
        mid = m["id"]
        where = m.get("where")
        if where == "display":
            out[mid] = {"role": "formula", "rule": "display equation", "span": None}
        elif where == "inline" and mid in spans_by_id:
            pi, a, b = spans_by_id[mid]
            plain = paras[pi].get("plain") or ""
            role, rule, span = _role_inline(m, plain, a, b)
            if span:
                span = {"para": pi, **span}
            out[mid] = {"role": role, "rule": rule, "span": span}
        elif where == "table":
            out[mid] = {
                "role": "definition" if _is_symbol_cell(rec, mid) else "other",
                "rule": "notation-table row" if _is_symbol_cell(rec, mid) else "table cell",
                "span": _table_span(rec, mid),
            }
        elif where == "deflist":
            out[mid] = {
                "role": "definition",
                "rule": "definition list",
                "span": _deflist_span(rec, mid),
            }
        else:
            out[mid] = {"role": "other", "rule": "caption / title / footnote", "span": None}
    return out


def _is_symbol_cell(rec: dict, mid: str) -> bool:
    for t in rec.get("tables", []):
        if not t.get("notation"):
            continue
        for r in t["rows"]:
            cells = r.get("cells") or []
            if cells and mid in cells[0].get("maths", []) and r.get("symbol") is not None:
                return True
    return False


def _table_span(rec: dict, mid: str) -> dict | None:
    for t in rec.get("tables", []):
        for ri, r in enumerate(t["rows"]):
            for c in r.get("cells") or []:
                if mid in c.get("maths", []):
                    text = " | ".join(cc["text"] for cc in r["cells"])
                    return {
                        "table": t["id"],
                        "row": ri,
                        "start": 0,
                        "end": len(text),
                        "text": text,
                        "position": "row",
                        "source": "rule",
                    }
    return None


def _deflist_span(rec: dict, mid: str) -> dict | None:
    for i, it in enumerate(rec.get("deflists", [])):
        if mid in it.get("term_maths", []) or mid in it.get("def_maths", []):
            text = f"{it['term']} | {it['def']}"
            return {
                "deflist": i,
                "start": 0,
                "end": len(text),
                "text": text,
                "position": "row",
                "source": "rule",
            }
    return None


def counts(roles: dict[str, dict]) -> dict[str, int]:
    c: Counter[str] = Counter(v["role"] for v in roles.values())
    c["definition_with_span"] = sum(
        1 for v in roles.values() if v["role"] == "definition" and v.get("span")
    )
    c["elements"] = len(roles)
    return dict(sorted(c.items()))


def report_all(discovery_dir: Path = DISCOVERY) -> dict:
    per: dict[str, dict] = {}
    total: Counter[str] = Counter()
    for p in sorted(discovery_dir.glob("*.json")):
        rec = json.loads(p.read_text(encoding="utf-8"))
        c = counts(propose_roles(rec))
        per[p.stem] = c
        total.update(c)
    return {"papers": len(per), "totals": dict(sorted(total.items())), "per_paper": per}


def render_report_md(report: dict) -> str:
    t = report["totals"]
    lines = [
        "# Roles: every mathematical element has a place",
        "",
        f"Papers: **{report['papers']}**, elements: **{t.get('elements', 0)}**. Proposed by sentence rules; the human decides on the discovery pages.",
        "",
        "| role | elements |",
        "|---|---:|",
    ]
    for role in ROLES:
        lines.append(f"| {role} | {t.get(role, 0)} |")
    lines += ["", f"Definitions with a citable span: {t.get('definition_with_span', 0)}.", ""]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args(argv)
    report = report_all()
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    REPORT_MD.write_text(render_report_md(report), encoding="utf-8", newline="\n")
    t = report["totals"]
    print(
        "roles: "
        + ", ".join(f"{r} {t.get(r, 0)}" for r in ROLES)
        + f"; definitions with span {t.get('definition_with_span', 0)}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
