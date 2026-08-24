"""Convert the lp2graph repo-extraction corpus into canonical Formulations.

``~/lp2graph/corpus/*.json`` holds 17 repository extractions (28 models) whose
*structured fields* — ``sets_indices``, ``parameters`` (small domain vocab),
``decision_variables`` (typed), ``objective`` and ``constraints`` (LaTeX with
``name`` + ``indexed_over``) — already state most of what a canonical
``Formulation`` needs. That is exactly the information a displayed equation
cannot carry (the 0/10156 lesson recorded in :mod:`corpusbuilder.promote`), so
this material converts where the paper-extraction corpus cannot: we RENDER a
canonical LaTeX document per model (a ``%@`` declaration block derived from the
structured fields + one ``align`` row per objective/constraint) and push it
through :func:`lp2graph.mining.ingest.ingest_latex`, whose ``IngestionResult``
is the sole arbiter of success — no-exception never means converted.

Why rendering instead of building ``Formulation`` objects directly: the ingest
path runs M1b normalization, the canonical parser, and semantic validation in
one audited pipeline, and leaves behind the exact ``.tex`` that produced each
entry — the same provenance discipline promotion uses. Direct construction
would re-implement the parser's binding/shape logic and could not fail
honestly at the same stages.

Every rewrite applied to the source LaTeX is *exact* (meaning-preserving
algebra or spelling canonicalization) and spends only declared knowledge, in
the :mod:`corpusbuilder.algebra` spirit. Anything the canonical grammar cannot
express (Iverson brackets, ``\\frac`` scaling, min/max inside constraints,
multi-factor products) is left to fail with an honest cause — never forced.
Capture losses the lp2graph parser itself tolerates (binder restrictions,
numeric sum ranges) are recorded as notes in the per-model metadata sidecar so
the loss is visible instead of silent.

Exclusions (recorded in the report, per the 2026-08-13 decision): the
``iitis/railways_HOBO`` repo (QUBO — not linear), and column-generation
pricing subproblems (algorithms, not LPs). Constraint rows that are
branch-and-price machinery (cuts/branching added during the search) are
omitted with a note: they are not part of the static model.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.repo_corpus [--out DIR] [--src DIR]

Outputs (deterministic bytes across reruns):

* ``corpus/repo_formulations/<stem>__<model_id>.json``      — the Formulation
* ``corpus/repo_formulations/<stem>__<model_id>.meta.json`` — metadata sidecar
* ``corpus/repo_formulations/<stem>__<model_id>.tex``       — ingested document
* ``corpus/repo_formulations/_report.json``                 — conversion report
"""

# ruff: noqa: I001 — the ``railpminer._lp2graph`` import is a side effect (it
# puts the sibling lp2graph checkout on sys.path) and must precede the
# ``lp2graph`` imports; import sorting would break that.

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

from corpusbuilder.algebra import declared_products
from corpusbuilder.symbols import domain_declaration
from lp2graph import loads as load_formulation
from lp2graph.mining.ingest import ingest_latex, normalize_latex

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = Path.home() / "lp2graph" / "corpus"
DEFAULT_OUT = ROOT / "corpus" / "repo_formulations"

REPORT_SCHEMA = "repo-corpus-1"

#: Repo excluded wholesale: QUBO/HOBO is quadratic, not an LP/MILP.
EXCLUDED_REPOS = {"iitis/railways_HOBO": "qubo_not_linear"}

#: A model whose id/description marks it as a column-generation pricing
#: subproblem is an *algorithm component*, not a standalone LP (2026-08-13).
_PRICING = re.compile(r"pricing", re.IGNORECASE)

#: Constraint rows added dynamically by the search (cuts, branching, pricing
#: oracles) are branch-and-price machinery, not the static model. Detected
#: from the extractor's own naming and ``indexed_over`` prose, which state it.
_MACHINERY = re.compile(
    r"added during|branching|cut-generation|column.generation|pricing", re.IGNORECASE
)

# ---------------------------------------------------------------------------
# LaTeX spelling helpers
# ---------------------------------------------------------------------------

#: Greek command -> ascii name, matching M1b's ``greek_ident`` rule so that
#: declared names and normalized body spellings land on the same identifier.
_GREEK = (
    "alpha|beta|gamma|delta|epsilon|zeta|eta|theta|iota|kappa|lambda|mu|nu|xi"
    "|omicron|pi|rho|sigma|tau|upsilon|phi|chi|psi|omega"
    "|Gamma|Delta|Theta|Lambda|Xi|Pi|Sigma|Upsilon|Phi|Psi|Omega"
    "|varepsilon|vartheta|varpi|varrho|varsigma|varphi|ell"
)
_GREEK_CMD = re.compile(rf"\\({_GREEK})(?![a-zA-Z])")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

#: Meaning-free spacing/size macros. ``\left``/``\right``/``\Big`` keep their
#: delimiter character; the sizes themselves carry no algebra.
_SPACING = re.compile(r"\\(?:,|;|:|!|quad|qquad|>|\s)")
_SIZERS = re.compile(r"\\(?:left|right|[bB]igg?[lrm]?)(?=[(){}|\[\].])")


def _spell(name: str) -> str:
    """Canonical body spelling of a declared identifier."""
    if re.fullmatch(r"[A-Za-z]", name):
        return name
    return r"\mathit{" + name.replace("_", r"\_") + "}"


def _clean_ident(token: str) -> str:
    """Force a raw source token into the ``Identifier`` grammar."""
    token = token.strip()
    token = _GREEK_CMD.sub(lambda m: m.group(1), token)
    token = token.replace("\\mathcal", "").replace("\\mathit", "").replace("\\mathrm", "")
    token = token.replace("^", "_").replace("{", "").replace("}", "").replace("\\", "")
    token = re.sub(r"[^A-Za-z0-9_]+", "_", token).strip("_")
    return token


# ---------------------------------------------------------------------------
# Declared-name parsing
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(
    r"^\s*(?P<base>\\?[A-Za-z][A-Za-z0-9_\\{}]*?)"
    r"(?:\^(?P<sup>\{[^{}]*\}|[A-Za-z0-9]+))?"
    r"(?:_(?P<sub>\{[^{}]*\}|[A-Za-z0-9']+))?\s*$"
)


@dataclass
class ParsedName:
    """A declared symbol name split into base / folded superscript / binders."""

    base: str
    sup: str | None
    sub_tokens: tuple[str, ...]

    @property
    def canonical(self) -> str:
        return self.base + (f"_{self.sup}" if self.sup else "")


