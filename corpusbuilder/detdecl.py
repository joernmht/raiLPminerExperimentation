"""Deterministic declarations: what the paper's own formulas and tables decide.

A declaration sidecar (``corpus/declarations/<key>.tex``) is the symbol table the
canonical grammar needs. Most sidecars were filled by the assist stage; they
are complete enough to read, and wrong often enough to block rows: a variable
declared as a scalar that every formula indexes, a label variant that was never
declared, a ``role=variable`` the schema does not know. This module repairs
what a **rule** can decide from artifacts that are already deterministic:

R1  metadata      values outside the schema's vocabulary become the schema's
                  default (``role=variable`` -> ``primary``); nothing algebraic.
R2  index letters the index reader's verdicts (``corpus/indices``, ADR-0023)
                  become ``%@ letter i over=I`` lines, and a family the
                  formulas bind but the sidecar never declared gets its
                  ``%@ index`` line. lp2graph's script rules then know which
                  letters are indices without a binder in the same row.
R3  folded names  a declared ``x_ij`` whose suffix is made of index letters is
                  the indexed symbol ``x``: the line is renamed (or dropped
                  when ``x`` is already declared).
R4  shapes        a declared symbol gets the family tuple its uses write, when
                  every use agrees position by position.
R5  missing names a name the formulas use and nobody declared is declared only
                  on evidence the paper states: a domain row (variable), a
                  notation-table row, definition-list item or defining
                  sentence (kind from its own words or its group header).
R6  kind          where the paper names a kind outright and the assisted
                  declaration says the other one, the paper is followed.
R7  deduction     kinds that follow from the algebra under two assumptions a
                  linear program satisfies by definition. *Linearity*: a term
                  holds at most one variable, so what multiplies a declared
                  variable is a parameter. *Non-vacuity*: a row constrains at
                  least one variable, so a quantity set equal to an expression
                  that contains a variable is a variable, and the single
                  undeclared name of an inequality (or objective) that names
                  no variable is one. (Not term by term: an objective may
                  hold constant terms, so a lone unknown in one term proves
                  nothing.) Applied to a fixpoint; a name both deductions
                  claim stays undeclared.
R8  variants      a labelled variant of a declared symbol (``t_arr`` next to a
                  declared ``t``) that nothing above decides takes its kind
                  from the base, in the three cases where the assisted
                  sidecars themselves agree with that reading (measured on
                  1,201 declared variants, see :data:`VARIANT_AGREEMENT`): a
                  variant of a parameter is a parameter; a bound or reference
                  label (``min``, ``max``, ``nom``, ``plan`` ...) on a variable
                  names a parameter; a variant of a variable whose labels are
                  only letters, numbers or direction words is a variable.
                  Accented variants of variables and other label words are
                  left undeclared: there the sidecars split both ways.

Nothing is guessed: a name without evidence stays undeclared and its rows stay
refused. No model is called. Every changed line keeps its original in a
``%det-was:`` comment and every added line sits inside a marked block, so a run
is undone exactly (:func:`restore`) before the next one starts: the module is
idempotent, and the assisted sidecar is always recoverable.

Descriptions never quote the paper (the sidecars are committed; the full text
is not ours to publish): an added line says which rule and which artifact
decided it, nothing else.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.detdecl                 # repair in place + report
    PYTHONPATH=. python3 -m corpusbuilder.detdecl --out DIR       # write repaired copies to DIR
    PYTHONPATH=. python3 -m corpusbuilder.detdecl --restore       # undo every repair
"""

# ruff: noqa: I001 — ``railpminer._lp2graph`` must run before the ``lp2graph`` imports.

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401
from lp2graph.mining.ingest import normalize_latex
from lp2graph.mining.ingest.latex_normalizer import bound_letters
from lp2graph.mining.ingest.row_canon import outline_row, scan_symbols

from corpusbuilder import promote as P
from corpusbuilder.discover import DISCOVERY, load_record
from corpusbuilder.dossier import Dossier
from corpusbuilder.indices import INDICES
from corpusbuilder.symbols import domain_declaration

REPORT_JSON = P.CORPUS / "detdecl.json"
REPORT_MD = P.CORPUS / "detdecl.md"

#: First comment line of a sidecar this module wrote from nothing: the paper
#: has decisions and formulas but no assisted sidecar. Everything in such a
#: file is rule output; ``--restore`` removes the file.
FROM_SCRATCH = "% DETERMINISTIC ONLY: no assisted sidecar exists; written by corpusbuilder.detdecl."

BEGIN = "% --- deterministic declarations (corpusbuilder.detdecl: rules, no model) ---"
END = "% --- end deterministic declarations ---"
WAS = "%det-was: "
DEL = "%det-del: "

VAR_DOMAINS = ("continuous", "non_negative", "integer", "binary")
VAR_ROLES = ("primary", "auxiliary", "slack", "indicator")
VAR_DROLES = (
    "selection_assignment",
    "ordering_precedence",
    "routing_path_column",
    "timing",
    "flow_quantity",
    "auxiliary_linearization",
    "unclassified",
)
PARAM_KINDS = ("scalar", "vector", "matrix", "big_m", "tolerance")
_DOMAIN_SYNONYMS = {
    "real": "continuous",
    "nonneg": "non_negative",
    "nonnegative": "non_negative",
    "non-negative": "non_negative",
    "positive": "non_negative",
    "bool": "binary",
    "boolean": "binary",
    "int": "integer",
}
_IDENT = re.compile(r"[A-Za-z_]\w*")
_DECL = re.compile(r"^(\s*%@\s*)(index|param|var|letter)(\s+)([A-Za-z_]\w*)(.*)$")
_NUM = re.compile(r"-?\d+(?:\.\d+)?")


# --------------------------------------------------------------------------- #
# Undo
# --------------------------------------------------------------------------- #


def restore(text: str) -> str:
    """The sidecar as it was before any run of this module."""
    out: list[str] = []
    lines = text.splitlines()
    i = 0
    in_block = False
    while i < len(lines):
        ln = lines[i]
        if ln.strip() == BEGIN:
            in_block = True
        elif ln.strip() == END:
            in_block = False
        elif in_block:
            pass
        elif ln.startswith(WAS):
            out.append(ln[len(WAS) :])
            i += 1  # the line that replaced it
        elif ln.startswith(DEL):
            out.append(ln[len(DEL) :])
        else:
            out.append(ln)
        i += 1
    return "\n".join(out) + ("\n" if out else "")


# --------------------------------------------------------------------------- #
# Sidecar model
# --------------------------------------------------------------------------- #


def _kv(rest: str) -> tuple[dict[str, str], str]:
    head, sep, desc = rest.partition("::")
    kv: dict[str, str] = {}
    for tok in head.split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            kv[k] = v
    return kv, (sep + desc if sep else "")


def _line(kind: str, name: str, kv: dict[str, str], desc: str) -> str:
    order = {
        "index": ("ordered", "cyclic"),
        "param": ("shape", "kind", "domain"),
        "var": ("shape", "domain", "role", "drole", "lo", "hi"),
        "letter": ("over",),
    }[kind]
    parts = [f"{k}={kv[k]}" for k in order if k in kv]
    parts += [f"{k}={v}" for k, v in kv.items() if k not in order]
    desc = desc if desc.strip() else ":: -"
    return f"%@ {kind} {name} " + " ".join(parts) + " " + desc.strip()


