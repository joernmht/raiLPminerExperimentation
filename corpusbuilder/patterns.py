"""Named structural patterns (knapsack, assignment, covering, ...) in canonical models.

The WL similarity in :mod:`corpusbuilder.wlcluster` answers "how alike are
two whole models"; it cannot say *what* a model contains, and a knapsack row
inside a large railway model is diluted by everything around it. This module
answers the other question: which textbook building blocks a canonical model
is made of, row by row, so the answer can be checked by hand.

Detection is purely structural (ADR-0024). It reads only what the schema
graph encodes (variable domain, parameter kind, aggregation operator, term
role) plus the two attributes the WL labels leave out: the comparator and the
effective sign of each term. It never reads names, descriptions, the
constraint ``kind`` or the ``domain_class`` annotations; those are reported
next to each hit as an independent cross-check, never used to find it.

Each constraint is first brought into one normal form::

    sum_k a_k x_k  (<=|>=|=)  b

variables on the left with their effective sign (side x sign x sign of a
numeric coefficient), constants (parameters and literals) on the right, and
the row multiplied by -1 when every variable term is negative. Row patterns
are predicates over that form; model patterns combine row hits with the
objective.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.patterns [--include-generated] \
        [--out corpus/patterns]
"""

from __future__ import annotations

# ruff: noqa: I001 — railpminer._lp2graph is a path-shim side effect and must
# precede the lp2graph imports (same posture as corpusbuilder.promote).

import argparse
import itertools
import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

from lp2graph.core.model import Formulation, Term

ROOT = Path(__file__).resolve().parent.parent

PATTERN_VERSION = "patterns-2026.11.1"

FLIP = {"le": "ge", "ge": "le", "eq": "eq"}
INTEGRAL = {"binary", "integer"}
FRACTIONAL = {"continuous", "non_negative"}

# Human-readable names, in report order.
ROW_PATTERNS = {
    "partition_row": "exactly one: sum of 0-1 terms = 1",
    "packing_row": "at most one: sum of 0-1 terms <= 1",
    "covering_row": "at least one: sum of 0-1 terms >= 1",
    "cardinality_row": "count bound: unit sum of integral terms vs a constant",
    "knapsack_row": "knapsack: weighted sum of integral terms <= capacity",
    "flow_conservation_row": "flow conservation: sum in = sum out of one variable",
    "pesp_row": "periodic tension: t_j - t_i + T*k within bounds",
    "big_m_row": "big-M switch: binary times a big-M parameter",
    "difference_row": "difference: x - y vs a constant (precedence, headway)",
    "linking_row": "linking: x <= U*y or x >= L*y with a binary switch",
    "bound_row": "bound: one variable vs a constant (0 when none is written)",
    "escape_row": "exactly / at least one with an escape: sum x + c = 1, c a separate 0-1",
    "balance_row": "balance recursion: the same variable on both sides (c_j = c_j-1 + in - out)",
    "definition_row": "definition: one variable alone, defined by the other terms",
}
MODEL_PATTERNS = {
    "knapsack_problem": "knapsack problem: max weighted 0-1 sum s.t. knapsack rows",
    "assignment_block": "assignment: two exactly-one row families on one variable",
    "generalized_assignment": "generalized assignment: exactly-one + knapsack on one variable",
    "set_covering_problem": "set covering: min cost s.t. covering rows",
    "set_partitioning_problem": "set partitioning: min cost s.t. partition rows",
    "set_packing_problem": "set packing: max value s.t. packing rows",
    "disjunctive_scheduling": "disjunctive scheduling: big-M ordering + y_ij + y_ji = 1",
    "pesp": "periodic event scheduling (PESP)",
    "absolute_deviation": "absolute deviation: z >= e and z >= -e on the same expression",
    "min_max": "min-max epigraph: h >= w_k for all k, h minimized",
    "positive_part": "positive part: z >= t - p, z >= 0 penalized, defined by that row only",
    "logic_or": "logical OR: r >= literal for two or more 0-1 literals",
    "logic_and": "logical AND: r <= literal for two or more 0-1 literals",
    "two_sided_bound": "two-sided bound: lower and upper bound rows on one variable",
}