def parse_symbol_name(raw: str) -> ParsedName | None:
    """Split a structured-field symbol name like ``omega^e_a`` or ``m_{e,e'}^{d}``.

    Scripts may come in either order. A trailing underscore token is only a
    *binder list* candidate here; whether it really binds (vs being part of
    the name, as in ``T_end``) is decided later against the model's binder
    map — the name alone cannot know.
    """
    raw = raw.strip()
    if not raw:
        return None
    # Longest base first: everything before the rightmost script region.
    # ``x_sec_{tr,t,s}`` keeps its underscore in the base.
    m = re.match(
        r"^(?P<base>\\?[A-Za-z][A-Za-z0-9_\\]*?)"
        r"(?P<scripts>(?:[_^](?:\{[^{}]*\}|[A-Za-z0-9'+-]+))*)$",
        raw,
    )
    if not m:
        return None
    base = _clean_ident(m.group("base"))
    if not base:
        return None
    scripts = re.findall(r"([_^])(\{[^{}]*\}|[A-Za-z0-9'+-]+)", m.group("scripts"))
    subs = [i for i, (k, _) in enumerate(scripts) if k == "_"]
    sup: str | None = None
    tokens: tuple[str, ...] = ()
    for i, (kind, val) in enumerate(scripts):
        val = val.strip("{}")
        if kind == "^":
            sup = _clean_ident(val) or None
        elif subs and i == subs[-1]:
            tokens = tuple(t.strip() for t in val.split(",") if t.strip())
        else:
            # An inner subscript is part of the name (x_sec_{tr,t,s}).
            base = f"{base}_{_clean_ident(val)}"
    return ParsedName(base, sup, tokens)


# ---------------------------------------------------------------------------
# Binder -> index-family mapping
# ---------------------------------------------------------------------------

_IN_CLAUSE = re.compile(
    r"(?P<binder>\(?[A-Za-z][A-Za-z0-9']*(?:\s*,\s*[A-Za-z][A-Za-z0-9']*)*\)?)"
    r"\s*(?:=\s*\((?P<comps>[^()]*)\)\s*)?"
    r"(?:\\in|∈|\bin\b)\s*"
    r"(?P<fam>\\?[A-Za-z][A-Za-z0-9_^{}\\]*)"
)


def _resolve_family(token: str, fam_by_lower: dict[str, str]) -> str | None:
    """Resolve a set token to a declared family: exact, else its base family.

    ``S_r`` (segments of route r) resolves to declared ``S`` when ``S_r``
    itself is not declared — the subset relation is stated by the spelling.
    """
    token = _clean_ident(token)
    hit = fam_by_lower.get(token.lower())
    if hit is not None:
        return hit
    if "_" in token:
        base = token.split("_", 1)[0]
        return fam_by_lower.get(base.lower())
    return None


def _binder_map(model: dict, families: list[str]) -> tuple[dict[str, str], list[str]]:
    """Map binder letters to declared index families, spending stated facts only.

    Priority: (1) case-insensitive exact name match (``tr`` -> ``TR``); (2)
    explicit ``x in F`` clauses from ``indexed_over``/body binders; (3) tuple
    components (``a=(i,j) in A`` maps i,j to A — the component family is not
    stated, so the pair family stands in; recorded as a note).
    """
    fam_by_lower = {}
    for f in families:
        fam_by_lower.setdefault(f.lower(), f)
    explicit: dict[str, str] = {}
    tuple_comp: dict[str, str] = {}
    notes: list[str] = []

    texts: list[str] = []
    for c in model.get("constraints", []):
        texts.append(str(c.get("indexed_over") or ""))
        texts.append(str(c.get("expression_latex") or ""))
    obj = model.get("objective") or {}
    texts.append(str(obj.get("expression_latex") or ""))

    for text in texts:
        for m in _IN_CLAUSE.finditer(text):
            fam = _resolve_family(m.group("fam"), fam_by_lower)
            if fam is None:
                continue
            binder = m.group("binder").strip()
            comps = m.group("comps")
            if binder.startswith("("):
                for tok in binder.strip("()").split(","):
                    tok = tok.strip()
                    if tok:
                        tuple_comp.setdefault(tok, fam)
            else:
                explicit.setdefault(binder, fam)
            if comps:
                for tok in comps.split(","):
                    tok = tok.strip()
                    if tok:
                        tuple_comp.setdefault(tok, fam)

    out: dict[str, str] = {}
    for binder, fam in sorted(explicit.items()):
        out[binder] = fam_by_lower.get(binder.lower(), fam)
    for binder, fam in sorted(tuple_comp.items()):
        if binder not in out:
            mapped = fam_by_lower.get(binder.lower())
            out[binder] = mapped or fam
            if mapped is None:
                notes.append(f"binder {binder!r} mapped to tuple family {fam!r} (component family unstated)")
    for low, fam in sorted(fam_by_lower.items()):
        out.setdefault(low, fam)
    return out, sorted(set(notes))


# ---------------------------------------------------------------------------
# Symbol table
# ---------------------------------------------------------------------------


@dataclass
class Sym:
    """One declared parameter or variable, canonicalized."""

    name: str
    kind: str  # "param" | "var"
    shape: tuple[str, ...]
    binders: tuple[str, ...]
    description: str
    domain: str = "continuous"  # vars only
    raw_base: str = ""


@dataclass
class Table:
    indices: list[tuple[str, str]] = field(default_factory=list)
    symbols: list[Sym] = field(default_factory=list)
    binder_map: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def by_name(self) -> dict[str, Sym]:
        return {s.name: s for s in self.symbols}

    @property
    def loose(self) -> dict[str, str]:
        """Underscore/case-insensitive lookup key -> canonical name (unique only)."""
        seen: dict[str, list[str]] = {}
        for s in self.symbols:
            seen.setdefault(s.name.replace("_", "").lower(), []).append(s.name)
        return {k: v[0] for k, v in seen.items() if len(v) == 1}


class ConversionFailure(Exception):
    def __init__(self, cause: str, detail: str) -> None:
        super().__init__(f"{cause}: {detail}")
        self.cause = cause
        self.detail = detail


def _family_from_meaning(desc: str, families: list[str]) -> str | None:
    """A family named verbatim in a symbol's meaning text (>=2 chars only —
    single letters would match prose articles)."""
    for fam in sorted(families, key=len, reverse=True):
        if len(fam) >= 2 and re.search(rf"\b{re.escape(fam)}\b", desc):
            return fam
    return None


def _var_domain(type_text: str, domain_text: str) -> str:
    t = (type_text or "").lower()
    if "binary" in t:
        return "binary"
    if "integer" in t:
        return "integer"
    d = (domain_text or "").replace(" ", "")
    if d.startswith("[0,") or "[0,inf" in t.replace(" ", ""):
        return "non_negative"
    return "continuous"


