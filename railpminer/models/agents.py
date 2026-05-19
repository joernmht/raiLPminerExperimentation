"""Agentic workflow builders for MILP generation.

The agentic design is deliberately kept minimal: the research question is
about the *graph-based screening* of generated MILPs, not about prompt
engineering.  There are therefore only two single-pass workflows and **no
feedback / self-review loops** (the previous Operator-Evaluator and
Parallel-Select workflows were removed):

    ZS  -- Zero-Shot: one prompt, one answer.
    CFC -- Code-First-Convert: write solver code first, then transcribe the
           equations.  Still a single forward pass (no iteration).

Both take a *problem description* (operational setting, decisions, objective
and rules) as input -- not a paper abstract.
"""

from pydantic_ai import Agent, RunContext

from railpminer.config import get_model


GENERAL_SYSTEMPROMPT = (
    "You are given a railway rescheduling problem description (operational "
    "setting, decisions, objective and operational rules). "
    "Produce a complete Mixed-Integer Linear Programming (MILP) formulation "
    "and return it in EXACTLY the following layout, nothing before or after:\n"
    "VARIABLES\n"
    "v1: <symbol> -- <domain> -- <short description>\n"
    "v2: <symbol> -- <domain> -- <short description>\n"
    "OBJECTIVE\n"
    "min: <linear equation in the variable symbols> -- <short description>\n"
    "CONSTRAINTS\n"
    "c1: <linear (in)equality in the variable symbols> -- <description>\n"
    "c2: ...\n\n"
    "Rules: use the exact section headers VARIABLES, OBJECTIVE, CONSTRAINTS; "
    "one item per line; every variable has a unique symbol that is reused "
    "verbatim inside the equations; the objective line starts with 'min:' or "
    "'max:'. Keep the formulation strictly linear (no products of variables, "
    "no absolute values, no min/max operators). Stay close to the problem "
    "description; if a detail is missing make a reasonable assumption and say "
    "so in the relevant description. Do not ask questions, do not add prose."
)

#: Supported workflow keys (kept here so experiment code can iterate them).
WORKFLOWS = ("ZS", "CFC")


def agent_builder(workflow, model, temperature, problem=None, paper=None):
    """Build a pydantic-ai Agent for the given workflow.

    Args:
        workflow: ``"ZS"`` or ``"CFC"``.
        model: Model key in the registry or a model object (e.g. a stub).
        temperature: Sampling temperature.
        problem: Problem-description content.  Unused here (passed to
            ``agent.run`` by the runner); accepted for grid compatibility.
        paper: Legacy alias for ``problem``.

    Returns:
        A configured :class:`pydantic_ai.Agent`.
    """
    model = get_model(model)

    if workflow == "ZS":
        return Agent(
            model,
            system_prompt=GENERAL_SYSTEMPROMPT,
            temperature=temperature,
        )

    if workflow == "CFC":
        agent = Agent(
            model,
            system_prompt=GENERAL_SYSTEMPROMPT
            + (
                " First call the `or_coder` tool to draft solver code for the "
                "model, then transcribe it back into plain objective / "
                "variable / constraint equations. Return only the equations, "
                "no code."
            ),
            temperature=temperature,
        )

        code_agent = Agent(model, temperature=temperature)

        @agent.tool
        async def or_coder(ctx: RunContext[None], description: str) -> str:
            """Draft PuLP/GAMS code for the described model (single pass)."""
            r = await code_agent.run(
                "Generate optimization-model code (Python/PuLP or GAMS) for "
                f"the following description.\n{description}",
                usage=ctx.usage,
            )
            return r.output

        return agent

    raise ValueError(f"Unknown workflow {workflow!r}; expected one of {WORKFLOWS}")
