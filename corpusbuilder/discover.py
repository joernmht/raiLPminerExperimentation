"""Bottom-up discovery: every mathematical element of every included paper.

Tier-2 extraction kept one record per ``<ce:formula>`` (display equations) and
threw the rest of the mathematics away. The rest is most of it: in the 238
included papers the full-text XML carries ~9k display formulas but ~70k
further ``<mml:math>`` elements, set inline in the prose ("let :math:`x_{ij}`
be a binary variable", "for all :math:`i \\in I`", ":math:`t = 1, \\ldots, T`"),
in the cells of the papers' own notation tables, and in definition lists.
Index declarations, domain statements and symbol definitions live *there*, not
in the displayed equations, which is why the declaration-driven ingestion keeps
running into undeclared symbols and unbound indices.

This module walks the XML once and records **all** of it, deterministically:

* one :data:`SCHEMA` record per paper in ``corpus/discovery/<key>.json``
  (gitignored: it quotes the paper's prose and tables, i.e. Elsevier TDM
  material, same rule as ``corpus/prose/``);
* every ``<mml:math>`` becomes a *math record* with a stable id (document
  order), where it sits (display / inline / table / deflist / other), its LaTeX
  (the same node bridge Tier-2 uses), a structural class (``statement`` if it
  carries a relation or membership sign, ``operator`` if it carries a big
  operator or min/max without a relation, ``symbol`` if it is one identifier
  with scripts, ``expr`` otherwise) and, for prose elements, the surrounding
  text window plus the kind words found in it ("variable", "parameter",
  "set", "index", "binary", ...). Display formulas keep the dossier's
  ``eq-NNNN`` id so decisions taken elsewhere still apply;
* paragraphs are kept as segment lists (text / inline math / display math) so
  a reading view can show the text with every formula marked, and a human can
  mark what the walker did not;
* notation tables (caption names notation/parameters/variables/sets, or most
  rows start with a symbol) are kept row by row: symbol cell + description.

``corpus/discovery.{json,md}`` hold counts only and are committed.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.discover            # all papers
    PYTHONPATH=. python3 -m corpusbuilder.discover --only KEY  # one paper
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from collections.abc import Callable
from pathlib import Path

from lxml import etree

from corpusbuilder.dossier import Dossier
from corpusbuilder.elsevier import _NS, _XML_PARSER
from corpusbuilder.fulltext import DOSSIERS, FULLTEXT, included_dossiers
from corpusbuilder.mathml import Converted, mathml_to_latex

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DISCOVERY = CORPUS / "discovery"
REPORT_JSON = CORPUS / "discovery.json"
REPORT_MD = CORPUS / "discovery.md"

SCHEMA = "discovery-1"
_MML = "{http://www.w3.org/1998/Math/MathML}"

#: Operator glyphs that make an element a *statement* (something is asserted).
RELATIONS = frozenset("=<>≤≥≦≧⩽⩾∈∉∋∌≠≡≔≅≈≺≻⪯⪰⊂⊆⊃⊇⊄⊈→⇒↔⇔≜≐∝⩴")
#: Multi-character relation spellings the MathML sometimes carries in one ``mo``.
RELATION_WORDS = frozenset({"<=", ">=", ":=", "==", "!=", "=:", "≤", "≥"})
#: Big operators / optimisation words: an *operator* element (a sum, an
#: objective) even without a relation sign.
BIG_OPERATORS = frozenset("∑∏∀∃⋃⋂∫")
OPERATOR_WORDS = frozenset(
    {
        "min",
        "max",
        "Min",
        "Max",
        "minimize",
        "maximize",
        "minimise",
        "maximise",
        "Minimize",
        "Maximize",
        "argmin",
        "argmax",
        "s.t.",
        "lim",
    }
)
#: One identifier, possibly scripted or accented: a symbol *mention*.
_SYMBOL_TAGS = frozenset({"mi", "msub", "msup", "msubsup", "mover", "munder", "munderover"})
_WRAPPERS = frozenset({"mrow", "mstyle", "semantics", "mpadded", "mphantom"})

#: Words near a symbol mention that hint at its kind. Recomputed later from the
#: stored ``context`` window if the list changes; kept here so the report can
#: count them without re-reading the TDM material.
KIND_WORDS: dict[str, re.Pattern[str]] = {
    name: re.compile(pattern, re.IGNORECASE)
    for name, pattern in {
        "variable": r"\bvariables?\b",
        "parameter": r"\bparameters?\b",
        "coefficient": r"\bcoefficients?\b",
        "index": r"\bindex\b|\bindices\b|\bindexed\b",
        "set": r"\bsets?\b",
        "binary": r"\bbinary\b|\b0[-–]1\b",
        "integer": r"\binteger\b|\bintegral\b",
        "continuous": r"\bcontinuous\b|\breal[- ]valued\b",
        "nonnegative": r"\bnon-?negative\b",
        "denote": r"\bdenot\w*",
        "let": r"\blet\b",
        "constant": r"\bconstants?\b",
        "foreach": r"\bfor (?:each|all|every|any)\b",
        "number_of": r"\bnumber of\b",
        "capacity": r"\bcapacit\w+",
        "cost": r"\bcosts?\b",
        "time": r"\btimes?\b",
        "weight": r"\bweights?\b",
        "penalty": r"\bpenalt\w+",
        "decision": r"\bdecision\b",
        "big_m": r"\bbig[- ]?M\b|\bsufficiently large\b|\blarge (?:positive )?(?:number|constant)\b",
        "dual": r"\bdual\b|\bmultipliers?\b",
        "auxiliary": r"\bauxiliary\b|\bslack\b|\bdeviation\b",
    }.items()
}
#: Notation-table captions.
_NOTATION_CAPTION = re.compile(
    r"notation|nomenclature|symbol|parameter|variable|indices|index|\bsets?\b|definition",
    re.IGNORECASE,
)
#: A plain-text symbol cell ("T", "x_ij", "λ", "Δ t"): short, no sentence.
_TEXT_SYMBOL = re.compile(r"^[^\s]{1,12}( [^\s]{1,6}){0,2}$")
#: Share of body rows that must start with a symbol for a table to count as a
#: notation table without a telling caption.
NOTATION_SHARE = 0.4
#: Characters of prose kept on each side of an inline element.
CONTEXT_CHARS = 120
#: Window (each side) in which kind words count as *near*.
NEAR_CHARS = 80

_SKIP_REGIONS = frozenset({"bio", "biography"})


def _ln(node: etree._Element) -> str:
    return etree.QName(node).localname if isinstance(node.tag, str) else ""


def _ws(text: str) -> str:
    return re.sub(r"\s+", " ", text)


# -- classification ---------------------------------------------------------


def _unwrap(node: etree._Element) -> etree._Element:
    kids = [c for c in node if isinstance(c.tag, str)]
    while len(kids) == 1 and _ln(kids[0]) in _WRAPPERS:
        node = kids[0]
        kids = [c for c in node if isinstance(c.tag, str)]
    return node


def classify_math(node: etree._Element) -> str:
    """Structural class of one MathML element: statement / operator / symbol / expr / empty."""
    texts = [
        ((_ln(n)), "".join(n.itertext()).strip())
        for n in node.iter()
        if _ln(n) in ("mo", "mi", "mtext")
    ]
    if not texts and not "".join(node.itertext()).strip():
        return "empty"
    for tag, text in texts:
        if tag == "mo" and (text in RELATION_WORDS or any(ch in RELATIONS for ch in text)):
            return "statement"
    for tag, text in texts:
        if text in OPERATOR_WORDS or (tag == "mo" and any(ch in BIG_OPERATORS for ch in text)):
            return "operator"
    inner = _unwrap(node)
    kids = [c for c in inner if isinstance(c.tag, str)]
    if inner is not node and _ln(inner) in _SYMBOL_TAGS:
        return "symbol"
    if len(kids) == 1 and _ln(kids[0]) in _SYMBOL_TAGS:
        return "symbol"
    if not kids and _ln(inner) in _SYMBOL_TAGS:
        return "symbol"
    return "expr"


def kind_words(window: str) -> list[str]:
    """Kind words present in a prose window, in :data:`KIND_WORDS` order."""
    return [name for name, rx in KIND_WORDS.items() if rx.search(window)]


# -- walking ----------------------------------------------------------------


class _Walk:
    """Document walk state: math ids in document order + the records."""

    def __init__(self) -> None:
        self.nodes: list[etree._Element] = []
        self.maths: list[dict] = []

    def add(self, node: etree._Element, **rec: object) -> str:
        mid = f"m-{len(self.nodes) + 1:04d}"
        self.nodes.append(node)
        self.maths.append({"id": mid, **rec})
        return mid


def _region(node: etree._Element) -> str:
    for anc in node.iterancestors():
        tag = _ln(anc)
        if tag == "abstract":
            return "abstract"
        if tag == "appendices":
            return "appendix"
        if tag == "sections":
            return "body"
        if tag in ("bio", "biography"):
            return "bio"
        if tag in ("floats", "table", "figure"):
            return "float"
        if tag in ("acknowledgment", "acknowledgement"):
            return "acknowledgment"
    return "other"


def _section_title(node: etree._Element) -> str:
    for anc in node.iterancestors():
        if _ln(anc) == "section":
            label = anc.find("ce:label", _NS)
            title = anc.find("ce:section-title", _NS)
            parts = [
                _ws("".join(label.itertext())).strip() if label is not None else "",
                _ws("".join(title.itertext())).strip() if title is not None else "",
            ]
            return " ".join(p for p in parts if p)
    return ""


def _display_owner(
    formula: etree._Element, owned: dict[etree._Element, list[str]]
) -> tuple[str | None, list[str]]:
    """The dossier ids of a ``<ce:formula>``'s first math element (if it was extracted)."""
    math = formula.find(".//mml:math", _NS)
    if math is None:
        return None, []
    ids = owned.get(math, [])
    return (ids[0] if ids else None), ids[1:]


