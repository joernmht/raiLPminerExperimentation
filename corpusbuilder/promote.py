"""Promote reviewed formulas into canonical corpus entries — the step that turns
HITL **decisions** into **Formulations** on disk.

Everything upstream of this module produces review state: ``corpusbuilder.game``
(and the older ``corpusbuilder.review_view``) export per-formula verdicts into
``corpus/decisions/*.json``, and ``corpusbuilder.prisma`` *counts* them. Nothing
consumed them. This module is the consumer: it reads both export schemas, builds
one candidate canonical model per paper, runs it through lp2graph's M1 ingestion
front-end (:func:`lp2graph.mining.ingest.ingest_latex` = M1b normalization ->
canonical parse -> semantic validation), and writes

* ``corpus/formulations/<key>.json``  — the validated canonical ``Formulation``
* ``corpus/provenance/<key>.json``    — its aligned ``ProvenanceRecord``
* ``corpus/promoted/<key>.tex``       — the exact document that was ingested
* ``corpus/promoted/<key>.rewrites.json`` — the M1b rewrites that fired (provenance)
* ``corpus/promotion.{json,md}``      — the promotion report, **categorized by cause**

The report is the point as much as the formulations are: Section
``sec:validation`` of the paper promises that "every failure is categorized by
cause", and this is where the promotion-stage half of that taxonomy is produced.

Declarations: what a displayed equation cannot carry
----------------------------------------------------
Canonical LaTeX is algebra *plus* a ``%@`` declaration block — index families,
parameter shape/kind, variable domain/role. A displayed equation in a paper
carries none of that; those facts live in prose and nomenclature tables. Feeding
raw extracted formulas to the canonical parser therefore fails for *every* paper
(measured: 0 of 10 156 extracted units parse, all ``KeyError 'meta'``), and no
amount of rewriting fixes it, because the information is not in the input.

So promotion takes the declarations from a sidecar the reviewer supplies:

    corpus/declarations/<paper_key>.tex

holding only ``%@ index`` / ``%@ param`` / ``%@ var`` / ``%@ obj`` / ``%@ con``
lines. The bibliographic half of the header (``meta``, ``name``, ``desc``,
``tags``, ``prov``) is generated here from the dossier, so a human never
transcribes metadata we already hold. When the sidecar is missing, promotion
fails with cause ``missing_declarations`` and a **fill-in-the-blank stub** is
written to ``corpus/declarations/<paper_key>.stub.tex``, pre-populated with the
symbols and row names of that paper's accepted formulas.

Failure causes
--------------
:data:`CAUSES` maps every promotion cause to one of four categories. Three are
statements about the *source material* and belong in the paper's failure
taxonomy; ``pipeline_incomplete`` is a statement about *our own* artifacts and
workflow, kept separate so a gap in our review process is never published as a
finding about the literature (the ADR-0006 / ADR-0007 posture applied to
promotion). Two of the paper's categories — missing instance data and
cross-solver disagreement — arise downstream at grounding/solving time and are
listed in :data:`NOT_ASSESSED_HERE` rather than silently omitted.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.promote            # promote + write report
    PYTHONPATH=. python3 -m corpusbuilder.promote --dry-run  # report only, write nothing
"""

# ruff: noqa: I001 — the ``railpminer._lp2graph`` import below is a *side effect*
# (it puts a sibling lp2graph checkout on ``sys.path``) and must run before the
# ``lp2graph`` imports. Import sorting would place third-party ``lp2graph``
# first and break the module on any machine where lp2graph is not installed.

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# The one sanctioned place that makes ``lp2graph`` importable from a sibling
# checkout; duplicating its search here would give the repo two answers to
# "where is lp2graph".
from railpminer import _lp2graph  # noqa: F401

from corpusbuilder.algebra import declared_names, declared_products
from corpusbuilder.dossier import Dossier
from corpusbuilder.game import extract_symbols, is_objective_latex, normalize_objective_head
from corpusbuilder.symbols import binder_roles, paper_evidence
from lp2graph import loads as load_formulation
from lp2graph.mining import REWRITE_RULES_VERSION
from lp2graph.mining.corpusmgr import PRIORITY_CELLS, QUALITY_TIERS
from lp2graph.mining.ingest import ingest_latex, normalize_latex

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"
DECISIONS = CORPUS / "decisions"
DECLARATIONS = CORPUS / "declarations"
FORMULATIONS = CORPUS / "formulations"
PROVENANCE = CORPUS / "provenance"
PROMOTED = CORPUS / "promoted"
VENUE_TIERS = CORPUS / "venue_tiers.json"

PROMOTION_SCHEMA = "promotion-1"

#: The review game's four terminal verdicts (ADR-0008). Kept in this order so
#: the report's counters are stable.
TERMINAL_STATUSES = ("accepted", "corrected", "rejected", "duplicate")

#: Verdicts whose LaTeX enters the candidate model.
PROMOTABLE_STATUSES = ("accepted", "corrected")

