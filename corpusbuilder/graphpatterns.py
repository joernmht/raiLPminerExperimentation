"""Named structural patterns as typed subgraphs of the lp2graph schema graph.

The graph counterpart of :mod:`corpusbuilder.patterns` (which reads the
canonical model row by row and stays as the reference oracle, ADR-0025).
Everything here is read off the schema graph (``schema_nx``):

1. **Term graph.** Operator nodes are contracted into the edge they sit on,
   so every term is one edge ``constraint|objective -> variable|parameter``
   whose labels are the operator, the summed indices, the sign and the
   coefficient (a parameter coefficient also has its own ``uses_parameter``
   edge). Constants live on the constraint node (``rhs_literal``, lp2graph
   >= 1a5b246) or as constant edges to parameters.
2. **Normal form on the graph.** Each constraint node gets its comparator and
   the effective sign of every term edge after moving variables left and
   constants right (negated when every variable edge is negative).
3. **Row patterns are labelled stars**: a constraint node with all its term
   edges (the star is *closed*: no other term may hang off the centre).
4. **Model patterns are subgraphs** that join stars at a shared variable node
   or at the objective node; they are found by subgraph monomorphism
   (networkx VF2 on the multigraph) with typed node and edge matching.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.graphpatterns [--include-generated] \
        [--out corpus/patterns]
"""

from __future__ import annotations

# ruff: noqa: I001 — railpminer._lp2graph is a path-shim side effect and must
# precede the lp2graph imports (same posture as corpusbuilder.promote).

import argparse
import itertools
import json
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

import networkx as nx
from networkx.algorithms.isomorphism import MultiDiGraphMatcher

from lp2graph.mining.isomorphism.report import schema_nx

ROOT = Path(__file__).resolve().parent.parent
GRAPH_PATTERN_VERSION = "graphpatterns-2026.11.1"

INTEGRAL = {"binary", "integer"}
FRACTIONAL = {"continuous", "non_negative"}
FLIP = {"le": "ge", "ge": "le", "eq": "eq"}


# ---------------------------------------------------------------------------
# 1-2. Term graph with the normal form written onto it
# ---------------------------------------------------------------------------


def _side(position: str) -> int:
    return -1 if position.startswith("rhs") else 1


def _coef(coef: object, g: nx.MultiDiGraph) -> tuple[str, int, str | None]:
    """(class, sign of a numeric coefficient, parameter name)."""
    if coef is None:
        return "unit", 1, None
    try:
        n = float(coef)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        kind = g.nodes.get(f"param:{coef}", {}).get("subtype", "?")
        return f"param:{kind}", 1, str(coef)
    if n == 0:
        return "num", 1, None
    return ("unit" if abs(n) == 1 else "num"), (1 if n > 0 else -1), None


def term_graph(formulation) -> nx.MultiDiGraph:
    g = schema_nx(formulation)
    tg = nx.MultiDiGraph()
    for n, d in g.nodes(data=True):
        if d.get("cls") == "operator":
            continue
        attrs = {
            "cls": d.get("cls"),
            "subtype": d.get("subtype"),
            "shape": tuple(d.get("shape") or ()),
        }
        if d.get("cls") == "constraint":
            attrs["comparator"] = d["data"]["comparator"]
            if "rhs_literal" not in d["data"]:
                raise RuntimeError(
                    "lp2graph's schema view has no rhs_literal (needs lp2graph >= 1a5b246); "
                    "graph patterns cannot tell sum x = 1 from sum x = 0 without it"
                )
            attrs["rhs_literal"] = d["data"]["rhs_literal"]
            attrs["forall"] = tuple(q["over"] for q in d["data"].get("quantifiers", ()))
        tg.add_node(n, **attrs)

    def add_term(u, v, position, data, op=None, over=()):
        target = g.nodes[v].get("cls")
        cls, nsign, ref = _coef(data.get("coefficient"), g)
        side = _side(position)
        tg.add_edge(
            u,
            v,
            kind="term" if target == "variable" else "const",
            position=position,
            side=side,
            sign=int(data.get("sign", 1)),
            coef=cls,
            coef_ref=ref,
            eff=side * int(data.get("sign", 1)) * nsign,
            agg=op == "sum",
            op=op or "none",
            over=tuple(over),
        )

    for u, v, d in g.edges(data=True):
        t = d.get("type")
        if g.nodes[u].get("cls") == "operator":
            continue  # handled through its operator_input edge
        if t == "operator_input":
            op = g.nodes[v]
            for _, target, d2 in g.out_edges(v, data=True):
                add_term(
                    u,
                    target,
                    d.get("label", ""),
                    d2.get("data", {}),
                    op.get("subtype"),
                    op.get("shape") or (),
                )
        elif t in ("var_in_constraint", "var_in_objective"):
            add_term(u, v, d.get("label", ""), d.get("data", {}))
        else:
            tg.add_edge(
                u,
                v,
                kind="coef" if d.get("role") == "coef" else d.get("role") or t,
                position=d.get("label", ""),
            )

    for n, d in tg.nodes(data=True):
        if d["cls"] == "constraint":
            _normal_form(tg, n)
    return tg


