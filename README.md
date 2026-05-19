# raiLPminer

**Open-LLM MILP generation for railway rescheduling, with a solver-validated
graph screen for early flaw detection.**

raiLPminer generates structurally diverse Mixed-Integer Linear Programming
(MILP) formulations for railway rescheduling from **problem descriptions**
using **open-weight** Large Language Models. It then asks one focused
question:

> **Hypothesis.** A graph-based depiction of a generated MILP allows
> identifying flaws *early* in the development process — before any solver
> is implemented.

To test this honestly, every generated MILP is **actually solved**. Each
formulation is turned into runnable solver code, solved on multiple
benchmark railway instances, and checked for operational safety (running
times, dwell, headway, station capacity). The cheap, solver-free *graph
screen* is then measured against this solver ground truth — so the graph
metrics are evaluated as a *predictor of real validity*, never presented as
validity in themselves.

---

## What changed after the rejection

The previous version was rejected because graph-based structural metrics
were used *as if* they proved correctness. This revision keeps the
structural-diversity goal but reframes and grounds it.

| Reviewer point | Concrete change |
|---|---|
| #2/#4 — no solver execution, no feasibility/safety | New `railpminer.validation` package: each MILP → PuLP code → solved on **multiple benchmark instances**; feasibility, optimality, runtime and **operational-safety checks** (headway, capacity, running time, dwell) recorded. |
| #2 — completeness/coherence are superficial; a content-free model "passed" | These are now an explicit *early screen*, not a validity claim. Their true/false-positive rate is **measured** against the solver truth (`analysis.hypothesis`). The empty-model case is a tracked test case in the dry run, not a hidden admission. |
| #2 — questionable practical significance | The contribution is reframed: a millisecond-cost graph screen that flags flawed MILPs *before* expensive solver implementation; its early-catch rate is quantified. |
| #2 — incomplete / non-existent reference MILPs | "Deviation from a hand-picked reference" is dropped as a quality proxy. Ground truth is now solver + safety on shared benchmark instances. |
| #2 — unfair design (skipped/repeated/hand-filtered cells, temperature fudge) | `experiments.build_factorial_design` produces a fully-crossed, equally-replicated, deterministic design; `assert_balanced` fails loudly on any imbalance. Temperature support is recorded, never silently rewritten. |
| #3.1 — keyword-matching limitation | Documented in `analysis.constraints` and below: synonyms undercount domain relevance; results are a lower bound. |
| #3.2 — hard operator-coverage threshold | `detect_milp_artifacts(min_coverage=…)` is parameterised; the edge case and a dynamic-threshold future direction are documented. |
| #4.4 — LLM parser is a second LLM layer | The **primary parser is deterministic** (strict generation layout, pure string processing). The LLM parser is kept only for a **measured** agreement metric (`parser_agreement`). |
| #4.5 — non-linear absolute-value / products slip through | `analysis.linearity` flags them structurally; the solver build independently rejects them; both signals are compared. |
| Creativity / structural diversity | Retained: diversity metrics and high-diversity subset selection remain, now reported alongside solver validity instead of instead of it. |

---

## Pipeline

```
Problem description (operational setting, decisions, objective, rules)
        │
        ▼
  MILP generation            ← open-weight LLMs, simple workflows (ZS / CFC),
  (strict delimited layout)     NO feedback loops
        │
        ├───────────────► Deterministic parse → bipartite graph   (no LLM)
        │                        │
        │                        ▼
        │                  Graph screen (cheap, solver-free):
        │                  parse_ok · complete · coherent · linear
        │                        │
        ▼                        │
  Validation per solving         │
  PuLP code → solve on N         │
  benchmark instances →          │
  feasibility / optimality /     │
  railway-safety  (ground truth) │
        │                        │
        └────────► Hypothesis analysis ◄────────┘
            (does the cheap screen predict the solver truth?
             precision / recall / early-catch rate)
```

**Workflows** (deliberately minimal — the study is about the graph screen,
not prompt engineering; no self-review / feedback loops):

| Key | Name | Description |
|-----|------|-------------|
| `ZS` | Zero-Shot | One prompt, one answer |
| `CFC` | Code-First-Convert | Draft solver code, then transcribe equations (still single pass) |

**Open-weight models** (via an OpenAI-compatible aggregator, default
OpenRouter; point `OPENROUTER_BASE_URL` at a local vLLM/Ollama server to run
fully offline):

| Key | Open-weight checkpoint |
|-----|------------------------|
| `llama_3_3_70b`   | `meta-llama/llama-3.3-70b-instruct` |
| `qwen_2_5_72b`    | `qwen/qwen-2.5-72b-instruct` |
| `deepseek_v3`     | `deepseek/deepseek-chat` |
| `mistral_small_3` | `mistralai/mistral-small-3.1-24b-instruct` |

