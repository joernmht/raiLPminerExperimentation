"""High-diversity subset selection for LP model analysis."""

import pandas as pd


def apply_selection_criteria(df_full, df_filtered, group_column, criteria, result_column_name):
    """Apply selection criteria to filtered data and add results to full DataFrame.

    Args:
        df_full: Full original DataFrame (unfiltered).
        df_filtered: Filtered DataFrame to apply criteria on.
        group_column: Column name to group by (e.g. ``"paper"``).
        criteria: List of tuples ``[(column_name, operation), ...]``
                  where operation is ``"max"``, ``"min"``, or ``"avg"``.
        result_column_name: Name of the new column to add.

    Returns:
        DataFrame with new column containing selection reasons or ``"none"``.
    """
    df_full[result_column_name] = 'none'

    for group_value in df_filtered[group_column].unique():
        group_df = df_filtered[df_filtered[group_column] == group_value]

        if len(group_df) == 0:
            continue

        for column_name, operation in criteria:
            if operation == 'max':
                idx = group_df[column_name].idxmax()
                reason = f'High {column_name.replace("_", " ").title()}'
            elif operation == 'min':
                idx = group_df[column_name].idxmin()
                reason = f'Low {column_name.replace("_", " ").title()}'
            elif operation == 'avg':
                mean_val = group_df[column_name].mean()
                idx = (group_df[column_name] - mean_val).abs().idxmin()
                reason = f'Average {column_name.replace("_", " ").title()}'
            else:
                continue

            df_full.at[idx, result_column_name] = reason

    return df_full
