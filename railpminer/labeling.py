"""Stage 5 — multi-level labeling (M4).

Clustering discovers anonymous, corpus-specific groups; labeling assigns each
entity a value from a controlled vocabulary that is stable and reusable. This
stage drives lp2graph's two-stage labeling service (rule seed -> calibrated
linear SVM -> confident write-back, with a closed loop) on the structural
dimensions of the taxonomy.

Because the harness runs without a human in the loop, the human-adjudication
oracle is replaced by a *deterministic structural labeler* read from each
entity's LP2Graph type signature. That same labeler seeds Stage-1 rules and
supplies the gold set, so the run is fully reproducible by replay. Every
emitted label records its source (``rule`` / ``clf`` / ``human`` for the
oracle / ``gold`` / ``seed_fallback``) so the provenance stays transparent.

Two structural dimensions are labeled here:

- **Level C → ``constraint_family``** (headway/separation, resource, linking, …)
- **Level V → ``variable_type``** (sequencing indicator, time/position variable,
  big-M parameter, …)

The model-type and the two text-derived dimensions (domain, solution approach)
are carried as the cluster *names* produced by Stage-4 (see
:mod:`railpminer.taxonomy_export`): the paper derives domain and approach from
the paper text, which the canonical templates of the seed corpus do not carry.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from lp2graph.core.model import Formulation
from lp2graph.mining.homologize import Entity, corpus_entities
from lp2graph.mining.label import (
    LabelingService,
    SeedRule,
    seed_vocabulary,
)

from . import _lp2graph  # noqa: F401
from .config import PipelineConfig

StructuralLabeler = Callable[[Entity], str]


# --------------------------------------------------------------------------- #
# Deterministic structural labelers (read straight off the type signature).
# --------------------------------------------------------------------------- #

_CONSTRAINT_FAMILY: dict[str, str] = {
    "headway": "separation",
    "capacity": "resource",
    "set_packing": "resource",
    "big_m": "linking",
    "modulo": "periodicity",
    "soft": "soft_regularity",
    "linear": "balance_generic",
    "min/sum": "objective",
    "min/weighted_sum": "objective",
    "min/lexicographic": "objective",
}


def constraint_family(entity: Entity) -> str:
    """Map a constraint/objective entity to its family label."""
    return _CONSTRAINT_FAMILY.get(entity.signature.kind, "unassigned")


def variable_type(entity: Entity) -> str:
    """Map a variable/parameter entity to its type label."""
    sig = entity.signature
    kind, role, domain = sig.kind, sig.role, sig.domain
    if role == "parameter":
        if kind == "big_m":
            return "big_m_parameter"
        return "data_parameter"
    if role == "slack":
        return "slack_variable"
    if kind == "binary":
        if "assign" in domain or "select" in domain:
            return "assignment_indicator"
        if role == "indicator":
            return "indicator_variable"
        return "binary_indicator"
    if kind == "integer":
        return "count_variable"
    if kind == "non_negative":
        if "tim" in domain:
            return "time_position_variable"
        return "continuous_variable"
    return "unassigned"


# --------------------------------------------------------------------------- #
# Results
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class LabelOutcome:
    """One entity's final label and where it came from."""

    entity_id: str
    formulation_id: str
    value: str
    source: str


@dataclass(frozen=True, slots=True)
class DimensionLabeling:
    """The labeling result for one (level, dimension)."""

    level: str
    dimension: str
    vocabulary: tuple[str, ...]
    outcomes: tuple[LabelOutcome, ...]
    loop_reports: tuple[dict[str, object], ...]
    source_counts: dict[str, int]

    def label_of(self, entity_id: str) -> str | None:
        for o in self.outcomes:
            if o.entity_id == entity_id:
                return o.value
        return None


@dataclass(frozen=True, slots=True)
class LabelingResult:
    """All labeled dimensions for a corpus."""

    dimensions: dict[str, DimensionLabeling]


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def _seed_rules(entities: list[Entity], labeler: StructuralLabeler) -> list[SeedRule]:
    """Emit a Stage-1 rule for every constraint *kind* that maps to a single
    label (the unambiguous, high-precision seeds the rule layer is meant for).
    """
    kind_labels: dict[str, set[str]] = {}
    for e in entities:
        kind_labels.setdefault(e.signature.kind, set()).add(labeler(e))
    rules = [
        SeedRule(f"kind:{kind}", next(iter(labels)))
        for kind, labels in sorted(kind_labels.items())
        if len(labels) == 1 and next(iter(labels)) != "unassigned"
    ]
    return rules


def _loop_report_dict(report: object) -> dict[str, object]:
    g = getattr(report, "guardrail", None)
    gold = getattr(g, "gold", None) if g is not None else None
    return {
        "loop": getattr(report, "loop", None),
        "n_auto_accept": getattr(report, "n_auto_accept", None),
        "n_adjudicate": getattr(report, "n_adjudicate", None),
        "n_defer": getattr(report, "n_defer", None),
        "gold_micro_precision": getattr(gold, "micro_precision", None),
        "lexicon_version": getattr(report, "lexicon_version", None),
        "clf_version": getattr(report, "clf_version", None),
    }


def label_dimension(
    formulations: list[Formulation],
    level: str,
    dimension: str,
    labeler: StructuralLabeler,
    config: PipelineConfig | None = None,
    *,
    n_loops: int = 2,
) -> DimensionLabeling:
    """Run the closed-loop labeling service for one (level, dimension)."""
    config = config or PipelineConfig()
    entities = corpus_entities(formulations, level)

    truth = {e.id: labeler(e) for e in entities}
    gold = entities[::3]
    gold_ids = {e.id for e in gold}
    pool = [e for e in entities if e.id not in gold_ids]
    gold_labels = {e.id: truth[e.id] for e in gold}

    vocab = seed_vocabulary(level, dimension, set(truth.values()))
    rules = _seed_rules(entities, labeler)

    service = LabelingService.bootstrap(vocab, rules, gold, gold_labels, config.labeling)

    def oracle(entity: Entity) -> str | None:
        return truth.get(entity.id)

    reports = [service.run_loop(pool, oracle) for _ in range(n_loops)]
    store_labels = service.store.latest_labels(dimension)

    outcomes: list[LabelOutcome] = []
    for e in entities:
        if e.id in store_labels:
            rec = store_labels[e.id]
            value, source = rec.value, str(rec.source)
        elif e.id in gold_ids:
            value, source = truth[e.id], "gold"
        else:
            # Deferred by the loop: fall back to the deterministic seed labeler.
            value, source = truth[e.id], "seed_fallback"
        outcomes.append(LabelOutcome(e.id, e.formulation_id, value, source))

    source_counts = dict(Counter(o.source for o in outcomes))
    return DimensionLabeling(
        level=level,
        dimension=dimension,
        vocabulary=vocab.labels,
        outcomes=tuple(outcomes),
        loop_reports=tuple(_loop_report_dict(r) for r in reports),
        source_counts=source_counts,
    )


def run_labeling(
    formulations: list[Formulation], config: PipelineConfig | None = None
) -> LabelingResult:
    """Label the structural dimensions of the taxonomy."""
    config = config or PipelineConfig()
    dims = {
        "constraint_family": label_dimension(
            formulations, "C", "constraint_family", constraint_family, config
        ),
        "variable_type": label_dimension(formulations, "V", "variable_type", variable_type, config),
    }
    return LabelingResult(dimensions=dims)


__all__ = [
    "DimensionLabeling",
    "LabelOutcome",
    "LabelingResult",
    "constraint_family",
    "label_dimension",
    "run_labeling",
    "variable_type",
]
