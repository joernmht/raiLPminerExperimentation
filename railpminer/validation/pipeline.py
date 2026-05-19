"""Validation-per-solving pipeline -> the solver ground-truth label.

For each generated MILP this:

1. turns it into ``build_model`` code (single LLM pass, or a pre-supplied
   ``code_column`` for offline runs),
2. solves it on **every** benchmark instance,
3. runs the operational-safety checks on each solved schedule,
4. reduces everything to one strict ground-truth label, ``solver_valid``.

``solver_valid`` is True iff, for *every* benchmark instance, the model
compiles, builds a linear PuLP problem, solves to proven optimality, and the
resulting schedule passes all railway-safety checks.  This label is used
purely for analysis (it never feeds back into generation) -- it is the
"truth" against which the cheap graph screen is measured.
"""

from typing import Dict, List, Optional

import pandas as pd
from tqdm.auto import tqdm

from railpminer.validation.codegen import (
    _strip_fences,
    generate_solver_code,
    looks_like_code,
)
from railpminer.validation.instances import all_instances
from railpminer.validation.railway_checks import check_schedule
from railpminer.validation.solve import solve_milp_code


def _aggregate(reports, safety) -> Dict:
    parses = all(r.code_parses for r in reports)
    builds = all(r.builds for r in reports)
    linear = all(r.is_linear for r in reports)
    optimal = all(r.optimal for r in reports)
    runtimes = [r.runtime_s for r in reports if r.builds]

    applicable = [s["railway_safe"] for s in safety if s["railway_safe"] is not None]
    railway_safe = bool(applicable) and all(applicable)

    return {
        "solver_code_parses": parses,
        "solver_builds_all": builds,
        "solver_is_linear": linear,
        "solver_optimal_all": optimal,
        "solver_railway_safe_all": railway_safe,
        "solver_valid": parses and builds and linear and optimal and railway_safe,
        "solver_mean_runtime_s": (sum(runtimes) / len(runtimes)) if runtimes else 0.0,
        "solver_reports": [r.as_row() for r in reports],
        "solver_safety": safety,
    }


def validate_code(code: str, instances: Optional[List[Dict]] = None,
                   time_limit=10) -> Dict:
    """Solve one ``build_model`` source string across all instances."""
    instances = instances or all_instances()
    reports = solve_milp_code(code, instances, time_limit=time_limit)
    safety = [
        check_schedule(r.schedule, inst)
        for r, inst in zip(reports, instances)
    ]
    return _aggregate(reports, safety)


async def validate_dataframe(
    df: pd.DataFrame,
    instances: Optional[List[Dict]] = None,
    model="deepseek_v3",
    answer_column="answer",
    code_column=None,
    time_limit=10,
) -> pd.DataFrame:
    """Add solver ground-truth columns to ``df``.

    Args:
        df: rows with the generated MILP text in ``answer_column``.
        instances: benchmark instances (defaults to all built-in ones).
        model: code-generation model key (ignored when ``code_column`` set).
        answer_column: column holding the MILP text.
        code_column: if given, use this column's pre-generated
            ``build_model`` source instead of calling an LLM (offline runs).
        time_limit: per-solve CBC time limit (seconds).

    Returns:
        Copy of ``df`` with ``solver_*`` columns added.
    """
    instances = instances or all_instances()
    df = df.copy()
    rows = []
    for idx in tqdm(df.index, total=len(df)):
        answer = df.at[idx, answer_column]
        if code_column and pd.notna(df.at[idx, code_column]):
            code = df.at[idx, code_column]          # offline / precomputed
        elif looks_like_code(answer):
            code = _strip_fences(answer)            # DC baseline: already code
        else:
            code = await generate_solver_code(answer, model=model)  # SIM/TAF
        df.at[idx, "solver_code"] = code
        rows.append(validate_code(code, instances, time_limit=time_limit))

    agg = pd.DataFrame(rows, index=df.index)
    return pd.concat([df, agg], axis=1)
