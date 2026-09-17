"""corpusbuilder.declare — deterministic declarations from notation tables and index discovery."""

from __future__ import annotations

import json
from pathlib import Path

from corpusbuilder import declare


def test_kind_from_desc_reads_the_rows_words() -> None:
    assert declare.kind_from_desc("Binary variable, 1 if train i uses track j") == (
        "var",
        {"domain": "binary"},
    )
    assert declare.kind_from_desc("Integer decision variable: number of coaches") == (
        "var",
        {"domain": "integer"},
    )
    assert declare.kind_from_desc("Non-negative variable, arrival time") == (
        "var",
        {"domain": "non_negative"},
    )
    assert declare.kind_from_desc("Set of trains") == ("index", {})
    assert declare.kind_from_desc("Running time of train i on segment j") == ("param", {"kind": ""})
    assert declare.kind_from_desc("A sufficiently large constant") == ("param", {"kind": "big_m"})


def test_symbol_key_drops_index_letters_and_keeps_labels() -> None:
    assert declare.symbol_key(r"g_{i}^{t r a i n}", {"i"}) == "gtrain"
    assert declare.symbol_key(r"T_{\max}", set()) == "tmax"
    assert declare.symbol_key(r"\Delta_{j}^{M}", {"j"}) == "deltam"
    assert declare.symbol_key("3.5", set()) is None


def test_shape_for_resolves_through_families_or_refuses() -> None:
    rows = [
        r"x_{i, j} + g_{i}^{train} \le T_{max} \quad \forall i \in I, j \in J",
        r"x_{i, j} \ge 0",
    ]
    rows = [declare.ix.normalise(r) for r in rows]
    assert declare.shape_for("x", rows, {"i": "I", "j": "J"}) == (["I", "J"], "")
    assert declare.shape_for("gtrain", rows, {"i": "I"}) == (["I"], "")
    assert declare.shape_for("tmax", rows, {"i": "I"}) == ([], "")  # used, never indexed: a scalar
    assert declare.shape_for("nowhere", rows, {"i": "I"}) == (
        [],
        "no indexed use found (declared as scalar)",
    )
    # a capital superscript is a label (Delta_F), a known superscript index letter is an index
    more = [declare.ix.normalise(r) for r in (r"\Delta_{j}^{F} \le y^{k}_{i}",)]
    assert declare.shape_for("deltaf", more, {"i": "I", "j": "B", "k": "K"}) == (["B"], "")
    assert declare.shape_for("y", more, {"i": "I", "j": "B", "k": "K"}) == (["I", "K"], "")
    assert (
        declare.symbol_key(r"\Delta_{j}^{F}", {"j"}) == "deltaf"
        and declare.symbol_key(r"y_{i}^{k}", {"i", "k"}) == "y"
    )
    shape, note = declare.shape_for("x", rows, {"i": "I"})
    assert shape is None and note.startswith("letters without family: j")


