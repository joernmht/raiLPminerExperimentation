"""Experiment design: a fully-crossed, deterministic factorial grid.

Reviewer feedback on the previous version pointed out that the executed
design was *not* a fair comparison: some factor combinations were skipped,
some were run more often than others, and some were filtered out by hand.

This module makes the design explicit and reproducible:

* :func:`create_all_permutations` enumerates the **complete** Cartesian
  product of all factor levels (no combination can be missing).
* :func:`build_factorial_design` attaches a fixed number of replications per
  cell and a deterministic ``run_seed`` per run, so every cell has exactly
  the same number of runs and the ordering is reproducible.
* :func:`assert_balanced` fails loudly if any cell is under- or
  over-represented, so an unbalanced design can never be analysed as if it
  were complete.
"""

import itertools

import pandas as pd


def create_all_permutations(param_dict):
    """Return a DataFrame with the complete Cartesian product of factors.

    Args:
        param_dict: ``{factor_name: [levels...]}``.

    Returns:
        DataFrame with one row per factor combination.
    """
    names = list(param_dict)
    combos = list(itertools.product(*(param_dict[n] for n in names)))
    return pd.DataFrame(combos, columns=names)


def build_factorial_design(param_dict, replications=14, base_seed=20240501):
    """Build a balanced factorial design with fixed replication.

    Every factor combination receives exactly ``replications`` runs.  Each
    run gets a deterministic ``run_seed`` so the whole experiment is
    reproducible and no cell is silently skipped or repeated.

    Args:
        param_dict: ``{factor_name: [levels...]}``.
        replications: Runs per cell (identical for every cell).
        base_seed: Base for the deterministic per-run seed.

    Returns:
        DataFrame with the factor columns plus ``cell_id``, ``replicate``
        and ``run_seed``.
    """
    cells = create_all_permutations(param_dict)
    cells["cell_id"] = range(len(cells))

    rows = []
    for _, cell in cells.iterrows():
        for rep in range(replications):
            row = cell.to_dict()
            row["replicate"] = rep
            row["run_seed"] = base_seed + row["cell_id"] * 1000 + rep
            rows.append(row)
    return pd.DataFrame(rows)


def assert_balanced(df, factor_columns):
    """Raise ``AssertionError`` unless every factor cell has equal run count.

    Use this right before analysis so an incomplete or lopsided experiment
    can never be reported as a fair comparison.
    """
    counts = df.groupby(list(factor_columns)).size()
    if counts.nunique() != 1:
        raise AssertionError(
            "Unbalanced design: cells have run counts "
            f"{sorted(counts.unique())}. Re-run the missing cells before "
            "analysing; do not drop or hand-filter combinations."
        )
    return int(counts.iloc[0])