@dataclass(frozen=True)
class VarTerm:
    var: str
    domain: str
    sign: int  # effective sign after normal form
    coef: str  # "unit" | "num" | "param:<kind>"
    aggregated: bool
    over: tuple[str, ...]
    coef_ref: str | None = None  # parameter name of a symbolic coefficient


@dataclass(frozen=True)
class Row:
    name: str
    comparator: str
    vars: tuple[VarTerm, ...]
    consts: tuple[tuple[str, int, object], ...]  # (kind, sign, value) on the right
    flipped: bool
    kind: str  # annotation, cross-check only
    domain_class: str | None  # annotation, cross-check only
    quantified: tuple[str, ...] = field(default_factory=tuple)

    @property
    def rhs_is_one(self) -> bool:
        return (
            len(self.consts) == 1
            and self.consts[0][0] == "literal"
            and self.consts[0][1] * float(self.consts[0][2]) == 1.0
        )

    @property
    def rhs_is_zero(self) -> bool:
        return all(c[0] == "literal" and float(c[2]) == 0.0 for c in self.consts)

    @property
    def rhs_positive_constant(self) -> bool:
        """A non-empty right side whose every term is a positive constant."""
        if not self.consts:
            return False
        for k, s, v in self.consts:
            if k == "literal":
                if s * float(v) <= 0:
                    return False
            elif s < 0 or k.endswith("big_m"):
                return False
        return True


def _num(x: object) -> float | None:
    try:
        return float(x)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _coef(term: Term, params: dict[str, str]) -> tuple[str, int]:
    """(coefficient class, sign of a numeric coefficient)."""
    c = term.coefficient
    if c is None:
        return "unit", 1
    n = _num(c)
    if n is not None:
        if n == 0:
            return "num", 1
        return ("unit" if abs(n) == 1 else "num"), (1 if n > 0 else -1)
    return f"param:{params.get(str(c), '?')}", 1


def normal_form(f: Formulation, c) -> Row:
    domains = {v.name: v.domain for v in f.variables}
    params = {p.name: p.kind for p in f.parameters}
    vterms: list[VarTerm] = []
    consts: list[tuple[str, int, object]] = []
    for side, terms in ((1, c.lhs), (-1, c.rhs)):
        for t in terms:
            if t.ref_kind == "variable":
                cls, csign = _coef(t, params)
                vterms.append(
                    VarTerm(
                        var=t.ref,
                        domain=domains.get(t.ref, "?"),
                        sign=side * t.sign * csign,
                        coef=cls,
                        aggregated=t.operator == "sum",
                        over=tuple(t.operator_over),
                        coef_ref=str(t.coefficient) if cls.startswith("param:") else None,
                    )
                )
            elif t.ref_kind == "parameter":
                cls, csign = _coef(t, params)
                consts.append((f"param:{params.get(t.ref, '?')}", -side * t.sign * csign, t.ref))
            else:
                consts.append(("literal", -side * t.sign, t.coefficient))
    comparator = c.comparator
    flipped = bool(vterms) and all(v.sign < 0 for v in vterms)
    if flipped:
        vterms = [
            VarTerm(v.var, v.domain, -v.sign, v.coef, v.aggregated, v.over, v.coef_ref)
            for v in vterms
        ]
        consts = [(k, -s, val) for k, s, val in consts]
        comparator = FLIP[comparator]
    # Normal form keeps the comparator as read; equal rows with both signs
    # present stay as they are (flow and definition rows).
    return Row(
        name=c.name,
        comparator=comparator,
        vars=tuple(vterms),
        consts=tuple(consts),
        flipped=flipped,
        kind=c.kind,
        domain_class=c.domain_class,
        quantified=tuple(q.over for q in c.quantifiers),
    )


# ---------------------------------------------------------------------------
# Row patterns
# ---------------------------------------------------------------------------


