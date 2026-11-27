"""Abstracted graphs: the structure of every promoted model without its words.

The canonical model of a paper restates the paper's formulation (its symbols,
its descriptions, its rows), so it is public only for openly licensed sources
(``corpus/public_keys.txt``). What a structural search needs is less: which
kinds of nodes a model has and how they are connected. This script writes that
layer for every promoted paper to ``corpus/graphs/<key>.json``:

* the schema graph of the canonical model (``lp2graph.views.schema``) with
  every identifier replaced by a class and a running number (``var:3``,
  ``con:12``, ``index:2``), and without labels or descriptions;
* what is structural about each node and edge: class and subtype, the shape as
  index ids, comparator, quantifiers, variable role, sign, operator, numeric
  coefficients and right-hand sides; a symbolic coefficient is the id of its
  parameter node;
* per model: DOI, the objective feature, candidate and canonical row counts,
  and (with ``--patterns``) the named patterns the pattern search found.

No symbol name, description or formula is written. The files are meant to be
public for all papers; ``corpus/graphs`` is therefore not among the per-paper
directories that ``scripts/publish_public.py`` filters.

    PYTHONPATH=. python3 scripts/export_abstract_graphs.py \\
        [--patterns PATH/graph_patterns.json] [--out corpus/graphs]
"""

# ruff: noqa: I001 — railpminer._lp2graph must run before the lp2graph imports.

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401
from lp2graph import loads
from lp2graph.views.schema import schema

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
SCHEMA_VERSION = "abstract-graph-1"
_PREFIX = {
    "index": "index",
    "parameter": "param",
    "variable": "var",
    "constraint": "con",
    "operator": "op",
    "objective": "obj",
}


def abstract_graph(formulation) -> dict:
    """The schema graph of ``formulation`` with anonymous ids and no text."""
    graph = schema(formulation)
    counters: Counter[str] = Counter()
    new_id: dict[str, str] = {}
    for node in graph.nodes:
        prefix = _PREFIX.get(node.cls, node.cls)
        counters[prefix] += 1
        new_id[node.id] = f"{prefix}:{counters[prefix]}"
    family = {n.label: new_id[n.id] for n in graph.nodes if n.cls == "index"}
    parameter = {n.label: new_id[n.id] for n in graph.nodes if n.cls == "parameter"}

    def families(names) -> list[str]:
        return [family.get(str(name), "index:?") for name in names or ()]

    nodes = []
    for node in graph.nodes:
        out: dict = {"id": new_id[node.id], "cls": node.cls, "subtype": node.subtype}
        if node.shape:
            out["shape"] = families(node.shape)
        data = node.data or {}
        if node.cls == "constraint":
            out["comparator"] = data.get("comparator")
            out["rhs_literal"] = data.get("rhs_literal")
            out["quantifiers"] = [
                {
                    "over": family.get(str(q.get("over")), "index:?"),
                    "restriction": q.get("restriction"),
                }
                for q in data.get("quantifiers") or []
            ]
        elif node.cls == "variable":
            out["role"] = data.get("role")
            out["bounded"] = [data.get("lower") is not None, data.get("upper") is not None]
        elif node.cls == "operator":
            out["over"] = families(data.get("operator_over"))
        elif node.cls == "objective":
            out["combination"] = data.get("combination")
        nodes.append(out)

    edges = []
    for edge in graph.edges:
        out = {
            "src": new_id[edge.src],
            "dst": new_id[edge.dst],
            "type": edge.type,
            "role": edge.role,
        }
        data = edge.data or {}
        if "sign" in data:
            out["sign"] = data["sign"]
        if "coefficient" in data:
            coefficient = data["coefficient"]
            out["coefficient"] = (
                coefficient
                if isinstance(coefficient, (int, float))
                else parameter.get(str(coefficient), "param:?")
            )
        for key in ("offset", "modulo"):
            if data.get(key) not in (None, 0):
                out[key] = data[key]
        edges.append(out)
    return {"nodes": nodes, "edges": edges}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=CORPUS / "graphs")
    ap.add_argument("--patterns", type=Path, help="graph_patterns.json of the pattern search")
    args = ap.parse_args(argv)
    report = json.loads((CORPUS / "promotion.json").read_text(encoding="utf-8"))
    patterns = {}
    if args.patterns:
        patterns = json.loads(args.patterns.read_text(encoding="utf-8"))["models"]
    args.out.mkdir(parents=True, exist_ok=True)
    written = 0
    for paper in report["papers"]:
        if not paper["promoted"]:
            continue
        key = paper["paper_key"]
        text = (CORPUS / "formulations" / f"{paper['entry_id']}.json").read_text(encoding="utf-8")
        source = json.loads((CORPUS / "dossiers" / f"{key}.json").read_text(encoding="utf-8"))[
            "source"
        ]
        record = {
            "schema_version": SCHEMA_VERSION,
            "doi": source.get("doi"),
            "year": source.get("year"),
            "objective": (paper.get("objective") or {}).get("status"),
            "rows": {
                "candidate": paper["rows"],
                "canonical": paper["partial"]["rows_included"]
                if "partial" in paper
                else paper["rows"],
            },
            **abstract_graph(loads(text, source=key)),
        }
        if key in patterns:
            rows = Counter(p for row in patterns[key]["rows"] for p in row["patterns"])
            record["patterns"] = {
                "rows": dict(sorted(rows.items())),
                "models": sorted({m["pattern"] for m in patterns[key]["models"]}),
            }
        (args.out / f"{key}.json").write_text(
            json.dumps(record, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        written += 1
    print(f"wrote {written} abstracted graphs to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
