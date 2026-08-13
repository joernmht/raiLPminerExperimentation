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
- `game` — **Formula Express**, the mobile gamified HITL front-end `corpus/review/game.html`
  (single self-contained file in the **CRO CD** via the `tud-mobile` skill; send to the phone
  via Telegram). Primary mode **Paper Run**: one paper at a time with a deterministic
  **symbol graph** (formulas ↔ shared symbols; `extract_symbols` in this module — a review
  aid, NOT canonical lp2graph), per-row ✓/✎/✗ + bulk accept-rest/reject-all, formula
  mini-graphs (relation+operators+symbols), and a P1–P5 cell prompt on finish (fills PRISMA
  `per_cell_P1_P5`, prunes off-topic). Plus Blitz (60 s sprint) and Shell Sorter. XP/ranks,
  daily streaks + heatmap, badges; progress in localStorage (key `fx:state:v1`, stable across
  regenerations); Export/Import → `game_decisions_<date>.json` (`formula_decisions` = the
  `review_view` per-paper format, plus `paper_cells`). Regenerate after corpus changes:
  `PYTHONPATH=. python3 -m corpusbuilder.game`.
- `prisma` — deterministic PRISMA flow → `prisma.{json,md}` + `prisma_macros.tex` (wired into the paper, Overleaf `0ef1ccd`).

### Key dependency / gotcha
Tier-2 needs the **ephemeral** TUD SOCKS tunnel: `ssh -D 8080 -N -f jrma562g@login1.zih.tu-dresden.de`
(password, no key auth from box) then `ELSEVIER_PROXY=socks5h://127.0.0.1:8080`. Dies with the session.
Permanent fix = institutional `ELSEVIER_INSTTOKEN` from the TUD library.

## What should happen next (in priority order)

1. **MANUAL REVIEW (human-in-the-loop) — the immediate critical-path step.**
   The 8,957 formulas are raw extractions, *not yet validated*. Open `corpus/review/index.html`,
   accept/correct/reject per formula, export the decisions JSONs into `corpus/decisions/`.
   The ingest step now **exists**: `corpusbuilder.promote` (2026-08-13) reads
   `corpus/decisions/*.json` in both export schemas and writes canonical `Formulation`s to
   `corpus/formulations/` + `ProvenanceRecord`s to `corpus/provenance/`, with every failure
   categorized by cause in `corpus/promotion.{json,md}`. Run it after every review session:
   `PYTHONPATH=. python3 -m corpusbuilder.promote` (add `--dry-run` to look first).
   Re-run `prisma` afterwards so the HITL counts populate. _Prune the 28 off-topic dossiers here._

   **Declarations are the real remaining work, and they are per paper, not per formula.**
   Canonical LaTeX needs a `%@` symbol table (index families, parameter shape/kind, variable
   domain/role) that a displayed equation simply does not carry — measured: 0 of 10,156
   extracted units parse without one. So each promoted paper needs a sidecar
   `corpus/declarations/<paper_key>.tex`; promote writes a fill-in-the-blank
   `<paper_key>.stub.tex` (symbols and constraint-row names pre-filled) for every paper that
   lacks one, and the `missing_declarations` count in the report *is* the outstanding worklist.
   See `docs/adr/0010-promotion-needs-a-declaration-sidecar.md`.

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

## Instance coverage & seed-formulation defects (2026-08-12)

Every one of the 10 seed formulations now has at least one instance in `corpus/instances/`
(was 5), and all 10 validate across **CBC, HiGHS and Gurobi** with `matches_expected` /
`matches_status` true and cross-solver agreement. Two lp2graph grounder gaps that blocked
this were closed upstream (`abs` terms are epigraph-lifted where that is exact;
`solve.solve_lexicographic` stages a lexicographic objective level by level).

Instances are **planted**, per the S2 decision: pick the optimal solution first, then choose
parameters that make it optimal, so `expected_optimum` is derived by argument and recorded in
`optimum_source` rather than copied from whatever a solver happened to return.

Writing them exposed four defects in the seed templates. **None is a data problem; all four are
structural, and all four are corpus-content calls** (changing a formulation shifts the taxonomy
and clustering artifacts, so they are deliberately left for a human decision).

| # | Formulation | Defect | Consequence | Proposed minimal fix |
|---|---|---|---|---|
| 1 | `lp_1_5_soft_regularity` | No anchor: `t` is non-negative with no upper bound and nothing pins any element | Optimum is **0 for every instance**. A perfectly regular schedule always exists, so no parameter choice discriminates | Anchor the first departure (`t_0 = earliest`) or bound `t` above; either makes the penalty weight `w` bite |
| 2 | `mip_2_8_pesp` | Wrap counter `k` is `integer` with **no lower bound**, and the objective minimizes `sum k` | **Unbounded.** No optimum exists to publish | Add `lower: 0` to `k`. That is exactly what the sibling `pesp_solvable` already does, so the two would then be duplicates: prefer deleting one, or keep `mip_2_8_pesp` as an intentional negative fixture |
| 3 | `objective_abs_deviation` | Declares parameter `target`, never references it; also has **no constraints** | Model is `min sum_i abs(t_i)`, not the `min sum_i abs(t_i - target_i)` its own description promises; optimum is 0 for any data. lp2graph's own validator flags the unused symbol | Needs an `abs` over a two-term difference, which the **flat `Term` schema cannot express** (a term is one `ref` plus a coefficient). Either extend the schema with nested terms, or restate the model with explicit deviation variables the way `lp_1_5_soft_regularity` does |
| 4 | `objective_lex_priority` | No constraints and no parameters | The two priority levels never compete, so lexicographic ordering is untested for any instance | Add one coupling constraint (e.g. `c_i + t_i >= 1`) so buying a lower level 1 costs level 2 |

Also found: the lab's `corpus/formulations/mip_2_8_pesp.json` is **stale** relative to
`~/lp2graph/formulations/constraints/mip_2_8_pesp.json`, which dropped the unused index `T` on
2026-07-13 (it produced a loose node; the schema view went 11n/18e to 10n/20e). The two copies
should be reconciled, again a content call because it moves the graph metrics.

A fifth finding is about solvers, not the corpus, and is now encoded in the harness: on an
unbounded MILP the three solvers **disagree on the word**. CBC reports `unbounded`; HiGHS and
Gurobi both report `infeasible`, because MILP presolve routinely cannot separate the two and
returns a combined verdict. `expected_status` in an instance file is therefore a *set* of
acceptable statuses, and for non-optimal instances cross-solver agreement means agreeing on the
verdict class, not on the spelling. Any downstream harness that treats these as distinguishable
will misreport unbounded models.

These matter beyond the seed corpus: defects 1, 3 and 4 all make an instance's optimum
**independent of its data**, which is precisely what the planned S1/S2 instance generators must
detect and refuse to emit, and what the real HITL-ingested formulations must be screened for.
