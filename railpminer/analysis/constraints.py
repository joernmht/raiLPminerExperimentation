"""Constraint type classification and table generation."""

import ast
import os
import re
from typing import Any, Dict, List

import pandas as pd


def classify_constraint_types(nodes: List[Dict]) -> Dict[str, Any]:
    """Classify each constraint node by type using keyword matching.

    .. warning::
       **Known limitation (keyword matching).** This classifier is a
       lexical screen: it only fires when a constraint's name / equation /
       description contains one of the predefined keywords.  LLMs frequently
       express a valid constraint with synonymous wording that is not in the
       keyword list (e.g. "minimum separation" instead of "headway", "buffer
       time" instead of "dwell"), so the *true* domain relevance of the
       generated models is systematically **undercounted**.  The figures it
       produces are therefore a lower bound on domain coverage, never a
       correctness statement, and they are reported as such.  Embedding- or
       LLM-based semantic matching is left as future work.

    Args:
        nodes: List of node dictionaries (or string representation from CSV).

    Returns:
        Dictionary with ``classification_matrix``, ``type_counts``,
        ``total_constraints``, ``classified_constraints``.
    """
    if isinstance(nodes, str):
        try:
            nodes = ast.literal_eval(nodes)
        except Exception as e:
            print(f"Error parsing nodes string: {e}")
            return {
                'classification_matrix': [],
                'type_counts': {},
                'total_constraints': 0,
                'classified_constraints': 0,
            }

    if not nodes:
        return {
            'classification_matrix': [],
            'type_counts': {},
            'total_constraints': 0,
            'classified_constraints': 0,
        }

    constraint_type_keywords = {
        'ordering': [
            r'\bprecedence\b', r'\border\b', r'\bordering\b',
            r'\bovertaking\b', r'\bovertake\b', r'\bre-?ordering\b',
        ],
        'routing': [
            r'\broute\s+selection\b', r'\brouting\b', r'\brerouting\b',
            r'\bre-?routing\b',
        ],
        'timing': [
            r'\bdepart(ure)?\b', r'\bdwell\b', r'\bdelay\b',
            r'\bscheduled\b', r'\brunning\s+time\b',
            r'\bminimum\s+duration\b', r'\bre-?timing\b',
        ],
        'cancellation': [
            r'\bcancel(l?ation|l?ed|l?ing)?\b',
            r'\btrain\s+service\s+balance\b',
            r'\bunbalanced\s+timetable\b',
        ],
        'headway': [
            r'\bheadway\b', r'\bconflict-free\b',
            r'\bincompatible\s+arc\b', r'\btrain\s+incompatibility\b',
        ],
        'capacity': [
            r'\binfrastructure\s+capacity\b', r'\btrack\s+capacity\b',
            r'\bstation\s+capacity\b', r'\bblock\s+section\b',
            r'\bsingle-?track\b', r'\bno-?store\b',
        ],
        'flow_balance': [
            r'\bflow\s+balance\b', r'\bflow\s+conservation\b',
        ],
        'big_m': [
            r'\blarge\s+constant\b', r'\bbig-?M\s+constraint\b',
            r'\bbig-?M\b',
        ],
        'passenger_connection': [
            r'\bminimum\s+transfer\s+time\b',
            r'\bpassenger\s+connection\b',
        ],
        'rolling_stock_connection': [
            r'\brolling\s+stock\s+connection\b',
        ],
    }

    constraint_nodes = [n for n in nodes if n.get('type') == 'constraint']
    classification_matrix = []
    constraint_type_counts = {ctype: 0 for ctype in constraint_type_keywords}

    for constraint in constraint_nodes:
        search_text = (
            f"{constraint.get('name', '')} "
            f"{constraint.get('equation', '')} "
            f"{constraint.get('description', '')}"
        )

        matched_types = []
        type_match_dict = {}

        for ctype, keywords in constraint_type_keywords.items():
            pattern = '|'.join(keywords)
            if re.search(pattern, search_text, re.IGNORECASE):
                matched_types.append(ctype)
                type_match_dict[ctype] = 1
                constraint_type_counts[ctype] += 1
            else:
                type_match_dict[ctype] = 0

        classification_matrix.append({
            'constraint_number': constraint.get('number'),
            'constraint_name': constraint.get('name', ''),
            'matched_types': matched_types,
            'type_vector': type_match_dict,
            'is_classified': len(matched_types) > 0,
        })

    return {
        'classification_matrix': classification_matrix,
        'type_counts': constraint_type_counts,
        'total_constraints': len(constraint_nodes),
        'classified_constraints': sum(
            1 for c in classification_matrix if c['is_classified']
        ),
    }


