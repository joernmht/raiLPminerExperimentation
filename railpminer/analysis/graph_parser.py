"""MILP text -> bipartite-graph parsing.

Reviewer concern addressed here
-------------------------------
The previous pipeline parsed every generated MILP with a *second* LLM, which
adds an unquantified layer of interpretation (false positives / negatives).

This module makes the **primary parser deterministic**:

* Generation agents emit the formulation in a strict delimited layout
  (``VARIABLES`` / ``OBJECTIVE`` / ``CONSTRAINTS`` blocks, one item per line,
  each variable carrying an explicit symbol).
* :func:`parse_structured_text` extracts the bipartite graph purely by string
  processing -- an edge exists between an equation and a variable iff the
  variable's symbol occurs as a whole token in the equation.  No model is
  called, so the parse is exactly reproducible.

The LLM parser is kept only as a comparison baseline, and
:func:`parser_agreement` reports how far the two disagree (node-count delta
and edge Jaccard), so the error introduced by the LLM layer is *measured*
instead of assumed away.
"""

import ast
import re
from dataclasses import dataclass
from typing import Any, Dict, List

import pandas as pd


# --------------------------------------------------------------------------
# Deterministic parser for the strict delimited generation format
# --------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"^\s*(VARIABLES|OBJECTIVE|CONSTRAINTS)\s*$", re.IGNORECASE | re.MULTILINE
)
# A variable line:  v1: <symbol> -- <domain> -- <description>
_VAR_RE = re.compile(r"^\s*v\d+\s*:\s*(?P<sym>[^-]+?)\s*--\s*(?P<rest>.*)$")
# An objective line: min|max : <equation> -- <description>
_OBJ_RE = re.compile(
    r"^\s*(?P<sense>min|max)\w*\s*:\s*(?P<eq>.+?)\s*(?:--\s*(?P<desc>.*))?$",
    re.IGNORECASE,
)
# A constraint line: c1: <equation> -- <description>
_CON_RE = re.compile(
    r"^\s*c\d+\s*:\s*(?P<eq>.+?)\s*(?:--\s*(?P<desc>.*))?$"
)


def _split_sections(text):
    """Return ``{section_name: body}`` for the three known sections."""
    parts, sections = _SECTION_RE.split(text), {}
    # parts = [pre, NAME, body, NAME, body, ...]
    for i in range(1, len(parts) - 1, 2):
        sections[parts[i].strip().upper()] = parts[i + 1]
    return sections


def _symbol_token_re(symbol):
    """Whole-token matcher for a variable symbol (handles subscripts)."""
    base = re.escape(symbol.strip())
    return re.compile(rf"(?<![A-Za-z0-9_]){base}(?![A-Za-z0-9_])")


def parse_structured_text(text: str) -> Dict[str, Any]:
    """Deterministically parse the strict delimited MILP layout.

    Returns ``{"nodes": [...], "connections": [...], "objective_sense": ...,
    "parse_ok": bool}``.  ``parse_ok`` is True iff at least an objective and
    one variable and one constraint were found.
    """
    text = str(text)
    sections = _split_sections(text)

    variables, var_symbols = [], {}
    for line in sections.get("VARIABLES", "").splitlines():
        m = _VAR_RE.match(line)
        if not m:
            continue
        num = len(variables) + 1
        sym = m.group("sym").strip()
        rest = m.group("rest").split("--")
        domain = rest[0].strip() if rest else ""
        desc = rest[1].strip() if len(rest) > 1 else ""
        variables.append(
            {"number": num, "abbreviation": sym, "name": sym,
             "description": f"{domain}; {desc}".strip("; ")}
        )
        var_symbols[num] = sym

    objective, objective_sense = None, None
    for line in sections.get("OBJECTIVE", "").splitlines():
        m = _OBJ_RE.match(line)
        if m and m.group("eq").strip():
            objective_sense = "min" if m.group("sense").lower().startswith(
                "min") else "max"
            objective = {
                "number": 0, "name": f"{objective_sense} objective",
                "equation": m.group("eq").strip(),
                "description": (m.group("desc") or "").strip(),
            }
            break

    constraints = []
    for line in sections.get("CONSTRAINTS", "").splitlines():
        m = _CON_RE.match(line)
        if not m or not m.group("eq").strip():
            continue
        constraints.append({
            "number": len(constraints) + 1,
            "name": f"c{len(constraints) + 1}",
            "equation": m.group("eq").strip(),
            "description": (m.group("desc") or "").strip(),
        })

    nodes, connections = [], []
    for v in variables:
        nodes.append({"id": f"var_{v['number']}", "type": "variable", **v})

    compiled = {n: _symbol_token_re(s) for n, s in var_symbols.items()}

    if objective is not None:
        nodes.append({"id": "obj_0", "type": "objective", **objective})
        for num, rx in compiled.items():
            if rx.search(objective["equation"]):
                connections.append([0, num])

    for c in constraints:
        nodes.append({"id": f"const_{c['number']}", "type": "constraint", **c})
        for num, rx in compiled.items():
            if rx.search(c["equation"]):
                connections.append([c["number"], num])

    parse_ok = objective is not None and bool(variables) and bool(constraints)
    return {
        "nodes": nodes,
        "connections": connections,
        "objective_sense": objective_sense,
        "variables": variables,
        "objective": objective,
        "constraints": constraints,
        "parse_ok": parse_ok,
    }


