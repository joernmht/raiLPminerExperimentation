"""Experiment runners for model generation and graphing agents."""

import asyncio
import time

import pandas as pd
from pydantic_ai import Agent
from tqdm.notebook import tqdm

from railpminer.config import get_model, get_paper
from railpminer.models.agents import agent_builder
from railpminer.models.schema import Model


DEFAULT_GRAPHING_TASK = (
    "Find all constraints, variables and the objective function in the given "
    "test. Return these in the predefined form, with the objective function "
    "having the number 0. Also see, what variables are in the equations by "
    "number. This is your input:"
)


async def run_agent_experiments(permutation_df, num_runs=1):
    """Run agent experiments with different parameter permutations.

    Args:
        permutation_df: DataFrame containing parameter permutations.
                        Columns: ``model``, ``temperature``, ``workflow``, ``paper``.
        num_runs: Number of times to run each permutation.

    Returns:
        DataFrame with experiment results.
    """
    results_df = pd.DataFrame()

    for idx, row in tqdm(permutation_df.iterrows(), total=len(permutation_df)):
        params = row.to_dict()

        paper_content = get_paper(params.get('paper', ''))

        agent = agent_builder(**params)

        for run_id in range(num_runs):
            start_time = time.time()
            result = await agent.run(f"\n{paper_content}")
            runtime_seconds = time.time() - start_time

            result_dict = {
                'run_id': run_id,
                'runtime': runtime_seconds,
                'permutation_id': idx,
                'conversation': result.all_messages(),
                'answer': result.output,
                'usage': result.usage(),
                **params,
            }

            results_df = pd.concat(
                [results_df, pd.DataFrame([result_dict])], ignore_index=True
            )

            results_df.to_csv(
                'agent_experiment_results_test.csv',
                mode='a', index=False, header=False,
            )
            print(f'Run {run_id}')

    return results_df


async def model_graphing(
    paper, answer, model=None, temperature=0.2,
    output_type=None, task=None,
):
    """Run a single graphing agent to extract structured model components.

    Args:
        paper: Paper content (for context).
        answer: The LP model text to parse.
        model: Model name or object.  Defaults to ``"openai_o4_mini"``.
        temperature: Sampling temperature.
        output_type: Pydantic model for structured output.
        task: Task prompt prefix.

    Returns:
        Agent run result.
    """
    if model is None:
        model = get_model("openai_o4_mini")
    else:
        model = get_model(model)

    if output_type is None:
        output_type = Model

    if task is None:
        task = DEFAULT_GRAPHING_TASK

    model_graphing_agent = Agent(
        model,
        output_type=output_type,
        retries=10,
        temperature=temperature,
    )

    result = await model_graphing_agent.run(f'{task}{answer}')
    return result


async def run_graphing_agent_experiments(permutation_df, num_runs=1):
    """Run graphing agent experiments with different parameter permutations.

    Args:
        permutation_df: DataFrame containing parameter permutations.
        num_runs: Number of times to run each permutation.

    Returns:
        DataFrame with experiment results.
    """
    results_df = pd.DataFrame()
    timestamp = pd.Timestamp.now().strftime("%H%M")
    results_file = f'graphagent_results_{timestamp}.csv'
    header_written = False

    for idx, row in tqdm(permutation_df.iterrows(), total=len(permutation_df)):
        params = row.to_dict()
        runkey = params.pop('runkey', None)

        for run_id in range(num_runs):
            start_time = time.time()
            result = await model_graphing(**params)
            runtime_seconds = time.time() - start_time

            result_dict = {
                'run_id': run_id,
                'legacy_id': runkey,
                'runtime': runtime_seconds,
                'input': params['paper'],
                'permutation_id': idx,
                'answer': result.output,
                'usage': result.usage(),
                **params,
            }

            new_row_df = pd.DataFrame([result_dict])
            results_df = pd.concat(
                [results_df, new_row_df], ignore_index=True
            )

            if not header_written:
                new_row_df.to_csv(results_file, index=False, mode='w')
                header_written = True
            else:
                new_row_df.to_csv(
                    results_file, index=False, mode='a', header=False
                )

            print(f'Run {run_id}')

    return results_df


async def run_graphing_agent(
    permutation_df, num_runs=5, max_retries=3, retry_delay=10,
):
    """Run graphing agent experiments with retry logic.

    Args:
        permutation_df: DataFrame containing parameter permutations.
        num_runs: Number of times to run each permutation.
        max_retries: Maximum retry attempts for API calls.
        retry_delay: Delay in seconds between retry attempts.

    Returns:
        DataFrame with experiment results.
    """
    results_df = pd.DataFrame()
    timestamp = pd.Timestamp.now().strftime("%H%M")
    results_file = f'graphagent_results_{timestamp}.csv'
    header_written = False

    for idx, row in tqdm(permutation_df.iterrows(), total=len(permutation_df)):
        params = row.to_dict()
        runkey = params.pop('runkey', None)

        for run_id in range(num_runs):
            result = None
            retry_count = 0
            success = False
            runtime_seconds = -1

            while not success and retry_count <= max_retries:
                try:
                    start_time = time.time()
                    result = await model_graphing(**params)
                    runtime_seconds = time.time() - start_time
                    success = True
                except Exception as e:
                    retry_count += 1
                    print(
                        f"Error during run {run_id}, "
                        f"attempt {retry_count}: {e!s}"
                    )
                    if retry_count <= max_retries:
                        print(f"Retrying in {retry_delay} seconds...")
                        await asyncio.sleep(retry_delay)
                    else:
                        print(
                            f"Max retries reached for run {run_id}. "
                            "Storing error information and continuing."
                        )

            result_dict = {
                'run_id': run_id,
                'legacy_id': runkey,
                'runtime': runtime_seconds,
                'paper': params['paper'],
                'answer': params['answer'],
                'permutation_id': idx,
                'error': None if success else "API call failed after max retries",
                'graph': result.output if result else "ERROR",
                'usage': result.usage() if result else "ERROR",
                **params,
            }

            new_row_df = pd.DataFrame([result_dict])
            results_df = pd.concat(
                [results_df, new_row_df], ignore_index=True
            )

            try:
                if not header_written:
                    new_row_df.to_csv(results_file, index=False, mode='w')
                    header_written = True
                else:
                    new_row_df.to_csv(
                        results_file, index=False, mode='a', header=False
                    )
            except Exception as e:
                print(
                    f"Warning: Failed to write to CSV: {e!s}. "
                    "Will try again on next iteration."
                )

            print(
                f"Completed run {run_id} "
                f"{'successfully' if success else 'with errors'}"
            )

    return results_df


async def process_all_rows(dataframe):
    """Run model_graphing on every row's answer column.

    Args:
        dataframe: DataFrame with an ``answer`` column.

    Returns:
        List of model outputs.
    """
    results = []
    for opt_model in dataframe['answer']:
        thismodel = await model_graphing(paper=opt_model, answer=opt_model)
        results.append(thismodel.output)
        print(thismodel.output)
    return results
