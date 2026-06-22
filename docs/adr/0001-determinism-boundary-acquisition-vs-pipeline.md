# 1. Determinism boundary between corpus acquisition and the forward pipeline

- Status: accepted
- Date: 2026-06-22
- Deciders: Jörn Maurischat

## Context and problem statement

The paper's central claim is reproducibility: *same corpus + same versioned
config ⇒ byte-identical artifacts* (`test_run_is_deterministic` guards this).
But corpus **acquisition** is inherently non-deterministic — it hits live
APIs (OpenAlex, arXiv, Elsevier/Scopus), and citation counts and availability
change over time. How do we keep a hard determinism guarantee while still
acquiring corpus data from a moving external world?

## Decision

Draw an explicit **determinism boundary**. Two phases, two contracts:

- **Acquisition (`corpusbuilder/`)** — *allowed* to be non-deterministic.
  Talks to the network. It records, rather than hides, the moving parts: every
  dossier stamps a real **retrieval date** (`SourceInfo.retrieved`, passed in
  explicitly via `cli._today()`, never read inside a model) and a **SHA-256** of
  the exact artifact pulled (`SourceInfo.file_sha256`). The output is a set of
  *frozen files* under `corpus/`.
- **Forward pipeline (`railpminer/`)** — *must* be deterministic. It reads only
  the frozen files, sorts before every order-sensitive step
  (`corpus.load_corpus` sorts entries by formulation id), and contains no
  `Date.now`, RNG-without-seed, or set-iteration in its output path.

The retrieval date lives in the data, not in the code path, so re-running the
pipeline over a frozen corpus reproduces byte-identically regardless of when it
runs.

## Consequences

- **Good:** the determinism guarantee holds without pretending acquisition is
  reproducible; provenance (date + hash) is auditable per paper.
- **Good:** acquisition can be re-run/extended independently without perturbing
  published results, as long as the frozen corpus is unchanged.
- **Bad:** the boundary is a *convention* enforced by review + one test, not by
  the type system; a stray `datetime.now()` inside `railpminer/` would silently
  break it. Mitigation: keep all date/RNG inputs in `PipelineConfig` and the
  acquisition layer only.
</content>
