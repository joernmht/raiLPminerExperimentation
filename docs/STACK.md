# Software stack — raiLPminerExperimentation

The experiment harness for Paper 1, *"LP Mining with LP2Graph: A Use Case for
Railway Rescheduling."* Two packages: **`railpminer/`** (the deterministic
mining pipeline) and **`corpusbuilder/`** (corpus acquisition: OpenAlex / arXiv /
Elsevier ingest → per-paper dossiers → PRISMA tally). The *method* lives in
`lp2graph`; this repo is corpus management + orchestration + artifact generation.

_Last verified: 2026-06-22 (nightly quality pass)._

## Languages & runtime

- **Python ≥ 3.11** (`pyproject.toml: requires-python = ">=3.11"`). The code
  uses 3.11+ features: `enum.StrEnum` (`corpusbuilder/dossier.py`),
  `zip(..., strict=True)`, PEP 604 unions, `from __future__ import annotations`.
- Build backend: **hatchling**. Two wheel packages: `railpminer`, `corpusbuilder`.
- Optional Node.js toolchain for `corpusbuilder/mathml.py` (vendored
  `mathml-to-latex` bridge under `corpusbuilder/_mathml2latex`; needs
  `node` + `npm install`). Pure-Python paths never touch it.

## Dependencies

### Core method (required)
- **`lp2graph[mining,solver]>=0.3`** — the single source of truth for the
  method. Not pip-installed here; the sibling checkout `../lp2graph/src` is
  auto-detected via `railpminer/_lp2graph.py` (override with `LP2GRAPH_SRC`).
  **Always `from . import _lp2graph` before importing `lp2graph.*`.**
- Through lp2graph's `solver` extra: `pulp`, `highspy` (the validation stage's
  external-fidelity check; reported *unavailable* if absent, never skipped silently).

### `corpus` extra — corpusbuilder acquisition (`pip install -e .[corpus]`)
- `requests>=2.31` — HTTP for OpenAlex / arXiv / Elsevier clients.
- `python-dotenv>=1.0` — `.env` credential loading (graceful fallback to
  `os.environ` if absent; see `corpusbuilder/config.py`).
- `lxml>=5.0` — Elsevier full-text XML parsing (`corpusbuilder/elsevier.py`).
- `PySocks>=1.7` — SOCKS routing through an SSH tunnel for entitled Elsevier
  full text from a campus IP (ADR-0002).

### `notebooks` extra
- `pandas`, `matplotlib`, `plotly`, `jupyter` — analysis notebooks only.

### `dev` extra
- `pytest>=8.0`, `ruff`, `mypy`.

There are **no lazy/optional in-process extras** like lp2graph's torch/networkx;
the only deferred work is network I/O (clients) and the Node subprocess (mathml).

## Architecture / module map

### `railpminer/` — the deterministic pipeline (1.5k LOC)
`pipeline.run()` is the glue over six forward stages + validation:

    corpus(M5) → clustering(M2/M3) → labeling(M4)
       → dataset + taxonomy_export (outputs) → validation (codec/solve/M6)

- `config.py` — the single `PipelineConfig` (paths + versioned cluster/loop
  config + tolerance). Determinism knobs live here.
- `corpus.py` — loads & validates the on-disk corpus; load failures *reported*,
  never dropped.
- `cli.py` — `python -m railpminer {run|corpus|cluster|label|validate|taxonomy}`.

### `corpusbuilder/` — corpus acquisition (1.7k LOC, the active 2026-06 work)
Clean DAG, no import cycles. `dossier.py` (Pydantic models) is the leaf data
contract; `config.py` is dependency-light credential loading. Acquisition
clients (`arxiv`, `openalex`, `elsevier` + `mathml`) sit above; `cli.py`
orchestrates. One-off drivers (`_discover`, `prisma`, `snowball`, `review_view`)
read `corpus/*.json` and emit artifacts. Extraction is a **tiered ladder**
(ADR-0003): Tier-1 arXiv `.tex` → Tier-2 Elsevier MathML → Tier-3 OCR (future).
A clear **determinism boundary** (ADR-0001) separates acquisition (records a
real retrieval date, network) from the forward pipeline (frozen files, no
`Date.now`).

## Entry points

| Command | What |
|---|---|
| `python -m railpminer run` | full pipeline → `outputs/` (git-ignored, regenerable) |
| `python -m railpminer <stage>` | single stage (corpus/cluster/label/validate/taxonomy) |
| `python -m corpusbuilder seeds --query …` | list well-cited candidate papers |
| `python -m corpusbuilder dossier <id> [--arxiv …]` | build a per-paper dossier |
| `python -m corpusbuilder fetch-arxiv <id>` | extract equations from arXiv source |
| console scripts | `railpminer`, `corpusbuilder` (via `[project.scripts]`) |

## Build / test / lint commands

| Task | Command |
|---|---|
| Tests | `PYTHONPATH=../lp2graph/src python3 -m pytest` (24 tests; offline) |
| Lint | `ruff check .` |
| Format | `ruff format --check .` (apply: `ruff format .`) |
| Types | `mypy railpminer corpusbuilder` (**not** `--strict` yet — relaxing |
| | incrementally; missing `requests`/`lxml` stubs + a few `snowball.py` nits) |
| Pre-commit | `pre-commit run --all-files` (ruff lint+format, pinned) |

Run order before declaring done (STYLE.md §1): `ruff check` → `ruff format
--check` → `mypy` → `pytest`.

## CI

`.github/workflows/ci.yml` runs `ruff check` + `ruff format --check` + `pytest`
on py3.11–3.13 (mirrors lp2graph; `mypy` deferred until the corpusbuilder type
debt is paid down). `lp2graph` is installed in CI from the sibling checkout /
PyPI per the workflow. *(Added 2026-06-22; previously no CI.)*

## Known drift / watch items

- **mypy is not yet a gate.** `corpusbuilder/` has 38 mypy findings (mostly
  missing third-party stubs; a real variable-shadowing smell at
  `snowball.py:101` where `rec` is rebound dict→list). Pay down, then gate.
- **PuLP deprecation** (inherited via lp2graph's solver path): `pyproject.toml`
  already silences pulp's `DeprecationWarning`; a PuLP 4.0 migration is tracked
  in lp2graph, not here.
- **Corpus status:** the shipped `corpus/` is an illustrative SEED (10 structural
  templates), *not* the paper-grade dataset. See `README.md` and the home
  `CLAUDE.md` corpus ground-truth note.
</content>
</invoke>
