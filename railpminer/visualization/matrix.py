"""Matrix-layout graph visualizations (papers x selection reasons)."""

import ast
from typing import Tuple

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

from railpminer.visualization.graphviz_utils import (
    create_manual_tree_layout,
    setup_graphviz,
)


def _build_graph_from_nodes_connections(nodes, connections):
    """Build a NetworkX graph from node/connection lists."""
    G = nx.Graph()
    for node in nodes:
        G.add_node(
            node['id'], type=node['type'],
            name=node['name'], number=node['number'],
        )
    for conn in connections:
        eq_num, var_num = conn
        eq_node = var_node = None
        for node in nodes:
            if node['type'] in ['objective', 'constraint'] and node['number'] == eq_num:
                eq_node = node['id']
            elif node['type'] == 'variable' and node['number'] == var_num:
                var_node = node['id']
        if eq_node and var_node:
            G.add_edge(eq_node, var_node)
    return G


def _get_layout(G, graphviz_available):
    """Get tree layout for graph."""
    root_node = None
    for node_id in G.nodes():
        if G.nodes[node_id]['type'] == 'objective':
            root_node = node_id
            break

    if graphviz_available:
        try:
            return nx.nx_pydot.graphviz_layout(G, prog='dot', root=root_node)
        except Exception:
            pass
    return create_manual_tree_layout(G)


def _classify_nodes(G):
    """Classify nodes into variable/objective/constraint lists and labels."""
    variable_nodes, objective_nodes, constraint_nodes = [], [], []
    node_labels = {}
    for node_id in G.nodes():
        nd = G.nodes[node_id]
        if nd['type'] == 'variable':
            variable_nodes.append(node_id)
            node_labels[node_id] = f"V{nd['number']}"
        elif nd['type'] == 'objective':
            objective_nodes.append(node_id)
            node_labels[node_id] = "OBJ"
        else:
            constraint_nodes.append(node_id)
            node_labels[node_id] = f"C{nd['number']}"
    return variable_nodes, objective_nodes, constraint_nodes, node_labels


def _draw_standard_nodes(ax, G, pos, variable_nodes, objective_nodes, constraint_nodes, node_labels, node_size=400):
    """Draw edges and nodes with standard styling."""
    nx.draw_networkx_edges(G, pos, edge_color='gray', alpha=0.6, ax=ax, width=1.5)

    if objective_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=objective_nodes, node_color='lightgreen',
            node_shape='s', node_size=node_size * 1.2, alpha=0.8, ax=ax,
        )
    if variable_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=variable_nodes, node_color='lightblue',
            node_shape='o', node_size=node_size, alpha=0.8, ax=ax,
        )
    if constraint_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=constraint_nodes, node_color='lightcoral',
            node_shape='D', node_size=node_size, alpha=0.8, ax=ax,
        )
    nx.draw_networkx_labels(
        G, pos, labels=node_labels, font_size=7, font_weight='bold', ax=ax,
    )


