"""Tests for :mod:`corpusbuilder.factory` — the factory-floor page + heartbeat.

Everything runs against a tiny synthetic corpus in a tmp dir. The live corpus
is never read or written: the autouse fixture in ``conftest.py`` redirects
heartbeats, and every path here is explicit.
"""

from __future__ import annotations

import json
import threading
import urllib.request
from pathlib import Path

import pytest

from corpusbuilder import factory, promote

# ---------------------------------------------------------------------------
# Synthetic corpus
# ---------------------------------------------------------------------------

_DETAILS = {
    "sup": "normalized LaTeX is not in the canonical grammar: referent 'v_{i}^{c}': "
    "trailing '^{c}' after the subscript is not part of the canonical grammar",
    "jux": "normalized LaTeX is not in the canonical grammar: referent 'p_{a} x_{a}': "
    "trailing 'x_{a}' after the subscript is not part of the canonical grammar",
    "chain3": "normalized LaTeX is not in the canonical grammar: chained relation with 3 "
    "comparators is not supported: 'l \\leq x \\leq u \\leq v'",
    "mixed": "normalized LaTeX is not in the canonical grammar: mixed-direction or equality "
    "chained relation is not supported: 'a = b \\leq c'",
    "label": "normalized LaTeX is not in the canonical grammar: referent 'h': subscript "
    "'\\mathit{min}' cannot serve as an index family",
    "pyd": "normalized LaTeX is not in the canonical grammar: 1 validation error for Term ref "
    "String should match pattern [type=string_pattern_mismatch, input_value='(w']",
    "float": "normalized LaTeX is not in the canonical grammar: could not convert string to "
    "float: 'Buff_max'",
    "coef": "normalized LaTeX is not in the canonical grammar: subscripted coefficient 'w_{1}' "
    "writes 1 indices but 'w' is declared with shape ()",
    "braces": "normalized LaTeX is not in the canonical grammar: unbalanced braces: '{k^{'",
    "nocmp": "normalized LaTeX is not in the canonical grammar: no comparator in constraint body",
    "other": "normalized LaTeX is not in the canonical grammar: something new",
}


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload, indent=1)
    path.write_text(text, encoding="utf-8", newline="\n")


def _promotion(papers: list[tuple[str, str, str | None, bool]]) -> dict:
    causes = {
        "outside_grammar": "outside_grammar",
        "no_objective": "under_specified",
        "not_sorted": "pipeline_incomplete",
        "all_rejected": "extraction_error",
    }
    rows = [
        {
            "paper_key": key,
            "entry_id": key,
            "promoted": promoted,
            "rows": 3,
            "cause": None if promoted else cause,
            "category": None if promoted else causes.get(cause, ""),
            "detail": detail,
        }
        for key, cause, detail, promoted in papers
    ]
    by_cause: dict[str, dict] = {}
    for r in rows:
        if r["promoted"]:
            continue
        rec = by_cause.setdefault(
            r["cause"], {"papers": 0, "category": r["category"], "remedy": "x"}
        )
        rec["papers"] += 1
    return {
        "schema_version": "promotion-1",
        "rewrite_rules_version": "rewrite-9999.01.0",
        "papers_with_decisions": len(rows),
        "promoted": sum(1 for r in rows if r["promoted"]),
        "failed": sum(1 for r in rows if not r["promoted"]),
        "formula_decisions": {"accepted": 5, "corrected": 1, "rejected": 2},
        "failures_by_cause": by_cause,
        "papers": rows,
    }


