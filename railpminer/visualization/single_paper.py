"""Single-paper selected MILP visualization."""

import textwrap
from typing import Tuple

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd
from matplotlib.patches import FancyBboxPatch

from railpminer.visualization.graphviz_utils import (
    create_manual_tree_layout,
    setup_graphviz,
)


def visualize_single_paper_selected(
    df: pd.DataFrame,
    paper_name: str,
    analysis_column: str = 'analysis_parameter',
    save_path: str = None,
    figsize_per_cell: Tuple[int, int] = (5, 10),
):
    """Visualize selected MILPs for a single paper in a 1-row layout.

    Args:
        df: DataFrame with analysis column, ``nodes``, ``connections``, etc.
        paper_name: e.g. ``"Paper_1"``.
        analysis_column: Column containing selection reasons.
        save_path: Full file path to save the figure.
        figsize_per_cell: Size per subplot cell.
    """
    df_selected = df[
        (df['paper'] == paper_name)
        & (df[analysis_column] != 'none')
    ].copy()

    if df_selected.empty:
        print(f"No selected models found for {paper_name}")
        return

    reason_order = [
        'High Minimal Size',
        'Low Minimal Size',
        'High Constraint Variable Ratio',
        'High Graph Diameter',
    ]
    df_selected['_sort_key'] = df_selected[analysis_column].apply(
        lambda x: reason_order.index(x) if x in reason_order else 99
    )
    df_selected = df_selected.sort_values('_sort_key').reset_index(drop=True)

    n_cols = len(df_selected)
    graphviz_available = setup_graphviz()

    fig, axes = plt.subplots(
        1, n_cols,
        figsize=(figsize_per_cell[0] * n_cols, figsize_per_cell[1]),
    )
    if n_cols == 1:
        axes = [axes]

    for col_idx, (_, row_data) in enumerate(df_selected.iterrows()):
        ax = axes[col_idx]
        selection_reason = row_data[analysis_column]
        nodes = row_data['nodes']
        connections = row_data['connections']

        if not nodes or len(nodes) == 0:
            ax.text(0.5, 0.5, 'Empty Graph', ha='center', va='center',
                    transform=ax.transAxes, fontsize=10, style='italic')
            ax.axis('off')
            continue

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

        root_node = None
        for node_id in G.nodes():
            if G.nodes[node_id]['type'] == 'objective':
                root_node = node_id
                break

        if graphviz_available:
            try:
                pos = nx.nx_pydot.graphviz_layout(G, prog='dot', root=root_node)
            except Exception:
                pos = create_manual_tree_layout(G)
        else:
            pos = create_manual_tree_layout(G)

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

        nx.draw_networkx_edges(G, pos, edge_color='gray', alpha=0.6, ax=ax, width=1.5)
        if objective_nodes:
            nx.draw_networkx_nodes(
                G, pos, nodelist=objective_nodes, node_color='lightgreen',
                node_shape='s', node_size=800, alpha=0.8, ax=ax,
            )
        if variable_nodes:
            nx.draw_networkx_nodes(
                G, pos, nodelist=variable_nodes, node_color='lightblue',
                node_shape='o', node_size=600, alpha=0.8, ax=ax,
            )
        if constraint_nodes:
            nx.draw_networkx_nodes(
                G, pos, nodelist=constraint_nodes, node_color='lightcoral',
                node_shape='D', node_size=600, alpha=0.8, ax=ax,
            )
        nx.draw_networkx_labels(
            G, pos, labels=node_labels, font_size=8, font_weight='bold', ax=ax,
        )

        ax.axis('off')

        # Header box (fixed size for uniform appearance)
        display_reason = textwrap.fill(selection_reason, width=18)
        box = FancyBboxPatch(
            (0.04, 1.04), 0.92, 0.22,
            transform=ax.transAxes,
            boxstyle="round,pad=0.02",
            facecolor='#f0f0f0', edgecolor='#cccccc', alpha=0.9,
            clip_on=False,
        )
        ax.add_patch(box)
        ax.text(
            0.5, 1.15, display_reason,
            transform=ax.transAxes, ha='center', va='center',
            fontsize=24, fontweight='bold', clip_on=False,
        )

        # Info box (fixed size for uniform appearance)
        model_name = str(row_data.get('model', ''))
        temperature = row_data.get('temperature', '')
        workflow = str(row_data.get('workflow', ''))
        model_display = model_name.replace('_', ' ').title() if model_name else 'N/A'

        info_text = f"LLM: {model_display}\nTemperature: {temperature}\nWorkflow: {workflow}"
        box = FancyBboxPatch(
            (0.04, -0.28), 0.92, 0.24,
            transform=ax.transAxes,
            boxstyle="round,pad=0.02",
            facecolor='#f7f7f7', edgecolor='#dddddd', alpha=0.8,
            clip_on=False,
        )
        ax.add_patch(box)
        ax.text(
            0.5, -0.16, info_text,
            transform=ax.transAxes, ha='center', va='center',
            fontsize=24, clip_on=False,
        )

    plt.tight_layout(rect=[0, 0.08, 1, 0.92])

    if save_path:
        fig.savefig(save_path, dpi=300, bbox_inches='tight')
        print(f"Saved to: {save_path}")

    plt.show()
