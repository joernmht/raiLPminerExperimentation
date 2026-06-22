"""Offline tests for the corpusbuilder package (no network)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from corpusbuilder import prisma

_CORPUS = Path(__file__).resolve().parents[1] / "corpus"

from corpusbuilder.arxiv import extract_equations_from_text
from corpusbuilder.elsevier import ElsevierClient, is_elsevier_doi
from corpusbuilder.mathml import _BRIDGE, _normalize, mathml_to_latex
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


# --- Elsevier Tier-2 -------------------------------------------------------

_NODE_READY = shutil.which("node") is not None and (_BRIDGE / "node_modules").exists()
needs_node = pytest.mark.skipif(not _NODE_READY, reason="node + mathml-to-latex not installed")

_FIXTURE_XML = """<?xml version="1.0"?>
<full-text-retrieval-response xmlns:ce="http://www.elsevier.com/xml/common/dtd"
                              xmlns:mml="http://www.w3.org/1998/Math/MathML">
  <ce:sections>
    <ce:formula id="fd1"><mml:math><mml:mrow>
      <mml:mi>x</mml:mi><mml:mo>+</mml:mo><mml:mi>y</mml:mi>
    </mml:mrow></mml:math></ce:formula>
    <ce:para>some body text</ce:para>
  </ce:sections>
</full-text-retrieval-response>"""

_META_ONLY = """<?xml version="1.0"?>
<full-text-retrieval-response xmlns:ce="http://www.elsevier.com/xml/common/dtd">
  <ce:title>Just metadata</ce:title>
</full-text-retrieval-response>"""


def test_is_elsevier_doi() -> None:
    assert is_elsevier_doi("10.1016/j.trb.2016.08.011")
    assert not is_elsevier_doi("10.1287/opre.2014.1327")
    assert not is_elsevier_doi(None)


def test_has_full_text_detection() -> None:
    assert ElsevierClient.has_full_text(_FIXTURE_XML)
    assert not ElsevierClient.has_full_text(_META_ONLY)


def test_normalize_mathml_strips_prefix_and_adds_ns() -> None:
    out = _normalize('<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>x</mml:mi></mml:math>')
    assert "mml:" not in out and 'xmlns="http://www.w3.org/1998/Math/MathML"' in out


@needs_node
def test_mathml_to_latex_simple() -> None:
    res = mathml_to_latex(["<mml:math><mml:mi>x</mml:mi></mml:math>"])
    assert len(res) == 1 and res[0].ok and res[0].latex.strip() == "x"


@needs_node
def test_extract_formulas_from_fixture() -> None:
    client = ElsevierClient(api_key="test-key")  # no network; key not validated here
    recs = client.extract_formulas(_FIXTURE_XML)
    assert len(recs) == 1
    assert recs[0].label == "fd1"
    assert recs[0].method.value == "mathml"
    assert recs[0].mathml is not None and "mml:math" in recs[0].mathml
    assert recs[0].latex.replace(" ", "") == "x+y"


# --- PRISMA tally ----------------------------------------------------------


def test_prisma_tally_arithmetic_closes() -> None:
    t = prisma.build_tally(_CORPUS)
    # Screened-in records split exactly across the three retrieval outcomes.
    assert t.n_topical == (
        t.n_fulltext_retrieved + t.n_fulltext_metadata_only + t.n_fulltext_unavailable
    )
    # Topical = screened minus off-domain exclusions.
    assert t.n_topical == t.n_screened - t.n_offdomain_excluded
    # Retrieved full texts split into extracted vs. empty.
    assert t.n_fulltext_retrieved == t.n_extracted + t.n_extraction_empty
    # Snowball arm accounts for every identified neighbour.
    assert t.n_snowball_total == t.n_snowball_recommended + t.n_snowball_excluded


def test_prisma_offdomain_screen_flags_only_clinical_venues() -> None:
    t = prisma.build_tally(_CORPUS)
    # The clinical/medical false positives are excluded, nothing more.
    assert t.n_offdomain_excluded == len(t.offdomain_excluded)
    assert t.n_offdomain_excluded >= 1
    assert all(doi.startswith("10.") for doi in t.offdomain_excluded)


def test_prisma_tally_is_deterministic() -> None:
    a = prisma.tally_to_dict(prisma.build_tally(_CORPUS))
    b = prisma.tally_to_dict(prisma.build_tally(_CORPUS))
    assert a == b
    # The serialised forms are byte-stable too (the paper's source of truth).
    import json

    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert prisma.tally_to_markdown(prisma.build_tally(_CORPUS)) == prisma.tally_to_markdown(
        prisma.build_tally(_CORPUS)
    )
    # The mobile HTML page is self-contained (no external fetches) and stable too.
    html = prisma.render_html(prisma.build_tally(_CORPUS))
    assert html == prisma.render_html(prisma.build_tally(_CORPUS))
    assert "<!doctype html>" in html and "viewport" in html
    assert "http://" not in html and "https://" not in html  # no external resources
    assert f"{prisma.build_tally(_CORPUS).n_formulas_mined} formula records" in html