@pytest.fixture
def floor(tmp_path: Path) -> dict[str, Path]:
    corpus = tmp_path / "corpus"
    outputs = tmp_path / "outputs"
    _write(corpus / "candidates.json", {"n_candidates": 4, "queries": [1, 2], "candidates": []})
    _write(corpus / "snowball_candidates.json", {"n_candidates": 40, "n_recommended": 9})
    _write(
        corpus / "prisma.json",
        {
            "flow": {
                "identification": {"database_queries": 2, "database_unique_records": 4},
                "retrieval_eligibility": {
                    "reports_retrieved": 6,
                    "reports_excluded_total": 1,
                    "reports_excluded": {"not_entitled": 1},
                },
                "included": {
                    "source_papers": 5,
                    "candidate_formulations": 50,
                    "hitl_review": {
                        "accepted": 30,
                        "corrected": 5,
                        "rejected": 10,
                        "duplicate": 2,
                        "unreviewed": 3,
                    },
                },
            }
        },
    )
    _write(
        corpus / "resolution.json",
        {
            "formulas": 50,
            "papers": 5,
            "resolved": 25,
            "reviewed_pairs": 70,
            "structural_axes_pct": {
                "parses": 90.0,
                "single": 80.0,
                "renders": 70.0,
                "statement": 95.0,
            },
        },
    )
    _write(
        corpus / "promotion.json",
        _promotion(
            [
                ("10.1/a", "", None, True),
                ("10.1/b", "outside_grammar", _DETAILS["sup"], False),
                ("10.1/c", "outside_grammar", _DETAILS["sup"], False),
                ("10.1/d", "outside_grammar", _DETAILS["jux"], False),
                ("10.1/e", "outside_grammar", _DETAILS["chain3"], False),
                ("10.1/f", "outside_grammar", _DETAILS["mixed"], False),
                ("10.1/g", "outside_grammar", _DETAILS["label"], False),
                ("10.1/h", "outside_grammar", _DETAILS["pyd"], False),
                ("10.1/i", "outside_grammar", _DETAILS["float"], False),
                ("10.1/j", "outside_grammar", _DETAILS["coef"], False),
                ("10.1/k", "outside_grammar", _DETAILS["braces"], False),
                ("10.1/l", "outside_grammar", _DETAILS["nocmp"], False),
                ("10.1/m", "outside_grammar", _DETAILS["other"], False),
                ("10.1/n", "no_objective", "", False),
                ("10.1/o", "not_sorted", "", False),
            ]
        ),
    )
    _write(corpus / "objective_flags.json", {"counts": {"ok": 4, "unmarked": 1, "absent": 0}})
    for name, source in (("g1.json", "corpusbuilder.assist deepseek"), ("g2.json", "game")):
        _write(
            corpus / "decisions" / name,
            {
                "schema_version": "game-decisions-3",
                "source": source,
                "formula_decisions": [
                    {
                        "paper_key": "10.1/a",
                        "doi": "10.1/a",
                        "decisions": [
                            {"id": "eq-0001", "status": "accepted"},
                            {"id": "eq-0002", "status": "corrected", "parts": ["a", "b"]},
                        ],
                    }
                ],
                "symbol_tables": [{"paper_key": "10.1/a"}],
            },
        )
    _write(corpus / "declarations" / "10.1_a.tex", "%@ var x shape=- domain=continuous\n")
    _write(corpus / "declarations" / "10.1_b.tex", "%@ var y shape=- domain=continuous\n")
    _write(corpus / "declarations" / "10.1_c.stub.tex", "% stub\n")
    _write(corpus / "promoted" / "10.1_a.tex", "% assembled\n")
    _write(corpus / "formulations" / "10.1_a.json", {"id": "a"})
    _write(corpus / "formulations" / "seed_template.json", {"id": "seed"})
    _write(corpus / "repo_formulations" / "org__repo__model.json", {"id": "r"})
    _write(corpus / "repo_formulations" / "org__repo__model.meta.json", {"license": "MIT"})
    _write(corpus / "repo_formulations" / "_report.json", {"converted": 1, "failed": ["x", "y"]})
    _write(corpus / "repo_formulations" / "manual" / "hand.json", {"id": "h"})
    _write(corpus / "repo_formulations" / "manual" / "hand.meta.json", {"license": "Apache-2.0"})
    _write(corpus / "instances" / "i1.json", {})
    _write(
        corpus / "wl" / "similarity.json",
        {
            "iterations": 3,
            "models": ["a", "b"],
            "top_pairs": [{"a": "a", "b": "b", "similarity": 0.5}],
        },
    )
    _write(corpus / "fingerprint" / "clusters.json", {"k": 3, "silhouette": 0.4})
    _write(
        corpus / "vdemo3" / "10.1_a--feedback" / "report.json",
        {
            "converged": True,
            "final_verdict": "valid_with_warnings",
            "scenario": {"chars": 10, "id": "10.1/a"},
        },
    )
    _write(
        corpus / "vdemo3" / "10.1_b--feedback" / "report.json",
        {"converged": False, "final_verdict": "invalid"},
    )
    _write(corpus / "vdemo3" / "rematch.json", {"match_set_size": 7, "runs": {}})
    (corpus / "talkpack" / "figures").mkdir(parents=True)
    (corpus / "talkpack" / "figures" / "fig_a.png").write_bytes(b"png")
    (corpus / "talkpack" / "figures" / "fig_a.pdf").write_bytes(b"pdf")
    _write(corpus / "prisma_macros.tex", "\\newcommand{\\x}{1}\n")
    _write(
        outputs / "validation_report.json",
        {"solvers_used": ["CBC", "HiGHS"], "structural_pass_rate": 1.0, "external": [1, 2, 3]},
    )
    _write(
        outputs / "run_summary.json",
        {"n_formulations": 2, "versions": {"rewrite_rules": "rewrite-9999.01.0"}},
    )
    _write(outputs / "taxonomy.json", {"axes": [1, 2]})
    return {"corpus": corpus, "outputs": outputs}


