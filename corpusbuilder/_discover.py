"""One-off driver: run seed discovery across all frozen manifest queries,
dedup by DOI/OpenAlex id, rank by citations, and write a reviewable shortlist.

Run:  PYTHONPATH=. python3 -m corpusbuilder._discover <retrieved-iso-date>
      PYTHONPATH=. python3 -m corpusbuilder._discover <date> --resume

**Recoverability.** A full sweep is dozens of OpenAlex calls over many minutes.
``_http`` retries the transient ones, but a query that still fails must not throw
away the queries that already succeeded. Every query's result is checkpointed to
``candidates.partial.json`` as it lands, and ``--resume`` re-runs only what is
missing.

**PRISMA integrity.** ``prisma.py`` derives ``database_queries`` and
``database_search_records`` straight from ``candidates.json``. A partial sweep
would therefore *understate* the paper's identification counts while looking
perfectly well-formed. So the corpus artifact ``candidates.json`` is written
**only when every query succeeded**; otherwise the run keeps its checkpoint,
reports the failures, and exits non-zero. Same rule as ``cli.cmd_dossier``: a
transient fault is never laundered into a corpus fact. See ADR-0006.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from corpusbuilder import config
from corpusbuilder._http import is_transient_exception
from corpusbuilder.elsevier import is_elsevier_doi
from corpusbuilder.openalex import OpenAlexClient

MIN_CITATIONS = 30
LIMIT = 50

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "corpus" / "manifest.json"
OUT_JSON = ROOT / "corpus" / "candidates.json"
OUT_MD = ROOT / "corpus" / "candidates.md"
#: Crash-safe scratch file; never consumed by prisma.py. Git-ignored.
CHECKPOINT = ROOT / "corpus" / "candidates.partial.json"

EXIT_INCOMPLETE = 2


def _record(s) -> dict:
    return {
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


def _load_checkpoint() -> dict[str, list[dict]]:
    """Map query -> its raw seed records, from a previous partial sweep."""
    if not CHECKPOINT.exists():
        return {}
    try:
        return json.loads(CHECKPOINT.read_text())["by_query"]
    except (json.JSONDecodeError, KeyError, OSError):
        return {}  # a corrupt checkpoint is worth nothing; start over


def _save_checkpoint(by_query: dict[str, list[dict]]) -> None:
    CHECKPOINT.write_text(json.dumps({"by_query": by_query}, indent=2, ensure_ascii=False))


def _key_of(rec: dict) -> str:
    return (rec["doi"] or rec["openalex_id"] or rec["title"]).lower()


def _merge(by_query: dict[str, list[dict]]) -> list[dict]:
    """Dedup seed records across queries by DOI/OpenAlex id/title; rank by citations.

    Order-independent by construction: queries are visited sorted, and the final
    rank breaks citation ties on the stable record key. A ``--resume`` run, which
    necessarily processes queries in a different order, therefore produces exactly
    the same ``candidates.json`` as an uninterrupted sweep.
    """
    by_key: dict[str, dict] = {}
    for q in sorted(by_query):
        for raw in by_query[q]:
            rec = by_key.setdefault(_key_of(raw), {**raw, "queries": []})
            if q not in rec["queries"]:
                rec["queries"].append(q)
    return sorted(by_key.values(), key=lambda r: (-r["cited_by_count"], _key_of(r)))


def _write_markdown(ranked: list[dict], queries: list[str], retrieved: str) -> None:
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="corpusbuilder._discover", description=__doc__)
    p.add_argument("retrieved", nargs="?", default="2026-06-21", help="ISO retrieval date")
    p.add_argument(
        "--resume", action="store_true", help="reuse candidates.partial.json; re-run only failures"
    )
    args = p.parse_args(argv)

    config.load_env()
    queries: list[str] = json.loads(MANIFEST.read_text())["queries"]
    client = OpenAlexClient()

    by_query = _load_checkpoint() if args.resume else {}
    if by_query:
        print(f"resuming: {len(by_query)}/{len(queries)} queries already checkpointed")

    failed: list[tuple[str, str]] = []
    for q in queries:
        if q in by_query:
            continue
        try:
            seeds = client.search_seeds(q, min_citations=MIN_CITATIONS, limit=LIMIT)
        except Exception as e:  # recorded below, never silently dropped
            kind = "transient" if is_transient_exception(e) else "permanent"
            print(f"WARNING: query {q!r} failed ({kind}): {e}", file=sys.stderr)
            failed.append((q, f"{kind}: {e}"))
            continue
        by_query[q] = [_record(s) for s in seeds]
        _save_checkpoint(by_query)  # crash-safe: each query survives the next failure
        print(f"  {len(seeds):>3} seeds for {q!r}")

    if failed:
        print(
            f"\n{len(failed)}/{len(queries)} queries failed; {len(by_query)} checkpointed to "
            f"{CHECKPOINT.name}.\nRefusing to write {OUT_JSON.name}: a partial sweep would "
            f"understate PRISMA's identification counts.\nRe-run with --resume once the "
            f"upstream recovers.",
            file=sys.stderr,
        )
        for q, why in failed:
            print(f"  - {q!r}: {why}", file=sys.stderr)
        return EXIT_INCOMPLETE

    ranked = _merge(by_query)
    payload = {
        "retrieved": args.retrieved,
        "min_citations": MIN_CITATIONS,
        "queries": queries,
        "n_candidates": len(ranked),
        "candidates": ranked,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False))
    _write_markdown(ranked, queries, args.retrieved)
    CHECKPOINT.unlink(missing_ok=True)  # the complete artifact supersedes it

    n_arxiv = sum(r["tier1_arxiv"] for r in ranked)
    n_els = sum(r["tier2_elsevier"] for r in ranked)
    print(f"{len(ranked)} unique candidates -> {OUT_JSON.name}, {OUT_MD.name}")
    print(
        f"Tier-1 arXiv: {n_arxiv} | Tier-2 Elsevier: {n_els} | other: {len(ranked) - n_arxiv - n_els}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
