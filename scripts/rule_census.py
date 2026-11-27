"""Rule census and ablation: which rewrite rules do the canonical rows need?

A rule that only one paper needs is fitted to that paper. This script counts,
for every canonical row of every promoted paper, the M1b rewrite rules that
fired on it and the kinds of derived names its reading introduced, and it can
re-run promotion (dry) with the rarely needed rules switched off.

    PYTHONPATH=. python3 scripts/rule_census.py census OUT.json
    PYTHONPATH=. python3 scripts/rule_census.py table OUT.json
    PYTHONPATH=. python3 scripts/rule_census.py ablate OUT.json K RESULT.json

"census" writes {paper: {row: [rules and kinds]}} (names only, no row text).
"table" prints papers and rows per rule and how many canonical rows depend on
a rule needed by at most k papers. "ablate" switches off every rewrite rule
needed by at most K papers (and the rules no canonical row needs) and reports
the yield promote then measures; the sidecars stay as they are, so this is a
first-order sensitivity.

Limits: the structural row reader is two rules (row_exact, row_widened) whose
internal readings are not separately named or switchable, and the declaration
rules (corpusbuilder.detdecl) are not attributed to rows. Derived-name kinds
are counted but cannot be switched off.
"""

# ruff: noqa: I001 — railpminer._lp2graph must run before the lp2graph imports.

from __future__ import annotations

import dataclasses
import json
import re
import sys
from collections import defaultdict
from multiprocessing import Pool
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401
from lp2graph.mining.ingest import normalize_latex
from lp2graph.mining.ingest import latex_normalizer as LN

from corpusbuilder import promote as P
from corpusbuilder.dossier import Dossier

FIRED: set[str] = set()
STATE: dict = {}

#: Derived names by prefix and by infix -> the kind of reading that made them.
DERIVED_PREFIX = (
    ("case_", "derived:case"),
    ("card_", "derived:cardinality"),
    ("val_", "derived:index value"),
    ("aux_", "derived:auxiliary variable"),
)
DERIVED_INFIX = (
    ("_idx_", "derived:written against declaration"),
    ("_on_", "derived:coefficient broadcast"),
    ("_over_", "derived:ratio"),
    ("_x_", "derived:product"),
)


def instrument() -> None:
    """Wrap every rule so that a firing (a text change) is recorded in FIRED."""
    wrapped = []
    for rule in LN.REWRITE_RULES:

        def wrap(fn, rid=rule.rule_id, literal=None):
            def inner(*args):
                after = literal if fn is None else fn(*args)
                if after != args[0].group(0):
                    FIRED.add(rid)
                return after

            return inner

        if rule.ctx_replacement is not None:
            wrapped.append(dataclasses.replace(rule, ctx_replacement=wrap(rule.ctx_replacement)))
        elif callable(rule.replacement):
            wrapped.append(dataclasses.replace(rule, replacement=wrap(rule.replacement)))
        else:
            wrapped.append(
                dataclasses.replace(rule, replacement=wrap(None, literal=rule.replacement))
            )
    LN.REWRITE_RULES = tuple(wrapped)


def derived_kinds(norm_doc: str) -> set[str]:
    kinds = set()
    for m in re.finditer(r"^%@ (?:param|var) (\S+) [^\n]*:: (derived|var:)", norm_doc, re.M):
        name = m.group(1)
        hit = next((k for p, k in DERIVED_PREFIX if name.startswith(p)), None)
        hit = hit or next((k for p, k in DERIVED_INFIX if p in name), None)
        kinds.add(hit or "derived:other")
    return kinds


