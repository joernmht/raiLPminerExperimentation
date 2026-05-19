"""Workflow builders.

The study is no longer about creativity/diversity: temperature is 0 and the
point is *graphing as a means of designing MILPs*.  Three workflows, no
feedback loops:

    SIM  -- Simultaneous: one call emits the fully classified MILP.
    TAF  -- Text-Analysis-First: call 1 classifies the problem text into a
            domain blueprint; call 2 develops the classified MILP from it
            (two sequential calls via a tool, no iteration).
    DC   -- Direct-Code baseline: one call emits runnable PuLP code *plus*
            an embedded CLASSIFICATION dict, so it can be solved and
            reverse-graphed for a like-for-like comparison.

Classification is embedded in the output (drawn from the taxonomy) so the
parser reads it deterministically -- no a-posteriori keyword matching.
"""

from pydantic_ai import Agent, RunContext

from railpminer.analysis.taxonomy import prompt_block
from railpminer.config import get_model
from railpminer.instance_contract import (
    BUILD_MODEL_CONTRACT,
    CLASSIFICATION_CONTRACT,
    INSTANCE_CONTRACT,
)

WORKFLOWS = ("SIM", "TAF", "DC")

_FORMAT = (
    "Return the formulation in EXACTLY this layout, nothing else, fields "
    "separated by ' | ', one item per line:\n"
    "PARAMETERS\n"
    "p1: <symbol> | <parameter class> | <description>\n"
    "VARIABLES\n"
    "v1: <symbol> | <variable class> | <domain> | <description>\n"
    "OBJECTIVE\n"
    "o: <min|max> | <objective class> | <linear equation> | <description>\n"
    "CONSTRAINTS\n"
    "c1: <constraint class> | <linear (in)equality> | <description>\n\n"
    "Every variable/parameter symbol is reused verbatim inside the "
    "equations. Keep everything strictly linear (no var*var, abs, min/max). "
    "Use ONLY these class vocabularies:\n" + prompt_block()
)

_SIM_SYS = (
    "You are given a railway rescheduling problem description. Produce a "
    "complete, classified MILP (parameters, variables, one objective, "
    "constraints). " + _FORMAT
)

_TAF_SYS = (
    "You design a railway rescheduling MILP in two steps. FIRST call the "
    "`analyze_problem` tool with the problem text to obtain a domain "
    "blueprint (which objective/variable/parameter/constraint classes the "
    "problem requires). THEN, guided by that blueprint, output the complete "
    "classified MILP. " + _FORMAT
)

_TAF_ANALYST_SYS = (
    "Classify the railway rescheduling problem text into a domain "
    "blueprint. List the required objective class, the variable classes, "
    "the parameter classes and the constraint classes the model will need, "
    "with a one-line justification each. Use ONLY these vocabularies:\n"
    + prompt_block()
)

_DC_SYS = (
    "You are given a railway rescheduling problem description. Output "
    "runnable Python (no markdown fences, no prose). " + BUILD_MODEL_CONTRACT
    + "\n" + INSTANCE_CONTRACT + "\n" + CLASSIFICATION_CONTRACT
    + "\nUse ONLY these class vocabularies:\n" + prompt_block()
)


def agent_builder(workflow, model, temperature=0.0, problem=None, paper=None):
    """Build the agent for a workflow (temperature defaults to 0)."""
    m = get_model(model)

    if workflow == "SIM":
        return Agent(m, system_prompt=_SIM_SYS, temperature=temperature)

    if workflow == "DC":
        return Agent(m, system_prompt=_DC_SYS, temperature=temperature)

    if workflow == "TAF":
        agent = Agent(m, system_prompt=_TAF_SYS, temperature=temperature)
        analyst = Agent(m, system_prompt=_TAF_ANALYST_SYS,
                        temperature=temperature)

        @agent.tool
        async def analyze_problem(ctx: RunContext[None], problem_text: str) -> str:
            """Classify the problem text into a domain blueprint."""
            r = await analyst.run(problem_text, usage=ctx.usage)
            return r.output

        return agent

    raise ValueError(f"Unknown workflow {workflow!r}; expected {WORKFLOWS}")