#: Shell Sorter cell -> (domain_shell, activity), the two taxonomy fields of a
#: ``ProvenanceRecord``. The cell labels are the game's own (``CELLS``), so the
#: mapping is a transcription, not a judgement.
CELL_TAXONOMY: dict[str, tuple[str, str]] = {
    "P1": ("railway", "rescheduling"),
    "P2": ("transport", "rescheduling"),
    "P3": ("railway", "operations"),
    "P4": ("transport", "operations"),
    "P5": ("production", "rescheduling"),
}

#: cause -> (category, one-line remedy). ``category`` is one of
#: ``extraction_error`` / ``outside_grammar`` / ``under_specified`` (findings
#: about the source) or ``pipeline_incomplete`` (findings about us).
CAUSES: dict[str, tuple[str, str]] = {
    "not_reviewed": (
        "pipeline_incomplete",
        "no terminal verdict on any formula of this paper — review it in the game",
    ),
    "not_sorted": (
        "pipeline_incomplete",
        "paper has no P1-P5 cell — file it in the Shell Sorter",
    ),
    "no_dossier": (
        "pipeline_incomplete",
        "decisions reference a paper with no dossier on disk — re-run acquisition",
    ),
    "id_conflict": (
        "pipeline_incomplete",
        "target formulation id is already taken by a different source",
    ),
    "all_rejected": (
        "extraction_error",
        "every extracted formula was rejected — the extraction yielded no usable model",
    ),
    "corrected_without_replacement": (
        "extraction_error",
        "a formula was marked corrected but carries no corrected LaTeX",
    ),
    "normalize_failed": (
        "extraction_error",
        "M1b rewriting could not run on the assembled LaTeX",
    ),
    "semantic_invalid": (
        "extraction_error",
        "the assembled model parses but fails lp2graph semantic validation",
    ),
    "outside_grammar": (
        "outside_grammar",
        "the normalized LaTeX is not in the canonical grammar",
    ),
    "multiple_objectives": (
        "outside_grammar",
        "several rows state an objective; the canonical model admits exactly one",
    ),
    "missing_declarations": (
        "under_specified",
        "no declaration sidecar — supply corpus/declarations/<key>.tex (a stub was written)",
    ),
    "no_objective": (
        "under_specified",
        "no accepted formula states an objective — the model is incomplete",
    ),
}

#: Failure categories the paper's taxonomy names that cannot be decided at
#: promotion time. Recorded in the report so the union of stages is the full
#: taxonomy and nothing looks quietly dropped.
NOT_ASSESSED_HERE: dict[str, str] = {
    "missing_instance_data": "decided at grounding time (validation stage)",
    "cross_solver_disagreement": "decided at solve time (validation stage)",
}

_ALIGN = "align"


def _rel(path: Path) -> str:
    """Repo-relative path when it is inside the repo, absolute otherwise.

    Report entries are read by humans against the repo root, but promotion also
    runs against staging trees (and tmp dirs in tests), where a naive
    ``relative_to(ROOT)`` raises instead of reporting.
    """
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------- #
# Decisions — normalizing both export schemas
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Decision:
    """One reviewer verdict on one extracted formula."""

    paper_key: str
    formula_id: str
    status: str
    #: Corrected LaTeX, part 1 first. The game's ``parts`` (a glued Tier-2 blob
    #: split into several formulas) is the general case; ``note`` is part 1.
    replacement: tuple[str, ...] = ()
    duplicate_of: str | None = None


@dataclass(frozen=True, slots=True)
class PaperDecisions:
    """Every verdict on one paper, plus its Shell Sorter cell."""

    paper_key: str
    doi: str | None = None
    cell: str | None = None
    decisions: tuple[Decision, ...] = ()

    def by_formula(self) -> dict[str, Decision]:
        return {d.formula_id: d for d in self.decisions}


def _parts(entry: dict) -> tuple[str, ...]:
    """The corrected LaTeX carried by a decision record.

    ``parts`` is the multi-part export contract (game v10): the full list of
    corrected formulas, of which ``note`` is only the first. Readers that take
    ``note`` alone silently drop every part after a split.
    """
    parts = entry.get("parts")
    if isinstance(parts, list):
        out = tuple(str(p).strip() for p in parts if str(p).strip())
        if out:
            return out
    note = entry.get("note")
    note = str(note).strip() if note else ""
    return (note,) if note else ()


def _iter_records(payload: dict):
    """Yield ``(paper_key, doi, decision_dict)`` from either export schema.

    ``game-decisions-1`` nests many papers under ``formula_decisions``; the older
    single-paper ``review_view`` export puts ``decisions`` at the top level. This
    is the promotion-side twin of ``prisma._iter_decisions`` — a new producer
    extends both.
    """
    groups = payload.get("formula_decisions")
    if not isinstance(groups, list):
        groups = [payload] if isinstance(payload.get("decisions"), list) else []
    for g in groups:
        if not isinstance(g, dict):
            continue
        key = g.get("paper_key")
        if not key:
            continue
        for entry in g.get("decisions") or []:
            if isinstance(entry, dict) and entry.get("id"):
                yield str(key), (g.get("doi") or None), entry


#: The kinds the review game's classifier can emit. Anything else in an export
#: is ignored rather than guessed at: a sidecar built on a misread verdict would
#: be worse than one the reviewer still has to fill in.
_SYMBOL_KINDS = frozenset({"index", "parameter", "variable"})


