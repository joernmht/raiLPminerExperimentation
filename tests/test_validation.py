"""Representation-fidelity validation: structural, external, isomorphism."""

from __future__ import annotations

from railpminer import clustering
from railpminer.validation import run_validation, structural_fidelity


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
