"""Check the Paper-1 "anatomy" figure against the canonical model it claims.

Every count, name and facet printed in ``figures/fig_lp2graph_anatomy_body.tex``
must be derivable from the canonical document

    corpus/repo_formulations/manual/Gurobi__modeling-examples__railway-dispatching-mip.tex

The figure is a claim about that document; this script is the proof.

Usage:  PYTHONPATH=src python3 anatomy_figure_check.py FIG.tex MODEL.tex
"""

from __future__ import annotations

import collections
import re
import sys

from lp2graph.codec.latex import from_canonical_latex, to_canonical_latex
from lp2graph.metrics import model_coherence, model_completeness
from lp2graph.views.schema import schema

FAILS: list[str] = []
OKS = 0


def check(label: str, got: object, want: object) -> None:
    global OKS
    if got == want:
        OKS += 1
    else:
        FAILS.append(f"{label}: figure says {want!r}, model says {got!r}")


def strip_colour(s: str) -> str:
    """Remove every \\lpX{...} colour wrapper, keeping its argument (nested too)."""
    while re.search(r"\\lp[iopvc]\{", s):
        s = _strip_once(s)
    return s


def _strip_once(s: str) -> str:
    out = []
    i = 0
    while i < len(s):
        m = re.match(r"\\lp[iopvc]\{", s[i:])
        if not m:
            out.append(s[i])
            i += 1
            continue
        i += m.end()
        depth = 1
        while i < len(s) and depth:
            if s[i] == "{":
                depth += 1
            elif s[i] == "}":
                depth -= 1
                if depth == 0:
                    i += 1
                    break
            out.append(s[i])
            i += 1
    return "".join(out)


