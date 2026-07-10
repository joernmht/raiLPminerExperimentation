"""Offline reliability tests for the acquisition boundary (no network, no sleeping).

Covers the two guarantees ``corpusbuilder/_http.py`` exists to provide:

1. **Availability** — transient faults (429/5xx, connection resets, timeouts) are
   retried with backoff and an honoured ``Retry-After``; permanent ones are not.
2. **Maturity / PRISMA integrity** — a transient fault is never laundered into a
   permanent corpus fact. Guarded end-to-end through ``cli.cmd_dossier``.
"""

from __future__ import annotations

import argparse
import json
import random
from unittest import mock

import pytest
import requests

from corpusbuilder import cli
from corpusbuilder._http import (
    RETRYABLE_STATUS,
    AcquisitionError,
    RetryPolicy,
    is_transient_exception,
    parse_retry_after,
    request_with_retry,
)
from corpusbuilder.arxiv import ArxivError
from corpusbuilder.dossier import Dossier, ExtractionMethod, FormulaRecord, SourceInfo
from corpusbuilder.elsevier import ElsevierClient, ElsevierError
from corpusbuilder.openalex import OpenAlexClient, OpenAlexError, OpenAlexNotFound

_ELS_DOI = "10.1016/j.trc.2017.06.018"

# A policy with no jitter and instant backoff keeps assertions exact.
_FAST = RetryPolicy(attempts=4, base_delay=1.0, max_delay=8.0, jitter=False)


def _response(status: int, *, headers: dict | None = None, body: str = "", json_body=None):
    r = mock.Mock(spec=requests.Response)
    r.status_code = status
    r.headers = headers or {}
    r.text = body
    r.url = "https://example.test/x"
    r.json = mock.Mock(return_value=json_body if json_body is not None else {})
    return r


