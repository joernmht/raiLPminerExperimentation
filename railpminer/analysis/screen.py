"""The early graph screen -- a cheap, solver-free verdict on a MILP.

Hypothesis under test: *a graph-based depiction of a MILP allows identifying
flaws early in the development process*.

Signals are derived purely from the parsed, **classified** bipartite graph
(no solver, no LLM, no a-posteriori keywords):

==========================  ==================================================
``graph_parse_ok``          objective + >=1 variable + >=1 constraint parsed
``graph_complete``          one objective, >=1 parameter, every variable wired
                            into >=2 equations, every constraint uses >=1 var
``graph_coherent``          the equation/variable graph is connected
``graph_linear``            no non-linear token / variable product
``graph_safety_classes``    the embedded constraint classification covers the
                            essential railway-safety triad (running time,
                            headway separation, station capacity)
``graph_predicts_valid``    AND of the five -- the cheap verdict
==========================  ==================================================

``graph_predicts_valid`` is measured against the solver ground truth in
:mod:`analysis.hypothesis`.  No flag is claimed to *prove* validity.
"""

import pandas as pd

from railpminer.analysis.linearity import is_structurally_linear
from railpminer.analysis.metrics import calculate_complexity_metrics
from railpminer.analysis.taxonomy import SAFETY_CONSTRAINT_CLASSES

_FALSE = {
    "graph_parse_ok": False, "graph_complete": False,
    "graph_coherent": False, "graph_linear": False,
    "graph_safety_classes": False, "graph_flag_count": 5,
    "graph_predicts_valid": False, "constraint_classes": (),
    "parameter_count": 0,
}


def screen_one(nodes, connections, parse_ok=None):
    """Return the early-screen flag dict for one parsed, classified MILP."""
    if not nodes:
        return dict(_FALSE)

    n_obj = sum(n["type"] == "objective" for n in nodes)
    n_var = sum(n["type"] == "variable" for n in nodes)
    n_con = sum(n["type"] == "constraint" for n in nodes)
    n_par = sum(n["type"] == "parameter" for n in nodes)
    if parse_ok is None:
        parse_ok = n_obj >= 1 and n_var >= 1 and n_con >= 1

    metrics = calculate_complexity_metrics(nodes, connections)
    linear, _ = is_structurally_linear(nodes)

    con_classes = tuple(sorted({
        n.get("domain_class", "other_constraint")
        for n in nodes if n["type"] == "constraint"
    }))
    safety_ok = SAFETY_CONSTRAINT_CLASSES.issubset(set(con_classes))

    # every constraint must touch >=1 variable (no free-floating constraint)
    con_with_var = {c for c, _ in connections}
    every_con_used = all(
        n["number"] in con_with_var
        for n in nodes if n["type"] == "constraint"
    )
    complete = bool(metrics["model_completeness"]) and n_par >= 1 and every_con_used
    coherent = bool(metrics["model_coherence"])

    core = [bool(parse_ok), complete, coherent, linear, safety_ok]
    return {
        "graph_parse_ok": bool(parse_ok),
        "graph_complete": complete,
        "graph_coherent": coherent,
        "graph_linear": linear,
        "graph_safety_classes": safety_ok,
        "graph_flag_count": sum(1 for c in core if not c),
        "graph_predicts_valid": all(core),
        "constraint_classes": con_classes,
        "parameter_count": n_par,
    }


def compute_graph_screen(df: pd.DataFrame) -> pd.DataFrame:
    """Add the early-screen flag columns.  Requires ``nodes`` /
    ``connections`` (run :func:`graph_parser.create_graph_columns` first)."""
    df = df.copy()
    rows = []
    for idx in df.index:
        nodes = df.at[idx, "nodes"] or []
        conns = df.at[idx, "connections"] or []
        po = df.at[idx, "parse_ok"] if "parse_ok" in df.columns else None
        rows.append(screen_one(nodes, conns, po))
    return pd.concat([df, pd.DataFrame(rows, index=df.index)], axis=1)
