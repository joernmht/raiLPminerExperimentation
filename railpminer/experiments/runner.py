"""Generation runner (single pass, temperature 0).

There is no LLM parsing step any more: classification is embedded by
generation and the bipartite graph is recovered deterministically
(:mod:`railpminer.analysis.graph_parser`).  This removes the previously
criticised "second LLM layer" entirely.
"""

import time

import pandas as pd
from tqdm.auto import tqdm

from railpminer.config import MODELS_WITHOUT_TEMPERATURE, get_problem
from railpminer.models.agents import agent_builder


async def run_agent_experiments(
    design_df, num_runs=None, results_file="generation_results.csv"
):
    """Run generation agents over a balanced factorial design.

    Args:
        design_df: output of ``experiments.build_factorial_design`` (one row
            per run) or a plain grid (then ``num_runs`` replications apply).
        num_runs: replications per row for a plain grid (ignored when the
            design already has one row per run).
        results_file: CSV path; rows appended as they complete.

    Returns:
        DataFrame with all results (one row per run, ``answer`` column holds
        the classified MILP text, or PuLP code for the DC baseline).
    """
    per_row = 1 if "replicate" in design_df.columns else (num_runs or 1)
    results, header_written = [], False

    for idx, row in tqdm(design_df.iterrows(), total=len(design_df)):
        params = row.to_dict()
        problem_key = params.get("problem", params.get("paper", ""))
        problem_content = get_problem(problem_key)
        temp_supported = params["model"] not in MODELS_WITHOUT_TEMPERATURE

        agent = agent_builder(
            workflow=params["workflow"],
            model=params["model"],
            temperature=params.get("temperature", 0.0),
            problem=problem_key,
        )

        for run_id in range(per_row):
            start = time.time()
            result = await agent.run(f"\n{problem_content}")
            runtime = time.time() - start

            record = {
                "run_id": run_id,
                "runtime": runtime,
                "permutation_id": idx,
                "answer": result.output,
                "usage": result.usage(),
                "temperature_supported": temp_supported,
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