def load_symbol_tables(paths) -> dict[str, dict[str, str]]:
    """Read the reviewer-supplied symbol tables out of decision exports.

    Same dedup rule as :func:`load_decisions` (sorted-name order, last verdict
    on a ``(paper_key, symbol)`` wins), so re-exporting a day's work supersedes
    it. Exports written before the classifier existed simply carry no
    ``symbol_tables`` key and contribute nothing.
    """
    tables: dict[str, dict[str, str]] = {}
    for path in sorted(paths, key=lambda p: p.name):
        try:
            payload = json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for group in payload.get("symbol_tables") or []:
            if not isinstance(group, dict):
                continue
            key = group.get("paper_key")
            symbols = group.get("symbols")
            if not key or not isinstance(symbols, dict):
                continue
            table = tables.setdefault(str(key), {})
            for name, kind in symbols.items():
                if isinstance(kind, str) and kind in _SYMBOL_KINDS:
                    table[str(name)] = kind
    return tables


def load_decisions(paths) -> tuple[dict[str, PaperDecisions], dict[str, int]]:
    """Read decision exports into per-paper records, and report odd statuses.

    Deduplication follows ADR-0008: files are read in sorted-name order and the
    last verdict on a ``(paper_key, formula_id)`` wins, so re-exporting a day's
    work supersedes rather than duplicates. Verdicts of ``unreviewed`` are not
    verdicts and are skipped; anything outside the modelled four is *reported*
    (returned as ``unrecognised``), never silently counted as one of them.
    """
    latest: dict[tuple[str, str], Decision] = {}
    dois: dict[str, str] = {}
    cells: dict[str, str] = {}
    unrecognised: Counter[str] = Counter()

    for path in sorted(Path(p) for p in paths):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        for key, doi, entry in _iter_records(payload):
            status = str(entry.get("status") or "unreviewed")
            if status == "unreviewed":
                continue
            if status not in TERMINAL_STATUSES:
                unrecognised[status] += 1
                continue
            if doi:
                dois[key] = doi
            fid = str(entry["id"])
            latest[(key, fid)] = Decision(
                paper_key=key,
                formula_id=fid,
                status=status,
                replacement=_parts(entry) if status == "corrected" else (),
                duplicate_of=(str(entry["duplicate_of"]) if entry.get("duplicate_of") else None),
            )
        for cell_rec in payload.get("paper_cells") or []:
            if isinstance(cell_rec, dict) and cell_rec.get("paper_key") and cell_rec.get("cell"):
                cells[str(cell_rec["paper_key"])] = str(cell_rec["cell"])

    papers: dict[str, list[Decision]] = {}
    for (key, _fid), decision in sorted(latest.items()):
        papers.setdefault(key, []).append(decision)
    for key in cells:
        papers.setdefault(key, [])

    out = {
        key: PaperDecisions(
            paper_key=key,
            doi=dois.get(key),
            cell=cells.get(key),
            decisions=tuple(decisions),
        )
        for key, decisions in sorted(papers.items())
    }
    return out, dict(sorted(unrecognised.items()))


# --------------------------------------------------------------------------- #
# Assembly — decisions + dossier -> a candidate canonical document
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Row:
    """One body row of the candidate model."""

    name: str
    latex: str
    is_objective: bool
    formula_id: str


_NON_IDENT = re.compile(r"[^A-Za-z0-9_]+")
#: ``\min Z = \sum ...`` — the objective's own label, which the canonical
#: objective row must not carry (the grammar reads the row as a bare term sum).
_OBJ_LABEL = re.compile(
    r"^(?P<op>\\(?:min|max)(?:imi[sz]e)?)\s*"
    r"(?:\\(?:limits|nolimits))?\s*"
    r"(?P<label>[A-Za-z]\w*(?:_\{[^{}]*\}|\^\{[^{}]*\}|_\w|\^\w)*)\s*=\s*",
)


def row_name(formula_id: str, part: int, n_parts: int) -> str:
    """A canonical identifier for a body row, derived from the formula id.

    Row names are how the reviewer's declarations (``%@ con <name> ...``) bind to
    the algebra, so the rule must be stable and obvious: ``eq-0007`` becomes
    ``eq_0007``, and a formula split into parts gets ``eq_0007_a``, ``eq_0007_b``.
    """
    base = _NON_IDENT.sub("_", formula_id).strip("_") or "row"
    if not re.match(r"^[A-Za-z_]", base):
        base = f"c_{base}"
    if n_parts > 1:
        base = f"{base}_{chr(ord('a') + part)}"
    return base


def strip_objective_label(latex: str) -> str:
    r"""Drop a leading ``Z =`` from an objective row.

    Papers overwhelmingly write ``\min Z = \sum_i c_i x_i``; the canonical
    grammar wants the sum alone after the operator. This is the one algebraic
    rewrite assembly performs — everything else is M1b's job or a reported
    failure.
    """
    return _OBJ_LABEL.sub(lambda m: m.group("op") + " ", latex.strip(), count=1)


