# PRISMA tally — corpus construction (LP Mining with LP2Graph)

Single source of truth for every `n =` in the paper. Regenerate with
`python -m corpusbuilder prisma`. Deterministic: same frozen corpus ⇒ same tally.

## Identification

- Database search (OpenAlex, 6 queries, ≥ 30 citations, de-duplicated): **43** records.
- Citation searching (snowball over the 43 seeds): **2899** unique neighbours; **958** recommended to screen, 1941 excluded (< 2 seed links and not topical & well-cited).
- **Total identified: 2942.**

## Screening (topical, over the database records)

- Screened: **43**.
- Excluded — off-domain venue (clinical/medical false positives): **6**.
- Topical, carried to eligibility: **37** (86% precision).

## Eligibility — full-text retrieval + deterministic extraction

- Sought for retrieval: **37**.
- Full text retrieved (entitled): **18** (49% of topical).
- Metadata only (not entitled): 2.
- Full text unavailable (no entitled XML / no arXiv source): 17.
- Extraction succeeded: **17** of 18 (94%); 1 retrieved but no extractable LP.
- **Formula records mined: 708** (≈ 39 per retrieved full text).

## Included

- Validated canonical LP2Graph formulations: **10** (10 with provenance).
- Solvable instances with a published optimum (external-fidelity check): 5.

## Yield (why the pipeline pays off)

- A 2942-record identification sweep is distilled to **10** validated formulations.
- The deterministic-first ladder mines **708** machine-checked formula records from **18** full texts — **no OCR, no hand transcription** — i.e. ≈ 39 formulas per paper.