def _pick_alternative(raw: str, bodies: str) -> str:
    """``y_r (lambda_r)`` names two spellings; prefer the one the algebra uses."""
    m = re.match(r"^(?P<a>[^()]+?)\s+\((?P<b>[^()=]+)\)\s*$", raw.strip())
    if not m:
        return raw
    counts = []
    for cand in (m.group("a").strip(), m.group("b").strip()):
        parsed = parse_symbol_name(cand)
        base = parsed.base if parsed else cand
        spellings = [base]
        greek = re.fullmatch(rf"(?:{_GREEK})", base)
        if greek:
            spellings.append("\\" + base)
        n = sum(bodies.count(sp) for sp in spellings)
        counts.append((n, cand))
    counts.sort(key=lambda x: -x[0])
    return counts[0][1]


def _binder_lookup(token: str, binder_map: dict[str, str]) -> str | None:
    """Resolve a binder token, tolerating primed/numbered/adjacent spellings.

    ``tr1``/``r'`` are the mapped binder plus a discriminator; ``jp``/``sp``
    spell primes as a p-suffix; ``j``/``k`` conventionally range where ``i``
    does when nothing states otherwise. Each fallback spends a stated fact.
    """
    if token in binder_map:
        return binder_map[token]
    stripped = token.rstrip("'0123456789")
    if stripped and stripped in binder_map:
        return binder_map[stripped]
    if token.endswith("p") and token[:-1] in binder_map:
        return binder_map[token[:-1]]
    if token in ("j", "k") and "i" in binder_map:
        return binder_map["i"]
    return None


def _segment(token: str, binder_map: dict[str, str], arity: int) -> tuple[str, ...] | None:
    """Split a fused subscript (``odt``) into known binder letters, or None."""
    out: list[str] = []
    for ch in token:
        if ch in "'0123456789" and out:
            out[-1] += ch
        else:
            out.append(ch)
    if len(out) != arity:
        return None
    if all(_binder_lookup(t, binder_map) is not None for t in out):
        return tuple(out)
    return None


def _split_outside_parens(s: str) -> list[str]:
    """Split on commas that sit outside parentheses (tuple binders stay whole)."""
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in s:
        if ch in "({":
            depth += 1
        elif ch in ")}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [p for p in (p.strip() for p in out) if p]


def build_table(model: dict) -> Table:
    """Derive the declaration table for one model from its structured fields."""
    tab = Table()
    families: list[str] = []
    for s in model.get("sets_indices", []):
        name = _clean_ident(str(s.get("name", "")))
        if not name or not _IDENT.match(name):
            continue
        if name not in families:
            families.append(name)
            tab.indices.append((name, str(s.get("meaning") or "")))
    tab.binder_map, notes = _binder_map(model, families)
    tab.notes.extend(notes)

    bodies = "\n".join(
        [str((model.get("objective") or {}).get("expression_latex") or "")]
        + [str(c.get("expression_latex") or "") for c in model.get("constraints", [])]
    )

    def add(raw_name: str, kind: str, desc: str, domain: str = "continuous") -> None:
        raw_name = _pick_alternative(str(raw_name), bodies)
        parsed = parse_symbol_name(raw_name)
        if parsed is None:
            tab.notes.append(f"unparseable {kind} name {raw_name!r} dropped")
            return
        binders: list[str] = []
        shape: list[str] = []
        name = parsed.canonical
        for tok in parsed.sub_tokens:
            tok_c = _clean_ident(tok)
            fam = _binder_lookup(tok_c, tab.binder_map)
            if fam is None and len(parsed.sub_tokens) == 1:
                seg = _segment(tok_c, tab.binder_map, max(2, len(tok_c)))
                if seg and 1 < len(seg) <= 4:
                    for t2 in seg:
                        binders.append(t2)
                        shape.append(tab.binder_map[t2])
                    break
            if fam is None and len(parsed.sub_tokens) > 1:
                # The symbol's own meaning may name the family outright
                # ("...TTD section s"): a stated fact, so spend it. Single
                # tokens fall through to name-consumption (T_end) instead.
                fam = _family_from_meaning(desc, [f for f, _ in tab.indices])
                if fam is not None:
                    tab.notes.append(
                        f"binder {tok!r} of {raw_name!r} mapped to {fam!r} via its meaning text"
                    )
            if fam is None:
                # Not a binder: the token is part of the name (T_end, K_min).
                if len(parsed.sub_tokens) == 1:
                    name = f"{name}_{tok_c}"
                    binders, shape = [], []
                    break
                raise ConversionFailure(
                    "unmapped_binder",
                    f"{kind} {raw_name!r}: subscript token {tok!r} maps to no index family",
                )
            binders.append(tok_c)
            shape.append(fam)
        if not _IDENT.match(name):
            tab.notes.append(f"non-identifier {kind} name {raw_name!r} dropped")
            return
        tab.symbols.append(
            Sym(
                name=name,
                kind=kind,
                shape=tuple(shape),
                binders=tuple(binders),
                description=desc,
                domain=domain,
                raw_base=parsed.base,
            )
        )

    for p in model.get("parameters", []):
        desc = " ".join(str(p.get("meaning") or "").split())
        dom = str(p.get("domain") or "")
        if dom:
            desc = f"{desc} [domain {dom}]" if desc else f"[domain {dom}]"
        add(str(p.get("name", "")), "param", desc)

    for v in model.get("decision_variables", []):
        desc = " ".join(str(v.get("meaning") or "").split())
        add(
            str(v.get("name", "")),
            "var",
            desc,
            domain=_var_domain(str(v.get("type") or ""), str(v.get("domain") or "")),
        )

    _disambiguate(tab)
    _check_collisions(tab)
    return tab


def _disambiguate(tab: Table) -> None:
    """Same canonical name, different shapes (``w_e`` vs ``w_a``): keep the
    binder in the name so each keeps its own shape."""
    groups: dict[str, list[Sym]] = {}
    for s in tab.symbols:
        groups.setdefault(s.name, []).append(s)
    for name, syms in sorted(groups.items()):
        shapes = {s.shape for s in syms}
        if len(syms) > 1 and len(shapes) > 1:
            for s in syms:
                if s.binders:
                    s.name = s.name + "_" + "_".join(s.binders)
            tab.notes.append(f"name {name!r} split by binder to keep distinct shapes")


def _check_collisions(tab: Table) -> None:
    seen: dict[str, Sym] = {}
    deduped: list[Sym] = []
    for s in tab.symbols:
        prev = seen.get(s.name)
        if prev is None:
            seen[s.name] = s
            deduped.append(s)
        elif prev.shape == s.shape and prev.kind == s.kind:
            continue  # exact duplicate declaration
        else:
            raise ConversionFailure(
                "symbol_collision",
                f"{s.kind} {s.name!r} collides with {prev.kind} of shape {list(prev.shape)}",
            )
    tab.symbols[:] = deduped
    index_names = {n for n, _ in tab.indices}
    for s in tab.symbols:
        if s.name in index_names:
            raise ConversionFailure(
                "symbol_collision", f"{s.kind} {s.name!r} collides with an index family"
            )


