"""Corpus loading: every formulation validates and has aligned provenance."""

from __future__ import annotations

from railpminer.corpus import load_corpus


def test_corpus_loads_without_failures(corpus) -> None:
    assert len(corpus) >= 1
    assert corpus.load_failures == ()


def test_formulations_and_records_aligned(corpus) -> None:
    forms = corpus.formulations
    recs = corpus.records
    assert len(forms) == len(recs)
    # ProvenanceRecord.source_id matches the formulation id it annotates.
    for f, r in zip(forms, recs):
        assert r.source_id == f.id


def test_entries_sorted_by_id(corpus) -> None:
    ids = [f.id for f in corpus.formulations]
    assert ids == sorted(ids)


def test_manifest_has_freeze_date_and_queries(corpus) -> None:
    m = corpus.manager.manifest
    assert m.frozen_search_date
    assert len(m.queries) >= 1


def test_loading_is_deterministic(config) -> None:
    a = load_corpus(config)
    b = load_corpus(config)
    assert [f.id for f in a.formulations] == [f.id for f in b.formulations]
