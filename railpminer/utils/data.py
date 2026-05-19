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


def annotate_temperature_support(df):
    """Add an explicit ``temperature_supported`` column.

    The previous version silently rewrote the recorded temperature of models
    that ignore the parameter to ``1.001`` -- a hidden fudge that Reviewer #2
    flagged as an inconsistent variable.  Instead we keep the requested
    temperature untouched and add a transparent boolean flag, so analyses can
    either drop or condition on temperature-insensitive models openly.

    Args:
        df: DataFrame with a ``model`` column.

    Returns:
        Copy of ``df`` with a ``temperature_supported`` column.
    """
    from railpminer.config import MODELS_WITHOUT_TEMPERATURE

    df = df.copy()
    if "temperature_supported" not in df.columns:
        df["temperature_supported"] = ~df["model"].isin(
            MODELS_WITHOUT_TEMPERATURE
        )
    return df
