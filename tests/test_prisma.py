"""PRISMA flow artifact — the tally behind every ``n =`` number in the paper.

These pin the HITL arm specifically. It is the one part of the flow whose input
does not exist yet in ``corpus/`` (no reviewer has exported decisions), so it was
the one part no run had ever exercised — and it was silently reporting zeros.
"""

from __future__ import annotations

import json

from corpusbuilder.prisma import HITL_STATUSES, hitl_tally


def _write(tmp_path, name: str, payload: dict) -> str:
    p = tmp_path / name
    p.write_text(json.dumps(payload))
    return str(p)


def _game_export(paper: str, decisions: list[dict]) -> dict:
    """A ``game-decisions-1`` export, as ``corpusbuilder.game`` actually writes it."""
    return {
        "schema_version": "game-decisions-1",
        "exported": "2026-08-11T00:00:00Z",
        "formula_decisions": [{"paper_key": paper, "doi": "10.1/x", "decisions": decisions}],
        "paper_cells": [],
    }


def test_no_decision_files_means_everything_is_unreviewed(tmp_path) -> None:
    tally = hitl_tally([], formulas_total=8957)
    assert tally["unreviewed"] == 8957
    assert all(tally[s] == 0 for s in HITL_STATUSES)


def test_game_export_schema_is_counted(tmp_path) -> None:
    """Regression: the game nests decisions under ``formula_decisions``.

    Reading only the top-level ``decisions`` key made every count — including
    ``unreviewed`` — collapse to zero the moment a reviewer exported anything.
    """
    f = _write(
        tmp_path,
        "game_decisions_2026-08-11.json",
        _game_export(
            "p1",
            [
                {"id": "f1", "status": "accepted"},
                {"id": "f2", "status": "corrected"},
                {"id": "f3", "status": "rejected"},
                {"id": "f4", "status": "unreviewed"},
            ],
        ),
    )
    tally = hitl_tally([f], formulas_total=100)
    assert tally["accepted"] == 1
    assert tally["corrected"] == 1
    assert tally["rejected"] == 1
    assert tally["unreviewed"] == 97  # 100 total - 3 decided, not 0 and not 1


def test_single_paper_review_view_schema_is_still_counted(tmp_path) -> None:
    """The older ``review_view`` export puts ``decisions`` at the top level."""
    f = _write(
        tmp_path,
        "decisions_p1.json",
        {"paper_key": "p1", "doi": "10.1/x", "decisions": [{"id": "f1", "status": "accepted"}]},
    )
    assert hitl_tally([f], formulas_total=10)["accepted"] == 1


def test_duplicate_verdict_is_its_own_box(tmp_path) -> None:
    """``duplicate`` (⧉) is a real verdict and must not vanish from the flow."""
    f = _write(tmp_path, "g.json", _game_export("p1", [{"id": "f1", "status": "duplicate"}]))
    tally = hitl_tally([f], formulas_total=10)
    assert tally["duplicate"] == 1
    assert tally["unreviewed"] == 9


def test_reexports_do_not_double_count(tmp_path) -> None:
    """Two exports of the same review state must tally once, not twice."""
    d = [{"id": "f1", "status": "accepted"}, {"id": "f2", "status": "rejected"}]
    files = [
        _write(tmp_path, "game_decisions_2026-08-10.json", _game_export("p1", d)),
        _write(tmp_path, "game_decisions_2026-08-11.json", _game_export("p1", d)),
    ]
    tally = hitl_tally(files, formulas_total=10)
    assert (tally["accepted"], tally["rejected"], tally["unreviewed"]) == (1, 1, 8)


def test_later_export_supersedes_an_earlier_verdict(tmp_path) -> None:
    files = [
        _write(
            tmp_path,
            "game_decisions_2026-08-10.json",
            _game_export("p1", [{"id": "f1", "status": "accepted"}]),
        ),
        _write(
            tmp_path,
            "game_decisions_2026-08-11.json",
            _game_export("p1", [{"id": "f1", "status": "rejected"}]),
        ),
    ]
    tally = hitl_tally(files, formulas_total=10)
    assert tally["accepted"] == 0
    assert tally["rejected"] == 1


def test_same_formula_id_in_different_papers_counts_twice(tmp_path) -> None:
    """Dedup is per ``(paper, formula)`` — ids are only unique within a paper."""
    payload = {
        "schema_version": "game-decisions-1",
        "formula_decisions": [
            {"paper_key": "p1", "decisions": [{"id": "eq1", "status": "accepted"}]},
            {"paper_key": "p2", "decisions": [{"id": "eq1", "status": "accepted"}]},
        ],
    }
    assert hitl_tally([_write(tmp_path, "g.json", payload)], formulas_total=10)["accepted"] == 2


def test_unknown_status_is_reported_not_dropped(tmp_path) -> None:
    """Honesty about coverage: an unmodelled verdict surfaces in the artifact."""
    f = _write(tmp_path, "g.json", _game_export("p1", [{"id": "f1", "status": "deferred"}]))
    tally = hitl_tally([f], formulas_total=10)
    assert tally["unrecognised_status"] == {"deferred": 1}
    assert tally["unreviewed"] == 9  # still counted as decided-away from the residual


def test_tally_is_deterministic_regardless_of_file_argument_order(tmp_path) -> None:
    files = [
        _write(
            tmp_path,
            "game_decisions_2026-08-10.json",
            _game_export("p1", [{"id": "f1", "status": "accepted"}]),
        ),
        _write(
            tmp_path,
            "game_decisions_2026-08-11.json",
            _game_export("p1", [{"id": "f1", "status": "rejected"}]),
        ),
    ]
    assert hitl_tally(files, 10) == hitl_tally(list(reversed(files)), 10)