class Sidecar:
    """The ``%@`` lines of one sidecar, editable with a record of every change."""

    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        #: (kind, name) -> added line, in insertion order
        self.added: dict[tuple[str, str], str] = {}
        #: operations performed (drives the fixpoint loops, not the report)
        self.changes: Counter[str] = Counter()
        #: the rule that last wrote each name: what the report counts
        self.rule_of: dict[tuple[str, str], str] = {}
        #: used names left undeclared, with the reason (report only)
        self.undeclared: dict[str, str] = {}
        #: names a symbol is declared for next to the index family of that name
        self.beside: set[str] = set()

    def decls(self) -> list[tuple[int, str, str, dict[str, str], str]]:
        """``(line index, kind, name, key-values, description)`` of every live
        declaration; added lines carry the index ``-1``."""
        out = []
        for i, ln in enumerate(self.lines):
            if ln.startswith((WAS, DEL)):
                continue
            m = _DECL.match(ln)
            if m:
                kv, desc = _kv(m.group(5))
                out.append((i, m.group(2), m.group(4), kv, desc))
        for (_kind, _name), ln in self.added.items():
            m = _DECL.match(ln)
            if m:
                kv, desc = _kv(m.group(5))
                out.append((-1, m.group(2), m.group(4), kv, desc))
        return out

    def names(self, kind: str) -> dict[str, dict[str, str]]:
        return {name: kv for _i, k, name, kv, _d in self.decls() if k == kind}

    def set(self, kind: str, name: str, kv: dict[str, str], desc: str, rule: str) -> None:
        """Write one declaration: replace the live line of that name (keeping
        its original in a ``%det-was:`` comment) or add a new one."""
        new = _line(kind, name, kv, desc)
        self.rule_of[("letter" if kind == "letter" else "symbol", name)] = rule
        candidates = [
            d
            for d in self.decls()
            if d[2] == name
            and d[1] in ("param", "var", "index", "letter")
            and (d[1] == "letter") == (kind == "letter")
        ]
        # A set and a symbol may share a name (the horizon T next to the
        # periods \mathcal{T}): a write goes to the line of its own class, and
        # for a name in ``beside`` never replaces the line of the other class.
        same = [d for d in candidates if (d[1] == "index") == (kind == "index")]
        if same:
            candidates = same
        elif name in self.beside:
            candidates = []
        for i, k, n, _kv_old, _d in candidates[:1]:
            if i < 0:
                key = (k, n)
                if self.added[key] != new:
                    del self.added[key]
                    self.added[(kind, name)] = new
                    self.changes[rule] += 1
                return
            if self.lines[i] == new:
                return
            if i > 0 and self.lines[i - 1].startswith(WAS):
                self.lines[i] = new  # already a replacement: the original is kept above
            else:
                self.lines[i : i + 1] = [WAS + self.lines[i], new]
            self.changes[rule] += 1
            return
        self.added[(kind, name)] = new
        self.changes[rule] += 1

    def delete(self, name: str, rule: str) -> None:
        for i, k, n, _kv, _d in self.decls():
            if n != name or k not in ("param", "var"):
                continue
            if i < 0:
                del self.added[(k, n)]
            elif i > 0 and self.lines[i - 1].startswith(WAS):
                self.lines[i - 1 : i + 1] = [DEL + self.lines[i - 1][len(WAS) :]]
            else:
                self.lines[i] = DEL + self.lines[i]
            self.changes[rule] += 1
            return

    def text(self) -> str:
        out = list(self.lines)
        if self.added:
            out += [BEGIN, *self.added.values(), END]
        return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# R1 — metadata
# --------------------------------------------------------------------------- #


def rule_metadata(sc: Sidecar) -> None:
    """Values the schema's vocabulary does not contain become its default."""
    for _i, kind, name, kv, desc in sc.decls():
        new = dict(kv)
        if kind == "var":
            dom = _DOMAIN_SYNONYMS.get(new.get("domain", ""), new.get("domain", "continuous"))
            new["domain"] = dom if dom in VAR_DOMAINS else "continuous"
            if new.get("role", "primary") not in VAR_ROLES:
                new["role"] = "primary"
            if new.get("drole", "-") not in VAR_DROLES:
                new["drole"] = "-"
            for bound in ("lo", "hi"):
                if bound in new and new[bound] != "-" and not _NUM.fullmatch(new[bound]):
                    new[bound] = "-"
        elif kind == "param":
            if new.get("kind", "scalar") not in PARAM_KINDS:
                new["kind"] = "scalar"
        if kind in ("var", "param") and "shape" in new and new["shape"] != "-":
            fams = [_unwrap_family(f) for f in new["shape"].split(",") if f]
            new["shape"] = (
                ",".join(fams) if fams and all(_IDENT.fullmatch(f) for f in fams) else "-"
            )
        if new != kv:
            sc.set(kind, name, new, desc, "R1 metadata")


def _unwrap_family(f: str) -> str:
    m = re.fullmatch(r"\\(?:mathcal|mathbb|mathbf|mathit|mathrm)\{([^{}]*)\}", f.strip())
    return (m.group(1) if m else f.strip()).replace("\\_", "_")


# --------------------------------------------------------------------------- #
# R2 — index letters and families
# --------------------------------------------------------------------------- #


def index_letters(idx: dict | None) -> dict[str, str]:
    """letter -> family for every letter the index reader calls an index."""
    out: dict[str, str] = {}
    for name, e in sorted((idx or {}).get("letters", {}).items()):
        fam = e.get("family")
        if e.get("verdict") not in ("index", "alias") or not fam:
            continue
        if e.get("verdict") == "alias" and e.get("rule") != "decorated":
            continue  # an alias by subscript position alone is too weak to rebind a letter
        if not _IDENT.fullmatch(name) or not _IDENT.fullmatch(fam):
            continue
        if not name[0].islower():
            continue  # a capital read as an index letter stays a human decision
        out[name] = fam
    return out


def rule_letters(sc: Sidecar, letters: dict[str, str]) -> None:
    symbols = set(sc.names("param")) | set(sc.names("var"))
    for letter, fam in letters.items():
        if letter in symbols or fam in symbols:
            continue  # a declared symbol is never rebound as an index letter
        ensure_family(sc, fam, "family the index reader found (ADR-0023)")
        sc.set("letter", letter, {"over": fam}, ":: index letter (ADR-0023)", "R2 index letter")


def ensure_family(sc: Sidecar, fam: str, why: str) -> None:
    if fam not in sc.names("index") and _IDENT.fullmatch(fam):
        sc.set("index", fam, {"ordered": "0", "cyclic": "0"}, ":: " + why, "R2 index family")


def rule_shape_families(sc: Sidecar, norm_rows: dict[str, str] | None = None) -> None:
    """A family a declared shape names, or a formula binds over, must itself
    be declared; a shape that names an unnamed letter family (``fam_f``)
    makes ``f`` an index letter over it."""
    for _i, kind, _name, kv, _desc in sc.decls():
        if kind in ("param", "var"):
            for fam in [f for f in kv.get("shape", "-").split(",") if f and f != "-"]:
                if fam.startswith("fam_") and fam not in sc.names("index"):
                    ensure_family(sc, fam, "unnamed set of an index letter no formula binds")
                    letter = fam[len("fam_") :]
                    taken = {**sc.names("letter"), **sc.names("param"), **sc.names("var")}
                    if letter not in taken:
                        sc.set(
                            "letter",
                            letter,
                            {"over": fam},
                            ":: index letter (only ever written as a subscript)",
                            "R2 index letter",
                        )
                ensure_family(sc, fam, "family a declared shape names")
    for row, text in (norm_rows or {}).items():
        if not row:
            continue
        for m in re.finditer(r"\\in\s*\\mathcal\{([A-Za-z_][\w\\]*)\}", text):
            ensure_family(sc, m.group(1).replace("\\_", "_"), "family the formulas bind over")


# --------------------------------------------------------------------------- #
# R3 / R4 — folded names and shapes, read off the normalized rows
# --------------------------------------------------------------------------- #

_PIECE_LETTER = re.compile(r"^([A-Za-z]\w*?)(?:\s*[+-]\s*\d+)?$")
_OPERATOR_WORDS = frozenset(
    {"min", "max", "minimize", "maximize", "minimise", "maximise", "subject", "to", "st", "s", "t"}
    | {"such", "that", "where", "for", "all", "and", "or", "if", "otherwise", "else", "forall"}
)


