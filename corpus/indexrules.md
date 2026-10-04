# Index letters: the two deterministic rules against the human labels

Papers labelled: **5**, letters (unsure excluded): **76**. No model involved.

* **measured**: `corpusbuilder.indices`, the rule as the discovery pages propose it.
* **reported**: the same rule as `corpusbuilder.resolution` counts it as the free index prefill (`\resIndexPrefill`), via `corpusbuilder.symbols.paper_evidence`: in the game tokenizer's names (`k'` is `k`, `\vartheta` is `θ`), a name shared by several letters binds only when all of them do, and a domain row makes a symbol a variable (ADR-0023).

| rule | agrees with the human (bind vs not) | precision (index verdicts confirmed) | recall (human indices found) |
|---|---:|---:|---:|
| measured | 62/76 (82%) | 42/53 (79%) | 42/45 (93%) |
| reported | 62/76 (82%) | 42/53 (79%) | 42/45 (93%) |

The rules disagree on 0 letters.

## Letters the reported rule gets wrong

| paper | letter | read as | human | reported | measured |
|---|---|---|---|---|---|
| 10.1016_j.aej.2025.03.003 | `F` | `F` | not | index | index |
| 10.1016_j.aej.2025.03.003 | `delta` | `δ` | not | index | index |
| 10.1016_j.aej.2025.03.003 | `lambda` | `λ` | not | index | index |
| 10.1016_j.aej.2025.03.003 | `q` | `q` | not | index | index |
| 10.1016_j.aej.2025.03.003 | `vp` | `v` | not | index | index |
| 10.1016_j.aej.2025.03.003 | `x` | `x` | not | index | index |
| 10.1016_j.trb.2019.02.015 | `D` | `D` | not | index | index |
| 10.1016_j.trb.2019.02.015 | `dr` | `dr` | not | index | index |
| 10.1016_j.trb.2019.02.015 | `st` | `st` | not | index | index |
| 10.1016_j.trc.2024.104893 | `S` | `S` | index | not | not |
| 10.1016_j.trc.2024.104893 | `s_hat` | `ŝ` | not | index | index |
| 10.1016_j.tre.2026.104704 | `P` | `P` | index | not | not |
| 10.1016_j.tre.2026.104704 | `alpha` | `α` | index | not | not |
| 10.1016_j.tre.2026.104704 | `g` | `g` | not | index | index |