def _zero_one_sum(row: Row, *, relaxed_ok: bool = True) -> bool:
    """Positive sum of 0-1 terms with unit (or incidence-matrix) weights.

    ``relaxed_ok`` admits the LP relaxation of a master problem
    (``lambda >= 0`` with incidence coefficients), reported as such.
    """
    if not row.vars or not all(v.sign > 0 for v in row.vars):
        return False
    if not (len(row.vars) >= 2 or any(v.aggregated for v in row.vars)):
        return False
    if any(v.coef not in ("unit", "param:matrix") for v in row.vars):
        return False
    if all(v.domain in INTEGRAL for v in row.vars):
        return True
    # LP relaxation of a master problem: every column fractional, none mixed in.
    return relaxed_ok and all(v.domain in FRACTIONAL for v in row.vars)


def _is_big_m(row: Row, v: VarTerm) -> bool:
    """A binary whose coefficient is a big-M: declared ``big_m``, or the
    M(1 - y) signature (the same parameter also stands alone in the row)."""
    if v.domain != "binary":
        return False
    if v.coef == "param:big_m":
        return True
    return v.coef_ref is not None and any(
        k.startswith("param:") and val == v.coef_ref for k, _, val in row.consts
    )


def row_patterns(row: Row) -> list[str]:
    hits: list[str] = []
    vs = row.vars
    if not vs:
        return hits
    names = [v.var for v in vs]

    if row.rhs_is_one and _zero_one_sum(row):
        hits.append(
            {"eq": "partition_row", "le": "packing_row", "ge": "covering_row"}[row.comparator]
        )
    elif (
        all(v.sign > 0 and v.coef == "unit" and v.domain in INTEGRAL for v in vs)
        and (len(vs) >= 2 or any(v.aggregated for v in vs))
        and row.consts
        and not row.rhs_is_one
        and row.rhs_positive_constant
    ):
        hits.append("cardinality_row")

    if (
        row.comparator == "le"
        and all(v.sign > 0 and v.domain in INTEGRAL for v in vs)
        and any(v.coef in ("num", "param:scalar", "param:vector", "param:matrix") for v in vs)
        # A matrix coefficient against a right side of 1 is an incidence
        # matrix (packing row), against a capacity it is a weight.
        and not (row.rhs_is_one and all(v.coef in ("unit", "param:matrix") for v in vs))
        and (len(vs) >= 2 or any(v.aggregated for v in vs))
        and row.rhs_positive_constant
    ):
        hits.append("knapsack_row")

    if (
        row.comparator == "eq"
        and len(vs) == 2
        and names[0] == names[1]
        and all(v.aggregated and v.coef == "unit" for v in vs)
        and vs[0].sign != vs[1].sign
    ):
        hits.append("flow_conservation_row")

    ints = [v for v in vs if v.domain == "integer" and v.coef.startswith("param:")]
    times = [v for v in vs if v.domain in FRACTIONAL and v.coef == "unit" and not v.aggregated]
    if (
        row.comparator in ("le", "ge")
        and len(ints) == 1
        and len(times) == 2
        and len(vs) == 3
        and times[0].var == times[1].var
        and times[0].sign != times[1].sign
    ):
        hits.append("pesp_row")

    if any(_is_big_m(row, v) for v in vs) and any(not _is_big_m(row, v) for v in vs):
        hits.append("big_m_row")

    if (
        row.comparator in ("le", "ge")
        and len(vs) == 2
        and all(v.coef == "unit" and not v.aggregated and v.domain in FRACTIONAL for v in vs)
        and vs[0].sign != vs[1].sign
        and "pesp_row" not in hits
    ):
        hits.append("difference_row")

    if (
        row.comparator in ("le", "ge")
        and len(vs) == 2
        and not any(v.aggregated for v in vs)
        and vs[0].sign != vs[1].sign
        and row.rhs_is_zero
        and "difference_row" not in hits
        # x <= U*y or x >= L*y: one side is a binary switch.
        and any(v.domain == "binary" for v in vs)
    ):
        hits.append("linking_row")

    if (
        len(vs) == 1
        and not vs[0].aggregated
        and vs[0].coef == "unit"
        and all(not k.startswith("param:big_m") for k, _, _ in row.consts)
    ):
        hits.append("bound_row")

    once = [v for v in vs if sum(w.var == v.var for w in vs) == 1]
    if (
        row.comparator in ("eq", "ge")
        and any(h in hits for h in ("partition_row", "covering_row"))
        and any(v.aggregated for v in vs)
    ):
        agg_vars = {v.var for v in vs if v.aggregated}
        singles = [v for v in vs if not v.aggregated]
        if len(singles) == 1 and singles[0].var not in agg_vars:
            hits.append("escape_row")
    stock = [
        v.var
        for v in vs
        if sum(
            1 for w in vs if w.var == v.var and not w.aggregated and w.coef == "unit" and w.sign > 0
        )
        >= 1
        and sum(
            1 for w in vs if w.var == v.var and not w.aggregated and w.coef == "unit" and w.sign < 0
        )
        >= 1
    ]
    if (
        row.comparator == "eq"
        and stock
        and not _deviation_pair([v for v in vs if v.var not in stock])
    ):
        hits.append("balance_row")
    if (
        row.comparator == "eq"
        and len(vs) >= 2
        and any(v.coef == "unit" and not v.aggregated for v in once)
        and not set(hits)
        & {
            "partition_row",
            "packing_row",
            "covering_row",
            "cardinality_row",
            "flow_conservation_row",
            "balance_row",
        }
    ):
        hits.append("definition_row")
    return hits


