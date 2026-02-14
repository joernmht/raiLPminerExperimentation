"""Graph diameter analysis and visualization with diameter path highlighting."""

from typing import Tuple

import matplotlib.pyplot as plt
import networkx as nx
import pandas as pd

from railpminer.visualization.graphviz_utils import (
    create_manual_tree_layout,
    setup_graphviz,
)


def find_graph_diameter_and_path(G):
    """Find the diameter of the graph and the path(s) that achieve it.

    Args:
        G: NetworkX graph.

    Returns:
        Tuple of ``(diameter, diameter_edges, diameter_paths)``.
    """
    if len(G.nodes()) == 0:
        return 0, set(), []

    if not nx.is_connected(G):
        largest_component = max(nx.connected_components(G), key=len)
        G = G.subgraph(largest_component)

    all_shortest_paths = dict(nx.all_pairs_shortest_path_length(G))

    diameter = 0
    diameter_pairs = []

    for source in all_shortest_paths:
        for target, distance in all_shortest_paths[source].items():
            if distance > diameter:
                diameter = distance
                diameter_pairs = [(source, target)]
            elif distance == diameter and distance > 0:
                diameter_pairs.append((source, target))

    diameter_edges = set()
    diameter_paths = []

    for source, target in diameter_pairs:
        try:
            path = nx.shortest_path(G, source, target)
            diameter_paths.append(path)
            for i in range(len(path) - 1):
                edge = tuple(sorted([path[i], path[i + 1]]))
                diameter_edges.add(edge)
        except nx.NetworkXNoPath:
            continue

    return diameter, diameter_edges, diameter_paths


def visualize_matrix_graphs_with_diameter(
    final_df: pd.DataFrame,
    figsize_per_cell: Tuple[int, int] = (4, 3),
):
    """Enhanced matrix visualization with graph diameter highlighted.

    Args:
        final_df: DataFrame with ``paper``, ``selection_reason``, ``nodes``, ``connections``.
        figsize_per_cell: Size of each individual graph cell.
    """
    unique_papers = sorted(final_df['paper'].unique())
    unique_selection_reasons = sorted(final_df['selection_reason'].unique())

    n_rows = len(unique_selection_reasons)
    n_cols = len(unique_papers)

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

    max_diameter = 0

    for row_idx, selection_reason in enumerate(unique_selection_reasons):
        for col_idx, paper in enumerate(unique_papers):
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

            diameter, diameter_edges, diameter_paths = find_graph_diameter_and_path(G)
            max_diameter = max(max_diameter, diameter)

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

            # Separate diameter edges from regular edges
            regular_edges = []
            diameter_edge_list = []
            for edge in G.edges():
                edge_tuple = tuple(sorted(edge))
                if edge_tuple in diameter_edges:
                    diameter_edge_list.append(edge)
                else:
                    regular_edges.append(edge)

            if regular_edges:
                nx.draw_networkx_edges(
                    G, pos, edgelist=regular_edges,
                    edge_color='gray', alpha=0.6, ax=ax, width=1.5,
                )
            if diameter_edge_list:
                nx.draw_networkx_edges(
                    G, pos, edgelist=diameter_edge_list,
                    edge_color='orange', alpha=0.9, ax=ax, width=2,
                    style='solid',
                )

            # Identify diameter endpoints
            diameter_nodes = set()
            for path in diameter_paths:
                if len(path) >= 2:
                    diameter_nodes.add(path[0])
                    diameter_nodes.add(path[-1])

            node_size = 400

            if objective_nodes:
                obj_diameter = [n for n in objective_nodes if n in diameter_nodes]
                obj_regular = [n for n in objective_nodes if n not in diameter_nodes]
                if obj_regular:
                    nx.draw_networkx_nodes(
                        G, pos, nodelist=obj_regular, node_color='lightgreen',
                        node_shape='s', node_size=node_size * 1.2, alpha=0.8, ax=ax,
                    )
                if obj_diameter:
                    nx.draw_networkx_nodes(
                        G, pos, nodelist=obj_diameter, node_color='darkgreen',
                        node_shape='s', node_size=node_size * 1.4, alpha=0.9, ax=ax,
                        edgecolors='orange', linewidths=3,
                    )

            if variable_nodes:
                var_diameter = [n for n in variable_nodes if n in diameter_nodes]
                var_regular = [n for n in variable_nodes if n not in diameter_nodes]
                if var_regular:
                    nx.draw_networkx_nodes(
                        G, pos, nodelist=var_regular, node_color='lightblue',
                        node_shape='o', node_size=node_size, alpha=0.8, ax=ax,
                    )
                if var_diameter:
                    nx.draw_networkx_nodes(
                        G, pos, nodelist=var_diameter, node_color='darkblue',
                        node_shape='o', node_size=node_size * 1.2, alpha=0.9, ax=ax,
                        edgecolors='orange', linewidths=3,
                    )

            if constraint_nodes:
                con_diameter = [n for n in constraint_nodes if n in diameter_nodes]
                con_regular = [n for n in constraint_nodes if n not in diameter_nodes]
                if con_regular:
                    nx.draw_networkx_nodes(
                        G, pos, nodelist=con_regular, node_color='lightcoral',
                        node_shape='D', node_size=node_size, alpha=0.8, ax=ax,
                    )
                if con_diameter:
                    nx.draw_networkx_nodes(
                        G, pos, nodelist=con_diameter, node_color='darkred',
                        node_shape='D', node_size=node_size * 1.2, alpha=0.9, ax=ax,
                        edgecolors='orange', linewidths=3,
                    )

            nx.draw_networkx_labels(
                G, pos, labels=node_labels,
                font_size=7, font_weight='bold', ax=ax,
            )
            ax.axis('off')

    for col_idx, paper in enumerate(unique_papers):
        axes[0][col_idx].annotate(
            paper, xy=(0.5, 1.15), xycoords='axes fraction',
            ha='center', va='bottom', fontsize=12, fontweight='bold',
            bbox=dict(boxstyle="round,pad=0.3", facecolor='lightblue', alpha=0.7),
        )

    for row_idx, selection_reason in enumerate(unique_selection_reasons):
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
        Line2D([0], [0], color='gray', linewidth=2, label='Regular Edges'),
        Line2D([0], [0], color='orange', linewidth=4, label='Diameter Path'),
        Line2D([0], [0], marker='o', color='w', markerfacecolor='darkblue',
               markeredgecolor='orange', markeredgewidth=2, markersize=8,
               label='Diameter Endpoints'),
    ]
    fig.legend(handles=legend_elements, loc='upper right', bbox_to_anchor=(0.98, 0.98))
    plt.tight_layout()
    plt.show()