def _normal_form(tg: nx.MultiDiGraph, c: str) -> None:
    terms = [e for e in tg.out_edges(c, keys=True, data=True) if e[3]["kind"] == "term"]
    consts = [e for e in tg.out_edges(c, keys=True, data=True) if e[3]["kind"] == "const"]
    node = tg.nodes[c]
    lit = node.get("rhs_literal")
    # constants move right: a constant edge on the lhs changes sign
    const_list = [(f"param:{tg.nodes[v]['subtype']}", -d["eff"], v) for _, v, _, d in consts]
    flip = bool(terms) and all(d["eff"] < 0 for *_, d in terms)
    cmp = node["comparator"]
    if flip:
        for *_, d in terms:
            d["eff"] = -d["eff"]
        const_list = [(k, -s, v) for k, s, v in const_list]
        lit = -lit if lit is not None else None
        cmp = FLIP[cmp]
    node["cmp"] = cmp
    node["flipped"] = flip
    node["lit"] = lit
    node["consts"] = const_list
    node["n_terms"] = len(terms)


# ---------------------------------------------------------------------------
# 3. Row patterns: closed labelled stars around a constraint node
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Star:
    center: dict
    leaves: list[tuple[dict, dict, str]]  # (edge attrs, leaf node attrs, leaf id)

    @property
    def rhs_one(self) -> bool:
        return not self.center["consts"] and self.center["lit"] == 1.0

    @property
    def rhs_zero(self) -> bool:
        return not self.center["consts"] and self.center["lit"] in (None, 0.0)

    @property
    def rhs_positive(self) -> bool:
        c, lit = self.center, self.center["lit"]
        if not c["consts"] and lit is None:
            return False
        if lit is not None and lit <= 0:
            return False
        return all(s > 0 and not k.endswith("big_m") for k, s, _ in c["consts"])

    def has_big_m(self, e: dict, leaf: dict) -> bool:
        """A binary whose coefficient is a big-M: declared big_m, or the
        M(1 - y) signature (a coefficient edge and a constant edge to the
        same parameter node)."""
        if leaf["subtype"] != "binary":
            return False
        if e["coef"] == "param:big_m":
            return True
        ref = e["coef_ref"]
        return ref is not None and any(v == f"param:{ref}" for _, _, v in self.center["consts"])


def _zero_one_sum(s: Star) -> bool:
    L = s.leaves
    if not L or not all(e["eff"] > 0 and e["coef"] in ("unit", "param:matrix") for e, _, _ in L):
        return False
    if not (len(L) >= 2 or any(e["agg"] for e, _, _ in L)):
        return False
    doms = {leaf["subtype"] for _, leaf, _ in L}
    return doms <= INTEGRAL or doms <= FRACTIONAL