def _deviation_pair(rest: list[VarTerm]) -> bool:
    """Two unit slacks of opposite sign on two non-negative variables (s+ - s-):
    the row is a soft (goal) row, not a balance."""
    return (
        len(rest) == 2
        and rest[0].var != rest[1].var
        and rest[0].sign != rest[1].sign
        and all(v.coef == "unit" and not v.aggregated and v.domain == "non_negative" for v in rest)
    )


def _literal(row: Row) -> float | None:
    """Net literal constant on the right side, or None if a parameter is there."""
    total = 0.0
    for k, sgn, val in row.consts:
        if k != "literal":
            return None
        total += sgn * float(val)
    return total


def oriented(row: Row, var: str):
    """The row solved for ``var``: (comparator, other terms with signs as seen
    from var's side, net literal or None, parameter constants), or None when
    var is not exactly one unit, non-aggregated term of the row."""
    mine = [v for v in row.vars if v.var == var]
    if len(mine) != 1 or mine[0].aggregated or mine[0].coef != "unit":
        return None
    s = mine[0].sign
    cmp = row.comparator if s > 0 else FLIP[row.comparator]
    others = [
        VarTerm(v.var, v.domain, v.sign * s, v.coef, v.aggregated, v.over, v.coef_ref)
        for v in row.vars
        if v.var != var
    ]
    lit = _literal(row)
    params = [(k, sgn * s, val) for k, sgn, val in row.consts if k != "literal"]
    return cmp, others, (None if lit is None else lit * s), params


# ---------------------------------------------------------------------------
# Model patterns
# ---------------------------------------------------------------------------


def _objective_vars(f: Formulation) -> dict[str, tuple[int, str]]:
    """variable -> (effective sign, coefficient class) in the objective."""
    if f.objective is None:
        return {}
    params = {p.name: p.kind for p in f.parameters}
    out: dict[str, tuple[int, str]] = {}
    for t in f.objective.terms:
        if t.ref_kind != "variable":
            continue
        cls, csign = _coef(t, params)
        out[t.ref] = (t.sign * csign, cls)
    return out