# ---------------------------------------------------------------------------
# Failure classes
# ---------------------------------------------------------------------------


def test_classify_detail_uses_the_agreed_rule_set() -> None:
    expected = {
        "sup": "superscript after subscript",
        "jux": "juxtaposed factor / residue",
        "chain3": "chain: 3+ comparators",
        "mixed": "chain: mixed / equality",
        "label": "label subscript",
        "pyd": "parenthesised / text residue",
        "float": "non-numeric coefficient",
        "coef": "subscripted-coef shape mismatch",
        "braces": "unbalanced braces",
        "nocmp": "no comparator",
        "other": "other",
    }
    for key, label in expected.items():
        assert factory.classify_detail(_DETAILS[key]) == label, key
    assert factory.classify_detail(None) == "other"
    # The superscript test must win over the generic "trailing" test.
    assert factory.classify_detail("trailing '^{k}' after") == "superscript after subscript"


def test_failure_bins_count_classes_largest_first(floor) -> None:
    bins = factory.failure_bins(
        json.loads((floor["corpus"] / "promotion.json").read_text(encoding="utf-8"))
    )
    assert bins[0] == {
        "label": "grammar: superscript after subscript",
        "category": "outside_grammar",
        "n": 2,
    }
    labels = {b["label"]: b for b in bins}
    assert labels["no objective"]["category"] == "under_specified"
    assert labels["not sorted"]["category"] == "pipeline_incomplete"
    assert sum(b["n"] for b in bins) == 14  # every failed paper lands in exactly one bin
    assert [b["n"] for b in bins] == sorted((b["n"] for b in bins), reverse=True)
    assert factory.failure_bins(None) == []


# ---------------------------------------------------------------------------
# Snapshot + page
# ---------------------------------------------------------------------------


def _by_id(data: dict) -> dict[str, dict]:
    return {s["id"]: s for s in data["stations"]}