def star_patterns(s: Star) -> list[str]:
    hits: list[str] = []
    L = s.leaves
    if not L:
        return hits
    cmp = s.center["cmp"]
    if s.rhs_one and _zero_one_sum(s):
        hits.append({"eq": "partition_row", "le": "packing_row", "ge": "covering_row"}[cmp])
    elif (
        all(
            e["eff"] > 0 and e["coef"] == "unit" and leaf["subtype"] in INTEGRAL for e, leaf, _ in L
        )
        and (len(L) >= 2 or any(e["agg"] for e, _, _ in L))
        and not s.rhs_one
        and s.rhs_positive
    ):
        hits.append("cardinality_row")
    if (
        cmp == "le"
        and all(e["eff"] > 0 and leaf["subtype"] in INTEGRAL for e, leaf, _ in L)
        and any(
            e["coef"] in ("num", "param:scalar", "param:vector", "param:matrix") for e, _, _ in L
        )
        and not (s.rhs_one and all(e["coef"] in ("unit", "param:matrix") for e, _, _ in L))
        and (len(L) >= 2 or any(e["agg"] for e, _, _ in L))
        and s.rhs_positive
    ):
        hits.append("knapsack_row")
    if (
        cmp == "eq"
        and len(L) == 2
        and L[0][2] == L[1][2]
        and all(e["agg"] and e["coef"] == "unit" for e, _, _ in L)
        and L[0][0]["eff"] != L[1][0]["eff"]
    ):
        hits.append("flow_conservation_row")
    ints = [
        (e, lf, v)
        for e, lf, v in L
        if lf["subtype"] == "integer" and e["coef"].startswith("param:")
    ]
    times = [
        (e, lf, v)
        for e, lf, v in L
        if lf["subtype"] in FRACTIONAL and e["coef"] == "unit" and not e["agg"]
    ]
    if (
        cmp in ("le", "ge")
        and len(L) == 3
        and len(ints) == 1
        and len(times) == 2
        and times[0][2] == times[1][2]
        and times[0][0]["eff"] != times[1][0]["eff"]
    ):
        hits.append("pesp_row")
    big = [s.has_big_m(e, lf) for e, lf, _ in L]
    if any(big) and not all(big):
        hits.append("big_m_row")
    if (
        cmp in ("le", "ge")
        and len(L) == 2
        and all(
            e["coef"] == "unit" and not e["agg"] and lf["subtype"] in FRACTIONAL for e, lf, _ in L
        )
        and L[0][0]["eff"] != L[1][0]["eff"]
        and "pesp_row" not in hits
    ):
        hits.append("difference_row")
    if (
        cmp in ("le", "ge")
        and len(L) == 2
        and not any(e["agg"] for e, _, _ in L)
        and L[0][0]["eff"] != L[1][0]["eff"]
        and s.rhs_zero
        and "difference_row" not in hits
        and any(lf["subtype"] == "binary" for _, lf, _ in L)
    ):
        hits.append("linking_row")
    if (
        len(L) == 1
        and not L[0][0]["agg"]
        and L[0][0]["coef"] == "unit"
        and (s.center["consts"] or s.center["lit"] is not None)
        and not any(k.endswith("big_m") for k, _, _ in s.center["consts"])
    ):
        hits.append("bound_row")
    return hits


def star(tg: nx.MultiDiGraph, c: str) -> Star:
    leaves = [
        (d, tg.nodes[v], v)
        for _, v, d in sorted(tg.out_edges(c, data=True), key=lambda e: e[2]["position"])
        if d["kind"] == "term"
    ]
    return Star(tg.nodes[c], leaves)


def label_stars(tg: nx.MultiDiGraph) -> None:
    for n, d in tg.nodes(data=True):
        if d["cls"] == "constraint":
            d["rows"] = frozenset(star_patterns(star(tg, n)))


# ---------------------------------------------------------------------------
# 4. Model patterns: subgraphs joining stars, matched by monomorphism
# ---------------------------------------------------------------------------

NodePred = Callable[[dict], bool]
EdgePred = Callable[[dict], bool]


@dataclass
class GraphPattern:
    name: str
    nodes: dict[str, NodePred]
    edges: list[tuple[str, str, EdgePred]]
    check: Callable[[dict[str, str], nx.MultiDiGraph], bool] = lambda m, g: True
    variable: str = "X"  # pattern node whose image names the hit
    variant: Callable[[dict[str, str], nx.MultiDiGraph], str] = lambda m, g: ""
    drawing: dict = field(default_factory=dict)

    def graph(self) -> nx.MultiDiGraph:
        p = nx.MultiDiGraph()
        for n, pred in self.nodes.items():
            p.add_node(n, pred=pred)
        for u, v, pred in self.edges:
            p.add_edge(u, v, pred=pred)
        return p