def _operator_word(name: str, subscripted: bool) -> bool:
    """A word of the row's frame (``min``, ``subject to``, the letters of a
    stripped ``s.t.``), not a symbol. The single letters count only bare and
    in lower case: ``t_{e,a}`` and the horizon ``T`` are symbols."""
    if len(name) == 1:
        return name in _OPERATOR_WORDS and not subscripted
    return name.lower() in _OPERATOR_WORDS


def row_uses(norm_rows: dict[str, str]) -> dict[str, list]:
    """name -> its occurrences in the algebra of the normalized rows."""
    out: dict[str, list] = {}
    derived = set(norm_rows.get("", "").split())
    for row, text in sorted(norm_rows.items()):
        if not row:
            continue
        for use in scan_symbols(text):
            if not use.in_tail and use.name not in derived:
                out.setdefault(use.name, []).append(use)
    return out


def body_families(norm_rows: dict[str, str]) -> dict[str, str]:
    """letter -> family for letters the rows bind over exactly one family."""
    seen: dict[str, set[str]] = {}
    for row, text in norm_rows.items():
        if not row:
            continue
        for m in re.finditer(
            r"(?<![\\A-Za-z0-9_])([A-Za-z]\w*)\s*\\in\s*\\mathcal\{([A-Za-z_][\w\\]*)\}", text
        ):
            seen.setdefault(m.group(1), set()).add(m.group(2).replace("\\_", "_"))
    return {k: next(iter(v)) for k, v in seen.items() if len(v) == 1}


def shape_of_uses(uses: list, families: dict[str, str]) -> tuple[list[str] | None, str]:
    """The family tuple the uses write, or ``None`` with the reason.

    The arity is the one most uses write (ties: the larger). Per position the
    family is the one most uses name through a bound letter; a position no
    use binds takes the unnamed family of its letter (:func:`letter_family`).
    """
    clean = [u for u in uses if u.sup is None]
    if not clean:
        return None, "no clean use"
    arities = Counter(len(u.pieces) for u in clean)
    n = max(arities, key=lambda a: (arities[a], a))
    shape: list[str] = []
    for pos in range(n):
        named: Counter[str] = Counter()
        letters: Counter[str] = Counter()
        for u in clean:
            if len(u.pieces) != n:
                continue
            # an index given by a map, s_{f} ("the station of f"), ranges over
            # what its base letter ranges over; a number votes for no family
            piece = u.pieces[pos]
            m = _PIECE_LETTER.match(piece) or re.match(r"^([A-Za-z]\w*?)_\{", piece)
            if not m:
                continue
            fam = families.get(m.group(1))
            if fam:
                named[fam] += 1
            elif len(base_letter(m.group(1))) == 1:
                letters[base_letter(m.group(1))] += 1
        if named:
            shape.append(max(sorted(named), key=lambda f: named[f]))
        elif letters:
            shape.append(letter_family(max(sorted(letters), key=lambda x: letters[x])))
        else:
            return None, f"position {pos + 1}: no index letter"
    return shape, ""


def base_letter(letter: str) -> str:
    """``ip`` (i') and ``i_1`` range over the same set as ``i``."""
    return re.sub(r"(?<=[A-Za-z])p+$|_\w+$", "", letter) or letter


def letter_family(letter: str) -> str:
    """The unnamed family an index letter ranges over when no formula binds
    it: the paper indexes by ``f`` and never names the set it ranges over."""
    return "fam_" + letter


def rule_folded_names(sc: Sidecar, uses: dict[str, list], families: dict[str, str]) -> None:
    """An undeclared used name ``u`` whose only declared relatives are folded
    spellings ``u_<index letters>`` is the same symbol written with those
    letters as indices: it takes their kind (when they agree)."""
    params, variables = sc.names("param"), sc.names("var")
    declared = set(params) | set(variables)
    for name in sorted(uses):
        if name in declared or name in sc.names("index") or name in families:
            continue
        relatives = []
        for d in sorted(declared):
            if not d.startswith(name + "_"):
                continue
            suffix = d[len(name) + 1 :].split("_")
            letters = [ch for tok in suffix for ch in ([tok] if tok in families else list(tok))]
            if letters and all(x in families for x in letters) and d not in uses:
                relatives.append(d)
        if not relatives:
            continue
        kinds = {"var" if d in variables else "param" for d in relatives}
        if len(kinds) != 1:
            continue
        (kind,) = kinds
        kv = dict((variables if kind == "var" else params)[relatives[0]])
        if kind == "var":
            domains = {variables[d].get("domain", "continuous") for d in relatives}
            kv["domain"] = next(iter(domains)) if len(domains) == 1 else "continuous"
        kv["shape"] = "-"
        sc.set(
            kind,
            name,
            kv,
            ":: indexed symbol; was declared in folded spellings " + ", ".join(relatives),
            "R3 folded name",
        )
        for d in relatives:
            sc.delete(d, "R3 folded name")


def rule_shapes(sc: Sidecar, uses: dict[str, list], families: dict[str, str]) -> None:
    """A declared symbol gets the family tuple its uses write."""
    for _i, kind, name, kv, desc in sc.decls():
        if kind not in ("param", "var") or name not in uses:
            continue
        shape, _why = shape_of_uses(uses[name], families)
        if shape is None:
            continue
        declared = [f for f in kv.get("shape", "-").split(",") if f and f != "-"]
        if shape == declared:
            continue
        new = dict(kv)
        new["shape"] = ",".join(shape) if shape else "-"
        if kind == "param" and new.get("kind") in ("scalar", "vector", "matrix"):
            new["kind"] = "scalar" if not shape else "vector" if len(shape) == 1 else "matrix"
        for fam in shape:
            ensure_family(sc, fam, "family a shape read off the formulas names")
        sc.set(kind, name, new, desc, "R4 shape")


# --------------------------------------------------------------------------- #
# R5 — missing names, declared on stated evidence only
# --------------------------------------------------------------------------- #


#: Labels that name a bound or a reference value of the base quantity.
BOUND_LABELS = frozenset(
    {"min", "max", "lb", "ub", "lower", "upper", "nom", "nominal", "plan", "planned"}
    | {"sched", "scheduled"}
)
#: Labels that name a direction or phase of the same quantity.
PHASE_LABELS = frozenset(
    {"up", "dn", "down", "arr", "dep", "arrival", "departure", "in", "out", "entr", "exit"}
    | {"entry", "plus", "minus", "start", "end"}
)
ACCENT_LABELS = frozenset(
    {
        "hat",
        "bar",
        "tilde",
        "star",
        "underline",
        "vec",
        "lvec",
        "lrvec",
        "dot",
        "ddot",
        "breve",
        "check",
    }
)
#: Share of declared variants in the assisted sidecars (2026-11-26, n = 1,201)
#: whose kind equals what rule R8 would assign; the rule's measured agreement
#: with an independent source, not a ground-truth precision.
VARIANT_AGREEMENT = {
    "variant of a parameter": (486, 519),
    "bound label on a variable": (119, 119),
    "letters, numbers or direction on a variable": (226, 266),
}


def variant_kind(
    name: str, params: dict[str, dict[str, str]], variables: dict[str, dict[str, str]]
) -> tuple[str, dict[str, str], str] | None:
    """R8: ``(kind, key-values to inherit, reason)`` for a labelled variant of
    a declared symbol, or ``None`` when the rule does not decide."""
    toks = name.split("_")
    for k in range(len(toks) - 1, 0, -1):
        base = "_".join(toks[:k])
        if base in params or base in variables:
            labels = toks[k:]
            break
    else:
        return None
    if base in params:
        return "param", {}, f"variant of the declared parameter {base}"
    low = [x.lower() for x in labels]
    if any(x in BOUND_LABELS for x in low):
        return "param", {}, f"a bound or reference value of the declared variable {base}"
    if any(x in ACCENT_LABELS for x in low):
        return None
    if all(
        len(x) == 1 or x.isdigit() or x.lower() in PHASE_LABELS or re.fullmatch(r"[a-z]p+", x)
        for x in labels
    ):
        dom = variables[base].get("domain", "continuous")
        return "var", {"domain": dom}, f"variant of the declared variable {base}"
    return None


