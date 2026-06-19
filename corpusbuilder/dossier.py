"""The per-paper **dossier** — Paper 1's "search documentation file".

A :class:`Dossier` is the single record for one source paper. It captures three
things the user asked corpus construction to document:

* **Source** — where the paper came from (DOI/arXiv/OpenAlex id, venue,
  publisher, the API used, the local artifact path + SHA-256, entitlement, and
  the retrieval date), so the provenance of every paper is unambiguous.
* **Citations in both directions** — the works this paper *references*
  (cited-by-this) and the works that *cite* it (citing-this), each with a
  citation count and the source of the citation edge.
* **Formulas** — each extracted formula with its page span, LaTeX, optional
  MathML, the extraction *method*, and a human *verification status*.

Dossiers serialize to JSON (the machine record) and render to Markdown (the
human-readable documentation file). Both live next to the corpus artifacts.
"""

from __future__ import annotations

import json
import re
from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class ExtractionMethod(str, Enum):
    """How a formula's LaTeX was obtained — recorded per formula for provenance."""

    arxiv_tex = "arxiv-tex"  # pulled byte-exact from arXiv .tex source (Tier 1)
    mathml = "mathml"  # deterministic MathML->LaTeX from publisher XML (Tier 2)
    ocr = "ocr"  # math VLM on a PDF crop (Tier 3, non-deterministic)
    llm = "llm"  # LLM transcription/suggestion (DeepSeek/Claude, temp 0)
    human = "human"  # typed or corrected by a human reviewer


class VerificationStatus(str, Enum):
    """Where a formula sits in the human-in-the-loop review."""

    unreviewed = "unreviewed"
    accepted = "accepted"  # reviewer confirmed the extraction verbatim
    corrected = "corrected"  # reviewer edited the LaTeX
    rejected = "rejected"  # not a real/usable formula


class FormulaRecord(BaseModel):
    """One formula extracted from a paper, with provenance and review state."""

    id: str  # stable within a dossier, e.g. "eq-0007"
    label: str | None = None  # the paper's own number, e.g. "(3)"
    page_start: int | None = None
    page_end: int | None = None
    latex: str = ""
    mathml: str | None = None
    method: ExtractionMethod
    status: VerificationStatus = VerificationStatus.unreviewed
    confidence: float | None = None  # extractor/LLM confidence if available
    source_file: str | None = None  # which .tex/.xml/page it came from
    note: str | None = None


class CitationRef(BaseModel):
    """A neighbour in the citation graph (a reference of, or a citer of, the paper)."""

    title: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    year: int | None = None
    cited_by_count: int | None = None
    source: str = "openalex"  # provenance of this citation edge: openalex | scopus | crossref


class SourceInfo(BaseModel):
    """Identity and acquisition provenance of the paper itself."""

    title: str
    doi: str | None = None
    arxiv_id: str | None = None
    openalex_id: str | None = None
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    publisher: str | None = None
    cited_by_count: int | None = None  # OpenAlex count
    scopus_cited_by_count: int | None = None  # Scopus count (authoritative cross-check)
    # Provenance of the artifact we actually pulled (None until materialized):
    api: str | None = None  # openalex | arxiv | sciencedirect
    retrieved: str | None = None  # ISO date string, passed in explicitly (no Date.now in forward path)
    file_path: str | None = None  # local PDF/XML/tex artifact
    file_sha256: str | None = None
    entitlement: str | None = None  # open-access | tud-subscription | none | unknown
    landing_url: str | None = None


def paper_key(source: SourceInfo) -> str:
    """Filesystem-safe stable key for a paper (prefers DOI, then arXiv, then OpenAlex)."""
    raw = source.doi or source.arxiv_id or source.openalex_id or source.title
    return re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("_")[:120]


