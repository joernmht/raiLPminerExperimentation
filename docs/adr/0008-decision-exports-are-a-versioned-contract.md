# 8. HITL decision exports are a versioned contract, and the tally must reconcile against a total

- Status: accepted
- Date: 2026-08-11
- Deciders: Jörn Maurischat
- Extends: ADR-0004 (PRISMA tally as a versioned corpus artifact); shares the
  posture of ADR-0006 / ADR-0007 (a fault is never coverage information)

## Context and problem statement

ADR-0004 made `corpus/prisma.json` the single source of truth for every `n =`
number in the paper: derived deterministically from the committed corpus
artifacts, never hand-tallied. The *human-in-the-loop review* box of that flow —
accepted / corrected / rejected / unreviewed — is fed by decision files exported
by the reviewer into `corpus/decisions/`.

That directory does not exist yet. No reviewer has exported anything, so the
HITL branch of `prisma.py` had never once executed against real input, and no
test covered it. Meanwhile the *producer* moved: the review UI is now
`corpusbuilder/game.py`, which writes

```json
{"schema_version": "game-decisions-1",
 "formula_decisions": [{"paper_key": "…", "decisions": [{"id": "…", "status": "…"}]}]}
```

while the consumer still read the shape of the older single-paper
`review_view.py` export:

```python
for dd in json.loads(Path(f).read_text()).get("decisions", []):
```

For a game export `.get("decisions", [])` returns `[]`. The loop body never
runs. And because the branch *also* reset `unreviewed` to `0` on entry ("we have
decision files now, so we will count them"), the first export a reviewer ever
dropped in would have produced:

    accepted 0 · corrected 0 · rejected 0 · unreviewed 0

for a corpus of 8 957 candidate formulations — all four counts wrong, no error,
no warning, straight into `prisma.md` and `prisma_macros.tex` and from there
into the paper. Three further defects sat behind the same branch:

- **`duplicate` was unmodelled.** The game has four terminal verdicts
  (✓ accepted, ✎ corrected, ⧉ duplicate, ✗ rejected). `duplicate` would have
  landed in a dict key that nothing renders — a decision silently deleted.
- **`unreviewed` counted only what the files said.** The game omits papers
  nobody has opened (`if (!d || !Object.keys(d).length) continue;`), so the
  outstanding work would have been under-reported by however many papers were
  untouched — which, early in review, is nearly all of them.
- **Re-exports double-counted.** Exports are per-device and per-day
  (`game_decisions_<date>.json`); the tally summed across files with no key, so
  exporting twice counted every decision twice.

## Decision

**Treat the decision export as a versioned interface with more than one
producer, and make the tally reconcile against a known total rather than
trusting the files to be complete.**

1. `_iter_decisions(payload)` normalises **both** known export schemas — the
   multi-paper `game-decisions-1` (`formula_decisions[].decisions[]`) and the
   single-paper `review_view` shape (top-level `decisions[]`) — into a stream of
   `(paper_key, formula_id, status)`. New producers extend this one function.
2. Verdicts are **deduplicated by `(paper_key, formula_id)`**, not counted per
   record. Files are read in sorted-name order and the last verdict wins, so a
   corrected re-export supersedes rather than duplicates, and the tally is
   deterministic regardless of the order the paths are handed in.
3. **`unreviewed` is the residual**, `formulas_total - decided`, never a count of
   records that happen to say `"unreviewed"`. The tally therefore always sums to
   the corpus size, which is the property that makes it checkable at all.
4. `duplicate` is a first-class box, rendered in `prisma.md` and emitted as
   `\prismaHitlDuplicate`.
5. A status outside the modelled four is **reported, not dropped**: it appears
   under `unrecognised_status` in `prisma.json` and is called out in `prisma.md`.
   This is ADR-0006/0007's rule applied to the review arm — an unmodelled verdict
   is a gap in our vocabulary, and a gap must be visible in the artifact.
6. The five HITL counts get `\newcommand` macros like every other box, so the
   paper never transcribes a review number by hand.

## Consequences

- The HITL arm now has tests (`tests/test_prisma.py`, 9 cases) covering both
  schemas, the residual, `duplicate`, re-export dedup, supersession, per-paper id
  scoping, unknown statuses and order-independence. It was previously the only
  part of the flow with no coverage — and, not coincidentally, the only part
  that was broken.
- `prisma_macros.tex` gains five commands; the existing counts are unchanged
  (verified: regenerating moved no pre-existing number), so nothing already
  written into the paper shifts.
- Adding a third review producer means extending `_iter_decisions` and adding a
  schema case to the tests — a deliberate, small, single-point cost.
- The tally is still only as good as `formulas_total`; if dossier extraction
  changes, `unreviewed` moves with it. That is correct — but it means the review
  numbers are not stable until the corpus is frozen.

## Follow-ups (not in this ADR)

- The game's `paper_cells` (P1–P5 domain/activity sorting) are exported but not
  yet consumed; `included.per_cell_P1_P5` remains `null`.
- PRISMA 2020 wants *records excluded at screening* as its own box. The current
  flow reports `flagged_off_topic_in_corpus` post-hoc over retrieved dossiers,
  which is not the same transition and should be modelled properly before the
  diagram is drawn.
