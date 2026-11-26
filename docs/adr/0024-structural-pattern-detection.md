# ADR-0024: Named structural patterns are detected from structure, never from annotations

**Status:** accepted (2026-11-26)

## Context

The WL similarity (`corpusbuilder.wlcluster`) says how alike two whole models
are. It cannot say what a model contains: a knapsack row inside a large
railway model is diluted by everything around it, and the WL node and edge
labels (`cls|subtype`, `type|role`) leave out the comparator and the term
signs, so `sum a x <= b` (knapsack) and `sum a x >= b` (covering) hash the
same. The question "which textbook building blocks is this model made of?"
needs a different instrument.

The canonical models also carry annotations that look like the answer: the
constraint `kind` (`capacity`, `set_packing`, ...) and the `domain_class`
facets. They come from extraction and naming, so a detector that reads them
would find what someone wrote, not what the structure is.

## Decision

`corpusbuilder.patterns` detects a fixed library of named patterns
(`patterns-2026.11.1`) from structure only:

- Every constraint is brought into one normal form, `sum a_k x_k (<=|>=|=) b`:
  variables left with their effective sign (side x sign x sign of a numeric
  coefficient), constants right, the row negated when every variable term is
  negative.
- Row patterns (partition / packing / covering, count bound, knapsack, flow
  conservation, periodic tension, big-M, difference, linking, bound) are
  predicates over that form, reading variable domain, parameter kind,
  aggregation, comparator and sign. Big-M is the declared `big_m` kind or the
  M(1 - y) signature (the same parameter as a binary's coefficient and as a
  constant in the row).
- Model patterns (knapsack problem, assignment, generalized assignment, set
  covering / partitioning / packing, disjunctive scheduling, PESP) combine row
  hits on the same variable with the objective sense and its terms.
- The annotations are printed next to each hit as a cross-check and are never
  inputs; `tests/test_patterns.py` asserts that relabelling a row does not
  change the result.

Output: `corpus/patterns/patterns.{json,md}`.

## Consequences

- The detector is a hand-checkable list; it does not change the WL numbers
  (Paper 1 §5.1) or any promoted model.
- It inherits canonicalization losses: in the arc models the in-arc and
  out-arc sums canonicalize to the same set, so an in/out degree pair is
  reported as "degree pair", not by direction.
- A matrix coefficient against a right side of 1 is read as an incidence
  matrix (packing row), against any other constant as a weight (knapsack).
- Adding the comparator and sign to the WL labels is a separate decision: it
  would change published numbers.