def visualize_matrix_graphs(
    final_df: pd.DataFrame,
    figsize_per_cell: Tuple[int, int] = (4, 3),
):
    """Visualize graphs in matrix format: papers as rows, selection reasons as columns.

    Args:
        final_df: DataFrame with ``paper``, ``selection_reason``, ``nodes``, ``connections``.
        figsize_per_cell: Size of each individual graph cell.
    """
    unique_papers = sorted(final_df['paper'].unique())
    unique_selection_reasons = sorted(final_df['selection_reason'].unique())

    n_rows = len(unique_papers)
    n_cols = len(unique_selection_reasons)

    if n_rows == 0 or n_cols == 0:
        print("No data to visualize")
        return

    graphviz_available = setup_graphviz()

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(figsize_per_cell[0] * n_cols, figsize_per_cell[1] * n_rows),
    )

    if n_rows == 1 and n_cols == 1:
        axes = [[axes]]
    elif n_rows == 1:
        axes = [axes]
    elif n_cols == 1:
        axes = [[ax] for ax in axes]

    for row_idx, paper in enumerate(unique_papers):
        for col_idx, selection_reason in enumerate(unique_selection_reasons):
            ax = axes[row_idx][col_idx]

            matching_rows = final_df[
                (final_df['paper'] == paper)
                & (final_df['selection_reason'] == selection_reason)
            ]

            if matching_rows.empty:
                ax.text(0.5, 0.5, 'No Data', ha='center', va='center',
                        transform=ax.transAxes, fontsize=10, style='italic')
                ax.axis('off')
                continue

            row_data = matching_rows.iloc[0]
            nodes = row_data['nodes']
            connections = row_data['connections']

            if not nodes or len(nodes) == 0:
                ax.text(0.5, 0.5, 'Empty Graph', ha='center', va='center',
                        transform=ax.transAxes, fontsize=10, style='italic')
                ax.axis('off')
                continue

            if isinstance(nodes, str):
                nodes = ast.literal_eval(nodes)
            if isinstance(connections, str):
                connections = ast.literal_eval(connections)

            G = _build_graph_from_nodes_connections(nodes, connections)
            pos = _get_layout(G, graphviz_available)
            variable_nodes, objective_nodes, constraint_nodes, node_labels = _classify_nodes(G)
            _draw_standard_nodes(ax, G, pos, variable_nodes, objective_nodes, constraint_nodes, node_labels)
            ax.axis('off')

    for col_idx, selection_reason in enumerate(unique_selection_reasons):
        axes[0][col_idx].annotate(
            selection_reason, xy=(0.5, 1.15), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=12, fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor='lightcoral', alpha=0.7),
        )

    for row_idx, paper in enumerate(unique_papers):
        axes[row_idx][0].annotate(
            paper, xy=(-0.15, 0.5), xycoords='axes fraction',
            ha='right', va='center', fontsize=12, fontweight='bold',
            rotation=90,
            bbox=dict(boxstyle="round,pad=0.3", facecolor='lightblue', alpha=0.7),
        )

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='s', color='w', markerfacecolor='lightgreen',
               markersize=8, label='Objective'),
        Line2D([0], [0], marker='D', color='w', markerfacecolor='lightcoral',
               markersize=6, label='Constraints'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='lightblue',
               markersize=6, label='Variables'),
    ]
    fig.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(0.98, 0.98))
    plt.tight_layout()
    plt.show()


def visualize_pivot_graphs(
    pivot_df: pd.DataFrame,
    figsize_per_cell: Tuple[int, int] = (4, 3),
):
    """Visualize graphs based on pivot table structure.

    Args:
        pivot_df: Pivot table with selection_reason as index, paper as columns.
        figsize_per_cell: Size of each individual graph cell.
    """
    n_rows = len(pivot_df.index)
    n_cols = len(pivot_df.columns)

    if n_rows == 0 or n_cols == 0:
        print("No data to visualize in pivot table")
        return

    setup_graphviz()

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(figsize_per_cell[0] * n_cols, figsize_per_cell[1] * n_rows),
    )

    if n_rows == 1 and n_cols == 1:
        axes = [[axes]]
    elif n_rows == 1:
        axes = [axes]
    elif n_cols == 1:
        axes = [[ax] for ax in axes]

    for row_idx, selection_reason in enumerate(pivot_df.index):
        for col_idx, paper in enumerate(pivot_df.columns):
            ax = axes[row_idx][col_idx]
            ax.axis('off')

    for col_idx, paper in enumerate(pivot_df.columns):
        axes[0][col_idx].annotate(
            paper, xy=(0.5, 1.15), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=14, fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor='lightblue', alpha=0.7),
        )

    for row_idx, selection_reason in enumerate(pivot_df.index):
        axes[row_idx][0].annotate(
            selection_reason, xy=(-0.15, 0.5), xycoords='axes fraction',
            ha='right', va='center', fontsize=12, fontweight='bold',
            rotation=90,
            bbox=dict(boxstyle="round,pad=0.3", facecolor='lightcoral', alpha=0.7),
        )

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='s', color='w', markerfacecolor='lightgreen',
               markersize=8, label='Objective'),
        Line2D([0], [0], marker='D', color='w', markerfacecolor='lightcoral',
               markersize=6, label='Constraints'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='lightblue',
               markersize=6, label='Variables'),
    ]
    fig.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(0.98, 0.98))
    plt.tight_layout()
    plt.show()


