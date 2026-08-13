"""Deterministic symbol evidence — what the algebra settles before a human does.

Both readers here feed the declaration sidecar, so a false positive is not a
missed opportunity but a wrong declaration the reviewer is invited to trust.
The tests are weighted accordingly: most of them pin things the readers must
*refuse*.
"""

from __future__ import annotations

import pytest

from corpusbuilder.symbols import (
    INDEX,
    VARIABLE,
    binder_roles,
    binder_symbols,
    domain_declaration,
    paper_evidence,
)

# --------------------------------------------------------------------------- #
# Binders
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("latex", "indices", "families"),
    [
        (r"\sum_{i \in I} c_i x_i", {"i"}, {"I"}),
        (r"\prod_{t \in T} y_t", {"t"}, {"T"}),
        # The upper limit bounds the family: "t = 1 .. T" ranges over T.
        (r"\sum_{t = 1}^{T} x_t", {"t"}, {"T"}),
        (r"\sum^{T}_{t = 1} x_t", {"t"}, {"T"}),
        # One clause, two families: cutting at the comma would drop the second.
        (r"\forall i \in I, j \in J", {"i", "j"}, {"I", "J"}),
        (r"\sum_{r \in R_{k}} x_r", {"r"}, {"R"}),
        # No membership sign and no limit: an index with no family to declare.
        (r"\sum_{i} x_i", {"i"}, set()),
        (r"a + b = c", set(), set()),
    ],
)
def test_binder_roles(latex, indices, families):
    roles = binder_roles(latex)
    assert set(roles.indices) == indices
    assert set(roles.families) == families


def test_binder_reading_survives_left_right_delimiters():
    r"""The expression tree flattens \left into a spurious "ft" symbol."""
    assert "ft" not in binder_symbols(r"\sum_{r \in R} x \left(u\right) \le b")


def test_a_symbol_is_a_family_even_where_another_binder_only_bounds_it():
    """One binder writing "\\sum_{I}" must not demote the family I elsewhere."""
    roles = binder_roles(r"\sum_{i \in I} \sum_{I} z")
    assert "I" in roles.families
    assert "I" not in roles.indices


# --------------------------------------------------------------------------- #
# Domain rows
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("latex", "expected"),
    [
        (r"x_{ij} \in \{0,1\}", {"x": "binary"}),
        # The MathML conversion writes set braces with \left and \right.
        (r"q_{k} \in \left\{0 , 1\right\} \forall k \in L", {"q": "binary"}),
        (r"t_i \ge 0 \forall i \in I", {"t": "non_negative"}),
        (r"k_{a} \in \mathbb{Z}", {"k": "integer"}),
        (r"z \in \mathbb{R}", {"z": "continuous"}),
        (
            r"d_{r} , n_{r} , w_{r} \in \mathcal{R}^{+} .",
            {"d": "non_negative", "n": "non_negative", "w": "non_negative"},
        ),
    ],
)
def test_domain_rows_declare_variables(latex, expected):
    assert domain_declaration(latex) == expected


@pytest.mark.parametrize(
    "latex",
    [
        # A bounded difference is a constraint, not a declaration of t.
        r"t_{e}^{a} - \bar{t}_{e}^{a} \geq 0 , \forall e \in \mathcal{E}",
        r"x_i + y_i \ge 0",
        r"d_{b + 1 , s} - d_{b , s} \geq h^{dep} , \forall s \in \mathcal{S}",
        # A set definition, not a variable declaration.
        r"H = \left\{\right. z \in \mathbb{R}^{M} \left|\right. \ell \leq z \leq u \left.\right\}",
        # A bound against something other than zero says nothing about a domain.
        r"x_i \ge b_i",
        r"\sum_{i \in I} x_i \le C",
    ],
)
def test_non_declarations_are_refused(latex):
    assert domain_declaration(latex) == {}


# --------------------------------------------------------------------------- #
# Paper-level evidence
# --------------------------------------------------------------------------- #


def test_a_domain_row_beats_a_binder_limit():
    """Explicit beats circumstantial: "\\sum_{x=1}^{X}" cannot outvote "x >= 0"."""
    e = paper_evidence([r"\sum_{i \in I} x_i", r"x_i \ge 0"])
    assert e.kinds["x"] == VARIABLE
    assert e.domains["x"] == "non_negative"
    assert e.kinds["I"] == INDEX


def test_a_declared_variable_is_not_also_offered_as_a_family():
    e = paper_evidence([r"\sum_{t = 1}^{T} x_t", r"T \in \mathbb{Z}"])
    assert e.kinds["T"] == VARIABLE
    assert "T" not in e.families


def test_bound_letters_are_indices_but_need_no_declaration():
    """The reviewer answers nothing about j, and must not be asked to declare it."""
    e = paper_evidence([r"\sum_{i \in I} x_{ij} \le b_j \quad \forall j \in J"])
    assert e.kinds["j"] == INDEX
    assert set(e.families) == {"I", "J"}
    assert set(e.bound) == {"i", "j"}


def test_evidence_is_deterministic():
    rows = [r"\sum_{i \in I} c_i x_i", r"x_{ij} \in \{0,1\}", r"\forall j \in J"]
    first = paper_evidence(rows)
    assert paper_evidence(rows) == first
    assert paper_evidence(list(rows)) == first