def model_patterns(f: Formulation, rows: list[tuple[Row, list[str]]]) -> list[dict]:
    by_pat: dict[str, list[Row]] = defaultdict(list)
    for row, hits in rows:
        for h in hits:
            by_pat[h].append(row)
    shapes = {v.name: tuple(v.shape) for v in f.variables}
    domains = {v.name: v.domain for v in f.variables}
    obj = _objective_vars(f)
    sense = f.objective.sense if f.objective is not None else None
    found: list[dict] = []

    def rows_on(pat: str, var: str) -> list[Row]:
        return [r for r in by_pat.get(pat, []) if var in {v.var for v in r.vars}]

    # Knapsack problem: maximize a positive weighted sum of the same 0-1 /
    # integer variable that knapsack rows constrain.
    for var in sorted({v.var for r in by_pat.get("knapsack_row", []) for v in r.vars}):
        ks = rows_on("knapsack_row", var)
        if sense == "max" and var in obj and obj[var][0] > 0 and obj[var][1] != "unit":
            found.append(
                {
                    "pattern": "knapsack_problem",
                    "variable": var,
                    "variant": (
                        ("0-1" if domains[var] == "binary" else "integer")
                        + (" multi-dimensional" if len(ks) > 1 else "")
                    ),
                    "rows": [r.name for r in ks],
                }
            )

    # Assignment: two exactly-one families on one binary variable.
    for var in sorted({v.var for r in by_pat.get("partition_row", []) for v in r.vars}):
        ps = rows_on("partition_row", var)
        if len(ps) < 2 or domains.get(var) != "binary":
            continue
        overs = {tuple(v.over for v in r.vars if v.var == var) for r in ps}
        quants = {r.quantified for r in ps}
        shape = set(shapes.get(var, ()))
        summed = {i for o in overs for t in o for i in t}
        if len(overs) >= 2 and len(shape) >= 2 and summed <= shape:
            variant = "classical (rows sum different shape indices)"
        elif len(overs) == 1 and len(quants) == 1:
            variant = "degree pair (in/out rows on arcs, same index sets)"
        else:
            variant = ""
        if variant:
            found.append(
                {
                    "pattern": "assignment_block",
                    "variable": var,
                    "variant": variant,
                    "rows": [r.name for r in ps],
                }
            )
        ks = rows_on("knapsack_row", var)
        if ks:
            found.append(
                {
                    "pattern": "generalized_assignment",
                    "variable": var,
                    "variant": "",
                    "rows": [r.name for r in ps + ks],
                }
            )
    if not any(m["pattern"] == "generalized_assignment" for m in found):
        for var in sorted({v.var for r in by_pat.get("partition_row", []) for v in r.vars}):
            ps = rows_on("partition_row", var)
            ks = rows_on("knapsack_row", var)
            caps = [r for r in rows_on("cardinality_row", var) if r.comparator == "le"]
            if len(ps) == 1 and (ks or caps):
                found.append(
                    {
                        "pattern": "generalized_assignment",
                        "variable": var,
                        "variant": "weighted" if ks else "unit weights (capacitated assignment)",
                        "rows": [ps[0].name] + [r.name for r in ks + caps],
                    }
                )

    # Set covering / partitioning / packing problems: the objective prices the
    # same variable the rows count.
    for pat, model_pat, want in (
        ("covering_row", "set_covering_problem", "min"),
        ("partition_row", "set_partitioning_problem", "min"),
        ("packing_row", "set_packing_problem", "max"),
    ):
        for var in sorted({v.var for r in by_pat.get(pat, []) for v in r.vars}):
            # The column variable is the one the rows sum over; a lone slack
            # (x + u = 1) does not make a partitioning problem of its own.
            rs = [
                r for r in rows_on(pat, var) if any(v.var == var and v.aggregated for v in r.vars)
            ]
            if sense == want and var in obj and rs:
                if pat == "covering_row" and domains.get(var) != "binary":
                    continue
                found.append(
                    {
                        "pattern": model_pat,
                        "variable": var,
                        "variant": "LP relaxation (master)"
                        if domains.get(var) in FRACTIONAL
                        else "",
                        "rows": [r.name for r in rs],
                    }
                )

    covered = {m["variable"] for m in found if m["pattern"] == "set_covering_problem"}
    for var in sorted({v.var for r in by_pat.get("cardinality_row", []) for v in r.vars}):
        rs = [r for r in rows_on("cardinality_row", var) if r.comparator == "ge"]
        if rs and sense == "min" and var in obj and var not in covered:
            found.append(
                {
                    "pattern": "set_covering_problem",
                    "variable": var,
                    "variant": "multi-cover (right side is a demand)"
                    + ("" if domains.get(var) == "binary" else ", integer"),
                    "rows": [r.name for r in rs],
                }
            )

    # Disjunctive scheduling: big-M ordering rows on a binary that also sits
    # in an exactly-one pair row (y_ij + y_ji = 1).
    for row in by_pat.get("big_m_row", []):
        bins = {v.var for v in row.vars if _is_big_m(row, v)}
        fr = [v for v in row.vars if v.domain in FRACTIONAL and not v.aggregated]
        if len(fr) == 2 and fr[0].var == fr[1].var and fr[0].sign != fr[1].sign:
            pair = [
                r
                for r in by_pat.get("partition_row", [])
                if {v.var for v in r.vars} <= bins and len(r.vars) == 2
            ]
            found.append(
                {
                    "pattern": "disjunctive_scheduling",
                    "variable": ",".join(sorted(bins)),
                    "variant": "with pair row" if pair else "ordering rows only",
                    "rows": [row.name] + [r.name for r in pair],
                }
            )

    found += _block_patterns(f, [r for r, _ in rows], by_pat, obj, sense, shapes)

    if by_pat.get("pesp_row"):
        found.append(
            {
                "pattern": "pesp",
                "variable": ",".join(sorted({v.var for r in by_pat["pesp_row"] for v in r.vars})),
                "variant": "",
                "rows": [r.name for r in by_pat["pesp_row"]],
            }
        )
    return found


