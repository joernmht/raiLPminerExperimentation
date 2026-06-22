"""PRISMA flow + extraction-yield tally for the corpus build (Paper 1).

Corpus construction is framed as a **PRISMA flow** (identification -> screening
-> eligibility -> included). This module is the single source of truth for every
"n =" number in the paper's methods section: it reads the *frozen* corpus
artifacts on disk and recomputes the running tally deterministically, so the same
inputs always yield the same counts (CLAUDE.md, "PRISMA tally"). It also renders
a publication-quality figure that makes the pipeline's **yield** obvious — how a
broad identification sweep is distilled into a small set of validated, machine-
checked LP2Graph formulations, and how many formula records the deterministic-
first extraction ladder mines per retrieved full text.

Stages and their data sources (all under ``corpus/``):

* **Identification** — ``candidates.json`` (database search: OpenAlex queries,
  >= min-citations, de-duplicated across queries) and ``snowball_candidates.json``
  (citation searching over the seeds; neighbours recommended-to-screen vs. the
  rest).
* **Screening** — a transparent topical screen over the database records: records
  whose venue is off-domain (clinical/medical false positives surfaced by generic
  query terms such as "rescheduling"/"recovery"/"management") are excluded. The
  rule and the excluded ids are recorded in the tally for audit.
* **Eligibility / retrieval** — ``dossiers/*.json``: for each screened-in record a
  dossier records the full-text entitlement (``tud-subscription`` = entitled
  Elsevier XML, ``metadata-only`` = not entitled, ``None`` = no retrievable
  full text / no arXiv source) and the formulas mined by the deterministic-first
  ladder (arXiv LaTeX -> Elsevier MathML -> [future] OCR).
* **Included** — ``formulations/*.json`` (validated canonical LP2Graph models)
  with matching ``provenance/*.json`` and the ``instances/*.json`` carrying a
  published optimum for the external-fidelity check.

Honesty rule (CLAUDE.md): retrieval / extraction misses are *reported* here with
their reason, never silently dropped.

Run it::

    python -m corpusbuilder prisma            # write corpus/prisma.{json,md} + figure
    python -m corpusbuilder prisma --no-figure
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

# Off-domain venue keywords. The database queries ("... rescheduling",
# "... recovery", "... disruption management") surface clinical/medical false
# positives; this transparent venue rule screens them out. Kept explicit and
# recorded in the tally so the exclusion is auditable and deterministic.
OFFDOMAIN_VENUE_KEYWORDS = (
    "psychiatry",
    "diabetes",
    "nutrition",
    "anesthesia",
    "anaesthesia",
    "clinical practice",
    "medicine",
    "medical",
    "pediatric",
    "paediatric",
)


@dataclass(frozen=True)
class PrismaTally:
    """Deterministic, immutable snapshot of the PRISMA flow + extraction yield."""

    # Identification
    queries: tuple[str, ...]
    min_citations: int
    n_db_records: int
    n_snowball_total: int
    n_snowball_recommended: int
    n_snowball_excluded: int
    # Screening (topical, over the database records)
    n_screened: int
    n_offdomain_excluded: int
    offdomain_excluded: tuple[str, ...]
    n_topical: int
    # Eligibility / retrieval + extraction (over the screened-in records)
    n_fulltext_retrieved: int
    n_fulltext_metadata_only: int
    n_fulltext_unavailable: int
    n_extracted: int
    n_extraction_empty: int
    n_formulas_mined: int
    # Included
    n_included: int
    n_with_provenance: int
    n_instances: int

    @property
    def screen_precision(self) -> float:
        return self.n_topical / self.n_db_records if self.n_db_records else 0.0

    @property
    def retrieval_rate(self) -> float:
        return self.n_fulltext_retrieved / self.n_topical if self.n_topical else 0.0

    @property
    def extraction_success(self) -> float:
        return self.n_extracted / self.n_fulltext_retrieved if self.n_fulltext_retrieved else 0.0

    @property
    def formulas_per_fulltext(self) -> float:
        return self.n_formulas_mined / self.n_fulltext_retrieved if self.n_fulltext_retrieved else 0.0


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_offdomain(venue: str | None) -> bool:
    v = (venue or "").lower()
    return any(kw in v for kw in OFFDOMAIN_VENUE_KEYWORDS)


def build_tally(corpus_dir: Path) -> PrismaTally:
    """Recompute the PRISMA tally from the frozen corpus artifacts (deterministic)."""
    candidates = _load_json(corpus_dir / "candidates.json")
    snowball = _load_json(corpus_dir / "snowball_candidates.json")

    cand_list = candidates["candidates"]
    n_db = len(cand_list)

    # Screening: transparent off-domain (venue) topical screen.
    offdomain = sorted(
        c["doi"] for c in cand_list if _is_offdomain(c.get("venue"))
    )
    n_off = len(offdomain)
    n_topical = n_db - n_off

    # Eligibility / retrieval + extraction, read from the dossiers. A dossier is
    # built per screened-in (topical) record; medical false positives are skipped.
    dossier_dir = corpus_dir / "dossiers"
    retrieved = meta_only = unavailable = extracted = empty = formulas = 0
    for path in sorted(dossier_dir.glob("*.json")):
        d = _load_json(path)
        src = d.get("source", {})
        if _is_offdomain(src.get("venue")):
            continue  # excluded at screening; not part of eligibility assessment
        ent = src.get("entitlement")
        n_clean = sum(1 for f in (d.get("formulas") or []) if not f.get("note"))
        if ent in ("tud-subscription", "open-access"):
            retrieved += 1
            if n_clean > 0:
                extracted += 1
                formulas += n_clean
            else:
                empty += 1
        elif ent == "metadata-only":
            meta_only += 1
        else:  # None / unknown: no entitled full text and no arXiv source
            unavailable += 1

    # Included: validated canonical formulations + provenance + instances.
    formulations = sorted((corpus_dir / "formulations").glob("*.json"))
    provenance_ids = {p.stem for p in (corpus_dir / "provenance").glob("*.json")}
    n_included = len(formulations)
    n_prov = sum(1 for f in formulations if f.stem in provenance_ids)
    n_instances = len(list((corpus_dir / "instances").glob("*.json")))

    return PrismaTally(
        queries=tuple(candidates.get("queries", [])),
        min_citations=int(candidates.get("min_citations", 0)),
        n_db_records=n_db,
        n_snowball_total=int(snowball.get("n_candidates", 0)),
        n_snowball_recommended=int(snowball.get("n_recommended", 0)),
        n_snowball_excluded=int(snowball.get("n_candidates", 0)) - int(snowball.get("n_recommended", 0)),
        n_screened=n_db,
        n_offdomain_excluded=n_off,
        offdomain_excluded=tuple(offdomain),
        n_topical=n_topical,
        n_fulltext_retrieved=retrieved,
        n_fulltext_metadata_only=meta_only,
        n_fulltext_unavailable=unavailable,
        n_extracted=extracted,
        n_extraction_empty=empty,
        n_formulas_mined=formulas,
        n_included=n_included,
        n_with_provenance=n_prov,
        n_instances=n_instances,
    )


def tally_to_dict(t: PrismaTally) -> dict:
    """Serialise the tally as the versioned ``prisma.json`` source-of-truth record."""
    return {
        "schema_version": "prisma-1",
        "identification": {
            "database_search": {
                "source": "OpenAlex",
                "queries": list(t.queries),
                "min_citations": t.min_citations,
                "records": t.n_db_records,
            },
            "citation_searching": {
                "method": "snowball over seeds (backward + forward)",
                "neighbours_found": t.n_snowball_total,
                "recommended_to_screen": t.n_snowball_recommended,
                "excluded": t.n_snowball_excluded,
                "exclusion_reason": "fewer than 2 seed links and not (topical and >= min cites)",
            },
            "records_identified_total": t.n_db_records + t.n_snowball_total,
        },
        "screening": {
            "records_screened": t.n_screened,
            "excluded_offdomain": t.n_offdomain_excluded,
            "exclusion_reason": "off-domain venue (clinical/medical false positive)",
            "offdomain_venue_keywords": list(OFFDOMAIN_VENUE_KEYWORDS),
            "excluded_dois": list(t.offdomain_excluded),
            "records_topical": t.n_topical,
        },
        "eligibility": {
            "sought_for_retrieval": t.n_topical,
            "full_text_retrieved": t.n_fulltext_retrieved,
            "full_text_metadata_only": t.n_fulltext_metadata_only,
            "full_text_unavailable": t.n_fulltext_unavailable,
            "assessed_for_extraction": t.n_fulltext_retrieved,
            "extraction_succeeded": t.n_extracted,
            "extraction_empty": t.n_extraction_empty,
            "formulas_mined": t.n_formulas_mined,
        },
        "included": {
            "canonical_formulations": t.n_included,
            "with_provenance": t.n_with_provenance,
            "solvable_instances": t.n_instances,
        },
        "yield": {
            "topical_screen_precision": round(t.screen_precision, 4),
            "full_text_retrieval_rate": round(t.retrieval_rate, 4),
            "extraction_success_rate": round(t.extraction_success, 4),
            "formulas_per_full_text": round(t.formulas_per_fulltext, 2),
        },
    }


def tally_to_markdown(t: PrismaTally) -> str:
    """Human-readable PRISMA tally for the paper's methods section."""
    lines = [
        "# PRISMA tally — corpus construction (LP Mining with LP2Graph)",
        "",
        "Single source of truth for every `n =` in the paper. Regenerate with",
        "`python -m corpusbuilder prisma`. Deterministic: same frozen corpus ⇒ same tally.",
        "",
        "## Identification",
        "",
        f"- Database search (OpenAlex, {len(t.queries)} queries, ≥ {t.min_citations} citations, "
        f"de-duplicated): **{t.n_db_records}** records.",
        f"- Citation searching (snowball over the {t.n_db_records} seeds): "
        f"**{t.n_snowball_total}** unique neighbours; **{t.n_snowball_recommended}** recommended "
        f"to screen, {t.n_snowball_excluded} excluded (< 2 seed links and not topical & well-cited).",
        f"- **Total identified: {t.n_db_records + t.n_snowball_total}.**",
        "",
        "## Screening (topical, over the database records)",
        "",
        f"- Screened: **{t.n_screened}**.",
        f"- Excluded — off-domain venue (clinical/medical false positives): **{t.n_offdomain_excluded}**.",
        f"- Topical, carried to eligibility: **{t.n_topical}** "
        f"({t.screen_precision:.0%} precision).",
        "",
        "## Eligibility — full-text retrieval + deterministic extraction",
        "",
        f"- Sought for retrieval: **{t.n_topical}**.",
        f"- Full text retrieved (entitled): **{t.n_fulltext_retrieved}** "
        f"({t.retrieval_rate:.0%} of topical).",
        f"- Metadata only (not entitled): {t.n_fulltext_metadata_only}.",
        f"- Full text unavailable (no entitled XML / no arXiv source): {t.n_fulltext_unavailable}.",
        f"- Extraction succeeded: **{t.n_extracted}** of {t.n_fulltext_retrieved} "
        f"({t.extraction_success:.0%}); {t.n_extraction_empty} retrieved but no extractable LP.",
        f"- **Formula records mined: {t.n_formulas_mined}** "
        f"(≈ {t.formulas_per_fulltext:.0f} per retrieved full text).",
        "",
        "## Included",
        "",
        f"- Validated canonical LP2Graph formulations: **{t.n_included}** "
        f"({t.n_with_provenance} with provenance).",
        f"- Solvable instances with a published optimum (external-fidelity check): {t.n_instances}.",
        "",
        "## Yield (why the pipeline pays off)",
        "",
        f"- A {t.n_db_records + t.n_snowball_total}-record identification sweep is distilled to "
        f"**{t.n_included}** validated formulations.",
        f"- The deterministic-first ladder mines **{t.n_formulas_mined}** machine-checked formula "
        f"records from **{t.n_fulltext_retrieved}** full texts — **no OCR, no hand transcription** "
        f"— i.e. ≈ {t.formulas_per_fulltext:.0f} formulas per paper.",
        "",
    ]
    return "\n".join(lines)