def _node_match(target: dict, pattern: dict) -> bool:
    return bool(pattern["pred"](target))


def _edge_match(target: dict, pattern: dict) -> bool:
    """Injective assignment of the pattern's parallel edges to the target's."""
    tlist = list(target.values())
    plist = [p["pred"] for p in pattern.values()]
    if len(plist) > len(tlist):
        return False
    return any(
        all(pred(t) for pred, t in zip(plist, perm, strict=False))
        for perm in itertools.permutations(tlist, len(plist))
    )


def constraint(*rows: str) -> NodePred:
    return lambda d: d.get("cls") == "constraint" and bool(d.get("rows", frozenset()) & set(rows))


def variable(domains: set[str]) -> NodePred:
    return lambda d: d.get("cls") == "variable" and d.get("subtype") in domains


def objective(sense: str) -> NodePred:
    return lambda d: d.get("cls") == "objective" and d.get("subtype") == sense


def term(
    *, eff: int | None = None, agg: bool | None = None, weighted: bool | None = None
) -> EdgePred:
    def pred(e: dict) -> bool:
        if e.get("kind") != "term":
            return False
        if eff is not None and e["eff"] != eff:
            return False
        if agg is not None and e["agg"] != agg:
            return False
        return weighted is None or (e["coef"] != "unit") == weighted

    return pred


def _assignment_variant(m: dict[str, str], g: nx.MultiDiGraph) -> str:
    """Classical when the two stars sum different shape indices of X; a degree
    pair when they sum the same set and range over the same set; else none."""
    x = m["X"]
    shape = set(g.nodes[x]["shape"])
    overs = []
    for c in (m["C1"], m["C2"]):
        o = {
            i
            for _, v, d in g.out_edges(c, data=True)
            if v == x and d["kind"] == "term"
            for i in d["over"]
        }
        overs.append(o)
    if overs[0] != overs[1] and len(shape) >= 2 and overs[0] | overs[1] <= shape:
        return "classical (rows sum different shape indices)"
    if overs[0] == overs[1] and g.nodes[m["C1"]]["forall"] == g.nodes[m["C2"]]["forall"]:
        return "degree pair (both rows sum the same index set)"
    return ""


def _assignment_check(m: dict[str, str], g: nx.MultiDiGraph) -> bool:
    return m["C1"] < m["C2"] and bool(_assignment_variant(m, g))  # each pair once


def _disjunctive_check(m: dict[str, str], g: nx.MultiDiGraph) -> bool:
    s = star(g, m["C"])
    return any(lf is g.nodes[m["Y"]] and s.has_big_m(e, lf) for e, lf, _ in s.leaves)