# --------------------------------------------------------------------------
# Legacy parser: the pydantic ``Model(...)`` repr produced by the LLM parser
# --------------------------------------------------------------------------

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
    """Evaluate a ``Model(...)`` repr with a restricted namespace."""
    safe_globals = {
        "Variable": _Variable,
        "ObjectiveFunction": _ObjectiveFunction,
        "Constraint": _Constraint,
        "__builtins__": {},
    }
    local_vars: Dict[str, Any] = {}
    words = [r"constraints=\[Constraint", "objective_function=ObjectiveFunction"]
    model_str = re.sub(r"(" + "|".join(words) + r")", r"\n\1", model_str)
    try:
        exec(model_str.strip(), safe_globals, local_vars)  # noqa: S102
        return {
            "variables": local_vars.get("variablesInModel", []),
            "objective": local_vars.get("objective_function", None),
            "constraints": local_vars.get("constraints", []),
        }
    except Exception as e:
        print(f"Error parsing legacy model repr: {e}")
        return {"variables": [], "objective": None, "constraints": []}


def parse_lp_model(model_str: str) -> Dict[str, Any]:
    """Parse a legacy ``Model(...)`` repr into nodes/connections."""
    parsed = safe_eval_model(str(model_str))
    variables = parsed["variables"]
    objective = parsed["objective"]
    constraints = parsed["constraints"]

    nodes, connections = [], []
    for var in variables:
        nodes.append({
            "id": f"var_{var.Number}", "type": "variable",
            "number": var.Number, "name": var.Name,
            "abbreviation": var.Abbreviation, "description": var.Description,
        })
    if objective:
        nodes.append({
            "id": "obj_0", "type": "objective", "number": 0,
            "name": objective.Name, "equation": objective.equation,
            "description": objective.description,
        })
        for var_num in getattr(objective, "VariablesIncluded", []) or []:
            connections.append([0, var_num])
    for c in constraints:
        nodes.append({
            "id": f"const_{c.Number}", "type": "constraint",
            "number": c.Number, "name": c.Name, "equation": c.equation,
            "description": c.description,
        })
        for var_num in getattr(c, "VariablesIncluded", []) or []:
            connections.append([c.Number, var_num])

    return {"nodes": nodes, "connections": connections,
            "variables": variables, "objective": objective,
            "constraints": constraints}


# --------------------------------------------------------------------------
# Unified entry point + LLM-parser agreement
# --------------------------------------------------------------------------

def parse_milp(text: str) -> Dict[str, Any]:
    """Parse a MILP, preferring the deterministic structured parser.

    Falls back to the legacy ``Model(...)`` repr parser when the text is not
    in the delimited layout (e.g. cached LLM-graph output from old runs).
    """
    text = str(text)
    if _SECTION_RE.search(text):
        return parse_structured_text(text)
    return parse_lp_model(text)


def _edge_set(connections):
    return {tuple(c) for c in connections}


def parser_agreement(det: Dict[str, Any], llm: Dict[str, Any]) -> Dict[str, float]:
    """Quantify disagreement between deterministic and LLM parsers.

    Returns variable/constraint count deltas and the Jaccard similarity of
    the edge sets.  Reported per row so the LLM-parser layer's error is a
    measured quantity, not an assumption.
    """
    def counts(p):
        return (
            sum(n["type"] == "variable" for n in p["nodes"]),
            sum(n["type"] == "constraint" for n in p["nodes"]),
        )

    dv, dc = counts(det)
    lv, lc = counts(llm)
    e_det, e_llm = _edge_set(det["connections"]), _edge_set(llm["connections"])
    union = e_det | e_llm
    jacc = len(e_det & e_llm) / len(union) if union else 1.0
    return {
        "var_count_delta": lv - dv,
        "constraint_count_delta": lc - dc,
        "edge_jaccard": jacc,
        "deterministic_vars": dv,
        "deterministic_constraints": dc,
    }


def create_graph_columns(df: pd.DataFrame, column_name: str = "answer") -> pd.DataFrame:
    """Add deterministic ``nodes`` / ``connections`` / ``objective_sense``
    / ``parse_ok`` columns by parsing ``column_name`` for every row."""
    df = df.copy()
    nodes, conns, senses, oks = [], [], [], []
    for _, val in df[column_name].items():
        if isinstance(val, str) and val.strip().startswith("[") and "{" in val:
            val = ast.literal_eval(val)  # pre-parsed nodes restored from CSV
        parsed = parse_milp(val) if isinstance(val, str) else {
            "nodes": val, "connections": [], "objective_sense": None,
            "parse_ok": bool(val)}
        nodes.append(parsed["nodes"])
        conns.append(parsed["connections"])
        senses.append(parsed.get("objective_sense"))
        oks.append(parsed.get("parse_ok", bool(parsed["nodes"])))
    df["nodes"] = nodes
    df["connections"] = conns
    df["objective_sense"] = senses
    df["parse_ok"] = oks
    return df
