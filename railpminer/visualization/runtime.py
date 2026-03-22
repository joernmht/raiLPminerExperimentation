"""Runtime analysis boxplots."""

import matplotlib.pyplot as plt


def plot_runtime_boxplots(
    df,
    categories=("model", "temperature", "workflow", "paper"),
    figsize=(8, 6),
    fontsize=14,
    save_path=None,
):
    """Plot horizontal boxplots of runtime for given categorical columns.

    Uses a log-scaled x-axis to compress the long tail and make the bulk
    of the distribution readable.

    Args:
        df: DataFrame with ``runtime`` and the categorical columns.
        categories: Column names to group by.
        figsize: Size of the figure (width, height).
        fontsize: Base font size for labels, ticks, and title.
        save_path: If given, save the figure to this path.
    """
    positions = []
    data = []
    labels = []
    offset = 0
    gap = 1.5

    for cat in categories:
        groups = sorted(df[cat].unique(), key=str)
        for i, g in enumerate(groups):
            runtimes = df.loc[df[cat] == g, "runtime"].dropna()
            data.append(runtimes)
            positions.append(offset + i)
            labels.append(f"{g}")
        offset += len(groups) + gap

    n_rows = len(data)
    height = max(figsize[1], 0.45 * n_rows + 1.5)
    fig, ax = plt.subplots(figsize=(figsize[0], height))

    bp = ax.boxplot(
        data,
        positions=positions,
        widths=0.6,
        vert=False,
        patch_artist=True,
        flierprops=dict(marker="o", markersize=3, alpha=0.4),
    )

    for box in bp["boxes"]:
        box.set_facecolor("#b3cde3")
        box.set_edgecolor("#333333")
    for median in bp["medians"]:
        median.set_color("#d6604d")
        median.set_linewidth(2)

    ax.set_xscale("log")
    ax.set_xlim(left=1, right=1e3)
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=fontsize)
    ax.set_xlabel("Runtime [s]  (log scale)", fontsize=fontsize)
    ax.tick_params(axis="x", labelsize=fontsize - 1)
    ax.grid(axis="x", alpha=0.3, which="both")

    fig.tight_layout()

    if save_path:
        fig.savefig(save_path, bbox_inches="tight")

    plt.show()
