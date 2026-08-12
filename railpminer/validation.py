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

import contextlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field

from lp2graph.codec import canonical_normal_form, from_canonical_latex, to_canonical_latex
from lp2graph.core.model import Formulation
from lp2graph.mining.cluster import Taxonomy
from lp2graph.mining.isomorphism import clusters_from_labels, isomorphism_report

from . import _lp2graph  # noqa: F401
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
        except Exception as exc:  # report, never drop
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
    #: One value per priority level, for lexicographic objectives (which have
    #: no single optimum). ``objective`` then carries the last level, so
    #: consumers that expect a scalar still see one.
    objectives: tuple[float, ...] | None = None


@dataclass(frozen=True, slots=True)
class ExternalFidelity:
    formulation_id: str
    instance: str
    #: A number, or one number per priority level for a lexicographic objective.
    expected_optimum: float | list[float] | None
    solvers: tuple[SolverOutcome, ...]
    cross_solver_agree: bool
    matches_expected: bool | None
    #: The status(es) the instance says solving it may report. Almost always
    #: ``"optimal"``; an instance may instead publish a set such as
    #: ``["unbounded", "infeasible"]`` when *that* is the fact being validated.
    #: A set rather than one value because MILP presolve routinely cannot tell
    #: an unbounded model from an infeasible one and reports a combined
    #: verdict: on the same unbounded model CBC says ``unbounded`` while HiGHS
    #: and Gurobi both say ``infeasible``. Demanding one spelling would record
    #: a solver-dialect difference as a fidelity failure.
    expected_status: tuple[str, ...] = ("optimal",)
    matches_status: bool | None = None


def _available_solvers() -> list[tuple[str, Callable[[], object]]]:
    """Independent solvers pulp can drive here, in a fixed order.

    Returns **factories**, not instances. A ``pulp`` solver object must not be
    reused across problems: the native-API back-ends are stateful. ``pulp``'s
    ``GUROBI`` keeps one ``gurobipy.Model`` on the *solver* (``initGurobi`` is a
    no-op after the first call, and ``buildSolverModel`` then assigns that same
    model to the next problem), so a second problem is appended to the first —
    it is solved against the union of both constraint sets, and the solution is
    read back by zipping the new problem's variables against *all* of the
    model's variables. The result is a wrong objective reported as ``optimal``.
    One fresh instance per solve is the only safe contract.

    ``gapRel=0`` is set where the back-end supports it: this is a *fidelity*
    check against a published optimum, so a default relative MIP gap (Gurobi's
    is 1e-4) would let a merely near-optimal incumbent be compared against the
    exact published value.
    """
    try:
        import pulp
    except ModuleNotFoundError:
        return []
    names = pulp.listSolvers(onlyAvailable=True)
    solvers: list[tuple[str, Callable[[], object]]] = []
    if "PULP_CBC_CMD" in names:
        solvers.append(("CBC", lambda: pulp.PULP_CBC_CMD(msg=False, gapRel=0)))
    if "HiGHS" in names:
        solvers.append(("HiGHS", lambda: pulp.HiGHS(msg=False, gapRel=0)))
    if "GUROBI" in names:
        solvers.append(("Gurobi", lambda: pulp.GUROBI(msg=False, gapRel=0)))
    elif "GUROBI_CMD" in names:
        solvers.append(("Gurobi", lambda: pulp.GUROBI_CMD(msg=False, gapRel=0)))
    return solvers


def _close(backend: object) -> None:
    """Release a solver's native resources, if it holds any.

    The API back-ends own an environment/licence token (``pulp.GUROBI`` manages
    a ``gurobipy.Env``). Since we now build one per solve, they must also be
    closed per solve, or a long validation run leaks one environment per model.
    """
    close = getattr(backend, "close", None)
    if callable(close):
        with contextlib.suppress(Exception):  # cleanup must never fail the check
            close()


