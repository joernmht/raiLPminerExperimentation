# Software stack — raiLPminerExperimentation

The experiment harness for Paper 1, *"LP Mining with LP2Graph: A Use Case for
Railway Rescheduling."* Two packages: **`railpminer/`** (the deterministic
mining pipeline) and **`corpusbuilder/`** (corpus acquisition: OpenAlex / arXiv /
Elsevier ingest → per-paper dossiers → PRISMA tally). The *method* lives in
`lp2graph`; this repo is corpus management + orchestration + artifact generation.

_Last verified: 2026-09-07 (nightly quality pass — Compatibility)._

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
  full text from a campus IP (ADR-0003).

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

### `corpusbuilder/` — corpus acquisition + HITL review (14.6k LOC, 26 modules)
Clean DAG, no import cycles. `dossier.py` (Pydantic models) is the leaf data
contract; `config.py` is dependency-light credential loading; `_http.py` is the
shared transient-fault layer (retry/backoff + `AcquisitionError`) that every
client GET goes through. Acquisition clients (`arxiv`, `openalex`, `elsevier` +
`mathml`) sit above; `cli.py` orchestrates. One-off drivers (`_discover`,
`prisma`, `snowball`, `review_view`) read `corpus/*.json` and emit artifacts.
Extraction is a **tiered ladder** (ADR-0002): Tier-1 arXiv `.tex` → Tier-2
Elsevier MathML → Tier-3 OCR (future). A clear **determinism boundary**
(ADR-0001) separates acquisition (records a real retrieval date, network) from
the forward pipeline (frozen files, no `Date.now`).

Since the 2026-07 refresh the package has grown well past acquisition; the
five largest modules are now the review/promotion machinery, not the clients:
`game.py` (3.2k — the browser HITL review app), `talkpack.py` (1.9k — figure
and numbers pack), `assist.py` (1.9k — parser-gated assisted resolution,
ADR-0011), `repo_corpus.py` (1.4k — exact-or-refused repo conversion,
ADR-0015), `promote.py` (1.2k — declaration-gated promotion, ADR-0010/0013).
`split.py`, `fingerprint.py`, `resolution.py`, `symbols.py` and `wlcluster.py`
(ADR-0014) round out the corpus-shaping side.

Sizes (2026-09-07): `railpminer` 2.8k LOC / 14 files · `corpusbuilder` 14.6k /
26 · `scripts` 3.3k / 7 · `tests` 4.3k / 17.

## Entry points

| Command | What |
|---|---|
| `python -m railpminer run` | full pipeline → `outputs/` (git-ignored, regenerable) |
| `python -m railpminer <stage>` | single stage (corpus/cluster/label/validate/taxonomy) |
| `python -m corpusbuilder seeds --query …` | list well-cited candidate papers |
| `python -m corpusbuilder dossier <id> [--arxiv …]` | build a per-paper dossier |
| `python -m corpusbuilder fetch-arxiv <id>` | extract equations from arXiv source |
| `python -m corpusbuilder._discover <date> [--resume]` | seed sweep → `corpus/candidates.json` |
| console scripts | `railpminer`, `corpusbuilder` (via `[project.scripts]`) |

**Exit codes** (`corpusbuilder dossier`, `_discover`): `0` success · `1` permanent
error · **`2` transient — nothing written, safe to re-run** (ADR-0006). Batch
drivers should retry on 2 and escalate on 1.

## Build / test / lint commands

| Task | Command |
|---|---|
| Tests | `python3 -m pytest` (285 tests; offline, no sleeps — the sibling lp2graph checkout is auto-detected, or set `LP2GRAPH_SRC`) |
| Lint | `ruff check .` |
| Format | `ruff format --check .` (apply: `ruff format .`) |
| Types | `mypy railpminer corpusbuilder` (**not** `--strict` yet — relaxing |
| | incrementally; missing `requests`/`lxml` stubs + a few `snowball.py` nits) |
| Pre-commit | `pre-commit run --all-files` (ruff lint+format, pinned) |

Run order before declaring done (STYLE.md §1): `ruff check` → `ruff format
--check` → `mypy` → `pytest`.

