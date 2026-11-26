"""Structural pattern detection on textbook models (ADR-0024)."""

from __future__ import annotations

import json
from pathlib import Path

# ruff: noqa: I001 — the railpminer path shim must precede the lp2graph import.
import pytest

from railpminer import _lp2graph  # noqa: F401

from lp2graph.core.model import Formulation

from corpusbuilder.patterns import detect, normal_form, row_patterns

ROOT = Path(__file__).resolve().parent.parent


def _x(name: str, *idx: str, **kw) -> dict:
    """A variable term with one binding per shape index (lower-case letter)."""
    return {
        "ref": name,
        "ref_kind": "variable",
        "bindings": [{"index": i, "expr": i.lower()} for i in idx],
        **kw,
    }


def _p(name: str, *idx: str, **kw) -> dict:
    return {
        "ref": name,
        "ref_kind": "parameter",
        "bindings": [{"index": i, "expr": i.lower()} for i in idx],
        **kw,
    }


def _one(**kw) -> dict:
    return {"constant": 1, **kw}


def model(constraints, objective, *, indices, params, variables) -> Formulation:
    return Formulation.model_validate(
        {
            "id": "t",
            "name": "t",
            "family": "milp",
            "indices": [{"name": i} for i in indices],
            "parameters": [{"name": n, "shape": list(s), "kind": k} for n, s, k in params],
            "variables": [{"name": n, "shape": list(s), "domain": d} for n, s, d in variables],
            "constraints": constraints,
            "objective": objective,
        }
    )


def knapsack(*, write_as_ge: bool = False, sense: str = "max", kind: str = "linear"):
    lhs = _x("x", "I", coefficient="w", operator="sum", operator_over=["I"], role="lhs")
    cap = _p("C", role="rhs")
    if write_as_ge:  # -sum w x >= -C
        row = {
            "name": "cap",
            "comparator": "ge",
            "kind": kind,
            "lhs": [{**lhs, "sign": -1}],
            "rhs": [{**cap, "sign": -1}],
        }
    else:
        row = {"name": "cap", "comparator": "le", "kind": kind, "lhs": [lhs], "rhs": [cap]}
    return model(
        [row],
        {
            "sense": sense,
            "terms": [
                _x("x", "I", coefficient="v", operator="sum", operator_over=["I"], role="objective")
            ],
        },
        indices=["I"],
        params=[("w", ["I"], "vector"), ("v", ["I"], "vector"), ("C", [], "scalar")],
        variables=[("x", ["I"], "binary")],
    )


def _model_pats(f) -> set[str]:
    return {m["pattern"] for m in detect(f)["models"]}


def test_textbook_knapsack_is_found():
    d = detect(knapsack())
    assert d["rows"][0]["patterns"] == ["knapsack_row"]
    (m,) = d["models"]
    assert m["pattern"] == "knapsack_problem" and m["variant"] == "0-1"


def test_knapsack_written_with_ge_is_normalized():
    f = knapsack(write_as_ge=True)
    row = normal_form(f, f.constraints[0])
    assert row.flipped and row.comparator == "le"
    assert "knapsack_problem" in _model_pats(f)


def test_min_objective_is_not_a_knapsack_problem():
    # Same row, but minimizing value: the row is still a knapsack row,
    # the model is not a knapsack problem.
    d = detect(knapsack(sense="min"))
    assert d["rows"][0]["patterns"] == ["knapsack_row"]
    assert d["models"] == []


def test_covering_row_is_not_a_knapsack_row():
    # min c x  s.t.  sum a x >= b  -- the WL labels cannot tell this apart.
    f = model(
        [
            {
                "name": "demand",
                "comparator": "ge",
                "lhs": [
                    _x("x", "I", coefficient="a", operator="sum", operator_over=["I"], role="lhs")
                ],
                "rhs": [_p("b", role="rhs")],
            }
        ],
        {"sense": "min", "terms": [_x("x", "I", coefficient="c", role="objective")]},
        indices=["I"],
        params=[("a", ["I"], "vector"), ("c", ["I"], "vector"), ("b", [], "scalar")],
        variables=[("x", ["I"], "binary")],
    )
    assert detect(f)["rows"][0]["patterns"] == []


def test_annotations_are_never_read():
    plain = detect(knapsack())
    labelled = detect(knapsack(kind="set_packing"))
    assert [r["patterns"] for r in plain["rows"]] == [r["patterns"] for r in labelled["rows"]]
    assert plain["models"] == labelled["models"]


def test_multi_dimensional_knapsack():
    rows = [
        {
            "name": f"cap_{k}",
            "comparator": "le",
            "lhs": [
                _x("x", "I", coefficient=f"w{k}", operator="sum", operator_over=["I"], role="lhs")
            ],
            "rhs": [_p(f"C{k}", role="rhs")],
        }
        for k in (1, 2)
    ]
    f = model(
        rows,
        {"sense": "max", "terms": [_x("x", "I", coefficient="v", role="objective")]},
        indices=["I"],
        params=[
            ("w1", ["I"], "vector"),
            ("w2", ["I"], "vector"),
            ("v", ["I"], "vector"),
            ("C1", [], "scalar"),
            ("C2", [], "scalar"),
        ],
        variables=[("x", ["I"], "binary")],
    )
    (m,) = detect(f)["models"]
    assert m["variant"] == "0-1 multi-dimensional" and m["rows"] == ["cap_1", "cap_2"]


