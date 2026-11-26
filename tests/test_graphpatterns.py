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


def _v(name, *idx, sign=1, **kw):
    return {**tp._x(name, *idx, **kw), "sign": sign, "role": kw.get("role", "lhs")}


def _mini(constraints, objective, variables, params=(), indices=("I", "K")):
    return tp.model(
        constraints,
        objective,
        indices=list(indices),
        params=list(params),
        variables=variables,
    )


def _pats(f) -> dict[str, set[str]]:
    g = _same(f)
    out: dict[str, set[str]] = {}
    for m in g["models"]:
        out.setdefault(m["pattern"], set()).add(m["variable"])
    return out


def test_absolute_deviation_and_two_sided_bound():
    # z_i >= x_i - a_i ; z_i >= a_i - x_i ; 0 <= x_i ; x_i <= u ; min sum z
    q = [{"index": "i", "over": "I"}]
    f = _mini(
        [
            {
                "name": "pos",
                "quantifiers": q,
                "comparator": "ge",
                "lhs": [_v("z", "I")],
                "rhs": [_v("x", "I", role="rhs"), tp._p("a", "I", role="rhs", sign=-1)],
            },
            {
                "name": "neg",
                "quantifiers": q,
                "comparator": "ge",
                "lhs": [_v("z", "I")],
                "rhs": [tp._p("a", "I", role="rhs"), _v("x", "I", role="rhs", sign=-1)],
            },
            {
                "name": "lo",
                "quantifiers": q,
                "comparator": "le",
                "lhs": [],
                "rhs": [_v("x", "I", role="rhs")],
            },
            {
                "name": "up",
                "quantifiers": q,
                "comparator": "le",
                "lhs": [_v("x", "I")],
                "rhs": [tp._p("u", role="rhs")],
            },
        ],
        {
            "sense": "min",
            "terms": [_v("z", "I", operator="sum", operator_over=["I"], role="objective")],
        },
        [("z", ["I"], "non_negative"), ("x", ["I"], "continuous"), ("b", ["I"], "binary")],
        params=[("a", ["I"], "vector"), ("u", [], "scalar")],
    )
    p = _pats(f)
    assert p["absolute_deviation"] == {"z"} and p["two_sided_bound"] == {"x"}
    assert "positive_part" not in p


def test_min_max_and_positive_part():
    # min h + sum s ; h >= w_k for all k ; s_k >= t_k - p_k
    qk = [{"index": "k", "over": "K"}]
    f = _mini(
        [
            {
                "name": "epi",
                "quantifiers": qk,
                "comparator": "ge",
                "lhs": [_v("h")],
                "rhs": [_v("w", "K", role="rhs")],
            },
            {
                "name": "late",
                "quantifiers": qk,
                "comparator": "ge",
                "lhs": [_v("s", "K")],
                "rhs": [_v("t", "K", role="rhs"), tp._p("p", "K", role="rhs", sign=-1)],
            },
        ],
        {
            "sense": "min",
            "terms": [
                _v("h", role="objective"),
                _v("s", "K", operator="sum", operator_over=["K"], role="objective"),
            ],
        },
        [
            ("h", [], "non_negative"),
            ("w", ["K"], "continuous"),
            ("s", ["K"], "non_negative"),
            ("t", ["K"], "continuous"),
            ("b", ["K"], "binary"),
        ],
        params=[("p", ["K"], "vector")],
    )
    p = _pats(f)
    assert p["min_max"] == {"h"} and p["positive_part"] == {"s"}


def test_logic_and_or():
    # z <= x, z <= y (AND);  r >= x, r >= 1 - y (OR)
    def row(name, cmp, a, b, *, rhs_one=False, b_sign=1):
        rhs = [_v(b, "I", role="rhs", sign=b_sign)]
        if rhs_one:
            rhs.append({"constant": 1, "role": "rhs"})
        return {
            "name": name,
            "quantifiers": [{"index": "i", "over": "I"}],
            "comparator": cmp,
            "lhs": [_v(a, "I")],
            "rhs": rhs,
        }

    f = _mini(
        [
            row("a1", "le", "z", "x"),
            row("a2", "le", "z", "y"),
            row("o1", "ge", "r", "x"),
            row("o2", "ge", "r", "y", rhs_one=True, b_sign=-1),
        ],
        None,
        [
            ("x", ["I"], "binary"),
            ("y", ["I"], "binary"),
            ("z", ["I"], "binary"),
            ("r", ["I"], "continuous"),
        ],
    )
    p = _pats(f)
    assert p["logic_and"] == {"z"} and p["logic_or"] == {"r"}


def test_escape_balance_definition_rows():
    q = [{"index": "i", "over": "I"}]
    f = _mini(
        [
            {
                "name": "esc",
                "quantifiers": q,
                "comparator": "eq",
                "lhs": [_v("x", "I", "K", operator="sum", operator_over=["K"]), _v("c", "I")],
                "rhs": [{"constant": 1, "role": "rhs"}],
            },
            {
                "name": "bal",
                "quantifiers": q,
                "comparator": "eq",
                "lhs": [_v("s", "I")],
                "rhs": [
                    {**_v("s", "I", role="rhs"), "bindings": [{"index": "I", "expr": "i-1"}]},
                    _v("f", "I", role="rhs"),
                ],
            },
            {
                "name": "dfn",
                "quantifiers": q,
                "comparator": "eq",
                "lhs": [_v("d", "I")],
                "rhs": [_v("a", "I", role="rhs"), _v("e", "I", role="rhs")],
            },
        ],
        None,
        [
            ("x", ["I", "K"], "binary"),
            ("c", ["I"], "binary"),
            ("s", ["I"], "non_negative"),
            ("f", ["I"], "continuous"),
            ("d", ["I"], "continuous"),
            ("a", ["I"], "continuous"),
            ("e", ["I"], "continuous"),
        ],
    )
    rows = {r["constraint"]: set(r["patterns"]) for r in _same(f)["rows"]}
    assert {"partition_row", "escape_row"} <= rows["esc"]
    assert rows["bal"] == {"balance_row"} and rows["dfn"] == {"definition_row"}
