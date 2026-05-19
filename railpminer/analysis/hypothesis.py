"""Does the graph screen catch flaws early?  -- the central analysis.

Hypothesis: *a graph-based depiction of a MILP allows identifying flaws
early in the development process* (before any solver is implemented).

The cheap, solver-free verdict ``graph_predicts_valid`` (see
:mod:`analysis.screen`) is treated as a screening test for the strict solver
ground truth ``solver_valid`` (see :mod:`validation`).  "Flawed" =
``not solver_valid``; a good early screen fires (``not
graph_predicts_valid``) on flawed models.

Reported: confusion of (screen flags flawed) vs (actually flawed),
``early_catch_rate`` (recall on flaws), ``false_alarm_rate``,
``screen_precision``; a per-signal breakdown; and a per-workflow comparison
so the graph-based workflows (SIM/TAF) can be compared with the direct-code
baseline (DC) like-for-like.
"""

from typing import Dict

import pandas as pd

_SIGNALS = [
    "graph_predicts_valid",
    "graph_parse_ok",
    "graph_complete",
    "graph_coherent",
    "graph_linear",
    "graph_safety_classes",
]


def screen_confusion(
    df: pd.DataFrame,
    predictor: str = "graph_predicts_valid",
    truth: str = "solver_valid",
) -> Dict[str, float]:
    """Confusion of the screen flagging *flaws* against the solver truth.

    Positive event = "flawed" = ``not truth``; screen predicts flawed when
    ``not predictor``.
    """
    d = df.dropna(subset=[predictor, truth])
    flawed = ~d[truth].astype(bool)
    flagged = ~d[predictor].astype(bool)

    tp = int((flagged & flawed).sum())
    fp = int((flagged & ~flawed).sum())
    fn = int((~flagged & flawed).sum())
    tn = int((~flagged & ~flawed).sum())
    n = tp + fp + fn + tn

    def s(a, b):
        return a / b if b else float("nan")

    return {
        "n": n,
        "flawed": int(flawed.sum()),
        "valid": int((~flawed).sum()),
        "tp_flawed_caught": tp,
        "fp_false_alarm": fp,
        "fn_flawed_missed": fn,
        "tn_valid_passed": tn,
        "early_catch_rate": s(tp, tp + fn),
        "false_alarm_rate": s(fp, fp + tn),
        "screen_precision": s(tp, tp + fp),
        "accuracy": s(tp + tn, n),
    }


def evaluate_screen_against_solver(
    df: pd.DataFrame, truth: str = "solver_valid"
) -> pd.DataFrame:
    """Per-signal early-catch breakdown (which cheap signal does the work)."""
    rows = [
        {"signal": s, **screen_confusion(df, s, truth)}
        for s in _SIGNALS if s in df.columns
    ]
    return pd.DataFrame(rows).set_index("signal")


def workflow_comparison(
    df: pd.DataFrame,
    workflow_col: str = "workflow",
    predictor: str = "graph_predicts_valid",
    truth: str = "solver_valid",
) -> pd.DataFrame:
    """Per-workflow: solver-valid rate, screen-pass rate and screen quality.

    Lets the graph-based workflows (SIM/TAF) be compared with the
    direct-code baseline (DC) on the same solver ground truth.
    """
    out = []
    for wf, g in df.groupby(workflow_col):
        conf = screen_confusion(g, predictor, truth)
        out.append({
            workflow_col: wf,
            "n": len(g),
            "solver_valid_rate": g[truth].astype(bool).mean(),
            "screen_pass_rate": g[predictor].astype(bool).mean(),
            "early_catch_rate": conf["early_catch_rate"],
            "false_alarm_rate": conf["false_alarm_rate"],
            "screen_accuracy": conf["accuracy"],
        })
    return pd.DataFrame(out).set_index(workflow_col)
