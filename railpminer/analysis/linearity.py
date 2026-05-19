"""Structural linearity heuristic.

Reviewer #4 noted that generated formulations sometimes contain non-linear
absolute-value objectives, products of variables, or min/max operators while
still being labelled "MILP".  This module flags such cases *from the text
alone*, before any solver is involved -- it is one of the early graph-screen
signals whose predictive value is later tested against the solver ground
truth.

It is a heuristic, not a proof: it scans every objective/constraint equation
for tokens that cannot appear in a linear expression, and for products of
two declared variable symbols.  False positives/negatives are possible; that
is exactly why its agreement with the solver outcome is *measured* rather
than trusted.
"""

import re
from typing import Dict, List, Tuple

# Tokens that never appear in a linear expression.
_NONLINEAR_TOKENS = [
    (r"\babs\s*\(", "abs()"),
    (r"\|[^|]+\|", "|.| absolute value"),
    (r"\bmax\s*\(", "max()"),
    (r"\bmin\s*\(", "min()"),
    (r"\bsqrt\s*\(", "sqrt()"),
    (r"\bexp\s*\(", "exp()"),
    (r"\blog\s*\(", "log()"),
    (r"\^\s*[2-9]", "power ^n"),
    (r"\*\*\s*[2-9]", "power **n"),
]


def _variable_symbols(nodes: List[Dict]) -> List[str]:
    return [
        n.get("abbreviation") or n.get("name", "")
        for n in nodes
        if n.get("type") == "variable"
    ]


def is_structurally_linear(nodes: List[Dict]) -> Tuple[bool, List[str]]:
    """Return ``(linear, reasons)`` for the formulation described by ``nodes``.

    ``linear`` is False if any equation contains a non-linear token or a
    product of two declared variable symbols (e.g. ``x_i * x_j``).
    """
    reasons: List[str] = []
    symbols = [re.escape(s) for s in _variable_symbols(nodes) if s]
    equations = [
        (n.get("name", n.get("id", "?")), n.get("equation", ""))
        for n in nodes
        if n.get("type") in ("objective", "constraint")
    ]

    if symbols:
        # var * var (allowing whitespace / coefficients between the symbols)
        sym_alt = "|".join(symbols)
        prod_re = re.compile(
            rf"(?<![A-Za-z0-9_])(?:{sym_alt})\s*\*\s*(?:{sym_alt})(?![A-Za-z0-9_])"
        )
    else:
        prod_re = None

    for label, eq in equations:
        for pattern, desc in _NONLINEAR_TOKENS:
            if re.search(pattern, eq, re.IGNORECASE):
                reasons.append(f"{label}: contains {desc}")
        if prod_re is not None and prod_re.search(eq):
            reasons.append(f"{label}: product of two variables")

    return (len(reasons) == 0), reasons
