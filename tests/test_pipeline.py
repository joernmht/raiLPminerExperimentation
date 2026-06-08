"""End-to-end pipeline: it runs, writes artifacts, and is reproducible."""

from __future__ import annotations

import json
from dataclasses import replace

from railpminer import run


def test_run_produces_taxonomy_and_dataset(config) -> None:
    result = run(config, write=False)
    assert result.dataset["n_formulations"] == len(result.corpus)
    counts = result.taxonomy.summary()
    # All five axes induce at least one cluster.
    assert all(counts[axis] >= 1 for axis in ("V", "C", "M", "domain", "solution_approach"))


def test_run_is_deterministic(config) -> None:
    a = run(config, write=False)
    b = run(config, write=False)
    # The mined dataset is byte-identical across runs (the core determinism claim).
    assert json.dumps(a.dataset, sort_keys=True, default=str) == json.dumps(
        b.dataset, sort_keys=True, default=str
    )
    assert a.taxonomy.summary() == b.taxonomy.summary()
    assert a.silhouettes == b.silhouettes


def test_run_writes_all_artifacts(tmp_path, config) -> None:
    cfg = replace(config, output_dir=tmp_path)
    run(cfg, write=True)
    expected = {
        "dataset.json",
        "taxonomy.json",
        "taxonomy.csv",
        "taxonomy_axes.tex",
        "clustering_report.json",
        "validation_report.json",
        "run_summary.json",
    }
    written = {p.name for p in tmp_path.iterdir()}
    assert expected <= written
    # Artifacts are valid JSON where claimed.
    json.loads((tmp_path / "dataset.json").read_text())
    json.loads((tmp_path / "run_summary.json").read_text())


def test_summary_reports_versions(config) -> None:
    summary = run(config, write=False).summary()
    assert summary["versions"]["clustering"].startswith("cluster-")
