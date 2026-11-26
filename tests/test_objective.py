"""Objective heads: the operator is found by grammar, never by position."""

from __future__ import annotations

import pytest

from corpusbuilder.objective import objective_split, resolve_objectives


@pytest.mark.parametrize(
    ("latex", "sense", "starts"),
    [
        (r"\min F", "min", "F"),
        (r"Z = min \alpha_{0} \underset{i \in N}{\sum} y_{i}", "min", r"\alpha_{0}"),
        (r"\left(\text{TTR}\right) min Z_{1} = \lambda_{1} \sum x", "min", "Z_{1} ="),
        (r"\left[\mathbf{MILP}\right] : \mathbf{min} z = \sum_{t} w_{t}", "min", "z ="),
        (r"\underset{\mathbf{x} , \mathbf{b}}{min} Z \left(\mathbf{x}\right) = w x", "min", "Z"),
        (r"\mathit{minZ} = \alpha_{1} Z_{1} + \alpha_{2} Z_{2}", "min", "Z ="),
        (r"o b j = min w_{p} \cdot x", "min", "w_{p}"),
        (r"\text{Max} \sum_{l} p_{l} x_{l}", "max", r"\sum"),
        (r"P E S P : Maximize F_{PESP} \left(\pi , p\right)", "max", "F_{PESP}"),
    ],
)
def test_objective_statements_are_split_at_the_operator(latex, sense, starts):
    head = objective_split(latex)
    assert head is not None, latex
    assert head[0] == sense
    assert head[1].startswith(starts), head


def test_repeated_operator_and_subject_to_tail_are_dropped():
    assert objective_split(r"min T = min \left(T_{1} + T_{2}\right)") == (
        "min",
        r"T = \left(T_{1} + T_{2}\right)",
    )
    head = objective_split(
        r"\begin{cases} min f = \gamma_{1} f_{1} , \\ \text{s}.\text{t}. \text{constraints} (1)-(7) . \end{cases}"
    )
    assert head == ("min", r"f = \gamma_{1} f_{1}")


@pytest.mark.parametrize(
    "latex",
    [
        r"max \left\{f_{min} \left(1\right) , D - x\right\} \leq d",  # the function max{a, b}
        r"h_{i j s} = \underset{l \in B}{max} \left(d_{i l} - d_{j l}\right)",  # indexed label
        r"Z_{2} = \underset{k \in K}{\text{max}} \frac{a}{b}",  # an aggregation defines Z_2
        r"P_{q s}^{B} = min \left\{P_{q s}^{W} , C_{q s}\right\} , \forall s",
        r"x_{i} + y \le max_{j} y \forall i",  # algebra before the operator
        r"x_{i} \le 1",
    ],
)
def test_functions_and_definitions_are_not_objectives(latex):
    assert objective_split(latex) is None


def test_component_objectives_become_definitions_of_their_labels():
    rows = [
        r"\mathit{minZ} = \alpha_{1} Z_{1} + \alpha_{2} Z_{2}",
        r"min Z_{1} = \sum x",
        r"\mathit{min} Z_{2} = \sum y",
    ]
    assert resolve_objectives(rows) == [
        (True, rows[0]),
        (False, r"Z_{1} = \sum x"),
        (False, r"Z_{2} = \sum y"),
    ]


def test_a_restated_objective_is_not_a_second_objective():
    rows = [
        r"min Z = \theta \sum c y",
        r"\left(\text{M0}\right) min Z",
        r"\left(\text{M} 1\right) min Z",
    ]
    out = resolve_objectives(rows)
    assert [is_obj for is_obj, _ in out] == [False, True, False]
    assert out[0][1] == r"Z = \theta \sum c y"  # the definition the bare "min Z" refers to


def test_unrelated_objectives_stay_objectives():
    rows = [r"\mathit{Min} Z_{1} = W^{u} + W^{d}", r"\mathit{Min} Z_{2} = \sum C"]
    assert resolve_objectives(rows) == [(True, rows[0]), (True, rows[1])]


def test_min_and_max_of_one_expression_are_two_objectives():
    rows = [r"\min \sum_{i} t_{i}", r"\max \sum_{i} t_{i}"]
    assert resolve_objectives(rows) == [(True, rows[0]), (True, rows[1])]
