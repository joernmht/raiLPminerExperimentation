# 12. Deterministic algebra normalization spends only declared knowledge

- Status: accepted
- Date: 2026-08-24
- Deciders: design by Claude, sprint direction Jörn Maurischat
- Extends: ADR-0001 (determinism boundary), ADR-0010 (declaration sidecar)

## Context and problem statement

Tier-2 MathML writes products by juxtaposition (`q \pi_{ij} x_{ij}`) and
multi-letter identifiers as spaced letters (`t r_{e}` meaning `tr_e`); Greek
commands and `\times` are outside the canonical identifier/operator grammar.
Deciding "product or identifier" is impossible lexically — but it is fully
determined GIVEN the paper's declared symbol table, which ADR-0010's sidecar
states explicitly.

## Decision

Two deterministic layers, placed by what knowledge they need:

1. **Context-free rewrites live in lp2graph M1b** (versioned rule table,
   `REWRITE_RULES_VERSION`): Greek and unicode-Greek identifiers →
   `\mathit{name}`, `\times` → `\cdot`, alongside the existing underset/
   unicode rules. Bijective, meaning-preserving, corpus-evidence-driven.
2. **Context-dependent rewrites live in `corpusbuilder.algebra`,** applied at
   promotion assembly where the sidecar is in hand: spaced letters matching a
   declared multi-char name merge to `\mathit{name}` (longest match wins);
   adjacency of two declared atoms (or number × declared) becomes `\cdot`.
   Script groups are masked; undeclared adjacency is left alone — the pass
   spends only knowledge the sidecar actually states.

## Consequences

Whole error classes closed without an LLM call (word-form and spaced
objectives, Greek-named papers, weighted `\times` objectives), shrinking the
non-deterministic share rung (c) must cover. The masked-scripts scanner and
the atom lookbehind (`(?<![\\A-Za-z])`) exist because a failed regex match
starting inside `\cdot`/`\min` consumes the true adjacency — a measured bug
class, now tested.
