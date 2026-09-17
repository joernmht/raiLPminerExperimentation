# Experiment: sidecars + deterministic table/index declarations, promote --partial in scratch

Rewrite rules: baseline rewrite-2026.09.4, experiment rewrite-2026.09.4. The live corpus was not touched.

| measure | baseline (committed) | with table + index declarations |
|---|---:|---:|
| papers promoted | 18 | 18 |
| objective rows parsing | 23 | 23 |
| constraint rows parsing | 415 | 435 |
| rows probed | 4429 | 4385 |
| share of rows parsing | 0.0937 | 0.0992 |
| papers with ≥ half their rows | 3 | 3 |

| row failure class | baseline | with tables | delta |
|---|---:|---:|---:|
| quantifier: clause not understood | 1118 | 1112 | -6 |
| undeclared symbol (vocabulary) | 1011 | 964 | -47 |
| juxtaposed factor / residue | 398 | 389 | -9 |
| other | 393 | 388 | -5 |
| quantifier: tuple | 324 | 325 | +1 |
| label subscript | 162 | 158 | -4 |
| no comparator | 144 | 145 | +1 |
| superscript after subscript | 121 | 121 | +0 |
| subscripted-coef shape mismatch | 75 | 79 | +4 |
| binder: range | 69 | 68 | -1 |
| frac | 51 | 53 | +2 |
| binder: tuple | 51 | 50 | -1 |
| variable x variable (nonlinear) | 41 | 41 | +0 |
| undeclared coefficient (vocabulary) | 33 | 34 | +1 |
| quantifier: subscripted set | 11 | 11 | +0 |
| binder: subscripted set | 7 | 7 | +0 |
| parenthesised / text residue | 4 | 4 | +0 |
| unbalanced braces | 1 | 1 | +0 |
