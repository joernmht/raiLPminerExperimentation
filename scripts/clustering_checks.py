"""Clustering-slide backing checks: M3 taxonomy on canonical models + k-means ARI.

Two facts a clustering slide needs beyond fingerprints and WL:
(1) the paper's own M3 operator (`lp2graph.mining.cluster.taxonomy.induce`)
    run on the REAL canonical models, and
(2) a k-means robustness cross-check against the frozen agglomerative
    fingerprint families (adjusted Rand index over several seeds).

Run:  PYTHONPATH=. python3 scripts/clustering_checks.py
Writes corpus/talkpack/clustering_checks.json.
"""

from __future__ import annotations

# ruff: noqa: I001 — railpminer._lp2graph is a path-shim side effect.

import glob
import json
import random
import statistics
from collections import Counter
from math import comb
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

from lp2graph import load
from lp2graph.mining.cluster.taxonomy import induce

ROOT = Path(__file__).resolve().parent.parent


def m3_taxonomy() -> dict:
    models = []
    for pat in ("corpus/formulations/*.json", "corpus/repo_formulations/*.json"):
        for p in sorted(glob.glob(str(ROOT / pat))):
            if p.endswith((".meta.json", "_report.json")):
                continue
            try:
                models.append(load(p))
            except Exception:
                continue
    tax = induce(models)
    part = tax.level_m.named_partition()
    clusters = []
    # named_partition() maps cluster label -> tuple of member entity keys
    for label, members in sorted(part.items(), key=lambda kv: (-len(kv[1]), str(kv[0]))):
        ids = sorted(str(m).split("::", 1)[0] for m in members)
        clusters.append({"label": str(label), "size": len(ids), "members": ids})
    return {"models": len(models), "m_clusters": len(clusters), "clusters": clusters}


def kmeans_ari(seeds: int = 5, k: int = 8) -> dict:
    feats = json.loads((ROOT / "corpus/fingerprint/features.json").read_text())
    papers = feats["papers"] if "papers" in feats else feats
    if isinstance(papers, list):
        papers = {p["paper_key"]: p["features"] for p in papers}
    frozen = {}
    for cl in json.loads((ROOT / "corpus/fingerprint/clusters.json").read_text())["clusters"]:
        for key in cl["papers"]:
            frozen[key] = cl["id"]
    keys = sorted(set(papers) & set(frozen))
    fnames = sorted({f for v in papers.values() for f in v})
    mu = {f: statistics.mean(papers[key].get(f, 0) for key in keys) for f in fnames}
    sd = {f: (statistics.pstdev([papers[key].get(f, 0) for key in keys]) or 1) for f in fnames}
    x = {key: [(papers[key].get(f, 0) - mu[f]) / sd[f] for f in fnames] for key in keys}

    def kmeans(seed: int) -> dict:
        rnd = random.Random(seed)
        cents = [x[key] for key in rnd.sample(keys, k)]
        assign: dict = {}
        for _ in range(100):
            changed = False
            for key in keys:
                d = [sum((a - b) ** 2 for a, b in zip(x[key], c, strict=True)) for c in cents]
                best = d.index(min(d))
                if assign.get(key) != best:
                    assign[key] = best
                    changed = True
            for j in range(k):
                mem = [x[key] for key in keys if assign[key] == j]
                if mem:
                    cents[j] = [sum(col) / len(mem) for col in zip(*mem, strict=True)]
            if not changed:
                break
        return assign

    def ari(a: dict, b: dict) -> float:
        ct = Counter((a[key], b[key]) for key in keys)
        ai = Counter(a[key] for key in keys)
        bi = Counter(b[key] for key in keys)
        idx = sum(comb(n, 2) for n in ct.values())
        ea = sum(comb(n, 2) for n in ai.values())
        eb = sum(comb(n, 2) for n in bi.values())
        exp = ea * eb / comb(len(keys), 2)
        return (idx - exp) / ((ea + eb) / 2 - exp)

    scores = [round(ari(kmeans(s), frozen), 4) for s in range(seeds)]
    return {"papers": len(keys), "k": k, "seeds": seeds, "ari": scores,
            "ari_mean": round(sum(scores) / len(scores), 4)}


def main() -> None:
    out = {"m3_taxonomy": m3_taxonomy(), "kmeans_vs_agglomerative": kmeans_ari()}
    path = ROOT / "corpus/talkpack/clustering_checks.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    t = out["m3_taxonomy"]
    multi = [c for c in t["clusters"] if c["size"] > 1]
    print(f"M3: {t['models']} models -> {t['m_clusters']} M-clusters, {len(multi)} multi-member:")
    for c in multi:
        print(f"   {c['label']}: {c['members']}")
    km = out["kmeans_vs_agglomerative"]
    print(f"k-means ARI vs frozen families: mean {km['ari_mean']}, per-seed {km['ari']}")


if __name__ == "__main__":
    main()
