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

Kinds come from the review game's symbol classifier via the decision exports
(``corpus/decisions/*.json``, key ``symbol_tables``); before any review has
happened the only typed symbols are the ones the binder of a big operator
identifies deterministically as index families, which is the free prefill the
manual stage starts from.

Outputs (all under ``corpus/``):
  * ``resolution.json``        — per-paper and corpus-level counts (source of truth)
  * ``resolution.md``          — human-readable report with the routing queues
  * ``resolution_macros.tex``  — ``\\newcommand`` per count, for ``\\input`` into the paper

Run:  PYTHONPATH=. python3 -m corpusbuilder.resolution
"""

from __future__ import annotations

import json
import re
import statistics
from pathlib import Path

from corpusbuilder.dossier import Dossier
from corpusbuilder.game import (
    _collapse_words,
    _group_end,
    _rewrite_ops,
    extract_symbols,
    is_objective_latex,
    parse_tree,
    render_latex,
)
from corpusbuilder.promote import DECISIONS, load_symbol_tables
from corpusbuilder.split import split_latex

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"

#: Operators whose subscript binds an index over a family, plus the universal
#: quantifier. A symbol named there is an index or an index family, which is the
#: one kind assignment the algebra states outright instead of leaving to the prose.
_BINDER_HEAD = re.compile(r"\\(?:sum|prod|bigcup|bigcap|max|min|forall)(?![a-zA-Z])")

#: Where a ``\forall`` clause ends: it runs to the end of the formula or to the
#: next relation/separator, unlike a subscript group whose braces delimit it.
_FORALL_END = re.compile(r"[,;]|\\\\|\\quad|\\qquad|\\text")

#: The share of a paper's formulas the greedy symbol ordering must reach before
#: we call the paper "mostly resolved" — used only to report how many symbols
#: that costs, i.e. whether the manual stage has useful leverage or has to type
#: the whole table.
_LEVERAGE_TARGET = 0.8


def formula_symbols(latex: str) -> set[str]:
    """Every distinct symbol in a formula (never the truncated display list)."""
    return {name for name, _ in extract_symbols(latex, limit=None)[0]}


def binder_symbols(latex: str) -> set[str]:
    """Symbols named in a big operator's binder or a ``\\forall`` clause.

    Read off the normalized source rather than the expression tree: the tree
    flattens a binder to a display string, and that flattening leaks artifacts
    (``\\left`` contributes a spurious ``ft``) which would then auto-type a
    body symbol of the same name. Reuses :func:`extract_symbols` on the binder
    text so one scanner defines what counts as a symbol everywhere.
    """
    s = _rewrite_ops(_collapse_words(latex))
    found: set[str] = set()
    for m in _BINDER_HEAD.finditer(s):
        i = m.end()
        if m.group(0) == "\\forall":
            end = _FORALL_END.search(s, i)
            chunk = s[i : end.start() if end else len(s)]
        else:
            # Both scripts bind: "\\sum_{t = 1}^{T}" names the index below and
            # the family bound above, in either written order.
            chunks = []
            while True:
                while i < len(s) and s[i] == " ":
                    i += 1
                if i >= len(s) or s[i] not in "_^":
                    break
                i += 1
                while i < len(s) and s[i] == " ":
                    i += 1
                if i < len(s) and s[i] == "{":
                    end = _group_end(s, i)
                    chunks.append(s[i + 1 : end - 1])
                    i = end
                else:
                    chunks.append(s[i : i + 1])
                    i += 1
            chunk = " ".join(chunks)
        found.update(name for name, _ in extract_symbols(chunk, limit=None)[0])
    return found


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
    """Resolution state of one paper, deterministic in dossier formula order."""
    formulas, symbol_sets = [], []
    binders: set[str] = set()
    for f in dossier.formulas:
        names = formula_symbols(f.latex)
        symbol_sets.append(names)
        binders |= binder_symbols(f.latex) & names
        formulas.append({"id": f.id, "n_symbols": len(names), **structural_flags(f.latex)})

    symbols = sorted(set().union(*symbol_sets)) if symbol_sets else []
    typed = {s for s in symbols if s in table} | binders
    for entry, names in zip(formulas, symbol_sets, strict=True):
        entry["beta"] = round(len(names & typed) / len(names), 3) if names else 1.0
        entry["resolved"] = bool(names) and names <= typed
        entry["clean"] = all(entry[k] for k in ("parses", "single", "renders", "statement"))
    return {
        "key": dossier.key,
        "n_formulas": len(formulas),
        "n_symbols": len(symbols),
        "n_typed": len(typed),
        "n_binder_typed": len(binders),
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
        "binder_typed_pairs": sum(p["n_binder_typed"] for p in papers),
        "binder_typed_pct": round(100 * sum(p["n_binder_typed"] for p in papers) / pairs, 1)
        if pairs
        else 0.0,
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
        f"{r['binder_typed_pairs']} ({r['binder_typed_pct']}%) come free from big-operator binders",
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
        "| paper | formulas | symbols | typed | resolved | clean | ready |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for p in r["per_paper"]:
        lines.append(
            f"| {p['key']} | {p['n_formulas']} | {p['n_symbols']} | {p['n_typed']} | "
            f"{p['n_resolved']} | {p['n_clean']} | {p['n_ready']} |"
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
                cmd("resBinderTyped", r["binder_typed_pairs"]),
                cmd("resBinderTypedPct", r["binder_typed_pct"]),
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
        f"({r['binder_typed_pairs']} from binders); "
        f"{r['resolved']} formulas fully broken down; "
        f"{r['structurally_clean']} ({r['structurally_clean_pct']}%) structurally clean; "
        f"{r['ready']} ready. "
        f"wrote resolution.json, resolution.md, resolution_macros.tex"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
