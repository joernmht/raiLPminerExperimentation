# `corpus/decisions/` — HITL review exports

Drop review exports here, unchanged:

* `game_decisions_<date>.json` — from the review game (`corpusbuilder.game`),
  schema `game-decisions-1`, many papers per file.
* `decisions_<paper_key>.json` — from the older single-paper `review_view`.

Both schemas are read by `corpusbuilder.prisma` (the PRISMA tally) and
`corpusbuilder.promote` (promotion to canonical formulations). Files are read in
sorted-name order and the **last verdict on a `(paper_key, formula_id)` wins**, so
re-exporting a day's work supersedes rather than duplicates it (ADR-0008).

Commit these: they are the provenance of every accept/correct/reject in the corpus.
