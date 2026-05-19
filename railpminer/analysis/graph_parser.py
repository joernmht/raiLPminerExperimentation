"""Classified MILP -> bipartite-graph parsing (deterministic, no LLM).

Classification is embedded by generation and read back here deterministically
-- there is no a-posteriori keyword matching anywhere.

Two input shapes, one uniform classified graph:

1. **Strict classified text** (workflows ``SIM`` / ``TAF``)::

       PARAMETERS
       p1: <symbol> | <parameter_class> | <description>
       VARIABLES
       v1: <symbol> | <variable_class> | <domain> | <description>
       OBJECTIVE
       o: <min|max> | <objective_class> | <equation> | <description>
       CONSTRAINTS
       c1: <constraint_class> | <equation> | <description>

   Edges are inferred by whole-token symbol occurrence in the equations.

2. **Direct-code with an embedded ``CLASSIFICATION`` dict** (workflow
   ``DC``) -- the baseline emits PuLP code *and* a literal dict; we
   reverse-graph it from that dict (still deterministic, still parsed, not
   keyword-guessed).

Output per model: ``nodes`` (parameter/variable/objective/constraint, each
carrying ``domain_class``), ``connections`` (equation<->variable, kept for
the existing structural metrics), ``parameter_links`` (equation<->parameter),
``objective_sense`` and ``parse_ok``.
"""

import ast
import re
from typing import Any, Dict, List

import pandas as pd

from railpminer.analysis.taxonomy import normalize

_HEADERS = ("PARAMETERS", "VARIABLES", "OBJECTIVE", "CONSTRAINTS")
_SECTION_RE = re.compile(
    r"^\s*(" + "|".join(_HEADERS) + r")\s*$", re.IGNORECASE | re.MULTILINE
)
_VAR_RE = re.compile(r"^\s*v\d+\s*:\s*(?P<body>.+)$")
_PAR_RE = re.compile(r"^\s*p\d+\s*:\s*(?P<body>.+)$")
_OBJ_RE = re.compile(r"^\s*o\s*:\s*(?P<body>.+)$", re.IGNORECASE)
_CON_RE = re.compile(r"^\s*c\d+\s*:\s*(?P<body>.+)$")
_CLASSIFICATION_RE = re.compile(r"CLASSIFICATION\s*=\s*(\{.*?\n\})", re.DOTALL)


def _split_sections(text: str) -> Dict[str, str]:
    parts = _SECTION_RE.split(text)
    out = {}
    for i in range(1, len(parts) - 1, 2):
        out[parts[i].strip().upper()] = parts[i + 1]
    return out


def _fields(line: str) -> List[str]:
    return [f.strip() for f in line.split("|")]


def _token_re(symbol: str):
    return re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(symbol.strip())}(?![A-Za-z0-9_])"
    )


def _empty():
    return {"nodes": [], "connections": [], "parameter_links": [],
            "objective_sense": None, "parse_ok": False}


def parse_structured_text(text: str) -> Dict[str, Any]:
    """Parse the strict classified text layout deterministically."""
    sections = _split_sections(str(text))

    parameters, par_sym = [], {}
    for line in sections.get("PARAMETERS", "").splitlines():
        m = _PAR_RE.match(line)
        if not m:
            continue
        f = _fields(m.group("body"))
        if not f or not f[0]:
            continue
        num = len(parameters) + 1
        parameters.append({
            "number": num, "symbol": f[0],
            "domain_class": normalize("parameter", f[1] if len(f) > 1 else None),
            "description": f[2] if len(f) > 2 else "",
        })
        par_sym[num] = f[0]

    variables, var_sym = [], {}
    for line in sections.get("VARIABLES", "").splitlines():
        m = _VAR_RE.match(line)
        if not m:
            continue
        f = _fields(m.group("body"))
        if not f or not f[0]:
            continue
        num = len(variables) + 1
        variables.append({
            "number": num, "symbol": f[0],
            "domain_class": normalize("variable", f[1] if len(f) > 1 else None),
            "domain": f[2] if len(f) > 2 else "",
            "description": f[3] if len(f) > 3 else "",
        })
        var_sym[num] = f[0]

    objective, sense = None, None
    for line in sections.get("OBJECTIVE", "").splitlines():
        m = _OBJ_RE.match(line)
        if not m:
            continue
        f = _fields(m.group("body"))
        if len(f) < 3:
            continue
        sense = "max" if f[0].lower().startswith("max") else "min"
        objective = {
            "number": 0, "name": "objective", "sense": sense,
            "domain_class": normalize("objective", f[1]),
            "equation": f[2],
            "description": f[3] if len(f) > 3 else "",
        }
        break

    constraints = []
    for line in sections.get("CONSTRAINTS", "").splitlines():
        m = _CON_RE.match(line)
        if not m:
            continue
        f = _fields(m.group("body"))
        if len(f) < 2:
            continue
        constraints.append({
            "number": len(constraints) + 1,
            "name": f"c{len(constraints) + 1}",
            "domain_class": normalize("constraint", f[0]),
            "equation": f[1],
            "description": f[2] if len(f) > 2 else "",
        })

    return _assemble(parameters, var_sym, variables, objective, sense,
                     constraints, edge_mode="text",
                     var_sym=var_sym, par_sym=par_sym)


