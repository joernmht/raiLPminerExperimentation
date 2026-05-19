"""Single-pass code generation: MILP text -> runnable PuLP model.

To *solve* a generated formulation it must become executable solver code.
A single LLM call transcribes the textual MILP into a Python function
``build_model(inst)`` against the fixed instance schema in
:mod:`validation.instances`.  There is **no feedback / repair loop**: the
code is generated once and then either runs or does not -- the failure modes
(non-linear expression, build error, infeasibility) are exactly the signal
we want to measure against the early graph screen.

The default code model is an open-weight model.  For offline / dry runs,
pass a stub model object as ``model``.
"""

from pydantic_ai import Agent

from railpminer.config import get_model

CODEGEN_SYSTEMPROMPT = """\
You convert a textual MILP for railway rescheduling into ONE Python function.

Return ONLY Python code (no markdown fences, no prose) defining exactly:

    def build_model(inst):
        ...
        return prob, sched

Requirements:
- Use the `pulp` library (already imported as `pulp`).
- `inst` is a dict with keys: trains (list), stations (ordered list),
  route (dict train->list of station indices, in stop order),
  planned_departure (dict train->minute), min_run (dict (i,j)->minutes for
  consecutive station indices), min_dwell (dict station->minutes),
  headway (number), station_capacity (dict station->int),
  weight (dict train->float), horizon (number).
- `prob` is a pulp.LpProblem with the objective and all constraints added.
- `sched` is a dict mapping (train, station) -> {"arr": v, "dep": v} where
  v are the pulp arrival/departure time variables you created, for every
  (train, station) on that train's route. This lets the schedule be read
  back uniformly.
- Keep every expression strictly linear (no var*var, no abs(), no min/max).
- Implement the objective and constraints as described in the MILP text;
  if the text omits a needed quantity, use the matching `inst` value.
"""


def generate_solver_code_prompt(milp_text: str) -> str:
    return (
        "Transcribe the following MILP into the build_model(inst) function "
        "exactly as specified. MILP text:\n\n" + str(milp_text)
    )


async def generate_solver_code(milp_text: str, model="deepseek_v3") -> str:
    """Generate ``build_model`` source code for one MILP (single pass)."""
    agent = Agent(
        get_model(model),
        system_prompt=CODEGEN_SYSTEMPROMPT,
        output_type=str,
        temperature=0.0,
    )
    result = await agent.run(generate_solver_code_prompt(milp_text))
    return _strip_fences(result.output)


def _strip_fences(code: str) -> str:
    """Remove ``` fences if the model added them despite instructions."""
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()
