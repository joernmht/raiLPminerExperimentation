# ADR-0027: The objective is a row like any other, and a recorded feature of the entry

**Status:** accepted (2026-11-27, Joern: "change the objective gate, if we collect that feature")

## Context

Partial promotion (`promote --partial`) kept the canonical subset of a paper's
rows, on one condition: the paper states exactly one objective and the grammar
reads it. A paper without that was not promoted at all, whatever its
constraints looked like. Measured at eb7db0d, row by row:

| where the parsing constraint rows were | papers | rows |
| --- | ---: | ---: |
| promoted | 100 | 1,340 |
| one objective, outside the grammar | 67 | 750 |
| several objectives | 25 | 302 |
| no objective among the accepted formulas | 19 | 197 |

The canonical grammar is linear. An objective that is quadratic, an
expectation, a function call `F(X, Z)`, or a weighted sum over quantities the
paper never defines, is outside it for good, and no rule will change that.
The constraint system of such a paper is as readable as any other: the gate
threw away 1,249 readable rows because of one row that was not.

## Decision

In partial promotion the objective is a row like any other. It is in the
model when the paper states exactly one and the grammar reads it; otherwise
the constraint system is promoted without an objective (lp2graph's
`Formulation.objective` is optional), and the objective rows are excluded
with their reason like any excluded row.

What the paper's objective is like is **collected as a feature**, per paper,
in `corpus/promotion.json` (`papers[].objective`) and in the provenance record
(`objective`):

- `status`: `canonical` (one objective, in the model), `outside_grammar`
  (one, the grammar cannot read it), `several`, `none`;
- `stated`: the number of objective statements after restatements and
  component definitions were resolved (`corpusbuilder.objective`);
- `rows`: per statement its row name, its sense, whether it parses on its
  own, and when it does not the failure class and the parser's message.

The report tabulates papers, promotions and rows by status (section "The
objective as a feature").

Three guards come with it:

1. **No objective is invented.** The synthetic probe objective that measured
   constraint rows of such papers is gone; rows are probed, and promoted,
   without one.
2. **A model decides something.** An entry whose kept rows name no decision
   variable (rows that relate data only) is not promoted
   (`semantic_invalid`).
3. **Several objectives stay several.** Each is recorded and probed, none is
   put into the model: choosing one would be a statement the paper does not
   make.

Rows that parse alone can fail together, because some readings are decided
for the whole document (the one family of a binder letter, the one shape of a
derived parameter). Promotion then gave up the whole paper. It now
re-assembles row by row and leaves out the row that breaks the document, with
the reason "parses alone, not with the rows before it".

Whole-paper promotion (without `--partial`) keeps its gate: a complete model
has exactly one objective (`no_objective`, `multiple_objectives`).

## Consequences

- The yield calculation is unchanged (rows included of promoted papers over
  the candidate rows of all papers with decisions). What changed is which
  papers are promoted; numbers before and after this ADR are not comparable
  without saying so.
- "Promoted" no longer implies "has an objective". Every consumer of
  `corpus/formulations` must handle `objective: null`; the feature says why.
  Analyses that need an objective filter on `status == "canonical"`.
- The corpus is no longer a corpus of linear programs in the strict sense: it
  holds linear constraint systems, some with a linear objective, some whose
  objective is recorded as non-linear, plural or absent. If the library is to
  describe these honestly its name ("LP" to graph) is too narrow; a rename
  toward mathematical programs is an open question, not decided here.
- `multiple_objectives` and `no_objective` remain causes of whole-paper
  promotion only.
