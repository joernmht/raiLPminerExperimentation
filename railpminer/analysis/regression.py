"""OLS regression helpers."""


def run_ols_regression(df, formula):
    """Run an OLS regression and print the summary.

    Args:
        df: DataFrame with the data.
        formula: Patsy-style formula string (e.g.
                 ``"model_coherence ~ C(paper)"``).

    Returns:
        Fitted OLS model.
    """
    from statsmodels.formula.api import ols

    model = ols(formula, data=df).fit()
    print(formula)
    print(model.summary())
    return model
