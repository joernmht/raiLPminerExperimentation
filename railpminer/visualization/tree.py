"""Hierarchical tree-layout graph visualization for LP models."""

from typing import Dict, List, Tuple

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

from railpminer.visualization.graphviz_utils import (
    create_manual_tree_layout,
    setup_graphviz,
)


def _build_graph_and_layout(nodes, connections, graphviz_available):
    """Build a NetworkX graph, compute tree layout, and classify nodes."""
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

    return G, pos, variable_nodes, objective_nodes, constraint_nodes, node_labels


def visualize_tree_graph(
    nodes: List[Dict],
    connections: List[List],
    title: str = "LP Model Tree Graph",
    figsize: Tuple[int, int] = (12, 10),
):
    """Visualize the graph in a hierarchical tree layout.

    Args:
        nodes: List of node dictionaries.
        connections: List of ``[equation_number, variable_number]`` pairs.
        title: Title for the plot.
        figsize: Figure size tuple.
    """
    if not nodes:
        print("No nodes to visualize")
        return

    graphviz_available = setup_graphviz()
    G, pos, variable_nodes, objective_nodes, constraint_nodes, node_labels = (
        _build_graph_and_layout(nodes, connections, graphviz_available)
    )

    fig, ax = plt.subplots(figsize=figsize)
    nx.draw_networkx_edges(G, pos, edge_color='gray', alpha=0.6, ax=ax, width=2)

    if objective_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=objective_nodes, node_color='lightgreen',
            node_shape='s', node_size=1500, alpha=0.8, ax=ax,
        )
    if variable_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=variable_nodes, node_color='lightblue',
            node_shape='o', node_size=1200, alpha=0.8, ax=ax,
        )
    if constraint_nodes:
        nx.draw_networkx_nodes(
            G, pos, nodelist=constraint_nodes, node_color='lightcoral',
            node_shape='D', node_size=1200, alpha=0.8, ax=ax,
        )

    nx.draw_networkx_labels(
        G, pos, labels=node_labels, font_size=9, font_weight='bold', ax=ax,
    )

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='s', color='w', markerfacecolor='lightgreen',
               markersize=12, label='Objective (Square)'),
        Line2D([0], [0], marker='D', color='w', markerfacecolor='lightcoral',
               markersize=10, label='Constraints (Diamond)'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='lightblue',
               markersize=10, label='Variables (Circle)'),
    ]
    ax.legend(handles=legend_elements, loc='upper left', bbox_to_anchor=(1, 1))
    ax.set_title(title, fontsize=16, fontweight='bold', pad=20)
    ax.axis('off')
    plt.tight_layout()
    plt.show()


def visualize_multiple_models_tree(
    df: pd.DataFrame,
    indices: List[int] = None,
    max_models: int = 4,
):
    """Visualize multiple models in a grid layout with tree structure.

    Args:
        df: Processed DataFrame with ``nodes`` and ``connections`` columns.
        indices: List of specific indices to visualize.
        max_models: Maximum number of models to visualize.
    """
    if indices is None:
        indices = list(range(min(len(df), max_models)))

    n_models = len(indices)
    if n_models == 0:
        print("No models to visualize")
        return

    graphviz_available = setup_graphviz()

    cols = min(5, n_models)
    rows = (n_models + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(6 * cols, 5 * rows))

    if n_models == 1:
        axes = [axes]
    else:
        axes = axes.flatten() if hasattr(axes, 'flatten') else [axes]

    for i, idx in enumerate(indices):
        if idx >= len(df):
            continue

        ax = axes[i] if n_models > 1 else axes[0]
        nodes = df.iloc[idx]['nodes']
        connections = df.iloc[idx]['connections']

        if not nodes:
            ax.text(0.5, 0.5, f'Model {idx+1}\nNo data',
                    ha='center', va='center', transform=ax.transAxes)
            ax.axis('off')
            continue

        G, pos, variable_nodes, objective_nodes, constraint_nodes, node_labels = (
            _build_graph_and_layout(nodes, connections, graphviz_available)
        )

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
            G, pos, labels=node_labels, font_size=7, font_weight='bold', ax=ax,
        )

        ax.set_title(f'Model {idx+1}', fontsize=12, fontweight='bold')
        ax.axis('off')

    for i in range(n_models, len(axes)):
        axes[i].axis('off')

    from matplotlib.lines import Line2D
    legend_elements = [
        Line2D([0], [0], marker='s', color='w', markerfacecolor='lightgreen',
               markersize=10, label='Objective'),
        Line2D([0], [0], marker='D', color='w', markerfacecolor='lightcoral',
               markersize=8, label='Constraints'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='lightblue',
               markersize=8, label='Variables'),
    ]
    fig.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(0.98, 0.98))
    plt.tight_layout()
    plt.show()