## CI

`.github/workflows/ci.yml` has two jobs. **`test`** runs `ruff check` +
`ruff format --check` + `pytest` on py3.11–3.13 (mirrors lp2graph; `mypy`
deferred until the corpusbuilder type debt is paid down). **`c-locale`** runs
the suite once with `PYTHONUTF8=0 PYTHONCOERCECLOCALE=0 LC_ALL=C`, which drives
Python's preferred encoding to ASCII — the compatibility gate for ADR-0016.
`lp2graph` is installed in CI from the sibling checkout per the workflow.
*(CI added 2026-06-22; `c-locale` added 2026-09-07.)*

> The lint step gates the rest of the job. CI on `main` was **red at `Lint`
> for three consecutive runs (2026-08-14 → 2026-09-06)**, so `pytest` did not
> run in CI at all for ~3.5 weeks. If `ruff check` fails, assume there is *no*
> test signal, not a passing one.

## Known drift / watch items

- **mypy is not yet a gate.** `corpusbuilder/` has 38 mypy findings (mostly
  missing third-party stubs; a real variable-shadowing smell at
  `snowball.py:101` where `rec` is rebound dict→list). Pay down, then gate.
- **PuLP deprecation** (inherited via lp2graph's solver path): `pyproject.toml`
  already silences pulp's `DeprecationWarning`; a PuLP 4.0 migration is tracked
  in lp2graph, not here.
- **Corpus status:** the shipped `corpus/` is an illustrative SEED (10 structural
  templates), *not* the paper-grade dataset. See `README.md` and the home
  `CLAUDE.md` corpus ground-truth note. (`corpus/formulations/` now holds 18
  entries — the 10 seed templates plus 8 hand-canonicalised extractions.)
- **The `lp2graph` requirement is unbounded** (`lp2graph[mining,solver]>=0.3`)
  and lp2graph is a *source* sibling here, not an installed distribution, so
  the method implementation in play is whatever `../lp2graph/src` currently
  contains. ADR-0017 makes that visible in `run_summary.json.software`; it does
  not constrain it. Capping at `<0.4` would make a breaking bump loud but would
  also break the cross-repo dev loop — a deliberate open question, not an
  oversight.
- **`schema_version` is written but almost never verified.** Seven schema
  families ship in `corpus/` (`dossier-1` ×286, `game-decisions-3` ×228,
  `0.1.0` ×25, `fingerprint-1`, `promotion-1`, `prisma-1`, plain `"1"`), and
  the only reader that checks one is `railpminer/verifier_demo.py:416`. A
  version tag no consumer validates buys nothing; naming is ad hoc too
  (`name-N` vs semver vs bare integer).

## Security posture (acquisition boundary)

`corpusbuilder/` is the only network- and untrusted-input surface; the
`railpminer/` forward pipeline reads only frozen local corpus files. Controls
(hardened in the 2026-07-04 security pass, ADR-0005):

- **Untrusted tarballs** (`arxiv.fetch_source`): `_safe_extract` extracts regular
  files only (skips symlinks/hardlinks/devices), enforces containment with
  `Path.is_relative_to` (not a string `startswith`), and passes `filter="data"`.
- **Untrusted publisher XML** (`elsevier.extract_formulas`): parsed via a
  hardened `_XML_PARSER` (`resolve_entities=False, no_network=True,
  load_dtd=False, huge_tree=False`) — no XXE, no entity-expansion DoS.
- **Node MathML bridge** (`mathml.py`): `subprocess.run` with an argv **list**
  (no `shell=True`), content via stdin/JSON — no command-injection surface.
- **Secrets** (`config.py`): API keys read only via accessors from
  `~/.config/raiLP/secrets.env` (chmod 600) or repo `.env` (git-ignored); never
  echoed in error messages (only paths/response bodies are).
- **Open items / watch:** the ScienceDirect DOI is interpolated into the request
  path (`article/doi/{doi}`) without validation — low risk (DOI comes from
  OpenAlex, target is a fixed host) but a malformed DOI could reshape the path;
  consider a DOI-shape guard. HTTP clients rely on `requests`' default TLS
  verification (good) and set no explicit `verify=`.

## Reliability posture (acquisition boundary)

Hardened in the 2026-07-10 reliability pass (**ADR-0006**). One rule: *a transient
fault is not coverage information* — retry it, and if it survives the retries,
refuse to write the corpus record it would have produced.

- **Shared retry layer** (`_http.py`): every client GET goes through
  `request_with_retry` — jittered exponential backoff, capped, honouring an
  upstream `Retry-After` (delta-seconds *and* HTTP-date). Retries
  `{408, 425, 429, 500, 502, 503, 504}` plus transport blips
  (`ConnectionError`/`ProxyError`, `Timeout`, `ChunkedEncodingError`). 4xx client
  errors are never retried — a 404 is an answer.
- **Fault classification**: `AcquisitionError` (base of `OpenAlexError`,
  `ElsevierError`, `ArxivError`) carries `status` + a `transient` flag.
  `is_transient_exception()` is the single decision point for the tier ladder.
- **Refusal to record**: `cmd_dossier` exits **2** and writes nothing on a
  transient tier failure (a rate-limited arXiv must not demote a Tier-1 paper).
  `_discover` checkpoints per query and only writes `candidates.json` when *all*
  queries succeeded, because `prisma.py` derives `database_queries` /
  `database_search_records` from it. Permanent failures (PDF-only e-print, 403
  not-entitled) still degrade and record — those are facts about the paper.
- **Determinism unaffected**: `_http` sits wholly on the acquisition side of
  ADR-0001's boundary; `railpminer/` gains no clock and no RNG. `sleep`/`rng`/
  `now` are injectable, so all 41 reliability tests run offline and instantly.
- **Watch:** no client-side pacing for OpenAlex's ~10 req/s polite pool (retries
  absorb 429s reactively). `SourceInfo` cannot distinguish "Scopus: not indexed"
  from "Scopus lookup failed" — both serialize as `null`; fixing needs a
  `dossier-2` schema bump.

