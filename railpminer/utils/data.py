"""Centralized data loading and temperature correction."""

import pandas as pd


def load_experiment_data(csv_path):
    """Load experiment results CSV.

    Args:
        csv_path: Path to the CSV file.

    Returns:
        DataFrame with experiment data.
    """
    return pd.read_csv(csv_path)


def fix_temperature_labels(df):
    """Fix openai_o4_mini temperature labels.

    The o4-mini model does not support temperature != 1, so rows
    that were recorded with other temperature values are corrected
    to ``1.001`` (to distinguish them while being close to 1).

    Args:
        df: DataFrame with ``model`` and ``temperature`` columns.

    Returns:
        DataFrame with corrected temperature values (copy).
    """
    df = df.copy()
    mask = (df["model"] == "openai_o4_mini") & (df["temperature"] != 1)
    df.loc[mask, "temperature"] = 1.001
    return df
