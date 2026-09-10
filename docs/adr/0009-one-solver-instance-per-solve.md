# 9. One solver instance per solve, exact gap, relative tolerance

- Status: accepted
- Date: 2026-08-11
- Deciders: Jörn Maurischat

## Context and problem statement

The paper's **external-fidelity** claim is that a mined LP2Graph model, grounded
at published instance data, reproduces the optimum printed in the source paper —
and that solving it with *several independent solvers* rules out solver-specific
artefacts. `railpminer/validation.py` implements this by solving every instance
with every solver `pulp` can drive here (CBC, HiGHS, Gurobi).

The suite was red on `main`: two of five instances "missed their published
optimum". The reported evidence looked damning —

    mip_2_1_big_m / bigm_ordering_4   expected 30   CBC 30.0   HiGHS 30.0   Gurobi 1.0
    lp_1_1_fixed_sequence / …_4       expected 18   CBC 18.0   HiGHS 18.0   Gurobi 1.0

— and would have been read as a representation-fidelity failure. It was not. The
harness was manufacturing exactly the artefact the cross-solver design exists to
exclude.

`_available_solvers()` built each backend **once** and reused the object across
all instances. The file-based backends (CBC, HiGHS) are stateless — they write an
LP file per call — so they were unaffected. `pulp`'s native `GUROBI` backend is
not:

- `initGurobi()` returns immediately if `self.init_gurobi` is set, so a single
  `gurobipy.Model` is created on the *solver* and never replaced;
- `buildSolverModel(lp)` then does `lp.solverModel = self.model` and **adds** the
  new problem's variables and constraints to that already-populated model, so
  problem *k* is solved against the union of problems *1…k*;
- `findSolutionValues(lp)` reads results back by zipping `lp._variables` against
  `model.getVars()` — all of them, in insertion order — so the new problem's
  variables receive the *old* problem's values, and `pulp` then computes the
  objective from those.

Status comes back `optimal`. Nothing raises. Solving `mip_2_1_big_m` with a fresh
`pulp.GUROBI` object returns 30.0; solving it with an object that has already
seen `assignment` returns 1.0.

A second, latent defect sat in the same comparison. The optimum check used a
**1e-6 absolute** tolerance while no backend was told to close the MIP gap.
Gurobi's default `MIPGap` is 1e-4 *relative*, so a legitimately-returned
incumbent may differ from the true optimum by 1e-4 × objective. On the seed
corpus (optima 1, 4, 13, 18, 30) that is invisible. On a real railway
formulation — total delay in train-minutes, easily 1e5–1e7 — it guarantees false
fidelity failures, and no floating-point solver can hit 1e-6 absolute at that
magnitude anyway.

## Decision

1. **`_available_solvers()` returns factories, not instances.** `external_fidelity`
   calls `make_backend()` immediately before each `solve` and `_close(backend)`
   in a `finally`. A `pulp` solver object is treated as single-use.
2. **`_close()`** calls the backend's `close()` if it has one, suppressing
   failures. The API backends own a licence environment; building one per solve
   without closing it leaks one `gurobipy.Env` per model over a full corpus run.
3. **`gapRel=0`** is passed to every backend that accepts it. This is a fidelity
   check against an exact published value; accepting a near-optimal incumbent
   makes the comparison meaningless.
4. **The tolerance is absolute-or-relative** (`_within`): `|v − t| ≤ eps ·
   max(1, |t|)`. Below 1.0 it is the old strict absolute test; above it, `eps`
   becomes a relative precision, so the check scales to real objectives.

## Consequences

- The suite is green: all five instances match their published optima and all
  three solvers agree. The two "fidelity failures" were harness bugs, and no
  claim about the LP2Graph representation needed to change.
- One `gurobipy.Model`/`Env` is built and torn down per (instance, solver). At
  corpus scale that is more overhead than reuse — accepted deliberately: a
  correct number is worth more than a fast one, and the file-based backends
  (which dominate the run) were already paying per-call cost.
- `cross_solver_agree` now also uses the relative test, so agreement is judged at
  the same precision as the optimum match.
- **The hazard is not local to this repo.** `lp2graph.solve.solve()` accepts any
  pre-built `pulp.LpSolver`, so any caller that hoists a `pulp.GUROBI` object out
  of a loop hits this. Worth a warning in lp2graph's `solve/` docstring —
  tracked in the quality backlog, not done here.
- A regression test (`test_solver_instances_are_never_reused_across_solves`)
  asserts on object identity rather than on numbers, because the failure mode
  returns a plausible `optimal` result: no value-based assertion can distinguish
  it from a genuine fidelity miss. Mutation-verified — restoring the hoisted
  construction makes it fail.