def _walk_para(
    para: etree._Element, walk: _Walk, owned: dict[etree._Element, list[str]], block: int
) -> list[list]:
    """Segments of one paragraph: ``["t", text]`` / ``["m", id]`` / ``["d", id]``."""
    segs: list[list] = []

    def add_text(text: str) -> None:
        text = _ws(text)
        if not text:
            return
        if segs and segs[-1][0] == "t":
            segs[-1][1] += text
        else:
            segs.append(["t", text])

    def emit(node: etree._Element) -> None:
        tag = _ln(node)
        if tag == "formula":
            label = node.find("ce:label", _NS)
            tagtext = _ws("".join(label.itertext())).strip() if label is not None else ""
            seen: set[etree._Element] = set()
            for math in node.iter(_MML + "math"):
                if any(a in seen for a in math.iterancestors()):
                    continue
                seen.add(math)
                ids = owned.get(math, [])
                mid = walk.add(
                    math,
                    where="display",
                    block=block,
                    eq=ids[0] if ids else None,
                    dups=ids[1:],
                    tag=tagtext,
                    label=node.get("id"),
                )
                segs.append(["d", mid])
            return  # the formula's own text (labels, "where") is not prose
        if tag == "math":
            mid = walk.add(node, where="inline", block=block)
            segs.append(["m", mid])
            return
        if tag == "para" and node is not para:
            add_text(" ¶ ")
        if node.text:
            add_text(node.text)
        for child in node:
            if not isinstance(child.tag, str):  # comments / PIs
                if child.tail:
                    add_text(child.tail)
                continue
            emit(child)
            if child.tail:
                add_text(child.tail)

    emit(para)
    if segs and segs[-1][0] == "t":
        segs[-1][1] = segs[-1][1].rstrip()
    if segs and segs[0][0] == "t":
        segs[0][1] = segs[0][1].lstrip()
    return [s for s in segs if not (s[0] == "t" and not s[1])]