---

## Installation

Python 3.11+.

```bash
pip install -r requirements.txt
export PYTHONPATH="$PWD:$PYTHONPATH"
```

The default solver is PuLP's bundled CBC (no extra setup). Gurobi can be
added later as an alternative back end without changing the pipeline.

---

## Quick start — offline dry run (no API key, no network)

This proves the validation-per-solving pipeline end to end on four mock
MILPs (a correct one, a non-linear one, an infeasible one, and an
empty-but-structurally-passing one):

```bash
python examples/dry_run.py
```

Expected: the combined graph screen flags all three flawed MILPs and passes
the correct one (early-catch rate 1.0, false-alarm rate 0.0 on the mock
set), while the per-signal table shows that **no single cheap flag
suffices** — exactly the nuance the reviewers asked for.

---

## Reproducing the full experiment

```python
from railpminer.utils.io import process_inputfiles
from railpminer.config import register_problem
from railpminer.experiments import build_factorial_design, assert_balanced
from railpminer.experiments.runner import run_agent_experiments
from railpminer.analysis.graph_parser import create_graph_columns
from railpminer.analysis.screen import compute_graph_screen
from railpminer.validation import validate_dataframe
from railpminer.analysis.hypothesis import (
    evaluate_screen_against_solver, screen_confusion)

# 1. Problem descriptions (NOT paper abstracts) as input
df_p = process_inputfiles("inputs/", output_name="problem", file_suffix=".txt")
for _, r in df_p.iterrows():
    register_problem(r["variable_name"], r["text"])

# 2. Balanced, deterministic factorial design
design = build_factorial_design({
    "model":       ["llama_3_3_70b", "qwen_2_5_72b",
                    "deepseek_v3", "mistral_small_3"],
    "workflow":    ["ZS", "CFC"],
    "temperature": [0.2, 0.6, 1.0],
    "problem":     list(df_p["variable_name"]),
}, replications=14)

# 3. Generate (needs OPENROUTER_API_KEY)
gen = await run_agent_experiments(design)

# 4. Deterministic graph + cheap screen
gen = compute_graph_screen(create_graph_columns(gen, "answer"))

# 5. Validation per solving (ground truth)
val = await validate_dataframe(gen)            # codegen + PuLP on benchmarks
assert_balanced(val, ["model", "workflow", "temperature", "problem"])

# 6. Test the hypothesis
print(screen_confusion(val))
print(evaluate_screen_against_solver(val))
```

`analysis.ipynb` still reproduces the structural-diversity figures and the
high-diversity subset selection.

---

## Project structure

```
railpminer/
  config.py                 # open-weight registry, problem registry
  models/agents.py          # ZS / CFC, no feedback loops, strict output layout
  experiments/
    permutations.py         # fully-crossed, balanced, deterministic design
    runner.py               # single-pass generation / LLM-parser runners
  analysis/
    graph_parser.py         # deterministic parser (primary) + LLM-parser agreement
    metrics.py              # complexity metrics (framed as an early screen)
    linearity.py            # structural non-linearity detection
    screen.py               # the cheap solver-free verdict
    constraints.py          # domain keyword screen (limitations documented)
    milp_detection.py       # operator pre-filter (parameterised threshold)
    hypothesis.py           # screen vs solver-truth: precision / recall
    selection.py            # high-diversity subset selection
  validation/
    instances.py            # benchmark railway rescheduling instances
    codegen.py              # MILP text → PuLP code (single pass)
    solve.py                # build + solve + status/runtime
    railway_checks.py       # headway / capacity / running-time / dwell checks
    pipeline.py             # → strict solver_valid ground-truth label
inputs/                     # problem descriptions (replace paper abstracts)
examples/                   # offline dry run + mock MILPs
analysis.ipynb              # structural-diversity figures & subset selection
```

---

## Known limitations (stated up front)

- **Keyword domain matching** (`analysis.constraints`) only fires on
  predefined terms; synonymous phrasing is undercounted, so reported domain
  coverage is a *lower bound*, never a correctness statement. Semantic
  matching is future work.
- **Operator-coverage threshold** is an empirical hard cut; a small but
  valid MILP using only `∑` and `≤` can be rejected. The threshold is a
  parameter so its sensitivity is reportable; a dynamic threshold is future
  work.
- **LLM parser** error is *measured* (`parser_agreement`), not eliminated;
  the deterministic parser is the primary path precisely to avoid relying on
  a second LLM layer.
- **Generated solver code is executed.** Run validation only in an isolated,
  sandboxed environment.
- The benchmark instances are small, synthetic and deterministic by design
  (reproducibility without external data); they are sufficient to detect
  infeasibility, non-linearity and safety violations, not to benchmark
  solver performance at industrial scale.

---

## License

MIT — see [LICENSE](LICENSE).
