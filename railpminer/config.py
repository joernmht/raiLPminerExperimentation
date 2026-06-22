"""Configuration for the LP-mining experiment.

A single :class:`PipelineConfig` carries every knob the pipeline needs:
where the corpus lives, where outputs go, and the *versioned* configuration
of the clustering and labeling stages. Determinism is the whole point of the
method, so the config holds explicit seeds and thresholds rather than
relying on defaults scattered through the code.

The clustering / labeling configs are the real ones from
:mod:`lp2graph.mining`; we re-export thin defaults here so a caller can tune
the experiment without importing the library directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lp2graph.mining.cluster import ClusterConfig
from lp2graph.mining.label import LoopConfig

from . import _lp2graph  # noqa: F401  (ensures lp2graph is importable)

#: Repository root (the directory containing this package).
REPO_ROOT: Path = Path(__file__).resolve().parent.parent


@dataclass(frozen=True, slots=True)
class PipelineConfig:
    """All inputs, outputs and tuning for one reproducible mining run."""

    #: Directory holding ``formulations/``, ``provenance/``, ``instances/``
    #: and ``manifest.json``.
    corpus_dir: Path = REPO_ROOT / "corpus"

    #: Where generated artifacts (dataset, taxonomy, reports) are written.
    output_dir: Path = REPO_ROOT / "outputs"

    #: M3 cluster-and-name configuration (algorithm, threshold, seed, version).
    cluster: ClusterConfig = field(default_factory=ClusterConfig)

    #: M4 closed-loop labeling configuration (confidence gates, seed).
    labeling: LoopConfig = field(default_factory=LoopConfig)

    #: Tolerance for the external-fidelity optimum check ``|z* - z_paper| <= eps``.
    optimum_tolerance: float = 1e-6

    @property
    def formulations_dir(self) -> Path:
        return self.corpus_dir / "formulations"

    @property
    def provenance_dir(self) -> Path:
        return self.corpus_dir / "provenance"

    @property
    def instances_dir(self) -> Path:
        return self.corpus_dir / "instances"

    @property
    def manifest_path(self) -> Path:
        return self.corpus_dir / "manifest.json"


__all__ = ["REPO_ROOT", "ClusterConfig", "LoopConfig", "PipelineConfig"]
