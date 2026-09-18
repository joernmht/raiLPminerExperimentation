"""Index discovery: the index letters and index families of a paper, found deterministically.

Stage 2 of the bottom-up pipeline. Before any symbol can be typed, the indices
must be known: a letter that is bound somewhere (``\\sum_{i \\in I}``,
``\\forall t = 1, \\ldots, T``, "for all :math:`i \\in I`" in the prose, "*i*:
generic train in *T*" in the notation table) is an index dummy, the thing it
ranges over is an index family, and every other subscript position is then
either one of those letters or a label. This module reads **all** the
mathematics the discovery stage recorded (display rows, inline statements,
notation tables, definition lists) and assembles, per paper:

* **letters** — every index-like letter with the evidence for what it ranges
  over: binder pairs, range caps ("capped" indices, ``t = 1..T``), tuple
  binders ``(i, j) \\in A``, prose memberships, notation-table rows, and the
  subscript positions it occupies;
* **families** — every index family named by that evidence, with the letters
  that range over it and, when a notation table says so, the paper's own
  description ("Set of trains");
* **rules that raise the yield** — a letter never bound anywhere is resolved
  by (a) *decoration*: ``k'``, ``\\hat{k}`` range over what ``k`` ranges over;
  (b) *position*: a letter used at the same subscript position of the same
  symbol as a bound letter ranges over that letter's family. Both are recorded
  as ``alias`` verdicts with their evidence, never silently merged;
* a comparison with the declaration sidecar (``%@ index`` lines), so the
  report shows which declared families are now backed by evidence, which
  found families are undeclared, and which declared "index" lines are in
  fact dummy letters (the sidecars written by the assistant declare ``k`` and
  ``E`` alike; lp2graph wants the family).

Everything is a pure function of the corpus artifacts; no model is involved.
Per-paper records go to ``corpus/indices/<key>.json`` (gitignored: they quote
notation-table descriptions); ``corpus/indices.{json,md}`` carry counts and
names only.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.indices [--only KEY ...]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from corpusbuilder.discover import DISCOVERY, load_record
from corpusbuilder.dossier import Dossier
from corpusbuilder.fulltext import DOSSIERS, included_dossiers
from corpusbuilder.game import _collapse_words, _group_end, _rewrite_ops
from corpusbuilder.labels import is_standard_label
from corpusbuilder.promote import CORPUS, DECLARATIONS

INDICES = CORPUS / "indices"
REPORT_JSON = CORPUS / "indices.json"
REPORT_MD = CORPUS / "indices.md"
SCHEMA = "indices-1"

GREEK = frozenset(
    [
        "alpha",
        "beta",
        "gamma",
        "delta",
        "epsilon",
        "varepsilon",
        "zeta",
        "eta",
        "theta",
        "vartheta",
        "iota",
        "kappa",
        "lambda",
        "mu",
        "nu",
        "xi",
        "pi",
        "varpi",
        "rho",
        "varrho",
        "sigma",
        "varsigma",
        "tau",
        "upsilon",
        "phi",
        "varphi",
        "chi",
        "psi",
        "omega",
        "Gamma",
        "Delta",
        "Theta",
        "Lambda",
        "Xi",
        "Pi",
        "Sigma",
        "Upsilon",
        "Phi",
        "Psi",
        "Omega",
    ]
)
_GREEK_UNICODE = {
    "α": "alpha",
    "β": "beta",
    "γ": "gamma",
    "δ": "delta",
    "ε": "epsilon",
    "ζ": "zeta",
    "η": "eta",
    "θ": "theta",
    "ι": "iota",
    "κ": "kappa",
    "λ": "lambda",
    "μ": "mu",
    "ν": "nu",
    "ξ": "xi",
    "π": "pi",
    "ρ": "rho",
    "σ": "sigma",
    "τ": "tau",
    "υ": "upsilon",
    "φ": "phi",
    "χ": "chi",
    "ψ": "psi",
    "ω": "omega",
    "Γ": "Gamma",
    "Δ": "Delta",
    "Θ": "Theta",
    "Λ": "Lambda",
    "Ξ": "Xi",
    "Π": "Pi",
    "Σ": "Sigma",
    "Φ": "Phi",
    "Ψ": "Psi",
    "Ω": "Omega",
}
_ACCENTS = {
    "hat": "hat",
    "widehat": "hat",
    "bar": "bar",
    "overline": "bar",
    "tilde": "tilde",
    "widetilde": "tilde",
    "vec": "vec",
    "dot": "dot",
    "underline": "underline",
}
_OVERSET_ACCENT = {
    "\\land": "hat",
    "\\wedge": "hat",
    "^": "hat",
    "\\sim": "tilde",
    "~": "tilde",
    "-": "bar",
    "\\_": "bar",
    "_": "bar",
    "\\rightarrow": "vec",
    "→": "vec",
}
_DECO_SUFFIX = re.compile(r"^(.*?)(?:_(hat|bar|tilde|vec|dot|underline))?(p*)$")
_WRAPPERS = (
    "mathcal",
    "mathbb",
    "mathrm",
    "mathbf",
    "boldsymbol",
    "mathit",
    "mathsf",
    "textit",
    "textbf",
    "text",
    "operatorname",
    "mbox",
)
_GRP = r"\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}"
_OPS = r"(\\sum|\\prod|\\bigcup|\\bigcap|\\max|\\min|sum|max|min)"
_STACKED_UNDER_OVER = re.compile(
    r"\\underset\s*" + _GRP + r"\s*\{\s*\\overset\s*" + _GRP + r"\s*\{\s*" + _OPS + r"\s*\}\s*\}"
)
_STACKED_OVER_UNDER = re.compile(
    r"\\overset\s*" + _GRP + r"\s*\{\s*\\underset\s*" + _GRP + r"\s*\{\s*" + _OPS + r"\s*\}\s*\}"
)
_OVERSET_RX = re.compile(r"\\overset\s*\{\s*([^{}]*?)\s*\}\s*\{([^{}]*)\}")


def _overset_accent(m: re.Match[str]) -> str:
    deco = _OVERSET_ACCENT.get(m.group(1))
    return f"\\{deco}{{{m.group(2)}}}" if deco else m.group(0)


_BINDER = re.compile(r"\\(sum|prod|bigcup|bigcap|max|min|forall|exists)(?![A-Za-z])")
_IDENT = re.compile(
    r"(?<![\\A-Za-z0-9_])([A-Za-z][A-Za-z0-9]*(?:_(?:hat|bar|tilde|vec|dot|underline)(?![A-Za-z0-9]))?)"
)
_DOTS = r"(?:\\ldots|\\cdots|\\dots|\\dotsc|\.\.\.)"
_RANGE_EQ = re.compile(
    rf"^\s*(\w+)\s*=\s*([^,\s]+)\s*,(?:\s*[^,\s]+\s*,)?\s*{_DOTS}\s*,?\s*([^,\s]+)\s*$"
)
_RANGE_EQ_G = re.compile(
    rf"(\w+)\s*=\s*([^,\s]+)\s*,(?:\s*[^,\s]+\s*,)?\s*{_DOTS}\s*,?\s*([^,\s]+)"
)
_RANGE_LE = re.compile(r"^\s*([^\s\\]+)\s*\\le\s*(\w+)\s*\\le\s*([^\s\\]+)\s*$")
_RANGE_SET = re.compile(
    rf"^\s*(\w+)\s*\\in\s*\\\{{\s*([^,\s]+)\s*,(?:\s*[^,\s]+\s*,)?\s*{_DOTS}\s*,?\s*([^,\s}}]+)\s*\\\}}\s*$"
)
_MEMBER = re.compile(r"^\s*(.+?)\s*\\in\s*(.+?)\s*$")
_TUPLE = re.compile(r"^\(\s*([^()]+?)\s*\)$")
_FAMILY_CUT = re.compile(
    r"\s*(?::|\\mid|\||\\setminus|\\backslash|\\cup|\\cap|\\times|\\text|\\quad|\s\\ne|\s\\neq|\s<|\s>|\s\\le|\s\\ge|\s=)"
)
_TABLE_INDEX = re.compile(r"\bindex\b|\bindices\b|\bgeneric\b|\bcounter\b", re.IGNORECASE)
_TABLE_SET = re.compile(r"\bset\b|\bsets\b|\bcollection\b", re.IGNORECASE)
_TABLE_IN = re.compile(
    r"\b(?:in|of|over)\s+(?:the\s+)?(?:set\s+)?⟨([^⟩]{1,30})⟩|\b(?:in|of)\s+([A-Z][A-Za-z0-9_]{0,3})\b"
)
_NOT_FAMILY = frozenset(
    [
        "Set",
        "Sets",
        "Parameter",
        "Parameters",
        "Variable",
        "Variables",
        "Index",
        "Indices",
        "Notation",
        "Symbol",
        "Symbols",
        "Decision",
        "Data",
        "Input",
        "Output",
        "Where",
        "Let",
        "Min",
        "Max",
        "Constraint",
        "Constraints",
        "Objective",
        "Model",
        "Table",
        "Number",
        "Time",
        "Cost",
    ]
)
_CARD = re.compile(r"^\s*\|\s*(.+?)\s*\|\s*$")
_DECL = re.compile(r"^\s*%@\s*(index|param|var)\s+([A-Za-z_]\w*)\b(.*)$")


# -- normalisation ----------------------------------------------------------


def _strip_wrappers(s: str) -> str:
    for _ in range(4):
        before = s
        for w in _WRAPPERS:
            s = re.sub(r"\\" + w + r"\s*\{([^{}]*)\}", r"\1", s)
        if s == before:
            break
    return s


def normalise(latex: str) -> str:
    """One spelling for the forms that matter to index discovery (pure)."""
    s = _collapse_words(latex or "")
    s = re.sub(r"\\(?:limits|nolimits)(?![A-Za-z])", "", s)
    s = _STACKED_UNDER_OVER.sub(
        lambda m: f"\\{_plain(m.group(3))}_{{{m.group(1)}}}^{{{m.group(2)}}}", s
    )
    s = _STACKED_OVER_UNDER.sub(
        lambda m: f"\\{_plain(m.group(3))}_{{{m.group(2)}}}^{{{m.group(1)}}}", s
    )
    s = _OVERSET_RX.sub(_overset_accent, s)
    s = _rewrite_ops(s)
    for u, name in _GREEK_UNICODE.items():
        s = s.replace(u, f"\\{name} ")
    s = (
        s.replace("′", "'")
        .replace("\\prime", "'")
        .replace("≤", "\\le ")
        .replace("⩽", "\\le ")
        .replace("≥", "\\ge ")
        .replace("⩾", "\\ge ")
        .replace("∈", "\\in ")
        .replace("∉", "\\notin ")
        .replace("…", "\\ldots ")
        .replace("≠", "\\ne ")
        .replace("\\leq", "\\le")
        .replace("\\geq", "\\ge")
        .replace("\\leqslant", "\\le")
        .replace("\\geqslant", "\\ge")
    )
    s = re.sub(r"\\(?:left|right|Bigl|Bigr|bigl|bigr|big|Big)\s*(?=[\(\)\[\]\{\}\|.\\])", "", s)
    s = re.sub(r"\\(?:,|;|!|quad|qquad|:|>)", " ", s)
    s = _strip_wrappers(s)
    # accents and primes become name suffixes, as the lp2graph normaliser spells them
    for _ in range(3):
        s = re.sub(
            r"\\(" + "|".join(_ACCENTS) + r")\s*\{\s*([A-Za-z](?:\\?[A-Za-z]*)?)\s*\}",
            lambda m: _plain(m.group(2)) + "_" + _ACCENTS[m.group(1)],
            s,
        )
    s = re.sub(
        r"\\([a-zA-Z]+)",
        lambda m: (" " + m.group(1) + " ") if m.group(1) in GREEK else m.group(0),
        s,
    )
    s = re.sub(
        r"\^\{\s*('(?:\s*')*)\s*\}", lambda m: "'" * m.group(1).count("'"), s
    )  # ^{'' '} -> '''
    s = re.sub(
        r"([A-Za-z][A-Za-z0-9_]*)\s*((?:'|\^\{?'+\}?)+)",
        lambda m: m.group(1) + "p" * m.group(2).count("'"),
        s,
    )
    s = re.sub(r"([A-Za-z]) +([_^])", r"\1\2", s)  # "lambda _{i}" -> "lambda_{i}"
    return re.sub(r"\s+", " ", s).strip()


def _plain(s: str) -> str:
    s = s.strip()
    if s.startswith("\\"):
        s = s[1:]
    return s


def base_letter(name: str) -> str | None:
    """The undecorated letter a name derives from, or None if it is not letter-like."""
    m = _DECO_SUFFIX.match(name)
    if not m:
        return None
    core = m.group(1) or name
    if core == name and m.group(3):  # trailing p's were no primes (a name like "kp"?)
        core = name[: len(name) - len(m.group(3))] if len(name) - len(m.group(3)) == 1 else name
    if len(core) == 1 and core.isalpha():
        return core
    if core in GREEK:
        return core
    return None


def is_letterish(name: str) -> bool:
    return base_letter(name) is not None


# -- clauses ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Binding:
    letters: tuple[str, ...]
    family: str | None
    kind: str  # member | tuple | range
    lo: str | None = None
    hi: str | None = None
    family_sub: tuple[str, ...] = ()
    subset: str | None = (
        None  # the label that restricts the family: A_{dwell} ranges over the dwell subset of A
    )


def _split_top(s: str, seps: str = ",") -> list[str]:
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch in "({[":
            depth += 1
        elif ch in ")}]":
            depth -= 1
        if ch in seps and depth <= 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [p.strip() for p in out if p.strip()]


def _family_of(
    text: str, words: frozenset[str] = frozenset()
) -> tuple[str | None, tuple[str, ...]]:
    """The family symbol at the head of a set expression: ``N_i`` -> ("N", ("i",))."""
    text = _FAMILY_CUT.split(text.strip(), 1)[0].strip()
    card = _CARD.match(text)
    if card:  # a cardinality cap "1..|K|" ranges over K itself
        text = card.group(1)
    if not text or text.startswith("{") or text.startswith("\\{") or text[0].isdigit():
        return None, ()
    m = re.match(r"^([A-Za-z][A-Za-z0-9]*(?:_(?:hat|bar|tilde|vec|dot|underline))?p*)", text)
    if not m:
        return None, ()
    fam = m.group(1)
    scripts = text[
        m.end(1) :
    ]  # subscript and superscript in either order: E_{st}^{dr}, E^{dr}_{st}
    sub_m = re.search(r"_\s*(\{[^{}]*\}|[A-Za-z0-9])", scripts)
    sup_m = re.search(r"\^\s*(\{[^{}]*\}|\S)", scripts)
    sup_text = sup_m.group(1) if sup_m else ""
    # a spaced pair in a superscript is one qualifier ("t l"), not two index letters
    sup_text = re.sub(r"(?<![A-Za-z])([a-z]) ([a-z])(?![A-Za-z])", r"\1\2", sup_text)
    sub = (sub_m.group(1) if sub_m else "") + " , " + sup_text
    subs = tuple(t for t in re.findall(r"[A-Za-z][A-Za-z0-9_]*", sub) if _is_index_token(t, words))
    _family_of.last_subset = _subset_label(
        text[m.end(1) :], words
    )  # side channel read by family_and_subset
    if (
        len(fam) > 1
        and fam not in GREEK
        and not re.match(r"^[A-Za-z](?:_(?:hat|bar|tilde|vec|dot|underline))?p*$", fam)
        and not fam[0].isupper()
    ):
        return None, ()  # a lower-case word is a label ("dep", "max"), not a set
    if fam in _NOT_FAMILY:
        return None, ()
    return fam, subs


def _is_index_token(t: str, words: frozenset[str]) -> bool:
    return is_letterish(t) or t in words


def _subset_label(scripts: str, words: frozenset[str] = frozenset()) -> str | None:
    """The word labels in a family's scripts: ``_{dwell}`` -> ``dwell``, ``^{plan}_{odturn}`` -> ``odturn_plan``.

    Index letters and the paper's index names (``E_{de, st}``: st is the
    station the set is indexed by) are not part of the label.
    """
    sub = re.search(r"_\s*(\{[^{}]*\}|[A-Za-z0-9])", scripts)
    sup = re.search(r"\^\s*(\{[^{}]*\}|[A-Za-z0-9])", scripts)
    labels = []
    for g, is_sup in ((sub, False), (sup, True)):
        if not g:
            continue
        text = g.group(1)
        if is_sup:  # a spaced pair in a superscript qualifies the set ("t l", "d r"); in a subscript it indexes it
            text = re.sub(r"(?<![A-Za-z])([a-z]) ([a-z])(?![A-Za-z])", r"\1\2", text)
        labels += [
            t for t in re.findall(r"[A-Za-z][A-Za-z0-9]*", text) if not _is_index_token(t, words)
        ]
    return "_".join(labels) or None


def family_and_subset(
    text: str, words: frozenset[str] = frozenset()
) -> tuple[str | None, tuple[str, ...], str | None]:
    """``_family_of`` plus the subset label of the set expression."""
    _family_of.last_subset = None
    fam, subs = _family_of(text, words)
    return fam, subs, getattr(_family_of, "last_subset", None)


def _letters_of(text: str, words: frozenset[str] = frozenset()) -> tuple[str, ...]:
    return tuple(t for t in re.findall(r"[A-Za-z][A-Za-z0-9_]*", text) if _is_index_token(t, words))


def _cap_family(hi: str) -> str | None:
    """The family a range cap names: ``T`` or ``|K|`` -> that symbol; ``3``, ``2N-1`` -> none."""
    text = hi.strip()
    card = _CARD.match(text)
    if card:
        text = card.group(1).strip()
    if not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]*(?:_(?:hat|bar|tilde|vec|dot|underline))?p*(?:_(?:\{[^{}]*\}|[A-Za-z0-9]))?",
        text,
    ):
        return None
    fam, _ = _family_of(text)
    return fam


def parse_binder(text: str, sup: str = "", words: frozenset[str] = frozenset()) -> list[Binding]:
    """Bindings named by one binder clause (already normalised).

    ``words`` are the paper's multi-letter index names (``st`` for a station),
    accepted wherever a single letter would be.
    """
    text = text.strip()
    out: list[Binding] = []
    m = _RANGE_EQ.match(text) or _RANGE_SET.match(text)
    if m and _is_index_token(m.group(1), words):
        return [Binding((m.group(1),), _cap_family(m.group(3)), "range", m.group(2), m.group(3))]
    m = _RANGE_LE.match(text)
    if m and _is_index_token(m.group(2), words):
        return [Binding((m.group(2),), _cap_family(m.group(3)), "range", m.group(1), m.group(3))]
    m = re.match(r"^\s*(\w+)\s*=\s*([^,\s]+)\s*$", text)
    if m and sup.strip() and is_letterish(m.group(1)):
        return [Binding((m.group(1),), _cap_family(sup), "range", m.group(2), sup.strip())]
    for rm in _RANGE_EQ_G.finditer(text):
        if is_letterish(rm.group(1)):
            out.append(
                Binding((rm.group(1),), _cap_family(rm.group(3)), "range", rm.group(2), rm.group(3))
            )
    text = _RANGE_EQ_G.sub(" ", text)
    pending: list[str] = []
    for clause in _split_top(text):
        m = _RANGE_LE.match(clause) or _RANGE_SET.match(clause)
        if m:
            letter = m.group(2) if m.re is _RANGE_LE else m.group(1)
            hi_text = m.group(3)
            lo_text = m.group(1) if m.re is _RANGE_LE else m.group(2)
            if is_letterish(letter):
                out.append(Binding((letter,), _cap_family(hi_text), "range", lo_text, hi_text))
            pending = []
            continue
        mm = _MEMBER.match(clause)
        if mm and "\\notin" not in clause:
            lhs, rhs = mm.group(1), mm.group(2)
            fam, subs, subset = family_and_subset(rhs, words)
            tm = _TUPLE.match(lhs.strip())
            if tm:
                letters = _letters_of(tm.group(1), words)
                if letters and fam:
                    out.append(Binding(letters, fam, "tuple", family_sub=subs, subset=subset))
                pending = []
                continue
            dm = re.match(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*=\s*\(\s*([^()]*)\)\s*$", lhs)
            if dm and fam and _is_index_token(dm.group(1), words):
                # a = (e, e') \in A: the dummy a ranges over A, its components are e and e'
                out.append(Binding((dm.group(1),), fam, "member", family_sub=subs, subset=subset))
                comps = _letters_of(dm.group(2), words)
                if comps:
                    out.append(Binding(comps, fam, "tuple", family_sub=subs, subset=subset))
                pending = []
                continue
            letters = tuple(pending) + _letters_of(lhs, words)
            if letters and fam and not any(op in lhs for op in ("+", "-", "=", "<", ">")):
                out.append(Binding(letters, fam, "member", family_sub=subs, subset=subset))
            pending = []
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(\s+[A-Za-z][A-Za-z0-9_]*)*", clause) and all(
            _is_index_token(t, words) for t in clause.split()
        ):
            pending.extend(clause.split())
            continue
        pending = []  # a restriction ("i \ne j") ends the run of bare letters
    if pending and not out:
        out.append(Binding(tuple(pending), None, "member"))  # a bare dummy: \sum_{e}, \forall q
    return out


def _scripts(s: str, i: int) -> tuple[list[str], list[str], int]:
    sub: list[str] = []
    sup: list[str] = []
    while True:
        while i < len(s) and s[i] == " ":
            i += 1
        if i >= len(s) or s[i] not in "_^":
            return sub, sup, i
        target = sub if s[i] == "_" else sup
        i += 1
        while i < len(s) and s[i] == " ":
            i += 1
        if i < len(s) and s[i] == "{":
            end = _group_end(s, i)
            target.append(s[i + 1 : end - 1])
            i = end
        elif i < len(s):
            target.append(s[i])
            i += 1
        else:
            return sub, sup, i


def binders_of(norm: str, words: frozenset[str] = frozenset()) -> list[Binding]:
    """All bindings in one normalised row (big operators and quantifiers)."""
    out: list[Binding] = []
    for line in re.split(r"\\\\", norm):
        for m in _BINDER.finditer(line):
            if m.group(1) in ("forall", "exists"):
                tail = line[m.end() :]
                stop = re.search(
                    r"\\(?:forall|exists)(?![A-Za-z])|(?<![\\A-Za-z])(?:where|for|and)\b", tail
                )
                out.extend(parse_binder(tail[: stop.start()] if stop else tail, words=words))
            else:
                sub, sup, _ = _scripts(line, m.end())
                if sub:
                    out.extend(parse_binder(" , ".join(sub), " ".join(sup), words))
        if not re.search(r"\\(?:forall|exists)(?![A-Za-z])", line):
            # a membership tail without \forall: "x_{ij} \le 1 , i \in I , j \in J"
            clauses = _split_top(line)
            if len(clauses) > 1 and any(re.search(r"\\in(?![A-Za-z])", c) for c in clauses[1:]):
                out.extend(parse_binder(" , ".join(clauses[1:]), words=words))
    return out


@dataclass(slots=True)
class Use:
    base: str
    position: str  # "1", "2", ... or "sup"
    letter: str
    juxtaposed: bool = False
    run: str | None = None  # the glued word the letter was cut from ("st" in N_{st})


def _pieces(
    sub: str, words: frozenset[str] = frozenset(), split_pairs: bool = True
) -> list[tuple[str, bool]]:
    """Index tokens of one subscript group: letters, arithmetic on a letter, juxtaposed letters.

    ``split_pairs=False`` for the scripts of an index family (``E^{dis, tl, dr}``):
    a glued pair there qualifies the set, it is never two dummies.
    """
    out: list[tuple[str, bool]] = []
    for piece in _split_top(sub):
        p = piece.strip()
        tm = _TUPLE.match(p)
        if tm:
            p = tm.group(1)
            for t in _split_top(p):
                out.extend(_pieces(t, words, split_pairs))
            continue
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", p):
            if p in words:
                out.append((p, False))  # the paper's own multi-letter index name (st, tr)
                continue
            if len(p) > 1 and is_standard_label(p):
                continue  # a label word (end, max, st): part of the name, never an index
            if is_letterish(p):
                out.append((p, False))
            elif split_pairs and p.isalpha() and len(p) == 2 and p.islower():
                for ch in p:
                    out.append((ch, p))
            continue
        am = re.fullmatch(
            r"([A-Za-z][A-Za-z0-9_]*)\s*[+-]\s*\w+|\d+\s*([A-Za-z][A-Za-z0-9_]*)|([A-Za-z][A-Za-z0-9_]*)\s*[+-]\s*1",
            p,
        )
        if am:
            t = am.group(1) or am.group(2) or am.group(3)
            if _is_index_token(t, words):
                out.append((t, False))
            continue
        toks = re.findall(r"[A-Za-z][A-Za-z0-9_]*", p)
        if (
            toks
            and all(_is_index_token(t, words) for t in toks)
            and not re.search(r"[=<>]|\\le|\\ge", p)
        ):
            spaced_pair = (
                "".join(toks) if len(toks) == 2 and all(len(t) == 1 for t in toks) else None
            )
            if spaced_pair and not split_pairs:
                continue  # "d r" in a family's script: a qualifier, not two letters
            for t in toks:
                out.append((t, spaced_pair or (len(toks) > 1)))
    return out


def subscript_uses(
    norm: str, words: frozenset[str] = frozenset(), families: frozenset[str] = frozenset()
) -> list[Use]:
    """Every (symbol, position, letter) in the subscripts and superscripts of one row.

    ``families`` are the paper's index families: glued pairs in their scripts
    are qualifiers (``E^{dr}``), not dummies.
    """
    uses: list[Use] = []
    i = 0
    n = len(norm)
    while i < n:
        m = _IDENT.search(norm, i)
        if not m:
            break
        name = m.group(1)
        i = m.end()
        if norm[m.start() - 1 : m.start()] == "\\":
            continue
        sub, sup, j = _scripts(norm, i)
        i = j
        if name in ("sum", "prod", "max", "min", "forall", "exists", "bigcup", "bigcap") or (
            name in GREEK and False
        ):
            continue
        split = name not in families
        for group in sub:
            for pos, (letter, jux) in enumerate(_pieces(group, words, split), 1):
                uses.append(
                    Use(name, str(pos), letter, bool(jux), jux if isinstance(jux, str) else None)
                )
        for group in sup:
            for letter, jux in _pieces(group, words, split):
                uses.append(
                    Use(name, "sup", letter, bool(jux), jux if isinstance(jux, str) else None)
                )
    return uses


_PAIR_RE = re.compile(r"(?<![A-Za-z\\])([a-z]) ?([a-z])(?![A-Za-z])")


def index_words(rows: list[tuple[str, str]]) -> tuple[frozenset[str], dict[str, dict]]:
    """Two-letter runs that are one identifier: ``st`` in ``N_{st}`` when ``st_e`` or bare ``st`` also occur.

    A run glued inside a subscript is ambiguous (two indices ``ij``, a label
    ``end``, or a name such as ``st`` for a station). It is a name of its own
    when it also appears with its own subscript (``st_{e}``, the station of
    e) or bare next to a relation (``st = st_{e'}``) and neither letter is
    bound separately anywhere in the paper.
    """
    runs: Counter[str] = Counter()
    for _rid, norm in rows:
        for use in subscript_uses(norm):
            if use.run:
                runs[use.run] += 1
    for (
        _rid,
        norm,
    ) in rows:  # "t r_{e} \ne t r_{e'}": a pair with its own subscript, never inside one
        for a, b in re.findall(r"(?<![A-Za-z\\])([a-z]) ?([a-z])_(?:\{|[A-Za-z0-9])", norm):
            if a + b not in runs and not is_letterish(a + b):
                runs[a + b] += 0
    if not runs:
        return frozenset(), {}
    bound: set[str] = set()
    for _rid, norm in rows:
        for b in binders_of(norm):
            bound.update(b.letters)
    evidence: dict[str, dict] = {}
    words: set[str] = set()
    for run in runs:
        a, b = run[0], run[1]
        own = sum(
            len(re.findall(rf"(?<![A-Za-z\\]){a} ?{b}_(?:\{{|[A-Za-z0-9])", norm))
            for _r, norm in rows
        )
        bare = sum(
            len(
                re.findall(
                    rf"(?<![A-Za-z\\_^{{]){a} ?{b} ?(?:=|\\ne|\\neq|\\le|\\ge|\\in|\\notin)(?![A-Za-z])",
                    norm,
                )
            )
            + len(re.findall(rf"(?:=|\\ne|\\neq|\\le|\\ge) ?{a} ?{b}(?![A-Za-z_])", norm))
            for _r, norm in rows
        )
        if own + bare == 0 or (runs[run] == 0 and own < 2):
            continue  # a pair never inside a subscript needs its own subscript at least twice
        separate = {a, b} & bound
        if separate and own < 2:
            continue
        words.add(run)
        evidence[run] = {
            "in_subscripts": runs[run],
            "own_subscript": own,
            "bare": bare,
            "letters_bound_separately": sorted(separate),
        }
    return frozenset(words), evidence


def join_words(norm: str, words: frozenset[str]) -> str:
    """Write a spaced two-letter name as one token (``s t_{e}`` -> ``st_{e}``)."""
    for w in sorted(words):
        norm = re.sub(rf"(?<![A-Za-z\\]){w[0]} {w[1]}(?![A-Za-z])", w, norm)
    return norm


# -- declared sidecars -------------------------------------------------------


def declared_names(key: str, decl_dir: Path = DECLARATIONS) -> dict[str, dict[str, str]]:
    """``{kind: {name: description}}`` from the paper's declaration sidecars."""
    out: dict[str, dict[str, str]] = {"index": {}, "param": {}, "var": {}}
    for suffix in (".tex", ".vocab.tex"):
        p = decl_dir / f"{key}{suffix}"
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            m = _DECL.match(line)
            if m:
                desc = m.group(3).split("::", 1)[1].strip() if "::" in m.group(3) else ""
                out[m.group(1)].setdefault(m.group(2), desc)
    return out


# -- the paper --------------------------------------------------------------


@dataclass(slots=True)
class Letter:
    name: str
    base: str
    bound: Counter = field(default_factory=Counter)
    tuple_bound: Counter = field(default_factory=Counter)
    capped: Counter = field(default_factory=Counter)
    cap_family: Counter = field(default_factory=Counter)
    prose: Counter = field(default_factory=Counter)
    table: Counter = field(default_factory=Counter)
    table_desc: str = ""
    sub_rows: int = 0
    sup_rows: int = 0
    juxtaposed_rows: int = 0
    runs: Counter = field(default_factory=Counter)
    bases: Counter = field(default_factory=Counter)
    positions: Counter = field(default_factory=Counter)
    rows: list[str] = field(default_factory=list)
    lo: str | None = None


def _note_row(letter: Letter, row: str) -> None:
    if row not in letter.rows and len(letter.rows) < 12:
        letter.rows.append(row)


def analyse(
    display_rows: list[tuple[str, str]],
    discovery: dict | None,
    declared: dict[str, dict[str, str]] | None = None,
) -> dict:
    """The index table of one paper.

    ``display_rows`` are ``(row_id, latex)`` pairs from the dossier;
    ``discovery`` is the paper's discovery record (may be None: display only).
    """
    declared = declared or {"index": {}, "param": {}, "var": {}}
    letters: dict[str, Letter] = {}
    fam_desc: dict[str, str] = {}
    fam_sub: dict[str, set[str]] = defaultdict(set)
    fam_subsets: dict[str, Counter] = defaultdict(Counter)
    subset_rows: set[tuple[str, str, str]] = set()
    counts: Counter[str] = Counter()

    def L(name: str) -> Letter:
        if name not in letters:
            letters[name] = Letter(name, base_letter(name) or name)
        return letters[name]

    def take(bindings: list[Binding], row: str, channel: str) -> None:
        for b in bindings:
            for letter in b.letters:
                lt = L(letter)
                _note_row(lt, row)
                if b.kind == "range":
                    lt.capped[b.hi or "?"] += 1
                    if b.family:
                        lt.cap_family[b.family] += 1
                    lt.lo = lt.lo or b.lo
                    counts["capped_bindings"] += 1
                elif b.kind == "tuple":
                    lt.tuple_bound[b.family or "?"] += 1
                    counts["tuple_bindings"] += 1
                elif channel == "prose":
                    lt.prose[b.family or "?"] += 1
                    counts["prose_bindings"] += 1
                else:
                    lt.bound[b.family or "?"] += 1
                    counts["binder_bindings"] += 1
                if b.family:
                    fam_sub[b.family].update(b.family_sub)
                    if b.subset and (row, b.family, b.subset) not in subset_rows:
                        subset_rows.add((row, b.family, b.subset))
                        fam_subsets[b.family][b.subset] += 1  # rows that use the subset

    rows_out: dict[str, dict] = {}
    rows_all: list[tuple[str, str]] = []
    for rid, latex in display_rows:
        rows_all.append((rid, normalise(latex)))
    inline_rows: list[tuple[str, str]] = []
    if discovery:
        for rec in discovery.get("maths", []):
            if (
                rec.get("where") == "inline"
                and rec.get("cls") in ("statement", "operator")
                and rec.get("ok")
            ):
                inline_rows.append((rec["id"], normalise(rec["latex"])))

    words, word_evidence = index_words(rows_all + inline_rows)
    rows_all = [(rid, join_words(norm, words)) for rid, norm in rows_all]
    inline_rows = [(rid, join_words(norm, words)) for rid, norm in inline_rows]

    def note_row(rid: str, bindings: list[Binding]) -> None:
        entry = rows_out.setdefault(rid, {"binders": [], "letters": {}})
        for b in bindings:
            entry["binders"].append(
                {
                    "letters": list(b.letters),
                    "family": b.family,
                    "kind": b.kind,
                    "lo": b.lo,
                    "hi": b.hi,
                    "subset": b.subset,
                }
            )
            for letter in b.letters:
                entry["letters"][letter] = "binder"

    for rid, norm in rows_all:
        bs = binders_of(norm, words)
        take(bs, rid, "binder")
        note_row(rid, bs)
    for rid, norm in inline_rows:
        bs = binders_of(norm, words)
        if not bs:  # a bare membership statement in the prose: "i \in I"
            bs = [b for b in parse_binder(norm, words=words) if b.family]
        take(bs, rid, "prose")
        note_row(rid, bs)

    # subscript positions over every row (display + inline statements)
    family_names = frozenset(
        f
        for lt in letters.values()
        for c in (lt.bound, lt.tuple_bound, lt.prose, lt.cap_family)
        for f in c
        if f != "?"
    )
    for rid, norm in rows_all + inline_rows:
        seen_letters: set[str] = set()
        seen_sup: set[str] = set()
        for use in subscript_uses(norm, words, family_names):
            lt = L(use.letter)
            row_letters = rows_out.setdefault(rid, {"binders": [], "letters": {}})["letters"]
            row_letters.setdefault(use.letter, "sup" if use.position == "sup" else "sub")
            if use.position == "sup":
                if use.letter not in seen_sup:
                    lt.sup_rows += 1
                    seen_sup.add(use.letter)
                continue
            lt.bases[use.base] += 1
            lt.positions[f"{use.base}#{use.position}"] += 1
            if use.juxtaposed:
                lt.juxtaposed_rows += 1
                if use.run:
                    lt.runs[use.run] += 1
            if use.letter not in seen_letters:
                lt.sub_rows += 1
                seen_letters.add(use.letter)
                _note_row(lt, rid)

    # notation tables + definition lists
    if discovery:
        items: list[tuple[str, str, str]] = []
        for t in discovery.get("tables", []):
            if not t.get("notation"):
                continue
            for row in t["rows"]:
                if row.get("symbol") and not row.get("header"):
                    items.append((t["id"], row["symbol"], row.get("desc", "")))
        for i, item in enumerate(discovery.get("deflists", [])):
            items.append((f"deflist-{i}", item["term"], item["def"]))
        for src, symbol, desc in items:
            norm = normalise(symbol.replace("⟨", "").replace("⟩", ""))
            bs = [b for b in parse_binder(norm) if b.family]
            if bs:  # "i \in T" in the symbol cell
                take(bs, src, "prose")
                for b in bs:
                    for letter in b.letters:
                        L(letter).table_desc = L(letter).table_desc or desc[:160]
                continue
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", norm):
                continue
            if (is_letterish(norm) and norm.islower()) or norm in GREEK:
                if _TABLE_INDEX.search(desc) or re.search(
                    r"\b(?:in|of)\s+(?:⟨[^⟩]+⟩|[A-Z]\b)", desc
                ):
                    lt = L(norm)
                    _note_row(lt, src)
                    lt.table_desc = lt.table_desc or desc[:160]
                    fm = _TABLE_IN.search(desc)
                    fam = None
                    if fm:
                        fam, _ = _family_of(normalise(fm.group(1) or fm.group(2)))
                    lt.table[fam or "?"] += 1
                    counts["table_bindings"] += 1
            if (
                _TABLE_SET.search(desc)
                and (norm[0].isupper() or norm in GREEK)
                and norm not in _NOT_FAMILY
            ):
                fam_desc.setdefault(norm, desc[:160])

    # verdicts
    out_letters: dict[str, dict] = {}
    families: dict[str, dict] = {}

    def fam_entry(name: str) -> dict:
        if name not in families:
            families[name] = {
                "name": name,
                "letters": Counter(),
                "declared_as": next(
                    (k for k in ("index", "param", "var") if name in declared[k]), None
                ),
                "desc": fam_desc.get(name, ""),
                "cap": False,
                "indexed_by": sorted(fam_sub.get(name, ())),
                "subsets": dict(fam_subsets.get(name, Counter()).most_common()),
            }
        return families[name]

    primary: dict[str, str] = {}
    for name, lt in letters.items():
        votes: Counter[str] = Counter()
        # a direct membership (i \in I) names the letter's own family; a tuple membership ((e, e') \in A)
        # only says the pair is in A, so it votes only when nothing direct is known
        direct = lt.bound or lt.prose or lt.table or lt.cap_family
        sources = (lt.bound, lt.prose, lt.table, lt.cap_family) if direct else (lt.tuple_bound,)
        for c in sources:
            for fam, n in c.items():
                if fam != "?":
                    votes[fam] += n
        for fam, n in lt.cap_family.items():
            votes[fam] += n
        if votes:
            fam = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            primary[name] = fam

    for name in sorted(letters):
        lt = letters[name]
        votes: Counter[str] = Counter()
        # a direct membership (i \in I) names the letter's own family; a tuple membership ((e, e') \in A)
        # only says the pair is in A, so it votes only when nothing direct is known
        direct = lt.bound or lt.prose or lt.table or lt.cap_family
        sources = (lt.bound, lt.prose, lt.table, lt.cap_family) if direct else (lt.tuple_bound,)
        for c in sources:
            for fam, n in c.items():
                if fam != "?":
                    votes[fam] += n
        rule = None
        family = primary.get(name)
        verdict = (
            "index" if (lt.bound or lt.tuple_bound or lt.capped or lt.prose or lt.table) else None
        )
        if verdict is None and name in words:
            verdict, rule = (
                "index",
                "multi-letter name",
            )  # st_e / bare st: a symbol of its own, used as an index
        if verdict == "index" and rule is None:
            rule = (
                "capped"
                if lt.capped and not (lt.bound or lt.tuple_bound)
                else "binder"
                if (lt.bound or lt.tuple_bound)
                else "prose"
                if lt.prose
                else "table"
            )
        alias_votes: Counter[str] = Counter()
        alias_rule: dict[str, str] = {}
        if verdict is None and lt.base[0].isupper() and lt.base not in GREEK:
            # never bound anywhere and written in capitals: a label (t_{B}) or a set, not a dummy
            verdict, rule, family = "label", "uppercase", None
        if verdict is None:
            if lt.base != name and lt.base in primary:  # decorated letter, base is bound
                alias_votes[primary[lt.base]] += lt.sub_rows or 1
                alias_rule[primary[lt.base]] = "decorated"
            for pos, n in lt.positions.items():
                for other, olt in letters.items():
                    if other == name or other not in primary or pos not in olt.positions:
                        continue
                    alias_votes[primary[other]] += n
                    alias_rule.setdefault(primary[other], "position")
            ranked = sorted(alias_votes.items(), key=lambda kv: (-kv[1], kv[0]))
            clear = bool(ranked) and (
                alias_rule[ranked[0][0]] == "decorated"
                or (ranked[0][1] >= 2 and (len(ranked) == 1 or ranked[0][1] >= 2 * ranked[1][1]))
            )
            if clear:
                family = ranked[0][0]
                verdict = "alias"
                rule = alias_rule[family]
            elif lt.sub_rows >= 1:
                verdict = "candidate"
                rule = "subscript"
                family = None
            else:
                continue
        if (
            lt.juxtaposed_rows
            and not (lt.bound or lt.tuple_bound or lt.capped or lt.prose or lt.table)
            and verdict == "candidate"
        ):
            bound_letters = {
                n
                for n, x in letters.items()
                if x.bound or x.tuple_bound or x.capped or x.prose or x.table
            }
            glued_to_bound = any(
                any(ch in bound_letters and ch != name for ch in run) for run in lt.runs
            )
            if lt.runs and not glued_to_bound:
                verdict, rule = (
                    "label",
                    "glued word",
                )  # "st" in N_{st}: no letter of the word is bound anywhere
            else:
                verdict = (
                    "juxtaposed"  # glued to a bound letter: two indices without a comma, or a label
                )
                rule = "juxtaposed"
        entry = {
            "name": name,
            "base": lt.base,
            "verdict": verdict,
            "rule": rule,
            "family": family,
            "families": dict(sorted(votes.items())),
            "bound_rows": sum(lt.bound.values()) + sum(lt.tuple_bound.values()),
            "capped": {k: v for k, v in lt.capped.items()} if lt.capped else {},
            "lo": lt.lo,
            "prose_rows": sum(lt.prose.values()),
            "table_rows": sum(lt.table.values()),
            "sub_rows": lt.sub_rows,
            "sup_rows": lt.sup_rows,
            "bases": dict(lt.bases.most_common(8)),
            "positions": dict(lt.positions.most_common(8)),
            "alias_votes": dict(sorted(alias_votes.items())) if alias_votes else {},
            "rows": lt.rows,
            "runs": dict(lt.runs.most_common(6)),
            "word_evidence": word_evidence.get(name, {}),
            "desc": lt.table_desc,
            "multi_family": len([f for f, n in votes.items() if n >= 2]) >= 2,
        }
        out_letters[name] = entry
        if family and verdict in ("index", "alias"):
            fe = fam_entry(family)
            fe["letters"][name] += (
                max(1, lt.sub_rows)
                if verdict == "alias"
                else entry["bound_rows"]
                + entry["prose_rows"]
                + entry["table_rows"]
                + sum(lt.capped.values())
            )
            if family in lt.cap_family:
                fe["cap"] = True
    for fam in fam_desc:
        if fam not in families and fam not in declared["param"] and fam not in declared["var"]:
            fam_entry(fam)
    for fam in families.values():
        fam["letters"] = dict(sorted(fam["letters"].items()))
    declared_idx = set(declared["index"])
    found = set(families)
    letters_named_as_index = sorted(
        n
        for n in declared_idx
        if n in out_letters and out_letters[n]["verdict"] in ("index", "alias") and n not in found
    )
    counts.update(
        {
            "letters_index": sum(1 for e in out_letters.values() if e["verdict"] == "index"),
            "letters_alias": sum(1 for e in out_letters.values() if e["verdict"] == "alias"),
            "letters_candidate": sum(
                1 for e in out_letters.values() if e["verdict"] == "candidate"
            ),
            "letters_juxtaposed": sum(
                1 for e in out_letters.values() if e["verdict"] == "juxtaposed"
            ),
            "letters_label": sum(1 for e in out_letters.values() if e["verdict"] == "label"),
            "letters_capped": sum(1 for e in out_letters.values() if e["capped"]),
            "letters_multi_family": sum(1 for e in out_letters.values() if e["multi_family"]),
            "families_found": len(found),
            "families_with_desc": sum(1 for f in families.values() if f["desc"]),
            "families_declared_backed": len(found & declared_idx),
            "families_new": len(found - declared_idx),
            "families_declared_as_param": sum(
                1 for f in families.values() if f["declared_as"] == "param"
            ),
            "declared_index_unfound": len(declared_idx - found - set(letters_named_as_index)),
            "declared_index_are_letters": len(letters_named_as_index),
            "display_rows": len(rows_all),
            "inline_rows": len(inline_rows),
        }
    )
    rules: Counter[str] = Counter(e["rule"] for e in out_letters.values() if e["rule"])
    return {
        "schema_version": SCHEMA,
        "counts": dict(sorted(counts.items())),
        "rules": dict(sorted(rules.items())),
        "letters": out_letters,
        "families": {k: families[k] for k in sorted(families)},
        "rows": {
            k: rows_out[k]
            for k in sorted(rows_out)
            if rows_out[k]["letters"] or rows_out[k]["binders"]
        },
        "declared_index": sorted(declared_idx),
        "declared_index_are_letters": letters_named_as_index,
    }


def proposed_index_lines(record: dict) -> list[str]:
    """``%@ index`` lines for the families the evidence names (deterministic provenance)."""
    lines: list[str] = []
    for fam in record["families"].values():
        if fam["declared_as"] == "index":
            continue
        letters = ", ".join(fam["letters"]) or "-"
        desc = fam["desc"] or f"index family (deterministic: ranged over by {letters})"
        cap = " (range 1..%s)" % fam["name"] if fam["cap"] else ""
        lines.append(f"%@ index {fam['name']} ordered=0 cyclic=0 :: {desc}{cap}")
    return lines


# -- corpus -----------------------------------------------------------------


def analyse_paper(
    d: Dossier, *, discovery_dir: Path = DISCOVERY, decl_dir: Path = DECLARATIONS
) -> dict:
    rows = [(f.id, f.latex) for f in d.formulas if f.latex]
    rec = analyse(rows, load_record(d.key, discovery_dir), declared_names(d.key, decl_dir))
    rec["paper_key"] = d.key
    return rec


def analyse_all(
    only: set[str] | None = None,
    *,
    dossier_dir: Path = DOSSIERS,
    out_dir: Path = INDICES,
    discovery_dir: Path = DISCOVERY,
    decl_dir: Path = DECLARATIONS,
) -> dict[str, dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict] = {}
    for d in included_dossiers(dossier_dir):
        if only and d.key not in only:
            continue
        rec = analyse_paper(d, discovery_dir=discovery_dir, decl_dir=decl_dir)
        (out_dir / f"{d.key}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        records[d.key] = rec
    return records


def build_report(records: dict[str, dict]) -> dict:
    totals: Counter[str] = Counter()
    rules: Counter[str] = Counter()
    per: dict[str, dict] = {}
    for key in sorted(records):
        rec = records[key]
        totals.update(rec["counts"])
        rules.update(rec["rules"])
        per[key] = {
            **rec["counts"],
            "letters": {
                n: (e["verdict"], e["family"])
                for n, e in rec["letters"].items()
                if e["verdict"] in ("index", "alias")
            },
            "families": sorted(rec["families"]),
        }
    return {
        "schema_version": SCHEMA,
        "papers": len(records),
        "papers_without_index": sum(
            1 for r in records.values() if not r["counts"].get("letters_index")
        ),
        "totals": dict(sorted(totals.items())),
        "rules": dict(sorted(rules.items())),
        "per_paper": per,
    }


def render_report_md(report: dict) -> str:
    t = report["totals"]
    r = report["rules"]
    lines = [
        "# Index discovery: letters, families, and the rules that resolved them",
        "",
        f"Papers: **{report['papers']}** ({report['papers_without_index']} with no index letter found).",
        "Deterministic; reads display rows, inline statements, notation tables and definition lists.",
        "",
        "| letters | count |",
        "|---|---:|",
        f"| index (bound, capped, prose or table evidence) | {t.get('letters_index', 0)} |",
        f"| alias (resolved by decoration or position rule) | {t.get('letters_alias', 0)} |",
        f"| candidate (subscript only, no family evidence) | {t.get('letters_candidate', 0)} |",
        f"| juxtaposed only (probably label fragments) | {t.get('letters_juxtaposed', 0)} |",
        f"| capital letters never bound (labels such as t_B, or sets) | {t.get('letters_label', 0)} |",
        f"| capped (ranges over 1..N) | {t.get('letters_capped', 0)} |",
        f"| bound to more than one family | {t.get('letters_multi_family', 0)} |",
        "",
        "| rule that decided the family | letters |",
        "|---|---:|",
    ]
    for rule in (
        "binder",
        "capped",
        "prose",
        "table",
        "decorated",
        "position",
        "subscript",
        "juxtaposed",
        "uppercase",
        "glued word",
    ):
        lines.append(f"| {rule} | {r.get(rule, 0)} |")
    lines += [
        "",
        "| families | count |",
        "|---|---:|",
        f"| found | {t.get('families_found', 0)} |",
        f"| … with the paper's own description (notation table) | {t.get('families_with_desc', 0)} |",
        f"| … already declared as `%@ index` | {t.get('families_declared_backed', 0)} |",
        f"| … not declared at all | {t.get('families_new', 0)} |",
        f"| … declared as a parameter instead (typically a cap N) | {t.get('families_declared_as_param', 0)} |",
        f"| declared `%@ index` lines that are dummy letters, not families | {t.get('declared_index_are_letters', 0)} |",
        f"| declared `%@ index` families with no evidence found | {t.get('declared_index_unfound', 0)} |",
        "",
        f"Bindings: binder {t.get('binder_bindings', 0)}, capped {t.get('capped_bindings', 0)}, tuple {t.get('tuple_bindings', 0)}, "
        f"prose {t.get('prose_bindings', 0)}, table {t.get('table_bindings', 0)}.",
        "",
        "| paper | index | alias | candidate | families | described | declared-backed | new | letters → family |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for key, p in report["per_paper"].items():
        pairs = ", ".join(f"{n}→{fam or '?'}" for n, (v, fam) in p["letters"].items())
        lines.append(
            f"| {key} | {p.get('letters_index', 0)} | {p.get('letters_alias', 0)} | {p.get('letters_candidate', 0)} | "
            f"{p.get('families_found', 0)} | {p.get('families_with_desc', 0)} | {p.get('families_declared_backed', 0)} | "
            f"{p.get('families_new', 0)} | {pairs[:160]} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args(argv)
    records = analyse_all(set(args.only) if args.only else None)
    if args.only:
        for p in sorted(INDICES.glob("*.json")):
            records.setdefault(p.stem, json.loads(p.read_text(encoding="utf-8")))
    report = build_report(records)
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    REPORT_MD.write_text(render_report_md(report), encoding="utf-8", newline="\n")
    t = report["totals"]
    print(
        f"indices: {report['papers']} papers; letters index {t.get('letters_index', 0)} / alias {t.get('letters_alias', 0)} / "
        f"candidate {t.get('letters_candidate', 0)}; families {t.get('families_found', 0)} "
        f"(declared-backed {t.get('families_declared_backed', 0)}, new {t.get('families_new', 0)})",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
