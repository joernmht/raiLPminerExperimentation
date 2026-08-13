"""Generate the **symbol-resolution artifact** — how far the corpus has been
broken down into declared symbols, and where the deterministic wall lies.

A recovered display equation carries algebra but no declarations: nothing in
``\\sum_{i \\in I} c_i x_i`` says that ``I`` is an index family, ``c`` a
parameter and ``x`` a variable. The canonical grammar needs exactly that, so a
formula becomes ingestible only once every symbol it uses has a kind in its
paper's symbol table. This module measures the gap.

For each candidate formula it computes the **breakdown coefficient**, the share
of the formula's distinct symbols that carry a kind, and the four structural
conditions that are decidable without any symbol table at all (the expression
tree parses, the record holds a single statement, the LaTeX renders without
repair, and the statement has a relation or an optimization head). Together
these say what is left for a human: a formula clean on the structural axes and
at coefficient 1 needs no structural review, while the rest are routed.

Kinds come from two places. :mod:`corpusbuilder.symbols` reads what the algebra
states outright, index families off big-operator binders and decision variables
off domain rows, and the review game's symbol classifier supplies the rest via
the decision exports (``corpus/decisions/*.json``, key ``symbol_tables``). The
first is the free prefill the manual stage starts from; the second is what the
manual stage costs. A reviewer verdict overrides an inference.

Outputs (all under ``corpus/``):
  * ``resolution.json``        — per-paper and corpus-level counts (source of truth)
  * ``resolution.md``          — human-readable report with the routing queues
  * ``resolution_macros.tex``  — ``\\newcommand`` per count, for ``\\input`` into the paper

Run:  PYTHONPATH=. python3 -m corpusbuilder.resolution
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from corpusbuilder.dossier import Dossier
from corpusbuilder.game import (
    extract_symbols,
    is_objective_latex,
    parse_tree,
    render_latex,
)
from corpusbuilder.promote import DECISIONS, load_symbol_tables
from corpusbuilder.split import split_latex
from corpusbuilder.symbols import INDEX, VARIABLE, paper_evidence

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"

#: The share of a paper's formulas the greedy symbol ordering must reach before
#: we call the paper "mostly resolved" — used only to report how many symbols
#: that costs, i.e. whether the manual stage has useful leverage or has to type
#: the whole table.
_LEVERAGE_TARGET = 0.8


def formula_symbols(latex: str) -> set[str]:
    """Every distinct symbol in a formula (never the truncated display list)."""
    return {name for name, _ in extract_symbols(latex, limit=None)[0]}


def structural_flags(latex: str) -> dict[str, bool]:
    """The conditions decidable before any symbol table exists.

    Each is a distinct kind of defect with a distinct remedy, so they are kept
    apart rather than summed: a glued record needs splitting, an unrenderable
    one needs a display repair, a fragment without a relation is not a
    statement at all.
    """
    rel = extract_symbols(latex)[2]
    tree = parse_tree(latex)
    split = split_latex(latex)
    return {
        "parses": tree is not None,
        "single": (not split.is_split) and split.confident,
        "renders": render_latex(latex) == latex,
        "statement": bool(rel) or is_objective_latex(latex),
    }


def _leverage(formulas: list[set[str]], typed: set[str]) -> int:
    """Symbols still to type before ``_LEVERAGE_TARGET`` of a paper resolves.

    Greedy in descending formula frequency, which is the order a reviewer
    working the paper's symbol list would naturally take.
    """
    live = [f for f in formulas if f]
    if not live:
        return 0
    freq: dict[str, int] = {}
    for names in live:
        for name in names:
            freq[name] = freq.get(name, 0) + 1
    order = [n for n, _ in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))]
    known = set(typed)
    target = _LEVERAGE_TARGET * len(live)
    taken = 0
    while taken < len(order) and sum(1 for f in live if f <= known) < target:
        known.add(order[taken])
        taken += 1
    return taken


def paper_record(dossier: Dossier, table: dict[str, str]) -> dict:
    """Resolution state of one paper, deterministic in dossier formula order.

    ``table`` is the reviewer's own symbol table and wins wherever it disagrees
    with the algebraic evidence: a human verdict supersedes an inference.
    """
    evidence = paper_evidence([f.latex for f in dossier.formulas])
    typed_kind = {**evidence.kinds, **table}

    formulas, symbol_sets = [], []
    for f in dossier.formulas:
        names = formula_symbols(f.latex)
        symbol_sets.append(names)
        formulas.append({"id": f.id, "n_symbols": len(names), **structural_flags(f.latex)})

    symbols = sorted(set().union(*symbol_sets)) if symbol_sets else []
    typed = {s for s in symbols if s in typed_kind}
    prefilled = {s for s in typed if s not in table}
    for entry, names in zip(formulas, symbol_sets, strict=True):
        entry["beta"] = round(len(names & typed) / len(names), 3) if names else 1.0
        entry["resolved"] = bool(names) and names <= typed
        entry["clean"] = all(entry[k] for k in ("parses", "single", "renders", "statement"))
    return {
        "key": dossier.key,
        "n_formulas": len(formulas),
        "n_symbols": len(symbols),
        "n_typed": len(typed),
        "n_prefilled": len(prefilled),
        "n_index_prefill": sum(1 for s in prefilled if typed_kind[s] == INDEX),
        "n_variable_prefill": sum(1 for s in prefilled if typed_kind[s] == VARIABLE),
        "n_reviewed": len(typed) - len(prefilled),
        "n_resolved": sum(1 for e in formulas if e["resolved"]),
        "n_clean": sum(1 for e in formulas if e["clean"]),
        "n_ready": sum(1 for e in formulas if e["clean"] and e["resolved"]),
        "symbols_to_leverage": _leverage(symbol_sets, typed),
        "formulas": formulas,
    }


def compute() -> dict:
    tables = load_symbol_tables(sorted(DECISIONS.glob("*.json"))) if DECISIONS.exists() else {}
    papers = []
    for path in sorted(DOSSIERS.glob("*.json")):
        dossier = Dossier.load(path)
        if dossier.formulas:
            papers.append(paper_record(dossier, tables.get(dossier.key, {})))

    entries = [e for p in papers for e in p["formulas"]]
    n = len(entries)
    pairs = sum(p["n_symbols"] for p in papers)

    def share(count: int) -> float:
        return round(100 * count / n, 1) if n else 0.0

    axes = {
        k: sum(1 for e in entries if e[k]) for k in ("parses", "single", "renders", "statement")
    }
    return {
        "papers": len(papers),
        "formulas": n,
        "symbol_pairs": pairs,
        "symbols_per_paper_median": int(statistics.median(p["n_symbols"] for p in papers)),
        "symbols_per_paper_max": max((p["n_symbols"] for p in papers), default=0),
        "symbols_per_formula_median": int(statistics.median(e["n_symbols"] for e in entries)),
        "symbols_per_formula_max": max((e["n_symbols"] for e in entries), default=0),
        "typed_pairs": sum(p["n_typed"] for p in papers),
        "prefilled_pairs": sum(p["n_prefilled"] for p in papers),
        "prefilled_pct": round(100 * sum(p["n_prefilled"] for p in papers) / pairs, 1)
        if pairs
        else 0.0,
        "index_prefill_pairs": sum(p["n_index_prefill"] for p in papers),
        "variable_prefill_pairs": sum(p["n_variable_prefill"] for p in papers),
        "reviewed_pairs": sum(p["n_reviewed"] for p in papers),
        "resolved": sum(p["n_resolved"] for p in papers),
        "resolved_pct": share(sum(p["n_resolved"] for p in papers)),
        "structural_axes": axes,
        "structural_axes_pct": {k: share(v) for k, v in axes.items()},
        "structurally_clean": sum(p["n_clean"] for p in papers),
        "structurally_clean_pct": share(sum(p["n_clean"] for p in papers)),
        "ready": sum(p["n_ready"] for p in papers),
        "ready_pct": share(sum(p["n_ready"] for p in papers)),
        "symbols_to_leverage_median": int(
            statistics.median(p["symbols_to_leverage"] for p in papers)
        )
        if papers
        else 0,
        "leverage_target_pct": int(100 * _LEVERAGE_TARGET),
        "per_paper": [{k: v for k, v in p.items() if k != "formulas"} for p in papers],
    }


_AXIS_LABEL = {
    "parses": "expression tree parses",
    "single": "one statement per record",
    "renders": "renders without repair",
    "statement": "has a relation or optimization head",
}


def render_md(r: dict) -> str:
    lines = [
        "# Symbol resolution",
        "",
        "Auto-generated by `corpusbuilder.resolution`; do not edit.",
        "",
        f"- papers with formulas: **{r['papers']}**",
        f"- candidate formulas: **{r['formulas']}**",
        f"- distinct (paper, symbol) pairs: **{r['symbol_pairs']}** "
        f"(median {r['symbols_per_paper_median']} per paper, max {r['symbols_per_paper_max']})",
        f"- symbols per formula: median {r['symbols_per_formula_median']}, "
        f"max {r['symbols_per_formula_max']}",
        "",
        "## Typed symbols",
        "",
        f"- typed: **{r['typed_pairs']}** of {r['symbol_pairs']} pairs, of which "
        f"{r['prefilled_pairs']} ({r['prefilled_pct']}%) come free from the algebra "
        f"({r['index_prefill_pairs']} indices and index families from binders, "
        f"{r['variable_prefill_pairs']} variables from domain rows) and "
        f"{r['reviewed_pairs']} from reviewer verdicts",
        f"- formulas at breakdown coefficient 1: **{r['resolved']}** ({r['resolved_pct']}%)",
        f"- symbols still to type for {r['leverage_target_pct']}% of a paper's formulas: "
        f"median {r['symbols_to_leverage_median']}",
        "",
        "## Structural axes (decidable without a symbol table)",
        "",
        "| condition | clean | share |",
        "| --- | ---: | ---: |",
    ]
    for key, label in _AXIS_LABEL.items():
        lines.append(
            f"| {label} | {r['structural_axes'][key]} | {r['structural_axes_pct'][key]}% |"
        )
    lines += [
        f"| **all four** | **{r['structurally_clean']}** | **{r['structurally_clean_pct']}%** |",
        "",
        f"Ready for promotion without structural review (clean on all four axes *and* "
        f"fully broken down): **{r['ready']}** ({r['ready_pct']}%).",
        "",
        "## Per paper",
        "",
        "| paper | formulas | symbols | typed | prefill | resolved | clean | ready |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for p in r["per_paper"]:
        lines.append(
            f"| {p['key']} | {p['n_formulas']} | {p['n_symbols']} | {p['n_typed']} | "
            f"{p['n_prefilled']} | {p['n_resolved']} | {p['n_clean']} | {p['n_ready']} |"
        )
    return "\n".join(lines) + "\n"


def render_macros(r: dict) -> str:
    def cmd(name: str, value) -> str:
        return f"\\newcommand{{\\{name}}}{{{value}}}"

    return (
        "\n".join(
            [
                "% Symbol-resolution counts — auto-generated by corpusbuilder.resolution;"
                " do not edit.",
                cmd("resPapers", r["papers"]),
                cmd("resFormulas", r["formulas"]),
                cmd("resSymbolPairs", r["symbol_pairs"]),
                cmd("resSymbolsPerPaper", r["symbols_per_paper_median"]),
                cmd("resSymbolsPerPaperMax", r["symbols_per_paper_max"]),
                cmd("resSymbolsPerFormula", r["symbols_per_formula_median"]),
                cmd("resSymbolsPerFormulaMax", r["symbols_per_formula_max"]),
                cmd("resPrefilled", r["prefilled_pairs"]),
                cmd("resPrefilledPct", r["prefilled_pct"]),
                cmd("resIndexPrefill", r["index_prefill_pairs"]),
                cmd("resVariablePrefill", r["variable_prefill_pairs"]),
                cmd("resReviewed", r["reviewed_pairs"]),
                cmd("resLeverageSymbols", r["symbols_to_leverage_median"]),
                cmd("resLeverageTarget", r["leverage_target_pct"]),
                cmd("resParseClean", r["structural_axes"]["parses"]),
                cmd("resParseCleanPct", r["structural_axes_pct"]["parses"]),
                cmd("resSingleClean", r["structural_axes"]["single"]),
                cmd("resSingleCleanPct", r["structural_axes_pct"]["single"]),
                cmd("resRenderClean", r["structural_axes"]["renders"]),
                cmd("resRenderCleanPct", r["structural_axes_pct"]["renders"]),
                cmd("resStatementClean", r["structural_axes"]["statement"]),
                cmd("resStatementCleanPct", r["structural_axes_pct"]["statement"]),
                cmd("resStructuralClean", r["structurally_clean"]),
                cmd("resStructuralCleanPct", r["structurally_clean_pct"]),
                cmd("resTypedPairs", r["typed_pairs"]),
                cmd("resResolved", r["resolved"]),
                cmd("resResolvedPct", r["resolved_pct"]),
                cmd("resReady", r["ready"]),
                cmd("resReadyPct", r["ready_pct"]),
            ]
        )
        + "\n"
    )


def main(out_dir: Path | None = None) -> int:
    out = out_dir or CORPUS
    out.mkdir(parents=True, exist_ok=True)
    r = compute()
    (out / "resolution.json").write_text(json.dumps(r, indent=2, sort_keys=True) + "\n")
    (out / "resolution.md").write_text(render_md(r))
    (out / "resolution_macros.tex").write_text(render_macros(r))
    print(
        f"Resolution: {r['formulas']} formulas over {r['papers']} papers; "
        f"{r['typed_pairs']}/{r['symbol_pairs']} symbol pairs typed "
        f"({r['index_prefill_pairs']} index + {r['variable_prefill_pairs']} variable "
        f"from the algebra); "
        f"{r['resolved']} formulas fully broken down; "
        f"{r['structurally_clean']} ({r['structurally_clean_pct']}%) structurally clean; "
        f"{r['ready']} ready. "
        f"wrote resolution.json, resolution.md, resolution_macros.tex"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
