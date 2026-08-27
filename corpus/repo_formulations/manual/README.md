# Hand-canonicalised repository models

`corpusbuilder.repo_corpus` converts the structured extractions in
`lp2graph/corpus/*.json` into canonical `Formulation`s automatically; the
models it refuses are listed with their cause in `../_report.json`.

This directory holds models that were canonicalised **by hand** instead,
because they are needed as worked examples. They are *not* produced by the
converter and are *not* part of its report; each carries a `.meta.json` with
`"conversion": "manual"`, the rows that had to be excluded, and why.

| model | source repo | licence | excluded rows |
|---|---|---|---|
| `Gurobi__modeling-examples__railway-dispatching-mip` | [Gurobi/modeling-examples](https://github.com/Gurobi/modeling-examples) | Apache-2.0 | `resource_capacity_hotspot` (subset binder, outside the grammar) |

Each file here must satisfy, with `PYTHONPATH=~/lp2graph/src`:

```
python3 -m lp2graph validate corpus/repo_formulations/manual/<id>.tex
python3 scripts/checks/anatomy_figure_check.py \
    <paper>/figures/fig_lp2graph_anatomy_body.tex \
    corpus/repo_formulations/manual/<id>.tex
```
