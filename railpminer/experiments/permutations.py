"""Parameter permutation generation for experiment design."""

import itertools

import pandas as pd


def create_all_permutations(param_dict):
    """Generate a DataFrame containing all possible permutations of parameter values.

    Args:
        param_dict: Dictionary where keys are parameter names and values are
                    lists of possible parameter values.

    Returns:
        DataFrame with all possible parameter combinations.
    """
    param_names = list(param_dict.keys())
    param_values = list(param_dict.values())
    all_combinations = list(itertools.product(*param_values))
    return pd.DataFrame(all_combinations, columns=param_names)


def set_default_parameters(df):
    """Return default parameter dict for experiment design.

    Args:
        df: DataFrame with a ``paper`` column whose unique values become
            the paper parameter list.

    Returns:
        Dictionary suitable for :func:`create_all_permutations`.
    """
    parameters = {
        'model': ["openai_o4_mini"],
        'temperature': [0.2],
        'paper': df.paper.tolist(),
    }
    return parameters