def deduce_variables(
    sc: Sidecar, norm_rows: dict[str, str], families: dict[str, str]
) -> dict[str, str]:
    """R7, non-vacuity: undeclared names that must be variables, with the row
    that says so. (The linearity half lives in :func:`rule_missing`.)"""
    params, variables = set(sc.names("param")), set(sc.names("var"))
    indices = set(sc.names("index"))
    derived = set(norm_rows.get("", "").split())
    known = frozenset(params | variables | derived)
    out: dict[str, str] = {}
    for row, text in sorted(norm_rows.items()):
        if not row:
            continue
        body = re.sub(r"^\s*(?:\\(?:min|max)\\quad)?\s*&", "", text)
        objective = bool(re.match(r"\s*\\(?:min|max)\\quad", text))
        o = outline_row(body, known)
        if o is None:
            continue
        names: list[str] = []
        for side in o.sides:
            for u in side:
                if u.in_tail or u.name in derived or _operator_word(u.name, bool(u.pieces)):
                    continue
                if not u.pieces and (u.name in families or u.name in indices):
                    continue  # an index letter or a family used as a number
                names.append(u.name)
        unknown = sorted({n for n in names if n not in known and _IDENT.fullmatch(n)})
        has_var = any(n in variables for n in names)
        if len(o.comparators) == 1 and o.comparators[0] == "eq" and len(o.sides) == 2:
            for k in (0, 1):
                side, other = o.sides[k], o.sides[1 - k]
                defined = o.alone[k] and side and side[0].name in unknown
                if defined and any(u.name in variables for u in other):
                    out.setdefault(side[0].name, f"row {row} sets it equal to variables")
        if not has_var and len(unknown) == 1:
            if objective and not o.comparators:
                out.setdefault(unknown[0], f"objective {row} names no other variable")
            elif o.comparators and any(c != "eq" for c in o.comparators):
                out.setdefault(unknown[0], f"inequality {row} names no other variable")
    return out


def rule_missing(
    sc: Sidecar,
    uses: dict[str, list],
    families: dict[str, str],
    domains: dict[str, str],
    table_kinds: dict[str, tuple[str, dict[str, str], str]],
    deduced: dict[str, str] | None = None,
    *,
    variants: bool = True,
    bound: frozenset[str] = frozenset(),
) -> None:
    """Declare used names nobody declared. ``bound`` holds the letters some
    row binds (a capital bound as ``T \\in \\mathcal{T}`` is an index letter). ``variants=False`` holds rule R8
    back: it has the weakest footing and must not pre-empt what the paper or
    the algebra decides once more names are known."""
    sc.undeclared = {}
    deduced = deduced or {}
    symbols = set(sc.names("param")) | set(sc.names("var"))
    for name in sorted(uses):
        if name in symbols or not _IDENT.fullmatch(name):
            continue
        if _operator_word(name, any(u.pieces for u in uses[name])):
            continue
        variables = set(sc.names("var"))  # includes what this pass declared so far
        n_scalar = sum(1 for u in uses[name] if not u.pieces)
        n_sub = len(uses[name]) - n_scalar
        subscripted = n_scalar == 0
        says_index = name in table_kinds and table_kinds[name][0] == "index"
        is_index = name in sc.names("index")
        is_letter = name in families or name in sc.names("letter")
        # Homonyms. Papers write a set and a quantity with one letter (the
        # periods \mathcal{T} and the horizon T; the time index t and the
        # times t_{e,a}); the model keeps families and symbols apart, so both
        # may be declared. Which uses belong to the symbol is decided by how
        # most uses are written; the others stay with the set or the letter.
        mostly_subscripted = n_sub >= 2 and n_sub > n_scalar
        homonym = False
        if says_index and (not is_index or subscripted):
            sc.undeclared[name] = "a set used as a value"
            continue
        if is_index and not subscripted:
            # (a capital only: a family named by a lower-case letter is an
            # index letter's own set, and the letter alone is an index)
            if not is_letter and name not in bound and name[:1].isupper() and n_scalar > n_sub:
                # A set's own name written as a number is its size (or its
                # last element): data.
                sc.beside.add(name)
                sc.set(
                    "param",
                    name,
                    {"shape": "-", "kind": "scalar", "domain": "-"},
                    ":: parameter: the index family of this name used as a number",
                    "R5 parameter (family as a number)",
                )
                continue
            if not mostly_subscripted:
                sc.undeclared[name] = "a set used as a value"
                continue
            homonym = True
        if is_letter and n_scalar:
            if not mostly_subscripted:
                sc.undeclared[name] = "index letter used as a value"
                continue
            homonym = True
        # a letter whose every occurrence carries subscripts is (also) a symbol
        own = [u for u in uses[name] if u.pieces] if homonym else uses[name]
        shape, why = shape_of_uses(own, families)
        coefficient = any(variables.intersection(u.product) for u in own)
        if homonym:
            # Only what the paper states or the algebra forces declares a
            # homonym: a letter next to a variable may be a call argument.
            stated = name in table_kinds and table_kinds[name][0] in ("var", "param")
            if not (name in domains or stated or name in deduced):
                sc.undeclared[name] = (
                    "homonym of an index name; neither the paper nor a row decides its kind"
                )
                continue
            coefficient = False
            sc.beside.add(name)
        if coefficient and name in deduced:
            sc.undeclared[name] = (
                "deductions disagree (coefficient of a variable, and " + deduced[name] + ")"
            )
            continue
        variant = (
            variant_kind(name, sc.names("param"), sc.names("var"))
            if variants and not homonym
            else None
        )
        evidence = (
            name in domains
            or name in table_kinds
            or coefficient
            or name in deduced
            or variant is not None
        )
        if shape is None:
            sc.undeclared[name] = ("kind known, no shape: " if evidence else "no evidence; ") + why
            continue
        if not evidence:
            sc.undeclared[name] = "no evidence"
        shape_s = ",".join(shape) if shape else "-"
        pkind = "scalar" if not shape else "vector" if len(shape) == 1 else "matrix"
        for fam in shape:
            ensure_family(sc, fam, "family a shape read off the formulas names")
        if name in domains:
            sc.set(
                "var",
                name,
                {
                    "shape": shape_s,
                    "domain": domains[name],
                    "role": "primary",
                    "drole": "-",
                    "lo": "-",
                    "hi": "-",
                },
                ":: variable: a domain row of the paper declares it",
                "R5 variable (domain row)",
            )
        elif name in table_kinds:
            kind, extra, where = table_kinds[name]
            if kind == "var":
                sc.set(
                    "var",
                    name,
                    {
                        "shape": shape_s,
                        "domain": extra["domain"],
                        "role": "primary",
                        "drole": "-",
                        "lo": "-",
                        "hi": "-",
                    },
                    f":: variable: the paper says so ({where})",
                    "R5 variable (paper states)",
                )
            elif kind == "param":
                sc.set(
                    "param",
                    name,
                    {"shape": shape_s, "kind": extra.get("kind") or pkind, "domain": "-"},
                    f":: parameter: the paper says so ({where})",
                    "R5 parameter (paper states)",
                )
        elif coefficient:
            # In a linear model a term holds one variable, so whatever
            # multiplies a declared variable is a parameter.
            sc.set(
                "param",
                name,
                {"shape": shape_s, "kind": pkind, "domain": "-"},
                ":: parameter: it multiplies a declared variable (the model is linear)",
                "R7 parameter (linearity)",
            )
        elif name in deduced:
            sc.set(
                "var",
                name,
                {
                    "shape": shape_s,
                    "domain": "continuous",
                    "role": "primary",
                    "drole": "-",
                    "lo": "-",
                    "hi": "-",
                },
                f":: variable: {deduced[name]} (a row constrains at least one variable)",
                "R7 variable (non-vacuity)",
            )
        elif variant is not None:
            kind, extra, why = variant
            if kind == "var":
                sc.set(
                    "var",
                    name,
                    {
                        "shape": shape_s,
                        "domain": extra["domain"],
                        "role": "primary",
                        "drole": "-",
                        "lo": "-",
                        "hi": "-",
                    },
                    f":: variable: {why}",
                    "R8 variable (variant)",
                )
            else:
                sc.set(
                    "param",
                    name,
                    {"shape": shape_s, "kind": pkind, "domain": "-"},
                    f":: parameter: {why}",
                    "R8 parameter (variant)",
                )


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def _load_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def paper_rows(key: str, decisions: dict) -> tuple[Dossier | None, list[P.Row]]:
    dp = P.DOSSIERS / f"{key}.json"
    if not dp.exists() or key not in decisions:
        return None, []
    dossier = Dossier.load(dp)
    rows, _counts = P.rows_for(dossier, decisions[key])
    rows, _absorbed = P.absorb_declaration_rows(rows)
    return dossier, rows