class _Recorder:
    """Collects the delays passed to ``sleep`` so backoff is assertable."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


# --- parse_retry_after ------------------------------------------------------


def test_retry_after_delta_seconds() -> None:
    assert parse_retry_after("120") == 120.0


def test_retry_after_http_date_is_relative_to_now() -> None:
    # 2015-10-21T07:28:00Z == 1445412480 epoch; 30s in the "future".
    delay = parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412450.0)
    assert delay == pytest.approx(30.0)


def test_retry_after_past_date_clamps_to_zero() -> None:
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:00 GMT", now=1445412600.0) == 0.0


def test_retry_after_is_capped() -> None:
    assert parse_retry_after("86400", cap=300.0) == 300.0


@pytest.mark.parametrize("value", [None, "", "   ", "soon", "not-a-date"])
def test_retry_after_unparseable_is_none(value) -> None:
    assert parse_retry_after(value) is None


# --- request_with_retry -----------------------------------------------------


def test_non_retryable_status_returns_on_first_call() -> None:
    send = mock.Mock(return_value=_response(404))
    sleep = _Recorder()
    r = request_with_retry(send, policy=_FAST, sleep=sleep)
    assert r.status_code == 404
    assert send.call_count == 1  # a 404 is an answer, not a fault
    assert sleep.delays == []


def test_retryable_status_is_retried_then_succeeds() -> None:
    send = mock.Mock(side_effect=[_response(503), _response(429), _response(200)])
    sleep = _Recorder()
    r = request_with_retry(send, policy=_FAST, sleep=sleep)
    assert r.status_code == 200
    assert send.call_count == 3
    assert sleep.delays == [1.0, 2.0]  # exponential, no jitter


def test_retry_after_header_overrides_computed_backoff() -> None:
    send = mock.Mock(side_effect=[_response(429, headers={"Retry-After": "7"}), _response(200)])
    sleep = _Recorder()
    request_with_retry(send, policy=_FAST, sleep=sleep, now=lambda: 0.0)
    assert sleep.delays == [7.0]  # upstream's instruction wins over base_delay=1.0


def test_exhausted_retries_return_the_last_retryable_response() -> None:
    send = mock.Mock(return_value=_response(503))
    sleep = _Recorder()
    r = request_with_retry(send, policy=_FAST, sleep=sleep)
    assert r.status_code == 503
    assert send.call_count == _FAST.attempts  # 4 tries
    assert len(sleep.delays) == _FAST.attempts - 1  # no sleep after the last


def test_backoff_is_capped_by_max_delay() -> None:
    policy = RetryPolicy(attempts=6, base_delay=1.0, max_delay=4.0, jitter=False)
    send = mock.Mock(return_value=_response(503))
    sleep = _Recorder()
    request_with_retry(send, policy=policy, sleep=sleep)
    assert sleep.delays == [1.0, 2.0, 4.0, 4.0, 4.0]  # 8.0 and 16.0 clamped to 4.0


def test_full_jitter_stays_within_the_backoff_envelope() -> None:
    policy = RetryPolicy(attempts=4, base_delay=1.0, max_delay=8.0, jitter=True)
    send = mock.Mock(return_value=_response(503))
    sleep = _Recorder()
    request_with_retry(send, policy=policy, sleep=sleep, rng=random.Random(0))
    assert len(sleep.delays) == 3
    for i, delay in enumerate(sleep.delays):
        assert 0.0 <= delay <= min(1.0 * 2**i, 8.0)


def test_proxy_error_is_retried_then_raised() -> None:
    """A dying SOCKS tunnel (ADR-0003) is transient — retried, then surfaced."""
    send = mock.Mock(side_effect=requests.exceptions.ProxyError("tunnel died"))
    sleep = _Recorder()
    with pytest.raises(requests.exceptions.ProxyError):
        request_with_retry(send, policy=_FAST, sleep=sleep)
    assert send.call_count == _FAST.attempts
    assert len(sleep.delays) == _FAST.attempts - 1


def test_transient_exception_then_success() -> None:
    send = mock.Mock(side_effect=[requests.exceptions.ReadTimeout("slow"), _response(200)])
    r = request_with_retry(send, policy=_FAST, sleep=_Recorder())
    assert r.status_code == 200 and send.call_count == 2


def test_non_transient_exception_is_not_retried() -> None:
    send = mock.Mock(side_effect=ValueError("bug in our own code"))
    with pytest.raises(ValueError):
        request_with_retry(send, policy=_FAST, sleep=_Recorder())
    assert send.call_count == 1


def test_is_transient_exception_classification() -> None:
    assert is_transient_exception(requests.exceptions.ProxyError())
    assert is_transient_exception(requests.exceptions.ConnectTimeout())
    assert is_transient_exception(AcquisitionError("x", transient=True))
    assert not is_transient_exception(AcquisitionError("x", transient=False))
    assert not is_transient_exception(ValueError("x"))
    # requests errors are OSError, NOT RuntimeError — the bug this module fixes.
    assert not isinstance(requests.exceptions.ProxyError(), RuntimeError)


def test_client_errors_are_not_in_the_retry_set() -> None:
    for status in (400, 401, 403, 404, 410):
        assert status not in RETRYABLE_STATUS


# --- OpenAlex ---------------------------------------------------------------


def _openalex_client() -> OpenAlexClient:
    client = OpenAlexClient(mailto="t@test", retry=RetryPolicy(attempts=2, jitter=False))
    client._s = mock.Mock()
    return client


def test_openalex_404_raises_not_found_not_transient() -> None:
    client = _openalex_client()
    client._s.get.return_value = _response(404)
    with pytest.raises(OpenAlexNotFound) as ei:
        client._get("works/doi:10.1/missing")
    assert ei.value.transient is False and ei.value.status == 404


def test_openalex_429_is_flagged_transient() -> None:
    client = _openalex_client()
    client._s.get.return_value = _response(429, body="rate limited")
    with mock.patch("corpusbuilder._http.time.sleep"), pytest.raises(OpenAlexError) as ei:
        client._get("works")
    assert ei.value.transient is True and ei.value.status == 429


def test_openalex_non_json_body_is_transient() -> None:
    client = _openalex_client()
    r = _response(200)
    r.json.side_effect = ValueError("Expecting value")
    client._s.get.return_value = r
    with pytest.raises(OpenAlexError) as ei:
        client._get("works")
    assert ei.value.transient is True


def test_arxiv_lookup_falls_back_to_search_only_on_404() -> None:
    """A genuine 404 may fall through to the fuzzy search."""
    client = _openalex_client()
    hit = {"locations": [{"landing_page_url": "https://arxiv.org/abs/2103.04618"}], "id": "W9"}
    with mock.patch.object(
        client, "_get", side_effect=[OpenAlexNotFound("404"), {"results": [hit]}]
    ) as get:
        work = client.get_work("arXiv:2103.04618")
    assert work == hit
    assert get.call_count == 2  # DOI lookup, then search


def test_arxiv_lookup_does_not_search_on_transient_error() -> None:
    """A 429/5xx must propagate: searching could bind a *different* paper.

    This is the corpus-corruption path — the old ``except OpenAlexError: pass``
    treated an overload exactly like a 404.
    """
    client = _openalex_client()
    boom = OpenAlexError("OpenAlex 500", status=500, transient=True)
    with (
        mock.patch.object(client, "_get", side_effect=[boom, {"results": []}]) as get,
        pytest.raises(OpenAlexError) as ei,
    ):
        client.get_work("arXiv:2103.04618")
    assert ei.value.transient is True
    assert get.call_count == 1  # the fuzzy search was never reached


# --- Elsevier / Scopus ------------------------------------------------------


def _elsevier_client() -> ElsevierClient:
    client = ElsevierClient(api_key="k", proxy=None, retry=RetryPolicy(attempts=2, jitter=False))
    client._s = mock.Mock()
    return client


def test_elsevier_proxy_error_becomes_a_transient_elsevier_error() -> None:
    """The regression that crashed ``cmd_dossier``: ProxyError is not a RuntimeError."""
    client = _elsevier_client()
    client._s.get.side_effect = requests.exceptions.ProxyError("tunnel died")
    with mock.patch("corpusbuilder._http.time.sleep"), pytest.raises(ElsevierError) as ei:
        client.full_text_xml(_ELS_DOI)
    assert ei.value.transient is True


def test_elsevier_403_not_entitled_is_permanent() -> None:
    client = _elsevier_client()
    client._s.get.return_value = _response(403, body="not entitled")
    with pytest.raises(ElsevierError) as ei:
        client.full_text_xml(_ELS_DOI)
    assert ei.value.transient is False and ei.value.status == 403


def test_scopus_404_means_not_indexed() -> None:
    client = _elsevier_client()
    client._s.get.return_value = _response(404)
    assert client.scopus_cited_by_count(_ELS_DOI) is None


def test_scopus_transient_failure_raises_instead_of_faking_not_indexed() -> None:
    """A 429 must not be recorded as 'not in Scopus' (CLAUDE.md honesty rule)."""
    client = _elsevier_client()
    client._s.get.return_value = _response(429, body="quota")
    with mock.patch("corpusbuilder._http.time.sleep"), pytest.raises(ElsevierError) as ei:
        client.scopus_cited_by_count(_ELS_DOI)
    assert ei.value.transient is True


def test_scopus_happy_path_parses_count() -> None:
    client = _elsevier_client()
    client._s.get.return_value = _response(
        200, json_body={"search-results": {"entry": [{"citedby-count": "42"}]}}
    )
    assert client.scopus_cited_by_count(_ELS_DOI) == 42


# --- The tier ladder, end to end (cli.cmd_dossier) --------------------------


def _args(tmp_path, **over) -> argparse.Namespace:
    base = dict(
        identifier=_ELS_DOI,
        arxiv=None,
        ref_limit=1,
        cite_limit=1,
        no_formulas=False,
        no_scopus=True,
        proxy=None,
        out=str(tmp_path),
    )
    base.update(over)
    return argparse.Namespace(**base)


def _dossier(*, arxiv_id: str | None = None) -> Dossier:
    return Dossier(
        source=SourceInfo(
            title="A MILP", doi=_ELS_DOI, arxiv_id=arxiv_id, api="openalex", retrieved="2026-07-10"
        )
    )


@pytest.fixture
def _no_openalex(monkeypatch):
    """Stub OpenAlex so the ladder tests exercise only Tier-1/Tier-2."""

    def _install(dossier: Dossier):
        monkeypatch.setattr(cli.OpenAlexClient, "__init__", lambda self: None)
        monkeypatch.setattr(cli.OpenAlexClient, "build_dossier", lambda self, *a, **k: dossier)

    return _install


def test_tier2_transient_failure_aborts_and_writes_nothing(tmp_path, _no_openalex) -> None:
    """The PRISMA-integrity guard: a dead tunnel must not become an exclusion reason."""
    _no_openalex(_dossier())
    with mock.patch.object(
        cli.ElsevierClient, "full_text_xml", side_effect=ElsevierError("tunnel", transient=True)
    ):
        rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == cli.EXIT_TRANSIENT
    assert list(tmp_path.glob("*.json")) == []  # nothing persisted
    assert list(tmp_path.glob("*.md")) == []


def test_tier2_proxy_error_degrades_instead_of_crashing(tmp_path, _no_openalex) -> None:
    """Regression: ``requests.ProxyError`` used to escape ``except RuntimeError``."""
    _no_openalex(_dossier())
    with mock.patch.object(
        cli.ElsevierClient, "full_text_xml", side_effect=requests.exceptions.ProxyError("down")
    ):
        rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == cli.EXIT_TRANSIENT  # handled, not a traceback


def test_tier2_permanent_failure_records_citations_only(tmp_path, _no_openalex) -> None:
    """Not entitled (403) is a real fact: write the dossier with citations only."""
    _no_openalex(_dossier())
    with mock.patch.object(
        cli.ElsevierClient,
        "full_text_xml",
        side_effect=ElsevierError("403 not entitled", status=403, transient=False),
    ):
        rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == 0
    written = Dossier.load(next(iter(tmp_path.glob("*.json"))))
    assert written.formulas == []


def test_tier1_permanent_failure_falls_through_to_tier2(tmp_path, _no_openalex) -> None:
    """PDF-only arXiv (permanent) → the ladder descends and Tier-2 supplies formulas."""
    _no_openalex(_dossier(arxiv_id="2103.04618"))
    record = FormulaRecord(id="eq-0001", latex="x+y=1", method=ExtractionMethod.mathml)
    with (
        mock.patch.object(cli, "fetch_source", side_effect=ArxivError("PDF-only", transient=False)),
        mock.patch.object(cli.ElsevierClient, "full_text_xml", return_value="<ce:formula/>"),
        mock.patch.object(cli.ElsevierClient, "extract_formulas", return_value=[record]),
    ):
        rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == 0
    written = Dossier.load(next(iter(tmp_path.glob("*.json"))))
    assert [f.method for f in written.formulas] == [ExtractionMethod.mathml]
    assert written.source.api == "openalex+elsevier"


def test_tier1_transient_failure_aborts_before_tier2(tmp_path, _no_openalex) -> None:
    """arXiv rate-limiting must not silently demote a Tier-1 paper to Tier-2."""
    _no_openalex(_dossier(arxiv_id="2103.04618"))
    with (
        mock.patch.object(
            cli, "fetch_source", side_effect=ArxivError("503", status=503, transient=True)
        ),
        mock.patch.object(cli.ElsevierClient, "full_text_xml") as els,
    ):
        rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == cli.EXIT_TRANSIENT
    els.assert_not_called()  # the ladder never descended
    assert list(tmp_path.glob("*.json")) == []


def test_tier1_success_keeps_arxiv_provenance(tmp_path, _no_openalex) -> None:
    _no_openalex(_dossier(arxiv_id="2103.04618"))
    record = FormulaRecord(id="eq-0001", latex="x+y=1", method=ExtractionMethod.arxiv_tex)
    with (
        mock.patch.object(cli, "fetch_source", return_value=(tmp_path / "_src", "abc123")),
        mock.patch.object(cli, "extract_equations", return_value=[record]),
    ):
        rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == 0
    written = Dossier.load(next(iter(tmp_path.glob("*.json"))))
    assert written.source.api == "openalex+arxiv"
    assert written.source.entitlement == "open-access"
    assert written.source.file_sha256 == "abc123"


def test_openalex_transient_failure_aborts_before_any_tier(tmp_path, _no_openalex, monkeypatch):
    monkeypatch.setattr(cli.OpenAlexClient, "__init__", lambda self: None)
    monkeypatch.setattr(
        cli.OpenAlexClient,
        "build_dossier",
        mock.Mock(side_effect=OpenAlexError("503", status=503, transient=True)),
    )
    rc = cli.cmd_dossier(_args(tmp_path))
    assert rc == cli.EXIT_TRANSIENT
    assert list(tmp_path.glob("*.json")) == []


# --- Discovery driver: checkpoint + PRISMA-safe partial handling -------------


def test_discover_merge_is_independent_of_query_order() -> None:
    """A --resume sweep visits queries in a different order; the artifact must not change."""
    from corpusbuilder._discover import _merge

    a = {"title": "A", "doi": "10.1/a", "openalex_id": None, "cited_by_count": 5}
    b = {"title": "B", "doi": "10.1/b", "openalex_id": None, "cited_by_count": 5}  # tie
    c = {"title": "C", "doi": "10.1/c", "openalex_id": None, "cited_by_count": 9}
    forward = _merge({"q1": [a, c], "q2": [b, a]})
    reverse = _merge({"q2": [b, a], "q1": [a, c]})
    assert forward == reverse
    assert [r["doi"] for r in forward] == ["10.1/c", "10.1/a", "10.1/b"]  # cites desc, key asc
    assert forward[1]["queries"] == ["q1", "q2"]  # A found by both, sorted


def test_discover_checkpoint_roundtrip_and_corruption_tolerance(tmp_path, monkeypatch) -> None:
    from corpusbuilder import _discover

    monkeypatch.setattr(_discover, "CHECKPOINT", tmp_path / "partial.json")
    assert _discover._load_checkpoint() == {}  # absent

    _discover._save_checkpoint({"q1": [{"title": "A"}]})
    assert _discover._load_checkpoint() == {"q1": [{"title": "A"}]}

    (tmp_path / "partial.json").write_text("{not json")
    assert _discover._load_checkpoint() == {}  # corrupt -> start over, never crash


def test_discover_partial_sweep_refuses_to_write_the_corpus_artifact(tmp_path, monkeypatch) -> None:
    """PRISMA integrity: a failed query must not silently shrink database_queries."""
    from corpusbuilder import _discover
    from corpusbuilder._http import AcquisitionError

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"queries": ["q1", "q2"]}')
    monkeypatch.setattr(_discover, "MANIFEST", manifest)
    monkeypatch.setattr(_discover, "OUT_JSON", tmp_path / "candidates.json")
    monkeypatch.setattr(_discover, "OUT_MD", tmp_path / "candidates.md")
    monkeypatch.setattr(_discover, "CHECKPOINT", tmp_path / "partial.json")

    seed = mock.Mock(
        title="A",
        doi="10.1/a",
        arxiv_id=None,
        openalex_id="W1",
        year=2020,
        venue="V",
        publisher="P",
        cited_by_count=7,
    )
    search = mock.Mock(side_effect=[[seed], AcquisitionError("429", transient=True)])
    monkeypatch.setattr(_discover.OpenAlexClient, "__init__", lambda self: None)
    monkeypatch.setattr(_discover.OpenAlexClient, "search_seeds", search)

    rc = _discover.main(["2026-07-10"])

    assert rc == _discover.EXIT_INCOMPLETE
    assert not (tmp_path / "candidates.json").exists()  # PRISMA never sees a partial tally
    assert not (tmp_path / "candidates.md").exists()
    # ...but q1's work survived for --resume.
    assert list(_discover._load_checkpoint()) == ["q1"]


def test_discover_resume_reuses_checkpoint_and_completes(tmp_path, monkeypatch) -> None:
    from corpusbuilder import _discover

    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"queries": ["q1", "q2"]}')
    monkeypatch.setattr(_discover, "MANIFEST", manifest)
    monkeypatch.setattr(_discover, "OUT_JSON", tmp_path / "candidates.json")
    monkeypatch.setattr(_discover, "OUT_MD", tmp_path / "candidates.md")
    monkeypatch.setattr(_discover, "CHECKPOINT", tmp_path / "partial.json")
    _discover._save_checkpoint(
        {
            "q1": [
                _discover._record(
                    mock.Mock(
                        title="A",
                        doi="10.1/a",
                        arxiv_id=None,
                        openalex_id="W1",
                        year=2020,
                        venue="V",
                        publisher="P",
                        cited_by_count=7,
                    )
                )
            ]
        }
    )

    seed = mock.Mock(
        title="B",
        doi="10.1016/x",
        arxiv_id=None,
        openalex_id="W2",
        year=2021,
        venue="V",
        publisher="Elsevier",
        cited_by_count=3,
    )
    search = mock.Mock(return_value=[seed])
    monkeypatch.setattr(_discover.OpenAlexClient, "__init__", lambda self: None)
    monkeypatch.setattr(_discover.OpenAlexClient, "search_seeds", search)

    rc = _discover.main(["2026-07-10", "--resume"])

    assert rc == 0
    search.assert_called_once()  # q1 came from the checkpoint; only q2 re-ran
    payload = json.loads((tmp_path / "candidates.json").read_text())
    assert payload["queries"] == ["q1", "q2"]  # full PRISMA denominator
    assert payload["n_candidates"] == 2
    assert not (tmp_path / "partial.json").exists()  # checkpoint cleared on success
