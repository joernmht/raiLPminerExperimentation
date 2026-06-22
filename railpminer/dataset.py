"""Stage 6 (part 1) — the mined dataset.

Assembles the per-formulation dataset described in the paper's "Outputs"
section: for every formulation, its canonical-model summary, structural metrics
and presence flags, multi-level labels, provenance, and validation status. The
result is machine-readable, versioned and *regenerable* — the same corpus and
versioned configuration reproduce it byte-for-byte.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from lp2graph.core.model import Formulation
from lp2graph.metrics.flags import presence_flags
from lp2graph.metrics.structural import structural_summary
from lp2graph.mining.cluster import LevelResult, Taxonomy
from lp2graph.views import schema

from . import _lp2graph  # noqa: F401
from .corpus import LoadedCorpus
from .labeling import LabelingResult
from .validation import ValidationReport


def _metric_values(d: dict[str, Any]) -> dict[str, Any]:
    """Reduce a ``{name: MetricResult}`` map to ``{name: value}``."""
    return {name: result.value for name, result in d.items()}


def _model_level_labels(level: LevelResult) -> dict[str, str]:
    """formulation_id -> cluster name for a model-level (one-entity-per-model) pass."""
    cl = level.clustering
    out: dict[str, str] = {}
    for entity, label in zip(level.entities, cl.labels, strict=True):
        out[entity.formulation_id] = cl.names.get(label, "unassigned")
    return out


def build_dataset(
    corpus: LoadedCorpus,
    tax: Taxonomy,
    labeling: LabelingResult,
    validation: ValidationReport,
) -> dict[str, Any]:
    """Assemble the full mined dataset as a JSON-serializable dict."""
    # Per-formulation structural labels from the labeling stage.
    entity_labels: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for dim_name, dim in labeling.dimensions.items():
        for o in dim.outcomes:
            entity_labels[o.formulation_id][dim_name].append(
                {"entity": o.entity_id.split("::")[-1], "label": o.value, "source": o.source}
            )

    model_type = _model_level_labels(tax.level_m)
    domain = _model_level_labels(tax.domain)
    approach = _model_level_labels(tax.solution_approach)

    structural_ok = {s.formulation_id: s.round_trip_ok for s in validation.structural}
    external_by_id: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for e in validation.external:
        external_by_id[e.formulation_id].append(
            {
                "instance": e.instance,
                "expected_optimum": e.expected_optimum,
                "cross_solver_agree": e.cross_solver_agree,
                "matches_expected": e.matches_expected,
                "solvers": [asdict(s) for s in e.solvers],
            }
        )

    records: list[dict[str, Any]] = []
    for formulation, prov in corpus.manager.entries:
        f: Formulation = formulation
        records.append(
            {
                "id": f.id,
                "name": f.name,
                "family": f.family,
                "description": f.description,
                "tags": list(f.tags),
                "provenance": asdict(prov),
                "metrics": _metric_values(structural_summary(schema(f))),
                "flags": _metric_values(presence_flags(f)),
                "labels": {
                    "model_type": model_type.get(f.id, "unassigned"),
                    "domain": domain.get(f.id, "unassigned"),
                    "solution_approach": approach.get(f.id, "unassigned"),
                    **{k: v for k, v in entity_labels.get(f.id, {}).items()},
                },
                "validation": {
                    "structural_round_trip": structural_ok.get(f.id),
                    "external": external_by_id.get(f.id, []),
                },
            }
        )

    return {
        "manifest": corpus.manager.manifest.to_dict(),
        "n_formulations": len(records),
        "formulations": records,
    }


__all__ = ["build_dataset"]
