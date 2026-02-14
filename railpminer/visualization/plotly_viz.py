"""Interactive Plotly-based matrix visualization with hover information."""

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from railpminer.visualization.diameter import find_graph_diameter_and_path
from railpminer.visualization.graphviz_utils import create_manual_tree_layout


def create_plotly_matrix_with_hover(final_df: pd.DataFrame):
    """Create interactive matrix visualization with hover text using Plotly.

    Args:
        final_df: DataFrame with ``paper``, ``selection_reason``,
                  ``nodes``, ``connections``.
    """
    unique_papers = sorted(final_df['paper'].unique())
    unique_selection_reasons = sorted(final_df['selection_reason'].unique())

    n_rows = len(unique_selection_reasons)
    n_cols = len(unique_papers)

    fig = make_subplots(
        rows=n_rows, cols=n_cols,
        subplot_titles=[
            f"{paper}<br>{reason}"
            for reason in unique_selection_reasons
            for paper in unique_papers
        ],
        vertical_spacing=0.1,
        horizontal_spacing=0.1,
    )

    for row_idx, selection_reason in enumerate(unique_selection_reasons):
        for col_idx, paper in enumerate(unique_papers):
            matching_rows = final_df[
                (final_df['paper'] == paper)
                & (final_df['selection_reason'] == selection_reason)
            ]

            if matching_rows.empty:
                fig.add_trace(
                    go.Scatter(
                        x=[0], y=[0], mode='text', text=['No Data'],
                        showlegend=False, textfont=dict(size=12),
                    ),
                    row=row_idx + 1, col=col_idx + 1,
                )
                continue

            row_data = matching_rows.iloc[0]
            nodes = row_data['nodes']
            connections = row_data['connections']

            if not nodes:
                fig.add_trace(
                    go.Scatter(
                        x=[0], y=[0], mode='text', text=['Empty Graph'],
                        showlegend=False, textfont=dict(size=12),
                    ),
                    row=row_idx + 1, col=col_idx + 1,
                )
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

            if len(G.nodes()) > 0:
                try:
                    pos = nx.nx_pydot.graphviz_layout(G, prog='dot')
                except Exception:
                    pos = create_manual_tree_layout(G)
            else:
                pos = {}

            diameter, diameter_edges, diameter_paths = find_graph_diameter_and_path(G)

            node_x, node_y = [], []
            node_text, node_hover = [], []
            node_colors, node_symbols, node_sizes = [], [], []

            for node in nodes:
                node_id = node['id']
                if node_id in pos:
                    x, y = pos[node_id]
                    node_x.append(x)
                    node_y.append(y)

                    hover_text = f"<b>{node['name']}</b><br>"
                    hover_text += f"Type: {node['type']}<br>"
                    hover_text += f"Number: {node['number']}<br>"

                    if 'equation' in node and node['equation']:
                        hover_text += f"Equation: {node['equation']}<br>"

                    diameter_node_set = set()
                    for path in diameter_paths:
                        if len(path) >= 2:
                            diameter_node_set.add(path[0])
                            diameter_node_set.add(path[-1])

                    if node_id in diameter_node_set:
                        hover_text += "<b>Diameter Endpoint</b><br>"

                    hover_text += f"Graph Diameter: {diameter}"
                    node_hover.append(hover_text)

                    if node['type'] == 'objective':
                        node_text.append('OBJ')
                        node_colors.append('lightgreen')
                        node_symbols.append('square')
                        node_sizes.append(20)
                    elif node['type'] == 'variable':
                        node_text.append(f"V{node['number']}")
                        node_colors.append('lightblue')
                        node_symbols.append('circle')
                        node_sizes.append(15)
                    else:
                        node_text.append(f"C{node['number']}")
                        node_colors.append('lightcoral')
                        node_symbols.append('diamond')
                        node_sizes.append(15)

            # Regular edges
            edge_x, edge_y = [], []
            for edge in G.edges():
                if edge[0] in pos and edge[1] in pos:
                    x0, y0 = pos[edge[0]]
                    x1, y1 = pos[edge[1]]
                    edge_x.extend([x0, x1, None])
                    edge_y.extend([y0, y1, None])

            if edge_x:
                fig.add_trace(
                    go.Scatter(
                        x=edge_x, y=edge_y, mode='lines',
                        line=dict(color='gray', width=2),
                        showlegend=False, hoverinfo='none',
                    ),
                    row=row_idx + 1, col=col_idx + 1,
                )

            # Diameter edges
            diameter_edge_x, diameter_edge_y = [], []
            for edge in G.edges():
                edge_tuple = tuple(sorted(edge))
                if edge_tuple in diameter_edges and edge[0] in pos and edge[1] in pos:
                    x0, y0 = pos[edge[0]]
                    x1, y1 = pos[edge[1]]
                    diameter_edge_x.extend([x0, x1, None])
                    diameter_edge_y.extend([y0, y1, None])

            if diameter_edge_x:
                fig.add_trace(
                    go.Scatter(
                        x=diameter_edge_x, y=diameter_edge_y, mode='lines',
                        line=dict(color='orange', width=2),
                        showlegend=False, hoverinfo='none',
                    ),
                    row=row_idx + 1, col=col_idx + 1,
                )

            if node_x:
                fig.add_trace(
                    go.Scatter(
                        x=node_x, y=node_y, mode='markers+text',
                        marker=dict(
                            size=node_sizes, color=node_colors,
                            symbol=node_symbols,
                            line=dict(width=2, color='black'),
                        ),
                        text=node_text, textposition="middle center",
                        hovertext=node_hover, hoverinfo='text',
                        showlegend=False,
                    ),
                    row=row_idx + 1, col=col_idx + 1,
                )

    fig.update_layout(
        title="Interactive Graph Matrix with Hover Information",
        showlegend=False,
        height=400 * n_rows,
        width=600 * n_cols,
    )

    for i in range(1, n_rows + 1):
        for j in range(1, n_cols + 1):
            fig.update_xaxes(
                showticklabels=False, showgrid=False, zeroline=False,
                row=i, col=j,
            )
            fig.update_yaxes(
                showticklabels=False, showgrid=False, zeroline=False,
                row=i, col=j,
            )

    fig.show()