def _block_patterns(f, rows, by_pat, obj, sense, shapes) -> list[dict]:
    """Multi-row patterns joined at one variable (absolute deviation, min-max,
    positive part, logic, two-sided bound)."""
    found: list[dict] = []
    variables = sorted({v.var for r in rows for v in r.vars})
    # absolute deviation: two rows z >= e, z >= -e
    abs_vars: set[str] = set()
    for z in variables:
        cands = []
        for r in rows:
            o = oriented(r, z)
            if o and o[0] == "ge" and o[1]:
                cands.append((r, o))
        for (r1, o1), (r2, o2) in itertools.combinations(cands, 2):
            k1 = sorted((v.var, v.aggregated, v.coef, -v.sign) for v in o1[1])
            k2 = sorted((v.var, v.aggregated, v.coef, v.sign) for v in o2[1])
            if k1 == k2:
                abs_vars.add(z)
                found.append(
                    {
                        "pattern": "absolute_deviation",
                        "variable": z,
                        "variant": "penalized in a min objective"
                        if sense == "min" and z in obj and obj[z][0] > 0
                        else "",
                        "rows": sorted([r1.name, r2.name]),
                    }
                )
    # min-max (or max-min): h >= w_k over more indices than h has, h optimized
    for h in variables:
        rs = []
        for r in rows:
            o = oriented(r, h)
            if not o or len(o[1]) != 1 or o[1][0].sign > 0 or o[1][0].aggregated:
                continue
            if not set(shapes.get(h, ())) < set(r.quantified):
                continue
            want = {"ge": "min", "le": "max"}.get(o[0])
            if want and sense == want and h in obj and obj[h][0] > 0:
                rs.append(r)
        if rs:
            found.append(
                {
                    "pattern": "min_max",
                    "variable": h,
                    "variant": "min-max" if sense == "min" else "max-min",
                    "rows": sorted(r.name for r in rs),
                }
            )
    # positive part: z >= t - p, z non-negative, penalized, defined by that row only
    minmax_vars = {m["variable"] for m in found if m["pattern"] == "min_max"}
    for z in variables:
        if (
            z in abs_vars
            or z in minmax_vars
            or f_domain(f, z) != "non_negative"
            or not (sense == "min" and z in obj and obj[z][0] > 0)
        ):
            continue
        on_z = [r for r in rows if any(v.var == z for v in r.vars)]
        defining = []
        for r in on_z:
            o = oriented(r, z)
            if (
                o
                and o[0] == "ge"
                and o[1]
                and any(v.sign < 0 and v.domain in FRACTIONAL for v in o[1])
            ):
                defining.append(r)
        others = [r for r in on_z if r not in defining and "bound_row" not in row_patterns(r)]
        if len(defining) == 1 and not others:
            found.append(
                {
                    "pattern": "positive_part",
                    "variable": z,
                    "variant": "",
                    "rows": [defining[0].name],
                }
            )
    # logic: r >= / <= 0-1 literals
    lits: dict[tuple[str, str], list[tuple[Row, tuple[str, bool]]]] = defaultdict(list)
    for r in rows:
        if (
            len(r.vars) != 2
            or any(v.aggregated or v.coef != "unit" for v in r.vars)
            or r.comparator == "eq"
        ):
            continue
        for v in r.vars:
            o = oriented(r, v.var)
            if not o or o[3]:
                continue
            (b,) = o[1]
            if b.domain != "binary" or b.var == v.var:
                continue
            lit = o[2] or 0.0
            if (b.sign < 0 and lit == 0.0) or (b.sign > 0 and lit == 1.0):
                lits[(v.var, o[0])].append((r, (b.var, b.sign > 0)))
    for (v, cmp), entries in sorted(lits.items()):
        rs = [r for r, _ in entries]
        if len({literal for _, literal in entries}) >= 2:
            name = "logic_or" if cmp == "ge" else "logic_and"
            found.append(
                {"pattern": name, "variable": v, "variant": "", "rows": sorted(r.name for r in rs)}
            )
    # two-sided bound: a lower and an upper bound row on one variable
    for x in variables:
        bounds = [(r, oriented(r, x)) for r in by_pat.get("bound_row", []) if r.vars[0].var == x]
        lo = [r for r, o in bounds if o and o[0] == "ge"]
        up = [r for r, o in bounds if o and o[0] == "le"]
        pairs = [(a, b) for a in lo for b in up if a.quantified == b.quantified]
        if pairs:
            found.append(
                {
                    "pattern": "two_sided_bound",
                    "variable": x,
                    "variant": "",
                    "rows": sorted({n for a, b in pairs for n in (a.name, b.name)}),
                }
            )
    return found


