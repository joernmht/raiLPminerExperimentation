# ADR-0026: Declarations are repaired by rule, undoably, and the parser holds the veto

**Status:** accepted (2026-11-26)

## Context

Promotion needs a symbol table per paper (`corpus/declarations/<key>.tex`).
The assist stage filled 206 of them. They are complete enough to read and wrong
often enough to block rows: a variable declared as a scalar that every formula
indexes, a label variant (`t^{arr}`) that was never declared because the model
was shown the bare letter, `role=variable` where the schema says `primary`.
After lp2graph's structural row reader (lp2graph ADR-0018) the grammar was no
longer the wall; 2,300 of 5,870 rows stopped at an undeclared name and another
400 at a shape that disagreed with the formulas.

Everything needed to repair that is already on disk as a deterministic
artifact: the index reader's verdicts (ADR-0023), the notation tables and
defining sentences of the discovery records (ADR-0019, ADR-0020), and the
formulas themselves.

## Decision

`corpusbuilder.detdecl` repairs sidecars by rule. No model is called.

| rule | what it decides | from |
| --- | --- | --- |
| R1 metadata | values outside the schema vocabulary become its default | the schema |
| R2 index letters | `%@ letter i over=I`; families the formulas bind over; an index letter no formula binds ranges over an unnamed family `fam_i` | index reader (verdict `index`, or `alias` by decoration; never `alias` by position, never a capital) |
| R3 folded names | a declared `x_ij` whose suffix is index letters is the indexed `x` | the formulas |
| R4 shapes | the family tuple most uses write, position by position | the formulas |
| R5 missing names | a domain row (variable); a notation-table row, definition-list item or defining sentence (kind from its own words, else from the table's group header) | the paper |
| R6 kind | where the paper names the kind outright and the assisted line says the other one, the paper is followed | the paper |
| R7 deduction | *linearity*: what multiplies a declared variable is a parameter. *Non-vacuity*: a quantity set equal to an expression holding a variable is a variable; the one undeclared name of an inequality or objective that names no variable is a variable. To a fixpoint; a name both claim stays undeclared | the algebra |
| R8 variants | a labelled variant of a declared symbol that nothing above decides: variant of a parameter is a parameter; a bound or reference label (`min`, `max`, `nom`, `plan`) on a variable is a parameter; letters, numbers or direction words on a variable give a variable. Accents on variables and other label words decide nothing | the assisted sidecar, read at the granularity it was written |

Three properties hold for every rule:

1. **Undoable.** A changed line keeps its original in a `%det-was:` comment, a
   dropped line in `%det-del:`, added lines sit in one marked block.
   `detdecl --restore` returns every sidecar byte for byte; each run starts by
   undoing the last. The assisted sidecar is never lost.
2. **Attributed.** An added line's description says which rule and which
   artifact decided it and nothing else. It never quotes the paper: sidecars
   are committed, the full text is not ours to publish.
3. **Vetoed by the parser.** Per paper the rule sets are tried fullest first
   and promote itself (`promote_paper --partial`) scores each; the set under
   which most rows promote is kept, and the untouched assisted sidecar wins if
   it does better. A repair that costs a paper rows is rolled back for that
   paper. Nothing is tuned by hand.

Rules run in the order of their footing: what the paper states and what the
algebra implies are applied to a fixpoint first, and only then R8, so a variant
never takes a kind that later evidence would have decided otherwise.

A paper with decisions and formulas but **no** assisted sidecar gets one
written by the rules alone, starting from nothing but the objective's sense.
Such a file opens with a `DETERMINISTIC ONLY` line, is kept only if the paper
then promotes, and is removed by `--restore`.

R7 and R8 are inferences, not readings, and are labelled as such. R7 rests on
two properties a linear program has by definition. R8 has no such footing; it
is included only in the three cases where the assisted sidecars themselves
agree with it, measured on the 1,201 variants they declare
(`detdecl.VARIANT_AGREEMENT`): 94 % for variants of parameters, 100 % for
bound labels on variables, 85 % for letters, numbers and direction words on
variables. That is agreement with an independent, unconfirmed source, not a
precision. Accented variants of variables agree 51 % and are left alone.

The objective row is read by a head grammar (`corpusbuilder.objective`):
label material, operator, decision variables under the operator, expression,
"s.t." tail. Component objectives a combined objective names
(`min Z = a_1 Z_1 + a_2 Z_2` next to `min Z_1 = ...`) become definitions of
their labels, and a restated objective stops being a second one. A paper with
several unrelated objectives still fails `multiple_objectives`.

## Consequences

- The yield calculation is unchanged: canonical rows of promoted papers over
  the candidate rows of all papers with decisions, from `promote --partial`.
- `corpus/detdecl.{json,md}` report the rule set kept per paper, the lines per
  rule, and every used name still undeclared with the reason (no evidence, no
  shape, an index letter used as a value).
- Better objective detection moved 13 papers from `no_objective` and 11 from a
  single (wrong) objective to `multiple_objectives`. The gate did not change;
  the count did, and it is a finding about how papers state several models.
- Human decisions from the discovery round overrule every rule here: a
  confirmed index letter or kind goes into the sidecar above the marked block
  and the rules never rebind a declared symbol.
- R7 misdeclares a variable as a parameter when a row is bilinear in two
  variables of which one is undeclared. The corpus has about 40 such rows; the
  per-rule audit (lab issue #3) should sample R7 and R8 lines first.