def add_constraint_classification(df: pd.DataFrame) -> pd.DataFrame:
    """Add constraint type classification to the DataFrame.

    Run this as a separate step after :func:`process_lp_dataframe`.

    Args:
        df: DataFrame with ``nodes`` column.

    Returns:
        DataFrame with added ``constraint_classification`` and
        ``constraint_type_summary`` columns.
    """
    df_copy = df.copy()
    df_copy['constraint_classification'] = None
    df_copy['constraint_type_summary'] = None

    print(f"Classifying constraints for {len(df_copy)} models...")

    for idx in df_copy.index:
        print(f"Processing model {idx + 1}/{len(df_copy)}", end='\r')

        nodes = df_copy.loc[idx, 'nodes']

        if idx == df_copy.index[0]:
            print(f"\nDebug first row - nodes type: {type(nodes)}")

        if isinstance(nodes, str):
            try:
                nodes = ast.literal_eval(nodes)
            except Exception:
                print(f"\nWarning: Could not parse nodes for row {idx}")
                df_copy.at[idx, 'constraint_classification'] = []
                df_copy.at[idx, 'constraint_type_summary'] = {}
                continue

        if nodes and isinstance(nodes, list):
            try:
                classification = classify_constraint_types(nodes)
                df_copy.at[idx, 'constraint_classification'] = (
                    classification['classification_matrix']
                )
                df_copy.at[idx, 'constraint_type_summary'] = (
                    classification['type_counts']
                )
            except Exception as e:
                print(f"\nError classifying constraints for row {idx}: {e}")
                import traceback
                traceback.print_exc()
                df_copy.at[idx, 'constraint_classification'] = []
                df_copy.at[idx, 'constraint_type_summary'] = {}
        else:
            df_copy.at[idx, 'constraint_classification'] = []
            df_copy.at[idx, 'constraint_type_summary'] = {}

    print("\nConstraint classification complete!")
    return df_copy


def generate_constraint_tables_per_paper(
    df,
    selection_row,
    reference_csv_path='tables/constraint_reference.csv',
    output_name='constraint_table_output.csv',
    suffix="default",
):
    """Generate constraint coverage tables for each paper.

    Args:
        df: DataFrame with ``constraint_type_summary`` and ``paper`` columns.
        selection_row: Column containing selection reasons.
        reference_csv_path: Path to the reference constraint CSV.
        output_name: Base name for output CSV files.
        suffix: Suffix appended to output file names.

    Returns:
        Dictionary mapping paper names to constraint tables.
    """
    reference_dir = os.path.dirname(reference_csv_path)
    ref_df = pd.read_csv(reference_csv_path, index_col='Constraint Type')

    if df['constraint_type_summary'].dtype == 'object':
        df = df.copy()
        df['constraint_type_summary'] = df['constraint_type_summary'].apply(
            lambda x: ast.literal_eval(x) if isinstance(x, str) else x
        )

    constraint_types = {
        'ordering': '(Re-)Ordering',
        'routing': '(Re-)Routing',
        'timing': '(Re-)Timing',
        'cancellation': 'Cancelling',
        'headway': 'Headway',
        'capacity': 'Capacity',
        'flow_balance': 'Flow Balance',
        'big_m': 'Big-M',
        'passenger_connection': 'Passenger connection',
        'rolling_stock_connection': 'Rolling Stock connection',
    }

    attribute_columns = [
        'Low Minimal Size',
        'High Graph Diameter',
        'High Minimal Size',
        'High Constraint Variable Ratio',
    ]

    paper_tables = {}
    papers = ['Paper_1', 'Paper_2', 'Paper_3', 'Paper_4', 'Paper_5']

    for paper in papers:
        paper_df = df[df['paper'] == paper]

        result = pd.DataFrame(
            index=constraint_types.values(), columns=attribute_columns
        )
        result.index.name = 'Constraint Type'

        for _, row in paper_df.iterrows():
            attribute = row[selection_row]
            if attribute in attribute_columns:
                constraint_summary = row['constraint_type_summary']
                for constraint_key, constraint_name in constraint_types.items():
                    if constraint_summary.get(constraint_key, 0) > 0:
                        result.loc[constraint_name, attribute] = 'x'
                    else:
                        result.loc[constraint_name, attribute] = ''

        result = result.fillna('')

        if paper in ref_df.columns:
            ref_series = ref_df[paper].reindex(result.index).fillna('')
            result.insert(0, paper, ref_series)

        paper_tables[paper] = result

    for paper, table in paper_tables.items():
        out_path = os.path.join(
            reference_dir,
            f"{os.path.splitext(output_name)[0]}_{paper}_{suffix}.csv",
        )
        table.to_csv(out_path)

    return paper_tables
