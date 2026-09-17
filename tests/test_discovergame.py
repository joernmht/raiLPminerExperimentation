"""corpusbuilder.discovergame — the discovery-round pages and their decisions contract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpusbuilder import discover, discovergame, indices
from tests.test_discover import _XML, _fake_convert


def _payload() -> dict:
    rec = discover.discover_paper(_XML, key="p", convert=_fake_convert)
    rows = [(m["eq"], m["latex"]) for m in rec["maths"] if m["where"] == "display"]
    idx = indices.analyse(rows, rec, {"index": {}, "param": {}, "var": {}})
    return discovergame.page_payload(rec, idx)


def test_page_payload_carries_text_maths_tables_and_indices() -> None:
    p = _payload()
    assert p["schema_version"] == "discover-page-1" and p["paper"]["key"] == "p"
    assert [s[0] for s in p["paras"][1]["segments"]].count("m") == 3
    assert p["maths"]["m-0003"]["where"] == "display" and p["maths"]["m-0003"]["eq"] == "eq-0001"
    assert p["statements"] == ["m-0002"]
    assert len(p["tables"]) == 1 and p["tables"][0]["rows"][1]["symbol"] == "I"
    assert "i" in p["indices"]["letters"] and p["indices"]["letters"]["i"]["rule"] == "prose"
    assert "context" not in json.dumps(p["maths"])  # the paragraph itself is on the page


def test_build_page_and_index_are_deterministic(tmp_path: Path) -> None:
    p = _payload()
    a = discovergame.build_page(p, tmp_path / "p.html").read_text(encoding="utf-8")
    b = discovergame.build_page(p, tmp_path / "p2.html").read_text(encoding="utf-8")
    assert a == b and "discover-decisions-1" in a and "Set of trains" in a and 'id="data"' in a
    assert "<\\/" in a or "</script>" not in json.dumps(p)  # payload cannot close the script tag
    idx = discovergame.build_index(
        [
            {
                "key": "p",
                "title": "T",
                "year": 2026,
                "display": 1,
                "statements": 1,
                "notation_rows": 2,
                "letters_index": 1,
                "letters_alias": 0,
                "letters_candidate": 0,
                "families_new": 1,
            }
        ],
        tmp_path / "i.html",
    ).read_text(encoding="utf-8")
    assert '"key":"p"' in idx and "discover/" in idx


def test_load_decisions_merges_last_wins_and_validates(tmp_path: Path) -> None:
    a = {
        "schema_version": "discover-decisions-1",
        "paper_key": "p",
        "formulas": {"m-0002": "formula", "eq-0001": "not"},
        "spans": [{"para": 1, "text": "x = 1"}],
        "indices": {"i": {"verdict": "index", "family": "I"}},
        "families": {"I": {"verdict": "family", "rename": ""}},
    }
    b = {
        "schema_version": "discover-decisions-1",
        "paper_key": "p",
        "formulas": {"eq-0001": "formula"},
        "spans": [{"para": 1, "text": "x = 1"}, {"para": 2, "text": "y = 2"}],
        "indices": {"i": {"verdict": "not", "family": ""}},
        "families": {},
    }
    (tmp_path / "1.json").write_text(json.dumps(a), encoding="utf-8")
    (tmp_path / "2.json").write_text(json.dumps(b), encoding="utf-8")
    merged = discovergame.load_decisions(list(tmp_path.glob("*.json")))
    m = merged["p"]
    assert m["formulas"] == {"m-0002": "formula", "eq-0001": "formula"}
    assert (
        m["indices"]["i"]["verdict"] == "not"
        and len(m["spans"]) == 2
        and m["files"] == ["1.json", "2.json"]
    )
    s = discovergame.summarize_decisions(merged)
    assert s == {
        "papers": 1,
        "formulas_marked": 2,
        "formulas_rejected": 0,
        "spans": 2,
        "letters_confirmed": 0,
        "letters_rejected": 1,
        "families_confirmed": 1,
    }
    bad = dict(a, schema_version="vocab-decisions-1")
    (tmp_path / "3.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        discovergame.load_decisions([tmp_path / "3.json"])
    assert discovergame.validate_export(dict(a, indices={"i": {"verdict": "maybe"}}))