MODEL_GRAPH_PATTERNS: list[GraphPattern] = [
    GraphPattern(
        "knapsack_problem",
        {"O": objective("max"), "X": variable(INTEGRAL), "C": constraint("knapsack_row")},
        [("O", "X", term(eff=1, weighted=True)), ("C", "X", term(eff=1))],
    ),
    GraphPattern(
        "assignment_block",
        {
            "C1": constraint("partition_row"),
            "C2": constraint("partition_row"),
            "X": variable({"binary"}),
        },
        [("C1", "X", term(agg=True)), ("C2", "X", term(agg=True))],
        check=_assignment_check,
        variant=_assignment_variant,
    ),
    GraphPattern(
        "generalized_assignment",
        {
            "C1": constraint("partition_row"),
            "C2": constraint("knapsack_row", "cardinality_row"),
            "X": variable(INTEGRAL),
        },
        [("C1", "X", term(agg=True)), ("C2", "X", term(agg=True))],
        check=lambda m, g: (
            "knapsack_row" in g.nodes[m["C2"]]["rows"] or g.nodes[m["C2"]]["cmp"] == "le"
        ),
        variant=lambda m, g: (
            "weighted"
            if "knapsack_row" in g.nodes[m["C2"]]["rows"]
            else "unit weights (capacitated assignment)"
        ),
    ),
    GraphPattern(
        "set_covering_problem",
        {"O": objective("min"), "X": variable({"binary"}), "C": constraint("covering_row")},
        [("O", "X", term()), ("C", "X", term(agg=True))],
    ),
    GraphPattern(
        "set_covering_problem",
        {"O": objective("min"), "X": variable(INTEGRAL), "C": constraint("cardinality_row")},
        [("O", "X", term()), ("C", "X", term(agg=True))],
        check=lambda m, g: g.nodes[m["C"]]["cmp"] == "ge",
        variant=lambda m, g: (
            "multi-cover (right side is a demand)"
            + ("" if g.nodes[m["X"]]["subtype"] == "binary" else ", integer")
        ),
    ),
    GraphPattern(
        "set_partitioning_problem",
        {
            "O": objective("min"),
            "X": variable(INTEGRAL | FRACTIONAL),
            "C": constraint("partition_row"),
        },
        [("O", "X", term()), ("C", "X", term(agg=True))],
        variant=lambda m, g: (
            "LP relaxation (master)" if g.nodes[m["X"]]["subtype"] in FRACTIONAL else ""
        ),
    ),
    GraphPattern(
        "set_packing_problem",
        {
            "O": objective("max"),
            "X": variable(INTEGRAL | FRACTIONAL),
            "C": constraint("packing_row"),
        },
        [("O", "X", term()), ("C", "X", term(agg=True))],
    ),
    GraphPattern(
        "disjunctive_scheduling",
        {"C": constraint("big_m_row"), "T": variable(FRACTIONAL), "Y": variable({"binary"})},
        [
            # two parallel term edges to the same time variable, opposite signs
            ("C", "T", lambda e: e.get("kind") == "term" and e["eff"] == 1 and not e["agg"]),
            ("C", "T", lambda e: e.get("kind") == "term" and e["eff"] == -1 and not e["agg"]),
            ("C", "Y", term()),
        ],
        check=_disjunctive_check,
        variable="Y",
    ),
    GraphPattern(
        "pesp",
        {"C": constraint("pesp_row"), "T": variable(FRACTIONAL), "K": variable({"integer"})},
        [("C", "T", term(eff=1)), ("C", "T", term(eff=-1)), ("C", "K", term(weighted=True))],
        variable="K,T",
    ),
]


def match_models(tg: nx.MultiDiGraph) -> list[dict]:
    """All model-pattern hits, merged per (pattern, variable) like the oracle."""
    merged: dict[tuple[str, str], dict] = {}
    for gp in MODEL_GRAPH_PATTERNS:
        p = gp.graph()
        gm = MultiDiGraphMatcher(tg, p, node_match=_node_match, edge_match=_edge_match)
        for iso in gm.subgraph_monomorphisms_iter():
            m = {pn: tn for tn, pn in iso.items()}
            if not gp.check(m, tg):
                continue
            var_name = ",".join(sorted(m[v].split(":", 1)[1] for v in gp.variable.split(",")))
            key = (gp.name, var_name)
            rows = {tn.split(":", 1)[1] for pn, tn in m.items() if tn.startswith("constraint:")}
            hit = merged.setdefault(
                key,
                {"pattern": gp.name, "variable": var_name, "variants": set(), "rows": set()},
            )
            hit["rows"] |= rows
            v = gp.variant(m, tg)
            if v:
                hit["variants"].add(v)
    # disjunctive: report the pair row y_ij + y_ji = 1 when present
    for (name, var), hit in merged.items():
        if name == "disjunctive_scheduling":
            pair = [
                n.split(":", 1)[1]
                for n, d in tg.nodes(data=True)
                if d["cls"] == "constraint"
                and "partition_row" in d.get("rows", ())
                and {v for _, v, e in tg.out_edges(n, data=True) if e["kind"] == "term"}
                == {f"var:{var}"}
                and d["n_terms"] == 2
            ]
            hit["rows"] |= set(pair)
            hit["variants"].add("with pair row" if pair else "ordering rows only")
    out = []
    for (name, _), hit in sorted(merged.items()):
        out.append(
            {
                "pattern": name,
                "variable": hit["variable"],
                "variant": "; ".join(sorted(hit["variants"])),
                "rows": sorted(hit["rows"]),
            }
        )
    return out


