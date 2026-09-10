"""Generate the **PRISMA flow artifact** — the single source of truth for the
corpus selection counts that the paper's PRISMA diagram and every ``n =`` cite.

Everything here is *derived deterministically* from the committed corpus
artifacts (``candidates.json``, ``snowball_candidates.json``, ``dossiers/*.json``,
and HITL ``decisions/*.json`` if present), so re-running reproduces the same
numbers. Two PRISMA 2020 identification arms are modelled: database searching
(the frozen keyword queries) and other methods (citation searching / snowballing).

Outputs (all under ``corpus/``):
  * ``prisma.json``        — structured boxes + provenance (source of truth)
  * ``prisma.md``          — human-readable flow with counts and exclusion reasons
  * ``prisma_macros.tex``  — ``\\newcommand`` per count, for ``\\input`` into the paper

Run:  PYTHONPATH=. python3 -m corpusbuilder.prisma
"""

from __future__ import annotations

import glob
import json
import re
from pathlib import Path

from corpusbuilder.elsevier import is_elsevier_doi

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
# Topical screen reused from snowball (same regex → consistent screening counts).
_RELEVANT = re.compile(
    r"train|railway|rail\b|timetabl|reschedul|dispatch|metro|transit|"
    r"rolling stock|track|line plan|(mixed[- ]?integer|MILP|MIP|optimi)",
    re.IGNORECASE,
)


