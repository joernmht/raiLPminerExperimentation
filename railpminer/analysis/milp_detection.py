"""MILP/LP operator detection -- a coarse pre-filter on raw text.

This detects whether a text even *looks* like an optimisation model by
counting occurrences of five operator groups (<=, >=, sum, for-all,
subject-to).  It runs before parsing and is intentionally cheap.

Operator-coverage threshold (Reviewer #3.2)
-------------------------------------------
The accept rule is parameterised (:func:`detect_milp_artifacts`,
``min_coverage``).  The default ``min_coverage=0.5`` means at least 3 of the
5 operator groups must be present.  This is an **empirical hard threshold**
with a known edge case: a perfectly valid but small MILP may only use, say,
summation and ``<=`` (coverage 0.4) and would be rejected here even though
it is structurally fine.  Because the threshold is a parameter, the
sensitivity of downstream results to it can be reported, and a *dynamic*
threshold (e.g. scaled by model size, or learned against the solver ground
truth) is an explicit future-work direction rather than a hidden constant.
"""

import re

import pandas as pd

OPERATOR_GROUPS = [
    [r"<=", r"\\leq", r"leq", r"≤"],
    [r">=", r"\\geq", r"geq", r"≥"],
    [r"\\sum", r"sum_", r"∑"],
    [r"\\forall", r"forall", r"for all", r"for each", r"for any", r"∀"],
    [r"s\.t\.", r"subject to", r"such that"],
]


def detect_milp_artifacts(df, text_column="answer", min_coverage=0.5):
    """Add ``count``, ``operator_coverage`` and ``sufficient_operators``.

    Args:
        df: DataFrame containing the text.
        text_column: Column to scan.
        min_coverage: Acceptance threshold on operator-group coverage
            (fraction of the 5 groups present).  Exposed as a parameter so
            its effect on the results can be reported; see the module
            docstring for the hard-threshold edge case.

    Returns:
        The DataFrame with three added columns.
    """
    total_groups = len(OPERATOR_GROUPS)

    def analyze_text(text):
        text = str(text)
        total_count = sum(
            len(re.findall(p, text))
            for group in OPERATOR_GROUPS
            for p in group
        )
        groups_present = sum(
            any(re.search(p, text) for p in group)
            for group in OPERATOR_GROUPS
        )
        return total_count, groups_present / total_groups

    results = df[text_column].apply(analyze_text)
    df = df.copy()
    df["count"] = results.apply(lambda x: x[0])
    df["operator_coverage"] = results.apply(lambda x: x[1])
    df["sufficient_operators"] = df["operator_coverage"] >= min_coverage
    return df