def parse_classification_dict(code_or_dict) -> Dict[str, Any]:
    """Parse the embedded ``CLASSIFICATION`` dict from DC code."""
    data = code_or_dict
    if isinstance(code_or_dict, str):
        m = _CLASSIFICATION_RE.search(code_or_dict)
        if not m:
            return _empty()
        try:
            data = ast.literal_eval(m.group(1))
        except Exception:
            return _empty()
    if not isinstance(data, dict):
        return _empty()

    parameters, par_sym = [], {}
    for i, p in enumerate(data.get("parameters", []), start=1):
        parameters.append({
            "number": i, "symbol": p.get("symbol", f"p{i}"),
            "domain_class": normalize("parameter", p.get("class")),
            "description": p.get("description", p.get("name", "")),
        })
        par_sym[i] = p.get("symbol", f"p{i}")

    variables, var_sym, sym_to_num = [], {}, {}
    for i, v in enumerate(data.get("variables", []), start=1):
        sym = v.get("symbol", f"v{i}")
        variables.append({
            "number": i, "symbol": sym,
            "domain_class": normalize("variable", v.get("class")),
            "domain": v.get("domain", ""),
            "description": v.get("description", v.get("name", "")),
        })
        var_sym[i] = sym
        sym_to_num[sym] = i
    par_to_num = {s: n for n, s in par_sym.items()}

    obj = data.get("objective", {}) or {}
    sense = "max" if str(obj.get("sense", "min")).lower().startswith("max") else "min"
    objective = {
        "number": 0, "name": "objective", "sense": sense,
        "domain_class": normalize("objective", obj.get("class")),
        "equation": obj.get("expr", ""),
        "description": obj.get("description", ""),
        "_vars": [sym_to_num[s] for s in obj.get("vars", []) if s in sym_to_num],
        "_params": [par_to_num[s] for s in obj.get("params", []) if s in par_to_num],
    } if obj else None

    constraints = []
    for i, c in enumerate(data.get("constraints", []), start=1):
        constraints.append({
            "number": i, "name": c.get("name", f"c{i}"),
            "domain_class": normalize("constraint", c.get("class")),
            "equation": c.get("expr", ""),
            "description": c.get("description", ""),
            "_vars": [sym_to_num[s] for s in c.get("vars", []) if s in sym_to_num],
            "_params": [par_to_num[s] for s in c.get("params", []) if s in par_to_num],
        })

    return _assemble(parameters, var_sym, variables, objective, sense,
                     constraints, edge_mode="explicit")


def _assemble(parameters, _vs, variables, objective, sense, constraints,
              edge_mode, var_sym=None, par_sym=None):
    nodes, connections, param_links = [], [], []

    for p in parameters:
        nodes.append({"id": f"par_{p['number']}", "type": "parameter", **p})
    for v in variables:
        nodes.append({"id": f"var_{v['number']}", "type": "variable", **v})

    if edge_mode == "text":
        v_rx = {n: _token_re(s) for n, s in (var_sym or {}).items()}
        p_rx = {n: _token_re(s) for n, s in (par_sym or {}).items()}

        def edges(eq):
            ev = [n for n, rx in v_rx.items() if rx.search(eq)]
            ep = [n for n, rx in p_rx.items() if rx.search(eq)]
            return ev, ep
    else:
        def edges_from(item):
            return item.get("_vars", []), item.get("_params", [])

    if objective is not None:
        clean = {k: v for k, v in objective.items() if not k.startswith("_")}
        nodes.append({"id": "obj_0", "type": "objective", **clean})
        ev, ep = (edges(objective["equation"]) if edge_mode == "text"
                  else edges_from(objective))
        connections += [[0, n] for n in ev]
        param_links += [[0, n] for n in ep]

    for c in constraints:
        clean = {k: v for k, v in c.items() if not k.startswith("_")}
        nodes.append({"id": f"const_{c['number']}", "type": "constraint", **clean})
        ev, ep = (edges(c["equation"]) if edge_mode == "text"
                  else edges_from(c))
        connections += [[c["number"], n] for n in ev]
        param_links += [[c["number"], n] for n in ep]

    parse_ok = (objective is not None and bool(variables)
                and bool(constraints))
    return {"nodes": nodes, "connections": connections,
            "parameter_links": param_links, "objective_sense": sense,
            "parse_ok": parse_ok}


def parse_milp(text: str) -> Dict[str, Any]:
    """Auto-detect the input shape and parse it deterministically."""
    text = str(text)
    if _SECTION_RE.search(text):
        return parse_structured_text(text)
    if _CLASSIFICATION_RE.search(text):
        return parse_classification_dict(text)
    return _empty()


def create_graph_columns(df: pd.DataFrame, column_name: str = "answer") -> pd.DataFrame:
    """Add ``nodes`` / ``connections`` / ``parameter_links`` /
    ``objective_sense`` / ``parse_ok`` columns by parsing ``column_name``."""
    df = df.copy()
    cols = {"nodes": [], "connections": [], "parameter_links": [],
            "objective_sense": [], "parse_ok": []}
    for _, val in df[column_name].items():
        parsed = parse_milp(val) if isinstance(val, str) else _empty()
        for k in cols:
            cols[k].append(parsed[k])
    for k, v in cols.items():
        df[k] = v
    return df
