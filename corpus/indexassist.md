# Index letters: the model as second annotator, leave-one-paper-out

Papers labelled: **5**, letters with a human verdict (unsure excluded): **76**. Model: `deepseek-v4-flash`. Date: 2026-09-20.
Every paper is judged with the other labelled papers as examples; the model never sees the rule's verdict.

| | agrees with the human |
|---|---:|
| rule, bind vs don't bind | 62/76 (82%) |
| model, bind vs don't bind | 68/76 (89%) |
| model, exact verdict (index/label/not) | 59/76 (78%) |
| rule, family (human said index and named one) | 34/43 (79%) |
| model, family | 36/43 (84%) |

## Routing (rule and model agree → accepted; else the human)

| | count |
|---|---:|
| accepted without the human | 65/76 (86%) |
| … of which right (bind, and family where both name one) | 53/65 (82%) |
| sent to the human | 11/76 (14%) |
| rule errors caught by the routing | 8/14 (57%) |
| model unsure | 0 |

## Per class

| class | n | rule bind ok | model bind ok | model exact | family n | rule fam ok | model fam ok | auto | auto ok | human | rule errors caught |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| binder+family | 33 | 32 | 32 | 32 | 32 | 27 | 28 | 32 | 27 | 1 | 0/1 |
| candidate/subscript | 11 | 10 | 9 | 7 | 1 | 0 | 0 | 10 | 9 | 1 | 0/1 |
| label/uppercase | 10 | 8 | 8 | 3 | 0 | 0 | 0 | 10 | 8 | 0 | 0/2 |
| prose+family | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 4 | 0 | 0/0 |
| table-family | 4 | 1 | 3 | 3 | 1 | 0 | 1 | 0 | 0 | 4 | 3/3 |
| capped+family | 3 | 2 | 2 | 2 | 2 | 2 | 2 | 3 | 2 | 0 | 0/1 |
| decorated+family | 3 | 3 | 3 | 3 | 3 | 1 | 1 | 3 | 1 | 0 | 0/0 |
| binder-family | 2 | 0 | 2 | 2 | 0 | 0 | 0 | 0 | 0 | 2 | 2/2 |
| juxtaposed/juxtaposed | 2 | 2 | 2 | 2 | 0 | 0 | 0 | 2 | 2 | 0 | 0/0 |
| multi-letter name-family | 2 | 0 | 2 | 1 | 0 | 0 | 0 | 0 | 0 | 2 | 2/2 |
| binder+family/UPPER | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0/1 |
| prose+family/UPPER | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 1/1 |

## Per paper

| paper | labelled | auto | human | gated | examples | api calls |
|---|---:|---:|---:|---:|---:|---:|
| 10.1016_j.aej.2025.03.003 | 17 | 15 | 5 | 0 | 4 | 1 |
| 10.1016_j.trb.2014.05.005 | 13 | 11 | 2 | 0 | 4 | 1 |
| 10.1016_j.trb.2019.02.015 | 15 | 12 | 3 | 0 | 4 | 1 |
| 10.1016_j.trc.2024.104893 | 15 | 14 | 2 | 0 | 4 | 1 |
| 10.1016_j.tre.2026.104704 | 16 | 17 | 3 | 0 | 4 | 1 |

## Every letter

