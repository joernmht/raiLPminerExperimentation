"""MILP/LP operator detection in text."""

import re

import pandas as pd


def detect_milp_artifacts(df, text_column='answer'):
    """Detect typical MILP/LP operators in text.

    Adds two columns:
    - ``count``: total number of operator occurrences.
    - ``operator_coverage``: fraction of operator groups present (0.0--1.0).

    Args:
        df: DataFrame containing the text data.
        text_column: Name of the column to check.

    Returns:
        DataFrame with added ``count`` and ``operator_coverage`` columns.
    """
    operator_groups = [
        [r'<=', r'\\leq', r'leq', r'\u2264'],
        [r'>=', r'\\geq', r'geq', r'\u2265'],
        [r'\\sum', r'sum_', r'\u2211'],
        [r'\\forall', r'forall', r'for all', r'for each', r'for any', r'\u2200'],
        [r's\.t\.', r'subject to', r'such that'],
    ]

    total_groups = len(operator_groups)

    def analyze_text(text):
        text = str(text)
        total_count = 0
        for group in operator_groups:
            for pattern in group:
                total_count += len(re.findall(pattern, text))

        groups_present = 0
        for group in operator_groups:
            if any(re.search(pattern, text) for pattern in group):
                groups_present += 1

        coverage = groups_present / total_groups
        return total_count, coverage

    results = df[text_column].apply(analyze_text)
    df['count'] = results.apply(lambda x: x[0])
    df['operator_coverage'] = results.apply(lambda x: x[1])
    return df
