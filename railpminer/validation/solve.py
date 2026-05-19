"""Execute generated solver code and record what actually happens.

For each (MILP, instance) pair we record a :class:`SolverReport`:

==============  ==============================================================
``code_parses`` the generated source compiled and defined ``build_model``
``builds``      ``build_model(inst)`` returned a ``pulp.LpProblem``
``is_linear``   building did not raise PuLP's non-linear-expression error
``status``      PuLP status string (Optimal / Infeasible / Unbounded / ...)
``feasible``    a feasible solution exists (status not Infeasible/Undefined)
``optimal``     solved to proven optimality
``runtime_s``   wall-clock solve time
``objective``   objective value when optimal
``error``       first exception message, if any
==============  ==============================================================

.. note::
   The generated code is executed in this isolated container.  It is
   LLM-produced; running it is the point of "validation per solving".  Run
   this only in a sandboxed environment.
"""

import time
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import pulp


@dataclass
class SolverReport:
    instance: str
    code_parses: bool = False
    builds: bool = False
    is_linear: bool = True
    status: str = "NotAttempted"
    feasible: bool = False
    optimal: bool = False
    runtime_s: float = 0.0
    objective: Optional[float] = None
    error: Optional[str] = None
    schedule: Dict = field(default_factory=dict)

    def as_row(self):
        d = asdict(self)
        d.pop("schedule", None)
        return d


_NONLINEAR_MARKERS = (
    "Non-constant", "non-constant", "cannot be multiplied",
    "unsupported operand", "Non-linear",
)


def compile_build_model(code: str):
    """Compile generated source and return its ``build_model`` callable.

    Raises on syntax error or if ``build_model`` is not defined.
    """
    namespace: Dict = {"pulp": pulp}
    exec(compile(code, "<generated_build_model>", "exec"), namespace)  # noqa: S102
    fn = namespace.get("build_model")
    if not callable(fn):
        raise ValueError("generated code does not define build_model(inst)")
    return fn


def _read_schedule(sched) -> Dict:
    out = {}
    if not isinstance(sched, dict):
        return out
    for key, val in sched.items():
        try:
            arr = val.get("arr")
            dep = val.get("dep")
            out[key] = {
                "arr": None if arr is None else arr.value(),
                "dep": None if dep is None else dep.value(),
            }
        except Exception:
            continue
    return out


def solve_on_instance(code: str, inst: Dict, time_limit=10) -> SolverReport:
    """Compile, build and solve one generated model on one instance."""
    rep = SolverReport(instance=inst.get("name", "?"))

    try:
        build_model = compile_build_model(code)
        rep.code_parses = True
    except Exception as e:
        rep.error = f"compile: {e}"
        return rep

    try:
        result = build_model(inst)
        prob = result[0] if isinstance(result, (tuple, list)) else result
        sched = result[1] if isinstance(result, (tuple, list)) and len(result) > 1 else {}
        if not isinstance(prob, pulp.LpProblem):
            rep.error = "build_model did not return a pulp.LpProblem"
            return rep
        rep.builds = True
    except Exception as e:
        msg = str(e)
        rep.error = f"build: {msg}"
        if any(m in msg for m in _NONLINEAR_MARKERS):
            rep.is_linear = False
        return rep

    try:
        solver = pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit)
        start = time.time()
        prob.solve(solver)
        rep.runtime_s = time.time() - start
        rep.status = pulp.LpStatus[prob.status]
        rep.optimal = prob.status == pulp.LpStatusOptimal
        rep.feasible = prob.status in (
            pulp.LpStatusOptimal, pulp.LpStatusNotSolved
        ) and prob.status != pulp.LpStatusInfeasible
        if rep.optimal:
            try:
                rep.objective = pulp.value(prob.objective)
            except Exception:
                rep.objective = None
            rep.schedule = _read_schedule(sched)
    except Exception as e:
        rep.error = f"solve: {e}"

    return rep


def solve_milp_code(code: str, instances: List[Dict], time_limit=10) -> List[SolverReport]:
    """Solve one generated model on every benchmark instance."""
    return [solve_on_instance(code, inst, time_limit) for inst in instances]
