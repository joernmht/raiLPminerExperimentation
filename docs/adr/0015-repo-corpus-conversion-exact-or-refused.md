# 15. Repo-corpus conversion is exact-or-refused (residue guard)

- Status: accepted
- Date: 2026-08-24
- Deciders: design by Claude, scope decision Jörn Maurischat 2026-08-13
  (build Formulations from STRUCTURED fields; effort redirected from the
  killed Term-schema extension)

## Context and problem statement

The lp2graph corpus (17 repos, 28 models) carries structured declarations
the paper LaTeX lacks, making it convertible without HITL. But a lenient
canonical parser can silently swallow malformed input — iteration produced
two early "conversions" whose Terms had silently lost factors.

## Decision

`corpusbuilder.repo_corpus` renders a canonical document per model from the
STRUCTURED fields only and accepts a conversion only when (1) `ingest_latex`
returns ok, (2) the result round-trips `lp2graph.loads`, and (3) a
residue/juxtaposition guard (`check_row`) finds nothing the parser silently
dropped. Every rewrite is exact or the model is refused with a recorded
cause; binder restrictions that must be widened are captured verbatim in the
constraint description and the sidecar metadata, never silently.

## Consequences

6 of 28 convert exactly; the 20 recorded failure causes (`\frac` scaling,
Iverson brackets, triple products, index functions) independently confirm
the flat-Term grammar boundary the paper corpus shows, and two silent
misparses were caught by the guard rather than published.