def f_domain(f: Formulation, var: str) -> str:
    return next((v.domain for v in f.variables if v.name == var), "?")


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def detect(f: Formulation) -> dict:
    rows = [(r, row_patterns(r)) for r in (normal_form(f, c) for c in f.constraints)]
    return {
        "rows": [
            {
                "constraint": r.name,
                "patterns": hits,
                "comparator": r.comparator,
                "flipped": r.flipped,
                "annotated_kind": r.kind,
                "annotated_domain_class": r.domain_class,
            }
            for r, hits in rows
        ],
        "models": model_patterns(f, rows),
    }


def report(models: dict[str, Formulation]) -> dict:
    out = {k: detect(f) for k, f in sorted(models.items())}
    row_counts: dict[str, int] = defaultdict(int)
    model_counts: dict[str, int] = defaultdict(int)
    unmatched = 0
    n_rows = 0
    for d in out.values():
        for r in d["rows"]:
            n_rows += 1
            if not r["patterns"]:
                unmatched += 1
            for p in r["patterns"]:
                row_counts[p] += 1
        for p in {m["pattern"] for m in d["models"]}:
            model_counts[p] += 1
    return {
        "version": PATTERN_VERSION,
        "n_models": len(out),
        "n_rows": n_rows,
        "unmatched_rows": unmatched,
        "row_pattern_counts": {p: row_counts.get(p, 0) for p in ROW_PATTERNS},
        "model_pattern_counts": {p: model_counts.get(p, 0) for p in MODEL_PATTERNS},
        "models": out,
    }


def render_markdown(rep: dict) -> str:
    lines = [
        "# Structural patterns in the canonical models",
        "",
        f"`{rep['version']}`: {rep['n_models']} models, {rep['n_rows']} constraint rows, "
        f"{rep['unmatched_rows']} rows match no pattern. Detection reads structure only "
        "(domains, parameter kinds, aggregation, comparator, signs); the annotated "
        "kind / domain class is shown for cross-checking and is never used.",
        "",
        "## Model-level hits",
        "",
        "| Pattern | Models |",
        "|---|---|",
    ]
    for p, desc in MODEL_PATTERNS.items():
        who = [k for k, d in rep["models"].items() if any(m["pattern"] == p for m in d["models"])]
        lines.append(f"| {desc} | {', '.join(who) if who else '-'} |")
    lines += ["", "## Row-level counts", "", "| Pattern | Rows |", "|---|---|"]
    for p, desc in ROW_PATTERNS.items():
        lines.append(f"| {desc} | {rep['row_pattern_counts'][p]} |")
    lines += ["", "## Per model", ""]
    for k, d in rep["models"].items():
        lines.append(f"### {k}")
        lines.append("")
        for m in d["models"]:
            v = f" ({m['variant']})" if m["variant"] else ""
            lines.append(f"- **{m['pattern']}**{v} on `{m['variable']}`: {', '.join(m['rows'])}")
        for r in d["rows"]:
            pats = ", ".join(r["patterns"]) or "(none)"
            ann = r["annotated_kind"] + (
                f"/{r['annotated_domain_class']}" if r["annotated_domain_class"] else ""
            )
            lines.append(f"- `{r['constraint']}` {r['comparator']}: {pats}  [annotated {ann}]")
        lines.append("")
    return "\n".join(lines)


