# 11. Assisted resolution (rung c) is a pipeline stage whose gate is the parser

- Status: accepted
- Date: 2026-08-24
- Deciders: Jörn Maurischat (LLM-in-the-loop directive, 2026-08-23); design by Claude
- Extends: ADR-0008 (decision exports are a versioned contract), ADR-0010
  (promotion needs a declaration sidecar); implements paper §3.3.2 rung (c)

## Context and problem statement

ADR-0010 established that promotion needs human-supplied artifacts (verdicts,
symbol tables, declaration sidecars) and left them to the review game. At
corpus scale (238 papers, ~9 000 symbol pairs) the human pass is the
critical-path bottleneck; the paper's staged-resolution ladder names an
LLM-assisted rung (c) for exactly this, provided its output is marked and
never trusted outright.

## Decision

`corpusbuilder.assist` runs the reviewer's job as four stages, each writing
ONLY the formats ADR-0008/0010 already define: (a) triage verdicts + P1–P5
cell, (b) symbol kinds, (c) declaration sidecar fill, (r) row repair. Rules:

1. **The parser is the gate.** A stage-R rewrite is adopted only if a
   deterministic re-probe of the assembled row passes `ingest_latex`; a
   sidecar is written only if it passes token-level validation. The model
   proposes; the parser decides.
2. **Deterministic evidence wins.** Stage (b) may not contradict binder or
   domain-row facts; the validator rejects such replies.
3. **Failure causes route to the stage that owns them.** Body causes → (r),
   sidecar causes → (c), multiple objectives → (a). `no_objective` is
   TERMINAL: re-triage was measured to flip accepted rows to rejected,
   relabelling an `under_specified` finding as `extraction_error` — the loop
   must never launder one taxonomy category into another.
4. **Every artifact is marked non-deterministically sourced** (export
   `source` field, sidecar header) and lives in the game's schema, so a
   human verdict supersedes it under ADR-0008's last-wins rule.
5. **Replies are cached by payload hash including the model id**; backends
   (DeepSeek API, headless claude CLI) never share cached replies.

## Consequences

Symbol coverage rose from 31 % (deterministic prefill) to 71 %, formulas at
β = 1 from 11.5 % to 76.3 % — reported as the assisted tier, never merged
into the deterministic numbers. Measured operational facts: on a thinking
model, `max_tokens` caps reasoning + content together (a cap the reasoning
exhausts returns zero content — cap only non-thinking stages); repair
batches above ~12 rows provoke truncated replies (chunk and accept partial
coverage). Known defect, disclosed: 119 of 654 corrections (18 %) flagged as
possibly dropping trailing parts; detectable by relation counting.