# ---------------------------------------------------------------------------
# Body-row canonicalization
# ---------------------------------------------------------------------------

_SUM_HEAD = re.compile(r"\\sum\s*_\s*\{")


def _take_braced(s: str, i: int) -> tuple[str, int]:
    """``s[i] == '{'``: return (inner, index-after-close)."""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                return s[i + 1 : j], j + 1
    raise ValueError(f"unbalanced braces: {s[i:]!r}")


def _approx_clause(clause: str, tab: Table, remarks: list[str]) -> str:
    """Widen an inexpressible binder clause to its binder's base family.

    ``a \\in \\delta^-(n)`` / ``j \\neq i`` / ``t=1`` name a *subset* of a
    declared family through the binder letter; the flat grammar can only sum
    over the family, so the clause is widened and the original recorded as a
    capture remark — published in the constraint description, never dropped
    silently. A clause whose binder maps to no family at all refuses.
    """
    pretty = " ".join(clause.split())
    m = re.match(r"^\(?\s*(?:\\mathit\{)?(?P<b>[A-Za-z][A-Za-z0-9']*)\}?", clause.strip())
    fam = _binder_lookup(_clean_ident(m.group("b")), tab.binder_map) if m else None
    if m is None or fam is None:
        raise ConversionFailure(
            "outside_grammar",
            f"sum binder {pretty!r} maps to no declared index family",
        )
    remarks.append(f"sum over {m.group('b')} widened to family {fam}; source binder: {pretty!r}")
    return rf"{m.group('b')} \in \mathcal{{{fam}}}"


def _canon_binder(inner: str, tab: Table, notes: list[str], remarks: list[str]) -> str:
    """Canonicalize one ``\\sum`` binder block to ``x \\in \\mathcal{F}`` clauses.

    A restriction (``l \\in L : e \\in l``) cannot ride along in the flat
    grammar: the base clause is kept and the restriction becomes a capture
    remark on the constraint (schema-level capture, documented loss).
    """
    fam_by_lower = {f.lower(): f for f, _ in reversed(tab.indices)}
    restriction = ""
    for sep in (r"\mid", ":", r"\,|\,"):
        if sep in inner:
            inner, restriction = inner.split(sep, 1)
            break
    clauses_out: list[str] = []
    deferred: list[str] = []
    for clause in _split_outside_parens(inner):
        bare = re.fullmatch(r"[A-Za-z][A-Za-z0-9']*", clause)
        if bare:
            fam = _binder_lookup(_clean_ident(clause), tab.binder_map)
            if fam:
                clauses_out.append(rf"{clause} \in \mathcal{{{fam}}}")
            else:
                raise ConversionFailure(
                    "outside_grammar", f"sum binder {clause!r} maps to no index family"
                )
            continue
        m = _IN_CLAUSE.search(clause)
        fam = _resolve_family(m.group("fam"), fam_by_lower) if m else None
        if m is None or fam is None:
            try:
                clauses_out.append(_approx_clause(clause, tab, remarks))
            except ConversionFailure:
                # A side condition (``trip(a)=t``) rather than a binder: keep
                # it as a remark IF another clause carries the domain.
                deferred.append(" ".join(clause.split()))
            continue
        binder = m.group("binder").strip()
        if binder.startswith("("):
            for tok in binder.strip("()").split(","):
                tok = tok.strip()
                if tok:
                    clauses_out.append(rf"{tok} \in \mathcal{{{fam}}}")
        else:
            clauses_out.append(rf"{binder} \in \mathcal{{{fam}}}")
    if deferred and not clauses_out:
        raise ConversionFailure(
            "outside_grammar", f"sum binder {deferred[0]!r} maps to no declared index family"
        )
    remarks.extend(f"sum condition not representable: {d!r}" for d in deferred)
    if restriction.strip():
        remarks.append(f"sum restriction not representable: {' '.join(restriction.split())!r}")
    return ", ".join(clauses_out)


def _canon_sums(text: str, tab: Table, notes: list[str], remarks: list[str]) -> str:
    """Canonicalize sum binders, drop numeric ranges, merge consecutive sums.

    Merging happens here, brace-aware, because canonical binder clauses
    contain ``\\mathcal{...}`` braces that a flat regex merge cannot cross —
    and an unmerged inner ``\\sum`` would be read as a *subscript* by the
    canonical parser.
    """
    out: list[str] = []
    pending: list[str] = []  # canonical clauses of a run of consecutive sums
    i = 0

    def flush() -> None:
        if pending:
            clauses = [c for c in pending if c]
            out.append(rf"\sum_{{{', '.join(clauses)}}} ")
            pending.clear()

    while i < len(text):
        m = _SUM_HEAD.match(text, i)
        if not m:
            if pending and text[i].isspace():
                i += 1  # whitespace between consecutive sums
                continue
            flush()
            out.append(text[i])
            i += 1
            continue
        inner, j = _take_braced(text, m.end() - 1)
        # Drop a following ^{...} range: the canonical sum has set binders only.
        k = j
        while k < len(text) and text[k].isspace():
            k += 1
        if k < len(text) and text[k] == "^":
            # ``\sum_{t=1}^{T-2}`` — a numeric range over a family binder is
            # widened to the family, with the range recorded as a remark.
            k += 1
            if k < len(text) and text[k] == "{":
                rng, k = _take_braced(text, k)
            else:
                rng, k = text[k], k + 1
            j = k
            pending.append(_canon_binder(inner, tab, notes, remarks))
            remarks.append(f"sum range dropped by widening: up to {' '.join(rng.split())!r}")
            i = j
            continue
        canon = _canon_binder(inner, tab, notes, remarks)
        pending.append(canon)
        i = j
    flush()
    return "".join(out)


_ATOM_BEFORE_PAREN = re.compile(
    r"(?P<atom>(?:\\mathit\{[A-Za-z_\\]+\}|(?<![\\A-Za-z_^{])[A-Za-z])"
    r"(?:\^(?:\{[^{}]*\}|[A-Za-z0-9]))?"
    r"(?:_(?:\{[^{}]*\}|[A-Za-z0-9]))?"
    r"(?:\^(?:\{[^{}]*\}|[A-Za-z0-9]))?)\s*\($"
)

#: A sum group with (possibly \mathcal-nested) braces at the end of a string.
_SUM_TAIL = re.compile(r"\\sum_\{(?:[^{}]|\{[^{}]*\})*\}\s*$")