def rows_for(dossier: Dossier, decisions: PaperDecisions) -> tuple[list[Row], dict[str, int]]:
    """Build the body rows of the candidate model, in the paper's own order.

    Accepted formulas contribute their extracted LaTeX; corrected ones their
    replacement (every part of it); rejected and duplicate ones contribute
    nothing. The returned counter is the per-paper decision tally that goes into
    the report.
    """
    by_formula = decisions.by_formula()
    counts: Counter[str] = Counter()
    rows: list[Row] = []
    for formula in dossier.formulas:
        decision = by_formula.get(formula.id)
        if decision is None:
            counts["unreviewed"] += 1
            continue
        counts[decision.status] += 1
        if decision.status not in PROMOTABLE_STATUSES:
            continue
        texts = (formula.latex,) if decision.status == "accepted" else decision.replacement
        for i, text in enumerate(texts):
            rows.append(
                Row(
                    name=row_name(formula.id, i, len(texts)),
                    latex=text.strip(),
                    is_objective=is_objective_latex(text),
                    formula_id=formula.id,
                )
            )
    return rows, dict(counts)


def _oneline(text: str) -> str:
    return " ".join(str(text).split())


def _declaration_lines(text: str) -> list[str]:
    """The reviewer-authored ``%@`` lines, minus the ones we generate ourselves.

    ``meta``/``name``/``desc``/``tags``/``prov`` come from the dossier, so a
    sidecar that repeats them is not an error — its bibliographic lines are
    simply ignored in favour of the recorded ones. ``meta family=`` is the one
    exception: family is a modelling claim, honoured in :func:`declared_family`.
    """
    generated = ("meta", "name", "desc", "tags", "prov")
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line.startswith("%@"):
            continue
        head = line[2:].strip().split()
        if head and head[0] in generated:
            continue
        out.append(line)
    return out


def declared_family(declarations: str) -> str:
    """The model family: declared in the sidecar, else derived from the variables.

    Derivation is mechanical rather than assumed: a declared ``binary`` or
    ``integer`` variable makes the model a MILP, otherwise it is an LP.
    """
    for raw in declarations.splitlines():
        line = raw.strip()
        if line.startswith("%@") and line[2:].strip().startswith("meta"):
            for token in line.split():
                if token.startswith("family="):
                    return token.split("=", 1)[1]
    for raw in declarations.splitlines():
        line = raw.strip()
        if not (line.startswith("%@") and line[2:].strip().startswith("var ")):
            continue
        for token in line.split():
            if token in ("domain=binary", "domain=integer"):
                return "milp"
    return "lp"


def _objective_name(declarations: str, default: str) -> str:
    for raw in declarations.splitlines():
        line = raw.strip()
        if line.startswith("%@") and line[2:].strip().startswith("obj"):
            for token in line.split():
                if token.startswith("name="):
                    return token.split("=", 1)[1]
    return default


def assemble(dossier: Dossier, rows: list[Row], declarations: str, *, entry_id: str) -> str:
    """Render the candidate canonical document that will be ingested.

    Header = generated bibliography + the reviewer's declarations; body = one
    ``align`` row per promoted formula, tagged with its row name so the
    declarations bind to it, the objective row rendered as ``\\min\\quad &``.
    """
    source = dossier.source
    lines = [
        "% lp2graph canonical LaTeX — assembled by corpusbuilder.promote",
        f"% Source dossier: {dossier.key}",
        f"%@ meta id={entry_id} family={declared_family(declarations)} schema=0.1.0",
        f"%@ name :: {_oneline(source.title)}",
    ]
    description = f"Mined from {_oneline(source.title)}"
    if source.venue:
        description += f" ({_oneline(source.venue)}{f', {source.year}' if source.year else ''})"
    lines.append(f"%@ desc :: {description}.")
    if source.doi:
        lines.append(f"%@ prov source :: {source.doi}")
        lines.append(f"%@ prov reference :: {_oneline(source.title)}")
    if source.authors:
        lines.append(f"%@ prov author :: {_oneline('; '.join(source.authors))}")
    if source.retrieved:
        lines.append(f"%@ prov date :: {source.retrieved}")
    lines.extend(_declaration_lines(declarations))

    # Deterministic algebra normalization, in dependency order: M1b's rewrite
    # rules first (Greek -> \mathit{name}, \underset big operators, unicode
    # operators), THEN declared-name juxtaposition resolution — the sidecar's
    # names are spelled out, so Greek must already be \mathit{...} to match.
    names = declared_names(declarations)

    def _row_latex(latex: str) -> str:
        normalized, _prov = normalize_latex(latex, source="corpusbuilder.promote/assemble")
        return declared_products(normalized, names)

    lines.append(rf"\begin{{{_ALIGN}}}")
    for row in rows:
        tag = row.name.replace("_", r"\_")
        if row.is_objective:
            body = strip_objective_label(_row_latex(normalize_objective_head(row.latex)))
            body = re.sub(r"^\\(min|max)(?:imi[sz]e)?\b", r"\\\1", body)
            operator, rest = body.split(None, 1) if " " in body else (body, "")
            lines.append(rf"  {operator}\quad & {rest.strip()} \tag{{{tag}}} \\")
        else:
            lines.append(rf"  & {_row_latex(row.latex)} \tag{{{tag}}} \\")
    lines.append(rf"\end{{{_ALIGN}}}")
    return "\n".join(lines) + "\n"


