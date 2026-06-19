"""Offline tests for the corpusbuilder package (no network)."""

from __future__ import annotations

from corpusbuilder.arxiv import extract_equations_from_text
from corpusbuilder.dossier import (
    CitationRef,
    Dossier,
    ExtractionMethod,
    FormulaRecord,
    SourceInfo,
    VerificationStatus,
    paper_key,
)
from corpusbuilder.openalex import OpenAlexClient

_TEX = r"""
Some intro text.
\begin{equation}\label{eq:headway}
t_j - t_i \ge h \quad \forall (i,j) \in C
\end{equation}
% \begin{equation} this one is commented out \end{equation}
A display block:
\[ \sum_{i} x_i = 1 \]
\begin{align*}
a &= b + c \\
d &= e
\end{align*}
"""


def test_extract_equations_strips_comments_and_keeps_order() -> None:
    recs = extract_equations_from_text(_TEX, source_file="main.tex")
    assert len(recs) == 3  # the commented-out equation must NOT be picked up
    assert recs[0].id == "eq-0001"
    assert "t_j - t_i" in recs[0].latex
    assert recs[0].label == r"\label{eq:headway}"
    assert r"\label" not in recs[0].latex  # label stripped from the body
    assert recs[1].latex == r"\sum_{i} x_i = 1"
    assert recs[2].method is ExtractionMethod.arxiv_tex
    assert all(r.source_file == "main.tex" for r in recs)


def test_dossier_roundtrip_and_markdown(tmp_path) -> None:
    dossier = Dossier(
        source=SourceInfo(
            title="A MILP for Railway Rescheduling",
            doi="10.1016/j.trc.2017.06.018",
            year=2017,
            venue="Transportation Research Part C",
            cited_by_count=123,
            api="openalex+arxiv",
            retrieved="2026-06-19",
            entitlement="open-access",
        ),
        references=[CitationRef(title="Earlier work", year=2010, cited_by_count=50)],
        cited_by=[CitationRef(title="Later work", year=2020, cited_by_count=5)],
        references_count=1,
        cited_by_count=1,
        formulas=[
            FormulaRecord(id="eq-0001", latex="x+y=1", method=ExtractionMethod.arxiv_tex,
                          page_start=3, page_end=3, status=VerificationStatus.accepted),
        ],
    )
    json_path, md_path = dossier.save(tmp_path)
    assert json_path.exists() and md_path.exists()

    reloaded = Dossier.load(json_path)
    assert reloaded.model_dump() == dossier.model_dump()
    assert reloaded.formula_page_range == (3, 3)

    md = md_path.read_text(encoding="utf-8")
    for section in ("## Source", "## Formulas", "## References", "## Cited by"):
        assert section in md
    assert "10.1016/j.trc.2017.06.018" in md


def test_paper_key_is_filesystem_safe() -> None:
    key = paper_key(SourceInfo(title="x", doi="10.1016/j.trc.2017.06.018"))
    assert "/" not in key and key == "10.1016_j.trc.2017.06.018"


def test_openalex_mapping_from_fixture() -> None:
    work = {
        "id": "https://openalex.org/W123",
        "ids": {"openalex": "https://openalex.org/W123", "doi": "https://doi.org/10.1/abc"},
        "doi": "https://doi.org/10.1/abc",
        "display_name": "Train Rescheduling",
        "publication_year": 2018,
        "cited_by_count": 77,
        "authorships": [{"author": {"display_name": "A. Author"}}],
        "primary_location": {"source": {"display_name": "TR-C", "host_organization_name": "Elsevier"}},
        "locations": [{"landing_page_url": "https://arxiv.org/abs/1801.01234"}],
        "referenced_works": ["https://openalex.org/W1", "https://openalex.org/W2"],
    }
    src = OpenAlexClient.to_source(work)
    assert src.doi == "10.1/abc"  # https://doi.org/ prefix stripped
    assert src.arxiv_id == "1801.01234"  # parsed from the arXiv location
    assert src.openalex_id == "W123"
    assert src.venue == "TR-C" and src.publisher == "Elsevier"
    assert src.cited_by_count == 77

    ref = OpenAlexClient._ref(work)
    assert ref.openalex_id == "W123" and ref.doi == "10.1/abc"
