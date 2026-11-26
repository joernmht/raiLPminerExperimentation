"""Shared test fixtures.

Importing ``railpminer`` already makes ``lp2graph`` importable (via the sibling
fallback in ``railpminer._lp2graph``), so tests need no PYTHONPATH gymnastics.
"""

from __future__ import annotations

import shutil
from dataclasses import replace

import pytest

from railpminer.config import PipelineConfig
from railpminer.corpus import load_corpus

#: The pipeline stages (clustering, stability bootstrap, labeling) are
#: super-linear in the number of models, and the live corpus grows with every
#: promotion. Tests of the pipeline's *logic* therefore run on a bounded,
#: deterministic slice of it: every seed template plus the first promoted
#: papers by id. Tests that must see every entry (loading, provenance
#: alignment, codec round trip) use ``full_config`` / ``full_corpus``.
PIPELINE_TEST_PROMOTED = 4


@pytest.fixture(scope="session")
def full_config() -> PipelineConfig:
    """The live corpus, whole."""
    return PipelineConfig()


@pytest.fixture(scope="session")
def full_corpus(full_config: PipelineConfig):
    return load_corpus(full_config)


@pytest.fixture(scope="session")
def config(full_config: PipelineConfig, tmp_path_factory: pytest.TempPathFactory) -> PipelineConfig:
    """A bounded slice of the live corpus (see :data:`PIPELINE_TEST_PROMOTED`)."""
    root = tmp_path_factory.mktemp("pipeline_corpus")
    paths = sorted(full_config.formulations_dir.glob("*.json"))
    seeds = [p for p in paths if not p.name.startswith("10.")]
    promoted = [p for p in paths if p.name.startswith("10.")][:PIPELINE_TEST_PROMOTED]
    for sub in ("formulations", "provenance"):
        (root / sub).mkdir()
    for path in [*seeds, *promoted]:
        shutil.copy2(path, root / "formulations" / path.name)
        prov = full_config.provenance_dir / path.name
        if prov.exists():
            shutil.copy2(prov, root / "provenance" / path.name)
    shutil.copytree(full_config.instances_dir, root / "instances")
    shutil.copy2(full_config.manifest_path, root / "manifest.json")
    return replace(full_config, corpus_dir=root)


@pytest.fixture(scope="session")
def corpus(config: PipelineConfig):
    return load_corpus(config)


@pytest.fixture(autouse=True)
def _heartbeat_to_tmp(tmp_path, monkeypatch):
    """Never let a test's CLI entry point write the live corpus/factory_status.json
    (ADR-0018): heartbeats go to the test's tmp dir instead."""
    monkeypatch.setenv("RAILP_FACTORY_STATUS", str(tmp_path / "factory_status.json"))