def test_proposals_from_a_small_paper(tmp_path: Path) -> None:
    key = "p1"
    (tmp_path / "declarations").mkdir()
    (tmp_path / "declarations" / f"{key}.tex").write_text(
        "%@ index I ordered=0 cyclic=0 :: trains\n%@ param M shape=- kind=big_m domain=- :: big M\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "discovery").mkdir()
    disc = {
        "tables": [
            {
                "notation": True,
                "rows": [
                    {"header": True, "symbol": None, "desc": ""},
                    {"header": False, "symbol": "J", "desc": "Set of tracks"},
                    {
                        "header": False,
                        "symbol": r"x_{i , j}",
                        "desc": "Binary variable equal to 1 if train i uses track j",
                    },
                    {"header": False, "symbol": r"c_{i}", "desc": "Cost of train i"},
                    {"header": False, "symbol": r"w_{k}", "desc": "Weight of k"},
                ],
            }
        ],
        "deflists": [{"term": "T_{max}", "def": "Maximum horizon (parameter)"}],
        "maths": [],
        "paras": [],
    }
    (tmp_path / "discovery" / f"{key}.json").write_text(
        json.dumps(disc), encoding="utf-8", newline="\n"
    )
    (tmp_path / "indices").mkdir()
    idx = {
        "letters": {
            "i": {"verdict": "index", "family": "I"},
            "j": {"verdict": "alias", "family": "J"},
            "k": {"verdict": "candidate", "family": None},
        },
        "families": {
            "I": {
                "name": "I",
                "declared_as": "index",
                "letters": {"i": 3},
                "desc": "",
                "cap": False,
            },
            "J": {
                "name": "J",
                "declared_as": None,
                "letters": {"j": 2},
                "desc": "Set of tracks",
                "cap": False,
            },
        },
    }
    (tmp_path / "indices" / f"{key}.json").write_text(
        json.dumps(idx), encoding="utf-8", newline="\n"
    )
    (tmp_path / "vocab").mkdir()
    (tmp_path / "vocab" / f"{key}.json").write_text(
        json.dumps({"missing": {"x": {}, "c": {}, "w": {}, "T_max": {}, "zeta": {}}}),
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "dossiers").mkdir()
    dossier = {
        "schema_version": "dossier-1",
        "source": {
            "title": "t",
            "doi": "10.1/p1",
            "year": 2026,
            "authors": [],
            "venue": "v",
            "publisher": "e",
            "cited_by_count": 0,
            "api": "x",
            "retrieved": "2026-01-01",
            "landing_url": "u",
        },
        "references": [],
        "cited_by": [],
        "references_count": 0,
        "cited_by_count": 0,
        "formulas": [
            {
                "id": "eq-0001",
                "label": "e1",
                "latex": r"\sum_{i \in I} c_{i} x_{i,j} \le T_{max} \quad \forall j \in J",
                "mathml": "<m/>",
                "method": "mathml",
                "status": "unreviewed",
                "note": None,
            },
            {
                "id": "eq-0002",
                "label": "e2",
                "latex": r"w_{k} \ge 0",
                "mathml": "<m/>",
                "method": "mathml",
                "status": "unreviewed",
                "note": None,
            },
        ],
    }
    (tmp_path / "dossiers" / f"{key}.json").write_text(
        json.dumps(dossier), encoding="utf-8", newline="\n"
    )
    p = declare.proposals(
        key,
        decl_dir=tmp_path / "declarations",
        discovery_dir=tmp_path / "discovery",
        indices_dir=tmp_path / "indices",
        vocab_dir=tmp_path / "vocab",
        dossier_dir=tmp_path / "dossiers",
    )
    assert "%@ index J ordered=0 cyclic=0 :: Set of tracks" in p["lines"]
    assert any(line.startswith("%@ var x shape=I,J domain=binary") for line in p["lines"])
    assert any(line.startswith("%@ param c shape=I kind=vector") for line in p["lines"])
    assert any(line.startswith("%@ param T_max shape=- kind=scalar") for line in p["lines"])
    assert [s_["name"] for s_ in p["skipped"]] == ["w"] and "k" in p["skipped"][0]["why"]
    assert not any(" zeta " in line for line in p["lines"])  # not in any table: nothing guessed
    assert p["counts"] == {
        "index_lines": 1,
        "var_lines": 1,
        "param_lines": 2,
        "skipped": 1,
        "missing_names": 5,
        "table_items": 5,
    }
    target = declare.stage_sidecars(
        {key: p}, decl_dir=tmp_path / "declarations", scratch=tmp_path / "scratch"
    )
    text = (target / f"{key}.tex").read_text(encoding="utf-8")
    assert text.startswith("%@ index I ")
    assert "% --- vocabulary (deterministic: notation tables + index discovery" in text
    assert all(line in text for line in p["lines"]) and text.endswith("\n")