def _strip_braced(s: str) -> str:
    """Remove every braced group (iteratively, so nesting collapses)."""
    while True:
        s2 = re.sub(r"\{[^{}]*\}", "", s)
        if s2 == s:
            return s
        s = s2


def _merge_adjacent_sums(text: str) -> str:
    """Merge ``\\sum_{A} \\sum_{B}`` into ``\\sum_{A, B}``, brace-aware.

    An inner ``\\sum`` left adjacent would be read as a *subscript* by the
    canonical parser, so this must run again after distribution re-joins
    sum prefixes.
    """
    while True:
        i = text.find(r"\sum_{")
        changed = False
        while i >= 0:
            inner1, j = _take_braced(text, i + len(r"\sum_"))
            k = j
            while k < len(text) and text[k].isspace():
                k += 1
            if text.startswith(r"\sum_{", k):
                inner2, j2 = _take_braced(text, k + len(r"\sum_"))
                clauses = [c for c in (inner1.strip(", "), inner2.strip(", ")) if c]
                text = text[:i] + rf"\sum_{{{', '.join(clauses)}}} " + text[j2:]
                changed = True
                break
            i = text.find(r"\sum_{", j)
        if not changed:
            return text


def _distribute(text: str, notes: list[str]) -> str:
    """Exact linear rewrites: expand ``\\sum_{B} (a - b)`` and ``c (a - b)``.

    Distribution replicates the sum/coefficient prefix over each top-level
    signed piece and a leading minus flips every sign — plain linearity, no
    semantic judgement. Parens that carry top-level commas (tuples, function
    calls) are left for the parser to reject honestly.
    """
    offset = 0
    for _ in range(16):
        close = text.find(")", offset)
        if close < 0:
            return text
        start = text.rfind("(", 0, close)
        if start < 0:
            return text
        inner = text[start + 1 : close]
        end = close + 1
        if "," in _strip_braced(inner):
            offset = end  # tuple / function call: leave for the parser
            continue
        pieces = _split_signed(inner)
        if not pieces:
            offset = end
            continue
        head = text[:start].rstrip()
        # Order in the source is  [sums] [coefficient atom] ( ... )  — pop the
        # coefficient first, then every sum, so pieces stay under the sums.
        if head.endswith(r"\cdot"):
            offset = end  # explicit product with a bracket: leave for the parser
            continue
        coef = ""
        ma = _ATOM_BEFORE_PAREN.search(head + "(")
        if ma:
            coef = ma.group("atom")
            head = head[: len(head) - (len(ma.group(0)) - 1)].rstrip()
        prefix_sums: list[str] = []
        while True:
            ms = _SUM_TAIL.search(head)
            if ms:
                prefix_sums.insert(0, ms.group(0).strip())
                head = head[: ms.start()].rstrip()
            else:
                break
        # What now precedes must be a term boundary, not another factor: a
        # leftover multiplier (``2h(1-m)``) would silently drop off every
        # piece but the first — distribution must refuse, not approximate.
        if not re.search(r"(?:^|[+\-=(&]|\\le|\\ge|\\neq|\\quad|\\min|\\max)\s*$", head):
            offset = end
            continue
        neg = False
        if head.endswith("-"):
            neg, head = True, head[:-1].rstrip()
        elif head.endswith("+"):
            head = head[:-1].rstrip()
        parts: list[str] = []
        sums = (" ".join(prefix_sums) + " ") if prefix_sums else ""
        for sign, piece in pieces:
            eff = -sign if neg else sign
            body = f"{coef} \\cdot {piece}" if coef else piece
            parts.append(("- " if eff < 0 else "+ ") + (sums + body).strip())
        joined = " ".join(parts)
        if joined.startswith("+ "):
            joined = joined[2:]
        text = (head + " " + joined + " " + text[end:]).strip()
        offset = 0
    return text


