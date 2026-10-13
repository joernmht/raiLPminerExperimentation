"""Identifier shape checks run before an identifier reaches a request URL.

The clients interpolate DOIs and arXiv ids into URL paths, and ``requests``
normalises ``..`` segments, so a malformed "DOI" could address a different API
endpoint (with the Elsevier key attached). Every test that feeds a client a bad
identifier also asserts that no request was attempted.
"""

from __future__ import annotations

import argparse

import pytest

from corpusbuilder import cli
from corpusbuilder.arxiv import ArxivError, normalize_arxiv_id
from corpusbuilder.elsevier import ElsevierClient, ElsevierError
from corpusbuilder.identifiers import InvalidIdentifier, check_arxiv_id, check_doi
from corpusbuilder.openalex import OpenAlexClient, OpenAlexError

_TRAVERSAL = "10.1016/x/../../authors/A1?x="


def _no_request(*_args, **_kwargs):
    raise AssertionError("a request was attempted with a malformed identifier")


@pytest.mark.parametrize(
    "doi",
    [
        "10.1016/j.trb.2016.08.011",
        "10.1287/opre.2014.1327",
        "10.1016/0191-2615(95)00024-0",  # older Elsevier DOIs carry parentheses
        "10.48550/arXiv.2103.04618",
    ],
)
def test_real_dois_pass(doi: str) -> None:
    assert check_doi(doi) == doi


@pytest.mark.parametrize(
    "doi",
    [
        _TRAVERSAL,
        "10.1016/./x",
        "10.1016/x/..",
        "10.1/a b",
        "10.1/a#frag",
        "10.1/a?q=1",
        "10.1/a\\..\\b",
        "11.1016/x",
        "10.1016/",
        "doi:10.1016/x",
        "",
    ],
)
def test_malformed_dois_are_rejected(doi: str) -> None:
    with pytest.raises(InvalidIdentifier):
        check_doi(doi)


@pytest.mark.parametrize("arxiv_id", ["2103.04618", "2103.04618v2", "1501.0001", "hep-th/9901001"])
def test_real_arxiv_ids_pass(arxiv_id: str) -> None:
    assert check_arxiv_id(arxiv_id) == arxiv_id


@pytest.mark.parametrize("arxiv_id", ["../../authors/A1", "2103.04618/../x", "2103", "abc", ""])
def test_malformed_arxiv_ids_are_rejected(arxiv_id: str) -> None:
    with pytest.raises(InvalidIdentifier):
        check_arxiv_id(arxiv_id)


@pytest.mark.parametrize(
    "identifier",
    [_TRAVERSAL, "arXiv:../../authors/A1", "https://openalex.org/W1/../../authors/A1"],
)
def test_openalex_refuses_before_any_request(monkeypatch, identifier: str) -> None:
    client = OpenAlexClient()
    monkeypatch.setattr(client, "_get", _no_request)
    with pytest.raises(OpenAlexError) as e:
        client.get_work(identifier)
    assert e.value.transient is False


@pytest.mark.parametrize("call", ["full_text_xml", "scopus_cited_by_count"])
def test_elsevier_refuses_before_any_request(monkeypatch, call: str) -> None:
    client = ElsevierClient(api_key="test-key-not-a-real-credential")
    monkeypatch.setattr(client, "_get", _no_request)
    with pytest.raises(ElsevierError) as e:
        getattr(client, call)(_TRAVERSAL)
    assert e.value.transient is False


def test_unmatched_arxiv_string_is_an_error_not_passed_through() -> None:
    assert normalize_arxiv_id("arXiv:2103.04618v2") == "2103.04618v2"
    assert normalize_arxiv_id("https://arxiv.org/abs/2103.04618") == "2103.04618"
    with pytest.raises(ArxivError) as e:
        normalize_arxiv_id("../../etc")
    assert e.value.transient is False


def test_dossier_for_a_malformed_doi_fails_and_writes_nothing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(OpenAlexClient, "_get", _no_request)
    args = argparse.Namespace(
        identifier=_TRAVERSAL,
        arxiv=None,
        ref_limit=1,
        cite_limit=1,
        no_formulas=False,
        no_scopus=True,
        proxy=None,
        out=str(tmp_path),
    )
    assert cli.cmd_dossier(args) == 1
    assert list(tmp_path.iterdir()) == []