def normalized_rows(dossier: Dossier, rows: list[P.Row], declarations: str) -> dict[str, str]:
    """row name -> the row's text after M1b, under the given declarations.

    The key ``""`` holds the names of the derived parameters the normalizer
    created (space separated): they are its own, never declared here.
    """
    doc = P.assemble(dossier, rows, declarations, entry_id=P.entry_id_for(dossier.key))
    try:
        norm, _prov = normalize_latex(doc, source="corpusbuilder.detdecl")
    except Exception:  # a normalizer failure is promote's finding, not ours
        return {}
    out: dict[str, str] = {}
    derived = re.findall(r"^%@ (?:param|var) ([A-Za-z_]\w*) [^\n]*:: derived: ", norm, re.MULTILINE)
    out[""] = " ".join(derived)
    for line in norm.splitlines():
        m = re.search(r"\\tag\{([^{}]*)\}", line)
        if m:
            out[m.group(1).replace("\\_", "_")] = line[: m.start()]
    return out


def rule_kind_correction(sc: Sidecar, uses: dict[str, list], evidence: dict[str, Evidence]) -> None:
    """R6: where the paper names a symbol's kind outright (a notation table's
    group header, the words "variable" or "parameter" in its own definition)
    and the assisted declaration says the other kind, the paper is followed."""
    for _i, kind, name, kv, desc in sc.decls():
        if kind not in ("param", "var") or name not in uses:
            continue
        ev = evidence.get(name_key(name))
        if ev is None or ev[0] not in ("param", "var") or ev[0] == kind or not ev[1].get("strong"):
            continue
        shape = kv.get("shape", "-")
        if ev[0] == "var":
            new = {"shape": shape, "domain": ev[1].get("domain", "continuous"), "role": "primary"}
            new.update({"drole": "-", "lo": "-", "hi": "-"})
        else:
            arity = len([f for f in shape.split(",") if f and f != "-"])
            pkind = ev[1].get("kind") or (
                "scalar" if not arity else "vector" if arity == 1 else "matrix"
            )
            new = {"shape": shape, "kind": pkind, "domain": "-"}
        sc.delete(name, "R6 kind (paper states)")
        sc.set(ev[0], name, new, desc, "R6 kind (paper states)")
        sc.changes["R6 kind (paper states)"] -= 1  # one correction, counted once


# --------------------------------------------------------------------------- #
# Kind evidence: what the paper itself says a symbol is
# --------------------------------------------------------------------------- #

_W_VAR = re.compile(r"\b(?:decision\s+)?variables?\b|\bdecision\b", re.IGNORECASE)
# A description declares a set or an index only when it opens with the word
# ("Set of stations", "Station index", "The collection of ..."); a mention
# further in ("... of the train with index k") describes something else.
_W_SET = re.compile(
    r"^\s*(?:the\s+|a\s+)?(?:sub)?sets?\b|^\s*(?:the\s+|a\s+)?collection of\b", re.IGNORECASE
)
_W_INDEX = re.compile(
    r"^\s*(?:the\s+|an?\s+)?(?:[\w-]+\s+){0,2}ind(?:ex|ices|exes)\b", re.IGNORECASE
)
_W_PARAM = re.compile(
    r"\bparameters?\b|\bconstants?\b|\bgiven\b|\binput\b|\bpre-?(?:defined|specified|determined)\b"
    r"|\bknown\b|\bcoefficients?\b|\bweight(?:s|ing)?\b|\bpenalt(?:y|ies)\b|\bbig[- ]?M\b"
    r"|\bsufficiently large\b|\blarge (?:positive )?(?:number|constant|value|integer)\b"
    r"|\bscheduled\b|\bplanned\b|\bnominal\b|\bminimum\b|\bmaximum\b|\bminimal\b|\bmaximal\b"
    r"|\bcapacity\b|\bthreshold\b",
    re.IGNORECASE,
)
#: Words that name the kind outright; only these (and a table's group header)
#: are strong enough to overrule an assisted declaration (rule R6).
_W_PARAM_STRONG = re.compile(
    r"\bparameters?\b|\bconstants?\b|\bgiven\b|\binput\b|\bcoefficients?\b|\bweight(?:s|ing)?\b"
    r"|\bbig[- ]?M\b|\bsufficiently large\b|\blarge (?:positive )?(?:number|constant|value|integer)\b",
    re.IGNORECASE,
)
_W_BIG_M = re.compile(
    r"\bbig[- ]?M\b|\bsufficiently large\b|\blarge (?:positive )?(?:number|constant|value|integer)\b",
    re.IGNORECASE,
)
_W_BINARY = re.compile(
    r"\bbinary\b|\b0\s*[-–/]\s*1\b|\bboolean\b|\b(?:1|one),? if\b", re.IGNORECASE
)
_W_INTEGER = re.compile(r"\binteger\b", re.IGNORECASE)
_W_NONNEG = re.compile(r"\bnon-?negative\b", re.IGNORECASE)
_G_VAR = re.compile(r"\bvariables?\b", re.IGNORECASE)
_G_PARAM = re.compile(r"\bparameters?\b|\binputs?\b|\bconstants?\b|\bdata\b", re.IGNORECASE)
_G_SET = re.compile(r"\bsets?\b|\bind(?:ex|ices|exes)\b|\bsubscripts?\b", re.IGNORECASE)
_MATH_ONLY = re.compile(r"^(?:⟨[^⟩]*⟩[\s,;/]*(?:and|or)?\s*)+$")
_MATH = re.compile(r"⟨([^⟩]*)⟩")

#: ``⟨a⟩, ⟨b⟩ and`` in front of a symbol / ``, ⟨b⟩ and ⟨c⟩`` behind it
_ENUM_BEFORE = re.compile(r"⟨[^⟩]*⟩\s*(?:,|;|/|&|\band\b|\bor\b|,\s*and\b|as well as)\s*$")
_ENUM_AFTER = re.compile(r"\s*(?:,|;|/|&|\band\b|\bor\b|,\s*and\b|as well as)\s*⟨[^⟩]*⟩")

Evidence = tuple[str, dict[str, str], str]


def _var_domain(text: str) -> str:
    if _W_BINARY.search(text):
        return "binary"
    if _W_INTEGER.search(text):
        return "integer"
    if _W_NONNEG.search(text):
        return "non_negative"
    return "continuous"