#: One declaration line per kind, with the facts a stub cannot know left as ``?``.
_KIND_LINE = {
    "index": "%@ index {i} ordered=0 cyclic=0 :: ?",
    "parameter": "%@ param {i} shape=- kind=scalar domain=- :: ?",
    "variable": "%@ var {i} shape=- domain={d} role=primary drole=- lo=- hi=- :: ?",
}

#: Why a symbol arrived pre-classified, so the reviewer can judge the claim
#: instead of trusting it. Algebraic evidence is weaker than a human verdict and
#: says so.
_KIND_SOURCE = {
    "review": "classified as {kind} during review",
    "index": "an index family: a big operator or quantifier binds over it",
    "variable": "a decision variable: a domain row declares it {domain}",
}


def _kind_line(kind: str, ident: str, domain: str | None) -> str:
    return _KIND_LINE[kind].format(i=ident, d=domain or "?")


def declaration_stub(
    dossier: Dossier, rows: list[Row], symbols: dict[str, str] | None = None
) -> str:
    """A fill-in-the-blank declaration sidecar for a paper awaiting one.

    Every line the reviewer must supply is present with the symbol or row name
    already filled in; only the modelling facts left genuinely open are ``?``
    placeholders. A stub used unedited fails loudly rather than promoting a
    guessed model.

    Two sources pre-classify a symbol. :func:`corpusbuilder.symbols.paper_evidence`
    reads what the algebra states outright, index families off the binders and
    decision variables (with their domains) off domain rows, and ``symbols`` is
    the reviewer's own answer carried over from the review game's classifier
    (export key ``symbol_tables``). The reviewer wins where they disagree: a
    human verdict supersedes an inference. A symbol either source settles gets
    *only* its declared line; the rest still get all three to choose between.

    Index families are collected from the binders as well as from the formula
    bodies. They have to be: a family named only inside ``\\sum_{i \\in I}``
    never appears in a body, yet ``%@ index I`` is the one declaration the model
    cannot be assembled without, so a stub omitting it is unfillable as written.
    """
    evidence = paper_evidence([row.latex for row in rows])
    kinds = {**evidence.kinds, **(symbols or {})}
    reviewed = set(symbols or {})
    # A quantifier's bound letter needs no declaration of its own, and offering
    # one invites the reviewer to invent an index family the model never had.
    # It is listed at the foot of the stub instead of being silently dropped.
    bound = {name for name in evidence.bound if name not in reviewed}

    symbol_names: list[str] = []
    for row in rows:
        for name, _ in extract_symbols(row.latex, limit=None)[0]:
            if name not in symbol_names and name not in bound:
                symbol_names.append(name)
        for name in sorted(binder_roles(row.latex).families):
            if name not in symbol_names and name not in bound:
                symbol_names.append(name)

    out = [
        f"% Declaration sidecar for {dossier.key}",
        f"% {_oneline(dossier.source.title)}",
        "%",
        "% STUB — written by corpusbuilder.promote because no sidecar existed.",
        "% Fill in the ? placeholders, delete the lines that do not apply, then save",
        f"% this file as corpus/declarations/{dossier.key}.tex and re-run promote.",
        "%",
        "% Every symbol below was read out of the accepted formulas. Where a line",
        "% already carries a kind, the algebra or the reviewer settled it and the",
        "% comment above it says which; check it, do not assume it. Where three lines",
        "% appear, decide whether the symbol is an index family, a parameter or a",
        "% variable and delete the two that are wrong. Bibliographic lines",
        "% (meta/name/desc/prov) are generated from the dossier — do not add them here.",
        "%",
        "% index NAME  ordered=0|1 cyclic=0|1",
        "% param NAME  shape=I,J|- kind=scalar|vector|matrix|big_m|tolerance",
        "% var   NAME  shape=I,J|- domain=continuous|non_negative|integer|binary",
        "%              role=primary|auxiliary|slack|indicator",
        "% obj         sense=min|max name=IDENT combination=sum|weighted_sum|lexicographic",
        "% con   NAME  kind=linear|big_m|ordering|headway|capacity|flow_balance|modulo|...",
        "",
        "%@ obj sense=? name=objective combination=sum :: ?",
        "",
    ]
    for symbol in symbol_names:
        ident = _NON_IDENT.sub("_", symbol).strip("_")
        if not ident or not re.match(r"^[A-Za-z_]", ident):
            out.append(f"% unusable as an identifier, rename or drop: {symbol}")
            continue
        kind = kinds.get(symbol)
        if kind in _KIND_LINE:
            if symbol in reviewed:
                why = _KIND_SOURCE["review"].format(kind=kind)
            else:
                why = _KIND_SOURCE[kind].format(domain=evidence.domains.get(symbol, "?"))
            out.append(f"% {symbol}: {why}")
            out.append(_kind_line(kind, ident, evidence.domains.get(symbol)))
        else:
            for name in _KIND_LINE:
                out.append(_kind_line(name, ident, None))
        out.append("")
    if bound:
        out.append(
            "% Bound by a quantifier or big operator, so no declaration of their own: "
            + ", ".join(sorted(bound))
        )
        out.append("% (If one of these is really an index family, add a %@ index line for it.)")
        out.append("")
    out.append("% Constraint rows (optional — they default to kind=linear):")
    for row in rows:
        if not row.is_objective:
            out.append(f"%@ con {row.name} kind=linear domain=- indicator=- :: ?")
    return "\n".join(out) + "\n"


