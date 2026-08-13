# 10. Promotion takes declarations from a sidecar, and reports every failure by cause

- Status: accepted
- Date: 2026-08-13
- Deciders: Jörn Maurischat
- Extends: ADR-0008 (decision exports are a versioned contract); shares the
  posture of ADR-0006 / ADR-0007 (a fault of ours is never coverage information)

## Context and problem statement

ADR-0008 made the HITL decision export a contract with more than one producer
and taught `prisma.py` to *count* it. Nothing *consumed* it. Every verdict a
reviewer recorded on the phone produced JSON that no code turned into a corpus
entry, so the corpus still consisted of the ten placeholder seed templates and
the review marathon had no output path.

Building that consumer surfaced a harder fact. Canonical LaTeX is algebra plus a
`%@` declaration block: index families, parameter shape and kind, variable domain
and role. A displayed equation in a published paper carries none of that. Those
facts live in the surrounding prose and the nomenclature table, which the Tier-2
MathML extraction never sees. Measured on the corpus: **0 of 10 156 extracted
formula units parse**, every one failing with `KeyError 'meta'`. The splitter is
not the cause; strip the `%@` block from a known-good canonical model and it
fails identically, and a clean `\min \sum_{i \in I} c_i x_i` fails too.

So a "decisions in, formulations out" function cannot exist as stated. Accepting
a formula asserts that *the extraction is faithful*; it does not supply the
symbol table, and no rewriting can recover information that is not in the input.
Three ways out were considered:

1. **Infer the declarations** from the accepted LaTeX (a symbol that appears
   under a `\sum` binder is an index, a capital letter is a parameter, ...).
   Rejected: it fabricates the very facts the model is judged on, and a wrong
   `domain=binary` produces a model that validates, solves, and is wrong.
2. **Extend the review UI** so every formula is reviewed together with its
   declarations. Rejected for now: declarations are per *paper*, not per formula,
   and folding a second, much slower task into the fast accept/reject loop would
   wreck the throughput that makes phone review work at all.
3. **Take the declarations from a sidecar**, keep the fast loop fast, and let
   promotion report exactly which papers are waiting on one.

## Decision

**Promotion assembles a candidate model from the decisions, takes the symbol
table from a per-paper declaration sidecar, and categorizes every failure.**

1. `corpusbuilder.promote` reads *both* export schemas through one function
   (`load_decisions`), deduplicating by `(paper_key, formula_id)` in sorted file
   order with last-verdict-wins, exactly as ADR-0008 specified for the tally. A
   third producer extends that one function and `prisma._iter_decisions`.
2. Accepted formulas contribute their extracted LaTeX; corrected ones contribute
   the reviewer's replacement, *including every part* of a multi-part split fix
   (`parts`, not just `note`); rejected and duplicate ones contribute nothing.
3. The symbol table comes from `corpus/declarations/<paper_key>.tex`, holding
   only `%@ index` / `param` / `var` / `obj` / `con` lines. The bibliographic half
   of the header (`meta`, `name`, `desc`, `prov`) is *generated* from the
   dossier, so no human retypes metadata the corpus already holds.
4. A paper with no sidecar fails with cause `missing_declarations` and gets a
   **fill-in-the-blank stub** at `corpus/declarations/<paper_key>.stub.tex`, with
   its symbols and constraint-row names already filled in and the modelling facts
   left as `?`. A stub used unedited fails loudly; it never promotes a guess.
5. Assembly performs exactly **one** algebraic rewrite: it drops an objective's
   own label (`\min Z = \sum ...` becomes `\min \sum ...`). Everything else is
   M1b's job or a reported failure. Objective rows are identified by the game's
   existing detector (`game.is_objective_latex`), not a second heuristic.
6. Every failure carries a cause, and every cause maps to a category.
   `extraction_error`, `outside_grammar` and `under_specified` are findings about
   the *source material*. `pipeline_incomplete` (unreviewed paper, unsorted
   paper, missing dossier, id conflict) is a finding about *us* and is reported
   separately, so a gap in our own workflow is never published as a property of
   the literature. Missing instance data and cross-solver disagreement are
   decided downstream and are listed in the report as not assessed here, rather
   than quietly missing from a taxonomy that claims to be complete.
7. Writes are guarded: a formulation is re-loaded from its own serialized bytes
   before it is published, and an entry id already claimed by a *different*
   `source_id` is refused rather than overwritten (re-promoting the same paper
   does overwrite, which is what makes the corpus regenerable).

## Consequences

- The review marathon now has an output path: decisions plus a sidecar produce a
  validated `Formulation` and a `ProvenanceRecord`, and `corpus/promotion.md`
  says precisely which papers are blocked and on what.
- **Declarations become explicit corpus work**, visible and countable, instead of
  an assumption hidden inside "ingest the accepted formulas". The promotion report
  is the worklist: the count of `missing_declarations` is the remaining effort.
- The failure taxonomy the paper's `sec:validation` promises is now produced by
  running code for the promotion stage, not asserted in prose.
- `promote` is the first `corpusbuilder` module that needs `lp2graph`, which it
  reaches through `railpminer._lp2graph` rather than a second path shim. The
  acquisition modules stay free of that dependency.
- The reviewer's fast loop is unchanged. The cost is a second, slower per-paper
  pass; if that proves to be the bottleneck, option 2 (declarations inside the
  review UI, seeded by the stub) is the natural follow-up.

## Follow-ups (not in this ADR)

- `prisma.py` still reports `included.per_cell_P1_P5` as `null` although the
  promotion report now consumes the same `paper_cells`.
- Venue quality tiers are `unranked` unless `corpus/venue_tiers.json` exists;
  ranking venues is an editorial act and was deliberately not automated.
- Nothing yet re-runs `prisma` after a promotion pass; the two artifacts are
  generated independently and can disagree until both are regenerated.
