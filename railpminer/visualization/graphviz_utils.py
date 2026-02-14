"""Graphviz setup and manual tree layout fallback."""

import os
import subprocess
from pathlib import Path


def setup_graphviz():
    """Ensure Graphviz is available on the system PATH.

    Returns:
        ``True`` if Graphviz ``dot`` command is available, ``False`` otherwise.
    """
    possible_paths = [
        r"C:\Program Files\Graphviz\bin",
        r"C:\Program Files (x86)\Graphviz\bin",
        r"C:\tools\Graphviz\bin",
    ]

    try:
        subprocess.run(['dot', '-V'], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    for path in possible_paths:
        if Path(path).exists():
            os.environ["PATH"] += os.pathsep + path
            try:
                subprocess.run(['dot', '-V'], capture_output=True, check=True)
                print(f"Graphviz found and added: {path}")
                return True
            except subprocess.CalledProcessError:
                continue

    return False


def create_manual_tree_layout(G):
    """Manual tree layout with objective at top, variables middle, constraints bottom.

    Args:
        G: NetworkX graph with node attribute ``type`` in
           {``"objective"``, ``"variable"``, ``"constraint"``}.

    Returns:
        Dictionary mapping node IDs to ``(x, y)`` positions.
    """
    pos = {}
    levels = {'objective': [], 'constraint': [], 'variable': []}

    for node_id in G.nodes():
        node_type = G.nodes[node_id]['type']
        levels[node_type].append(node_id)

    if levels['objective']:
        pos[levels['objective'][0]] = (0, 2)

    variable_count = len(levels['variable'])
    for i, node in enumerate(levels['variable']):
        x = (i - variable_count / 2 + 0.5) * 2
        pos[node] = (x, 1)

    constraint_count = len(levels['constraint'])
    for i, node in enumerate(levels['constraint']):
        x = (i - constraint_count / 2 + 0.5) * 3
        pos[node] = (x, 0)

    return pos