def _cell(entry: etree._Element, walk: _Walk, block: str, context_holder: list) -> dict:
    """One table cell: plain text with math placeholders + the math ids."""
    maths: list[str] = []
    parts: list[str] = []

    def emit(node: etree._Element) -> None:
        if _ln(node) == "math":
            mid = walk.add(node, where="table", block=block)
            maths.append(mid)
            parts.append(f"⟨{mid}⟩")
            return
        if node.text:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str):
                emit(child)
            if child.tail:
                parts.append(child.tail)

    emit(entry)
    text = _ws("".join(parts)).strip()
    return {"text": text, "maths": maths}


def _walk_table(table: etree._Element, walk: _Walk, index: int) -> dict:
    tid = f"t-{index:04d}"
    cap = table.find(".//ce:caption", _NS)
    label = table.find("ce:label", _NS)
    caption = _ws("".join(cap.itertext())).strip() if cap is not None else ""
    rows: list[dict] = []
    for row in (n for n in table.iter() if _ln(n) == "row"):
        header = any(_ln(a) == "thead" for a in row.iterancestors())
        cells = [_cell(e, walk, tid, rows) for e in row if _ln(e) == "entry"]
        rows.append({"header": header, "cells": cells})
    body = [r for r in rows if not r["header"]]
    return {
        "id": tid,
        "label": _ws("".join(label.itertext())).strip() if label is not None else "",
        "caption": caption,
        "rows": rows,
        "body_rows": len(body),
    }


