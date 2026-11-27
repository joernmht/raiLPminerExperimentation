"""No promoted constraint may hold two sums that cancel each other.

A balance row sums one variable over two restricted sets (in-arcs minus
out-arcs). If a rewrite widens both sets to their base family, the two sums
name the same terms and the row says ``0 = 0``: it parses, counts as canonical
and means nothing. The rewrite rules refuse that (lp2graph ADR-0018); this
check looks at the result, so a new rule that reopens the hole is caught.

    PYTHONPATH=. python3 scripts/checks/cancelling_sums_check.py

Exit status 1 when a constraint of ``corpus/formulations`` holds a summed term
twice with opposite effective sign (same symbol, same indices, same sum scope,
same coefficient). Terms outside a sum are not reported: ``x - x`` after an
expansion of brackets is ordinary algebra.

A few source rows repeat a sum themselves (a derivation chain ``A = B + C =
B + D`` states ``B`` on both sides). Those are listed in ``SOURCE_REPEATS``
after reading the row; the model cannot tell them from a merge, the source can.
"""

# ruff: noqa: I001 — ``railpminer._lp2graph`` must run before the ``lp2graph`` imports.

from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401
from lp2graph.core.model import Formulation

FORMULATIONS = Path(__file__).resolve().parents[2] / "corpus" / "formulations"

#: (paper, source row) whose own text states one sum twice (checked 2026-11-27)
SOURCE_REPEATS = frozenset(
    {
        ("10.1016_j.omega.2024.103040", "eq_0022"),  # chain: ... = S_k + rest = S_k + mu
        ("10.1016_j.trc.2025.105076", "eq_0049"),  # the same chain, for y
        ("10.1016_j.trc.2025.105076", "eq_0050"),
        ("10.1016_j.trb.2016.07.006", "eq_0009"),  # a derivation that expands (v - v')(t - t')
    }
)
#: the source row a constraint came from: ``eq_0009_3`` and ``eq_0009_lo`` are parts of ``eq_0009``
_SOURCE_ROW = re.compile(r"eq_\d{4}(?:_[a-z](?![a-z]))?")


def cancelling_sums(formulation: Formulation) -> list[tuple[str, str]]:
    """``(constraint, symbol)`` for every summed term that cancels."""
    out: list[tuple[str, str]] = []
    for con in formulation.constraints:
        net: dict[tuple, int] = defaultdict(int)
        for side, terms in ((1, con.lhs), (-1, con.rhs)):
            for term in terms:
                if not term.operator_over:
                    continue
                key = (
                    str(term.coefficient),
                    term.ref,
                    tuple(b.expr for b in term.bindings),
                    tuple(str(q) for q in term.operator_over),
                    str(term.operator),
                )
                net[key] += side * term.sign
        out.extend((con.name, key[1]) for key, total in sorted(net.items()) if total == 0)
    return out


def main() -> int:
    found = 0
    for path in sorted(FORMULATIONS.glob("*.json")):
        formulation = Formulation.model_validate(json.loads(path.read_text(encoding="utf-8")))
        for constraint, symbol in cancelling_sums(formulation):
            row = _SOURCE_ROW.match(constraint)
            if (formulation.id, row.group(0) if row else constraint) in SOURCE_REPEATS:
                continue
            found += 1
            print(f"{formulation.id}: {constraint}: the sums over {symbol} cancel")
    print(f"{found} cancelling sum(s)")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