def render_figure(t: PrismaTally, out_path: Path) -> Path:
    """Render the PRISMA flow + extraction-yield figure (matplotlib, paper quality)."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import FancyArrowPatch, FancyBboxPatch

    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 9})
    fig = plt.figure(figsize=(13.5, 7.6), dpi=200)
    gs = fig.add_gridspec(1, 2, width_ratios=[1.18, 1.0], wspace=0.12)
    ax = fig.add_subplot(gs[0, 0])
    axy = fig.add_subplot(gs[0, 1])

    fig.suptitle(
        "Corpus extraction pipeline — PRISMA flow and mining yield",
        fontsize=14, fontweight="bold", y=0.985,
    )

    # ---- Panel A: PRISMA flow diagram ------------------------------------
    ax.set_xlim(0, 10)
    ax.set_ylim(0, 10)
    ax.axis("off")
    ax.set_title("(a) PRISMA flow", fontsize=11, fontweight="bold", loc="left")

    STAGE = "#1f4e79"      # main stage boxes
    EXCL = "#9c2b2b"       # exclusion boxes
    MAIN_FILL = "#dbe7f3"
    EXCL_FILL = "#f3dede"
    INC_FILL = "#d7ead0"
    INC = "#2e6b2e"

    def box(x, y, w, h, text, edge, fill, fontweight="normal", fontsize=8.6):
        p = FancyBboxPatch(
            (x - w / 2, y - h / 2), w, h,
            boxstyle="round,pad=0.04,rounding_size=0.10",
            linewidth=1.4, edgecolor=edge, facecolor=fill, zorder=2,
        )
        ax.add_patch(p)
        ax.text(x, y, text, ha="center", va="center", fontsize=fontsize,
                fontweight=fontweight, color="#1a1a1a", zorder=3, wrap=True)
        return (x, y, w, h)

    def arrow(x0, y0, x1, y1):
        ax.add_patch(FancyArrowPatch(
            (x0, y0), (x1, y1), arrowstyle="-|>", mutation_scale=14,
            linewidth=1.4, color="#444", zorder=1,
        ))

    mx = 3.4  # main column x
    total_id = t.n_db_records + t.n_snowball_total

    b_id = box(mx, 9.1, 5.6, 1.25,
               f"IDENTIFICATION\nDatabase search (OpenAlex): n = {t.n_db_records}\n"
               f"Citation searching (snowball): n = {t.n_snowball_total}\n"
               f"Total identified: n = {total_id:,}",
               STAGE, MAIN_FILL, fontweight="bold")
    b_scr = box(mx, 6.95, 5.6, 1.0,
                f"SCREENING (topical)\nDatabase records screened: n = {t.n_screened}",
                STAGE, MAIN_FILL, fontweight="bold")
    b_elig = box(mx, 4.7, 5.6, 1.15,
                 f"ELIGIBILITY — full-text retrieval\n"
                 f"Sought: n = {t.n_topical}   ·   Retrieved: n = {t.n_fulltext_retrieved}\n"
                 f"Assessed for extraction: n = {t.n_fulltext_retrieved}",
                 STAGE, MAIN_FILL, fontweight="bold")
    b_inc = box(mx, 2.25, 5.6, 1.25,
                f"INCLUDED\nValidated canonical formulations: n = {t.n_included}\n"
                f"with provenance: n = {t.n_with_provenance}\n"
                f"Solvable instances: n = {t.n_instances}",
                INC, INC_FILL, fontweight="bold")

    arrow(mx, b_id[1] - 0.63, mx, b_scr[1] + 0.5)
    arrow(mx, b_scr[1] - 0.5, mx, b_elig[1] + 0.58)
    arrow(mx, b_elig[1] - 0.58, mx, b_inc[1] + 0.63)

    # Exclusion boxes on the right, with reasons.
    ex = 8.3
    box(ex, 8.35, 3.0, 0.95,
        f"Snowball not screened\nn = {t.n_snowball_excluded:,}\n(< 2 seed links / off-topic)",
        EXCL, EXCL_FILL, fontsize=7.8)
    arrow(b_id[0] + 2.8, 8.6, ex - 1.5, 8.5)

    box(ex, 6.7, 3.0, 0.8,
        f"Excluded at screening\nn = {t.n_offdomain_excluded}\n(off-domain venue)",
        EXCL, EXCL_FILL, fontsize=7.8)
    arrow(b_scr[0] + 2.8, 6.85, ex - 1.5, 6.8)

    n_not_retrieved = t.n_topical - t.n_fulltext_retrieved
    box(ex, 4.55, 3.0, 0.95,
        f"Not retrieved / no LP\nn = {n_not_retrieved + t.n_extraction_empty}\n"
        f"({t.n_fulltext_metadata_only} metadata-only,\n"
        f"{t.n_fulltext_unavailable} no full text, {t.n_extraction_empty} no LP)",
        EXCL, EXCL_FILL, fontsize=7.6)
    arrow(b_elig[0] + 2.8, 4.6, ex - 1.5, 4.6)

    # ---- Panel B: yield ---------------------------------------------------
    axy.set_title("(b) Mining yield", fontsize=11, fontweight="bold", loc="left")
    axy.axis("off")
    axy.set_xlim(0, 10)
    axy.set_ylim(0, 10)

    # Count cascade as a labelled funnel: a fixed left gutter holds the stage
    # name (so it never overflows), the colored bar encodes magnitude (log-scaled
    # so 3k → 10 stays legible), the count sits at the bar's end, and the inter-
    # stage yield % rides on the arrow between bars.
    import math

    stages = [
        ("Identified", total_id, "#1f4e79"),
        ("Screened (database)", t.n_screened, "#2c6ca0"),
        ("Topical", t.n_topical, "#3d8ec9"),
        ("Full text retrieved", t.n_fulltext_retrieved, "#4f9d5b"),
        ("Included formulations", t.n_included, "#2e6b2e"),
    ]
    yields = [  # ratio of this stage to the previous, shown on the connecting arrow
        None,
        None,                       # 43 of 2942 is the database arm, not a yield
        f"{t.screen_precision:.0%} topical",
        f"{t.retrieval_rate:.0%} retrieved",
        f"distilled to {t.n_included}",
    ]

    bar_x0 = 3.9      # bars start here; gutter to the left holds labels
    bar_max = 5.7     # widest bar (Identified)
    y0, dy = 9.3, 0.92

    def width(n):
        return 0.5 + bar_max * (math.log10(n + 1) / math.log10(total_id + 1))

    for i, (label, n, color) in enumerate(stages):
        y = y0 - i * dy
        w = width(n)
        axy.add_patch(FancyBboxPatch(
            (bar_x0, y - 0.32), w, 0.64,
            boxstyle="round,pad=0.02,rounding_size=0.05",
            linewidth=0, facecolor=color, zorder=2,
        ))
        axy.text(bar_x0 - 0.15, y, label, ha="right", va="center",
                 color="#1a1a1a", fontsize=8.4, fontweight="bold", zorder=3)
        axy.text(bar_x0 + w + 0.15, y, f"{n:,}", ha="left", va="center",
                 color=color, fontsize=9, fontweight="bold", zorder=3)
        if yields[i]:
            axy.text(bar_x0 + 0.35, y + dy / 2, yields[i], ha="left", va="center",
                     color="#666", fontsize=7.2, style="italic", zorder=3)

    # Headline yield call-out (the "benefit obvious" line).
    axy.add_patch(FancyBboxPatch(
        (0.4, 0.7), 9.2, 2.25,
        boxstyle="round,pad=0.06,rounding_size=0.12",
        linewidth=1.6, edgecolor="#2e6b2e", facecolor="#eef6ea", zorder=2,
    ))
    axy.text(
        5.0, 2.4,
        f"{t.n_formulas_mined} machine-checked formula records",
        ha="center", va="center", fontsize=15.5, fontweight="bold", color="#2e6b2e", zorder=3,
    )
    axy.text(
        5.0, 1.78,
        f"mined from {t.n_fulltext_retrieved} full texts   —   ≈ {t.formulas_per_fulltext:.0f} per paper "
        f"at {t.extraction_success:.0%} extraction success",
        ha="center", va="center", fontsize=8.8, color="#1a1a1a", zorder=3,
    )
    axy.text(
        5.0, 1.15,
        "deterministic-first ladder: arXiv LaTeX → Elsevier MathML\nno OCR, no hand transcription",
        ha="center", va="center", fontsize=8.0, color="#555", zorder=3,
    )

    fig.text(0.5, 0.012,
             "Regenerated from frozen corpus artifacts via  python -m corpusbuilder prisma  "
             "(deterministic).",
             ha="center", fontsize=7.5, color="#777")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def render_html(t: PrismaTally) -> str:
    """Render the PRISMA flow + yield as a self-contained, mobile-first HTML page.

    No external resources (works offline), responsive down to a phone screen, and
    deterministic — the markup is a pure function of the tally, so the same frozen
    corpus produces byte-identical HTML.
    """
    import math

    total_id = t.n_db_records + t.n_snowball_total

    def bar_pct(n: int) -> float:
        # Log-scaled so 2,942 → 10 all stay visible; min width keeps labels legible.
        return round(8 + 92 * (math.log10(n + 1) / math.log10(total_id + 1)), 2)

    cascade = [
        ("Identified", total_id, "var(--c1)", ""),
        ("Screened (database)", t.n_screened, "var(--c2)", ""),
        ("Topical", t.n_topical, "var(--c3)", f"{t.screen_precision:.0%} topical"),
        ("Full text retrieved", t.n_fulltext_retrieved, "var(--c4)", f"{t.retrieval_rate:.0%} retrieved"),
        ("Included formulations", t.n_included, "var(--c5)", f"distilled to {t.n_included}"),
    ]
    cascade_rows = "\n".join(
        f"""      <div class="bar-row">
        <div class="bar-label">{label}</div>
        <div class="bar-track"><div class="bar-fill" style="width:{bar_pct(n)}%;background:{color}">
          <span class="bar-count">{n:,}</span></div></div>
        <div class="bar-note">{note}</div>
      </div>"""
        for label, n, color, note in cascade
    )

    n_not_retrieved = (t.n_topical - t.n_fulltext_retrieved) + t.n_extraction_empty

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Corpus extraction pipeline — PRISMA flow &amp; yield</title>
<style>
  :root {{
    --bg:#f5f7fa; --card:#ffffff; --ink:#1a2433; --muted:#5b6b7f; --line:#e2e8f0;
    --stage:#1f4e79; --stage-bg:#e9f1fa; --excl:#9c2b2b; --excl-bg:#fbeded;
    --inc:#2e6b2e; --inc-bg:#eaf4e6;
    --c1:#1f4e79; --c2:#2c6ca0; --c3:#3d8ec9; --c4:#4f9d5b; --c5:#2e6b2e;
  }}
  * {{ box-sizing:border-box; }}
  html {{ -webkit-text-size-adjust:100%; }}
  body {{
    margin:0; background:var(--bg); color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    line-height:1.45; padding:env(safe-area-inset-top) 0 env(safe-area-inset-bottom);
  }}
  .wrap {{ max-width:760px; margin:0 auto; padding:20px 16px 40px; }}
  header h1 {{ font-size:clamp(1.25rem,4.5vw,1.7rem); margin:.2em 0 .15em; letter-spacing:-.01em; }}
  header p {{ color:var(--muted); margin:0 0 1.4em; font-size:clamp(.85rem,3vw,.95rem); }}
  h2 {{ font-size:clamp(1rem,3.5vw,1.15rem); margin:1.8em 0 .8em; display:flex; align-items:center; gap:.5em; }}
  h2 .tag {{ font-size:.7rem; font-weight:700; color:var(--muted); border:1px solid var(--line);
            border-radius:999px; padding:.15em .7em; letter-spacing:.04em; text-transform:uppercase; }}

  /* PRISMA flow — stacked cards with connectors */
  .flow {{ display:flex; flex-direction:column; align-items:stretch; gap:0; }}
  .stage {{ background:var(--card); border:1px solid var(--line); border-left:5px solid var(--stage);
           border-radius:14px; padding:14px 16px; box-shadow:0 1px 3px rgba(20,40,70,.06); }}
  .stage .kicker {{ font-size:.72rem; font-weight:700; text-transform:uppercase; letter-spacing:.06em;
                   color:var(--stage); margin-bottom:.35em; }}
  .stage.inc {{ border-left-color:var(--inc); }}
  .stage.inc .kicker {{ color:var(--inc); }}
  .stage ul {{ margin:.2em 0 0; padding-left:0; list-style:none; }}
  .stage li {{ display:flex; justify-content:space-between; gap:1em; padding:.18em 0;
              font-size:clamp(.85rem,3vw,.95rem); border-top:1px dashed var(--line); }}
  .stage li:first-child {{ border-top:0; }}
  .stage li b {{ font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .stage li.total {{ font-weight:700; }}
  .connector {{ display:flex; align-items:stretch; gap:10px; padding:2px 0 2px 18px; }}
  .connector .down {{ width:2px; background:var(--line); margin-left:1px; flex:0 0 auto; min-height:26px; }}
  .excl {{ flex:1; background:var(--excl-bg); border:1px solid #f0d4d4; border-radius:10px;
          padding:8px 12px; margin:6px 0; font-size:clamp(.78rem,2.7vw,.86rem); color:#6f2222; }}
  .excl b {{ color:var(--excl); font-variant-numeric:tabular-nums; }}

  /* Yield cascade */
  .yield {{ background:var(--card); border:1px solid var(--line); border-radius:14px;
           padding:16px; box-shadow:0 1px 3px rgba(20,40,70,.06); }}
  .bar-row {{ display:grid; grid-template-columns:1fr; gap:2px; margin:0 0 12px; }}
  .bar-label {{ font-size:clamp(.8rem,2.8vw,.9rem); font-weight:600; }}
  .bar-track {{ background:#eef1f5; border-radius:8px; overflow:hidden; }}
  .bar-fill {{ height:26px; border-radius:8px; display:flex; align-items:center; justify-content:flex-end;
              min-width:42px; transition:width .2s; }}
  .bar-count {{ color:#fff; font-weight:700; font-size:.85rem; padding:0 10px; font-variant-numeric:tabular-nums; }}
  .bar-note {{ font-size:.74rem; color:var(--muted); font-style:italic; }}

  /* Headline callout */
  .callout {{ background:var(--inc-bg); border:1.5px solid #bcdcb2; border-radius:16px;
             padding:20px 18px; text-align:center; margin-top:16px; }}
  .callout .big {{ font-size:clamp(1.5rem,7vw,2.3rem); font-weight:800; color:var(--inc);
                  line-height:1.1; font-variant-numeric:tabular-nums; }}
  .callout .sub {{ margin-top:.5em; font-size:clamp(.85rem,3vw,1rem); }}
  .callout .fine {{ margin-top:.4em; font-size:clamp(.74rem,2.6vw,.84rem); color:var(--muted); }}

  .metrics {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:14px; }}
  .metric {{ flex:1 1 100px; background:var(--card); border:1px solid var(--line); border-radius:12px;
            padding:12px; text-align:center; }}
  .metric .v {{ font-size:clamp(1.1rem,5vw,1.5rem); font-weight:800; color:var(--stage);
               font-variant-numeric:tabular-nums; }}
  .metric .k {{ font-size:.72rem; color:var(--muted); text-transform:uppercase; letter-spacing:.04em; margin-top:.2em; }}

  footer {{ margin-top:26px; font-size:.74rem; color:var(--muted); text-align:center; }}
  footer code {{ background:#eef1f5; padding:.1em .45em; border-radius:5px; font-size:.95em; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0f151d; --card:#172230; --ink:#e6edf5; --muted:#9fb0c3; --line:#26344a;
            --stage-bg:#15263a; --excl-bg:#2b1a1d; --inc-bg:#16271a; }}
    .bar-track {{ background:#22303f; }}
    footer code {{ background:#22303f; }}
  }}
</style>
</head>
<body>
<div class="wrap">
  <header>
    <h1>Corpus extraction pipeline</h1>
    <p>PRISMA flow &amp; mining yield — how a broad literature sweep is distilled into a small set of
    validated, machine-checked LP2Graph formulations.</p>
  </header>

  <h2><span class="tag">a</span> PRISMA flow</h2>
  <div class="flow">
    <div class="stage">
      <div class="kicker">Identification</div>
      <ul>
        <li><span>Database search (OpenAlex)</span><b>{t.n_db_records}</b></li>
        <li><span>Citation searching (snowball)</span><b>{t.n_snowball_total:,}</b></li>
        <li class="total"><span>Total identified</span><b>{total_id:,}</b></li>
      </ul>
    </div>
    <div class="connector"><div class="down"></div>
      <div class="excl">Snowball not screened: <b>{t.n_snowball_excluded:,}</b><br>
      &lt; 2 seed links / off-topic</div></div>
    <div class="stage">
      <div class="kicker">Screening · topical</div>
      <ul>
        <li><span>Database records screened</span><b>{t.n_screened}</b></li>
        <li class="total"><span>Topical (carried forward)</span><b>{t.n_topical}</b></li>
      </ul>
    </div>
    <div class="connector"><div class="down"></div>
      <div class="excl">Excluded at screening: <b>{t.n_offdomain_excluded}</b><br>
      off-domain venue (clinical / medical)</div></div>
    <div class="stage">
      <div class="kicker">Eligibility · full-text retrieval</div>
      <ul>
        <li><span>Sought for retrieval</span><b>{t.n_topical}</b></li>
        <li><span>Full text retrieved</span><b>{t.n_fulltext_retrieved}</b></li>
        <li class="total"><span>Assessed for extraction</span><b>{t.n_fulltext_retrieved}</b></li>
      </ul>
    </div>
    <div class="connector"><div class="down"></div>
      <div class="excl">Not retrieved / no LP: <b>{n_not_retrieved}</b><br>
      {t.n_fulltext_metadata_only} metadata-only · {t.n_fulltext_unavailable} no full text ·
      {t.n_extraction_empty} no extractable LP</div></div>
    <div class="stage inc">
      <div class="kicker">Included</div>
      <ul>
        <li><span>Validated canonical formulations</span><b>{t.n_included}</b></li>
        <li><span>With provenance</span><b>{t.n_with_provenance}</b></li>
        <li><span>Solvable instances</span><b>{t.n_instances}</b></li>
      </ul>
    </div>
  </div>

  <h2><span class="tag">b</span> Mining yield</h2>
  <div class="yield">
{cascade_rows}
  </div>

  <div class="callout">
    <div class="big">{t.n_formulas_mined} formula records</div>
    <div class="sub">machine-checked, mined from <b>{t.n_fulltext_retrieved}</b> full texts
    — ≈ <b>{t.formulas_per_fulltext:.0f} per paper</b></div>
    <div class="fine">deterministic-first ladder: arXiv LaTeX → Elsevier MathML · no OCR, no hand transcription</div>
  </div>

  <div class="metrics">
    <div class="metric"><div class="v">{t.screen_precision:.0%}</div><div class="k">Topical screen</div></div>
    <div class="metric"><div class="v">{t.retrieval_rate:.0%}</div><div class="k">Full-text retrieval</div></div>
    <div class="metric"><div class="v">{t.extraction_success:.0%}</div><div class="k">Extraction success</div></div>
  </div>

  <footer>
    Regenerated from frozen corpus artifacts via
    <code>python -m corpusbuilder prisma</code> — deterministic.
  </footer>
</div>
</body>
</html>
"""


def write_artifacts(corpus_dir: Path, *, figure: bool = True) -> dict[str, Path]:
    """Recompute and write ``prisma.json`` + ``prisma.md`` + ``prisma.html`` (+ figure)."""
    t = build_tally(corpus_dir)
    written: dict[str, Path] = {}

    json_path = corpus_dir / "prisma.json"
    json_path.write_text(
        json.dumps(tally_to_dict(t), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    written["json"] = json_path

    md_path = corpus_dir / "prisma.md"
    md_path.write_text(tally_to_markdown(t), encoding="utf-8")
    written["md"] = md_path

    html_path = corpus_dir / "prisma.html"
    html_path.write_text(render_html(t), encoding="utf-8")
    written["html"] = html_path

    if figure:
        written["figure"] = render_figure(t, corpus_dir / "prisma_flow.png")

    return written