def _walk_deflists(root: etree._Element, walk: _Walk) -> list[dict]:
    items: list[dict] = []
    for dl in root.findall(".//ce:def-list", _NS):
        for term in dl.findall(".//ce:def-term", _NS):
            desc = term.getnext()
            t = _cell(term, walk, "deflist", [])
            d = (
                _cell(desc, walk, "deflist", [])
                if desc is not None and _ln(desc) == "def-description"
                else {"text": "", "maths": []}
            )
            items.append(
                {
                    "term": t["text"],
                    "term_maths": t["maths"],
                    "def": d["text"],
                    "def_maths": d["maths"],
                }
            )
    return items


def _plain(segs: list[list], latex: dict[str, str]) -> tuple[str, dict[str, tuple[int, int]]]:
    """Plain text of a paragraph (maths as ⟨latex⟩) + each math's char span."""
    parts: list[str] = []
    spans: dict[str, tuple[int, int]] = {}
    pos = 0
    for kind, val in segs:
        if kind == "t":
            parts.append(val)
            pos += len(val)
        else:
            s = f"⟨{latex.get(val, '')}⟩"
            spans[val] = (pos, pos + len(s))
            parts.append(s)
            pos += len(s)
    return "".join(parts), spans


def _symbol_cell(cell: dict, by_id: dict[str, dict]) -> str | None:
    """The symbol a notation-table row starts with (LaTeX or plain), else None."""
    if len(cell["maths"]) == 1 and not re.sub(r"⟨m-\d{4}⟩", "", cell["text"]).strip():
        rec = by_id[cell["maths"][0]]
        return rec["latex"] if rec["cls"] in ("symbol", "expr") and rec["latex"] else None
    if (
        not cell["maths"]
        and cell["text"]
        and _TEXT_SYMBOL.match(cell["text"])
        and not cell["text"][0].isdigit()
    ):
        return cell["text"]
    return None


_WORD = re.compile(r"[A-Za-z]{3,}")


def is_notation_table(table: dict) -> bool:
    """A table that explains symbols: a telling caption, or most rows read "symbol | words".

    Data tables also start rows with short codes ("A", "IC 1"), so the row rule
    demands a wordy description next to the symbol, which numbers never are.
    """
    body = [r for r in table["rows"] if not r.get("header")]
    if not body:
        return False
    if _NOTATION_CAPTION.search(table.get("caption", "")):
        return True
    explained = sum(
        1
        for r in body
        if r.get("symbol") is not None and len(_WORD.findall(r.get("desc", ""))) >= 2
    )
    return explained / len(body) >= NOTATION_SHARE


# -- the paper --------------------------------------------------------------