| paper | letter | class | rule → family | model → family | human → family | route |
|---|---|---|---|---|---|---|
| 10.1016_j.aej.2025.03.003 | `F` | binder+family/UPPER | index → S | index → S ✗ | not → S | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `L` | label/uppercase | label → – | not → – | label → – | auto (both do not bind) |
| 10.1016_j.aej.2025.03.003 | `U` | label/uppercase | label → – | not → – | label → – | auto (both do not bind) |
| 10.1016_j.aej.2025.03.003 | `delta` | table-family | index → – | not → – | not → – | human (bind disagreement) |
| 10.1016_j.aej.2025.03.003 | `k` | binder+family | index → K | index → K | index → K | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `kp` | binder+family | index → K | index → K | index → K | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `l` | binder+family | index → N | index → N | index → N | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `lambda` | binder-family | index → – | not → – | not → – | human (bind disagreement) |
| 10.1016_j.aej.2025.03.003 | `lp` | decorated+family | alias → N | index → N | index → N | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `m` | binder+family | index → P | index → P | index → P | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `mp` | binder+family | index → P | index → P | index → P | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `q` | table-family | index → – | label → – | label → – | human (bind disagreement) |
| 10.1016_j.aej.2025.03.003 | `r` | binder+family | index → R | index → R | index → R | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `s` | binder+family | index → S | index → S | index → S | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `v` | binder+family | index → N | index → N | index → N | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `vp` | binder+family | index → p | index → p ✗ | not → p | auto (both bind, same family) |
| 10.1016_j.aej.2025.03.003 | `x` | binder-family | index → – | not → – | not → – | human (bind disagreement) |
| 10.1016_j.trb.2014.05.005 | `P` | label/uppercase | label → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.trb.2014.05.005 | `S` | label/uppercase | label → – | not → – | label → – | auto (both do not bind) |
| 10.1016_j.trb.2014.05.005 | `e` | table-family | index → – | index → E | index → E | human (rule has no family) |
| 10.1016_j.trb.2014.05.005 | `f` | binder+family | index → E | index → E | index → F | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `fp` | decorated+family | alias → E | index → E | index → f | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `i` | binder+family | index → E | index → E | index → E | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `j` | binder+family | index → E | index → E | index → E | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `k` | binder+family | index → E | index → E | index → E | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `m` | binder+family | index → Omega | index → Omega | index → M | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `mp` | decorated+family | alias → Omega | index → Omega | index → m | auto (both bind, same family) |
| 10.1016_j.trb.2014.05.005 | `p` | binder+family | index → Omega | index → P | index → P | human (family disagreement) |
| 10.1016_j.trb.2014.05.005 | `s` | candidate/subscript | candidate → – | label → – | not → – | auto (both do not bind) |
| 10.1016_j.trb.2014.05.005 | `t` | binder+family | index → T | index → T | index → T | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `B` | label/uppercase | label → – | label → – | not → – | auto (both do not bind) |
| 10.1016_j.trb.2019.02.015 | `C` | label/uppercase | label → – | label → – | not → – | auto (both do not bind) |
| 10.1016_j.trb.2019.02.015 | `D` | prose+family/UPPER | index → E | label → – | not → E | human (bind disagreement) |
| 10.1016_j.trb.2019.02.015 | `a` | binder+family | index → A | index → A | index → A | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `ap` | binder+family | index → A | index → A | index → A | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `app` | binder+family | index → A | index → A | index → A | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `dr` | multi-letter name-family | index → – | label → – | not → – | human (bind disagreement) |
| 10.1016_j.trb.2019.02.015 | `e` | binder+family | index → E | index → E | index → E | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `ep` | binder+family | index → E | index → E | index → A | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `epp` | binder+family | index → E | index → E | index → A | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `eppp` | binder+family | index → A | index → A | index → A | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `n` | juxtaposed/juxtaposed | juxtaposed → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.trb.2019.02.015 | `st` | multi-letter name-family | index → – | label → – | label → – | human (bind disagreement) |
| 10.1016_j.trb.2019.02.015 | `tr` | binder+family | index → TR | index → TR | index → TR | auto (both bind, same family) |
| 10.1016_j.trb.2019.02.015 | `x` | juxtaposed/juxtaposed | juxtaposed → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `K` | label/uppercase | label → – | not → – | not → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `N` | label/uppercase | label → – | not → – | not → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `S` | label/uppercase | label → – | not → – ✗ | index → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `e` | candidate/subscript | candidate → – | index → e ✗ | label → – | human (bind disagreement) |
| 10.1016_j.trc.2024.104893 | `j` | capped+family | index → J | index → J | index → J | auto (both bind, same family) |
| 10.1016_j.trc.2024.104893 | `n` | binder+family | index → N | index → N | index → N | auto (both bind, same family) |
| 10.1016_j.trc.2024.104893 | `np` | binder+family | index → N | index → N | index → N | auto (both bind, same family) |
| 10.1016_j.trc.2024.104893 | `npp` | prose+family | index → N | index → N | index → N | auto (both bind, same family) |
| 10.1016_j.trc.2024.104893 | `p` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `r` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `s` | binder+family | index → S | index → S | index → S | auto (both bind, same family) |
| 10.1016_j.trc.2024.104893 | `s_hat` | table-family | index → – | index → S ✗ | not → – | human (rule has no family) |
| 10.1016_j.trc.2024.104893 | `sp` | prose+family | index → S | index → S | index → S | auto (both bind, same family) |
| 10.1016_j.trc.2024.104893 | `t` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.trc.2024.104893 | `w` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.tre.2026.104704 | `P` | label/uppercase | label → – | not → – ✗ | index → – | auto (both do not bind) |
| 10.1016_j.tre.2026.104704 | `alpha` | candidate/subscript | candidate → – | label → – ✗ | index → [0, 1) | auto (both do not bind) |
| 10.1016_j.tre.2026.104704 | `g` | capped+family | index → G | index → G ✗ | not → G | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `i` | capped+family | index → I | index → I | index → I | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `j` | binder+family | index → J | index → J | index → J | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `l` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.tre.2026.104704 | `m` | candidate/subscript | candidate → – | not → – | label → – | auto (both do not bind) |
| 10.1016_j.tre.2026.104704 | `n` | binder+family | index → R | index → R | index → R | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `omega` | binder+family | index → Omega | index → Omega | index → Omega | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `p` | binder+family | index → P | index → P | index → P | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `q` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
| 10.1016_j.tre.2026.104704 | `r` | binder+family | index → R | index → R | index → R | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `r_tilde` | prose+family | index → R | index → R | index → R | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `rp` | binder+family | index → R | index → R | index → R | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `rpp` | prose+family | index → R | index → R | index → R | auto (both bind, same family) |
| 10.1016_j.tre.2026.104704 | `u` | candidate/subscript | candidate → – | label → – | label → – | auto (both do not bind) |
