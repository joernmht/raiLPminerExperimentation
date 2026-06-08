# CLAUDE.md — raiLPminerExperimentation

Guidance for Claude Code when working in this repository.

## What this is

The **experiment harness** for the paper *“LP Mining with LP2Graph: A Use Case
for Railway Rescheduling.”* It mines published LP/MILP formulations into a
versioned dataset + induced taxonomy and validates the representation's fidelity.

This repo is deliberately **thin**. The *method* lives in the `lp2graph` library
(`../lp2graph`, its `mining/` package = modules M1–M6). Here we only do
**corpus management + orchestration + artifact generation**. Do not
re-implement method logic that belongs in lp2graph — call its API.

> The repo's earlier purpose — LLM *generation* of MILPs (SIM/TAF/DC workflows,
> 4×4×3 factorial experiments) — was **superseded** and removed on the
> `paper-lp-mining` branch. It is recoverable from git history on `main`.

## Layout

- `railpminer/` — one module per pipeline stage; `pipeline.run()` is the glue.
  - `_lp2graph.py` imports first: it makes `lp2graph` importable (installed, or
    the sibling `../lp2graph/src` via PYTHONPATH — auto-detected). Always
    `from . import _lp2graph` before importing `lp2graph.*` in a new module.
  - `corpus.py`(M5) → `clustering.py`(M2/M3) → `labeling.py`(M4) →
    `dataset.py` + `taxonomy_export.py`(outputs) → `validation.py`(codec/solve/M6).
  - `config.py` holds the single `PipelineConfig` (paths + versioned cluster/loop
    config + tolerance). `cli.py` is the `python -m railpminer` entry point.
- `corpus/` — the input data (see README). `formulations/*.json` are validated
  canonical LP2Graph models; `provenance/*.json` are `ProvenanceRecord`s matched
  by formulation id; `instances/*.json` carry cardinalities/data + a published
  optimum for the external-fidelity check; `manifest.json` is the regeneration
  record. **The shipped corpus is an illustrative SEED** (10 structural
  templates) — see README "Building the full corpus".
- `outputs/` — generated, git-ignored, regenerable with `python -m railpminer run`.
- `tests/` — pytest; `conftest.py` provides `config`/`corpus` fixtures.

## Running

```bash
python -m railpminer run        # full pipeline → outputs/
python -m railpminer <corpus|cluster|label|validate|taxonomy>   # single stage
python -m pytest                # 15 tests; end-to-end + determinism + fidelity
```

If `lp2graph` is not installed, the sibling checkout `../lp2graph/src` is used
automatically. Set `LP2GRAPH_SRC` to override. The validation stage needs the
solver extra (`pulp`, `highspy`); without it, external fidelity is *reported as
unavailable*, not skipped silently.

## Conventions (match lp2graph)

- **Determinism is the contract.** Same corpus + same versioned config ⇒
  byte-identical artifacts. No `Date.now`/randomness in the forward path. Keep
  seeds/thresholds explicit in `PipelineConfig`; the test
  `test_run_is_deterministic` guards this.
- **Honesty about coverage.** Load/ingest/solve failures are *reported* in the
  result objects, never silently dropped or coerced.
- **Provenance.** Every emitted label records its source; every frozen lp2graph
  resource version is stamped into `run_summary.json`.
- Python 3.11+, `from __future__ import annotations`, frozen dataclasses for
  result types, typed signatures, module docstrings that name the paper stage
  and the M-module each wraps.

## Adding corpus entries

No code changes needed: drop a validated `formulations/<id>.json`, a matching
`provenance/<id>.json`, and (optionally) an `instances/<…>.json`, then re-run.
The loader discovers entries by file and reports any without provenance.

## Related repos

`../lp2graph` (method + `lp2graph-dev` skill), `../milp_sources` (raw corpus),
`../67531d7506c81a8c34f5794e` (the LaTeX paper).