def visualize_paper_matrix_selected(
    df: pd.DataFrame,
    analysis_column: str = 'analysis_paper',
    save_path: str = None,
    figsize_per_cell: Tuple[int, int] = (4, 3),
):
    """Visualize selected MILPs as a matrix: Y=papers, X=selection reasons.

    Args:
        df: DataFrame with analysis column, ``nodes``, ``connections``, ``paper``.
        analysis_column: Column containing selection reasons.
        save_path: Full file path to save the figure.
        figsize_per_cell: Size per subplot cell.
    """
    df_selected = df[df[analysis_column] != 'none'].copy()
    df_selected = df_selected.rename(columns={analysis_column: 'selection_reason'})

    if df_selected.empty:
        print(f"No selected models found in column '{analysis_column}'")
        return

    unique_papers = sorted(df_selected['paper'].unique())
    unique_selection_reasons = sorted(df_selected['selection_reason'].unique())

    n_rows = len(unique_papers)
    n_cols = len(unique_selection_reasons)

    if n_rows == 0 or n_cols == 0:
        print("No data to visualize")
        return

    graphviz_available = setup_graphviz()

    fig, axes = plt.subplots(
        n_rows, n_cols,
        figsize=(figsize_per_cell[0] * n_cols, figsize_per_cell[1] * n_rows),
    )

    if n_rows == 1 and n_cols == 1:
        axes = [[axes]]
    elif n_rows == 1:
        axes = [axes]
    elif n_cols == 1:
        axes = [[ax] for ax in axes]

    for row_idx, paper in enumerate(unique_papers):
        for col_idx, selection_reason in enumerate(unique_selection_reasons):
            ax = axes[row_idx][col_idx]

            matching_rows = df_selected[
                (df_selected['paper'] == paper)
                & (df_selected['selection_reason'] == selection_reason)
            ]

            if matching_rows.empty:
                ax.text(0.5, 0.5, 'No Data', ha='center', va='center',
                        transform=ax.transAxes, fontsize=10, style='italic')
                ax.axis('off')
                continue

            row_data = matching_rows.iloc[0]
            nodes = row_data['nodes']
            connections = row_data['connections']

            if not nodes or len(nodes) == 0:
                ax.text(0.5, 0.5, 'Empty Graph', ha='center', va='center',
                        transform=ax.transAxes, fontsize=10, style='italic')
                ax.axis('off')
                continue

            G = _build_graph_from_nodes_connections(nodes, connections)
            pos = _get_layout(G, graphviz_available)
            variable_nodes, objective_nodes, constraint_nodes, node_labels = _classify_nodes(G)
            _draw_standard_nodes(ax, G, pos, variable_nodes, objective_nodes, constraint_nodes, node_labels)
            ax.axis('off')

    for col_idx, selection_reason in enumerate(unique_selection_reasons):
        axes[0][col_idx].annotate(
            selection_reason, xy=(0.5, 1.15), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=11, fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor='#f0f0f0',
                      edgecolor='#cccccc', alpha=0.9),
        )

    for row_idx, paper in enumerate(unique_papers):
        axes[row_idx][0].annotate(
            paper.replace('_', ' '), xy=(-0.15, 0.5),
            xycoords='axes fraction', ha='right', va='center',
            fontsize=12, fontweight='bold',
        )

    plt.tight_layout(rect=[0.08, 0, 1, 0.94])

    if save_path:
        fig.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Saved to: {save_path}")

    plt.show()
