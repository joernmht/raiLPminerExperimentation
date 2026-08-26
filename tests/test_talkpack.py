"""Tests for :mod:`corpusbuilder.talkpack` — the deterministic talk pack.

Everything runs offline against a tiny synthetic corpus written into a tmp
directory (three included papers, one excluded metadata-only dossier), plus
hand-written ``resolution.json`` / ``objective_flags.json`` / ``prisma.json``
/ ``promotion.json`` / vdemo summaries. The matplotlib Agg backend is forced
by the module itself at import time, so no display is ever needed.

What is deliberately NOT covered: the lp2graph-backed present-path of
``fig_architectures`` / ``fig_taxonomy`` (it needs > 20 promoted
Formulations plus the clustering extras — the gate itself and the
formulation -> dossier key mapping are covered instead).
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib
import pytest

from corpusbuilder import talkpack


def test_agg_backend_is_forced() -> None:
    # The module must pin Agg at import so figure runs are headless-safe.
    assert matplotlib.get_backend().lower() == "agg"


# ---------------------------------------------------------------------------
# Synthetic corpus fixture
# ---------------------------------------------------------------------------


def _dossier(key: str, year: int | None, n_formulas: int, venue: str = "Test J.") -> dict:
    formulas = [
        {
            "id": f"eq-{i:04d}",
            "latex": (
                r"\min \sum_{i \in I} c_i x_i"
                if i == 0
                else rf"\sum_{{i \in I}} a_{{i{i}}} x_i \leq b_{{{i}}}"
            ),
            "status": "candidate",
        }
        for i in range(n_formulas)
    ]
    return {
        "schema_version": "dossier-1",
        "source": {
            "title": f"Paper {key}",
            "doi": f"10.9999/{key}",
            "year": year,
            "venue": venue,
            "publisher": "TestPub",
            "cited_by_count": 10,
            "scopus_cited_by_count": None,
        },
        "formulas": formulas,
    }


@pytest.fixture()
def corpus(tmp_path: Path) -> dict:
    """A tiny but complete synthetic corpus on disk; returns the path map."""
    dossiers = tmp_path / "dossiers"
    dossiers.mkdir()
    records = {
        "10.9999_alpha": _dossier("alpha", 2015, 3),
        "10.9999_beta": _dossier("beta", 2015, 2),
        "10.9999_gamma": _dossier("gamma", 2021, 4),
        "10.9999_empty": _dossier("empty", 2020, 0),  # metadata-only: excluded
    }
    for key, rec in records.items():
        (dossiers / f"{key}.json").write_text(json.dumps(rec), encoding="utf-8")

    resolution = tmp_path / "resolution.json"
    resolution.write_text(
        json.dumps(
            {
                "formulas": 9,
                "papers": 3,
                "structural_axes": {"parses": 9, "single": 8, "renders": 8, "statement": 9},
                "structurally_clean": 7,
                "structurally_clean_pct": 77.8,
                "resolved": 2,
                "resolved_pct": 22.2,
                "ready": 1,
                "prefilled_pairs": 5,
                "symbol_pairs": 12,
            }
        ),
        encoding="utf-8",
    )
    flags = tmp_path / "objective_flags.json"
    flags.write_text(
        json.dumps({"counts": {"ok": 2, "unmarked": 1, "absent": 0}}), encoding="utf-8"
    )
    prisma = tmp_path / "prisma.json"
    prisma.write_text(
        json.dumps(
            {
                "flow": {
                    "freeze_date": "2020-01-01",
                    "identification": {
                        "database_search_records": 6,
                        "database_queries": 2,
                        "duplicates_removed": 1,
                        "database_unique_records": 5,
                        "citation_search_records_identified": 40,
                        "citation_search_recommended": 12,
                    },
                    "retrieval_eligibility": {
                        "reports_retrieved": 4,
                        "from_database_arm": 3,
                        "from_citation_arm": 1,
                        "reports_excluded": {"not_entitled": 1},
                        "reports_excluded_total": 1,
                    },
                    "included": {
                        "source_papers": 3,
                        "candidate_formulations": 9,
                        "hitl_review": {
                            "accepted": 4,
                            "corrected": 1,
                            "duplicate": 1,
                            "rejected": 2,
                            "unreviewed": 1,
                        },
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    promotion = tmp_path / "promotion.json"
    promotion.write_text(
        json.dumps(
            {
                "papers_with_decisions": 3,
                "promoted": 1,
                "failed": 2,
                "failures_by_cause": {
                    "missing_declarations": {"papers": 1, "category": "under_specified"},
                    "not_reviewed": {"papers": 1, "category": "pipeline_incomplete"},
                },
                "failures_by_category": {"pipeline_incomplete": 1, "under_specified": 1},
            }
        ),
        encoding="utf-8",
    )
    vdemo = tmp_path / "vdemo"
    for arm, valid, rounds in (("feedback", 8, [1, 2, 2]), ("single", 5, None)):
        d = vdemo / f"toy--{arm}"
        d.mkdir(parents=True)
        payload: dict = {"scenario": "toy", "mode": arm, "runs": 10, "valid": valid}
        if rounds:
            payload["rounds_to_valid"] = rounds
        (d / "summary.json").write_text(json.dumps(payload), encoding="utf-8")

    out = tmp_path / "talkpack"
    return {
        "tmp": tmp_path,
        "dossiers": dossiers,
        "resolution": resolution,
        "flags": flags,
        "prisma": prisma,
        "promotion": promotion,
        "vdemo": vdemo,
        "formulations": tmp_path / "formulations",  # intentionally absent
        "out": out,
    }


# ---------------------------------------------------------------------------
# Data layer
# ---------------------------------------------------------------------------


def test_load_papers_included_and_sorted(corpus: dict) -> None:
    papers = talkpack.load_papers(corpus["dossiers"])
    assert [p["key"] for p in papers] == sorted(p["key"] for p in papers)
    assert len(papers) == 4
    inc = talkpack._included(papers)
    assert len(inc) == 3  # the empty dossier is metadata-only
    assert all(p["n_formulas"] > 0 for p in inc)


def test_data_prisma_flow(corpus: dict) -> None:
    data = talkpack.data_prisma_flow(corpus["prisma"])
    assert data["freeze_date"] == "2020-01-01"
    assert (data["db_records"], data["db_queries"], data["db_unique"]) == (6, 2, 5)
    assert (data["citation_records"], data["citation_recommended"]) == (40, 12)
    assert (data["retrieved"], data["from_database_arm"], data["citation_retrieved"]) == (4, 3, 1)
    assert data["excluded"] == {"not_entitled": 1}
    assert (data["papers_included"], data["formulas_total"]) == (3, 9)
    # Verdict order is the pipeline's own order, not the JSON's key order.
    assert [v["label"] for v in data["verdicts"]] == [
        "accepted",
        "corrected",
        "duplicate",
        "rejected",
        "unreviewed",
    ]
    # Every extracted formulation carries exactly one verdict.
    assert sum(v["count"] for v in data["verdicts"]) == data["formulas_total"]


def test_data_prisma_flow_missing_artifact(tmp_path) -> None:
    assert talkpack.data_prisma_flow(tmp_path / "nope.json") is None


def _png_size(path) -> tuple[int, int]:
    """Width/height straight out of the PNG IHDR chunk (no image library)."""
    head = Path(path).read_bytes()[16:24]
    return int.from_bytes(head[:4], "big"), int.from_bytes(head[4:], "big")


def test_fig_prisma_flow_is_uncropped_widescreen(corpus: dict) -> None:
    png = talkpack.fig_prisma_flow(corpus["out"], corpus["prisma"])
    assert png is not None and png.exists()
    assert png.with_suffix(".svg").exists()
    # The whole point of tight=False: the canvas stays exactly 16:9 for a slide.
    w, h = _png_size(png)
    assert w / h == pytest.approx(16 / 9, abs=1e-3)


def test_fig_prisma_flow_skips_without_artifact(corpus: dict, tmp_path) -> None:
    assert talkpack.fig_prisma_flow(corpus["out"], tmp_path / "nope.json") is None


def test_data_timeline(corpus: dict) -> None:
    data = talkpack.data_timeline(talkpack.load_papers(corpus["dossiers"]))
    assert data == {
        "papers_included": 3,
        "papers_undated": 0,
        "span": [2015, 2021],
        "per_year": {"2015": 2, "2021": 1},
    }


def test_data_formulas_by_year(corpus: dict) -> None:
    data = talkpack.data_formulas_by_year(talkpack.load_papers(corpus["dossiers"]))
    assert data["formulas_total"] == 9
    assert data["per_year"] == {"2015": 5, "2021": 4}
    assert data["median_per_paper_by_year"] == {"2015": 2.5, "2021": 4.0}


def test_data_structural_yield_row_order(corpus: dict) -> None:
    data = talkpack.data_structural_yield(corpus["resolution"])
    labels = [r["label"] for r in data["rows"]]
    assert labels[0] == "extracted"
    assert labels[-2:] == ["fully symbol-resolved (beta = 1)", "ready (clean + beta = 1)"]
    classes = [r["class"] for r in data["rows"]]
    assert classes == ["volume", "axis", "axis", "axis", "axis", "clean", "resolved", "resolved"]


def test_data_vdemo_two_arms(corpus: dict) -> None:
    data = talkpack.data_vdemo(corpus["vdemo"])
    toy = data["scenarios"]["toy"]
    assert toy["feedback"]["valid_rate"] == 0.8
    assert toy["feedback"]["mean_rounds_to_valid"] == pytest.approx(1.67, abs=0.01)
    assert toy["single"]["valid_rate"] == 0.5
    assert toy["single"]["mean_rounds_to_valid"] is None


def test_paper_check_summary_reuses_game_check(corpus: dict) -> None:
    # Every synthetic paper has one \min objective, constraints sharing x/I,
    # so the game's _paper_check must call all three papers complete.
    papers = talkpack.load_papers(corpus["dossiers"])
    summary = talkpack.paper_check_summary(papers)
    assert summary["papers_checked"] == 3
    assert summary["complete"] == 3
    assert summary["with_objective"] == 3
    assert summary["median_coherence"] == 1.0


# ---------------------------------------------------------------------------
# Figures: rendered files, determinism, graceful skips
# ---------------------------------------------------------------------------


def _assert_pair(png: Path) -> None:
    assert png is not None and png.exists() and png.suffix == ".png"
    assert png.stat().st_size > 0
    svg = png.with_suffix(".svg")
    assert svg.exists() and svg.stat().st_size > 0
    assert png.parent.name == "figures"


def test_fig_timeline_renders(corpus: dict) -> None:
    _assert_pair(talkpack.fig_timeline(corpus["out"], corpus["dossiers"]))


def test_fig_formulas_by_year_renders(corpus: dict) -> None:
    _assert_pair(talkpack.fig_formulas_by_year(corpus["out"], corpus["dossiers"]))


def test_fig_structural_yield_renders(corpus: dict) -> None:
    _assert_pair(talkpack.fig_structural_yield(corpus["out"], corpus["resolution"]))


def test_fig_objective_status_renders(corpus: dict) -> None:
    _assert_pair(talkpack.fig_objective_status(corpus["out"], corpus["flags"]))


def test_fig_promotion_renders_when_report_exists(corpus: dict) -> None:
    _assert_pair(talkpack.fig_promotion(corpus["out"], corpus["promotion"]))


def test_fig_vdemo_renders_when_summaries_exist(corpus: dict) -> None:
    _assert_pair(talkpack.fig_vdemo(corpus["out"], corpus["vdemo"]))


def test_png_bytes_are_deterministic(corpus: dict, tmp_path: Path) -> None:
    a = talkpack.fig_structural_yield(tmp_path / "a", corpus["resolution"])
    b = talkpack.fig_structural_yield(tmp_path / "b", corpus["resolution"])
    assert a.read_bytes() == b.read_bytes()


def test_fig_promotion_skips_gracefully(corpus: dict, capsys) -> None:
    missing = corpus["tmp"] / "nope" / "promotion.json"
    assert talkpack.fig_promotion(corpus["out"], missing) is None
    assert "skip fig_promotion" in capsys.readouterr().out


def test_fig_vdemo_skips_gracefully(corpus: dict, capsys) -> None:
    assert talkpack.fig_vdemo(corpus["out"], corpus["tmp"] / "no_vdemo") is None
    assert "skip fig_vdemo" in capsys.readouterr().out


def test_fig_architectures_gate_skips(corpus: dict, capsys) -> None:
    # No formulations dir at all -> below the >20 real-formulations gate.
    assert (
        talkpack.fig_architectures(corpus["out"], corpus["formulations"], corpus["dossiers"])
        is None
    )
    assert "skip fig_architectures" in capsys.readouterr().out


def test_fig_taxonomy_gate_skips(corpus: dict, capsys) -> None:
    assert talkpack.fig_taxonomy(corpus["out"], corpus["formulations"], corpus["dossiers"]) is None
    assert "skip fig_taxonomy" in capsys.readouterr().out


def test_entry_id_mapping_matches_promote(corpus: dict) -> None:
    # The formulation -> dossier mapping fig_architectures documents: the
    # Formulation id of a promoted paper is promote.entry_id_for(dossier key).
    from corpusbuilder.promote import entry_id_for

    stems = sorted(p.stem for p in corpus["dossiers"].glob("*.json"))
    ids = [entry_id_for(s) for s in stems]
    assert len(set(ids)) == len(ids)  # injective over the corpus keys
    assert entry_id_for("10.9999_alpha") == "10.9999_alpha"


# ---------------------------------------------------------------------------
# The pack: run(), numbers.json, RESULTS.md, selection
# ---------------------------------------------------------------------------


def _run_all(corpus: dict, only: str | None = None) -> dict:
    return talkpack.run(
        out=corpus["out"],
        only=only,
        dossier_dir=corpus["dossiers"],
        resolution_path=corpus["resolution"],
        flags_path=corpus["flags"],
        prisma_path=corpus["prisma"],
        promotion_path=corpus["promotion"],
        formulations_dir=corpus["formulations"],
        vdemo_dir=corpus["vdemo"],
        # Point at a directory that does not exist: the fingerprint figures
        # must gracefully skip, and the test must never read the real corpus.
        fingerprint_dir=corpus["out"] / "no-fingerprint",
    )


def test_run_full_pack(corpus: dict) -> None:
    summary = _run_all(corpus)
    assert summary["rendered"] == [
        "prisma_flow",
        "timeline",
        "formulas_by_year",
        "structural_yield",
        "objective_status",
        "promotion",
        "vdemo",
    ]
    assert set(summary["skipped"]) == {
        "architectures",
        "taxonomy",
        "fingerprint_families",
        "fingerprint_timeline",
    }

    numbers = json.loads((corpus["out"] / "numbers.json").read_text(encoding="utf-8"))
    head = numbers["headline"]
    assert head["papers_included"] == 3
    assert head["formulas_extracted"] == 9
    assert head["structurally_clean"] == 7
    assert head["objective_ok"] == 2
    assert head["papers_complete_heuristic"] == 3
    assert head["prisma_reports_retrieved"] == 4
    assert numbers["figures"]["timeline"]["span"] == [2015, 2021]
    assert "skipped" in numbers["figures"]["architectures"]

    results = (corpus["out"] / "RESULTS.md").read_text(encoding="utf-8")
    assert "Headline numbers" in results
    assert "figures/fig_timeline.png" in results
    assert "fig_architectures: not rendered" in results
    assert "—" not in results  # house style: no em-dashes in prose


def test_run_only_selection(corpus: dict) -> None:
    summary = _run_all(corpus, only="fig3,objective_status")
    assert summary["rendered"] == ["structural_yield", "objective_status"]
    figdir = corpus["out"] / "figures"
    assert sorted(p.name for p in figdir.glob("*.png")) == [
        "fig_objective_status.png",
        "fig_structural_yield.png",
    ]
    numbers = json.loads((corpus["out"] / "numbers.json").read_text(encoding="utf-8"))
    assert set(numbers["figures"]) == {"structural_yield", "objective_status"}


def test_select_aliases() -> None:
    assert talkpack._select("fig6") == ["architectures", "taxonomy"]
    assert talkpack._select(None) == talkpack._ORDER
    with pytest.raises(SystemExit):
        talkpack._select("fig99")


def test_numbers_json_is_deterministic(corpus: dict) -> None:
    _run_all(corpus, only="fig1")
    first = (corpus["out"] / "numbers.json").read_bytes()
    _run_all(corpus, only="fig1")
    assert (corpus["out"] / "numbers.json").read_bytes() == first
