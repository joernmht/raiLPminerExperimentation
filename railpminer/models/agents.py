"""Agentic workflow builders for LP model generation.

Workflows:
    ZS  — Zero-Shot
    CFC — Code-First-Convert
    OE  — Operator-Evaluator
    PS  — Parallel-Select
"""

from typing import List

from pydantic_ai import Agent, RunContext

from railpminer.config import get_model


GENERAL_SYSTEMPROMPT = (
    'Inpired by the provided input: '
    'Generate a complete, linear optimization model with variables, '
    'objective function and constraints. '
    'The optimization model should be described completely with equations '
    'and assumptions'
    'The input is inspiration, so keep close to it, if you can, and if '
    'information is missing, make reasonable assumptions and mark them as such.'
    'Do not ask questions'
)


def agent_builder(workflow, model, temperature, paper):
    """Build a pydantic-ai Agent for the given workflow type.

    Args:
        workflow: One of ``"ZS"``, ``"CFC"``, ``"OE"``, ``"PS"``.
        model: Model name (string key in MODEL_REGISTRY) or model object.
        temperature: Sampling temperature.
        paper: Paper content (unused here, passed to agent.run later).

    Returns:
        A configured :class:`pydantic_ai.Agent`.
    """
    model = get_model(model)

    match workflow:
        case "ZS":
            agent = Agent(
                model,
                system_prompt=GENERAL_SYSTEMPROMPT,
                temperature=temperature,
            )

        case "CFC":
            systemprompt = GENERAL_SYSTEMPROMPT + (
                'Use the following approach for your task:'
                'Use the `or_coder` to generate a code-based model based on '
                'the provided input.'
                'Interpret the code and just return the equations for objective '
                'functions, variables and constraints as text.'
                'Do not return any code.'
            )
            agent = Agent(
                model,
                system_prompt=systemprompt,
                temperature=temperature,
            )

            code_generation_agent = Agent(model, temperature=temperature)

            @agent.tool
            async def or_coder(ctx: RunContext[None], description: str) -> str:
                r = await code_generation_agent.run(
                    "Generate optimization model code based on the provided "
                    "description.\nArgs:\n  description: A description of the "
                    "optimization model to implement\n"
                    f"Please generate a the python or GAMS code to implement "
                    f"the provided model\n{description}",
                    usage=ctx.usage,
                )
                return r.output

        case "OE":
            systemprompt = GENERAL_SYSTEMPROMPT + (
                'Use the following approach for your task:'
                'Use the `or_operator` to generate an optimization model '
                'based on your input.'
                'Review the optimization model and feedback improvements to '
                'the `or_operator` and let it try again'
                'Just return the final model.'
            )
            agent = Agent(
                model,
                system_prompt=systemprompt,
                temperature=temperature,
            )

            singlemodel_generation_agent = Agent(
                model,
                system_prompt=(
                    "Based on your input, generate a complete, linear "
                    "optimization model with variables, objective function "
                    "and constraints. Explain the elements of the model."
                ),
                temperature=temperature,
            )

            @agent.tool
            async def or_operator(ctx: RunContext[None], description: str):
                r = await singlemodel_generation_agent.run(
                    f"Please generate an optimization model based on the "
                    f"provided description\n{description}",
                    usage=ctx.usage,
                )
                return r.output

        case "PS":
            systemprompt = GENERAL_SYSTEMPROMPT + (
                'Use the `or_factory` to generate a model based on the '
                'provided input, then choose the best. '
                'Just return the best model.'
            )
            agent = Agent(
                model,
                system_prompt=systemprompt,
                temperature=temperature,
            )

            model_generation_agent = Agent(
                model,
                system_prompt=(
                    "Based on your input, generate a complete, linear "
                    "optimization model with variables, objective function "
                    "and constraints. Explain the elements of the model."
                ),
                output_type=List[str],
                retries=10,
                temperature=temperature,
            )

            @agent.tool
            async def or_factory(
                ctx: RunContext[None], count: int, description: str
            ) -> List[str]:
                r = await model_generation_agent.run(
                    f"Please generate \n{count} optimization models based on "
                    f"the provided description\n{description}",
                    usage=ctx.usage,
                )
                return r.output

        case _:
            agent = None
            print("None known agent addressed.")

    return agent