def detect(formulation) -> dict:
    tg = term_graph(formulation)
    label_stars(tg)
    rows = [
        {"constraint": n.split(":", 1)[1], "patterns": sorted(d["rows"]), "comparator": d["cmp"]}
        for n, d in tg.nodes(data=True)
        if d["cls"] == "constraint"
    ]
    return {"rows": rows, "models": match_models(tg)}


# ---------------------------------------------------------------------------
# Agreement with the row oracle (corpusbuilder.patterns)
# ---------------------------------------------------------------------------


def compare(models: dict) -> dict:
    from corpusbuilder import patterns as oracle

    out = {"models": 0, "rows": 0, "row_disagreements": [], "model_disagreements": []}
    for key, f in sorted(models.items()):
        g = detect(f)
        o = oracle.detect(f)
        out["models"] += 1
        orows = {r["constraint"]: set(r["patterns"]) for r in o["rows"]}
        for r in g["rows"]:
            out["rows"] += 1
            if set(r["patterns"]) != orows.get(r["constraint"], set()):
                out["row_disagreements"].append(
                    {
                        "model": key,
                        "constraint": r["constraint"],
                        "graph": r["patterns"],
                        "oracle": sorted(orows.get(r["constraint"], ())),
                    }
                )
        gm = {(m["pattern"], m["variable"]) for m in g["models"]}
        om = {(m["pattern"], m["variable"]) for m in o["models"]}
        if gm != om:
            out["model_disagreements"].append(
                {"model": key, "graph_only": sorted(gm - om), "oracle_only": sorted(om - gm)}
            )
    return out


def report(models: dict) -> dict:
    """Same shape as :func:`corpusbuilder.patterns.report`, from the graph."""
    from corpusbuilder.patterns import MODEL_PATTERNS, ROW_PATTERNS

    out = {k: detect(f) for k, f in sorted(models.items())}
    rows = [r for d in out.values() for r in d["rows"]]
    return {
        "version": GRAPH_PATTERN_VERSION,
        "n_models": len(out),
        "n_rows": len(rows),
        "unmatched_rows": sum(1 for r in rows if not r["patterns"]),
        "row_pattern_counts": {p: sum(p in r["patterns"] for r in rows) for p in ROW_PATTERNS},
        "model_pattern_counts": {
            p: sum(any(m["pattern"] == p for m in d["models"]) for d in out.values())
            for p in MODEL_PATTERNS
        },
        "models": out,
    }


def paper_macros(models: dict) -> str:
    """The paper's pattern macros: graph detection on the canonical models,
    plus its agreement with the row oracle."""
    from corpusbuilder.patterns import macros

    rep = report(models)
    agr = compare(models)
    text = macros(rep).replace("corpusbuilder.patterns", "corpusbuilder.graphpatterns")
    return text + (
        f"\\newcommand{{\\patOracleRowDisagree}}{{{len(agr['row_disagreements'])}}}\n"
        f"\\newcommand{{\\patOracleModelDisagree}}{{{len(agr['model_disagreements'])}}}\n"
        f"\\newcommand{{\\patModelGraphPatterns}}{{{len(MODEL_GRAPH_PATTERNS)}}}\n"
    )


def main(argv: list[str] | None = None) -> int:
    from corpusbuilder.wlcluster import load_universe

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--include-generated", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "corpus" / "patterns"))
    args = ap.parse_args(argv)
    models = load_universe(args.include_generated)
    rep = {
        "version": GRAPH_PATTERN_VERSION,
        "models": {k: detect(f) for k, f in sorted(models.items())},
        "agreement_with_row_oracle": compare(models),
    }
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "graph_patterns.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rep, fh, indent=1, default=sorted)
        fh.write("\n")
    canonical = models if not args.include_generated else load_universe(False)
    with open(out / "patterns_macros.tex", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(paper_macros(canonical))
    agr = rep["agreement_with_row_oracle"]
    counts: dict[str, int] = defaultdict(int)
    for d in rep["models"].values():
        for p in {m["pattern"] for m in d["models"]}:
            counts[p] += 1
    print(
        f"{agr['models']} models, {agr['rows']} rows; row disagreements "
        f"{len(agr['row_disagreements'])}, model disagreements {len(agr['model_disagreements'])}; "
        f"models: {dict(counts)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
