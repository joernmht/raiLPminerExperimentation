"""Two-stage labeling: deterministic, complete, and from a controlled vocabulary."""

from __future__ import annotations

from railpminer.labeling import run_labeling


def test_labels_cover_every_entity_from_the_vocabulary(corpus, config) -> None:
    result = run_labeling(list(corpus.formulations), config)
    for dim in result.dimensions.values():
        assert dim.outcomes, f"no labels emitted for {dim.dimension}"
        allowed = set(dim.vocabulary)
        for o in dim.outcomes:
            assert o.value in allowed, f"{o.value!r} not in the {dim.dimension} vocabulary"
            assert o.source in {"rule", "clf", "human", "gold", "seed_fallback"}


def test_labeling_is_deterministic(corpus, config) -> None:
    a = run_labeling(list(corpus.formulations), config)
    b = run_labeling(list(corpus.formulations), config)
    for name in a.dimensions:
        la = [(o.entity_id, o.value) for o in a.dimensions[name].outcomes]
        lb = [(o.entity_id, o.value) for o in b.dimensions[name].outcomes]
        assert la == lb


def test_expected_structural_dimensions_present(corpus, config) -> None:
    result = run_labeling(list(corpus.formulations), config)
    assert set(result.dimensions) == {"constraint_family", "variable_type"}