def test_snapshot_reads_every_station(floor) -> None:
    data = factory.snapshot(floor["corpus"], outputs=floor["outputs"], paper_dir=None)
    st = _by_id(data)
    assert st["discover"]["count"] == "4"
    assert st["snowball"]["count"] == "9"
    assert st["harvest"]["count"] == "6" and "5 with formulas" in st["harvest"]["artifact"]["count"]
    assert st["review"]["count"] == "30"
    assert "1 human-sourced" in st["review"]["artifact"]["count"]
    assert st["split"]["count"] == "2"  # both exports carry the same 2-part correction
    assert st["resolution"]["count"] == "50.0%"
    assert st["assist"]["count"] == "2" and "1 stubs" in st["assist"]["count_label"]
    assert st["vocab"]["kind"] == "planned" and st["vocab"]["artifact"]["present"] is False
    assert st["promote"]["count"] == "1 / 15" and st["promote"]["kind"] == "gate"
    assert st["codec"]["count"] == "rewrite-9999.01.0"
    assert st["codec"]["artifact"]["count"] == "1 mined · 1 seed · 2 repo"
    assert st["verifier"]["count"] == "1 / 2" and "match set 7" in st["verifier"]["count_label"]
    assert st["validation"]["count"] == "100.0%" and "CBC / HiGHS" in st["validation"]["role"]
    assert st["wlcluster"]["count"] == "2" and "0.50" in st["wlcluster"]["count_label"]
    assert st["talkpack"]["count"] == "1" and "1 PDF" in st["talkpack"]["count_label"]
    assert data["macros"] == {
        "prisma_macros.tex": "not copied to the paper",
        "resolution_macros.tex": "no artifact yet",
    }
    assert data["gate"]["promoted"] == 1 and len(data["gate"]["bins"]) == 13
    assert {e["from"] for e in data["edges"]} <= set(st)
    assert {e["to"] for e in data["edges"]} <= set(st)
    assert data["snapshot_of"] is not None


def test_build_is_deterministic_and_complete(floor, tmp_path) -> None:
    out1 = tmp_path / "one" / "factory.html"
    out2 = tmp_path / "two" / "factory.html"
    factory.build(floor["corpus"], out=out1, outputs=floor["outputs"], paper_dir=None)
    factory.build(floor["corpus"], out=out2, outputs=floor["outputs"], paper_dir=None)
    assert out1.read_bytes() == out2.read_bytes()
    assert (out1.parent / "factory_data.json").read_bytes() == (
        out2.parent / "factory_data.json"
    ).read_bytes()
    html = out1.read_text(encoding="utf-8")
    data = factory.snapshot(floor["corpus"], outputs=floor["outputs"], paper_dir=None)
    for s in data["stations"]:
        assert s["title"] in html, s["id"]
        if s.get("stereo"):
            assert s["stereo"] in html, s["id"]
    for lane in factory.LANES:
        assert lane["name"] in html
    assert 'id="data"' in html and "prefers-color-scheme: dark" in html
    assert "the difference is the fill-in list" in html  # the vocabulary-check legend
    assert "</script>" not in json.dumps(data)  # nothing in the data can close the data block


def test_missing_artifacts_degrade_gracefully(tmp_path) -> None:
    empty = tmp_path / "corpus"
    empty.mkdir()
    data = factory.snapshot(empty, outputs=None, paper_dir=None)
    st = _by_id(data)
    assert len(st) == 18
    assert st["promote"]["count"] == "— / —"
    assert data["gate"]["bins"] == [] and data["snapshot_of"] is None
    assert all(not s["artifact"]["present"] for s in data["stations"] if s.get("artifact"))
    html_path, data_path = factory.build(empty, outputs=None, paper_dir=None)
    assert html_path.exists() and data_path.exists()
    assert "no artifacts yet" in json.dumps(data) or data["snapshot_of"] is None


def test_page_carries_no_promotion_details(floor, tmp_path) -> None:
    out = tmp_path / "factory.html"
    factory.build(floor["corpus"], out=out, outputs=floor["outputs"], paper_dir=None)
    html = out.read_text(encoding="utf-8")
    data = (out.parent / "factory_data.json").read_text(encoding="utf-8")
    for detail in _DETAILS.values():
        assert detail not in html and detail not in data
    for fragment in ("v_{i}^{c}", "p_{a} x_{a}", "Buff_max", "trailing '", "\\leq"):
        assert fragment not in html and fragment not in data, fragment


# ---------------------------------------------------------------------------
# Heartbeat
# ---------------------------------------------------------------------------


