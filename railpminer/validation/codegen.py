"""Single-pass code generation: classified MILP text -> runnable PuLP.

Used by the graph-based workflows (``SIM`` / ``TAF``) to turn their
classified MILP text into executable ``build_model`` code so it can be
solved.  The ``DC`` baseline already emits code and skips this step.

No feedback / repair loop: the code is generated once; its failure modes are
exactly the signal measured against the early graph screen.
"""

from pydantic_ai import Agent

from railpminer.config import get_model
from railpminer.instance_contract import BUILD_MODEL_CONTRACT, INSTANCE_CONTRACT

CODEGEN_SYSTEMPROMPT = (
    "Convert the given textual MILP for railway rescheduling into ONE Python "
    "function. Return ONLY Python code (no markdown fences, no prose). "
    + BUILD_MODEL_CONTRACT + "\n" + INSTANCE_CONTRACT
    + "\nImplement the objective and constraints as written; if the text "
    "omits a needed quantity, use the matching `inst` value."
)


def _strip_fences(code: str) -> str:
    code = code.strip()
    if code.startswith("```"):
        lines = code.splitlines()[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines)
    return code.strip()


async def generate_solver_code(milp_text: str, model="deepseek_v3") -> str:
    """Generate ``build_model`` source for one classified MILP (single pass)."""
    agent = Agent(
        get_model(model),
        system_prompt=CODEGEN_SYSTEMPROMPT,
        output_type=str,
        temperature=0.0,
    )
    result = await agent.run(
        "Transcribe this MILP into build_model(inst):\n\n" + str(milp_text)
    )
    return _strip_fences(result.output)


def looks_like_code(answer: str) -> bool:
    """True if the answer is already runnable code (DC baseline)."""
    return "def build_model" in str(answer)