def discover_paper(
    xml: str,
    *,
    key: str = "",
    meta: dict | None = None,
    convert: Callable[[list[str]], list[Converted]] = mathml_to_latex,
) -> dict:
    """The discovery record of one article (pure function of the XML)."""
    root = etree.fromstring(xml.encode("utf-8"), parser=_XML_PARSER)

    # Dossier ids: replicate ElsevierClient.extract_formulas exactly, so a
    # display element here carries the same eq-NNNN the game/promotion use.
    owned: dict[etree._Element, list[str]] = {}
    keep_alive: list[etree._Element] = []
    n = 0
    for f in root.findall(".//ce:formula", _NS):
        math = f.find(".//mml:math", _NS)
        if math is None:
            continue
        n += 1
        keep_alive.append(math)
        owned.setdefault(math, []).append(f"eq-{n:04d}")

    walk = _Walk()
    paras: list[dict] = []
    for p in root.iter():
        tag = _ln(p)
        if tag not in ("para", "simple-para"):
            continue
        if any(
            _ln(a) in ("para", "simple-para", "table", "def-list", "caption")
            for a in p.iterancestors()
        ):
            continue  # nested paragraph (emitted by its parent), table/list/caption prose
        region = _region(p)
        if region in _SKIP_REGIONS:
            continue
        block = len(paras)
        segs = _walk_para(p, walk, owned, block)
        if not segs:
            continue
        paras.append({"i": block, "region": region, "section": _section_title(p), "segments": segs})

    tables = [
        _walk_table(t, walk, i)
        for i, t in enumerate((n for n in root.iter() if _ln(n) == "table"), 1)
    ]
    deflists = _walk_deflists(root, walk)

    # Any math not reached above (captions, section titles, footnotes ...).
    reached = set(walk.nodes)
    for math in root.iter(_MML + "math"):
        if math in reached or any(a in reached for a in math.iterancestors()):
            continue
        parent = math.getparent()
        ptag = _ln(parent) if parent is not None else ""
        ctx = (
            _ws("".join(parent.itertext())).strip()[: 2 * CONTEXT_CHARS]
            if parent is not None
            else ""
        )
        walk.add(math, where="other", block=ptag, context=ctx)

    converted = (
        convert([etree.tostring(node, encoding="unicode", with_tail=False) for node in walk.nodes])
        if walk.nodes
        else []
    )
    latex: dict[str, str] = {}
    for rec, node, conv in zip(walk.maths, walk.nodes, converted, strict=True):
        rec["latex"] = conv.latex
        rec["ok"] = bool(conv.ok)
        rec["cls"] = classify_math(node)
        if not conv.ok:
            rec["error"] = (conv.error or "")[:200]
        latex[rec["id"]] = conv.latex

    # Prose windows + kind words for inline elements; the paragraph's plain text.
    by_id = {rec["id"]: rec for rec in walk.maths}
    for para in paras:
        plain, spans = _plain(para["segments"], latex)
        para["plain"] = plain
        for mid, (a, b) in spans.items():
            rec = by_id[mid]
            if rec["where"] != "inline":
                continue
            before = plain[max(0, a - CONTEXT_CHARS) : a]
            after = plain[b : b + CONTEXT_CHARS]
            rec["context"] = f"{before}⟨{rec['latex']}⟩{after}"
            rec["near"] = kind_words(
                plain[max(0, a - NEAR_CHARS) : a] + " " + plain[b : b + NEAR_CHARS]
            )
    for table in tables:
        body = [r for r in table["rows"] if not r["header"]]
        first_symbol = 0
        for row in table["rows"]:
            cells = row["cells"]
            line = " | ".join(c["text"] for c in cells)
            for c in cells:
                for mid in c["maths"]:
                    by_id[mid]["context"] = line[: 2 * CONTEXT_CHARS]
                    by_id[mid]["near"] = kind_words(line)
            row["symbol"] = _symbol_cell(cells[0], by_id) if cells else None
            row["desc"] = " | ".join(c["text"] for c in cells[1:])
            if not row["header"] and row["symbol"] is not None:
                first_symbol += 1
        table["symbol_rows"] = first_symbol
        table["notation"] = is_notation_table(table)
    for item in deflists:
        line = f"{item['term']} | {item['def']}"
        for mid in item["term_maths"] + item["def_maths"]:
            by_id[mid]["context"] = line[: 2 * CONTEXT_CHARS]
            by_id[mid]["near"] = kind_words(line)

    # Replace ⟨m-id⟩ placeholders in table / deflist text by the LaTeX.
    def fill(text: str) -> str:
        return re.sub(r"⟨(m-\d{4})⟩", lambda m: f"⟨{latex.get(m.group(1), '')}⟩", text)

    for table in tables:
        for row in table["rows"]:
            for c in row["cells"]:
                c["text"] = fill(c["text"])
            row["desc"] = fill(row["desc"])
    for item in deflists:
        item["term"] = fill(item["term"])
        item["def"] = fill(item["def"])
    for rec in walk.maths:
        if "context" in rec:
            rec["context"] = fill(rec["context"])

    counts = _counts(walk.maths, tables, deflists, n)
    return {
        "schema_version": SCHEMA,
        "paper_key": key,
        "meta": meta or {},
        "counts": counts,
        "paras": paras,
        "tables": tables,
        "deflists": deflists,
        "maths": walk.maths,
    }