## Compatibility posture (interchange boundary)

Assessed in the 2026-09-07 compatibility pass (**ADR-0016**, **ADR-0017**). The
corpus is an interchange artifact — public GitHub, Zenodo-bound, read by third
parties on their own machines — so its encoding and line endings are part of
the contract, not of the environment.

- **UTF-8 everywhere, declared.** Every text-mode `open()` / `read_text()` /
  `write_text()` passes `encoding="utf-8"` (78 call sites converted). 487
  corpus JSON files carry non-ASCII; 472 contain at least one character cp1252
  cannot represent. Under a Windows default a read *silently mojibakes* and a
  write raises `UnicodeEncodeError`.
- **LF everywhere, declared.** Every text write in the producing packages
  passes `newline="\n"` (55 call sites). Without it `os.linesep` turns the
  emitted artifacts into CRLF on Windows, which breaks the repo's
  byte-identical-artifact claim by every line. Reads keep universal-newline
  translation, so CRLF input still parses.
- **Enforced, not asserted.** `tests/test_text_io_encoding.py` walks the AST of
  `railpminer`, `corpusbuilder`, `scripts` and `tests`, and carries
  planted-violation tests so a green run proves the guard can still fail. The
  CI `c-locale` job runs the whole suite under an ASCII default.
- **Provenance of the code, not just the data.** `run_summary.json` now carries
  a `software` block (`railpminer`, `lp2graph`, `python`) alongside the frozen
  lp2graph *resource* versions, so an artifact set is traceable to the
  implementation that emitted it (ADR-0017).
- **Interpreter range is claimed but only partly gated.** `requires-python =
  ">=3.11"` and CI covers 3.11–3.13 on ubuntu only. No Windows or macOS runner
  exists, which is exactly why the encoding drift went unnoticed; the
  `c-locale` job is the affordable proxy, not a substitute.
- **Node bridge.** `corpusbuilder/mathml.py` shells out to a vendored
  `mathml-to-latex` under `corpusbuilder/_mathml2latex` (needs `node` +
  `npm install`). Pure-Python paths never touch it, and it is invoked with an
  argv list over stdin/JSON, so it is a compatibility dependency rather than a
  security one.