class Dossier(BaseModel):
    """The full record for one source paper."""

    schema_version: str = "dossier-1"
    source: SourceInfo
    references: list[CitationRef] = Field(default_factory=list)  # works this paper cites
    cited_by: list[CitationRef] = Field(default_factory=list)  # works citing this paper
    references_count: int | None = None  # total per the source (may exceed len(references))
    cited_by_count: int | None = None  # total per the source (may exceed len(cited_by))
    formulas: list[FormulaRecord] = Field(default_factory=list)

    @property
    def key(self) -> str:
        return paper_key(self.source)

    @property
    def formula_page_range(self) -> tuple[int, int] | None:
        """First and last page on which any formula appears (None if unknown)."""
        pages = [p for f in self.formulas for p in (f.page_start, f.page_end) if p is not None]
        return (min(pages), max(pages)) if pages else None

    # -- persistence --------------------------------------------------------

    def save(self, directory: str | Path) -> tuple[Path, Path]:
        """Write ``<key>.json`` and ``<key>.md`` into ``directory``; return both paths."""
        d = Path(directory)
        d.mkdir(parents=True, exist_ok=True)
        json_path = d / f"{self.key}.json"
        md_path = d / f"{self.key}.md"
        json_path.write_text(
            json.dumps(self.model_dump(mode="json"), indent=2, ensure_ascii=False, sort_keys=False)
            + "\n",
            encoding="utf-8",
        )
        md_path.write_text(self.to_markdown(), encoding="utf-8")
        return json_path, md_path

    @classmethod
    def load(cls, json_path: str | Path) -> Dossier:
        return cls.model_validate_json(Path(json_path).read_text(encoding="utf-8"))

    # -- human-readable documentation file ----------------------------------

    def to_markdown(self) -> str:
        s = self.source
        out: list[str] = []
        out.append(f"# {s.title}\n")

        out.append("## Source\n")
        rows = [
            ("Authors", ", ".join(s.authors) if s.authors else None),
            ("Year", s.year),
            ("Venue", s.venue),
            ("Publisher", s.publisher),
            ("DOI", s.doi),
            ("arXiv", s.arxiv_id),
            ("OpenAlex", s.openalex_id),
            ("Cited by (OpenAlex)", s.cited_by_count),
            ("Cited by (Scopus)", s.scopus_cited_by_count),
            ("Acquired via", s.api),
            ("Retrieved", s.retrieved),
            ("Entitlement", s.entitlement),
            ("Artifact", s.file_path),
            ("SHA-256", s.file_sha256),
            ("Landing URL", s.landing_url),
        ]
        for label, value in rows:
            if value is not None and value != "":
                out.append(f"- **{label}:** {value}")
        out.append("")

        rng = self.formula_page_range
        out.append("## Formulas\n")
        if self.formulas:
            out.append(
                f"{len(self.formulas)} formula(s)"
                + (f", pages {rng[0]}–{rng[1]}" if rng else ", page span unknown")
                + ". Status legend: ☐ unreviewed · ✓ accepted · ✎ corrected · ✗ rejected.\n"
            )
            mark = {
                VerificationStatus.unreviewed: "☐",
                VerificationStatus.accepted: "✓",
                VerificationStatus.corrected: "✎",
                VerificationStatus.rejected: "✗",
            }
            out.append("| | id | label | pages | method | LaTeX |")
            out.append("|---|---|---|---|---|---|")
            for f in self.formulas:
                pages = (
                    f"{f.page_start}" if f.page_start == f.page_end else f"{f.page_start}–{f.page_end}"
                ) if f.page_start is not None else "?"
                latex = f.latex.replace("\n", " ").replace("|", r"\|")
                latex = (latex[:80] + "…") if len(latex) > 80 else latex
                out.append(
                    f"| {mark[f.status]} | {f.id} | {f.label or ''} | {pages} | {f.method.value} | `{latex}` |"
                )
        else:
            out.append("_No formulas extracted yet._")
        out.append("")

        out.append(f"## References — works this paper cites ({self.references_count or len(self.references)})\n")
        out.extend(self._cite_lines(self.references))
        out.append("")

        out.append(f"## Cited by — works citing this paper ({self.cited_by_count or len(self.cited_by)})\n")
        out.extend(self._cite_lines(self.cited_by))
        out.append("")
        return "\n".join(out)

    @staticmethod
    def _cite_lines(refs: list[CitationRef]) -> list[str]:
        if not refs:
            return ["_none recorded_"]
        lines = []
        for r in refs:
            bits = [r.title or r.doi or r.openalex_id or "?"]
            meta = []
            if r.year:
                meta.append(str(r.year))
            if r.cited_by_count is not None:
                meta.append(f"{r.cited_by_count} cites")
            if r.doi:
                meta.append(r.doi)
            if meta:
                bits.append(f"({', '.join(meta)})")
            lines.append(f"- {' '.join(bits)}")
        return lines