# --------------------------------------------------------------------------- #
# Provenance records
# --------------------------------------------------------------------------- #


def _venue_tier(venue: str | None) -> str:
    """Quality tier of a venue, from ``corpus/venue_tiers.json`` if it exists.

    Ranking venues is an editorial act, so nothing is inferred here: an unlisted
    venue is ``unranked``, and the report says how many entries that covers.
    """
    if not venue or not VENUE_TIERS.exists():
        return "unranked"
    table = json.loads(VENUE_TIERS.read_text(encoding="utf-8"))
    tier = table.get(_oneline(venue).lower())
    return tier if tier in QUALITY_TIERS else "unranked"


def provenance_record(dossier: Dossier, cell: str, *, entry_id: str) -> dict:
    """The ``ProvenanceRecord`` fields for a promoted entry.

    ``citation_count`` prefers the Scopus count, which the dossier documents as
    the authoritative cross-check, and is the count *at the freeze date* — an
    input, never fetched at promotion time.
    """
    source = dossier.source
    shell, activity = CELL_TAXONOMY[cell]
    return {
        "source_id": entry_id,
        "venue": source.venue or source.publisher or "unknown",
        "quality_tier": _venue_tier(source.venue),
        "year": source.year,
        "citation_count": int(source.scopus_cited_by_count or source.cited_by_count or 0),
        "domain_shell": shell,
        "activity": activity,
        "priority_cell": cell,
        "doi": source.doi,
    }


# --------------------------------------------------------------------------- #
# Promotion
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Outcome:
    """What happened to one paper, and why."""

    paper_key: str
    entry_id: str
    promoted: bool
    cause: str | None = None
    detail: str = ""
    counts: dict[str, int] = field(default_factory=dict)
    rows: int = 0
    written: tuple[str, ...] = ()

    @property
    def category(self) -> str | None:
        return CAUSES[self.cause][0] if self.cause else None

    def to_dict(self) -> dict:
        out: dict = {
            "paper_key": self.paper_key,
            "entry_id": self.entry_id,
            "promoted": self.promoted,
            "rows": self.rows,
            "decisions": self.counts,
        }
        if self.cause:
            out["cause"] = self.cause
            out["category"] = self.category
            out["remedy"] = CAUSES[self.cause][1]
            if self.detail:
                out["detail"] = self.detail
        if self.written:
            out["written"] = list(self.written)
        return out


def entry_id_for(paper_key: str) -> str:
    """The canonical ``Formulation`` id for a paper key.

    ``Formulation.id`` is lower-case and restricted to ``[a-z0-9_.-]``; DOI-derived
    dossier keys already fit once folded, and the fold is injective over the keys
    on disk (a collision is reported as ``id_conflict``, never merged).
    """
    ident = re.sub(r"[^a-z0-9_.-]+", "-", paper_key.lower()).strip("-_.")
    return ident if re.match(r"^[a-z0-9]", ident) else f"p-{ident}"


