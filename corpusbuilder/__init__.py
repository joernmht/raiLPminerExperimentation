"""Corpus construction for *LP Mining with LP2Graph* (Paper 1).

This package acquires published LP/MILP rescheduling papers and turns each into a
**dossier** — a per-paper documentation file recording where the paper came from,
what cites it and what it cites, and the formulas extracted from it (with their
provenance and human-review status).

Design boundary (determinism): acquisition talks to live external services
(OpenAlex, arXiv, ScienceDirect) and is therefore **not** part of the
deterministic forward pipeline. It is a one-time *materialization* that writes
pinned, hashed, timestamped artifacts to disk; the ``railpminer`` pipeline then
runs byte-deterministically over those frozen files. Every dossier records its
retrieval date and a content hash of the source artifact so a run is auditable.

Formula extraction follows a deterministic-first ladder:

1. **arXiv LaTeX source** (:mod:`corpusbuilder.arxiv`) — equations pulled
   straight from the ``.tex`` e-print; byte-exact, no OCR.
2. **Publisher structured XML** (Elsevier MathML; future) — deterministic
   MathML→LaTeX.
3. **PDF OCR** (future, optional) — math VLM, gated behind human review.
"""

from __future__ import annotations

from corpusbuilder.dossier import (
    CitationRef,
    Dossier,
    ExtractionMethod,
    FormulaRecord,
    SourceInfo,
    VerificationStatus,
)

__all__ = [
    "CitationRef",
    "Dossier",
    "ExtractionMethod",
    "FormulaRecord",
    "SourceInfo",
    "VerificationStatus",
]
