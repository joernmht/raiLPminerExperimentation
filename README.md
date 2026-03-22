# raiLPminer

**LLM-driven optimization model mining for generating creative railway rescheduling approaches**

raiLPminer generates diverse Mixed-Integer Linear Programming (MILP) formulations from unstructured textual descriptions using Large Language Models. It converts generated MILPs into graph representations (LP2Graph) to evaluate structural quality — without solving — and selects a high-diversity subset for further development.

> **Paper:** Maurischat, J., & Bešinović, N. (2025). *raiLPminer: LLM-driven optimization model mining for generating creative railway rescheduling approaches.* Preprint submitted to Elsevier.
> Experimental notebooks: [doi:10.5281/zenodo.17460374](https://doi.org/10.5281/zenodo.17460374) · Experimental results: [doi:10.5281/zenodo.17460136](https://doi.org/10.5281/zenodo.17460136)

---

## Overview

```
Textual Input (paper abstract + intro)
        │
        ▼
  MILP Development          ← 4 workflows × 4 LLMs × 3 temperatures
  (ZS / CFC / OE / PS)
        │
        ▼
  LP2Graph (PydanticAI)     ← textual MILP → bipartite graph
        │
        ▼
  Analysis & Filtering      ← completeness, coherence, complexity metrics
        │
        ▼
  High-Diversity Subset     ← 4 selected MILPs spanning the solution space
```

**Workflows**

| Key | Name | Description |
|-----|------|-------------|
| `ZS` | Zero-Shot | Single prompt, no intermediate steps |
| `CFC` | Code-First-Chain | Generate solver code first, then extract equations |
| `OE` | Operator-Expert | Orchestrator–worker with iterative self-review |
| `PS` | Parallelization-Selection | Multiple parallel ZS calls, best one selected |

**Supported LLMs** (via [PydanticAI](https://docs.pydantic.dev/latest/concepts/pydantic_ai/))

| Key | Model |
|-----|-------|
| `openai_o4_mini` | OpenAI o4-mini |
| `gemini_flash` | Gemini 2.5 Flash |
| `gemini_pro` | Gemini 2.5 Pro |
| `deepseek_v3` | DeepSeek V3 |

---

## Installation

**Python 3.11+** is required.

```bash
git clone <repo-url>
cd raiLPminerExperimentation

pip install pydantic-ai pandas tqdm statsmodels plotly ipykernel jupyter
```

Install the package in editable mode:

```bash
pip install -e .
```

> If there is no `setup.py` / `pyproject.toml`, add the repo root to your `PYTHONPATH`:
> ```bash
> export PYTHONPATH="$PWD:$PYTHONPATH"  # Linux/macOS
> set PYTHONPATH=%CD%;%PYTHONPATH%       # Windows CMD
> ```

---

## API Key Setup

Set the required API keys as environment variables before running any experiment. Only the keys for the models you intend to use are required.

```bash
# OpenAI (o4-mini)
export OPENAI_API_KEY="sk-..."

# Google (Gemini Flash / Pro)
export GOOGLE_API_KEY="AIza..."

# DeepSeek (V3 / R1)
export DEEPSEEK_API_KEY="..."
```

On Windows (PowerShell):
```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:GOOGLE_API_KEY = "AIza..."
$env:DEEPSEEK_API_KEY = "..."
```

---

## Reproducing the Experiments

### Step 1 — Prepare paper inputs

The experiments use the **abstract + introduction** of five railway rescheduling papers as input (pre-MILP sections, stripped of all mathematical formulations). Place each paper's text as a plain `.txt` file in an input directory, one file per paper:

```
inputs/
├── paper_1.txt   # Zhan et al. (2016) — high-speed train rescheduling
├── paper_2.txt   # Application study (no explicit MILP)
├── paper_3.txt   # Koniorczyk et al. (2025) — heterogeneous urban networks
├── paper_4.txt   # Pellegrini et al. (2014) — complex junctions
└── paper_5.txt   # Zhang et al. (2023) — large-scale networks
```

Load papers into the registry at the start of your notebook:

```python
from railpminer.utils.io import process_inputfiles
from railpminer.config import register_paper

df_papers = process_inputfiles("inputs/", output_name="Paper", file_suffix=".txt")
for _, row in df_papers.iterrows():
    register_paper(row["variable_name"], row["text"])
```

### Step 2 — Define the parameter grid

**Experiment Set 1** (full parameter sweep on Paper 1):

```python
from railpminer.experiments.permutations import create_all_permutations

params = {
    "model":       ["openai_o4_mini", "deepseek_v3", "gemini_flash", "gemini_pro"],
    "workflow":    ["ZS", "CFC", "OE", "PS"],
    "temperature": [0.2, 0.6, 1.0],
    "paper":       ["Paper_1"],
}
permutation_df = create_all_permutations(params)
```

**Experiment Set 2** (fixed setup across all papers):

```python
params = {
    "model":       ["openai_o4_mini"],
    "workflow":    ["ZS"],
    "temperature": [1.0],
    "paper":       ["Paper_1", "Paper_2", "Paper_3", "Paper_4", "Paper_5"],
}
permutation_df = create_all_permutations(params)
```

### Step 3 — Run MILP generation

```python
import asyncio
from railpminer.experiments.runner import run_agent_experiments

results_df = await run_agent_experiments(permutation_df, num_runs=14)
# Results are streamed to agent_experiment_results_test.csv
```

> **Note:** `num_runs=14` reproduces the minimum replication count from the paper. Total planned runs for Experiment Set 1: ≥ 720. Each run consumes LLM API tokens.

### Step 4 — Run LP2Graph (MILP graphing)

Parse generated MILP text into structured graph representations:

```python
from railpminer.experiments.runner import run_graphing_agent

graph_df = await run_graphing_agent(results_df, num_runs=1)
```

LP2Graph uses `openai_o4_mini` at `temperature=0.2` by default (precision over creativity).

### Step 5 — Compute metrics and analyze

Open `analysis.ipynb` to reproduce all figures and tables from the paper:

```bash
jupyter notebook analysis.ipynb
```

The notebook covers:
- **Acceptance analysis** (completeness × coherence filtering)
- **Complexity metrics** (minimal size, graph diameter, constraint-variable ratio)
- **OLS regression** (effect of LLM, workflow, temperature on output)
- **High-diversity subset selection** (extremal MILPs across metrics)
- **Domain-oriented constraint analysis** (keyword matching against railway constraint categories)
- **Inference time analysis**

To skip re-running the experiments and reproduce the analysis directly, load the published results:

```python
from railpminer.utils.data import load_experiment_data
df = load_experiment_data("experiment_results_metrics_corrected_selected.csv")
```

---

## Project Structure

```
raiLPminerExperimentation/
├── railpminer/
│   ├── config.py              # Model & paper registry, directory layout
│   ├── models/
│   │   ├── schema.py          # Pydantic models (Variable, Constraint, ObjectiveFunction, Model)
│   │   └── agents.py          # Workflow builders (ZS, CFC, OE, PS)
│   ├── experiments/
│   │   ├── permutations.py    # Cartesian parameter grid generation
│   │   └── runner.py          # Async experiment execution & LP2Graph runner
│   ├── analysis/
│   │   ├── metrics.py         # Complexity metrics (minimal size, diameter, C/V ratio)
│   │   ├── milp_detection.py  # Operator coverage pre-filter
│   │   ├── constraints.py     # Domain-oriented constraint classification
│   │   ├── selection.py       # High-diversity subset selection logic
│   │   └── regression.py      # OLS regression helpers
│   ├── utils/
│   │   ├── io.py              # File loading, Unicode → ASCII preprocessing
│   │   └── data.py            # CSV loading, temperature label correction
│   └── visualization/         # Plotly, Graphviz, scatter, runtime, matrix plots
├── analysis.ipynb             # Main analysis notebook
├── experiment_results_metrics_corrected_selected.csv  # Published results (35 MB)
└── legacy/                    # Earlier notebook versions
```

---

## Citation

If you use raiLPminer in your research, please cite:

```bibtex
@article{maurischat2025railpminer,
  title   = {{raiLPminer}: {LLM}-driven optimization model mining for generating
             creative railway rescheduling approaches},
  author  = {Maurischat, J{\"o}rn and Be{\v{s}}inovi{\'c}, Nikola},
  journal = {Preprint submitted to Elsevier},
  year    = {2025},
  note    = {Experimental notebooks: \url{https://doi.org/10.5281/zenodo.17460374}.
             Experimental results: \url{https://doi.org/10.5281/zenodo.17460136}}
}
```

---

## License

MIT — see [LICENSE](LICENSE).
