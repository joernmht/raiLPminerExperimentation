# 13. Partial promotion measures row coverage instead of vetoing papers

- Status: accepted
- Date: 2026-08-25
- Deciders: design by Claude under deadline direction from Jörn Maurischat
- Extends: ADR-0010 (promotion reports every failure by cause)

## Context and problem statement

Whole-paper canonicalization is a conjunction over 20–50 rows: one stubborn
row vetoes the paper. Three full corpus sweeps ended 0/228 promoted while
per-row repair kept succeeding — the honest distance between published
notation and the canonical grammar, but also zero canonical output from
9 000 mostly-fine rows.

## Decision

`promote --partial`: when the full document fails, keep the objective plus
every constraint row that parses in isolation, re-assemble and re-validate
the subset, and promote it with the exclusions recorded per row (name,
formula id, parser message) in the provenance (`record.partial`). A partial
model is weaker, never silently wrong: coverage is a measured number on the
record, and the full-promotion count (still 0/228) remains the headline
honesty figure. The objective is non-negotiable — no objective, no partial.

## Consequences

8 papers promote at 17–79 % row coverage (median 46 %), giving the corpus
its first dossier-backed canonical models while keeping the failure taxonomy
intact. Downstream consumers must check `partial` before treating a
formulation as a complete model of its paper.