MACRO_NAMES = {
    "knapsack_problem": "Knapsack",
    "assignment_block": "Assignment",
    "generalized_assignment": "GenAssignment",
    "set_covering_problem": "Covering",
    "set_partitioning_problem": "Partitioning",
    "set_packing_problem": "Packing",
    "disjunctive_scheduling": "Disjunctive",
    "pesp": "Pesp",
    "absolute_deviation": "AbsDev",
    "min_max": "MinMax",
    "positive_part": "PosPart",
    "logic_or": "LogicOr",
    "logic_and": "LogicAnd",
    "two_sided_bound": "TwoSidedBound",
}


def macros(rep: dict) -> str:
    """LaTeX macros for the paper, from a report over the canonical models
    only (generated models are never mixed into the paper's numbers)."""
    models = rep["models"]
    papers = [k for k in models if k.startswith("10.")]
    with_hit = [k for k in models if models[k]["models"]]
    papers_hit = [k for k in papers if models[k]["models"]]
    row_hits = rep["n_rows"] - rep["unmatched_rows"]
    paper_rows = [r for k in papers for r in models[k]["rows"]]
    paper_rows_hit = sum(1 for r in paper_rows if r["patterns"])
    lines = [
        f"%% Auto-generated by corpusbuilder.patterns ({rep['version']}); do not edit.",
        "%% Canonical models only (seeds, promoted papers, repository models).",
        f"\\newcommand{{\\patModels}}{{{rep['n_models']}}}",
        f"\\newcommand{{\\patPaperModels}}{{{len(papers)}}}",
        f"\\newcommand{{\\patRows}}{{{rep['n_rows']}}}",
        f"\\newcommand{{\\patRowsMatched}}{{{row_hits}}}",
        f"\\newcommand{{\\patRowsUnmatched}}{{{rep['unmatched_rows']}}}",
        f"\\newcommand{{\\patRowPatterns}}{{{len(ROW_PATTERNS)}}}",
        f"\\newcommand{{\\patModelPatterns}}{{{len(MODEL_PATTERNS)}}}",
        f"\\newcommand{{\\patModelsWithHit}}{{{len(with_hit)}}}",
        f"\\newcommand{{\\patPaperModelsWithHit}}{{{len(papers_hit)}}}",
        f"\\newcommand{{\\patPaperRows}}{{{len(paper_rows)}}}",
        f"\\newcommand{{\\patPaperRowsMatched}}{{{paper_rows_hit}}}",
        f"\\newcommand{{\\patKnapsackRows}}{{{rep['row_pattern_counts']['knapsack_row']}}}",
    ]
    for p, name in MACRO_NAMES.items():
        lines.append(f"\\newcommand{{\\pat{name}}}{{{rep['model_pattern_counts'][p]}}}")
    for p in ROW_PATTERNS:
        name = "".join(w.capitalize() for w in p.removesuffix("_row").split("_"))
        n = sum(1 for r in paper_rows if p in r["patterns"])
        lines.append(f"\\newcommand{{\\patPaperRow{name}}}{{{n}}}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from corpusbuilder.wlcluster import load_universe

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--include-generated", action="store_true")
    ap.add_argument("--out", default=str(ROOT / "corpus" / "patterns"))
    args = ap.parse_args(argv)
    rep = report(load_universe(args.include_generated))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / "patterns.json", "w", encoding="utf-8", newline="\n") as fh:
        json.dump(rep, fh, indent=1, sort_keys=False, default=str)
        fh.write("\n")
    with open(out / "patterns.md", "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_markdown(rep) + "\n")
    print(
        f"{rep['n_models']} models, {rep['n_rows']} rows, {rep['unmatched_rows']} unmatched; "
        f"models: { {k: v for k, v in rep['model_pattern_counts'].items() if v} }"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
