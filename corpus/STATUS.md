# Paper 1 corpus — status & next steps

_Last updated: 2026-06-21. Higher-scope handoff. For terse per-commit facts see the
paper1 session `CLAUDE.md` memory log; for ground truth see `~/CLAUDE.md`._

## Where we are

Corpus construction is **started and producing real data** (it was unstarted before
2026-06-21). The pipeline runs end-to-end and is reproducible from committed artifacts.

PRISMA snapshot (regenerate: `PYTHONPATH=. python3 -m corpusbuilder.prisma`; full flow in
`corpus/prisma.md`):

| Stage | Count |
|---|---|
| Database search (6 frozen queries) → unique | 45 → **43** |
| Citation searching (snowball) identified | **10,056** (4,235 recommended) |
| Reports retrieved (dossiers) | **286** (43 database + 243 snowball wave-1) |
| Excluded at eligibility | 48 (10 not entitled · 15 no machine-readable formulas · 23 awaiting Tier-3) |
| **Included** | **238 papers / 8,957 candidate formulations** (pre-review) |

All formulas so far are **Tier-2 (Elsevier MathML→LaTeX via the TUD SSH tunnel)** or
Tier-1 (none yet). 28 retrieved dossiers are flagged off-topic by the topical screen
(medical-noise leakage) and should be pruned in review.

### What exists (tooling, all in `corpusbuilder/`)
- `_discover` — keyword search → ranked candidates (`candidates.{json,md}`).
- `dossier` (CLI) — per-paper: OpenAlex refs+cited-by, Tier-2 Elsevier formulas, Scopus cross-check.
- `snowball` — backward+forward citation searching → `snowball_candidates.{json,md}`.
- `review_view` — static HITL review site `corpus/review/index.html` (MathJax + raw LaTeX,
  accept/correct/reject → localStorage → `decisions_<paper>.json` export). Regenerate after corpus changes.
- `prisma` — deterministic PRISMA flow → `prisma.{json,md}` + `prisma_macros.tex` (wired into the paper, Overleaf `0ef1ccd`).

### Key dependency / gotcha
Tier-2 needs the **ephemeral** TUD SOCKS tunnel: `ssh -D 8080 -N -f jrma562g@login1.zih.tu-dresden.de`
(password, no key auth from box) then `ELSEVIER_PROXY=socks5h://127.0.0.1:8080`. Dies with the session.
Permanent fix = institutional `ELSEVIER_INSTTOKEN` from the TUD library.

## What should happen next (in priority order)

1. **MANUAL REVIEW (human-in-the-loop) — the immediate critical-path step.**
   The 8,957 formulas are raw extractions, *not yet validated*. Open `corpus/review/index.html`,
   accept/correct/reject per formula, export the decisions JSONs into `corpus/decisions/`.
   Then build the ingest step: decisions → promote accepted/corrected formulas to canonical
   lp2graph `Formulation`s in `corpus/formulations/` + `ProvenanceRecord`s in `corpus/provenance/`
   (this finally replaces the 10 placeholder seed templates). Re-run `prisma` so the HITL counts populate.
   _Prune the 28 off-topic dossiers here._

2. **Expert-cluster baseline from surveys (the "compare our clusters vs the reviews" idea).**
   The corpus already contains review/survey papers whose author-proposed taxonomies are the
   anchor the methodology promises (sanity-check, not ground truth). See `corpus/SURVEYS.md`.
   Build an extraction of each survey's classification scheme → an expert-cluster baseline to
   compare the induced clusters against. NOTE: this extracts a *taxonomy/classification*, not
   formulas — a different extraction task than the current pipeline.

3. **Domain/activity classification → per-cell P1–P5 distribution.** Needed to fill the PRISMA
   per-cell numbers and the abstract's $K/V/T$, and to drive shell-priority reporting.

4. **Snowball wave-2.** Pool is ready (4,235 recommended). Same one-command background run with a
   live tunnel. Watch the central survey neighbours flagged in `SURVEYS.md` (esp. Cordeau 1998).

5. **Tier-3 PDF OCR** (MinerU / PaddleOCR-VL) for the 23+ non-Elsevier / IEEE / Springer papers
   currently citation-only — not yet built. Gated behind human line-by-line review.

6. **Then the method proper**: extraction/homologization into LP2Graph → feature vectors →
   multi-level clustering & naming → two-stage labeling → fidelity validation (round-trip + cross-solver).
