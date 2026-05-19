"""Experiment runners: MILP generation and LP2Graph parsing.

Both runners are single-pass and stream results to a CSV as they go.  No
feedback / retry-on-content loop is used -- the only retries are for
transient API errors.
"""

import asyncio
import time

import pandas as pd
from pydantic_ai import Agent
from tqdm.auto import tqdm

from railpminer.config import (
    MODELS_WITHOUT_TEMPERATURE,
    get_model,
    get_problem,
)
from railpminer.models.agents import agent_builder
from railpminer.models.schema import Model

DEFAULT_GRAPHING_TASK = (
    "Extract every constraint, every variable and the single objective "
    "function from the MILP text below. Return them in the predefined "
    "structured form, with the objective function numbered 0. For each "
    "equation list the numbers of the variables that appear in it. "
    "Do not invent content that is not in the text. MILP text:\n"
)


async def run_agent_experiments(
    design_df, num_runs=None, results_file="generation_results.csv"
):
    """Run MILP-generation agents over a factorial design.

    Args:
        design_df: Output of :func:`experiments.permutations.build_factorial_design`
            (one row per run) or a plain permutation grid (then ``num_runs``
            replications are applied).
        num_runs: Replications per row when ``design_df`` is a plain grid.
            Ignored when the design already has one row per run.
        results_file: CSV path; rows are appended as they complete.

    Returns:
        DataFrame with all results.
    """
    per_row_runs = 1 if "replicate" in design_df.columns else (num_runs or 1)
    results = []
    header_written = False

    for idx, row in tqdm(design_df.iterrows(), total=len(design_df)):
        params = row.to_dict()
        problem_key = params.get("problem", params.get("paper", ""))
        problem_content = get_problem(problem_key)
        temperature_supported = params["model"] not in MODELS_WITHOUT_TEMPERATURE

        builder_args = {
            k: params[k]
            for k in ("workflow", "model", "temperature")
            if k in params
        }
        builder_args["problem"] = problem_key
        agent = agent_builder(**builder_args)

        for run_id in range(per_row_runs):
            start = time.time()
            result = await agent.run(f"\n{problem_content}")
            runtime = time.time() - start

            record = {
                "run_id": run_id,
                "runtime": runtime,
                "permutation_id": idx,
                "answer": result.output,
                "usage": result.usage(),
                "temperature_supported": temperature_supported,
                **params,
            }
            results.append(record)

            row_df = pd.DataFrame([record])
            row_df.to_csv(
                results_file,
                mode="w" if not header_written else "a",
                header=not header_written,
                index=False,
            )
            header_written = True

    return pd.DataFrame(results)


async def model_graphing(answer, model="deepseek_v3", temperature=0.0, task=None):
    """Parse one MILP text into the structured :class:`Model` via an LLM.

    This is the *LLM parser*.  It is kept for completeness, but the primary
    parser used in the pipeline is the deterministic
    :func:`railpminer.analysis.graph_parser.parse_structured_text`, and the
    two are cross-checked (see ``graph_parser.parser_agreement``) so that the
    error introduced by this second LLM layer is quantified rather than
    hidden.
    """
    agent = Agent(get_model(model), output_type=Model, retries=5,
                  temperature=temperature)
    result = await agent.run(f"{task or DEFAULT_GRAPHING_TASK}{answer}")
    return result


async def run_graphing_agent(
    df, model="deepseek_v3", max_retries=3, retry_delay=10,
    results_file="graphing_results.csv",
):
    """Run the LLM graphing parser over every ``answer`` in ``df``.

    Retries only on transient API errors (no content feedback loop).
    """
    out = []
    header_written = False

    for idx, row in tqdm(df.iterrows(), total=len(df)):
        answer = row["answer"]
        result, attempt, ok = None, 0, False
        start = time.time()
        while not ok and attempt <= max_retries:
            try:
                result = await model_graphing(answer, model=model)
                ok = True
            except Exception as e:  # transient API failure
                attempt += 1
                print(f"row {idx} attempt {attempt} failed: {e}")
                if attempt <= max_retries:
                    await asyncio.sleep(retry_delay)
        runtime = time.time() - start

        record = {
            **row.to_dict(),
            "graph_runtime": runtime,
            "graph": result.output if ok else "ERROR",
            "graph_error": None if ok else "API failed after retries",
        }
        out.append(record)
        row_df = pd.DataFrame([record])
        row_df.to_csv(
            results_file,
            mode="w" if not header_written else "a",
            header=not header_written,
            index=False,
        )
        header_written = True

    return pd.DataFrame(out)
