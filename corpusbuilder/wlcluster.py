"""Structure-native similarity: WL subtree features on typed schema graphs.

The middle tier of the structural-similarity ladder. Exact schema-graph
isomorphism (M6) is identity or nothing — measured on this corpus it yields
only singletons — while paper-level fingerprints never look at the graph at
all. Weisfeiler-Lehman subtree features computed on *exactly the typed graphs
M6 matches* (node label ``cls|subtype``, edge label ``type|role``) give a
graded, embedding-space similarity: the PESP pair that exact isomorphism
rejects over one stale index scores 0.95 here, and the two formulations of
the same MDVSP paper score 0.75. Because the features are an explicit vector
embedding, any vector method (k-means included) applies downstream.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.wlcluster [--include-generated] \
        [--out corpus/wl]
"""

from __future__ import annotations

# ruff: noqa: I001 — railpminer._lp2graph is a path-shim side effect and must
# precede the lp2graph imports (same posture as corpusbuilder.promote).

import argparse
import glob
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

import networkx as nx
from lp2graph import load
from lp2graph.mining.ingest import ingest_latex
from lp2graph.mining.isomorphism.report import schema_nx

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"

WL_ITERATIONS = 3


def _ascii(s: object) -> str:
    return str(s).encode("ascii", "backslashreplace").decode()


def wl_features(formulation, iterations: int = WL_ITERATIONS) -> Counter:
    """WL subtree-hash counts over the M6 schema graph, per iteration depth.

    The multigraph is collapsed to a DiGraph whose edge label is the sorted
    join of the parallel edges' ``type|role`` labels (networkx's WL hasher
    refuses multigraphs); node labels are ``cls|subtype`` — exactly the
    attributes :func:`lp2graph.mining.isomorphism.report.are_isomorphic`
    matches on, so this is a graded relaxation of the same equivalence.
    """
    g = schema_nx(formulation)
    h = nx.DiGraph()
    for n, data in g.nodes(data=True):
        h.add_node(n, label=_ascii(f"{data.get('cls')}|{data.get('subtype')}"))
    merged: dict[tuple, list[str]] = defaultdict(list)
    for u, v, data in g.edges(data=True):
        merged[(u, v)].append(_ascii(f"{data.get('type')}|{data.get('role')}"))
    for (u, v), labels in merged.items():
        h.add_edge(u, v, label="+".join(sorted(labels)))
    hashes = nx.weisfeiler_lehman_subgraph_hashes(
        h, node_attr="label", edge_attr="label", iterations=iterations
    )
    counts: Counter = Counter()
    for per_iteration in hashes.values():
        for depth, digest in enumerate(per_iteration):
            counts[f"{depth}:{digest}"] += 1
    return counts


def cosine(a: Counter, b: Counter) -> float:
    keys = set(a) | set(b)
    num = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


def load_universe(include_generated: bool = False) -> dict:
    """Every loadable canonical model: seeds, promotions, repo conversions."""
    out = {}
    for pattern in ("corpus/formulations/*.json", "corpus/repo_formulations/*.json"):
        for p in sorted(glob.glob(str(ROOT / pattern))):
            if p.endswith((".meta.json", "_report.json")):
                continue
            try:
                out[Path(p).stem] = load(p)
            except Exception:  # a non-Formulation JSON is not a candidate
                continue
    if include_generated:
        for d in sorted(glob.glob(str(ROOT / "corpus/vdemo3*/*--feedback"))):
            final = Path(d) / "final.tex"
            if final.exists():
                res = ingest_latex(final.read_text(encoding="utf-8"), source=str(final))
                if res.ok:
                    key = "GEN:" + Path(d).name.split("--")[0].replace("10.1016_j.", "")
                    out[key] = res.formulation
    return out


def similarity_report(include_generated: bool = False) -> dict:
    models = load_universe(include_generated)
    feats = {k: wl_features(f) for k, f in sorted(models.items())}
    names = sorted(feats)
    matrix = {
        a: {b: round(cosine(feats[a], feats[b]), 4) for b in names} for a in names
    }
    pairs = sorted(
        ((matrix[a][b], a, b) for i, a in enumerate(names) for b in names[i + 1 :]),
        reverse=True,
    )
    return {
        "iterations": WL_ITERATIONS,
        "models": names,
        "matrix": matrix,
        "top_pairs": [
            {"a": a, "b": b, "similarity": s} for s, a, b in pairs[:15]
        ],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--include-generated", action="store_true")
    ap.add_argument("--out", type=Path, default=CORPUS / "wl")
    args = ap.parse_args(argv)
    report = similarity_report(args.include_generated)
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "similarity.json").write_text(
        json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    print(f"{len(report['models'])} models -> {args.out / 'similarity.json'}")
    for p in report["top_pairs"][:6]:
        print(f"  {p['similarity']:.3f}  {p['a']}  ~  {p['b']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
