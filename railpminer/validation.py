"""Stage 7 — representation-fidelity validation (codec + solve + M6).

The taxonomy is only trustworthy if the LP2Graph representation faithfully
captures the source models. This stage establishes that empirically, along the
three fidelity claims of the paper, plus the intra-cluster homogeneity check
that says how representative a validated anchor is:

- **Structural fidelity** — the deterministic codec round-trips every model:
  ``parse(render(f)) ≡ f`` under the canonical normal form (no instance data,
  no solver).
- **External fidelity** — each model with recoverable instance data is grounded
  and solved with every available independent solver (CBC, HiGHS, …) and the
  optimum is checked against the value published with the instance, within a
  tolerance ``eps``. Cross-solver agreement rules out solver-specific artefacts.
- **Intra-cluster isomorphism (M6)** — per Level-M cluster, the schema-graph
  isomorphism rate, so a reader can judge how representative the highest-cited
  anchor is.

Solvers and the optimum check degrade honestly: a missing solver extra or a
model the grammar cannot ground is *reported*, never silently passed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import _lp2graph  # noqa: F401
from lp2graph.codec import canonical_normal_form, from_canonical_latex, to_canonical_latex
from lp2graph.core.model import Formulation
from lp2graph.mining.cluster import Taxonomy
from lp2graph.mining.isomorphism import clusters_from_labels, isomorphism_report

from .config import PipelineConfig
from .corpus import LoadedCorpus


# --------------------------------------------------------------------------- #
# Structural fidelity
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class StructuralFidelity:
    formulation_id: str
    round_trip_ok: bool
    detail: str = ""


def structural_fidelity(formulations: list[Formulation]) -> list[StructuralFidelity]:
    """Codec round-trip every formulation under the canonical normal form."""
    out: list[StructuralFidelity] = []
    for f in formulations:
        try:
            restored = from_canonical_latex(to_canonical_latex(f))
            ok = canonical_normal_form(restored) == canonical_normal_form(f)
            out.append(StructuralFidelity(f.id, ok, "" if ok else "normal-form mismatch"))
        except Exception as exc:  # noqa: BLE001 — report, never drop
            out.append(StructuralFidelity(f.id, False, f"{type(exc).__name__}: {exc}"))
    return out


# --------------------------------------------------------------------------- #
# External fidelity (cross-solver, against published optima)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class SolverOutcome:
    solver: str
    status: str
    objective: float | None
    error: str = ""


@dataclass(frozen=True, slots=True)
class ExternalFidelity:
    formulation_id: str
    instance: str
    expected_optimum: float | None
    solvers: tuple[SolverOutcome, ...]
    cross_solver_agree: bool
    matches_expected: bool | None


def _available_solvers() -> list[tuple[str, object]]:
    """Independent solvers pulp can drive here, in a fixed order."""
    try:
        import pulp
    except ModuleNotFoundError:
        return []
    names = pulp.listSolvers(onlyAvailable=True)
    solvers: list[tuple[str, object]] = []
    if "PULP_CBC_CMD" in names:
        solvers.append(("CBC", pulp.PULP_CBC_CMD(msg=False)))
    if "HiGHS" in names:
        solvers.append(("HiGHS", pulp.HiGHS(msg=False)))
    if "GUROBI" in names:
        solvers.append(("Gurobi", pulp.GUROBI(msg=False)))
    elif "GUROBI_CMD" in names:
        solvers.append(("Gurobi", pulp.GUROBI_CMD(msg=False)))
    return solvers


def external_fidelity(
    corpus: LoadedCorpus, config: PipelineConfig | None = None
) -> list[ExternalFidelity]:
    """Solve every instance with every available solver, vs its published optimum."""
    config = config or PipelineConfig()
    by_id = {f.id: f for f in corpus.formulations}
    solvers = _available_solvers()

    try:
        from lp2graph.solve import Instance, solve
    except ModuleNotFoundError:
        solve = None  # type: ignore[assignment]

    results: list[ExternalFidelity] = []
    for ipath in sorted(config.instances_dir.glob("*.json")):
        d = json.loads(ipath.read_text())
        fid = d.get("formulation_id")
        f = by_id.get(fid)
        if f is None:
            continue
        expected = d.get("expected_optimum")
        eps = config.optimum_tolerance

        outcomes: list[SolverOutcome] = []
        if solve is None or not solvers:
            outcomes.append(SolverOutcome("(none)", "solver_unavailable", None,
                                          "install the lp2graph 'solver' extra (pulp)"))
        else:
            inst = Instance(cardinalities=d["cardinalities"], parameters=d.get("parameters", {}))
            for name, backend in solvers:
                try:
                    r = solve(f, inst, solver=backend)  # type: ignore[misc]
                    outcomes.append(SolverOutcome(name, r.status, r.objective))
                except Exception as exc:  # noqa: BLE001
                    outcomes.append(SolverOutcome(name, "error", None, f"{type(exc).__name__}: {exc}"))

        objs = [o.objective for o in outcomes if o.objective is not None]
        cross_agree = len(objs) >= 2 and all(abs(o - objs[0]) <= eps for o in objs)
        if expected is None or not objs:
            matches: bool | None = None
        else:
            matches = all(abs(o - float(expected)) <= eps for o in objs)

        results.append(
            ExternalFidelity(
                formulation_id=fid,
                instance=ipath.name,
                expected_optimum=expected,
                solvers=tuple(outcomes),
                cross_solver_agree=cross_agree,
                matches_expected=matches,
            )
        )
    return results


# --------------------------------------------------------------------------- #
# Intra-cluster isomorphism (M6) + citation-anchored representatives (M5)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class IsomorphismEntry:
    cluster_name: str
    size: int
    pairwise_rate: float
    whole_cluster_rate: float


@dataclass(frozen=True, slots=True)
class RepresentativeEntry:
    cluster_name: str
    chosen_formulation_id: str | None
    reason: str
    has_instance: bool


@dataclass(frozen=True, slots=True)
class ValidationReport:
    solvers_used: tuple[str, ...]
    structural: tuple[StructuralFidelity, ...]
    external: tuple[ExternalFidelity, ...]
    isomorphism: tuple[IsomorphismEntry, ...]
    representatives: tuple[RepresentativeEntry, ...]
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def structural_pass_rate(self) -> float:
        if not self.structural:
            return 0.0
        return sum(1 for s in self.structural if s.round_trip_ok) / len(self.structural)


def _isomorphism(formulations: list[Formulation], tax: Taxonomy) -> list[IsomorphismEntry]:
    mc = tax.level_m.clustering
    clusters = clusters_from_labels(formulations, list(mc.labels), mc.names)
    report = isomorphism_report(clusters)
    return [
        IsomorphismEntry(name, ci.size, round(ci.pairwise_rate, 4), round(ci.whole_cluster_rate, 4))
        for name, ci in sorted(report.items())
    ]


def _representatives(
    corpus: LoadedCorpus, tax: Taxonomy, external: list[ExternalFidelity]
) -> list[RepresentativeEntry]:
    """Highest-cited formulation per Level-M cluster (M5 selection)."""
    formulations = list(corpus.formulations)
    mc = tax.level_m.clustering
    # cluster name -> indices into the formulation list
    clusters: dict[str, list[int]] = {}
    for idx, cid in enumerate(mc.labels):
        clusters.setdefault(mc.names.get(cid, f"cluster_{cid}"), []).append(idx)

    choices = corpus.manager.representatives(clusters)
    fids_with_instance = {e.formulation_id for e in external}
    entries: list[RepresentativeEntry] = []
    for name, choice in sorted(choices.items()):
        idx = choice.chosen_index
        fid = formulations[idx].id if idx is not None else None
        entries.append(
            RepresentativeEntry(
                cluster_name=name,
                chosen_formulation_id=fid,
                reason=str(choice.reason),
                has_instance=fid in fids_with_instance,
            )
        )
    return entries


def run_validation(
    corpus: LoadedCorpus, tax: Taxonomy, config: PipelineConfig | None = None
) -> ValidationReport:
    """Run all fidelity checks and assemble the report."""
    config = config or PipelineConfig()
    formulations = list(corpus.formulations)

    structural = structural_fidelity(formulations)
    external = external_fidelity(corpus, config)
    iso = _isomorphism(formulations, tax)
    reps = _representatives(corpus, tax, external)

    solvers_used = tuple(name for name, _ in _available_solvers())
    notes: list[str] = []
    if "Gurobi" not in solvers_used:
        notes.append("Gurobi not available here; cross-solver check ran on " + ", ".join(solvers_used) + ".")
    return ValidationReport(
        solvers_used=solvers_used,
        structural=tuple(structural),
        external=tuple(external),
        isomorphism=tuple(iso),
        representatives=tuple(reps),
        notes=tuple(notes),
    )


__all__ = [
    "ExternalFidelity",
    "IsomorphismEntry",
    "RepresentativeEntry",
    "SolverOutcome",
    "StructuralFidelity",
    "ValidationReport",
    "external_fidelity",
    "run_validation",
    "structural_fidelity",
]
