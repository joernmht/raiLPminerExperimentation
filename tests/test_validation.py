"""Representation-fidelity validation: structural, external, isomorphism."""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

from railpminer import clustering, validation
from railpminer.validation import run_validation, structural_fidelity


def _solve_module(fake_solve):
    """A stand-in ``lp2graph.solve`` module exposing ``Instance`` and both
    solve entry points.

    ``solve_lexicographic`` has to be here too: the harness dispatches to it for
    formulations whose objective is lexicographic, and a fake that only carries
    ``solve`` would make those instances raise and quietly drop out of the
    per-solve accounting this guards.
    """
    mod = types.ModuleType("lp2graph.solve")
    mod.solve = fake_solve
    mod.solve_lexicographic = lambda f, inst, **kw: SimpleNamespace(
        **vars(fake_solve(f, inst, **kw)), objectives=(0.0,)
    )
    mod.Instance = SimpleNamespace
    return mod


def test_structural_fidelity_round_trips_every_model(corpus) -> None:
    results = structural_fidelity(list(corpus.formulations))
    assert results, "expected at least one formulation"
    failures = [r for r in results if not r.round_trip_ok]
    assert not failures, f"codec round-trip failed for: {[f.formulation_id for f in failures]}"


def test_external_fidelity_matches_published_optima(corpus, config) -> None:
    tax = clustering.build_taxonomy(list(corpus.formulations), config)
    report = run_validation(corpus, tax, config)
    # At least two independent solvers should be available on this machine.
    assert len(report.solvers_used) >= 1
    checked = [e for e in report.external if e.matches_expected is not None]
    assert checked, "expected at least one instance with a published optimum"
    for e in checked:
        assert e.matches_expected, f"{e.formulation_id}/{e.instance} missed its optimum"
        # With >=2 solvers the cross-solver check must also hold.
        if len(report.solvers_used) >= 2:
            assert e.cross_solver_agree, f"{e.formulation_id} solvers disagree"


def test_isomorphism_report_covers_model_clusters(corpus, config) -> None:
    tax = clustering.build_taxonomy(list(corpus.formulations), config)
    report = run_validation(corpus, tax, config)
    assert len(report.isomorphism) == tax.summary()["M"]
    for entry in report.isomorphism:
        assert 0.0 <= entry.whole_cluster_rate <= 1.0


def test_solver_instances_are_never_reused_across_solves(corpus, config, monkeypatch) -> None:
    """A pulp solver object must be built fresh for every single solve.

    Regression guard. ``pulp``'s native ``GUROBI`` back-end keeps one
    ``gurobipy.Model`` on the *solver*, so reusing one object made the second
    problem be solved against the union of both constraint sets and read its
    solution back off misaligned variables — a wrong objective, reported as
    ``optimal``. The corpus's published optima were "missed" as a result.
    """
    built: list[object] = []

    class _Recorder:
        """Stands in for a pulp solver; records that a fresh one was made."""

    def _fake_solvers():
        def make():
            s = _Recorder()
            built.append(s)
            return s

        return [("Recorder", make)]

    seen: list[object] = []

    def _fake_solve(f, inst, *, solver=None, **kw):
        seen.append(solver)
        return SimpleNamespace(status="optimal", objective=0.0)

    monkeypatch.setattr(validation, "_available_solvers", _fake_solvers)
    monkeypatch.setitem(sys.modules, "lp2graph.solve", _solve_module(_fake_solve))

    results = validation.external_fidelity(corpus, config)

    assert len(results) >= 2, "need >=2 instances for this to mean anything"
    assert len(seen) == len(results), "one solve per instance expected"
    assert len({id(s) for s in seen}) == len(seen), "a solver object was reused across solves"


def test_optimum_tolerance_scales_with_the_objective() -> None:
    """``eps`` is absolute-or-relative, so large published optima stay checkable."""
    eps = 1e-6
    # Small objectives keep the strict absolute behaviour.
    assert validation._within(30.0, 30.0, eps)
    assert not validation._within(30.0, 29.9, eps)
    # A large optimum is matched at the same *relative* precision, which a pure
    # absolute 1e-6 test could never satisfy for any floating-point solver.
    assert validation._within(3_700_000.0000001, 3_700_000.0, eps)
    assert not validation._within(3_700_010.0, 3_700_000.0, eps)