def _load(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


# --------------------------------------------------------------------------- #
# HITL review tally
# --------------------------------------------------------------------------- #

# The review game's four terminal verdicts. ``duplicate`` (⧉) is a decision like
# any other — a formula ruled a re-print of one already in the corpus — and must
# not be lost from the flow just because it is not accept/correct/reject.
HITL_STATUSES = ("accepted", "corrected", "rejected", "duplicate")


def _iter_decisions(payload: dict):
    """Yield ``(paper_key, formula_id, status)`` from either export schema.

    Two producers write into ``corpus/decisions/``:

    * ``corpusbuilder.game`` (``schema_version: game-decisions-1``) — the review
      game, which exports **many** papers under a top-level ``formula_decisions``
      list, each entry holding its own ``decisions``.
    * ``corpusbuilder.review_view`` — the older single-paper view, whose export
      puts ``decisions`` at the top level.

    Reading only the top-level ``decisions`` key silently yields nothing for a
    game export, which is how every HITL count in the flow came out as zero.
    """
    groups = payload.get("formula_decisions")
    if groups is None:
        groups = [payload] if isinstance(payload.get("decisions"), list) else []
    for g in groups:
        if not isinstance(g, dict):
            continue
        paper = str(g.get("paper_key") or g.get("doi") or "")
        for d in g.get("decisions") or []:
            if isinstance(d, dict):
                yield paper, str(d.get("id", "")), str(d.get("status") or "unreviewed")


def hitl_tally(decision_files, formulas_total: int) -> dict[str, object]:
    """Tally HITL verdicts over the exported decision files.

    Deduplicated by ``(paper_key, formula_id)`` so that re-exports and
    multi-device exports do not double-count the same formula; files are read in
    sorted order and the last verdict for a formula wins, which keeps the tally
    deterministic. ``unreviewed`` is the *residual* against ``formulas_total`` —
    formulas in papers nobody has opened yet emit no decision record at all, so
    counting only what the files contain under-reports the work outstanding.
    Statuses outside :data:`HITL_STATUSES` are reported, never dropped.
    """
    latest: dict[tuple[str, str], str] = {}
    for path in sorted(str(p) for p in decision_files):
        for paper, fid, status in _iter_decisions(
            json.loads(Path(path).read_text(encoding="utf-8"))
        ):
            latest[(paper, fid)] = status

    counts: dict[str, int] = dict.fromkeys(HITL_STATUSES, 0)
    unrecognised: dict[str, int] = {}
    for status in latest.values():
        if status == "unreviewed":
            continue
        if status in counts:
            counts[status] += 1
        else:
            unrecognised[status] = unrecognised.get(status, 0) + 1

    decided = sum(counts.values()) + sum(unrecognised.values())
    tally: dict[str, object] = dict(counts)
    tally["unreviewed"] = max(0, formulas_total - decided)
    if unrecognised:
        tally["unrecognised_status"] = dict(sorted(unrecognised.items()))
    return tally


def main() -> None:
    cand = _load(CORPUS / "candidates.json", {"candidates": [], "queries": [], "n_candidates": 0})
    snow = _load(CORPUS / "snowball_candidates.json", {"n_candidates": 0, "n_recommended": 0})

    # --- Identification: database search arm ---
    db_predup = sum(len(c.get("queries", [])) for c in cand["candidates"])
    db_unique = cand["n_candidates"]
    db_dups = db_predup - db_unique
    db_seed_dois = {(c["doi"] or "").lower() for c in cand["candidates"] if c.get("doi")}

    # --- Identification: other methods (citation searching / snowballing) ---
    # Neighbours currently identified and recommended-to-screen for the next wave.
    cite_identified = snow.get("n_candidates", 0)
    cite_recommended = snow.get("n_recommended", 0)

    # --- Reports sought / retrieved: the dossiers actually built ---
    doss = [
        json.loads(Path(p).read_text(encoding="utf-8"))
        for p in sorted(glob.glob(str(CORPUS / "dossiers" / "*.json")))
    ]
    n_retrieved = len(doss)
    from_db = from_cite = unattributed = 0
    incl_papers = 0
    formulas_total = 0
    excl = {"not_entitled": 0, "no_machine_readable_formulas": 0, "awaiting_tier3_pdf": 0}
    off_topic = 0
    for d in doss:
        s = d.get("source") or {}
        doi = s.get("doi") or ""
        nf = len(d.get("formulas", []))
        formulas_total += nf
        # Arm attribution keys off the DOI. A dossier without one cannot be
        # matched against the database-arm seeds, so it is reported as
        # unattributed rather than silently swelling the citation arm.
        if not doi:
            unattributed += 1
        elif doi.lower() in db_seed_dois:
            from_db += 1
        else:
            from_cite += 1
        if s.get("title") and not _RELEVANT.search(s["title"]):
            off_topic += 1
        if nf > 0:
            incl_papers += 1
        else:
            if (s.get("entitlement") or "") == "metadata-only":
                excl["not_entitled"] += 1
            elif is_elsevier_doi(doi):
                excl["no_machine_readable_formulas"] += 1
            else:
                excl["awaiting_tier3_pdf"] += 1

    # --- HITL review state (decisions exported from the review game/view) ---
    dec_files = glob.glob(str(CORPUS / "decisions" / "*.json"))
    reviewed = hitl_tally(dec_files, formulas_total)

    flow = {
        "freeze_date": cand.get("retrieved")
        or _load(CORPUS / "manifest.json", {}).get("frozen_search_date"),
        "identification": {
            "database_search_records": db_predup,
            "database_queries": len(cand.get("queries", [])),
            "duplicates_removed": db_dups,
            "database_unique_records": db_unique,
            "citation_search_records_identified": cite_identified,
            "citation_search_recommended": cite_recommended,
        },
        "screening": {
            "records_screened": db_unique,
            "flagged_off_topic_in_corpus": off_topic,
        },
        "retrieval_eligibility": {
            "reports_retrieved": n_retrieved,
            "from_database_arm": from_db,
            "from_citation_arm": from_cite,
            "arm_unattributed": unattributed,
            "reports_excluded": excl,
            "reports_excluded_total": sum(excl.values()),
        },
        "included": {
            "source_papers": incl_papers,  # M
            "candidate_formulations": formulas_total,  # N (pre-HITL)
            "hitl_review": reviewed,
            "per_cell_P1_P5": None,  # TODO: requires domain/activity classification step
        },
    }
    payload = {"schema_version": "prisma-1", "derived_from": "corpus/*", "flow": flow}
    (CORPUS / "prisma.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n"
    )

    h = flow["included"]["hitl_review"]
    _unattr = f", {unattributed} without a DOI (arm unattributed)" if unattributed else ""
    _unrec = (
        " · **unrecognised statuses: "
        + ", ".join(f"{k} {v}" for k, v in h["unrecognised_status"].items())
        + "**"
        if "unrecognised_status" in h
        else ""
    )
    i, s_, r, inc = (
        flow["identification"],
        flow["screening"],
        flow["retrieval_eligibility"],
        flow["included"],
    )
    md = f"""# PRISMA flow — Paper 1 corpus (derived; do not hand-edit)

Regenerate with `PYTHONPATH=. python3 -m corpusbuilder.prisma`. Freeze date: {flow["freeze_date"]}.

## Identification
- Records identified — **database searching** ({i["database_queries"]} frozen queries): **{i["database_search_records"]}**
- Duplicate records removed: **{i["duplicates_removed"]}** → unique: **{i["database_unique_records"]}**
- Records identified — **other methods (citation searching / snowballing)**: **{i["citation_search_records_identified"]}** ({i["citation_search_recommended"]} recommended to screen)

## Screening
- Records screened: **{s_["records_screened"]}** (database arm)
- Flagged off-topic among retrieved dossiers (topical screen): **{s_["flagged_off_topic_in_corpus"]}**

## Retrieval & eligibility
- Reports retrieved (dossiers built): **{r["reports_retrieved"]}** — {r["from_database_arm"]} via database arm, {r["from_citation_arm"]} via citation searching{_unattr}
- Reports excluded at eligibility: **{r["reports_excluded_total"]}**
  - full text not entitled (metadata-only): {r["reports_excluded"]["not_entitled"]}
  - full text retrieved, no machine-readable formulas: {r["reports_excluded"]["no_machine_readable_formulas"]}
  - awaiting Tier-3 PDF extraction (non-Elsevier, citation-only so far): {r["reports_excluded"]["awaiting_tier3_pdf"]}

## Included
- Source papers with ≥1 recoverable formulation (**M**): **{inc["source_papers"]}**
- Candidate formulations extracted (**N**, pre-review): **{inc["candidate_formulations"]}**
- HITL review: accepted {h["accepted"]} · corrected {h["corrected"]} · duplicate {h["duplicate"]} · rejected {h["rejected"]} · unreviewed {h["unreviewed"]}{_unrec}
- Per-cell P1–P5 distribution: _pending domain/activity classification step_
"""
    (CORPUS / "prisma.md").write_text(md, encoding="utf-8", newline="\n")

    def cmd(name, val):
        return f"\\newcommand{{\\{name}}}{{{val}}}"

    tex = (
        "% PRISMA counts — auto-generated by corpusbuilder.prisma; do not edit.\n"
        + "\n".join(
            [
                cmd("prismaDBqueries", i["database_queries"]),
                cmd("prismaDBrecords", i["database_search_records"]),
                cmd("prismaDBdups", i["duplicates_removed"]),
                cmd("prismaDBunique", i["database_unique_records"]),
                cmd("prismaCiteIdentified", i["citation_search_records_identified"]),
                cmd("prismaCiteRecommended", i["citation_search_recommended"]),
                cmd("prismaReportsRetrieved", r["reports_retrieved"]),
                cmd("prismaReportsExcluded", r["reports_excluded_total"]),
                cmd("prismaExclNotEntitled", r["reports_excluded"]["not_entitled"]),
                cmd("prismaExclNoFormulas", r["reports_excluded"]["no_machine_readable_formulas"]),
                cmd("prismaExclTierThree", r["reports_excluded"]["awaiting_tier3_pdf"]),
                cmd("prismaInclPapers", inc["source_papers"]),
                cmd("prismaInclFormulations", inc["candidate_formulations"]),
                cmd("prismaHitlAccepted", h["accepted"]),
                cmd("prismaHitlCorrected", h["corrected"]),
                cmd("prismaHitlDuplicate", h["duplicate"]),
                cmd("prismaHitlRejected", h["rejected"]),
                cmd("prismaHitlUnreviewed", h["unreviewed"]),
            ]
        )
        + "\n"
    )
    (CORPUS / "prisma_macros.tex").write_text(tex, encoding="utf-8", newline="\n")

    print(
        f"PRISMA: db {i['database_search_records']}→{i['database_unique_records']} unique; "
        f"+{i['citation_search_records_identified']} citation-identified; "
        f"{r['reports_retrieved']} retrieved → {inc['source_papers']} papers / "
        f"{inc['candidate_formulations']} formulations included (pre-HITL). "
        f"wrote prisma.json, prisma.md, prisma_macros.tex"
    )


if __name__ == "__main__":
    main()
