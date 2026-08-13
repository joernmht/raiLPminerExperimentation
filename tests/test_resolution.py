"""Symbol resolution — the measure that says when a formula is broken down.

The breakdown coefficient decides whether a formula still needs structural
review, so the two ways it can lie are what these pin: counting a truncated
symbol list (a long formula would score 1 while symbols past the cut are still
untyped) and crediting a kind the reviewer never gave.
"""

from __future__ import annotations

from corpusbuilder.resolution import (
    binder_symbols,
    formula_symbols,
    paper_record,
    render_macros,
    structural_flags,
)
from corpusbuilder.dossier import Dossier, ExtractionMethod, FormulaRecord, SourceInfo
from corpusbuilder.game import extract_symbols


def _dossier(*latex: str) -> Dossier:
    return Dossier(
        source=SourceInfo(doi="10.1000/x", title="A paper"),
        formulas=[
            FormulaRecord(id=f"eq-{i:04d}", latex=s, method=ExtractionMethod.mathml)
            for i, s in enumerate(latex, 1)
        ],
    )


def test_symbols_are_not_truncated_to_the_display_list():
    """The UI shows the top 12; the coefficient must see all of them."""
    latex = " + ".join(f"{chr(ord('a') + i)}_{{1}}" for i in range(20))
    assert len(extract_symbols(latex)[0]) == 12
    assert len(formula_symbols(latex)) == 20


def test_indices_are_not_symbols():
    """Sub-/superscripts are index positions, so x_{ij} and x_{ik} are one symbol."""
    assert formula_symbols(r"x_{ij} + x_{ik} \le b_i") == {"x", "b"}


def test_binder_symbols_are_read_off_the_big_operator():
    assert binder_symbols(r"\sum_{i \in I} c_i x_i") == {"i", "I"}
    assert binder_symbols(r"\forall i \in I") == {"i", "I"}
    assert binder_symbols(r"a + b") == set()


def test_binder_reading_survives_left_right_delimiters():
    """The flattened tree leaks 'ft' out of \\left, which would auto-type a body 'ft'."""
    assert "ft" not in binder_symbols(r"\sum_{r \in R} x \left(u\right) \le b")


def test_structural_flags_separate_the_defect_kinds():
    good = structural_flags(r"\sum_{i \in I} t_i \le C")
    assert good["parses"] and good["single"] and good["statement"]
    # A fragment with no relation and no optimization head is not a statement.
    assert not structural_flags(r"c_i x_i")["statement"]


def test_beta_counts_only_symbols_the_reviewer_typed():
    d = _dossier(r"c_i x_i \le b_i")
    # c, x, b are all untyped; the binder gives nothing here.
    bare = paper_record(d, {})
    assert bare["formulas"][0]["beta"] == 0.0
    assert bare["n_resolved"] == 0

    partial = paper_record(d, {"c": "parameter", "x": "variable"})
    assert partial["formulas"][0]["beta"] == round(2 / 3, 3)
    assert partial["n_resolved"] == 0

    full = paper_record(d, {"c": "parameter", "x": "variable", "b": "parameter"})
    assert full["formulas"][0]["beta"] == 1.0
    assert full["n_resolved"] == 1


def test_binder_typing_is_free_prefill():
    """A family the binder names, and the body then uses, needs no reviewer tap."""
    d = _dossier(r"\sum_{t = 1}^{T} x_t \le T")
    rec = paper_record(d, {"x": "variable"})
    assert rec["n_binder_typed"] == 1  # T, credited without a reviewer verdict
    assert rec["n_resolved"] == 1


def test_ready_needs_both_clean_and_resolved():
    """Full breakdown alone is not enough: a glued record still needs a human."""
    d = _dossier(r"x_i \le b, \quad y_j \ge c")
    rec = paper_record(d, {"x": "variable", "b": "parameter", "y": "variable", "c": "parameter"})
    entry = rec["formulas"][0]
    assert entry["resolved"] and not entry["single"]
    assert rec["n_ready"] == 0


def test_macros_are_deterministic_and_complete():
    r = {
        "papers": 2,
        "formulas": 5,
        "symbol_pairs": 9,
        "symbols_per_paper_median": 4,
        "symbols_per_paper_max": 5,
        "symbols_per_formula_median": 3,
        "symbols_per_formula_max": 4,
        "binder_typed_pairs": 1,
        "binder_typed_pct": 11.1,
        "symbols_to_leverage_median": 3,
        "leverage_target_pct": 80,
        "structural_axes": {"parses": 5, "single": 4, "renders": 5, "statement": 5},
        "structural_axes_pct": {
            "parses": 100.0,
            "single": 80.0,
            "renders": 100.0,
            "statement": 100.0,
        },
        "structurally_clean": 4,
        "structurally_clean_pct": 80.0,
        "typed_pairs": 2,
        "resolved": 1,
        "resolved_pct": 20.0,
        "ready": 1,
        "ready_pct": 20.0,
    }
    tex = render_macros(r)
    assert render_macros(r) == tex
    assert r"\newcommand{\resSymbolPairs}{9}" in tex
    assert r"\newcommand{\resStructuralCleanPct}{80.0}" in tex
    # Every macro the paper may \input must be defined exactly once.
    names = [line.split("}")[0] for line in tex.splitlines() if line.startswith("\\newcommand")]
    assert len(names) == len(set(names))
