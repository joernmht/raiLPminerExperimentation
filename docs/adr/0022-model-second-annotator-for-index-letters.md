# ADR-0022: A small model is the second annotator for index letters; disagreement goes to the human

**Status:** accepted (2026-09-20)

## Context

The discovery round (ADR-0019/0020/0021) asks the human to decide, per paper,
every letter the index discovery found in a script position: index, label,
not an index, or unsure, plus the family the letter ranges over. 238 papers
carry 3,161 such letters. Measured on the first five labelled papers
(2026-09-19, 84 verdicts) the deterministic proposal is near-perfect where a
binder names the family (32/32 on bind vs don't bind) and weak in a few small
classes: binder without a family (0/3), notation-table letters (1/4, three
unsure), multi-letter names (0/2), uppercase letters proposed as indices
(0/2); the position alias was not seen. At the measured pace (6 to 13 minutes
per paper for the indices round alone) the whole corpus is about 30 hours of
one expert's time, and the rows at stake are concentrated: the top 40 papers
of the worklist hold 53% of them, the 86 papers with ten or more rows hold 84%.

The human labels do three jobs: they are the input of the stage-3 rewrite,
they are the reliability number of the paper (precision of each discovery rule
against an expert), and they are the only test set any automation can be
validated on. Only the first job scales with the number of papers.

## Decision

1. **A second annotator, not a labeller** (`corpusbuilder.indexassist`,
   assist stage `i`). A small model judges every letter of a paper from the
   same deterministic evidence the page shows (formulas carrying the letter,
   the paper's notation-table line, a prose window, the binder sets, use
   counts) and **never sees the rule's verdict**. It learns the house
   convention from the human's decided papers, given as examples; a paper is
   never its own example. Its reply passes a gate: verdict from the page's
   four, family only from the sets the paper names, everything else recorded
   as a problem and treated as unsure. Replies are cached like every assist
   stage; the sidecar (`corpus/assist/indexassist/`) quotes formulas and stays
   gitignored.
2. **A deterministic router decides who sees a letter.** Rule and model agree
   on bind vs don't bind, and on the family where both name one: the letter is
   accepted with `source: assist`. They disagree, the model is unsure, or an
   index has no family: the letter goes to the human's queue. Label and "not
   an index" both mean don't bind for stage 3, so they are one class for the
   router; the exact verdict is still recorded. Accepted letters are never
   counted as human verdicts (the PRISMA `decision_source` split applies).
3. **The human labels stay the test set.** Model verdicts are not shown on
   the discovery page until the reliability sample is labelled: about 30
   papers (the worklist top 25 plus 10 drawn from the tail with few rows at
   stake), and the tiny classes (table, multi-letter, juxtaposed, uppercase as
   index; about 190 letters) exhaustively through the letter walk. The
   evaluation is leave-one-paper-out (`--evaluate`), reported counts-only in
   `corpus/indexassist.{json,md}`.
4. **Family convention is an open instrument question, not an error class.**
   The rule reports the set a binder uses; the page asks for the set the paper
   declares for the letter. For tuple-bound letters ((i, j) ∈ E with i ∈ N)
   the two differ. Until the owner settles which one the page means, a
   rule/human family difference of that kind is reported separately.

## Consequences

- First measurement (2026-09-20, five papers, 76 letters with a verdict): rule
  62/76 on bind vs don't bind, model 68/76; the router accepts 65/76 without
  the human, 60/65 of them right at bind level (53/65 when the family must
  match too; six of the twelve misses are the convention of point 4, two are
  the expert writing the base letter as the family); 11/76 go to the human;
  8 of the rule's 14 errors are caught. The six uncaught errors are shared
  errors (rule and model agree with each other, not with the expert): the
  router reduces the queue, it cannot replace the reliability sample.
- Cost: one call per paper, about 18k input tokens with four example papers,
  15 seconds for five papers on the flash model with thinking off.
- The model's "why" strings are model text and never leave the gitignored
  sidecar.
