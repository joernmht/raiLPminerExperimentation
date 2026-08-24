"""Re-run structural citations for verifier-demo outputs, offline.

The demo matches at run time against whatever ``--match-dir`` held; promotions
and repo conversions keep growing that set afterwards. This tool re-ingests
each run's ``final.tex`` (deterministic, no LLM) and matches it against the
CURRENT canonical universe: exact schema-graph isomorphism first, then graded
cosine similarity over Level-M feature documents.

Run::

    PYTHONPATH=. python3 -m railpminer.rematch [--runs corpus/vdemo3] \
        [--match corpus/formulations corpus/repo_formulations] [--top 3]
"""

from __future__ import annotations

# ruff: noqa: I001 — the railpminer._lp2graph import is a path-shim side effect
# and must precede the lp2graph imports (same posture as corpusbuilder.promote).

import argparse
import glob
import json
import math
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

from lp2graph import load
from lp2graph.mining.cluster.taxonomy import model_feature_document
from lp2graph.mining.corpusmgr.dedup import schema_graph_hash
from lp2graph.mining.ingest import ingest_latex
from lp2graph.mining.isomorphism.report import are_isomorphic


def _cosine(a: dict, b: dict) -> float:
    keys = set(a) | set(b)
    num = sum(a.get(k, 0) * b.get(k, 0) for k in keys)
    da = math.sqrt(sum(v * v for v in a.values()))
    db = math.sqrt(sum(v * v for v in b.values()))
    return num / (da * db) if da and db else 0.0


def load_match_set(dirs: list[str]) -> dict:
    """id -> Formulation for every loadable canonical JSON under ``dirs``."""
    out = {}
    for d in dirs:
        for p in sorted(glob.glob(str(Path(d) / "*.json"))):
            if p.endswith((".meta.json", "_report.json")):
                continue
            try:
                out[Path(p).stem] = load(p)
            except Exception:  # a non-Formulation JSON is simply not a match candidate
                continue
    return out


def rematch(runs_dir: str, match_dirs: list[str], top: int = 3) -> dict:
    match = load_match_set(match_dirs)
    feats = {k: model_feature_document(f) for k, f in match.items()}
    results = {}
    for d in sorted(glob.glob(str(Path(runs_dir) / "*--feedback"))):
        final = Path(d) / "final.tex"
        if not final.exists():
            continue
        res = ingest_latex(final.read_text(encoding="utf-8"), source=str(final))
        if not res.ok:
            continue
        g = res.formulation
        gf = model_feature_document(g)
        iso = sorted(k for k, f in match.items() if are_isomorphic(g, f))
        sims = sorted(((v, k) for k, v in ((k, _cosine(gf, mf)) for k, mf in feats.items())), reverse=True)
        results[Path(d).name.replace("--feedback", "")] = {
            "schema_hash": schema_graph_hash(g),
            "isomorphic": iso,
            "similar": [{"id": k, "similarity": round(s, 4)} for s, k in sims[:top]],
        }
    return {"match_set_size": len(match), "runs": results}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs", default="corpus/vdemo3")
    ap.add_argument(
        "--match", nargs="+", default=["corpus/formulations", "corpus/repo_formulations"]
    )
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--out", default=None, help="write JSON here (default: <runs>/rematch.json)")
    args = ap.parse_args(argv)
    report = rematch(args.runs, args.match, args.top)
    out = Path(args.out) if args.out else Path(args.runs) / "rematch.json"
    out.write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{report['match_set_size']} match candidates · {len(report['runs'])} runs rematched -> {out}")
    for key, r in report["runs"].items():
        head = ", ".join(f"{s['id']} {s['similarity']:.2f}" for s in r["similar"][:2])
        print(f"  {key}: iso={r['isomorphic'] or 'none'} · {head}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
