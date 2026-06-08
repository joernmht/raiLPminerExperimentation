"""raiLPminer — a reproducible LP-mining experiment harness.

This package applies the *LP Mining with LP2Graph* method to a corpus of
published LP/MILP formulations and emits the paper's artifacts: a versioned,
regenerable dataset and an induced taxonomy of variable types, constraint
families and model types, together with the representation-fidelity validation.

It is deliberately thin: the *method* lives in the deterministic ``lp2graph``
library (its ``mining`` package, modules M1–M6). This harness is the
*experiment* — corpus management, end-to-end orchestration, and artifact
generation — analogous to how the repository's earlier version drove the
(now superseded) LLM-generation experiments.

Quick start::

    from railpminer import run, PipelineConfig
    result = run(PipelineConfig())          # writes artifacts under outputs/
    print(result.summary())

The six forward stages plus validation map onto these modules:

    corpus      Stage 1   corpus construction + provenance (M5)
    clustering  Stage 3-4 feature construction + multi-level clustering (M2, M3)
    labeling    Stage 5   two-stage closed-loop labeling (M4)
    dataset     Stage 6   the mined per-formulation dataset
    taxonomy_export Stage 6   the induced taxonomy table (JSON/CSV/LaTeX)
    validation  Stage 7   structural/external fidelity + isomorphism (codec, solve, M6)
    pipeline              the deterministic glue (`run`)
"""

from __future__ import annotations

from . import _lp2graph  # noqa: F401  (import side effect: make lp2graph importable)
from .config import ClusterConfig, LoopConfig, PipelineConfig
from .corpus import LoadedCorpus, load_corpus
from .pipeline import MiningResult, run

__version__ = "1.0.0"

__all__ = [
    "ClusterConfig",
    "LoadedCorpus",
    "LoopConfig",
    "MiningResult",
    "PipelineConfig",
    "__version__",
    "load_corpus",
    "run",
]