def kind_of_words(text: str, group: str | None = None) -> tuple[str, dict[str, str]] | None:
    """``(kind, extra)`` read off a description, a group header taking over
    when the description itself names no kind; ``None`` when neither does."""
    if _W_VAR.search(text):
        return "var", {"domain": _var_domain(text), "strong": "1"}
    if _W_SET.search(text) or _W_INDEX.search(text):
        return "index", {}
    if _W_PARAM.search(text):
        strong = "1" if _W_PARAM_STRONG.search(text) or group == "param" else ""
        return "param", {"kind": "big_m" if _W_BIG_M.search(text) else "", "strong": strong}
    if group == "var":
        return "var", {"domain": _var_domain(text), "strong": "1"}
    if group == "param":
        return "param", {"kind": "", "strong": "1"}
    if group == "index":
        return "index", {}
    return None


def _group_of(text: str) -> str | None:
    """The one kind a header or caption names, or ``None`` (several or none)."""
    hits = [
        k for k, rx in (("var", _G_VAR), ("param", _G_PARAM), ("index", _G_SET)) if rx.search(text)
    ]
    return hits[0] if len(hits) == 1 else None


def name_key(name: str) -> str:
    """Order-free key of a folded name: ``t_arr_1`` and ``t_1_arr`` agree."""
    toks = [t for t in name.split("_") if t]
    return toks[0] + "|" + ",".join(sorted(toks[1:])) if toks else ""


_COMBINING_ACCENTS = {
    "\u0303": "tilde",
    "\u02dc": "tilde",
    "\u0302": "hat",
    "\u0304": "bar",
    "¯": "bar",
    "\u20d7": "vec",
    "\u0307": "dot",
}
_COMBINING_ACCENT = re.compile(
    r"\\overset\s*\{\s*(" + "|".join(_COMBINING_ACCENTS) + r")\s*\}\s*\{\s*([^{}]*)\s*\}"
)


def symbol_key(latex: str, letters: set[str]) -> str | None:
    """The :func:`name_key` of a symbol as the paper writes it: its base and
    its label scripts, index letters dropped (case kept: ``T`` is not ``t``)."""
    from corpusbuilder import indices as ix
    from corpusbuilder.declare import is_index_token

    # publishers write an accent as a combining character above the symbol
    latex = _COMBINING_ACCENT.sub(
        lambda am: "\\" + _COMBINING_ACCENTS[am.group(1)] + "{" + am.group(2) + "}", latex
    )
    n = ix.normalise(latex)
    m = ix._IDENT.search(n)
    if not m or n[: m.start()].strip():
        return None
    sub, sup, _end = ix._scripts(n, m.end())
    labels = [
        tok
        for g in sub + sup
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9]*|\d+", g)
        if not is_index_token(tok, letters)
    ]
    return name_key("_".join([m.group(1), *labels]))


def kind_evidence(key: str, letters: set[str]) -> dict[str, Evidence]:
    """symbol key -> ``(kind, extra, where)`` from the paper's notation tables,
    definition lists and defining sentences. A notation row decides before a
    sentence does; sentences that disagree decide nothing."""
    from corpusbuilder.roles import propose_roles

    disc = load_record(key, DISCOVERY) or {}
    out: dict[str, Evidence] = {}

    def note(latex: str, kind: tuple[str, dict[str, str]] | None, where: str) -> None:
        if kind is None:
            return
        for piece in _split_commas(latex):
            k = symbol_key(piece, letters)
            if k:
                out.setdefault(k, (kind[0], kind[1], where))

    for t in disc.get("tables", []):
        if not t.get("notation"):
            continue
        where = t.get("label") or t.get("id") or "table"
        group = _group_of(t.get("caption") or "")
        for r in t["rows"]:
            cells = [(c.get("text") or "").strip() for c in r.get("cells") or []]
            sym_at = next((i for i, c in enumerate(cells) if c and _MATH_ONLY.fullmatch(c)), None)
            if sym_at is None:
                words = " ".join(c for c in cells if c)
                g = _group_of(words) if not _MATH.search(words) else None
                if g and len(words) < 60:
                    group = g  # a group header row: "Parameters", "Decision variables"
                continue
            lead = " ".join(c for c in cells[:sym_at] if c)
            if lead and _group_of(lead):
                group = _group_of(lead)  # a group column: Set | S | ...
            desc = " ".join(c for c in cells[sym_at + 1 :] if c)
            kind = kind_of_words(_MATH.sub(" ", desc), group)
            for latex in _MATH.findall(cells[sym_at]):
                note(latex, kind, where)
    for it in disc.get("deflists", []):
        if it.get("term"):
            kind = kind_of_words(_MATH.sub(" ", it.get("def", "")))
            for latex in _MATH.findall(it["term"]) or [it["term"]]:
                note(latex, kind, "definition list")

    said: dict[str, set[tuple[str, str]]] = {}
    where_said: dict[str, str] = {}
    roles = propose_roles(disc) if disc.get("maths") else {}
    for m in disc.get("maths", []):
        role = roles.get(m["id"]) or {}
        sp = role.get("span")
        if (
            m.get("where") != "inline"
            or m.get("cls") != "symbol"
            or role.get("role") != "definition"
        ):
            continue
        if not sp or not sp.get("text"):
            continue
        text = sp["text"]
        at = text.find("⟨" + m["latex"] + "⟩")
        if at < 0:
            continue
        # An enumeration shares its description: in "⟨a⟩, ⟨b⟩ and ⟨c⟩ are
        # weights" the words after the last symbol describe all three, and in
        # "the weights ⟨a⟩ and ⟨b⟩" the words before the first do.
        lo, hi = at, at + len(m["latex"]) + 2
        while True:
            back = _ENUM_BEFORE.search(text[:lo])
            if back is None:
                break
            lo = back.start()
        while True:
            ahead = _ENUM_AFTER.match(text[hi:])
            if ahead is None:
                break
            hi += ahead.end()
        before = text[max(0, lo - 70) : lo].rsplit("⟩", 1)[-1]
        after = text[hi : hi + 90].split("⟨", 1)[0]
        kind = kind_of_words(before + " | " + after)
        if kind is None and len(_MATH.findall(text)) == 1:
            # a sentence about this one symbol: its words anywhere describe it
            kind = kind_of_words(_MATH.sub(" ", text))
        if kind is None:
            continue
        for piece in _split_commas(m["latex"]):
            k = symbol_key(piece, letters)
            if k:
                said.setdefault(k, set()).add((kind[0], json.dumps(kind[1], sort_keys=True)))
                where_said.setdefault(k, f"paragraph {sp.get('para')}")
    for k, kinds in said.items():
        if k in out or len({a for a, _b in kinds}) != 1:
            continue
        a, b = sorted(kinds)[0]
        out[k] = (a, json.loads(b), where_said[k])
    return out


#: Greek letters as publishers write them in running text (outside maths)
_GREEK_PROSE = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "epsilon": "εϵ",
    "varepsilon": "εϵ",
    "zeta": "ζ",
    "eta": "η",
    "theta": "θϑ",
    "vartheta": "θϑ",
    "iota": "ι",
    "kappa": "κ",
    "lambda": "λ",
    "mu": "μµ",
    "nu": "ν",
    "xi": "ξ",
    "pi": "π",
    "varpi": "ϖ",
    "rho": "ρ",
    "sigma": "σ",
    "tau": "τ",
    "upsilon": "υ",
    "phi": "φϕ",
    "varphi": "φϕ",
    "chi": "χ",
    "psi": "ψ",
    "omega": "ω",
    "Gamma": "Γ",
    "Delta": "Δ",
    "Theta": "Θ",
    "Lambda": "Λ",
    "Xi": "Ξ",
    "Pi": "Π",
    "Sigma": "Σ",
    "Phi": "Φ",
    "Psi": "Ψ",
    "Omega": "Ω",
    "ell": "ℓ",
}
_PROSE_NOUN = r"(?P<noun>variables?|parameters?|constants?|coefficients?|weights?)"
_PROSE_INDEX_RUN = r"(?:\s{0,3}[A-Za-z′'](?:\s?,?\s?[A-Za-z0-9′']){0,6})?"
#: "binary decision variable" + symbol: the kind noun stands right before it
_PROSE_BEFORE = re.compile(
    r"(?P<adj>(?:\b[\w–-]+\s+){0,3})\b" + _PROSE_NOUN + r"\s+(?:vector\s+)?$", re.IGNORECASE
)
#: symbol + "is a binary variable" / symbol + "variables"
_PROSE_AFTER = re.compile(
    r"^"
    + _PROSE_INDEX_RUN
    + r"\s*(?:(?:is|are|denotes?|represents?)\s+(?:a|an|the)\s+(?P<adj>(?:[\w–-]+\s+){0,3}?))?"
    + _PROSE_NOUN
    + r"\b",
    re.IGNORECASE,
)
_PROSE_NOT = re.compile(r"\bdual\b|lagrang|multiplier|\brandom\b", re.IGNORECASE)


