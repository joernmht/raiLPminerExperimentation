"""Corpus loading: every formulation validates and has aligned provenance."""

from __future__ import annotations

from railpminer.corpus import load_corpus


def test_corpus_loads_without_failures(full_corpus) -> None:
    assert len(full_corpus) >= 1
    assert full_corpus.load_failures == ()


def test_formulations_and_records_aligned(full_corpus) -> None:
    forms = full_corpus.formulations
    recs = full_corpus.records
    assert len(forms) == len(recs)
    # ProvenanceRecord.source_id matches the formulation id it annotates.
    for f, r in zip(forms, recs, strict=True):
        assert r.source_id == f.id


def test_entries_sorted_by_id(full_corpus) -> None:
    ids = [f.id for f in full_corpus.formulations]
    assert ids == sorted(ids)


def test_manifest_has_freeze_date_and_queries(corpus) -> None:
    m = corpus.manager.manifest
    assert m.frozen_search_date
    assert len(m.queries) >= 1


def test_loading_is_deterministic(config) -> None:
    a = load_corpus(config)
    b = load_corpus(config)
    assert [f.id for f in a.formulations] == [f.id for f in b.formulations]


def test_no_promoted_constraint_holds_two_sums_that_cancel():
    """Widening must never merge the two sums of a balance row (lp2graph
    ADR-0018, point 13): the corpus is scanned, not a hand-picked example."""
    import importlib.util
    import json
    from pathlib import Path

    from lp2graph.core.model import Formulation

    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "cancelling_sums_check", root / "scripts" / "checks" / "cancelling_sums_check.py"
    )
    assert spec is not None and spec.loader is not None
    check = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(check)
    hits = []
    for path in sorted((root / "corpus" / "formulations").glob("*.json")):
        formulation = Formulation.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for constraint, symbol in check.cancelling_sums(formulation):
            row = check._SOURCE_ROW.match(constraint)
            if (formulation.id, row.group(0) if row else constraint) not in check.SOURCE_REPEATS:
                hits.append((formulation.id, constraint, symbol))
    assert hits == []