def _counts(maths: list[dict], tables: list[dict], deflists: list[dict], n_dossier: int) -> dict:
    c: Counter[str] = Counter()
    for rec in maths:
        c[rec["where"]] += 1
        if rec["where"] == "display":
            c["display_in_dossier" if rec.get("eq") else "display_missing"] += 1
        if rec["where"] == "inline":
            c[f"inline_{rec['cls']}"] += 1
            if rec.get("near"):
                c["inline_near_kind"] += 1
        if not rec["ok"]:
            c["failed"] += 1
    c["dossier_formulas"] = n_dossier
    c["tables"] = len(tables)
    c["notation_tables"] = sum(1 for t in tables if t.get("notation"))
    c["notation_rows"] = sum(t["symbol_rows"] for t in tables if t.get("notation"))
    c["deflist_items"] = len(deflists)
    c["maths"] = len(maths)
    return dict(sorted(c.items()))


# -- corpus -----------------------------------------------------------------


def _meta(d: Dossier) -> dict:
    src = d.source
    return {
        "doi": getattr(src, "doi", ""),
        "title": getattr(src, "title", "") or "",
        "year": getattr(src, "year", None),
        "journal": getattr(src, "journal", "") or getattr(src, "venue", "") or "",
    }


def discover_all(
    only: set[str] | None = None,
    *,
    dossier_dir: Path = DOSSIERS,
    fulltext_dir: Path = FULLTEXT,
    out_dir: Path = DISCOVERY,
    convert: Callable[[list[str]], list[Converted]] = mathml_to_latex,
    log: Callable[[str], None] = lambda s: print(s, file=sys.stderr),
) -> dict[str, dict]:
    """Discovery records for every included paper with cached full text."""
    out_dir.mkdir(parents=True, exist_ok=True)
    counts: dict[str, dict] = {}
    dossiers = [d for d in included_dossiers(dossier_dir) if not only or d.key in only]
    for i, d in enumerate(dossiers, 1):
        src = fulltext_dir / f"{d.key}.xml"
        if not src.exists():
            log(f"[{i}/{len(dossiers)}] no full text: {d.key}")
            continue
        rec = discover_paper(
            src.read_text(encoding="utf-8", errors="replace"),
            key=d.key,
            meta=_meta(d),
            convert=convert,
        )
        if rec["counts"]["dossier_formulas"] != len(d.formulas):
            rec["counts"]["dossier_mismatch"] = len(d.formulas)
        (out_dir / f"{d.key}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        counts[d.key] = rec["counts"]
        log(f"[{i}/{len(dossiers)}] {d.key}: {rec['counts']['maths']} maths")
    return counts


def build_report(counts: dict[str, dict]) -> dict:
    total: Counter[str] = Counter()
    for c in counts.values():
        total.update(c)
    papers = len(counts)
    with_notation = sum(1 for c in counts.values() if c.get("notation_tables"))
    return {
        "schema_version": SCHEMA,
        "papers": papers,
        "papers_with_notation_table": with_notation,
        "papers_with_dossier_mismatch": sum(1 for c in counts.values() if "dossier_mismatch" in c),
        "totals": dict(sorted(total.items())),
        "per_paper": {k: counts[k] for k in sorted(counts)},
    }


def render_report_md(report: dict) -> str:
    t = report["totals"]
    lines = [
        "# Discovery: every mathematical element of the included papers",
        "",
        f"Papers with full text: **{report['papers']}**. Counts only; the records themselves",
        "(`corpus/discovery/<key>.json`) quote the papers and stay off the repository.",
        "",
        "| where | elements |",
        "|---|---:|",
        f"| display (in a `<ce:formula>`) | {t.get('display', 0)} |",
        f"| … of which carried by the dossiers (`eq-NNNN`) | {t.get('display_in_dossier', 0)} |",
        f"| … display maths the extractor never recorded | {t.get('display_missing', 0)} |",
        f"| inline, in prose | {t.get('inline', 0)} |",
        f"| … statements (relation / membership) | {t.get('inline_statement', 0)} |",
        f"| … operators (sum, min/max, no relation) | {t.get('inline_operator', 0)} |",
        f"| … symbol mentions | {t.get('inline_symbol', 0)} |",
        f"| … other expressions | {t.get('inline_expr', 0)} |",
        f"| … with a kind word within ±{NEAR_CHARS} chars | {t.get('inline_near_kind', 0)} |",
        f"| in table cells | {t.get('table', 0)} |",
        f"| in definition lists | {t.get('deflist', 0)} |",
        f"| elsewhere (captions, titles, footnotes) | {t.get('other', 0)} |",
        f"| conversion failures | {t.get('failed', 0)} |",
        "",
        f"Notation tables: **{t.get('notation_tables', 0)}** in **{report['papers_with_notation_table']}** papers,",
        f"**{t.get('notation_rows', 0)}** rows that start with a symbol. Definition-list items: {t.get('deflist_items', 0)}.",
        "",
        "| paper | display | inline | statements | symbols | near kind | table maths | notation tables | rows |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for key, c in report["per_paper"].items():
        lines.append(
            f"| {key} | {c.get('display', 0)} | {c.get('inline', 0)} | {c.get('inline_statement', 0)} | "
            f"{c.get('inline_symbol', 0)} | {c.get('inline_near_kind', 0)} | {c.get('table', 0)} | "
            f"{c.get('notation_tables', 0)} | {c.get('notation_rows', 0)} |"
        )
    return "\n".join(lines) + "\n"


def load_record(key: str, out_dir: Path = DISCOVERY) -> dict | None:
    path = out_dir / f"{key}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="paper keys to (re)discover")
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="rebuild corpus/discovery.{json,md} from the records",
    )
    ap.add_argument(
        "--reclassify",
        action="store_true",
        help="re-run the table classifier over the stored records",
    )
    args = ap.parse_args(argv)
    if args.reclassify:
        counts = {}
        for p in sorted(DISCOVERY.glob("*.json")):
            rec = json.loads(p.read_text(encoding="utf-8"))
            for table in rec["tables"]:
                table["notation"] = is_notation_table(table)
            rec["counts"]["notation_tables"] = sum(1 for t in rec["tables"] if t["notation"])
            rec["counts"]["notation_rows"] = sum(
                t["symbol_rows"] for t in rec["tables"] if t["notation"]
            )
            p.write_text(
                json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True),
                encoding="utf-8",
                newline="\n",
            )
            counts[p.stem] = rec["counts"]
    elif args.report_only:
        counts = {
            p.stem: json.loads(p.read_text(encoding="utf-8"))["counts"]
            for p in sorted(DISCOVERY.glob("*.json"))
        }
    else:
        counts = discover_all(set(args.only) if args.only else None)
        if args.only:  # merge into the existing report rather than shrinking it
            for p in sorted(DISCOVERY.glob("*.json")):
                counts.setdefault(p.stem, json.loads(p.read_text(encoding="utf-8"))["counts"])
    report = build_report(counts)
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    REPORT_MD.write_text(render_report_md(report), encoding="utf-8", newline="\n")
    t = report["totals"]
    print(
        f"discovery: {report['papers']} papers, {t.get('maths', 0)} maths "
        f"(display {t.get('display', 0)}, inline {t.get('inline', 0)}, table {t.get('table', 0)}); "
        f"notation tables {t.get('notation_tables', 0)} in {report['papers_with_notation_table']} papers"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