def _prose_kind(adjectives: str, noun: str) -> tuple[str, dict[str, str]] | None:
    if _PROSE_NOT.search(adjectives):
        return None  # a multiplier of a relaxation is neither of the model's kinds
    if noun.lower().startswith("variable"):
        low = adjectives.lower()
        domain = (
            "binary"
            if re.search(r"\bbinary\b|\bboolean\b|0\s*[–-]\s*1", low)
            else "integer"
            if "integer" in low
            else "continuous"
        )
        return "var", {"domain": domain}
    return "param", {"kind": ""}


def prose_evidence(key: str, names: list[str]) -> dict[str, Evidence]:
    """name -> ``(kind, extra, where)`` from running text that names a Greek
    symbol outside maths ("where α 1 is a binary variable", "the tolerance
    parameter ξ"). Publishers set many inline symbols as plain characters; the
    notation tables and defining sentences read only marked-up maths.

    Deliberately narrow: the kind noun must stand right before the symbol or
    right after it ("is a ... variable"), a sentence that calls it both ways
    decides nothing, and a symbol with a label (``α 1``) is never taken for
    the bare letter."""
    disc = load_record(key, DISCOVERY) or {}
    paras = [
        (p.get("i"), re.sub(r"⟨[^⟩]*⟩", " ⟨⟩ ", p.get("plain") or ""))
        for p in disc.get("paras", [])
    ]
    greek = [n for n in names if n.split("_")[0] in _GREEK_PROSE]
    out: dict[str, Evidence] = {}
    for name in sorted(greek):
        base, *rest = name.split("_")
        if any(r in ACCENT_LABELS for r in rest):
            continue  # an accent does not survive as plain text
        siblings = sorted(
            {
                o.split("_")[1]
                for o in names
                if o != name and o.split("_")[0] == base and len(o.split("_")) > len(rest) + 1
            }
        )
        pat = re.compile(
            r"(?<![A-Za-z0-9])["
            + _GREEK_PROSE[base]
            + "]"
            + "".join(r"\s{0,2}" + re.escape(r) for r in rest)
            + r"(?![0-9])"
        )
        found: set[tuple[str, str]] = set()
        where = ""
        for i, plain in paras:
            for m in pat.finditer(plain):
                after = plain[m.end() : m.end() + 90]
                if re.match(r"\s{0,2}\d", after) or any(
                    re.match(r"\s{0,2}" + re.escape(sib) + r"(?![A-Za-z])", after)
                    for sib in siblings
                ):
                    continue  # another symbol: the letter with a label
                clause = re.split(r"[.;:()]", plain[max(0, m.start() - 60) : m.start()])[-1]
                hit = _PROSE_BEFORE.search(clause) or _PROSE_AFTER.match(after)
                if hit is None:
                    continue
                kind = _prose_kind(hit.group("adj") or "", hit.group("noun"))
                if kind is not None:
                    found.add((kind[0], json.dumps(kind[1], sort_keys=True)))
                    where = where or f"running text, paragraph {i}"
        if len({k for k, _e in found}) == 1:
            kinds = sorted(found)
            # binary before integer before continuous: the most specific wording
            extra = json.loads(kinds[0][1])
            if kinds[0][0] == "var":
                doms = {json.loads(e)["domain"] for _k, e in kinds}
                extra = {
                    "domain": "binary"
                    if "binary" in doms
                    else "integer"
                    if "integer" in doms
                    else "continuous"
                }
            out[name] = (kinds[0][0], extra, where)
    return out


def _split_commas(latex: str) -> list[str]:
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in latex:
        if ch in "{(":
            depth += 1
        elif ch in "})":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def repair(key: str, text: str, decisions: dict, *, rules: frozenset[str]) -> tuple[str, dict]:
    """The repaired sidecar text and what changed, for one paper."""
    sc_base = restore(text)
    sc = Sidecar(sc_base)
    idx = _load_json(INDICES / f"{key}.json")
    letters = index_letters(idx)
    if "R1" in rules:
        rule_metadata(sc)
    if "R2" in rules:
        rule_letters(sc, letters)
        rule_shape_families(sc)
    if rules & {"R3", "R4", "R5"}:
        dossier, rows = paper_rows(key, decisions)
        if dossier is not None and rows:
            all_rows, _counts = P.rows_for(dossier, decisions[key])
            for _pass in range(3):
                norm = normalized_rows(dossier, rows, sc.text())
                if not norm:
                    break
                uses = row_uses(norm)
                fams = {
                    **{k: v["over"] for k, v in sc.names("letter").items()},
                    **body_families(norm),
                }
                if "R3" in rules:
                    rule_folded_names(sc, uses, fams)
                if "R4" in rules:
                    rule_shapes(sc, uses, fams)
                if "R5" in rules:
                    norm_all = normalized_rows(dossier, all_rows, sc.text())
                    domains: dict[str, str] = {}
                    for row_name, row_text in norm_all.items():
                        if not row_name:
                            continue
                        for sym, dom in domain_declaration(row_text.lstrip(" &")).items():
                            domains.setdefault(_plain(sym), dom)
                    tk = {}
                    by_key = kind_evidence(key, set(fams))
                    if "R6" in rules:
                        rule_kind_correction(sc, uses, by_key)
                    for name in uses:
                        hit = by_key.get(name_key(name))
                        if hit:
                            tk[name] = hit
                    for name, hit in prose_evidence(key, sorted(uses)).items():
                        tk.setdefault(name, hit)
                    bound = frozenset(bound_letters("\n".join(norm_all.values())))
                    # what the paper states and what the algebra implies first,
                    # to a fixpoint; labelled variants (R8) only after that
                    for with_variants in (False, True):
                        for _round in range(4):
                            before = sum(sc.changes.values())
                            deduced = deduce_variables(sc, norm, fams) if "R7" in rules else {}
                            rule_missing(
                                sc,
                                uses,
                                fams,
                                domains,
                                tk,
                                deduced,
                                variants=with_variants,
                                bound=bound,
                            )
                            if sum(sc.changes.values()) == before:
                                break
                rule_shape_families(sc, norm)
    final = sc.text()
    changes = dict(sorted(Counter(sc.rule_of.values()).items())) if final != sc_base else {}
    if sc.undeclared:
        changes["_undeclared"] = dict(sorted(sc.undeclared.items()))  # type: ignore[assignment]
    return sc.text(), changes


def _plain(name: str) -> str:
    m = re.fullmatch(r"\\mathit\{([^{}]*)\}", name.strip())
    return (m.group(1) if m else name.strip()).replace("\\_", "_").replace(" ", "")


ALL_RULES = frozenset({"R1", "R2", "R3", "R4", "R5", "R6", "R7"})

#: Rule sets tried per paper, fullest first. The canonical parser decides
#: between them: the set under which most rows of the paper promote is kept,
#: ties go to the fuller set. A repair that costs a paper rows is thereby
#: rolled back for that paper; nothing is tuned by hand.
VARIANTS: tuple[tuple[str, frozenset[str]], ...] = (
    ("all", ALL_RULES),
    ("no-kind-correction", ALL_RULES - {"R6"}),
    ("no-deduction", ALL_RULES - {"R6", "R7"}),
    ("no-letters", frozenset({"R1", "R4", "R5", "R7"})),
)