def main(fig_path: str, model_path: str) -> int:
    fig = open(fig_path, encoding="utf-8").read()
    src = open(model_path, encoding="utf-8").read()
    f = from_canonical_latex(src)
    g = schema(f)

    # -- the model itself is sound -------------------------------------
    check("round-trip", from_canonical_latex(to_canonical_latex(f)).model_dump(), f.model_dump())
    check("completeness", model_completeness(f).value, 1)
    check("coherence", model_coherence(g).value, 1)

    terms = [t for c in f.constraints for t in c.lhs + c.rhs] + list(f.objective.terms)
    quants = [q for c in f.constraints for q in c.quantifiers]
    binds = [b for t in terms for b in t.bindings]

    # -- (b) the per-class counts --------------------------------------
    def figcount(cls: str) -> int:
        m = re.search(r"\\textbf\{" + cls + r"\}\s*\\emph\{\((\d+)\)\}", fig)
        assert m, f"count for {cls} not found in figure"
        return int(m.group(1))

    check("n indices", len(f.indices), figcount("Index family"))
    check("n parameters", len(f.parameters), figcount("Parameter"))
    check("n variables", len(f.variables), figcount("Variable template"))
    check("n constraints", len(f.constraints), figcount("Constraint template"))
    check("n quantifiers", len(quants), figcount("Quantifier"))
    check("n terms", len(terms), figcount("Term"))
    check("n bindings", len(binds), figcount("Binding"))
    check("n objectives", 1 if f.objective else 0, figcount("Objective"))

    # -- (b) the facet sub-counts --------------------------------------
    m = re.search(r"\\lpn\{ne\\_other\}\}, (\d+) of (\d+)\)", fig)
    check("restricted quantifiers",
          sum(1 for q in quants if q.restriction != "none"), int(m.group(1)))
    m = re.search(r"\((\d+) of 21, on \\lpn\{first\}, \\lpn\{last\}, \\lpn\{single\}\)", fig)
    where = [q for q in quants if q.where is not None]
    check("where quantifiers", len(where), int(m.group(1)))
    check("where parameters", sorted({q.where.parameter for q in where}),
          ["first", "last", "single"])
    m = re.search(r"\\emph\{(\d+) variable, (\d+) parameter, (\d+) literal\}", fig)
    kinds = collections.Counter(t.ref_kind for t in terms)
    check("term ref kinds", (kinds["variable"], kinds["parameter"], kinds["literal"]),
          tuple(int(x) for x in m.groups()))
    m = re.search(r"\\textbf\{\\lpn\{offset\}\} on the ordered family \((\d+) of (\d+)\)", fig)
    check("offset bindings", sum(1 for b in binds if b.offset), int(m.group(1)))
    check("bindings total (offset claim)", len(binds), int(m.group(2)))

    # -- (c) graph size and edge-type counts ---------------------------
    m = re.search(r"schema view: (\d+) nodes, (\d+) edges", fig)
    check("graph nodes", len(g.nodes), int(m.group(1)))
    check("graph edges", len(g.edges), int(m.group(2)))
    et = collections.Counter(e.type for e in g.edges)
    for key in ("var_in_constraint", "var_in_objective", "uses_index",
                "uses_parameter", "operator_input"):
        tex = "\\lpn{" + key.replace("_", "\\_") + "}"
        m = re.search(re.escape(tex) + r" \((\d+)\)", fig)
        assert m, f"edge-count claim for {key} not found in figure"
        check(f"edge {key}", et[key], int(m.group(1)))

    # -- every declared name is a real name ----------------------------
    declared = ({i.name for i in f.indices} | {p.name for p in f.parameters}
                | {v.name for v in f.variables} | {c.name for c in f.constraints})
    facets = {
        "ordered", "cyclic", "kind", "domain", "role", "sense", "combination",
        "modulo", "offset", "restriction", "where", "ref\\_kind", "sum", "min",
        "lhs", "rhs", "objective", "primary", "indicator", "binary", "linear",
        "capacity", "ordering", "big\\_m", "set\\_packing", "timing", "1",
        "non\\_negative", "time\\_duration", "penalty\\_bigM", "domain\\_class",
        "domain\\_role", "network\\_structure", "ordering\\_precedence",
        "ne\\_other", "var\\_in\\_constraint", "var\\_in\\_objective",
        "uses\\_index", "uses\\_parameter", "operator\\_input",
        "(R, r, 0)", "(R, r+1, +1)", "(I, j, 0)",
        "i", "j", "r", "operator", "coef.", "0",
    }
    unknown = []
    for tok in re.findall(r"\\lpn\{([^{}]*)\}", fig):
        bare = tok.replace("\\_", "_").split("[")[0]
        if tok in facets or bare in declared:
            continue
        if re.fullmatch(r"[a-zA-Z]+\[[a-zA-Z,]+\]", tok.replace("\\_", "_")):
            if bare in declared:
                continue
        unknown.append(tok)
    check("unknown \\lpn tokens", unknown, [])

    # -- (a) each algebra row matches its constraint template ----------
    body = strip_colour(fig.split(r"\begin{align*}")[1].split(r"\end{align*}")[0])
    rows = [r for r in body.split("\\\\") if r.strip()]
    check("n algebra rows", len(rows), len(f.constraints) + 1)
    cmap = {c.name: c for c in f.constraints}
    sym_re = re.compile(
        r"\\mathit\{(?P<mathit>[A-Za-z]+)\}"
        r"|(?P<tf>t)\^\{F\}"
        r"|(?P<sub>[A-Za-z])_\{"
        r"|(?<![A-Za-z}])(?P<bare>[A-Z])(?![A-Za-z_])"
    )
    for row in rows:
        name = re.search(r"\\textsf\{([^}]*)\}", row).group(1).replace("\\_", "_")
        cells = [x for x in row.split("&") if x.strip()]
        expr = cells[0]
        if name == f.objective.name:
            container, tail = f.objective, ""
            expr = next(c for c in cells if "\\sum" in c)
            check("[objective] sense in row", f.objective.sense,
                  "min" if "\\min" in row else "max" if "\\max" in row else "?")
            check("[objective] aggregations", len(f.objective.terms),
                  expr.count("\\sum"))
        else:
            container = cmap.get(name)
            if container is None:
                FAILS.append(f"algebra row {name!r} is not a constraint of the model")
                continue
            tail = next((c for c in cells if "\\forall" in c), "")
            cmp_seen = ("ge" if r"\ge" in expr else "le" if r"\le" in expr
                        else "eq" if "=" in expr else "?")
            check(f"[{name}] comparator", container.comparator, cmp_seen)

        # symbols printed in the row vs. referents and symbolic coefficients
        # (aggregation binders and set names are checked as quantifiers, not here)
        expr = re.sub(r"\\sum_\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", " ", expr)
        expr = re.sub(r"\\mathcal\{[A-Za-z_]+\}", " ", expr)
        got = set()
        for m in sym_re.finditer(expr):
            got.add(m.group("mathit") or ("tF" if m.group("tf") else None)
                    or m.group("sub") or m.group("bare"))
        got.discard(None)
        cterms = (list(container.lhs) + list(container.rhs)
                  if not isinstance(container, type(f.objective)) else list(container.terms))
        want = {t.ref for t in cterms if t.ref_kind != "literal"}
        want |= {t.coefficient for t in cterms if isinstance(t.coefficient, str)}
        check(f"[{name}] symbols", sorted(want), sorted(got))

        # a literal term on the right must be printed as that number
        lits = [t for t in cterms if t.ref_kind == "literal"]
        if lits:
            check(f"[{name}] literal", str(int(lits[0].coefficient)),
                  (re.search(r"=\s*(\d+)\s*$", expr.strip()) or
                   re.search(r"(\d+)\s*$", expr.strip())).group(1))

        if tail:
            seen_q = re.findall(r"([a-z])\\in\\mathcal\{([A-Za-z_]+)\}", tail)
            check(f"[{name}] quantifiers",
                  [(q.index, q.over) for q in container.quantifiers], seen_q)
            check(f"[{name}] restriction",
                  any(q.restriction != "none" for q in container.quantifiers),
                  r"\neq" in tail)
            check(f"[{name}] where",
                  any(q.where is not None for q in container.quantifiers),
                  "{=}1" in row)

    print(f"{OKS} checks passed, {len(FAILS)} failed")
    for x in FAILS:
        print("  FAIL:", x)
    return 1 if FAILS else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1], sys.argv[2]))
