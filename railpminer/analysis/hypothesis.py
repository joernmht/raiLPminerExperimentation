"""Does the graph screen catch flaws early?  -- the central analysis.

Hypothesis under test:

    *A graph-based depiction of a MILP allows identifying flaws early in the
    development process* (before any solver is implemented).

We treat the cheap, solver-free verdict ``graph_predicts_valid`` (see
:mod:`analysis.screen`) as a screening test for the strict solver ground
truth ``solver_valid`` (see :mod:`validation`).  "Flawed" means
``not solver_valid``; a good early screen should fire (``not
graph_predicts_valid``) on flawed models.

The functions here only *measure* agreement -- they make no correctness
claim by themselves.  Reported quantities:

* confusion matrix of (screen says flawed) vs (actually flawed),
* ``early_catch_rate``  = P(screen flags flawed | actually flawed)  -- recall,
* ``false_alarm_rate``  = P(screen flags flawed | actually valid),
* ``screen_precision``  = P(actually flawed | screen flags flawed),
* per-flag breakdown so it is visible *which* graph signal does the work.
"""

from typing import Dict

import pandas as pd


def screen_confusion(
    df: pd.DataFrame,
    predictor: str = "graph_predicts_valid",
    truth: str = "solver_valid",
) -> Dict[str, float]:
    """Confusion of the screen flagging *flaws* against the solver truth.

    Positive event = "flawed" = ``not truth``.  The screen predicts flawed
    when ``not predictor``.
    """
    d = df.dropna(subset=[predictor, truth])
    flawed = ~d[truth].astype(bool)
    flagged = ~d[predictor].astype(bool)

    tp = int((flagged & flawed).sum())     # flawed and caught
    fp = int((flagged & ~flawed).sum())    # valid but flagged (false alarm)
    fn = int((~flagged & flawed).sum())    # flawed but missed
    tn = int((~flagged & ~flawed).sum())   # valid and passed
    n = tp + fp + fn + tn

    def _safe(a, b):
        return a / b if b else float("nan")

    return {
        "n": n,
        "flawed": int(flawed.sum()),
        "valid": int((~flawed).sum()),
        "tp_flawed_caught": tp,
        "fp_false_alarm": fp,
        "fn_flawed_missed": fn,
        "tn_valid_passed": tn,
        "early_catch_rate": _safe(tp, tp + fn),
        "false_alarm_rate": _safe(fp, fp + tn),
        "screen_precision": _safe(tp, tp + fp),
        "accuracy": _safe(tp + tn, n),
    }


def evaluate_screen_against_solver(
    df: pd.DataFrame, truth: str = "solver_valid"
) -> pd.DataFrame:
    """Per-signal early-catch breakdown.

    For the combined verdict and each individual graph flag, report how much
    of the flawed population it catches and how many false alarms it raises,
    so the contribution of each cheap signal is explicit.
    """
    flags = [
        "graph_predicts_valid",
        "graph_parse_ok",
        "graph_complete",
        "graph_coherent",
        "graph_linear",
        "graph_domain_covered",
    ]
    rows = []
    for flag in flags:
        if flag not in df.columns:
            continue
        rows.append({"signal": flag, **screen_confusion(df, flag, truth)})
    return pd.DataFrame(rows).set_index("signal")