def _within(value: float, target: float, eps: float) -> bool:
    """Absolute-or-relative closeness, so ``eps`` scales with the objective.

    A published optimum of 3.7e6 minutes cannot be matched to 1e-6 *absolute*
    by any floating-point solver; a pure absolute test would report a fidelity
    failure that is really a rounding artefact of the objective's magnitude.
    """
    return abs(value - target) <= eps * max(1.0, abs(target))


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
        raw_status = d.get("expected_status", "optimal")
        expected_status = tuple(raw_status if isinstance(raw_status, list) else [raw_status])
        eps = config.optimum_tolerance
        # A lexicographic objective has no single optimum: each priority level
        # is optimized in turn, subject to the levels above it holding. It needs
        # the staged entry point, and its "optimum" is the vector of levels.
        lex = f.objective is not None and f.objective.combination == "lexicographic"

        outcomes: list[SolverOutcome] = []
        if solve is None or not solvers:
            outcomes.append(
                SolverOutcome(
                    "(none)",
                    "solver_unavailable",
                    None,
                    "install the lp2graph 'solver' extra (pulp)",
                )
            )
        else:
            inst = Instance(cardinalities=d["cardinalities"], parameters=d.get("parameters", {}))
            for name, make_backend in solvers:
                backend = make_backend()  # one per solve — see _available_solvers
                try:
                    outcomes.append(_run_one(f, inst, name, backend, lex=lex))
                except Exception as exc:  # report, never drop
                    outcomes.append(
                        SolverOutcome(name, "error", None, f"{type(exc).__name__}: {exc}")
                    )
                finally:
                    _close(backend)

        statuses = [o.status for o in outcomes]
        matches_status = bool(statuses) and all(s in expected_status for s in statuses)

        if expected_status != ("optimal",):
            # Nothing solved to optimality by design, so the reported objective
            # is meaningless and agreement has to be about the verdict instead.
            # Agreement means every solver placed the model in the published
            # verdict class, not that they spelled it identically: see the
            # unbounded-vs-infeasible note on ExternalFidelity.expected_status.
            cross_agree = len(statuses) >= 2 and all(s in expected_status for s in statuses)
            matches: bool | None = None
        else:
            vecs = [v for v in (_levels(o) for o in outcomes) if v is not None]
            cross_agree = len(vecs) >= 2 and all(_vec_within(v, vecs[0], eps) for v in vecs)
            if expected is None or not vecs:
                matches = None
            else:
                spec = expected if isinstance(expected, list) else [expected]
                want = tuple(float(x) for x in spec)
                matches = all(_vec_within(v, want, eps) for v in vecs)

        results.append(
            ExternalFidelity(
                formulation_id=fid,
                instance=ipath.name,
                expected_optimum=expected,
                solvers=tuple(outcomes),
                cross_solver_agree=cross_agree,
                matches_expected=matches,
                expected_status=expected_status,
                matches_status=matches_status if outcomes else None,
            )
        )
    return results


def _run_one(f, inst, name: str, backend: object, *, lex: bool) -> SolverOutcome:
    """One solve, through the entry point the objective's combination requires."""
    if lex:
        from lp2graph.solve import solve_lexicographic

        r = solve_lexicographic(f, inst, solver=backend)  # type: ignore[arg-type]
        last = r.objectives[-1] if r.objectives else None
        return SolverOutcome(name, r.status, last, objectives=tuple(r.objectives))

    from lp2graph.solve import solve

    r = solve(f, inst, solver=backend)  # type: ignore[arg-type]
    return SolverOutcome(name, r.status, r.objective)


def _levels(o: SolverOutcome) -> tuple[float, ...] | None:
    """One outcome's objective value(s) as a level vector, or None if it has no
    usable value (an error, or a status that carries no objective)."""
    if o.objectives is not None:
        return o.objectives
    return None if o.objective is None else (o.objective,)


def _vec_within(value: tuple[float, ...], target: tuple[float, ...], eps: float) -> bool:
    """Elementwise :func:`_within`, so a lexicographic level vector is compared
    level by level and a length mismatch is a failure rather than a crash."""
    if len(value) != len(target):
        return False
    return all(_within(a, b, eps) for a, b in zip(value, target, strict=True))


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
        notes.append(
            "Gurobi not available here; cross-solver check ran on " + ", ".join(solvers_used) + "."
        )
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
