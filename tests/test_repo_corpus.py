"""Tests for corpusbuilder.repo_corpus — offline, against a synthetic fixture.

The suite must not depend on the sibling ``~/lp2graph/corpus`` checkout: every
test drives :func:`convert_all` (or the row helpers) on fixtures built here.
What matters most is what the paper promises: a converted entry LOADS as a
validated lp2graph ``Formulation``, exclusions and failures carry honest
causes, capture losses are recorded (not silent), and reruns are byte-stable.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpusbuilder.repo_corpus import (
    ConversionFailure,
    _binder_lookup,
    _distribute,
    build_table,
    canonicalize_row,
    check_row,
    convert_all,
    parse_symbol_name,
)

# The sanctioned lp2graph shim (see corpusbuilder/promote.py header).
from railpminer import _lp2graph  # noqa: F401  # ruff: isort:skip
from lp2graph import load


def _toy_doc() -> dict:
    """One repo, three models: convertible / pricing-excluded / outside-grammar."""
    return {
        "repo": "acme/toy",
        "url": "https://example.org/acme/toy",
        "license": "MIT",
        "area": "railway-test",
        "solver": "CBC",
        "extraction_confidence": "high",
        "paper_reference": "10.5555/toy.2026",
        "models": [
            {
                "model_id": "toy-assignment-milp",
                "description": "Toy assignment model.",
                "sets_indices": [
                    {"name": "W", "meaning": "workers"},
                    {"name": "J", "meaning": "jobs"},
                ],
                "parameters": [{"name": "c_{wj}", "meaning": "cost", "domain": "R_>=0"}],
                "decision_variables": [
                    {"name": "x_{wj}", "type": "binary", "meaning": "assignment"}
                ],
                "objective": {
                    "sense": "min",
                    "expression_latex": r"\min \sum_{w \in W} \sum_{j \in J} c_{wj} x_{wj}",
                    "expression_plain": "minimize cost",
                },
                "constraints": [
                    {
                        "name": "worker_once",
                        "indexed_over": "w in W",
                        "expression_latex": r"\sum_{j \in J} x_{wj} = 1",
                        "expression_plain": "one job",
                    },
                    {
                        "name": "job_once",
                        "indexed_over": "j in J",
                        "expression_latex": r"\sum_{w \in W} x_{wj} = 1",
                        "expression_plain": "one worker",
                    },
                    {
                        # Pure domain row: folded into the declaration, not a body row.
                        "name": "domain_row",
                        "indexed_over": "w in W, j in J",
                        "expression_latex": r"x_{wj} \in \{0,1\}",
                        "expression_plain": "binary",
                    },
                    {
                        # Search machinery: omitted with a note, never a body row.
                        "name": "lazy_cut",
                        "indexed_over": "added during branch-and-bound",
                        "expression_latex": r"x_{wj} \le 1",
                    },
                ],
            },
            {
                "model_id": "toy-pricing-subproblem",
                "description": "Column generation pricing step.",
                "sets_indices": [],
                "parameters": [],
                "decision_variables": [],
                "objective": {"sense": "min", "expression_latex": r"\min 0"},
                "constraints": [],
            },
            {
                "model_id": "toy-frac-objective",
                "description": "Objective outside the flat grammar.",
                "sets_indices": [{"name": "I", "meaning": "items"}],
                "parameters": [{"name": "d", "meaning": "scale", "domain": "R_>=0"}],
                "decision_variables": [{"name": "t_i", "type": "integer", "meaning": "time"}],
                "objective": {
                    "sense": "min",
                    "expression_latex": r"\min \frac{1}{d} \sum_{i \in I} t_i",
                },
                "constraints": [
                    {
                        "name": "lb",
                        "indexed_over": "i in I",
                        "expression_latex": r"t_i \ge 1",
                    }
                ],
            },
        ],
    }


@pytest.fixture
def toy_run(tmp_path: Path) -> tuple[dict, Path]:
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    (src / "acme__toy.json").write_text(json.dumps(_toy_doc()), encoding="utf-8")
    report = convert_all(src=src, out=out)
    return report, out


def test_converted_entry_loads_and_validates(toy_run: tuple[dict, Path]) -> None:
    report, out = toy_run
    assert report["converted"] == 1
    assert report["converted_ids"] == ["acme__toy__toy-assignment-milp"]
    # lp2graph.load runs pydantic + semantic validation; not raising IS the assertion.
    f = load(out / "acme__toy__toy-assignment-milp.json")
    assert f.family == "milp"
    assert {i.name for i in f.indices} == {"W", "J"}
    assert [v.name for v in f.variables] == ["x"]
    assert f.variables[0].shape == ("W", "J")
    assert f.variables[0].domain == "binary"
    assert f.objective is not None and f.objective.sense == "min"
    # Domain row folded and machinery omitted: exactly the two real constraints.
    assert [c.name for c in f.constraints] == ["worker_once", "job_once"]
    assert f.constraints[0].quantifiers[0].over == "W"


def test_exclusion_and_failure_are_honest(toy_run: tuple[dict, Path]) -> None:
    report, out = toy_run
    assert report["excluded"] == [
        {
            "id": "acme__toy__toy-pricing-subproblem",
            "reason": "column_generation_pricing_subproblem",
        }
    ]
    (failure,) = report["failed"]
    assert failure["id"] == "acme__toy__toy-frac-objective"
    assert failure["cause"] == "outside_grammar"
    assert "frac" in failure["detail"]
    # A failed model must not leave a formulation behind.
    assert not (out / "acme__toy__toy-frac-objective.json").exists()


def test_sidecar_carries_repo_license_doi_and_notes(toy_run: tuple[dict, Path]) -> None:
    _, out = toy_run
    meta = json.loads((out / "acme__toy__toy-assignment-milp.meta.json").read_text())
    assert meta["repo"] == "acme/toy"
    assert meta["license"] == "MIT"
    assert meta["source_paper"] == {"doi": "10.5555/toy.2026"}
    joined = " ".join(meta["notes"])
    assert "domain_row" in joined and "folded" in joined
    assert "lazy_cut" in joined and "machinery" in joined


def test_qubo_repo_is_excluded(tmp_path: Path) -> None:
    doc = _toy_doc()
    doc["repo"] = "iitis/railways_HOBO"
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    (src / "iitis__railways_HOBO.json").write_text(json.dumps(doc), encoding="utf-8")
    report = convert_all(src=src, out=out)
    assert report["converted"] == 0
    assert {e["reason"] for e in report["excluded"]} == {"qubo_not_linear"}
    assert len(report["excluded"]) == 3  # every model of the repo


def test_determinism_byte_identical(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    (src / "acme__toy.json").write_text(json.dumps(_toy_doc()), encoding="utf-8")
    outs = []
    for name in ("out_a", "out_b"):
        out = tmp_path / name
        convert_all(src=src, out=out)
        outs.append({p.name: p.read_bytes() for p in sorted(out.iterdir())})
    assert outs[0].keys() == outs[1].keys()
    for name in outs[0]:
        assert outs[0][name] == outs[1][name], f"{name} differs between reruns"


# ---------------------------------------------------------------------------
# Unit-level guarantees
# ---------------------------------------------------------------------------


def test_parse_symbol_name_shapes() -> None:
    p = parse_symbol_name("omega^e_a")
    assert (p.base, p.sup, p.sub_tokens) == ("omega", "e", ("a",))
    p = parse_symbol_name("x_sec_{tr,t,s}")
    assert (p.base, p.sub_tokens) == ("x_sec", ("tr", "t", "s"))
    p = parse_symbol_name("m_{e,e'}^{d}")  # scripts in either order
    assert (p.base, p.sup, p.sub_tokens) == ("m", "d", ("e", "e'"))
    p = parse_symbol_name("T_end")  # binder-or-name decided later, tokens kept
    assert (p.base, p.sub_tokens) == ("T", ("end",))


def test_binder_lookup_tolerates_primes_digits_and_ijk() -> None:
    bm = {"i": "I", "r": "R", "t": "T"}
    assert _binder_lookup("r'", bm) == "R"
    assert _binder_lookup("tr1", bm) is None  # 'tr' unmapped: no invention
    assert _binder_lookup("t2", bm) == "T"
    assert _binder_lookup("j", bm) == "I"  # j ranges where i does


def test_distribute_refuses_leftover_factor() -> None:
    # 2h(1-m): popping only 'h' would silently drop the 2 from later pieces.
    text = r"2 h (1 - m_{e})"
    assert _distribute(text, []) == text
    # -M(1-y) has a clean boundary and distributes exactly.
    out = _distribute(r"z \ge - M (1 - y_{i})", [])
    assert r"\cdot" in out and "(" not in out
    assert "- M" in out and "+ M" in out


def test_check_row_refuses_silent_residue() -> None:
    with pytest.raises(ConversionFailure):
        check_row("r", r"x_{i} \le \frac{T}{f}")  # \frac would be swallowed
    with pytest.raises(ConversionFailure):
        check_row("r", r"\sum_{} x_{i} = 1")  # empty binder = wrong algebra
    with pytest.raises(ConversionFailure):
        check_row("r", r"q_{i} y_{j} = 1")  # juxtaposed atoms: parser keeps one
    check_row("r", r"\sum_{i \in \mathcal{I}} c \cdot x_{i} \le 1")  # canonical: fine


def test_restriction_widening_is_recorded_not_silent() -> None:
    model = {
        "model_id": "m",
        "sets_indices": [{"name": "L", "meaning": "lines"}],
        "parameters": [{"name": "f_e", "meaning": "bound", "domain": "R_>=0"}],
        "decision_variables": [{"name": "f_l", "type": "integer", "meaning": "freq"}],
        "objective": {"sense": "min", "expression_latex": r"\min \sum_{l \in L} f_l"},
        "constraints": [],
    }
    tab = build_table(model)
    remarks: list[str] = []
    row = canonicalize_row(r"\sum_{l \in L : e \in l} f_{l} \ge 1", tab, [], remarks)
    assert r"\sum_{l \in \mathcal{L}}" in row
    assert any("restriction" in r for r in remarks)