def test_beat_and_running_round_trip(tmp_path, monkeypatch) -> None:
    clock = iter(
        ["2026-09-10T10:00:00+00:00", "2026-09-10T10:00:05+00:00", "2026-09-10T10:00:09+00:00"]
    )
    monkeypatch.setattr(factory, "_now", lambda: next(clock))
    path = tmp_path / "status.json"
    with factory.running("promote", total=3, path=path) as tick:
        st = factory.read_status(path)
        assert st["state"] == "running" and st["done"] == 0 and st["total"] == 3
        tick(2, note="10.1/b")
        st = factory.read_status(path)
        assert (st["done"], st["note"], st["started_at"]) == (
            2,
            "10.1/b",
            "2026-09-10T10:00:00+00:00",
        )
    st = factory.read_status(path)
    assert st["state"] == "done" and st["stage"] == "promote"
    assert st["history"][-1]["seconds"] == 9 and st["history"][-1]["state"] == "done"
    assert not path.with_name(path.name + ".tmp").exists()  # atomic replace leaves no temp file


def test_running_records_failure_and_reraises(tmp_path) -> None:
    path = tmp_path / "status.json"
    with pytest.raises(ValueError, match="boom"), factory.running("assist", total=2, path=path):
        raise ValueError("boom")
    st = factory.read_status(path)
    assert st["state"] == "failed" and "boom" in st["note"]
    assert st["history"][-1]["state"] == "failed"


