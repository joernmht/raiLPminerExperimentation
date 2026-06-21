"""Citation **snowballing** over the current corpus (PRISMA "citation searching").

Backward snowballing = papers our seeds *reference*; forward = papers that *cite*
our seeds. Both edges are already stored in every dossier, so this driver just
aggregates them: it collects all neighbours, drops ones already in the corpus,
counts how many corpus papers each neighbour connects to (co-citation strength),
flags topical relevance by title, ranks, and writes a candidate list to screen.

Run:  PYTHONPATH=. python3 -m corpusbuilder.snowball
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from corpusbuilder.dossier import Dossier

ROOT = Path(__file__).resolve().parent.parent
DOSS = ROOT / "corpus" / "dossiers"
OUT_JSON = ROOT / "corpus" / "snowball_candidates.json"
OUT_MD = ROOT / "corpus" / "snowball_candidates.md"

# Light topical screen (railway scheduling / optimization). Used as a flag for
# the human screening step, NOT a hard filter — recall first, precision by hand.
_RELEVANT = re.compile(
    r"train|railway|rail\b|timetabl|reschedul|dispatch|metro|transit|"
    r"rolling stock|track|line plan|(mixed[- ]?integer|MILP|MIP|optimi)",
    re.IGNORECASE,
)
MIN_CITES = 20


def _key(doi: str | None, oa: str | None) -> str | None:
    if doi:
        return ("doi:" + doi.lower())
    if oa:
        return ("oa:" + oa.lower())
    return None


def main() -> None:
    doss = [Dossier.load(p) for p in sorted(DOSS.glob("*.json"))]
    known: set[str] = set()
    for d in doss:
        k = _key(d.source.doi, d.source.openalex_id)
        if k:
            known.add(k)

    agg: dict[str, dict] = {}
    for d in doss:
        seed = d.source.doi or d.source.openalex_id or d.source.title
        for direction, refs in (("backward", d.references), ("forward", d.cited_by)):
            for r in refs:
                k = _key(r.doi, r.openalex_id)
                if not k or k in known:
                    continue
                rec = agg.get(k)
                if rec is None:
                    rec = {
                        "key": k, "doi": r.doi, "openalex_id": r.openalex_id,
                        "title": r.title, "year": r.year,
                        "cited_by_count": r.cited_by_count or 0,
                        "seeds": set(), "backward": 0, "forward": 0,
                        "relevant": bool(r.title and _RELEVANT.search(r.title)),
                    }
                    agg[k] = rec
                rec["seeds"].add(seed)
                rec[direction] += 1

    cands = []
    for rec in agg.values():
        rec["seed_connections"] = len(rec.pop("seeds"))
        cands.append(rec)

    # Rank: most-connected to the corpus first, then most-cited.
    cands.sort(key=lambda r: (r["seed_connections"], r["cited_by_count"]), reverse=True)

    # Recommended-to-screen subset: connected to >=2 seeds, or topically relevant
    # and reasonably cited. Everything else stays in the list, just unflagged.
    for r in cands:
        r["recommended"] = (
            r["seed_connections"] >= 2
            or (r["relevant"] and r["cited_by_count"] >= MIN_CITES)
        )

    payload = {
        "n_seeds": len(doss),
        "n_candidates": len(cands),
        "n_recommended": sum(c["recommended"] for c in cands),
        "min_cites_for_relevant": MIN_CITES,
        "candidates": cands,
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False))

    rec = [c for c in cands if c["recommended"]][:60]
    lines = [
        "# Snowball candidates (citation searching over the corpus)",
        "",
        f"- {len(doss)} seed papers → {len(cands)} unique new neighbours "
        f"(not already in corpus)",
        f"- **{payload['n_recommended']} recommended to screen** "
        f"(≥2 seed links, or topical & ≥{MIN_CITES} cites)",
        "- direction: ← backward (we cite them) · → forward (they cite us)",
        "",
        "| Links | ←/→ | Cites | Year | Rel | DOI | Title |",
        "|------:|:----|------:|-----:|:---:|:----|:------|",
    ]
    for c in rec:
        doi = c["doi"] or (c["openalex_id"] or "—")
        rel = "✓" if c["relevant"] else ""
        title = (c["title"] or "—")[:70]
        lines.append(
            f"| {c['seed_connections']} | {c['backward']}/{c['forward']} | "
            f"{c['cited_by_count']} | {c['year'] or '—'} | {rel} | {doi} | {title} |"
        )
    OUT_MD.write_text("\n".join(lines) + "\n")

    print(f"{len(cands)} neighbours, {payload['n_recommended']} recommended -> "
          f"{OUT_MD.name} (top 60 shown), {OUT_JSON.name} (full)")


if __name__ == "__main__":
    main()
