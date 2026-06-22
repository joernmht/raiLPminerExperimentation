"""One-off driver: run seed discovery across all frozen manifest queries,
dedup by DOI/OpenAlex id, rank by citations, and write a reviewable shortlist.

Run:  PYTHONPATH=. python3 -m corpusbuilder._discover <retrieved-iso-date>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from corpusbuilder import config
from corpusbuilder.elsevier import is_elsevier_doi
from corpusbuilder.openalex import OpenAlexClient

MIN_CITATIONS = 30
LIMIT = 50

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "corpus" / "manifest.json"
OUT_JSON = ROOT / "corpus" / "candidates.json"
OUT_MD = ROOT / "corpus" / "candidates.md"


def main() -> None:
    retrieved = sys.argv[1] if len(sys.argv) > 1 else "2026-06-21"
    config.load_env()
    queries = json.loads(MANIFEST.read_text())["queries"]
    client = OpenAlexClient()

    by_key: dict[str, dict] = {}
    for q in queries:
        for s in client.search_seeds(q, min_citations=MIN_CITATIONS, limit=LIMIT):
            key = (s.doi or s.openalex_id or s.title).lower()
            rec = by_key.get(key)
            if rec is None:
                rec = {
                    "title": s.title,
                    "doi": s.doi,
                    "arxiv_id": s.arxiv_id,
                    "openalex_id": s.openalex_id,
                    "year": s.year,
                    "venue": s.venue,
                    "publisher": s.publisher,
                    "cited_by_count": s.cited_by_count or 0,
                    "queries": [],
                    "tier1_arxiv": bool(s.arxiv_id),
                    "tier2_elsevier": is_elsevier_doi(s.doi),
                }
                by_key[key] = rec
            if q not in rec["queries"]:
                rec["queries"].append(q)

    ranked = sorted(by_key.values(), key=lambda r: r["cited_by_count"], reverse=True)
    payload = {
        "retrieved": retrieved,
        "min_citations": MIN_CITATIONS,
        "queries": queries,
        "n_candidates": len(ranked),
        "candidates": ranked,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    n_arxiv = sum(r["tier1_arxiv"] for r in ranked)
    n_els = sum(r["tier2_elsevier"] for r in ranked)
    lines = [
        f"# Corpus candidate shortlist ({retrieved})",
        "",
        f"- {len(ranked)} unique candidates across {len(queries)} queries "
        f"(>= {MIN_CITATIONS} citations)",
        f"- Tier-1 (arXiv e-print) available: **{n_arxiv}**",
        f"- Tier-2 (Elsevier full-text) candidates: **{n_els}**",
        f"- Neither (other publisher, PDF-only): **{len(ranked) - n_arxiv - n_els}**",
        "",
        "| # | Cites | Year | Path | DOI | Title |",
        "|--:|------:|-----:|:-----|:----|:------|",
    ]
    for i, r in enumerate(ranked, 1):
        path = "arXiv" if r["tier1_arxiv"] else ("Elsevier" if r["tier2_elsevier"] else "other")
        doi = r["doi"] or (f"arXiv:{r['arxiv_id']}" if r["arxiv_id"] else "—")
        title = r["title"][:80]
        lines.append(
            f"| {i} | {r['cited_by_count']} | {r['year'] or '—'} | {path} | {doi} | {title} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n")

    print(f"{len(ranked)} unique candidates -> {OUT_JSON.name}, {OUT_MD.name}")
    print(
        f"Tier-1 arXiv: {n_arxiv} | Tier-2 Elsevier: {n_els} | other: {len(ranked) - n_arxiv - n_els}"
    )


if __name__ == "__main__":
    main()
