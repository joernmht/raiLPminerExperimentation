# raiLPminer

**Graphing as a means of designing MILPs for railway rescheduling — with
embedded domain classification and solver-grounded validation.**

raiLPminer generates Mixed-Integer Linear Programming (MILP) formulations
for railway rescheduling from **problem descriptions** using **open-weight**
LLMs, and tests one focused question:

> **Hypothesis.** A graph-based depiction of a generated MILP allows
> identifying flaws *early* in the development process — before any solver
> is implemented.

Creativity / structural diversity is no longer a goal: temperature is **0**
and the graph is used as a *design and screening instrument*. Every model is
**actually solved** on benchmark railway instances (PuLP/CBC; Gurobi
pluggable later) and checked for operational safety, so the cheap,
solver-free *graph screen* is measured as a *predictor of real validity*.

---

## What is new in this version

- **Classification is part of generation and parsing — not a-posteriori.**
  A comprehensive taxonomy (`analysis/taxonomy.py`) spans the **objective**,
  the **variables**, the **parameters** (now first-class) and the
  **constraints**. The LLM tags every element; the deterministic parser
  reads the tags back. There is no keyword matching anywhere (the old
  keyword classifier was removed), so domain relevance is *declared and
  parsed*, never synonym-undercounted.
- **Three workflows, no feedback loops, temperature 0:**

  | Key | Name | Description |
  |-----|------|-------------|
  | `SIM` | Simultaneous | One call emits the fully classified MILP |
  | `TAF` | Text-Analysis-First | Call 1 classifies the problem text into a domain blueprint; call 2 develops the classified MILP from it (two sequential calls) |
  | `DC` | Direct-Code (baseline) | One call emits runnable PuLP code **plus** an embedded `CLASSIFICATION` dict, so it is solved *and* reverse-graphed for a like-for-like comparison |

- **Diversity subset selection removed.** The project fully commits to the
  structural-validation metrics and their measured agreement with the
  solver ground truth.
- **No second LLM layer.** Parsing is deterministic for all workflows
  (strict classified text for SIM/TAF; the embedded `CLASSIFICATION` dict
  for DC).

## How the original rejection points are addressed

| Reviewer point | Concrete change |
|---|---|
| #2/#4 — no solver execution / feasibility / safety | `railpminer.validation`: each MILP → PuLP code → solved on **multiple benchmark instances**; feasibility, optimality, runtime and operational-safety checks (running time, dwell, headway, station capacity) recorded as the ground truth. |
| #2 — completeness/coherence superficial; content-free model "passed" | They are an explicit *early screen*; their true/false-positive rate is **measured** against the solver truth. The empty-model case is a tracked dry-run test, caught by `graph_parse_ok` and the solver. |
| #2 — questionable significance | The contribution is a millisecond-cost screen that flags flawed MILPs (incl. **missing safety constraints**) before solver implementation; its early-catch rate is quantified per workflow. |
| #2 — incomplete reference MILPs | No reference MILP is used; ground truth is solver + safety on shared benchmark instances. |
| #2 — unfair design / temperature fudge | Balanced, fully-crossed, deterministic factorial design (`build_factorial_design` + `assert_balanced`); temperature fixed at 0 (no creativity), so the temperature inconsistency is gone. |
| #3.1 — keyword-matching limitation | Keyword matching **removed**; classification is embedded in generation and parsed deterministically from the taxonomy. |
| #3.2 — hard operator threshold | `detect_milp_artifacts(min_coverage=…)` parameterised and documented (kept only as a coarse pre-filter). |
| #4.4 — second LLM parser layer | Eliminated: deterministic parser for every workflow. |
| #4.5 — non-linear abs/products | `analysis/linearity.py` flags them structurally; the solver build independently rejects them; both are compared. |

---

## Pipeline

```
Problem description
        │
        ▼
  Generation (SIM / TAF / DC), temperature 0, no feedback loop
        │            answer = classified MILP text  (SIM/TAF)
        │                   = PuLP code + CLASSIFICATION (DC)
        ▼
  Deterministic classified parse → classified bipartite graph   (no LLM)
        │
        ▼
  Graph screen (cheap, solver-free):
  parse_ok · complete · coherent · linear · safety_classes
        │
        ▼
  Validation per solving:
  PuLP code → solve on N benchmark instances →
  feasibility / optimality / railway-safety   (ground truth)
        │
        ▼
  Hypothesis analysis: screen vs solver truth
  (early-catch / false-alarm / precision; per-workflow comparison)
```