def promote_paper(
    dossier: Dossier,
    decisions: PaperDecisions,
    *,
    declarations_dir: Path = DECLARATIONS,
    write: bool = True,
    out_dirs: dict[str, Path] | None = None,
    symbols: dict[str, str] | None = None,
) -> Outcome:
    """Promote one paper, or explain in one cause why it could not be promoted.

    ``out_dirs`` redirects the written artifacts (``formulations`` / ``provenance``
    / ``promoted``), so a caller can promote into a staging tree instead of the
    live corpus. Keys it omits keep their default.
    """
    dirs = {
        "formulations": FORMULATIONS,
        "provenance": PROVENANCE,
        "promoted": PROMOTED,
        "declarations": declarations_dir,
    }
    dirs.update(out_dirs or {})
    dirs["declarations"] = declarations_dir
    entry_id = entry_id_for(decisions.paper_key)
    rows, counts = rows_for(dossier, decisions)

    def fail(cause: str, detail: str = "", written: tuple[str, ...] = ()) -> Outcome:
        return Outcome(
            paper_key=decisions.paper_key,
            entry_id=entry_id,
            promoted=False,
            cause=cause,
            detail=detail,
            counts=counts,
            rows=len(rows),
            written=written,
        )

    decided = sum(counts.get(s, 0) for s in TERMINAL_STATUSES)
    if not decided:
        return fail("not_reviewed")
    if not rows:
        return fail(
            "all_rejected",
            f"{counts.get('rejected', 0)} rejected, {counts.get('duplicate', 0)} duplicate",
        )

    empty = [
        d.formula_id for d in decisions.decisions if d.status == "corrected" and not d.replacement
    ]
    if empty:
        return fail("corrected_without_replacement", ", ".join(sorted(empty)))

    objectives = [r.name for r in rows if r.is_objective]
    if not objectives:
        return fail("no_objective")
    if len(objectives) > 1:
        return fail("multiple_objectives", ", ".join(objectives))

    cell = decisions.cell
    if cell is None or cell not in PRIORITY_CELLS:
        return fail("not_sorted", str(cell))

    decl_path = dirs["declarations"] / f"{dossier.key}.tex"
    if not decl_path.exists():
        written: tuple[str, ...] = ()
        if write:
            stub = dirs["declarations"] / f"{dossier.key}.stub.tex"
            stub.parent.mkdir(parents=True, exist_ok=True)
            stub.write_text(declaration_stub(dossier, rows, symbols), encoding="utf-8")
            written = (_rel(stub),)
        return fail("missing_declarations", _rel(decl_path), written)

    declarations = decl_path.read_text(encoding="utf-8")
    document = assemble(dossier, rows, declarations, entry_id=entry_id)
    written_paths: list[str] = []
    if write:
        dirs["promoted"].mkdir(parents=True, exist_ok=True)
        tex_path = dirs["promoted"] / f"{entry_id}.tex"
        tex_path.write_text(document, encoding="utf-8")
        written_paths.append(_rel(tex_path))

    result = ingest_latex(document, source=f"corpus/promoted/{entry_id}.tex")
    if not result.ok:
        stage = result.failures[0].stage
        cause = {
            "normalize": "normalize_failed",
            "parse": "outside_grammar",
            "validate": "semantic_invalid",
        }.get(stage, "outside_grammar")
        detail = "; ".join(f.message for f in result.failures)
        return fail(cause, _oneline(detail)[:400], tuple(written_paths))

    formulation = result.formulation
    assert formulation is not None  # guaranteed by IngestionResult.ok
    payload = json.dumps(
        formulation.model_dump(mode="json", warnings=False),
        indent=2,
        ensure_ascii=False,
    )
    # Parse the serialized form back before it is published: a Formulation that
    # cannot be re-loaded from its own file is not a corpus entry.
    load_formulation(payload, source=entry_id)

    record = provenance_record(dossier, cell, entry_id=entry_id)
    if write:
        conflict = _existing_source_conflict(dirs["provenance"] / f"{entry_id}.json", record)
        if conflict:
            return fail("id_conflict", conflict, tuple(written_paths))
        for directory, name, text in (
            (dirs["formulations"], f"{entry_id}.json", payload + "\n"),
            (
                dirs["provenance"],
                f"{entry_id}.json",
                json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            ),
            (
                dirs["promoted"],
                f"{entry_id}.rewrites.json",
                json.dumps(_rewrites(result), indent=2, ensure_ascii=False) + "\n",
            ),
        ):
            directory.mkdir(parents=True, exist_ok=True)
            (directory / name).write_text(text, encoding="utf-8")
            written_paths.append(_rel(directory / name))

    return Outcome(
        paper_key=decisions.paper_key,
        entry_id=entry_id,
        promoted=True,
        counts=counts,
        rows=len(rows),
        written=tuple(written_paths),
    )


def _existing_source_conflict(path: Path, record: dict) -> str:
    """Guard against two different papers claiming one entry id.

    Re-promoting the same paper overwrites its own entry, which is the whole
    point of a regenerable corpus; overwriting a *different* paper's entry (or one
    of the seed templates) would destroy provenance, so it is refused.
    """
    if not path.exists():
        return ""
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return f"{path.name} exists and is not readable JSON"
    if existing.get("source_id") != record["source_id"]:
        return f"{path.name} belongs to source_id {existing.get('source_id')!r}"
    return ""


def _rewrites(result) -> dict:
    """The M1b rewrites that fired, as a diffable artifact."""
    provenance = result.provenance
    return {
        "source": result.source,
        "rules_version": REWRITE_RULES_VERSION,
        "rewrites": [
            {
                "rule": r.rule,
                "before": r.before,
                "after": r.after,
                "line": r.span.line,
                "start": r.span.start,
                "end": r.span.end,
            }
            for r in (provenance.rewrites if provenance else ())
        ],
    }


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def build_report(outcomes: list[Outcome], unrecognised: dict[str, int]) -> dict:
    """The promotion report: what was promoted, and every failure by cause."""
    by_cause: Counter[str] = Counter()
    by_category: Counter[str] = Counter()
    decisions: Counter[str] = Counter()
    for outcome in outcomes:
        for status, n in outcome.counts.items():
            decisions[status] += n
        if outcome.cause:
            by_cause[outcome.cause] += 1
            by_category[CAUSES[outcome.cause][0]] += 1

    promoted = [o for o in outcomes if o.promoted]
    return {
        "schema_version": PROMOTION_SCHEMA,
        "derived_from": "corpus/decisions/*.json + corpus/dossiers/*.json + corpus/declarations/*.tex",
        "rewrite_rules_version": REWRITE_RULES_VERSION,
        "papers_with_decisions": len(outcomes),
        "promoted": len(promoted),
        "failed": len(outcomes) - len(promoted),
        "formula_decisions": dict(sorted(decisions.items())),
        "unrecognised_status": unrecognised,
        "failures_by_cause": {
            cause: {
                "papers": by_cause[cause],
                "category": CAUSES[cause][0],
                "remedy": CAUSES[cause][1],
            }
            for cause in CAUSES
            if by_cause[cause]
        },
        "failures_by_category": dict(sorted(by_category.items())),
        "not_assessed_here": NOT_ASSESSED_HERE,
        "papers": [o.to_dict() for o in outcomes],
    }