def score(key: str, text: str, decisions: dict, scratch: Path) -> tuple[int, int]:
    """``(canonical rows, parsing rows)`` of a paper under one sidecar text,
    measured by promote itself (``promote_paper`` with ``--partial``)."""
    dp = P.DOSSIERS / f"{key}.json"
    if not dp.exists() or key not in decisions:
        return (0, 0)
    scratch.mkdir(parents=True, exist_ok=True)
    (scratch / f"{key}.tex").write_text(text, encoding="utf-8", newline="\n")
    out = P.promote_paper(
        Dossier.load(dp), decisions[key], declarations_dir=scratch, write=False, partial=True
    )
    cov = out.coverage or {}
    parsing = cov.get("rows_ok", out.rows_included if out.promoted else 0)
    return (out.rows_included if out.promoted else 0, parsing)


def repair_best(
    key: str, text: str, decisions: dict, scratch: Path, *, exhaustive: bool = True
) -> tuple[str, dict]:
    """The variant the parser prefers for this paper, and the record of the choice.

    ``exhaustive=False`` tries the fallback rule sets only when the full set
    promotes fewer rows than the assisted sidecar (a quick mode for
    development; the corpus run is exhaustive).
    """
    base = restore(text)
    scores: dict[str, tuple[int, int]] = {
        "assisted": score(key, base, decisions, scratch / "assisted")
    }
    seen: dict[str, str] = {}
    best: tuple[tuple[int, int], str, str, dict] | None = None
    for name, rules in VARIANTS:
        new, changes = repair(key, base, decisions, rules=rules)
        if new in seen:
            scores[name] = scores[seen[new]]
            continue
        seen[new] = name
        sc_ = score(key, new, decisions, scratch / name)
        scores[name] = sc_
        if best is None or sc_ > best[0]:
            best = (sc_, name, new, changes)
        if not exhaustive and sc_ >= scores["assisted"]:
            break
    assert best is not None
    if scores["assisted"] > best[0]:
        return base, {"variant": "assisted", "scores": scores, "changes": {}}
    return best[2], {"variant": best[1], "scores": scores, "changes": best[3]}


def scratch_sidecar(key: str, decisions: dict) -> str | None:
    """The empty sidecar of a paper that has none: an objective line whose
    sense is the paper's own objective row's, nothing else. ``None`` when the
    paper has no single objective row to read the sense from."""
    dossier, rows = paper_rows(key, decisions)
    if dossier is None:
        return None
    objectives = [r for r in rows if r.is_objective]
    if len(objectives) != 1:
        return None
    from corpusbuilder.objective import objective_split

    head = objective_split(objectives[0].latex)
    if head is None:
        return None
    return (
        f"% Declaration sidecar for {key}\n{FROM_SCRATCH}\n"
        f"%@ obj sense={head[0]} name=objective combination=sum :: -\n"
    )


def _work(args: tuple[str, str]) -> tuple[str, str, dict]:
    key, scratch = args
    decisions = _DECISIONS
    path = P.DECLARATIONS / f"{key}.tex"
    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = scratch_sidecar(key, decisions) or ""
    if not text:
        return key, "", {"variant": "none", "scores": {}, "changes": {}}
    new, record = repair_best(key, text, decisions, Path(scratch) / key, exhaustive=_EXHAUSTIVE)
    if FROM_SCRATCH in text and record["scores"].get(record["variant"], (0, 0))[0] == 0:
        # rules alone did not promote the paper: leave it without a sidecar
        return key, "", {"variant": "none", "scores": record["scores"], "changes": {}}
    return key, new, record


_DECISIONS: dict = {}
_EXHAUSTIVE = True


def run(
    out_dir: Path | None, *, only: set[str] | None = None, jobs: int = 4, exhaustive: bool = True
) -> dict:
    import tempfile
    from multiprocessing import Pool

    global _DECISIONS, _EXHAUSTIVE
    _EXHAUSTIVE = exhaustive
    _DECISIONS, _ = P.load_decisions(sorted(P.DECISIONS.glob("*.json")))
    target = out_dir or P.DECLARATIONS
    target.mkdir(parents=True, exist_ok=True)
    keys = []
    for path in sorted(P.DECLARATIONS.glob("*.tex")):
        if path.name.endswith((".stub.tex", ".vocab.tex")):
            continue
        key = path.name[: -len(".tex")]
        if only and key not in only:
            if out_dir is not None:
                shutil.copy2(path, target / path.name)
            continue
        keys.append(key)
    # papers with decisions and a dossier but no sidecar at all: rules only
    for key in sorted(_DECISIONS):
        if key not in keys and (not only or key in only) and (P.DOSSIERS / f"{key}.json").exists():
            if not (P.DECLARATIONS / f"{key}.stub.tex").exists():
                continue  # promote never reached the sidecar step for this paper
            keys.append(key)
    totals: Counter[str] = Counter()
    variants: Counter[str] = Counter()
    per_paper: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="detdecl-") as scratch, Pool(jobs) as pool:
        results = pool.map(_work, [(k, scratch) for k in keys], chunksize=2)
    for key, new, record in sorted(results):
        if not new:
            continue
        per_paper[key] = record
        variants[record["variant"]] += 1
        totals.update({k: v for k, v in record["changes"].items() if not k.startswith("_")})
        dest = target / f"{key}.tex"
        if not dest.exists() or dest.read_text(encoding="utf-8") != new:
            dest.write_text(new, encoding="utf-8", newline="\n")
    if out_dir is not None:
        (out_dir / "_report.json").write_text(
            json.dumps(per_paper, indent=1, sort_keys=True), encoding="utf-8", newline="\n"
        )
    return {
        "schema_version": "detdecl-1",
        "papers": len(per_paper),
        "variants": dict(sorted(variants.items())),
        "totals": dict(sorted(totals.items())),
        "per_paper": per_paper,
    }


def render_md(report: dict) -> str:
    lines = [
        "# Deterministic declarations",
        "",
        "Generated by `corpusbuilder.detdecl`; do not edit. Papers: "
        f"**{report['papers']}**. Per paper the canonical parser chooses the rule set "
        "under which most rows promote.",
        "",
        "| rule set kept | papers |",
        "| --- | ---: |",
    ]
    lines += [f"| {k} | {v} |" for k, v in report["variants"].items()]
    lines += ["", "| change | lines |", "| --- | ---: |"]
    lines += [f"| {k} | {v} |" for k, v in report["totals"].items()]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", type=Path, help="write repaired copies here instead of in place")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument(
        "--quick",
        action="store_true",
        help="try fallback rule sets only when the full set loses rows (development)",
    )
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--restore", action="store_true", help="undo every repair in place")
    args = ap.parse_args(argv)
    if args.restore:
        n = 0
        for path in sorted(P.DECLARATIONS.glob("*.tex")):
            text = path.read_text(encoding="utf-8")
            if FROM_SCRATCH in text:
                path.unlink()  # a sidecar the rules wrote from nothing
                n += 1
                continue
            new = restore(text)
            if new != text:
                path.write_text(new, encoding="utf-8", newline="\n")
                n += 1
        print(f"detdecl: restored {n} sidecars", file=sys.stderr)
        return 0
    if args.out is not None and args.out.exists():
        shutil.rmtree(args.out)
    report = run(
        args.out, only=set(args.only or []) or None, jobs=args.jobs, exhaustive=not args.quick
    )
    if args.out is None:
        REPORT_JSON.write_text(
            json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        REPORT_MD.write_text(render_md(report), encoding="utf-8", newline="\n")
    print(
        f"detdecl: {report['papers']} papers; kept "
        + ", ".join(f"{k} {v}" for k, v in report["variants"].items())
        + " | "
        + ", ".join(f"{k} {v}" for k, v in report["totals"].items()),
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
