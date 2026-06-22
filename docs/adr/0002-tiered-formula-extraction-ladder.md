# 2. Tiered formula-extraction ladder (arXiv .tex → Elsevier MathML → OCR)

- Status: accepted
- Date: 2026-06-22
- Deciders: Jörn Maurischat

## Context and problem statement

Paper 1 mines *published* LP/MILP formulations. The formulas must be captured as
LaTeX faithful enough that lp2graph's codec can parse them into the canonical
model. Sources vary wildly in fidelity: an arXiv e-print ships the author's own
`.tex`; a publisher (Elsevier) ships structured MathML behind an entitlement; a
PDF-only paper offers nothing but pixels. Picking one extraction method would
either miss most papers (arXiv-only) or inject transcription noise everywhere
(OCR/LLM-only). How should extraction choose its source?

## Decision

Use a **tiered ladder**, highest-fidelity source first, stopping at the first
tier that yields formulas (`corpusbuilder/cli.py: cmd_dossier`):

1. **Tier-1 — arXiv `.tex`** (`arxiv.py`): the gold path. The author's source is
   pulled byte-exact and equation environments lifted verbatim
   (`ExtractionMethod.arxiv_tex`). Deterministic.
2. **Tier-2 — Elsevier ScienceDirect full-text MathML** (`elsevier.py` +
   `mathml.py`): structured publisher math, converted MathML→LaTeX through a
   vendored Node bridge (`ExtractionMethod.mathml`). Deterministic given the XML.
   Used only when Tier-1 produced nothing and the DOI is Elsevier.
3. **Tier-3 — OCR / VLM on a PDF crop** (`ExtractionMethod.ocr`, future):
   non-deterministic; explicitly a last resort, flagged so it never masquerades
   as a clean extraction.

Each formula records *which* method produced it (`FormulaRecord.method`) and a
human `VerificationStatus`, so downstream review and the paper's methods section
can report extraction provenance per formula.

## Consequences

- **Good:** maximises coverage without sacrificing fidelity where it is
  available; every formula's trustworthiness is self-describing.
- **Good:** deterministic tiers (1–2) dominate; the non-deterministic tier (3)
  is isolated and labelled.
- **Bad:** Tier-2 adds a Node.js + `npm install` runtime dependency (the
  MathML→LaTeX bridge) and Tier-2 needs an Elsevier entitlement (see ADR-0003).
- **Bad:** the "stop at first non-empty tier" rule means a paper on both arXiv
  and Elsevier never cross-checks the two; acceptable because Tier-1 is the
  author's own source.
</content>
