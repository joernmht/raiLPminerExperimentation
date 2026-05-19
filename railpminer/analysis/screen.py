"""The early graph screen -- a cheap, solver-free verdict on a MILP.

This is the predictor in the central hypothesis:

    *A graph-based depiction of a MILP allows identifying flaws early in the
    development process.*

For every generated MILP we derive a handful of boolean signals purely from
its bipartite graph and equation text (no solver, no LLM):

==========================  ==================================================
``graph_parse_ok``          the strict layout parsed (objective + >=1 var +
                            >=1 constraint were found)
``graph_complete``          one objective, >=1 variable, >=1 constraint and
                            every variable wired into >=2 equations
``graph_coherent``          the bipartite graph is connected
``graph_linear``            no non-linear token / variable product in any
                            equation (:mod:`analysis.linearity`)
``graph_domain_covered``    at least one railway constraint category matched
                            (keyword screen; see :mod:`analysis.constraints`)
``graph_predicts_valid``    AND of the four core flags -- the cheap verdict
==========================  ==================================================

``graph_predicts_valid`` is later compared against the solver ground truth
to quantify how good an early screen the graph actually is (precision /
recall / false-accept rate).  No flag is claimed to *prove* validity.
"""

import pandas as pd

from railpminer.analysis.constraints import classify_constraint_types
from railpminer.analysis.linearity import is_structurally_linear
from railpminer.analysis.metrics import calculate_complexity_metrics


def screen_one(nodes, connections, parse_ok=None):
    """Return the early-screen flag dict for a single parsed MILP."""
    if parse_ok is None:
        n_obj = sum(n["type"] == "objective" for n in nodes)
        n_var = sum(n["type"] == "variable" for n in nodes)
        n_con = sum(n["type"] == "constraint" for n in nodes)
        parse_ok = n_obj >= 1 and n_var >= 1 and n_con >= 1

    if not nodes:
        return {
            "graph_parse_ok": False, "graph_complete": False,
            "graph_coherent": False, "graph_linear": False,
            "graph_domain_covered": False, "graph_flag_count": 4,
            "graph_predicts_valid": False,
        }

    metrics = calculate_complexity_metrics(nodes, connections)
    linear, _ = is_structurally_linear(nodes)
    domain = classify_constraint_types(nodes)
    domain_covered = domain["classified_constraints"] > 0

    complete = bool(metrics["model_completeness"])
    coherent = bool(metrics["model_coherence"])

    core = [bool(parse_ok), complete, coherent, linear]
    flags = {
        "graph_parse_ok": bool(parse_ok),
        "graph_complete": complete,
        "graph_coherent": coherent,
        "graph_linear": linear,
        "graph_domain_covered": domain_covered,
        "graph_flag_count": sum(1 for c in core if not c),
        "graph_predicts_valid": all(core),
    }
    return flags


def compute_graph_screen(df: pd.DataFrame) -> pd.DataFrame:
    """Add the early-screen flag columns to a DataFrame.

    Requires ``nodes`` / ``connections`` columns (run
    :func:`analysis.graph_parser.create_graph_columns` first).  Uses the
    optional ``parse_ok`` column when present.
    """
    df = df.copy()
    rows = []
    for idx in df.index:
        nodes = df.at[idx, "nodes"]
        conns = df.at[idx, "connections"]
        parse_ok = df.at[idx, "parse_ok"] if "parse_ok" in df.columns else None
        rows.append(screen_one(nodes or [], conns or [], parse_ok))
    return pd.concat([df, pd.DataFrame(rows, index=df.index)], axis=1)
