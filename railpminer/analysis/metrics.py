"""Complexity metrics calculation and the main analysis pipeline."""

from typing import Dict, List, Tuple

import networkx as nx
import pandas as pd
from IPython.display import display

from railpminer.analysis.graph_parser import create_graph_columns


def calculate_complexity_metrics(
    nodes: List[Dict], connections: List[List],
) -> Dict[str, float]:
    """Calculate complexity metrics for an LP model.

    Args:
        nodes: List of node dictionaries.
        connections: List of ``[equation_number, variable_number]`` pairs.

    Returns:
        Dictionary with ``minimal_complexity``, ``graph_diameter``,
        ``constraint_variable_ratio``, ``model_coherence``,
        ``model_completeness``, ``constraint_count``, ``variable_count``.
    """
    n_variables = len([n for n in nodes if n['type'] == 'variable'])
    n_constraints = len([n for n in nodes if n['type'] == 'constraint'])
    n_objectives = len([n for n in nodes if n['type'] == 'objective'])

    G = nx.Graph()
    for node in nodes:
        G.add_node(node['id'])

    for conn in connections:
        eq_num, var_num = conn
        eq_node = None
        var_node = None
        for node in nodes:
            if node['type'] in ['objective', 'constraint'] and node['number'] == eq_num:
                eq_node = node['id']
            elif node['type'] == 'variable' and node['number'] == var_num:
                var_node = node['id']
        if eq_node and var_node:
            G.add_edge(eq_node, var_node)

    metrics = {}

    nV_min = n_variables if n_variables > 0 else 1
    nC_min = n_constraints if n_constraints > 0 else 1
    metrics['minimal_complexity'] = nV_min * nC_min
    metrics['constraint_count'] = n_constraints
    metrics['variable_count'] = n_variables

    if len(G.nodes()) <= 1:
        metrics['graph_diameter'] = 0
    elif nx.is_connected(G):
        metrics['graph_diameter'] = nx.diameter(G)
    else:
        components = list(nx.connected_components(G))
        if components:
            largest_component = max(components, key=len)
            subgraph = G.subgraph(largest_component)
            if len(subgraph.nodes()) > 1:
                metrics['graph_diameter'] = nx.diameter(subgraph)
            else:
                metrics['graph_diameter'] = 0
        else:
            metrics['graph_diameter'] = 0

    if n_variables > 0:
        metrics['constraint_variable_ratio'] = n_constraints / n_variables
    else:
        metrics['constraint_variable_ratio'] = (
            float('inf') if n_constraints > 0 else 0
        )

    metrics['model_coherence'] = (
        1 if (len(G.nodes()) <= 1 or nx.is_connected(G)) else 0
    )

    metrics['model_completeness'] = 1 if (
        n_objectives == 1
        and n_variables > 0
        and n_constraints > 0
        and all(
            len([c for c in connections if c[1] == var['number']]) >= 2
            for var in nodes
            if var['type'] == 'variable'
        )
    ) else 0

    return metrics


def add_complexity_metrics(df: pd.DataFrame) -> pd.DataFrame:
    """Add complexity metrics as separate columns to the DataFrame.

    Args:
        df: DataFrame with ``nodes`` and ``connections`` columns.

    Returns:
        DataFrame with added metric columns.
    """
    df_copy = df.copy()

    metric_names = [
        'minimal_complexity', 'graph_diameter', 'constraint_variable_ratio',
        'model_coherence', 'model_completeness', 'model_naivety',
    ]
    for metric in metric_names:
        df_copy[metric] = 0.0

    print(f"Calculating metrics for {len(df_copy)} models...")

    for idx in df_copy.index:
        print(f"Processing model {idx + 1}/{len(df_copy)}", end='\r')
        nodes = df_copy.loc[idx, 'nodes']
        connections = df_copy.loc[idx, 'connections']

        if nodes and isinstance(nodes, list):
            try:
                metrics = calculate_complexity_metrics(nodes, connections)
                for metric_name, value in metrics.items():
                    df_copy.loc[idx, metric_name] = value
            except Exception as e:
                print(f"\nError calculating metrics for row {idx}: {e}")
                for metric in metric_names:
                    df_copy.loc[idx, metric] = 0.0

    print("\nMetric calculation complete!")
    return df_copy


def process_lp_dataframe(
    df: pd.DataFrame, graph_column: str = 'graph',
) -> pd.DataFrame:
    """Complete processing pipeline for LP model DataFrame.

    1. Creates graph representations (nodes/connections).
    2. Calculates complexity metrics.

    Args:
        df: Input DataFrame with LP model strings.
        graph_column: Name of column containing model strings.

    Returns:
        Processed DataFrame with all analysis columns.
    """
    print("=== LP Model Processing Pipeline ===")
    print(f"Input DataFrame shape: {df.shape}")

    print("\n1. Creating graph representations...")
    df_processed = create_graph_columns(df, graph_column)

    print("\n2. Calculating complexity metrics...")
    df_processed = add_complexity_metrics(df_processed)

    print(f"\nFinal DataFrame shape: {df_processed.shape}")
    print("Processing complete!")
    return df_processed


def analyze_lp_models(
    df: pd.DataFrame,
    graph_column: str = 'graph',
    visualize_first: bool = True,
    show_summary: bool = True,
) -> pd.DataFrame:
    """One-stop function to analyze all LP models in a DataFrame.

    Args:
        df: DataFrame with LP model strings.
        graph_column: Name of column containing model strings.
        visualize_first: Whether to visualize the first model.
        show_summary: Whether to show summary statistics.

    Returns:
        Fully processed DataFrame.
    """
    df_processed = process_lp_dataframe(df, graph_column)

    if show_summary and len(df_processed) > 0:
        print("\n=== Summary Statistics ===")
        metric_cols = [
            'minimal_complexity', 'graph_diameter',
            'constraint_variable_ratio', 'model_coherence',
            'model_completeness', 'model_naivety',
        ]
        summary_stats = df_processed[metric_cols].describe()
        display(summary_stats)

    if visualize_first and len(df_processed) > 0:
        from railpminer.visualization.summary import display_model_summary
        from railpminer.visualization.circular import visualize_circular_graph

        print("\n=== First Model Visualization ===")
        display_model_summary(df_processed, 0)
        visualize_circular_graph(
            df_processed.iloc[0]['nodes'],
            df_processed.iloc[0]['connections'],
            "First Model Graph",
        )

    return df_processed
