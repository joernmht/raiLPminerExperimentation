"""Runtime analysis boxplots."""

import matplotlib.pyplot as plt


def plot_runtime_boxplots(
    df,
    categories=("model", "temperature", "workflow", "paper"),
    figsize=(10, 8),
):
    """Plot horizontal boxplots of runtime for given categorical columns.

    Args:
        df: DataFrame with ``runtime`` and the categorical columns.
        categories: Column names to group by.
        figsize: Size of the figure (width, height).
    """
    plt.figure(figsize=figsize)

    positions = []
    data = []
    labels = []
    offset = 0
    gap = 2

    for cat in categories:
        groups = df[cat].unique()
        for i, g in enumerate(groups):
            runtimes = df.loc[df[cat] == g, "runtime"]
            data.append(runtimes)
            positions.append(offset + i)
            labels.append(f"{cat}: {g}")
        offset += len(groups) + gap

    plt.boxplot(data, positions=positions, widths=0.6, vert=False)
    plt.yticks(positions, labels)
    plt.xlabel("Runtime [s]")
    plt.title("Runtime Distribution by Setup")
    plt.tight_layout()
    plt.show()