def _split_signed(body: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    depth = 0
    sign = 1
    cur: list[str] = []
    started = False
    for ch in body:
        if ch == "{":
            depth += 1
            cur.append(ch)
        elif ch == "}":
            depth -= 1
            cur.append(ch)
        elif depth == 0 and ch in "+-" and started:
            piece = "".join(cur).strip()
            if piece:
                out.append((sign, piece))
            sign = -1 if ch == "-" else 1
            cur = []
            started = False
        elif depth == 0 and ch in "+-" and not started:
            sign = -1 if ch == "-" else 1
        else:
            if not ch.isspace():
                started = True
            cur.append(ch)
    piece = "".join(cur).strip()
    if piece:
        out.append((sign, piece))
    return out


_REF = re.compile(
    r"(?P<base>\\mathit\{[A-Za-z_\\]+\}|\\mathrm\{[A-Za-z_\\]+\}|(?<![\\A-Za-z{])[A-Za-z][A-Za-z0-9]*)"
    r"(?:\^(?P<sup>\{(?:[^{}]|\{[^{}]*\})*\}|[A-Za-z0-9]))?"
    r"(?:_(?P<sub>\{(?:[^{}]|\{[^{}]*\})*\}|[A-Za-z0-9']))?"
    r"(?:\^(?P<sup2>\{(?:[^{}]|\{[^{}]*\})*\}|[A-Za-z0-9]))?"
)


def _unbrace(s: str) -> str:
    """Strip exactly ONE brace layer — ``str.strip("{}")`` would also eat the
    closing brace of an inner group (``{e_{in},e_{out}}`` -> ``e_{in},e_{out``)."""
    if s.startswith("{") and s.endswith("}"):
        return s[1:-1]
    return s


def _sub_tokens(sub_raw: str) -> list[str]:
    """Binder tokens of a subscript, tolerating M1b's Greek rewrites inside.

    ``r\\mathit{pi}`` (from source ``r\\pi``) is the two binders r and pi;
    a comma-separated subscript is taken verbatim.
    """
    if "," in sub_raw:
        return [t.strip() for t in sub_raw.split(",") if t.strip()]
    atoms = re.findall(r"\\mathit\{([A-Za-z_\\]+)\}|([A-Za-z][A-Za-z0-9']*)", sub_raw)
    tokens = [a or b for a, b in atoms]
    if len(tokens) <= 1:
        return [sub_raw.strip()] if sub_raw.strip() else []
    return [t.replace("\\_", "_") for t in tokens]


def _canon_refs(text: str, tab: Table) -> str:
    """Rewrite every symbol occurrence to its declared canonical spelling.

    Superscripts fold into the name (``f^{min}_e`` -> ``\\mathit{f\\_min}_{e}``),
    fused subscripts split into binder commas, and name-consuming subscripts
    (``K_{min}`` for declared ``Kmin``) resolve via the underscore-insensitive
    table. Unresolvable occurrences are left untouched so the canonical parser
    fails with the true symbol in the message.
    """
    by_name = tab.by_name
    loose = tab.loose

    def repl(m: re.Match[str]) -> str:
        base = m.group("base")
        base_name = _clean_ident(base)
        sup = m.group("sup") or m.group("sup2")
        sup_name = _clean_ident(_unbrace(sup)) if sup else None
        if sup_name is not None and not sup_name.isalpha():
            return m.group(0)  # numeric power: outside grammar, leave honest
        sub = m.group("sub")
        sub_raw = _unbrace(sub) if sub else ""
        cand = base_name + (f"_{sup_name}" if sup_name else "")
        name = None
        if cand in by_name:
            name = cand
        elif cand.replace("_", "").lower() in loose:
            name = loose[cand.replace("_", "").lower()]
        sym = by_name.get(name) if name else None
        if sym is not None:
            tokens = _sub_tokens(sub_raw)
            arity = len(sym.shape)
            if arity == 0 and not tokens:
                return _spell(sym.name)
            if len(tokens) == arity and arity > 0:
                return _spell(sym.name) + "_{" + ",".join(tokens) + "}"
            if len(tokens) == 1 and arity > 1:
                seg = _segment(tokens[0], tab.binder_map, arity)
                if seg:
                    return _spell(sym.name) + "_{" + ",".join(seg) + "}"
            if (
                len(tokens) == 1
                and arity >= 1
                and _binder_lookup(tokens[0], tab.binder_map) is not None
            ):
                # fewer binders than shape — leave for validation to flag
                return _spell(sym.name) + "_{" + ",".join(tokens) + "}"
        # Name-consuming subscript: K_{min} -> Kmin / d_{max} -> dmax; a
        # binder-split symbol (w_e vs w_a) keeps its subscript as bindings.
        if sub_raw and "," not in sub_raw:
            fused = f"{cand}_{_clean_ident(sub_raw)}"
            target = None
            if fused in by_name:
                target = fused
            elif fused.replace("_", "").lower() in loose:
                target = loose[fused.replace("_", "").lower()]
            if target is not None:
                tshape = by_name[target].shape
                if not tshape:
                    return _spell(target)
                tokens = _sub_tokens(sub_raw)
                if len(tokens) == len(tshape):
                    return _spell(target) + "_{" + ",".join(tokens) + "}"
        if sym is not None and not sub_raw and len(sym.shape) > 0:
            return _spell(sym.name)  # bare use of an indexed symbol (coefficient)
        return m.group(0)

    # Never rewrite inside \sum binder blocks or \mathcal names: mask them.
    out: list[str] = []
    i = 0
    while i < len(text):
        m = _SUM_HEAD.match(text, i)
        if m:
            _, j = _take_braced(text, m.end() - 1)
            out.append(text[i:j])
            i = j
            continue
        mc = re.match(r"\\mathcal\{[^{}]*\}", text[i:])
        if mc:
            out.append(mc.group(0))
            i += mc.end()
            continue
        nxt_sum = text.find(r"\sum", i)
        nxt_cal = text.find(r"\mathcal", i)
        stops = [n for n in (nxt_sum, nxt_cal) if n >= 0]
        stop = min(stops) if stops else len(text)
        if stop == i:
            stop = i + 1
        out.append(_REF.sub(repl, text[i:stop]))
        i = stop
    return "".join(out)


def _bare_coefficients(text: str, tab: Table) -> str:
    """A parameter in coefficient position must be bare (``c \\cdot x_{w,j}``)."""
    params = {s.name for s in tab.symbols if s.kind == "param"}

    def repl(m: re.Match[str]) -> str:
        name = _clean_ident(m.group("name"))
        if name in params:
            return _spell(name) + r" \cdot "
        return m.group(0)

    return re.sub(
        r"(?P<name>\\mathit\{[A-Za-z_\\]+\}|(?<![\\A-Za-z])[A-Za-z])"
        r"(?:_\{[^{}]*\})?\s*\\cdot\s+",
        repl,
        text,
    )


def canonicalize_row(latex: str, tab: Table, notes: list[str], remarks: list[str]) -> str:
    """The full exact-rewrite pipeline for one align row body.

    ``remarks`` collects schema-level capture approximations (widened binder
    restrictions/ranges) — the caller publishes them in the constraint's
    description; ``notes`` collects sidecar-only observations.
    """
    text, _prov = normalize_latex(latex, source="corpusbuilder.repo_corpus")
    text = text.replace(r"\text{", r"\mathrm{")
    text = _SIZERS.sub("", text)
    text = _SPACING.sub(" ", text)
    text = re.sub(r"\\leq(?![a-zA-Z])", r"\\le", text)
    text = re.sub(r"\\geq(?![a-zA-Z])", r"\\ge", text)
    text = _canon_sums(text, tab, notes, remarks)
    text = _distribute(text, notes)
    text = _merge_adjacent_sums(text)
    text = _canon_refs(text, tab)
    names = {s.name for s in tab.symbols}
    text = declared_products(text, names)
    # A scalar coefficient written BEFORE a sum moves inside it (exact: it is
    # constant across the binder): ``c \sum_{B} x`` -> ``\sum_{B} c \cdot x``.
    params = {s.name for s in tab.symbols if s.kind == "param" and not s.shape}
    spelled = "|".join(re.escape(_spell(p)) for p in sorted(params)) or r"(?!x)x"
    text = re.sub(
        rf"(?P<c>{spelled})\s*(?P<s>\\sum_\{{(?:[^{{}}]|\{{[^{{}}]*\}})*\}})\s*",
        lambda m: m.group("s") + " " + m.group("c") + r" \cdot ",
        text,
    )
    text = _bare_coefficients(text, tab)
    # Exact cleanups: X \cdot 1 -> X ; X \cdot <number> -> <number> \cdot X.
    text = re.sub(r"(\\mathit\{[A-Za-z_\\]+\}|[A-Za-z])\s*\\cdot\s+1(?![\d.])", r"\1", text)
    text = re.sub(
        r"(?P<a>\\mathit\{[A-Za-z_\\]+\}|(?<![\\A-Za-z])[A-Za-z])\s*\\cdot\s+(?P<n>\d+(?:\.\d+)?)(?![\d.])",
        r"\g<n> \\cdot \g<a>",
        text,
    )
    return " ".join(text.split())


# ---------------------------------------------------------------------------
# Row guard — nothing the grammar cannot hold may pass silently
# ---------------------------------------------------------------------------

#: Everything a finished row may still contain. The canonical parser is
#: lenient (it silently discards trailing text after a referent), so WE must
#: refuse rows with residue instead of letting content vanish.
_ALLOWED_CMDS = {"sum", "in", "mathcal", "mathit", "cdot", "le", "ge", "neq"}
_SUM_BLOCK = re.compile(r"\\sum_\{(?:[^{}]|\{(?:[^{}]|\{[^{}]*\})*\})*\}")
_JUXT = re.compile(
    r"(?<![\\A-Za-z0-9_^}])"
    r"(?:\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9]*|\\mathit\{[A-Za-z_\\]+\})\x00?"
    r"[ \t]+"
    r"(?:\d+(?:\.\d+)?|[A-Za-z][A-Za-z0-9]*'?|\\mathit\{[A-Za-z_\\]+\})"
)


def check_row(name: str, body: str) -> None:
    """Refuse a row whose residue the lenient canonical parser would swallow.

    Raises :class:`ConversionFailure` on any macro outside the allowed set,
    structural characters the grammar has no meaning for, or two atoms left
    juxtaposed (an unresolved product — the parser would silently keep only
    the first).
    """

    def fail(tok: str) -> None:
        raise ConversionFailure(
            "outside_grammar", f"row {name!r}: residue {tok!r} after exact rewrites: {body[:160]!r}"
        )

    if r"\sum_{}" in body.replace(" ", ""):
        fail(r"\sum_{}")
    for m in re.finditer(r"\\([A-Za-z]+)", body):
        if m.group(1) not in _ALLOWED_CMDS:
            fail("\\" + m.group(1))
    for ch in "[]|()<>!^":
        if ch in body:
            fail(ch)
    # Juxtaposition scan with binder blocks and scripts masked out.
    masked = _SUM_BLOCK.sub(" ", body)
    masked = re.sub(r"[_^]\{(?:[^{}]|\{[^{}]*\})*\}", "\x00", masked)
    masked = re.sub(r"\\mathcal\{[A-Za-z_\\]+\}", " ", masked)
    m = _JUXT.search(masked)
    if m:
        fail(m.group(0))


# ---------------------------------------------------------------------------
# Quantifier tails
# ---------------------------------------------------------------------------


def quantifier_tail(indexed_over: str, tab: Table) -> str:
    """Formalize ``indexed_over`` prose into a ``\\forall`` tail, where stated."""
    if not indexed_over:
        return ""
    fam_by_lower = {f.lower(): f for f, _ in reversed(tab.indices)}
    clauses: list[str] = []
    seen: set[str] = set()
    for m in _IN_CLAUSE.finditer(indexed_over):
        fam = _resolve_family(m.group("fam"), fam_by_lower)
        if fam is None:
            continue
        binder = m.group("binder").strip()
        binders = (
            [t.strip() for t in binder.strip("()").split(",")]
            if binder.startswith("(")
            else [binder]
        )
        for b in binders:
            if b and b not in seen and re.fullmatch(r"\w+", b):
                seen.add(b)
                clauses.append(rf"\forall {b} \in \mathcal{{{fam}}}")
    return ",\\; ".join(clauses)


# ---------------------------------------------------------------------------
# Document rendering
# ---------------------------------------------------------------------------


def _oneline(s: str) -> str:
    return " ".join(str(s).split())


def render_document(stem: str, doc: dict, model: dict) -> tuple[str, list[str]]:
    """Render one model as a canonical LaTeX document (may raise ConversionFailure)."""
    tab = build_table(model)
    notes = list(tab.notes)
    entry_id = f"{stem}__{model['model_id']}".lower()
    family = "milp" if any(s.domain in ("binary", "integer") for s in tab.symbols if s.kind == "var") else "lp"

    lines = [
        "% lp2graph canonical LaTeX — assembled by corpusbuilder.repo_corpus",
        f"% Source repository: {doc.get('repo', '')}",
        f"%@ meta id={entry_id} family={family} schema=0.1.0",
        f"%@ name :: {_oneline(model.get('model_id', ''))}",
    ]
    desc = _oneline(model.get("description") or "")
    if desc:
        lines.append(f"%@ desc :: {desc}")
    lines.append(f"%@ prov source :: {_oneline(doc.get('url') or doc.get('repo') or '')}")
    if doc.get("paper_reference"):
        lines.append(f"%@ prov reference :: doi:{_oneline(doc['paper_reference'])}")
    lines.append(f"%@ prov author :: {_oneline(doc.get('repo') or '')}")

    for name, meaning in tab.indices:
        lines.append(f"%@ index {name} ordered=0 cyclic=0 :: {_oneline(meaning) or '?'}")
    for s in tab.symbols:
        if s.kind != "param":
            continue
        shape_tok = ",".join(s.shape) if s.shape else "-"
        kind = "scalar" if not s.shape else ("vector" if len(s.shape) == 1 else "matrix")
        if s.name == "M" or "big-m" in s.description.lower() or "big M" in s.description:
            kind = "big_m"
        lines.append(
            f"%@ param {s.name} shape={shape_tok} kind={kind} domain=- :: {_oneline(s.description) or '?'}"
        )
    for s in tab.symbols:
        if s.kind != "var":
            continue
        shape_tok = ",".join(s.shape) if s.shape else "-"
        lines.append(
            f"%@ var {s.name} shape={shape_tok} domain={s.domain} role=primary drole=- "
            f"lo=- hi=- :: {_oneline(s.description) or '?'}"
        )

    obj = model.get("objective") or {}
    sense = obj.get("sense", "min")
    obj_desc = _oneline(obj.get("expression_plain") or "")
    lines.append(f"%@ obj sense={sense} name=objective combination=sum :: {obj_desc or '?'}")

    body_rows: list[str] = []
    obj_latex = obj.get("expression_latex")
    if not obj_latex:
        raise ConversionFailure("no_objective", "model has no objective expression_latex")
    obj_remarks: list[str] = []
    obody = canonicalize_row(obj_latex, tab, notes, obj_remarks)
    obody = re.sub(r"^\\(min|max)(?:imi[sz]e)?\b\s*", "", obody).strip()
    obody = re.sub(r"^(?:\\quad|\\;|\\,|\s)+", "", obody)
    check_row("objective", obody)
    if obj_remarks:
        lines[-1] = lines[-1].rstrip() + f" [capture: {'; '.join(sorted(set(obj_remarks)))}]"
        notes.extend(f"objective: {r}" for r in obj_remarks)
    body_rows.append(rf"  \{sense}\quad & {obody} \tag{{objective}} \\")

    var_domains = {s.raw_base: s for s in tab.symbols if s.kind == "var"}
    for c in model.get("constraints", []):
        cname = re.sub(r"[^a-z0-9_]+", "_", str(c.get("name") or "con").lower()).strip("_") or "con"
        latex = c.get("expression_latex")
        over = str(c.get("indexed_over") or "")
        if not latex:
            notes.append(f"constraint {cname!r} omitted: no expression_latex")
            continue
        if _MACHINERY.search(f"{c.get('name') or ''} | {over}"):
            notes.append(f"constraint {cname!r} omitted: search machinery ({_oneline(over)!r})")
            continue
        declared = domain_declaration(latex)
        if not declared and r"\quad" in latex:
            # A domain row with a trailing parenthetical remark ("(relaxed to
            # ... during column generation)") is still a domain row.
            declared = domain_declaration(latex.split(r"\quad", 1)[0])
        if declared:
            folded = all(_clean_ident(sym) in var_domains or sym in var_domains for sym in declared)
            notes.append(
                f"constraint {cname!r} folded: pure domain row for {sorted(declared)}"
                + ("" if folded else " (symbols not all declared)")
            )
            continue
        if re.search(r"\bu\b|\\cup|∪", over):
            notes.append(f"constraint {cname!r}: union quantifier narrowed to its first family")
        # A quantifier written into the expression itself moves to the tail;
        # clauses the canonical grammar cannot hold (\notin, index arithmetic)
        # are dropped WITH a note, never silently.
        own_tail = ""
        if r"\forall" in latex:
            latex, own_tail = latex.split(r"\forall", 1)
            latex = re.sub(r"(?:,|\\quad|\\qquad|\\;|\\,|\s)+$", "", latex)
            if not _IN_CLAUSE.search(own_tail) or re.search(r"\\notin|\\neq", own_tail):
                notes.append(
                    f"constraint {cname!r}: in-body quantifier clause dropped/narrowed: "
                    f"{_oneline(own_tail)!r}"
                )
        tail = quantifier_tail(f"{over} ; {own_tail}" if own_tail else over, tab)
        qpart = rf" \qquad {tail}" if tail else ""
        # A conjunction of two comparisons is two constraints (exact split).
        parts = re.split(r"\\wedge", latex)
        for pi, part in enumerate(parts):
            pname = cname if len(parts) == 1 else f"{cname}_{'ab cdefgh'.replace(' ', '')[pi]}"
            remarks: list[str] = []
            cbody = canonicalize_row(part, tab, notes, remarks)
            check_row(pname, cbody)
            tag = pname.replace("_", r"\_")
            cdesc = _oneline(c.get("expression_plain") or "") or "?"
            if remarks:
                cdesc = f"{cdesc} [capture: {'; '.join(sorted(set(remarks)))}]"
                notes.extend(f"constraint {pname!r}: {r}" for r in remarks)
            lines.append(f"%@ con {pname} kind=linear domain=- indicator=- :: {cdesc}")
            body_rows.append(rf"  & {cbody}{qpart} \tag{{{tag}}} \\")

    if len(body_rows) < 2:
        raise ConversionFailure("no_constraints", "no constraint row survived canonicalization")

    lines.append(r"\begin{align}")
    lines.extend(body_rows)
    lines.append(r"\end{align}")
    return "\n".join(lines) + "\n", sorted(set(notes))


# ---------------------------------------------------------------------------
# Conversion driver
# ---------------------------------------------------------------------------


def convert_all(src: Path = DEFAULT_SRC, out: Path = DEFAULT_OUT) -> dict:
    """Convert every model; write formulations, sidecars, and the report."""
    out.mkdir(parents=True, exist_ok=True)
    # The directory is owned by this module: clear stale artifacts so a model
    # that stops converting cannot leave a ghost entry behind.
    for stale in sorted(out.glob("*.json")) + sorted(out.glob("*.tex")):
        stale.unlink()
    converted: list[str] = []
    failed: list[dict] = []
    excluded: list[dict] = []

    for fp in sorted(src.glob("*.json")):
        doc = json.loads(fp.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or "models" not in doc:
            continue
        stem = fp.stem
        repo = str(doc.get("repo") or stem)
        if repo in EXCLUDED_REPOS:
            for model in doc.get("models", []):
                excluded.append(
                    {
                        "id": f"{stem}__{model.get('model_id', '?')}",
                        "reason": EXCLUDED_REPOS[repo],
                    }
                )
            continue
        for model in doc.get("models", []):
            model_id = str(model.get("model_id") or "model")
            entry = f"{stem}__{model_id}"
            # Only the model_id decides: master problems legitimately *mention*
            # pricing in their prose (they are solved BY column generation) —
            # only a model that IS the pricing step is the algorithm component.
            if _PRICING.search(model_id):
                excluded.append({"id": entry, "reason": "column_generation_pricing_subproblem"})
                continue
            try:
                document, notes = render_document(stem, doc, model)
            except ConversionFailure as exc:
                failed.append({"id": entry, "cause": exc.cause, "detail": exc.detail})
                continue
            result = ingest_latex(document, source=f"repo_corpus/{entry}.tex")
            if not result.ok:
                stage = result.failures[0].stage
                cause = {
                    "normalize": "normalize_failed",
                    "parse": "outside_grammar",
                    "validate": "semantic_invalid",
                }.get(stage, "outside_grammar")
                detail = "; ".join(f.message for f in result.failures)
                failed.append({"id": entry, "cause": cause, "detail": _oneline(detail)[:400]})
                continue
            formulation = result.formulation
            assert formulation is not None  # guaranteed by result.ok
            payload = json.dumps(
                formulation.model_dump(mode="json", warnings=False),
                indent=2,
                ensure_ascii=False,
            )
            # Round-trip guard: an entry that cannot re-load is not a corpus entry.
            load_formulation(payload, source=entry)
            (out / f"{entry}.json").write_text(payload + "\n", encoding="utf-8")
            (out / f"{entry}.tex").write_text(document, encoding="utf-8")
            meta = {
                "repo": repo,
                "url": doc.get("url"),
                "license": doc.get("license"),
                "area": doc.get("area"),
                "solver": doc.get("solver"),
                "extraction_confidence": doc.get("extraction_confidence"),
                "model_id": model_id,
                "notes": notes,
            }
            if doc.get("paper_reference"):
                meta["source_paper"] = {"doi": doc["paper_reference"]}
            (out / f"{entry}.meta.json").write_text(
                json.dumps(meta, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            converted.append(entry)

    report = {
        "schema": REPORT_SCHEMA,
        "source": str(src),
        "converted": len(converted),
        "converted_ids": sorted(converted),
        "failed": sorted(failed, key=lambda r: r["id"]),
        "excluded": sorted(excluded, key=lambda r: r["id"]),
    }
    (out / "_report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--src", type=Path, default=DEFAULT_SRC, help="repo-corpus dir")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output dir")
    args = parser.parse_args(argv)
    report = convert_all(src=args.src, out=args.out)
    print(
        f"repo_corpus: converted={report['converted']} "
        f"failed={len(report['failed'])} excluded={len(report['excluded'])}"
    )
    for row in report["failed"]:
        print(f"  FAIL {row['id']}: {row['cause']}: {row['detail'][:140]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
