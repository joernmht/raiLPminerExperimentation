"""LP model string parsing into graph representations."""

import re
from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd


# Dataclasses used by safe_eval_model — kept private to this module
@dataclass
class _Variable:
    Number: int
    Abbreviation: str
    Name: str
    Description: str


@dataclass
class _ObjectiveFunction:
    Name: str
    Number: int
    equation: str
    description: str
    VariablesIncluded: List[int]


@dataclass
class _Constraint:
    Name: str
    Number: int
    equation: str
    description: str
    VariablesIncluded: List[int]


def safe_eval_model(model_str: str) -> Dict[str, Any]:
    """Safely evaluate the model string with proper class definitions.

    Uses ``exec`` with a restricted namespace containing only the
    dataclass definitions above — no builtins.

    Args:
        model_str: String representation of the LP model.

    Returns:
        Dictionary with ``variables``, ``objective``, and ``constraints``.
    """
    safe_globals = {
        'Variable': _Variable,
        'ObjectiveFunction': _ObjectiveFunction,
        'Constraint': _Constraint,
        '__builtins__': {},
    }
    local_vars = {}

    words = [r"constraints=\[Constraint", "objective_function=ObjectiveFunction"]
    pattern = r'(' + '|'.join(words) + ')'
    model_str = re.sub(pattern, r'\n\1', model_str)

    try:
        exec(model_str.strip(), safe_globals, local_vars)
        return {
            'variables': local_vars.get('variablesInModel', []),
            'objective': local_vars.get('objective_function', None),
            'constraints': local_vars.get('constraints', []),
        }
    except Exception as e:
        print(f"Error parsing model: {e}")
        print(model_str)
        return {'variables': [], 'objective': None, 'constraints': []}


def parse_lp_model(model_str: str) -> Dict[str, Any]:
    """Parse an LP model string to extract variables, objective, and constraints.

    Args:
        model_str: String representation of the LP model.

    Returns:
        Dictionary with ``nodes``, ``connections``, ``variables``,
        ``objective``, ``constraints``.
    """
    parsed_components = safe_eval_model(str(model_str))
    variables = parsed_components['variables']
    objective = parsed_components['objective']
    constraints = parsed_components['constraints']

    nodes = []
    connections = []

    for var in variables:
        nodes.append({
            'id': f'var_{var.Number}',
            'type': 'variable',
            'number': var.Number,
            'name': var.Name,
            'abbreviation': var.Abbreviation,
            'description': var.Description,
        })

    if objective:
        nodes.append({
            'id': 'obj_0',
            'type': 'objective',
            'number': 0,
            'name': objective.Name,
            'equation': objective.equation,
            'description': objective.description,
        })
        if hasattr(objective, 'VariablesIncluded') and objective.VariablesIncluded:
            for var_num in objective.VariablesIncluded:
                connections.append([0, var_num])

    for constraint in constraints:
        nodes.append({
            'id': f'const_{constraint.Number}',
            'type': 'constraint',
            'number': constraint.Number,
            'name': constraint.Name,
            'equation': constraint.equation,
            'description': constraint.description,
        })
        if hasattr(constraint, 'VariablesIncluded') and constraint.VariablesIncluded:
            for var_num in constraint.VariablesIncluded:
                connections.append([constraint.Number, var_num])

    return {
        "nodes": nodes,
        "connections": connections,
        "variables": variables,
        "objective": objective,
        "constraints": constraints,
    }


def create_graph_columns(df: pd.DataFrame, column_name: str = 'graph') -> pd.DataFrame:
    """Create graph representation columns for the DataFrame.

    Args:
        df: Input DataFrame with model strings.
        column_name: Name of the column containing model strings.

    Returns:
        DataFrame with added ``nodes`` and ``connections`` columns.
    """
    df_copy = df.copy()
    nodes_list = []
    connections_list = []

    print(f"Processing {len(df_copy)} models...")

    for idx, model_str in df_copy[column_name].items():
        print(f"Processing model {idx + 1}/{len(df_copy)}", end='\r')
        parsed = parse_lp_model(str(model_str))
        nodes_list.append(parsed["nodes"])
        connections_list.append(parsed["connections"])

    print("\nProcessing complete!")

    df_copy['nodes'] = nodes_list
    df_copy['connections'] = connections_list
    return df_copy
