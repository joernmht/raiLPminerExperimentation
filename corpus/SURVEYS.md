# Surveys & literature reviews in the corpus → expert-cluster baseline

**The idea (from the meeting, do not lose):** surveys and literature reviews in our
field already group/classify optimization models into the authors' own clusters
(taxonomies). Extract those **expert clusters** and **compare them against the clusters
LP2Graph induces**. The divergence between expert taxonomy and machine-induced structure
is a result in its own right — and the methodology already promises to use expert
classifications as an *interpretive anchor / sanity-check*, NOT as ground truth.

> This extracts a survey's **classification scheme** (its taxonomy tables/sections),
> which is a *different extraction task* from the formula-mining pipeline. Build it separately.

## Review/survey papers already IN the corpus (have dossiers)
| Cites | Year | Formulas | DOI | Title | Use |
|------:|-----:|:--------:|:----|:------|:----|
| 662 | 2014 | 19 | 10.1016/j.trb.2014.01.009 | An overview of recovery models and algorithms for real-time railway rescheduling (Cacchiani et al.) | **prime** — classifies recovery models |
| 97 | 2012 | 37 | 10.1016/j.sorms.2012.08.002 | A tutorial on fundamental model structures for railway timetable optimization | **prime** — explicitly about model structures |
| 56 | 2024 | 7 | 10.1016/j.tre.2024.103429 | Handling uncertainty in train timetable rescheduling: A review | review of methods |
| 30 | 2015 | 0 | 10.1007/s12469-015-0108-5 | A state-of-the-art realization of cyclic railway timetable computation | state-of-art |

(Two medical false positives — sleep/benzodiazepine — are part of the 28 off-topic
dossiers flagged by the topical screen; prune in review.)

## High-value survey neighbours NOT yet mined (snowball — add in wave-2)
| Cites | Year | Corpus links | DOI | Title |
|------:|-----:|:-----:|:----|:------|
| 778 | 1998 | **52** | 10.1287/trsc.32.4.380 | **A Survey of Optimization Models for Train Routing and Scheduling** (Cordeau et al.) — the obvious anchor |
| 669 | 2008 | 14 | 10.1016/j.tra.2008.03.011 | Transit network design and scheduling: A global review |
| 5789 | 1979 | 2 | 10.1016/s0167-5060(08)70356-x | Optimization and Approximation in Deterministic Sequencing and Scheduling (Graham et al.) — the classic α\|β\|γ scheduling taxonomy |

Also wanted (IEEE/non-Elsevier, citation-only — text/taxonomy not formulas):
the railway rescheduling survey cited as `ReschReview2023` (10.1109/tits.2015.2446985,
"A Survey on Problem Models and Solution Approaches to Rescheduling in Railway Networks").

176 relevant review-type neighbours exist in the snowball pool overall — screen them for
ones that actually propose a *classification*, not just a narrative summary.
