# ADR-0019: Bottom-up discovery — every mathematical element enters the pipeline

**Status:** accepted (2026-09-17)

## Context

Tier-2 extraction recorded one formula per `<ce:formula>` (display equations)
and discarded everything else. Measured over the 238 included papers, the
full-text XML holds 8,568 display maths but 81,439 mathematical elements in
total: 51,984 inline in the prose (14,331 statements such as domain
declarations and index memberships, 26,481 symbol mentions next to the words
that say what the symbol is), 20,036 in table cells, 315 in definition lists.
634 of the tables in 194 papers are the papers' own notation tables ("Set of
trains", "Generic train in T"). Declarations, index families and symbol
definitions live there, not in the displayed equations, which is why the
declaration-driven ingestion keeps hitting undeclared symbols and unbound
indices, and why the vocabulary round has been reconstructing by hand what the
papers state outright.

## Decision

1. **Discovery first** (`corpusbuilder.discover`): walk the XML once,
   deterministically, and record *every* `<mml:math>` element with a stable
   id, where it sits, its LaTeX (same node bridge as Tier-2), a structural
   class (statement / operator / symbol / expr) and, for prose elements, the
   surrounding text window and the kind words in it. Display elements keep
   the dossier's `eq-NNNN` id so earlier decisions still apply. Paragraphs
   are kept as segment lists; notation tables row by row.
2. **Indices second** (`corpusbuilder.indices`): before any symbol is typed,
   find the index letters and families from all of that evidence (binder
   pairs, capped ranges, tuple binders, prose memberships, notation rows),
   then raise the yield with two named rules, *decoration* and *position*,
   recorded as `alias` verdicts, never merged silently. Compare with the
   declaration sidecars so the report says which `%@ index` lines are dummy
   letters rather than families.
3. **The human sees the text** (`corpusbuilder.discovergame`): one page per
   paper with every element marked, the index table with the rule that
   decided each letter, and a `discover-decisions-1` export per paper. Nothing
   on the page is model-proposed.
4. **Kind evidence is kept, not interpreted yet**: the words near a symbol
   mention are stored on the record for the symbol-deconstruction stage.

## Consequences

- `corpus/discovery/`, `corpus/indices/` and `corpus/review/discover*` are
  gitignored (Elsevier TDM material); `corpus/discovery.{json,md}` and
  `corpus/indices.{json,md}` carry counts and names only.
- The promotion pipeline is unchanged by this ADR; the deconstruction stage
  that consumes confirmed indices and inline statements comes next and must
  keep existing gold labels importable (ids are stable: `eq-NNNN` for display
  rows, `m-NNNN` in document order for the rest).
- Re-running discovery is idempotent and byte-identical for identical XML.
