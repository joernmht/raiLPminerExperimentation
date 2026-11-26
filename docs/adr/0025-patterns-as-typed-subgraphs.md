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
