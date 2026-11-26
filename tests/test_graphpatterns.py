"""Graph patterns (ADR-0025) agree with the row oracle (ADR-0024)."""

from __future__ import annotations

import json
from pathlib import Path

# ruff: noqa: I001 — the railpminer path shim must precede the lp2graph import.
import pytest

from railpminer import _lp2graph  # noqa: F401

from lp2graph.core.model import Formulation

from corpusbuilder import graphpatterns as gp
from corpusbuilder import patterns as oracle
import test_patterns as tp

ROOT = Path(__file__).resolve().parent.parent


def _same(f: Formulation) -> dict:
    g, o = gp.detect(f), oracle.detect(f)
    assert {r["constraint"]: r["patterns"] for r in g["rows"]} == {
        r["constraint"]: sorted(r["patterns"]) for r in o["rows"]
    }
    assert {(m["pattern"], m["variable"]) for m in g["models"]} == {
        (m["pattern"], m["variable"]) for m in o["models"]
    }
    return g


@pytest.mark.parametrize(
    "build",
    [
        tp.knapsack,
        lambda: tp.knapsack(write_as_ge=True),
        lambda: tp.knapsack(sense="min"),
        lambda: tp.knapsack(kind="set_packing"),
    ],
)
def test_knapsack_variants_agree(build):
    _same(build())


def test_knapsack_is_a_graph_hit():
    g = _same(tp.knapsack())
    assert [m["pattern"] for m in g["models"]] == ["knapsack_problem"]


def test_rhs_literal_separates_one_from_zero():
    # sum x = 1 is an exactly-one star; sum x = 0 is not.
    f = tp.knapsack()
    data = f.model_dump(mode="json")
    row = {
        "name": "pick",
        "comparator": "eq",
        "lhs": [tp._x("x", "I", operator="sum", operator_over=["I"], role="lhs")],
        "rhs": [{"constant": 1, "role": "rhs"}],
    }
    zero = {**row, "name": "none", "rhs": [{"constant": 0, "role": "rhs"}]}
    data["constraints"] = [row, zero]
    g = _same(Formulation.model_validate(data))
    rows = {r["constraint"]: r["patterns"] for r in g["rows"]}
    assert rows == {"pick": ["partition_row"], "none": []}


@pytest.mark.parametrize(
    "name",
    ["assignment", "mip_2_8_pesp", "mip_2_1_big_m", "mip_2_4_time_indexed", "pesp_solvable"],
)
def test_seed_models_agree(name):
    f = Formulation.model_validate(
        json.loads((ROOT / f"corpus/formulations/{name}.json").read_text(encoding="utf-8"))
    )
    _same(f)


def test_term_graph_contracts_operators():
    f = Formulation.model_validate(
        json.loads((ROOT / "corpus/formulations/assignment.json").read_text(encoding="utf-8"))
    )
    tg = gp.term_graph(f)
    assert not [n for n, d in tg.nodes(data=True) if d["cls"] == "operator"]
    e = [d for _, v, d in tg.out_edges("constraint:each_worker_one_job", data=True) if v == "var:x"]
    assert len(e) == 1 and e[0]["agg"] and e[0]["over"] == ("J",)
