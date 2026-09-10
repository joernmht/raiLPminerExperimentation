"""Shared test fixtures.

Importing ``railpminer`` already makes ``lp2graph`` importable (via the sibling
fallback in ``railpminer._lp2graph``), so tests need no PYTHONPATH gymnastics.
"""

from __future__ import annotations

import pytest

from railpminer.config import PipelineConfig
from railpminer.corpus import load_corpus


@pytest.fixture(scope="session")
def config() -> PipelineConfig:
    return PipelineConfig()


@pytest.fixture(scope="session")
def corpus(config: PipelineConfig):
    return load_corpus(config)


@pytest.fixture(autouse=True)
def _heartbeat_to_tmp(tmp_path, monkeypatch):
    """Never let a test's CLI entry point write the live corpus/factory_status.json
    (ADR-0018): heartbeats go to the test's tmp dir instead."""
    monkeypatch.setenv("RAILP_FACTORY_STATUS", str(tmp_path / "factory_status.json"))
