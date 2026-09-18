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
    assert p["schema_version"] == "discover-page-2" and p["paper"]["key"] == "p"
    assert set(p["roles"]) == set(p["maths"]) and p["roles"]["m-0001"]["role"] == "definition"
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
    assert a == b and "discover-decisions-2" in a and "Set of trains" in a and 'id="data"' in a
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


def test_load_decisions_merges_v1_and_v2_last_wins_and_validates(tmp_path: Path) -> None:
    v1 = {
        "schema_version": "discover-decisions-1",
        "paper_key": "p",
        "formulas": {"m-0002": "formula", "eq-0001": "not"},
        "spans": [{"para": 1, "text": "x = 1"}],
        "indices": {"i": {"verdict": "index", "family": "I"}},
        "families": {"I": {"verdict": "family", "rename": ""}},
    }
    v2 = {
        "schema_version": "discover-decisions-2",
        "paper_key": "p",
        "roles": {
            "eq-0001": {"role": "formula", "span": None, "source": "human"},
            "m-0007": {
                "role": "definition",
                "span": {
                    "para": 3,
                    "start": 0,
                    "end": 9,
                    "text": "Let x be.",
                    "position": "around",
                    "source": "human",
                },
                "source": "human",
            },
        },
        "marks": [{"para": 1, "text": "x = 1"}, {"para": 2, "text": "y = 2"}],
        "indices": {"i": {"verdict": "not", "family": ""}},
        "families": {},
        "label_proposals": ["st", "end"],
    }
    (tmp_path / "1.json").write_text(json.dumps(v1), encoding="utf-8")
    (tmp_path / "2.json").write_text(json.dumps(v2), encoding="utf-8")
    merged = discovergame.load_decisions(list(tmp_path.glob("*.json")))
    m = merged["p"]
    assert (
        m["roles"]["m-0002"]["role"] == "formula" and m["roles"]["eq-0001"]["role"] == "formula"
    )  # v2 wins over v1's "not"
    assert m["roles"]["m-0007"]["span"]["text"] == "Let x be."
    assert (
        m["indices"]["i"]["verdict"] == "not"
        and len(m["marks"]) == 2
        and m["files"] == ["1.json", "2.json"]
    )
    s = discovergame.summarize_decisions(merged)
    assert (
        s["papers"] == 1
        and s["formulas"] == 2
        and s["definitions"] == 1
        and s["definitions_span_by_human"] == 1
        and s["marks"] == 2
    )
    assert (
        s["letters_rejected"] == 1
        and s["families_confirmed"] == 1
        and s["label_proposals"] == ["end", "st"]
    )
    bad = dict(v1, schema_version="vocab-decisions-1")
    (tmp_path / "3.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        discovergame.load_decisions([tmp_path / "3.json"])
    assert discovergame.validate_export(dict(v2, roles={"m-1": {"role": "maybe"}}))
    assert discovergame.validate_export(
        dict(v2, roles={"m-1": {"role": "definition", "span": {"para": 1}}})
    )
