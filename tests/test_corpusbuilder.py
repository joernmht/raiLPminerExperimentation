"""Offline tests for the corpusbuilder package (no network)."""

from __future__ import annotations

import shutil

import pytest

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
from corpusbuilder.elsevier import ElsevierClient, is_elsevier_doi
from corpusbuilder.mathml import _BRIDGE, _normalize, mathml_to_latex
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
            FormulaRecord(
                id="eq-0001",
                latex="x+y=1",
                method=ExtractionMethod.arxiv_tex,
                page_start=3,
                page_end=3,
                status=VerificationStatus.accepted,
            ),
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
        "primary_location": {
            "source": {"display_name": "TR-C", "host_organization_name": "Elsevier"}
        },
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
    out = _normalize(
        '<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML"><mml:mi>x</mml:mi></mml:math>'
    )
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


# -- Security: untrusted-input hardening ------------------------------------


def _tar_add_bytes(tar, name: str, data: bytes) -> None:
    import io
    import tarfile

    info = tarfile.TarInfo(name=name)
    info.size = len(data)
    tar.addfile(info, io.BytesIO(data))


def test_safe_extract_blocks_path_traversal(tmp_path) -> None:
    """arXiv e-prints are untrusted: members escaping the dest dir are dropped.

    Guards the sibling-prefix escape a naive ``startswith`` check would accept
    (``.../2103.04618`` vs ``.../2103.04618_evil/x``) plus plain ``..`` traversal.
    """
    import io
    import tarfile

    from corpusbuilder.arxiv import _safe_extract

    dest = tmp_path / "2103.04618"
    dest.mkdir()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        _tar_add_bytes(tar, "main.tex", b"legit")
        _tar_add_bytes(tar, "../2103.04618_evil/pwn.tex", b"sibling-escape")
        _tar_add_bytes(tar, "../../etc_pwn.tex", b"parent-escape")
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        _safe_extract(tar, dest)

    assert (dest / "main.tex").read_bytes() == b"legit"
    assert not (tmp_path / "2103.04618_evil").exists()
    assert not (tmp_path / "etc_pwn.tex").exists()
    # nothing landed anywhere outside the intended dest dir
    assert [p.relative_to(tmp_path).as_posix() for p in sorted(tmp_path.rglob("*.tex"))] == [
        "2103.04618/main.tex"
    ]


def test_safe_extract_skips_symlink_members(tmp_path) -> None:
    """Symlink members must never be materialised (link-based escape)."""
    import io
    import tarfile

    from corpusbuilder.arxiv import _safe_extract

    dest = tmp_path / "id"
    dest.mkdir()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name="link.tex")
        info.type = tarfile.SYMTYPE
        info.linkname = "/etc/passwd"
        tar.addfile(info)
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        _safe_extract(tar, dest)

    assert not (dest / "link.tex").exists()
    assert not (dest / "link.tex").is_symlink()


def test_elsevier_parser_does_not_disclose_local_files(tmp_path) -> None:
    """The hardened parser must not resolve external SYSTEM entities (XXE)."""
    from lxml import etree

    from corpusbuilder.elsevier import _XML_PARSER

    secret = tmp_path / "secret.txt"
    secret.write_text("TOPSECRET")
    xml = f'<?xml version="1.0"?><!DOCTYPE r [<!ENTITY x SYSTEM "file://{secret}">]><r>&x;</r>'
    try:
        root = etree.fromstring(xml.encode(), parser=_XML_PARSER)
    except etree.XMLSyntaxError:
        return  # refusing to resolve the entity at all is a valid safe outcome
    assert "TOPSECRET" not in (root.text or "")
