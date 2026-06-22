# raiLPminer — LP Mining experiments

Reproducible experiment harness for the paper **“LP Mining with LP2Graph: A Use
Case for Railway Rescheduling.”**

It mines a corpus of published LP/MILP formulations from the scientific
literature and distills them into a **versioned, regenerable dataset** and an
**induced taxonomy** of variable types, constraint families and model types —
then validates that the underlying graph representation is faithful by
round-tripping each model through LaTeX and re-solving it against the optima
published with it.

> **What changed.** Earlier, this repository drove LLM *generation* of MILPs
> (the SIM/TAF/DC workflows and the 4×4×3 experiments). That direction has been
> **superseded**: the new paper is about *mining* existing formulations, not
> generating new ones. All generation-era code, data and notebooks were removed
> (recoverable from git history on the `main` branch). Structure-aware
> generation now reappears only as a *downstream use* of the mined taxonomy.

---

## How it fits together

The **method** is deterministic and lives in the
[`lp2graph`](https://github.com/joernmht/lp2graph) library — specifically its
`mining` package, which implements the six modules the paper's methodology
needs (M1–M6: ingestion, homologization, clustering, labeling, corpus
management, isomorphism). This repository is the **experiment**: it manages the
corpus, orchestrates lp2graph's mining API end-to-end, and writes the paper's
artifacts. No method logic is re-implemented here — the harness stays thin so it
tracks the library as it evolves.

```
          corpus  ──►  taxonomy  ──►  labels  ──►  dataset + taxonomy tables
 (formulations +      (multi-level    (two-stage     (the mined outputs)
  provenance, M5)      clustering,     closed-loop
                       M2+M3)          labeling, M4)
                                                          │
                       a-posteriori validation  ◄─────────┘
        (codec round-trip + cross-solver re-solve + intra-cluster isomorphism, M6)
```

Each stage is one module in `railpminer/` (see *Architecture* below).

---

## Quick start

You need Python 3.11+, the `lp2graph` library, and two solvers for the
validation stage.

```bash
# 1. Third-party runtime deps (mining + validation):
pip install -r requirements.txt

# 2. lp2graph itself — EITHER install it…
pip install "lp2graph[mining,solver]"
#    …OR keep it as a sibling checkout (../lp2graph). railpminer auto-detects
#    ../lp2graph/src and adds it to the path, so nothing else is needed.

# 3. Run the whole pipeline (writes artifacts under outputs/):
python -m railpminer run
```

That prints a run summary and writes the artifacts listed below. Everything is
deterministic: re-running reproduces byte-identical outputs.

From Python:

```python
from railpminer import run, PipelineConfig

result = run(PipelineConfig())     # writes outputs/, returns a MiningResult
print(result.summary())
```

---

## Command-line interface

```bash
python -m railpminer run          # full pipeline → outputs/
python -m railpminer corpus       # load + summarize the corpus
python -m railpminer cluster      # induce + print the multi-level taxonomy
python -m railpminer label        # run the closed-loop labeling, print labels
python -m railpminer validate     # structural + external fidelity + isomorphism
python -m railpminer taxonomy --latex   # the taxonomy axes table as LaTeX

# Point at a different corpus / output dir:
python -m railpminer run --corpus path/to/corpus --output path/to/outputs

# Corpus construction (acquisition; not in the deterministic forward path):
python -m corpusbuilder prisma    # recompute the PRISMA tally + yield figure
```

---

## The corpus

```
corpus/
  manifest.json            queries + frozen search date (makes the corpus regenerable)
  candidates.json/.md      database-search hits (identification)
  snowball_candidates.*    citation-searching neighbours (identification)
  dossiers/<key>.json/.md  per-paper acquisition record: entitlement + mined formulas (eligibility)
  formulations/<id>.json   one VALIDATED canonical LP2Graph model per formulation (included)
  provenance/<id>.json     one ProvenanceRecord per formulation, matched by id
  instances/*.json         validation instances (cardinalities + data + published optimum)
  prisma.json/.md          running PRISMA tally — source of truth for every "n =" in the paper
  prisma_flow.png          PRISMA flow + mining-yield figure (regenerable)
```

### PRISMA flow + yield

Corpus construction is a **PRISMA flow** (identification → screening → eligibility
→ included). `python -m corpusbuilder prisma` recomputes the tally from the frozen
corpus artifacts and writes `corpus/prisma.{json,md}` plus the `prisma_flow.png`
figure. It is **deterministic** (same frozen corpus ⇒ byte-identical tally) and is
the single source of truth for the paper's methods section. The figure makes the
pipeline's *yield* obvious: a broad identification sweep is distilled to a small
set of validated formulations, while the deterministic-first extraction ladder
(arXiv LaTeX → Elsevier MathML, no OCR) mines hundreds of machine-checked formula
records from the retrieved full texts.

The shipped corpus is a **seed**: ten canonical *structural templates*
(assignment, PESP, big-M ordering, time-indexed, …) paired with illustrative
bibliographic provenance, so the full pipeline runs end-to-end out of the box.
The provenance metadata is clearly marked as illustrative in `manifest.json`.

### Building the full corpus

To produce a paper-grade run, replace the seed entries with real extractions
following the paper's protocol (`chapters/03_methodology/sec_scope_corpus.tex`):

1. **Collect sources** by priority cell P1–P5 (railway rescheduling first,
   production rescheduling as an analogical outer shell). Source material lives
   in `../milp_sources/` and is categorized in `../lp2graph/corpus/`.
2. **Extract** each formulation into a validated canonical LP2Graph model —
   either by transcribing to the canonical LaTeX grammar
   (`lp2graph parse model.tex`) or via the M1 ingestion front-end
   (`lp2graph.mining.ingest`). Ingestion failures are *reported*, never dropped.
3. **Drop in** the validated `formulations/<id>.json`, a matching
   `provenance/<id>.json` (real venue, tier, year, citation count at the freeze
   date, domain shell, activity, priority cell), and — where instance data and a
   published optimum exist — an `instances/<…>.json`.
4. Re-run `python -m railpminer run`.

Adding entries needs no code changes: the loader discovers everything by file.

---

## Outputs

`python -m railpminer run` writes to `outputs/` (git-ignored, regenerable):

| File | Contents |
|------|----------|
| `dataset.json` | Per-formulation mined dataset: canonical-model summary, structural metrics, presence flags, multi-level labels, provenance, validation status. |
| `taxonomy.json` | The induced taxonomy: cluster counts and the five axes with their categories. |
| `taxonomy.csv` | The taxonomy axes as a flat table. |
| `taxonomy_axes.tex` | The taxonomy axes as a LaTeX table (paper's `tab:taxonomy_axes`). |
| `clustering_report.json` | Cluster counts, per-level silhouette, and stability (ARI under perturbed configs). |
| `validation_report.json` | Structural / external fidelity per model, cross-solver agreement, intra-cluster isomorphism, citation-anchored representatives. |
| `run_summary.json` | Compact digest + the versions of every frozen resource in force. |

---

## Architecture

A thin module per stage; `pipeline.run()` is the deterministic glue.

| Module | Stage | lp2graph modules used |
|--------|-------|-----------------------|
| `railpminer/corpus.py` | 1 — corpus construction + provenance | `mining.corpusmgr` (M5) |
| `railpminer/clustering.py` | 3–4 — features + multi-level clustering | `mining.homologize` (M2), `mining.cluster` (M3) |
| `railpminer/labeling.py` | 5 — two-stage closed-loop labeling | `mining.label` (M4) |
| `railpminer/dataset.py` | 6 — the mined dataset | `metrics`, `views` |
| `railpminer/taxonomy_export.py` | 6 — the taxonomy table | `mining.cluster` |
| `railpminer/validation.py` | 7 — fidelity validation | `codec`, `solve`, `mining.isomorphism` (M6) |
| `railpminer/pipeline.py` | — orchestration | all of the above |
| `railpminer/config.py` | — versioned configuration | — |
| `railpminer/_lp2graph.py` | — locate/import lp2graph | — |

**Determinism.** Same corpus + same versioned config ⇒ identical artifacts.
Seeds and thresholds are explicit in `PipelineConfig`; every frozen lp2graph
resource (lexicon, thesaurus, vocabulary, clustering, label lexicon, rewrite
rules) is versioned and stamped into `run_summary.json`. The labeling loop runs
without a human by using a deterministic structural labeler (read off each
model's type signature) in place of human adjudication, and records the source
of every label so the provenance stays transparent.

**Honesty about coverage.** Models the canonical grammar cannot express, or that
have no published instance data, are *reported* and excluded from the claims
they cannot support — never silently coerced.

---

## Tests

```bash
python -m pytest
```

Covers corpus loading, end-to-end determinism (the dataset is byte-identical
across runs), external fidelity against published optima, and labeling
completeness/determinism.

---

## Relationship to the other repositories

- **`lp2graph`** — the deterministic method library (core model, codec, solver,
  and the `mining` M1–M6 modules) this harness orchestrates.
- **`milp_sources`** — the raw corpus of published formulations to extract from.
- **`67531d7506c81a8c34f5794e`** — the LaTeX paper this experiment supports.

## License

MIT — see `LICENSE`.
