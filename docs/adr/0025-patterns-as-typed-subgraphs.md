# ADR-0025: Named structural patterns are typed subgraphs of the schema graph

**Status:** accepted (2026-11-26); refines ADR-0024

## Context

ADR-0024 detects named building blocks (knapsack, assignment, covering, ...)
by reading the canonical model row by row. Paper 1's claim is that every
analysis is derived from one typed graph, so the patterns should be stated
and found on that graph. Two things were missing from the schema graph:
literal constants had no node, no edge and no attribute (so `sum x = 1` and
`sum x = 0` were the same graph), and a row pattern must be *closed* (no
other term on the constraint), which plain subgraph matching cannot say.

## Decision

`corpusbuilder.graphpatterns` (`graphpatterns-2026.11.1`):

- lp2graph (1a5b246) stores the net literal right-hand side on each schema
  constraint node (`rhs_literal`) as data. Topology, WL labels and the
  isomorphism test are unchanged.
- **Term graph:** the schema graph with each operator node contracted into
  the edge it sits on; every term is one edge labelled with operator, summed
  indices, sign and coefficient class. The normal form (effective signs,
  comparator after negation) is written onto the edges and constraint nodes.
- **Row patterns are closed labelled stars** around a constraint node: the
  centre and *all* its term edges and variable leaves.
- **Model patterns are typed subgraphs** joining labelled stars at a shared
  variable node or at the objective node (9 pattern graphs for the 8 named
  model patterns), found by subgraph monomorphism (networkx VF2 on the
  multigraph, typed node and edge matching, injective matching of parallel
  edges). Big-M has a graph signature: a coefficient edge and a constant edge
  from one constraint to the same parameter node.
- The row detector of ADR-0024 stays as the reference oracle. On the 40
  canonical and generated models (146 rows) the two agree on every row and
  every model hit; `tests/test_graphpatterns.py` keeps them in lock step.
- The graph detector writes `corpus/patterns/patterns_macros.tex` for the
  paper, with the agreement counts.

## Consequences

- The paper can state patterns as graphs and draw them.
- The detector needs lp2graph >= 1a5b246 and refuses to run without
  `rhs_literal` instead of silently missing every exactly-one row.
- Contraction keeps operator semantics on the edge; a pattern can still ask
  for "summed over a shape index of X" (assignment variants).

## Addendum (2026-11-26): second group of patterns

Added in lock step to both detectors (agreement still 0/0 on 40 models):

- Row stars: `escape_row` (exactly / at least one with a separate 0-1 escape,
  e.g. train cancellation), `balance_row` (the same variable with both signs:
  stock recursion; rows whose rest is a `s+ - s-` deviation pair are excluded),
  `definition_row` (one variable alone, defined by the others). `bound_row` now
  also covers a bare `0 <= x` (no constant written).
- Block patterns, found by joining stars at one variable node (and the
  objective edge): `absolute_deviation` (two `>=` stars on z with all other
  term signs flipped), `min_max` (h >= w over more indices than h has, h
  optimized), `positive_part` (non-negative z >= t - p, penalized, defined by
  that star only; not an absolute deviation or min-max), `logic_or` /
  `logic_and` (two or more distinct 0-1 literals bounding one variable),
  `two_sided_bound` (lower and upper bound stars on one variable over the same
  quantifier sets).
- Canonical set: unmatched constraint nodes 37 -> 8.
