"""Offline end-to-end dry run -- no network, no API keys.

Exercises the full revised pipeline on the four mock MILPs:

    parse (deterministic)  ->  graph screen  ->  solver validation
                                            ->  hypothesis analysis

and writes ``examples/dry_run_results.csv``.  This proves the
validation-per-solving pipeline works before any paid LLM run.

Run::

    python examples/dry_run.py
"""

import asyncio
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from examples.mock_milps import MOCK_MILPS  # noqa: E402
from railpminer.analysis.graph_parser import create_graph_columns  # noqa: E402
from railpminer.analysis.hypothesis import (  # noqa: E402
    evaluate_screen_against_solver,
    screen_confusion,
)
from railpminer.analysis.screen import compute_graph_screen  # noqa: E402
from railpminer.validation.pipeline import validate_dataframe  # noqa: E402


def main():
    df = pd.DataFrame(MOCK_MILPS)

    # 1. Deterministic parse -> bipartite graph (no LLM).
    df = create_graph_columns(df, column_name="answer")

    # 2. Cheap solver-free graph screen.
    df = compute_graph_screen(df)

    # 3. Validation per solving (offline: use the pre-written code column).
    df = asyncio.run(
        validate_dataframe(df, code_column="solver_code", time_limit=10)
    )

    cols = [
        "case", "graph_parse_ok", "graph_complete", "graph_coherent",
        "graph_linear", "graph_predicts_valid",
        "solver_is_linear", "solver_optimal_all",
        "solver_railway_safe_all", "solver_valid",
    ]
    print("\n=== Per-case: cheap graph screen vs solver ground truth ===")
    print(df[cols].to_string(index=False))

    print("\n=== Hypothesis: does the graph catch flaws early? ===")
    conf = screen_confusion(df)
    for k, v in conf.items():
        print(f"  {k:>20}: {v}")

    print("\n=== Per-signal early-catch breakdown ===")
    print(evaluate_screen_against_solver(df).to_string())

    out = Path(__file__).with_name("dry_run_results.csv")
    df.drop(columns=["nodes", "connections", "solver_reports",
                     "solver_safety"], errors="ignore").to_csv(out, index=False)
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
