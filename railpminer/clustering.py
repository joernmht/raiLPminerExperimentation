"""Stage 3+4 — feature construction and multi-level clustering (M2 + M3).

Thin orchestration over :func:`lp2graph.mining.cluster.induce`, which runs the
cluster-and-name operator bottom-up (variables/parameters -> constraints/
objective -> model type) and, in parallel, the text-only domain and
solution-approach clusterings. Feature construction (M2: lexical concepts
folded together with the structural type signature and metrics) happens inside
``induce``; we expose its result plus two validity diagnostics the paper
reports: the per-level silhouette, and the stability of each partition under
perturbations of the clustering configuration (adjusted Rand index).
"""

from __future__ import annotations

from dataclasses import dataclass

from lp2graph.core.model import Formulation
from lp2graph.mining.cluster import (
    ClusterConfig,
    LevelResult,
    Taxonomy,
    adjusted_rand_index,
    induce,
)

from . import _lp2graph  # noqa: F401
from .config import PipelineConfig

#: The five taxonomy axes, in induction order.
LEVELS: tuple[str, ...] = ("V", "C", "M", "domain", "solution_approach")


def build_taxonomy(
    formulations: list[Formulation], config: PipelineConfig | None = None
) -> Taxonomy:
    """Induce the full multi-level taxonomy from the corpus formulations."""
    config = config or PipelineConfig()
    return induce(formulations, config.cluster)


def _level(tax: Taxonomy, name: str) -> LevelResult:
    return {
        "V": tax.level_v,
        "C": tax.level_c,
        "M": tax.level_m,
        "domain": tax.domain,
        "solution_approach": tax.solution_approach,
    }[name]


def silhouettes(tax: Taxonomy) -> dict[str, float]:
    """Per-level silhouette coefficient (cluster separation quality)."""
    return {name: _level(tax, name).clustering.silhouette for name in LEVELS}


@dataclass(frozen=True, slots=True)
class StabilitySummary:
    """How robust each level's partition is to clustering-config choices.

    For every level we re-induce the taxonomy under a set of perturbed configs
    and measure the adjusted Rand index (ARI) between each perturbed labeling
    and the reference one. ARI of 1.0 means the partition is unchanged; values
    near 0 mean it is an artefact of the parameter choice.
    """

    reference_version: str
    variants: tuple[str, ...]
    ari_by_level: dict[str, dict[str, float]]
    ari_min_by_level: dict[str, float]


def _default_variants(base: ClusterConfig) -> dict[str, ClusterConfig]:
    """Reasonable perturbations: tighter/looser threshold, a different seed."""
    from dataclasses import replace

    return {
        "threshold-0.6": replace(base, distance_threshold=0.6),
        "threshold-0.8": replace(base, distance_threshold=0.8),
        "seed-1": replace(base, seed=1),
    }


def stability(
    formulations: list[Formulation],
    config: PipelineConfig | None = None,
    *,
    reference: Taxonomy | None = None,
) -> StabilitySummary:
    """Measure partition stability across perturbed clustering configs."""
    config = config or PipelineConfig()
    ref = reference or build_taxonomy(formulations, config)
    variants = _default_variants(config.cluster)

    ari_by_level: dict[str, dict[str, float]] = {name: {} for name in LEVELS}
    for vname, vcfg in variants.items():
        alt = induce(formulations, vcfg)
        for name in LEVELS:
            ref_labels = list(_level(ref, name).clustering.labels)
            alt_labels = list(_level(alt, name).clustering.labels)
            ari_by_level[name][vname] = adjusted_rand_index(ref_labels, alt_labels)

    ari_min = {
        name: (min(scores.values()) if scores else 1.0) for name, scores in ari_by_level.items()
    }
    return StabilitySummary(
        reference_version=config.cluster.version,
        variants=tuple(variants),
        ari_by_level=ari_by_level,
        ari_min_by_level=ari_min,
    )


__all__ = [
    "LEVELS",
    "StabilitySummary",
    "build_taxonomy",
    "silhouettes",
    "stability",
]
