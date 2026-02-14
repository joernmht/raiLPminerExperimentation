"""Advanced scatter plot analysis with coherence/completeness highlighting."""

import numpy as np
import matplotlib.pyplot as plt


def create_advanced_scatter_analysis(
    df,
    category_col='model',
    x_col='constraint_count',
    y_col='variable_count',
    coherence_col='model_coherence',
    completeness_col='corrected_completeness',
    size_multiplier=30,
    figsize=None,
    show_annotations=True,
    annotation_threshold=1,
    save_path=None,
    title_suffix="",
    bottom_margin=0.15,
    title=False,
    legend=False,
    cols=4,
    category_order=None,
):
    """Enhanced scatter analysis with coherence/completeness highlighting.

    Points with coherence=1 and completeness=1 are shown with an inner ring.

    Args:
        df: DataFrame with the data.
        category_col: Column to create subplots for.
        x_col: Column for x-axis.
        y_col: Column for y-axis.
        coherence_col: Column for coherence values.
        completeness_col: Column for completeness values.
        size_multiplier: Bubble size multiplier.
        figsize: Figure size (auto-calculated if None).
        save_path: Path to save the figure.
        title_suffix: Suffix for the main title.
        bottom_margin: Bottom margin for legend space.
        title: Whether to show the main title.
        legend: Whether to show the legend.
        cols: Number of columns in the grid.
        category_order: Ordered list of categories.

    Returns:
        Tuple of (figure, axes).
    """
    if category_order is not None:
        categories = [c for c in category_order if c in df[category_col].values]
    else:
        categories = df[category_col].unique()
    n_categories = len(categories)

    if n_categories <= cols:
        n_cols, n_rows = cols, 1
        default_figsize = (cols * n_categories, 6)
    elif n_categories <= cols * 2:
        n_cols, n_rows = cols, 2
        default_figsize = (19, 12)
    else:
        n_cols = cols
        n_rows = int(np.ceil(n_categories / cols))
        default_figsize = (10, 5 * n_rows + 2)

    figsize = figsize or default_figsize
    fig, axes = plt.subplots(n_rows, n_cols, figsize=figsize)

    if n_categories == 1:
        axes = [axes]
    elif n_rows == 1:
        axes = axes if n_categories > 1 else [axes]
    else:
        axes = axes.flatten()

    max_frequency = 0

    for i, category in enumerate(categories):
        category_data = df[df[category_col] == category]

        freq_data = category_data.groupby([x_col, y_col]).agg({
            category_col: 'size',
            coherence_col: lambda x: (x == 1).sum() if coherence_col in category_data.columns else 0,
            completeness_col: lambda x: (x == 1).sum() if completeness_col in category_data.columns else 0,
        }).reset_index()

        freq_data.columns = [
            x_col, y_col, 'frequency', 'coherence_ones', 'completeness_ones',
        ]

        max_frequency = max(max_frequency, freq_data['frequency'].max())

        freq_data['has_perfect'] = np.minimum(
            freq_data['coherence_ones'], freq_data['completeness_ones'],
        )

        axes[i].scatter(
            freq_data[x_col], freq_data[y_col],
            s=freq_data['frequency'] * size_multiplier,
            c='#808080', alpha=0.5, edgecolors='none', zorder=1,
        )

        perfect_data = freq_data[freq_data['has_perfect'] > 0]
        if not perfect_data.empty:
            inner_sizes = perfect_data['has_perfect'] * size_multiplier * 0.6
            axes[i].scatter(
                perfect_data[x_col], perfect_data[y_col],
                s=inner_sizes, c='#404040', alpha=1,
                edgecolors='darkblue', linewidth=0.5, zorder=2,
            )

        axes[i].set_xlim(0, 25)
        axes[i].set_ylim(0, 15)
        axes[i].set_xlabel(x_col.replace('_', ' ').title(), fontsize=18)
        axes[i].set_ylabel(y_col.replace('_', ' ').title(), fontsize=18)
        axes[i].set_title(
            f'{category_col.title()}: {category}',
            fontsize=18, fontweight='bold',
        )
        axes[i].grid(True, alpha=0.3, zorder=0)

    for j in range(n_categories, len(axes)):
        axes[j].axis('off')

    if title:
        main_title = (
            f'{y_col.replace("_", " ").title()} vs '
            f'{x_col.replace("_", " ").title()} by {category_col.title()}'
        )
        if title_suffix:
            main_title += f' - {title_suffix}'
        plt.suptitle(
            main_title, fontsize=18, fontweight='bold',
            x=figsize[0] * 0.009,
        )

    plt.tight_layout(rect=[0, bottom_margin, 1, 0.96])

    if legend:
        legend_y = 0.08

        size_legend_ax = fig.add_axes(
            [0.15, legend_y - 0.02, 0.30, 0.08], frameon=False,
        )
        size_legend_ax.axis('off')

        sizes = [1, 5, 10, 20]
        size_labels = ['1', '5', '10', '20']
        x_positions = np.linspace(0.2, 0.8, len(sizes))

        for si, (size, label) in enumerate(zip(sizes, size_labels)):
            size_legend_ax.scatter(
                x_positions[si], 0.5, s=size * size_multiplier,
                c='#808080', alpha=0.5, edgecolors='none',
            )
            size_legend_ax.text(
                x_positions[si], 0.01, label,
                ha='center', va='top', fontsize=18,
            )

        size_legend_ax.text(
            0.5, 0.9, 'Frequency', ha='center', va='bottom',
            fontsize=18, fontweight='bold',
        )
        size_legend_ax.set_xlim(0, 1)
        size_legend_ax.set_ylim(0, 1)

        ring_legend_ax = fig.add_axes(
            [0.55, legend_y - 0.02, 0.30, 0.08], frameon=False,
        )
        ring_legend_ax.axis('off')

        ring_legend_ax.scatter(0.25, 0.5, s=150, c='#808080', alpha=0.5, edgecolors='none')
        ring_legend_ax.text(0.1, 0.01, 'All values', ha='center', va='top', fontsize=18)

        ring_legend_ax.scatter(0.75, 0.5, s=150, c='#808080', alpha=0.5, edgecolors='none')
        ring_legend_ax.scatter(
            0.75, 0.5, s=90, c='#404040', alpha=1,
            edgecolors='darkblue', linewidth=1.5,
        )
        ring_legend_ax.text(
            0.9, 0.01, 'Accepted (Coherence=1 & Completeness=1)',
            ha='center', va='top', fontsize=18,
        )

        ring_legend_ax.text(
            0.5, 0.95, 'MILP acceptance', ha='center', va='bottom',
            fontsize=18, fontweight='bold',
        )
        ring_legend_ax.set_xlim(0, 1)
        ring_legend_ax.set_ylim(0, 1)

    if save_path:
        plt.savefig(save_path, dpi=300, format="pdf", bbox_inches='tight')
        print(f"Plot saved to: {save_path}")

    return fig, axes