def census_paper(key: str) -> tuple[str, dict | None]:
    decisions = STATE["decisions"]
    dossier_path = P.DOSSIERS / f"{key}.json"
    sidecar = P.DECLARATIONS / f"{key}.tex"
    if not dossier_path.exists() or not sidecar.exists():
        return key, None
    dossier = Dossier.load(dossier_path)
    declarations = sidecar.read_text(encoding="utf-8")
    entry = P.entry_id_for(key)
    FIRED.clear()
    outcome = P.promote_paper(dossier, decisions[key], write=False, partial=True)
    if not outcome.promoted:
        return key, None
    excluded = {e["name"] for e in outcome.rows_excluded}
    rows, _counts = P.rows_for(dossier, decisions[key])
    rows, _absorbed = P.absorb_declaration_rows(rows)
    usable = [r for r in rows if r.is_objective and r.name not in excluded]
    result = {}
    for row in rows:
        if row.name in excluded:
            continue
        FIRED.clear()
        base = [row] if row.is_objective else [*usable, row]
        doc = P.assemble(dossier, base, declarations, entry_id=entry)
        norm, prov = normalize_latex(doc, source="census")
        tag = "\\tag{" + row.name.replace("_", "\\_") + "}"
        line = next((i for i, ln in enumerate(doc.splitlines()) if tag in ln), -1)
        fired = set(FIRED)
        # the structural rules of the document pass count for the row they touched
        fired -= {
            rw.rule
            for rw in prov.rewrites
            if rw.span.line - 1 != line and rw.rule in ("row_exact", "row_widened")
        }
        result[row.name] = sorted(fired | derived_kinds(norm))
    return key, result


def dry_promote(key: str) -> tuple[bool, int, int]:
    dossier_path = P.DOSSIERS / f"{key}.json"
    if not dossier_path.exists():
        return (False, 0, 0)
    outcome = P.promote_paper(
        Dossier.load(dossier_path), STATE["decisions"][key], write=False, partial=True
    )
    return (outcome.promoted, outcome.rows, outcome.rows_included if outcome.promoted else 0)


def usage(census: dict) -> tuple[dict[str, set[str]], dict[str, int]]:
    papers: dict[str, set[str]] = defaultdict(set)
    rows: dict[str, int] = defaultdict(int)
    for key, per_row in census.items():
        for fired in per_row.values():
            for rule in fired:
                papers[rule].add(key)
                rows[rule] += 1
    return papers, rows


def table(path: str) -> None:
    census = json.loads(Path(path).read_text(encoding="utf-8"))
    papers, rows = usage(census)
    total = sum(len(v) for v in census.values())
    print(f"papers {len(census)}, canonical rows {total}, rules and kinds needed {len(papers)}")
    for rule in sorted(papers, key=lambda r: (len(papers[r]), rows[r])):
        print(f"{len(papers[rule]):4d} papers {rows[rule]:5d} rows  {rule}")
    for k in (1, 2, 5, 10, 20):
        weak = {r for r, ps in papers.items() if len(ps) <= k}
        hit = sum(1 for per_row in census.values() for f in per_row.values() if weak & set(f))
        print(f"needed by <= {k} papers: {len(weak)} rules/kinds, {hit} rows depend on one of them")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in ("census", "table", "ablate"):
        print((__doc__ or "").strip())
        return 2
    if args[0] == "table":
        table(args[1])
        return 0
    decisions, _unrecognised = P.load_decisions(sorted(P.DECISIONS.glob("*.json")))
    STATE["decisions"] = decisions
    if args[0] == "census":
        instrument()
        with Pool(3) as pool:
            results = dict(pool.map(census_paper, sorted(decisions), chunksize=2))
        kept = {k: v for k, v in results.items() if v}
        with open(args[1], "w", encoding="utf-8", newline="\n") as fh:
            json.dump(kept, fh)
        print("papers", len(kept), "rows", sum(len(v) for v in kept.values()))
        return 0
    census = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    k = int(args[2])
    papers, _rows = usage(census)
    off = {r for r, ps in papers.items() if len(ps) <= k and not r.startswith("derived:")}
    never = {r.rule_id for r in LN.REWRITE_RULES} - set(papers)
    LN.REWRITE_RULES = tuple(r for r in LN.REWRITE_RULES if r.rule_id not in off | never)
    with Pool(3) as pool:
        outcomes = pool.map(dry_promote, sorted(decisions), chunksize=2)
    total = sum(o[1] for o in outcomes)
    included = sum(o[2] for o in outcomes)
    promoted = sum(1 for o in outcomes if o[0])
    print(
        f"K={k}: off {len(off)} rules needed by <= {k} papers + {len(never)} never needed; "
        f"yield {included}/{total} = {100 * included / total:.2f} %; promoted {promoted}"
    )
    with open(args[3], "w", encoding="utf-8", newline="\n") as fh:
        json.dump(
            {
                "k": k,
                "off": sorted(off),
                "never": sorted(never),
                "included": included,
                "total": total,
                "promoted": promoted,
            },
            fh,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
