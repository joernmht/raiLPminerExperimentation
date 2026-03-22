"""Model summary display for Jupyter notebooks."""

import pandas as pd
from IPython.display import HTML, display


def display_model_summary(df: pd.DataFrame, index: int = 0):
    """Display a summary of a specific model in a Jupyter notebook.

    Args:
        df: Processed DataFrame with ``nodes``, ``connections``, and metric columns.
        index: Index of the model to display.
    """
    if index >= len(df):
        print(f"Index {index} out of range. DataFrame has {len(df)} rows.")
        return

    row = df.iloc[index]
    nodes = row['nodes']
    connections = row['connections']

    n_vars = len([n for n in nodes if n['type'] == 'variable'])
    n_const = len([n for n in nodes if n['type'] == 'constraint'])
    n_obj = len([n for n in nodes if n['type'] == 'objective'])

    completeness_key = (
        'corrected_completeness'
        if 'corrected_completeness' in row.index
        else 'model_completeness'
    )

    html = f"""
    <div style="border: 2px solid #4CAF50; padding: 15px; margin: 10px; border-radius: 10px;">
        <h3 style="color: #4CAF50; margin-top: 0;">Model {index + 1} Summary</h3>
        <div style="display: flex; gap: 20px; margin: 15px 0;">
            <div style="background: #e3f2fd; padding: 10px; border-radius: 5px;">
                <strong>Variables:</strong> {n_vars}
            </div>
            <div style="background: #e8f5e8; padding: 10px; border-radius: 5px;">
                <strong>Objectives:</strong> {n_obj}
            </div>
            <div style="background: #fce4ec; padding: 10px; border-radius: 5px;">
                <strong>Constraints:</strong> {n_const}
            </div>
            <div style="background: #f3e5f5; padding: 10px; border-radius: 5px;">
                <strong>Connections:</strong> {len(connections)}
            </div>
        </div>
        <h4>Complexity Metrics:</h4>
        <div style="display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px;">
            <div><strong>Minimal Size:</strong> {row['minimal_size']:.2f}</div>
            <div><strong>Graph Diameter:</strong> {row['graph_diameter']:.0f}</div>
            <div><strong>Constraint/Variable Ratio:</strong> {row['constraint_variable_ratio']:.2f}</div>
            <div><strong>Model Coherence:</strong> {'V' if row['model_coherence'] == 1 else 'X'}</div>
            <div><strong>Model Completeness:</strong> {'V' if row[completeness_key] == 1 else 'X'}</div>
            <div><strong>Model Naivety:</strong> {'Not Naive' if row.get('model_naivety', 0) == 1 else 'Naive'}</div>
        </div>
    </div>
    """
    display(HTML(html))