def test_generalized_assignment():
    f = model(
        [
            {
                "name": "assign",
                "quantifiers": [{"index": "i", "over": "I"}],
                "comparator": "eq",
                "lhs": [_x("x", "I", "J", operator="sum", operator_over=["J"], role="lhs")],
                "rhs": [_one(role="rhs")],
            },
            {
                "name": "capacity",
                "quantifiers": [{"index": "j", "over": "J"}],
                "comparator": "le",
                "lhs": [
                    _x(
                        "x",
                        "I",
                        "J",
                        coefficient="w",
                        operator="sum",
                        operator_over=["I"],
                        role="lhs",
                    )
                ],
                "rhs": [_p("C", "J", role="rhs")],
            },
        ],
        {"sense": "min", "terms": [_x("x", "I", "J", coefficient="c", role="objective")]},
        indices=["I", "J"],
        params=[("w", ["I", "J"], "matrix"), ("c", ["I", "J"], "matrix"), ("C", ["J"], "vector")],
        variables=[("x", ["I", "J"], "binary")],
    )
    pats = detect(f)
    gap = [m for m in pats["models"] if m["pattern"] == "generalized_assignment"]
    assert gap and gap[0]["variant"] == "weighted"
    assert "knapsack_problem" not in {m["pattern"] for m in pats["models"]}


def test_set_cover():
    f = model(
        [
            {
                "name": "cover",
                "quantifiers": [{"index": "e", "over": "E"}],
                "comparator": "ge",
                "lhs": [_x("x", "S", operator="sum", operator_over=["S"], role="lhs")],
                "rhs": [_one(role="rhs")],
            }
        ],
        {"sense": "min", "terms": [_x("x", "S", coefficient="c", role="objective")]},
        indices=["S", "E"],
        params=[("c", ["S"], "vector")],
        variables=[("x", ["S"], "binary")],
    )
    d = detect(f)
    assert d["rows"][0]["patterns"] == ["covering_row"]
    assert _model_pats(f) == {"set_covering_problem"}


def test_big_m_linking_is_not_a_knapsack():
    # gamma <= M y : one continuous, one binary switch.
    f = model(
        [
            {
                "name": "switch",
                "comparator": "le",
                "lhs": [_x("g", "I", role="lhs")],
                "rhs": [_x("y", "I", coefficient="M", role="rhs")],
            }
        ],
        None,
        indices=["I"],
        params=[("M", [], "big_m")],
        variables=[("g", ["I"], "continuous"), ("y", ["I"], "binary")],
    )
    row = normal_form(f, f.constraints[0])
    assert row_patterns(row) == ["big_m_row", "linking_row"]


@pytest.mark.parametrize(
    "path, expected",
    [
        ("corpus/formulations/assignment.json", {"assignment_block", "set_partitioning_problem"}),
        ("corpus/formulations/mip_2_8_pesp.json", {"pesp"}),
        ("corpus/formulations/mip_2_1_big_m.json", {"disjunctive_scheduling"}),
    ],
)
def test_seed_models(path, expected):
    f = Formulation.model_validate(json.loads((ROOT / path).read_text(encoding="utf-8")))
    assert _model_pats(f) == expected


def test_big_m_found_by_signature_when_not_declared():
    # t_j - t_i >= h - M (1 - y)  with M declared as a plain scalar.
    f = model(
        [
            {
                "name": "order",
                "quantifiers": [{"index": "i", "over": "I"}, {"index": "j", "over": "I"}],
                "comparator": "ge",
                "lhs": [
                    {
                        "ref": "t",
                        "ref_kind": "variable",
                        "bindings": [{"index": "I", "expr": "j"}],
                        "role": "lhs",
                    },
                    {
                        "ref": "t",
                        "ref_kind": "variable",
                        "bindings": [{"index": "I", "expr": "i"}],
                        "sign": -1,
                        "role": "lhs",
                    },
                ],
                "rhs": [
                    _p("h", role="rhs"),
                    _p("M", role="rhs", sign=-1),
                    {
                        "ref": "y",
                        "ref_kind": "variable",
                        "bindings": [{"index": "I", "expr": "i"}, {"index": "I", "expr": "j"}],
                        "coefficient": "M",
                        "role": "rhs",
                    },
                ],
            }
        ],
        None,
        indices=["I"],
        params=[("h", [], "scalar"), ("M", [], "scalar")],
        variables=[("t", ["I"], "continuous"), ("y", ["I", "I"], "binary")],
    )
    assert "big_m_row" in detect(f)["rows"][0]["patterns"]
    assert _model_pats(f) == {"disjunctive_scheduling"}