def test_beat_never_raises(tmp_path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a dir", encoding="utf-8")
    factory.beat("promote", 1, 2, path=blocker / "status.json")  # parent is a file: mkdir fails
    assert factory.read_status(blocker / "status.json") is None


def test_env_can_disable_or_redirect_heartbeats(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("RAILP_FACTORY_STATUS", "off")
    assert factory.status_path() is None
    factory.beat("promote", 1, 1)  # no-op, no error
    target = tmp_path / "elsewhere.json"
    monkeypatch.setenv("RAILP_FACTORY_STATUS", str(target))
    factory.beat("promote", 1, 1, "x")
    assert factory.read_status()["stage"] == "promote" and target.exists()


def test_history_is_capped(tmp_path) -> None:
    path = tmp_path / "status.json"
    for i in range(factory.HISTORY_LIMIT + 5):
        with factory.running(f"s{i}", path=path):
            pass
    assert len(factory.read_status(path)["history"]) == factory.HISTORY_LIMIT


def test_promote_all_reports_progress_without_changing_the_result(tmp_path) -> None:
    decisions = tmp_path / "decisions"
    _write(
        decisions / "game_decisions_2026-09-10.json",
        {
            "schema_version": "game-decisions-3",
            "source": "game",
            "formula_decisions": [
                {
                    "paper_key": "10.9/zzz",
                    "doi": "10.9/zzz",
                    "decisions": [{"id": "eq-0001", "status": "accepted"}],
                }
            ],
            "paper_cells": [],
        },
    )
    seen: list[tuple[int, int, str]] = []
    plain = promote.promote_all(
        decisions_dir=decisions, dossiers_dir=tmp_path / "none", write=False
    )
    hooked = promote.promote_all(
        decisions_dir=decisions,
        dossiers_dir=tmp_path / "none",
        write=False,
        progress=lambda d, t, k: seen.append((d, t, k)),
    )
    assert seen == [(0, 1, "10.9/zzz")]
    assert plain == hooked


# ---------------------------------------------------------------------------
# Local live server
# ---------------------------------------------------------------------------


def test_serve_once_answers_with_no_store_headers(floor) -> None:
    ready = threading.Event()
    box: dict[str, str] = {}

    def on_ready(url: str) -> None:
        box["url"] = url
        ready.set()

    t = threading.Thread(
        target=factory.serve,
        args=(floor["corpus"], 0),
        kwargs={"outputs": floor["outputs"], "paper_dir": None, "once": True, "on_ready": on_ready},
        daemon=True,
    )
    t.start()
    assert ready.wait(10), "server never announced its URL"
    with urllib.request.urlopen(box["url"], timeout=10) as resp:  # loopback only
        body = resp.read().decode("utf-8")
        assert resp.status == 200
        assert resp.headers["Cache-Control"] == "no-store"
    assert "Factory floor" in body
    t.join(10)
    assert not t.is_alive()
    assert (floor["corpus"] / "factory_data.json").exists()


def test_classifier_names_the_vocabulary_bins() -> None:
    # rewrite-2026.09.0 refuses undeclared symbols by name; those are the
    # sidecar's fill-in list, not a parser hole.
    assert (
        factory.classify_detail(
            "normalized LaTeX is not in the canonical grammar: referent 'v_c' is not a "
            "declared variable or parameter (declare it in the %@ header)"
        )
        == "undeclared symbol (vocabulary)"
    )
    assert (
        factory.classify_detail(
            "normalized LaTeX is not in the canonical grammar: coefficient 'B_u' is not a "
            "declared parameter (declare 'B_u' in the %@ header)"
        )
        == "undeclared coefficient (vocabulary)"
    )


# ---------------------------------------------------------------------------
# Public navigator build (docs/factory.html) + swimlane buttons
# ---------------------------------------------------------------------------


def test_public_build_is_deterministic_relative_and_silent(floor, tmp_path) -> None:
    out1 = tmp_path / "one" / "factory.html"
    out2 = tmp_path / "two" / "factory.html"
    p1, d1 = factory.build(
        floor["corpus"], out=out1, outputs=floor["outputs"], paper_dir=None, public=True
    )
    p2, d2 = factory.build(
        floor["corpus"], out=out2, outputs=floor["outputs"], paper_dir=None, public=True
    )
    assert d1 is None and d2 is None  # nothing to poll on the demo site
    assert not (out1.parent / factory.DATA_NAME).exists()
    assert p1.read_bytes() == p2.read_bytes()
    html = p1.read_text(encoding="utf-8")
    assert 'class="proto"' in html and "working prototype" in html
    assert html.index('class="proto"') < html.index("<header>")
    assert '"public": true' in html and "!DATA.public" in html  # polling is gated off
    for href in ('href="prisma.html"', 'href="game.html#run"', 'href="./"', factory.LP2GRAPH_URL):
        assert href in html, href
    assert factory.ARXIV_URL in html and factory.REPO_URL in html
    assert factory.SITE_URL not in html.split('id="data"')[1]  # own links are relative in the data
    assert 'id="rotBtn"' in html and "factory:orient" in html


def test_local_build_links_the_site_absolutely(floor, tmp_path) -> None:
    out = tmp_path / "factory.html"
    factory.build(floor["corpus"], out=out, outputs=floor["outputs"], paper_dir=None)
    html = out.read_text(encoding="utf-8")
    assert '"public": false' in html and 'class="proto"' not in html
    assert factory.SITE_URL + "prisma.html" in html and factory.SITE_URL + "game.html#run" in html


def test_every_lane_has_buttons_and_stations_link_to_subpages(floor) -> None:
    data = factory.snapshot(floor["corpus"], outputs=floor["outputs"], paper_dir=None, public=True)
    assert [lane["id"] for lane in data["lanes"]] == [lane["id"] for lane in factory.LANES]
    for lane in data["lanes"]:
        assert lane["links"], lane["id"]
        for link in lane["links"]:
            assert link["label"] and link["href"]
    linked = {s["id"]: s["href"] for s in data["stations"] if s.get("href")}
    assert linked["prisma"] == "prisma.html" and linked["review"] == "game.html#run"
    assert linked["paper"] == factory.ARXIV_URL
    assert "join" not in linked and "vocab" not in linked
    for s in data["stations"]:
        if s.get("href") and not s["href"].startswith("https://"):
            assert s["href"].split("#")[0] in ("prisma.html", "game.html")


def test_public_build_refuses_a_leaked_paper_key(floor, tmp_path, monkeypatch) -> None:
    real = factory.snapshot

    def leaky(*args, **kwargs):
        data = real(*args, **kwargs)
        data["notes"] = [*data["notes"], "assist retried 10.1016_j.trc.2020.102823"]
        return data

    monkeypatch.setattr(factory, "snapshot", leaky)
    with pytest.raises(ValueError, match="paper key"):
        factory.build(
            floor["corpus"],
            out=tmp_path / "f.html",
            outputs=floor["outputs"],
            paper_dir=None,
            public=True,
        )
    assert not (tmp_path / "f.html").exists()