def render_report_md(report: dict) -> str:
    lines = [
        "# Promotion report",
        "",
        "Decisions -> canonical formulations, generated by `corpusbuilder.promote`.",
        "Regenerate with `PYTHONPATH=. python3 -m corpusbuilder.promote`; do not edit by hand.",
        "",
        f"- papers with decisions: **{report['papers_with_decisions']}**",
        f"- promoted: **{report['promoted']}**",
        f"- failed: **{report['failed']}**",
        f"- M1b rewrite rules: `{report['rewrite_rules_version']}`",
        "",
        "## Formula decisions consumed",
        "",
    ]
    for status, n in report["formula_decisions"].items():
        lines.append(f"- {status}: {n}")
    if report["unrecognised_status"]:
        joined = ", ".join(f"{k} {v}" for k, v in report["unrecognised_status"].items())
        lines.append(f"- **unrecognised statuses: {joined}**")

    lines += ["", "## Failures by cause", ""]
    if report["failures_by_cause"]:
        lines.append("| cause | category | papers | remedy |")
        lines.append("| --- | --- | ---: | --- |")
        for cause, info in report["failures_by_cause"].items():
            lines.append(
                f"| `{cause}` | {info['category']} | {info['papers']} | {info['remedy']} |"
            )
    else:
        lines.append("None.")

    lines += [
        "",
        "Categories `extraction_error`, `outside_grammar` and `under_specified` are",
        "findings about the source material. `pipeline_incomplete` is a finding about",
        "this pipeline (an unreviewed paper, an unsorted paper, a missing dossier) and",
        "must not be reported as a property of the literature.",
        "",
        "Not decided at promotion time:",
        "",
    ]
    for cause, why in report["not_assessed_here"].items():
        lines.append(f"- `{cause}` — {why}")

    lines += [
        "",
        "## Papers",
        "",
        "| paper | entry id | outcome | rows | detail |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for paper in report["papers"]:
        verdict = "promoted" if paper["promoted"] else f"`{paper['cause']}`"
        detail = paper.get("detail", "").replace("|", "\\|")
        lines.append(
            f"| {paper['paper_key']} | `{paper['entry_id']}` | {verdict} | {paper['rows']} | {detail} |"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def promote_all(
    *,
    decisions_dir: Path = DECISIONS,
    dossiers_dir: Path = DOSSIERS,
    declarations_dir: Path = DECLARATIONS,
    out_dirs: dict[str, Path] | None = None,
    write: bool = True,
    only: set[str] | None = None,
) -> dict:
    """Promote every paper that has decisions, and return the report."""
    paths = sorted(decisions_dir.glob("*.json")) if decisions_dir.exists() else []
    papers, unrecognised = load_decisions(paths)
    symbol_tables = load_symbol_tables(paths)

    outcomes: list[Outcome] = []
    for key, decisions in papers.items():
        if only and key not in only:
            continue
        dossier_path = dossiers_dir / f"{key}.json"
        if not dossier_path.exists():
            outcomes.append(
                Outcome(
                    paper_key=key,
                    entry_id=entry_id_for(key),
                    promoted=False,
                    cause="no_dossier",
                    detail=_rel(dossier_path),
                )
            )
            continue
        outcomes.append(
            promote_paper(
                Dossier.load(dossier_path),
                decisions,
                declarations_dir=declarations_dir,
                out_dirs=out_dirs,
                write=write,
                symbols=symbol_tables.get(key),
            )
        )
    return build_report(outcomes, unrecognised)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusbuilder.promote",
        description="Promote HITL decisions into canonical Formulations + ProvenanceRecords.",
    )
    parser.add_argument("--decisions", type=Path, default=DECISIONS, help="decision export dir")
    parser.add_argument("--dossiers", type=Path, default=DOSSIERS, help="dossier dir")
    parser.add_argument(
        "--declarations", type=Path, default=DECLARATIONS, help="declaration sidecar dir"
    )
    parser.add_argument(
        "--only", action="append", default=[], metavar="PAPER_KEY", help="promote only this paper"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="report only; write no corpus artifacts"
    )
    args = parser.parse_args(argv)

    if not args.decisions.exists():
        print(
            f"No decisions directory at {args.decisions} — export a review session from the "
            "game (or review_view) first.",
            file=sys.stderr,
        )
        return 1

    report = promote_all(
        decisions_dir=args.decisions,
        dossiers_dir=args.dossiers,
        declarations_dir=args.declarations,
        write=not args.dry_run,
        only=set(args.only) or None,
    )

    if not args.dry_run:
        (CORPUS / "promotion.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        (CORPUS / "promotion.md").write_text(render_report_md(report), encoding="utf-8")

    print(
        f"papers with decisions: {report['papers_with_decisions']} · "
        f"promoted: {report['promoted']} · failed: {report['failed']}"
    )
    for cause, info in report["failures_by_cause"].items():
        print(f"  {cause:<32} {info['papers']:>4}  ({info['category']})")
    if args.dry_run:
        print("(dry run — nothing written)")
    else:
        print("wrote corpus/promotion.json, corpus/promotion.md")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