**Open-weight models** (OpenAI-compatible aggregator, default OpenRouter;
set `OPENROUTER_BASE_URL` to a local vLLM/Ollama server to run offline):
`llama_3_3_70b`, `qwen_2_5_72b`, `deepseek_v3`, `mistral_small_3`.

---

## Installation

Python 3.11+.

```bash
pip install -r requirements.txt
export PYTHONPATH="$PWD:$PYTHONPATH"
```

Default solver is PuLP's bundled CBC (no setup).

## Quick start — offline dry run (no API key, no network)

```bash
python examples/dry_run.py
```

Runs the full pipeline on mock data for all three workflows. The combined
graph screen separates the valid models from the non-linear / infeasible /
empty / **missing-safety** ones (early-catch 1.0, false-alarm 0.0 on the
mock set); the per-signal table shows `graph_safety_classes` carries most of
the signal, and the per-workflow table compares SIM/TAF against the DC
baseline.

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
    screen_confusion, evaluate_screen_against_solver, workflow_comparison)

df_p = process_inputfiles("inputs/", output_name="problem", file_suffix=".txt")
for _, r in df_p.iterrows():
    register_problem(r["variable_name"], r["text"])

design = build_factorial_design({
    "model":    ["llama_3_3_70b", "qwen_2_5_72b",
                 "deepseek_v3", "mistral_small_3"],
    "workflow": ["SIM", "TAF", "DC"],
    "problem":  list(df_p["variable_name"]),
}, replications=14)                       # temperature fixed at 0

gen = await run_agent_experiments(design)                 # OPENROUTER_API_KEY
gen = compute_graph_screen(create_graph_columns(gen, "answer"))
val = await validate_dataframe(gen)                       # codegen + solve
assert_balanced(val, ["model", "workflow", "problem"])

print(screen_confusion(val))
print(evaluate_screen_against_solver(val))
print(workflow_comparison(val))           # SIM/TAF vs DC baseline
```

`analysis.ipynb` reproduces the same analysis with figures.

---

## Project structure

```
railpminer/
  config.py                 # open-weight registry, problem registry
  instance_contract.py      # shared instance / build_model / CLASSIFICATION text
  models/
    schema.py               # Parameter first-class; domain_class on every element
    agents.py               # SIM / TAF / DC, temperature 0, no feedback loops
  experiments/
    permutations.py         # balanced, deterministic factorial design
    runner.py               # single-pass generation runner
  analysis/
    taxonomy.py             # comprehensive 4-kind domain taxonomy
    graph_parser.py         # deterministic classified parser (text + DC dict)
    linearity.py            # structural non-linearity detection
    metrics.py              # structural complexity metrics
    screen.py               # cheap solver-free verdict (incl. safety classes)
    hypothesis.py           # screen vs solver truth; per-workflow comparison
    milp_detection.py       # coarse operator pre-filter (parameterised)
  validation/
    instances.py            # benchmark railway rescheduling instances
    codegen.py              # classified MILP text → PuLP code (single pass)
    solve.py                # build + solve + status/runtime
    railway_checks.py       # running-time / dwell / headway / capacity checks
    pipeline.py             # → strict solver_valid ground-truth label
inputs/                     # problem descriptions
examples/                   # offline dry run + mock data
analysis.ipynb              # validation & hypothesis analysis
```

---

## Known limitations (stated up front)

- Classification quality depends on the LLM tagging correctly; mistags fall
  into the `other_*` buckets (never silently dropped) and their effect is
  visible in the measured screen accuracy.
- The operator pre-filter threshold is an empirical hard cut (parameterised;
  a dynamic threshold is future work). It is now only a coarse gate, not the
  domain classifier.
- Generated solver code is executed — run validation only in an isolated
  sandbox.
- Benchmark instances are small, synthetic and deterministic (reproducible
  without external data); sufficient to detect infeasibility, non-linearity
  and safety violations, not to benchmark industrial-scale solver
  performance.

## License

MIT — see [LICENSE](LICENSE).
