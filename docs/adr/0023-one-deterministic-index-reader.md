# ADR-0023: One deterministic index reader for the pages, the prefill and every stage

**Status:** accepted (2026-10-04)

## Context

Two pieces of code decided which letters of a paper are indices and which sets
are their families:

- `corpusbuilder.indices` (ADR-0019), over every math element of the paper
  (display, inline, notation tables, prose), with alias, label and
  multi-letter-name rules. The discovery pages propose its verdicts, and the
  human reliability sample (ADR-0022) measures it.
- `corpusbuilder.symbols.paper_evidence`, an older reader of binder scripts in
  the display formulas only. `corpusbuilder.resolution` counted its verdicts as
  the free index prefill (`\resIndexPrefill`, Paper 1 §3.3 rung a), and the
  promote stub, the review game and assist stage B used them as the symbols'
  deterministic kinds.

So the paper reported one rule and the lab measured another (coherence report
2026-09-22, PREFILL-UNMEASURED). Scored on the same 76 human-labelled letters
of 5 papers (`indexassist --rules`, 2026-10-04): the discovery rule agreed with
the expert on 62/76 (precision 42/53, recall 42/45), the binder reader on 56/76
(precision 35/45, recall 35/45), and the two disagreed with each other on 18
letters. The binder reader also read `k'` as `k`, split the two-letter index
name `tr` into the letters `t` and `r`, and took `\min`-subscripts and
set-builder braces for binders.

Measuring it uncovered a defect in the half of `paper_evidence` that stays: the
domain-row reader took memberships in calligraphic or bare sets for number
domains (`\forall n \in \mathcal{N}^{up}` as "n is an integer variable",
`\forall r \in \mathcal{R}^{dn}` as "r is real") and read the membership of a
binder whose operator comes after it (`\underset{n' \in N}{\sum}`) as a row of
its own. 49 of 428 domain rows were such misreadings, in 7 papers; because a
declared variable wins over an index, they also erased index letters.

## Decision

1. **The discovery rule is the only index reader.** `paper_evidence` takes the
   index letters and families from the paper's stored record
   (`corpus/indices/<key>.json`) and adds the domain rows; without a record it
   runs the same rule (`indices.analyse`) over the rows it is given, with less
   evidence. Every consumer passes the stored record: `resolution`, the promote
   stub, the review game's evidence codes, assist stage B's worklist.
   `binder_roles` stays only as a lister of the names a binder mentions.
2. **Names are mapped, not re-read.** The game tokenizer's names, which the
   reviewer's symbol tables and the resolution count use, drop primes and write
   Greek letters and accents as glyphs; `symbols.game_name` maps a discovery
   letter onto them (`kp` -> `k`, `s_hat` -> `ŝ`, `ell` -> `ℓ`). A name several
   discovery letters share is an index only when all of them bind: a wrong
   prefill is worse than none.
3. **The domain-row reader refuses** a membership inside a script group or a
   `\forall` clause, and accepts a number set only in blackboard or bold
   (`\mathbb{Z}`, `\mathbf{R}`) or as `\mathcal{R}^{+}`; a calligraphic or bare
   `B`, `N`, `Z`, `R` is how the corpus names index families. All 49 misread
   rows go, no genuine row is lost (`tests/test_symbols.py` pins both sides).
4. **The discovery rule sees `ℓ`.** The Unicode letter and `\ell` normalise to
   the letter `ell`; the rule could not see it before. 24 records change: 12
   papers gain the letter (in 3 of them it replaces a stray glyph letter `ℓ`),
   one position alias flips (`b` in cor.2009.03.022 now aliases to `A`), the
   rest change only in their row text.
5. `indexassist --rules` now scores the rule twice, as the pages propose it and
   as the resolution counts it; on the labelled sample the two readings agree
   on every letter.

## Consequences

- Resolution (lab artifacts, 238 papers): index prefill 1,101 -> 1,096,
  variable prefill 52 -> 49, typed pairs 6,406 -> 6,398, fully broken-down
  formulas 6,838 -> 6,450 (76.3% -> 72.0%), ready 5,798 -> 5,494 (64.7% ->
  61.3%), median symbols still to type for 80% of a paper 0 -> 7. Of the
  symbols that lost their type, the largest share are the binder reader's false
  prefills going away (`t` typed as an index in 67 formulas of
  cie.2023.109809 from one misread binder row; `tr` split into `t` and `r`;
  `\min`-subscripts and set-builder braces read as binders). Known misses of
  the discovery rule among them: multi-letter dummies such as `odw \in ODW`
  (11 symbols, 60 formula occurrences). Families the binder reader named and
  the discovery rule does not (52 symbols, 200 occurrences) are not yet sorted
  into misses and false prefills. The paper's frozen `resolution_macros.tex`
  (and the talk's "76.3% at beta = 1") still carry the old reader's numbers;
  re-copying is a paper decision.
- The reported prefill and the measured precision are now one rule, so §3.3's
  rung (a) can cite the reliability sample directly.
- Inputs of the review game and of assist stage B change for papers where the
  two readers differed, so their payload digests change: a re-run asks the
  model again instead of hitting the cache. Recorded decisions are not touched.
- Fingerprint domain counts lose the 49 false domain rows (7 papers); the
  feature clustering moves when it is next run.
- The human labels stay valid: the pages always proposed the discovery rule,
  and none of the five labelled papers is among the 24 whose records changed.
  Those 24 are the ones to re-check on the pages.
