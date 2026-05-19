"""raiLPminer -- open-LLM MILP generation with solver-validated graph screening.

The framework generates MILP formulations for railway rescheduling from
*problem descriptions* using open-weight LLMs, screens them with a cheap
solver-free graph depiction, and validates them by actually solving them on
benchmark instances -- so the graph screen's predictive value can be
measured rather than assumed.

Quick usage::

    from railpminer.config import get_model, get_problem, register_problem
    from railpminer.experiments import build_factorial_design
    from railpminer.analysis import compute_graph_screen
    from railpminer.validation import validate_dataframe
"""

from railpminer.config import (
    get_model,
    get_paper,
    get_problem,
    register_paper,
    register_problem,
)
