"""Number macros of Paper 1 (promotion, objective feature, rule census, search examples).

Reads the lab artifacts and four measurement files and prints LaTeX
\newcommand lines for the paper's prelim_corpus_macros.tex:

    PYTHONPATH=. python3 scripts/yield_probe.py run PROBE.json
    PYTHONPATH=. python3 scripts/rule_census.py census CENSUS.json
    PYTHONPATH=. python3 scripts/rule_census.py ablate CENSUS.json 5 ABL5.json
    PYTHONPATH=. python3 scripts/rule_census.py ablate CENSUS.json 10 ABL10.json
    PYTHONPATH=. python3 -m corpusbuilder.graphpatterns --out PATTERNS_DIR
    python3 scripts/paper1_macros.py PROBE.json CENSUS.json ABL5.json ABL10.json PATTERNS_DIR/graph_patterns.json

Needs the full (private) corpus. The output holds counts only.
"""

from __future__ import annotations

import json
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


probe, census_path, abl5, abl10, patterns_path = sys.argv[1:6]
LAB = Path("corpus")
rep = _load(LAB / "promotion.json")
papers = rep["papers"]
total = sum(p["rows"] for p in papers)
prom = [p for p in papers if p["promoted"]]
inc = {
    p["paper_key"]: (p["partial"]["rows_included"] if "partial" in p else p["rows"]) for p in prom
}
full = [p for p in prom if "partial" not in p]
cov = sorted(100 * inc[p["paper_key"]] / p["rows"] for p in prom if "partial" in p)
causes = Counter(p.get("cause") for p in papers if not p["promoted"])

census = _load(census_path)
WIDE = {"row_widened", "restricted_set_widen", "strict_relaxed"}
rows_all = [set(f) for rs in census.values() for f in rs.values()]
widened = sum(1 for f in rows_all if f & WIDE)
used = defaultdict(set)
for key, rs in census.items():
    for fired in rs.values():
        for r in fired:
            used[r].add(key)
rules_needed = sum(1 for r in used if not r.startswith("derived:"))
few = sum(1 for r, ps in used.items() if len(ps) <= 10)

derived = 0
for p in prom:
    f = _load(LAB / "formulations" / f"{p['entry_id']}.json")
    derived += sum(1 for q in f["parameters"] if (q.get("description") or "").startswith("derived"))

obj = defaultdict(lambda: [0, 0, 0])
for p in prom:
    s = (p.get("objective") or {}).get("status", "canonical")
    obj[s][0] += 1
    obj[s][1] += p["rows"]
    obj[s][2] += inc[p["paper_key"]]

R = _load(probe)
why = Counter()
for r in R:
    for x in r["rows"]:
        if not x["ok"]:
            why[re.sub(r"'[^']*'", "", x.get("why", ""))[:28]] += 1
undeclared = sum(v for k, v in why.items() if k.startswith("undeclared symbol"))
product = sum(v for k, v in why.items() if k.startswith("product of variables"))

# source properties
rows = []
for p in papers:
    if not p["rows"]:
        continue
    src = _load(LAB / "dossiers" / f"{p['paper_key']}.json")["source"]
    rows.append((inc.get(p["paper_key"], 0), p["rows"], src.get("cited_by_count") or 0))


def spearman(xs, ys):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2 + 1
            i = j + 1
        return r

    rx, ry = rank(xs), rank(ys)
    mx, my = st.mean(rx), st.mean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry, strict=True))
    return num / (sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry)) ** 0.5


corr = spearman([a / n for a, n, _c in rows], [c for _a, _n, c in rows])
cs = sorted(c for _a, _n, c in rows)
q1, q3 = cs[len(cs) // 4], cs[3 * len(cs) // 4]
bottom = [(a, n) for a, n, c in rows if c < q1]
top = [(a, n) for a, n, c in rows if c >= q3]
pct = lambda sel: 100 * sum(a for a, _n in sel) / sum(n for _a, n in sel)  # noqa: E731
ART = [
    re.compile(r"(?<![A-Za-z\\])(?:[a-z] ){2,}[a-z](?![A-Za-z])"),
    re.compile(r"\\(?:over|under)set\{[^A-Za-z0-9\\]"),
    re.compile(r"\\left\s*\.|\\right\s*\.|\\left\(\\right\.|\\left\|\\right\."),
]
ok = [x for r in R for x in r["rows"] if x["ok"] and not x["obj"]]
bad = [x for r in R for x in r["rows"] if not x["ok"] and not x["obj"]]
art = lambda xs: 100 * sum(1 for x in xs if any(a.search(x["raw"]) for a in ART)) / len(xs)  # noqa: E731

public = [
    ln.strip()
    for ln in (LAB / "public_keys.txt").read_text(encoding="utf-8").splitlines()
    if ln.strip() and not ln.startswith("#")
]
included = sum(inc.values())
out = {
    "promPapers": len(papers),
    "promPromoted": len(prom),
    "promFull": len(full),
    "promPartial": len(prom) - len(full),
    "promCovMedian": round(st.median(cov)),
    "promCandidateRows": total,
    "promCanonicalRows": included,
    "promCanonicalPct": f"{100 * included / total:.1f}",
    "promExactRows": len(rows_all) - widened,
    "promWidenedRows": widened,
    "promDerivedParams": derived,
    "promOutsideGrammar": causes.get("outside_grammar", 0),
    "promAllRejected": causes.get("all_rejected", 0),
    "promNotSorted": causes.get("not_sorted", 0),
    "promMissingDecl": causes.get("missing_declarations", 0),
    "promNoVariable": causes.get("semantic_invalid", 0),
    "promVOnePct": "1.5",
    "promVOneRows": 97,
    "promVOnePapers": 18,
    "promRulesPct": "23.4",
    "promRulesRows": 1472,
    "promRulesPapers": 100,
    "blockUndeclared": undeclared,
    "blockProduct": product,
    "ruleCount": 54,
    "ruleNeeded": rules_needed,
    "ruleFewPapers": few,
    "ablFivePct": f"{100 * _load(abl5)['included'] / total:.1f}",
    "ablTenPct": f"{100 * _load(abl10)['included'] / total:.1f}",
    "eqLeftAgree": round(100 * 732 / 767),
    "eqRightAgree": round(100 * 82 / 146),
    "corrCitations": f"{corr:.2f}",
    "quartTopPct": f"{pct(top):.0f}",
    "quartBottomPct": f"{pct(bottom):.0f}",
    "artefactFailPct": f"{art(bad):.0f}",
    "artefactOkPct": f"{art(ok):.0f}",
    "pubPapers": len(public),
    "pubPromoted": sum(1 for p in prom if p["paper_key"] in public),
}
for status, name in (
    ("canonical", "Canonical"),
    ("outside_grammar", "Outside"),
    ("several", "Several"),
    ("none", "None"),
):
    n, cand, can = obj[status]
    out[f"promObj{name}"] = n
    out[f"promObj{name}Cand"] = cand
    out[f"promObj{name}Rows"] = can

# search examples (Section "Searching the corpus")
graph = _load(patterns_path)["models"]
paper_models = {k: v for k, v in graph.items() if k.startswith("10.")}


def has_row(model: dict, pattern: str) -> bool:
    return any(pattern in r["patterns"] for r in model["rows"])


def has_model(model: dict, pattern: str) -> bool:
    return any(m["pattern"] == pattern for m in model["models"])


flow = {k for k, v in paper_models.items() if has_row(v, "flow_conservation_row")}
balance = {k for k, v in paper_models.items() if has_row(v, "balance_row")}
disjunctive = {k for k, v in paper_models.items() if has_model(v, "disjunctive_scheduling")}
headway, ordering = set(), set()
for key in paper_models:
    f = _load(LAB / "formulations" / f"{key}.json")
    used = {t["ref"] for c in f["constraints"] for t in c["lhs"] + c["rhs"]}
    pairwise = [
        v
        for v in f["variables"]
        if v.get("domain") == "binary"
        and len(v.get("shape") or []) >= 2
        and len(set(v["shape"])) < len(v["shape"])
    ]
    if any(v["name"] in used for v in pairwise):
        ordering.add(key)
    text = " ".join((x.get("description") or "") for x in f["variables"] + f["parameters"])
    if "headway" in text.lower():
        headway.add(key)
out.update(
    {
        "srchModels": len(paper_models),
        "srchFlowModels": len(flow),
        "srchFlowRows": sum(
            1
            for k in flow
            for r in paper_models[k]["rows"]
            if "flow_conservation_row" in r["patterns"]
        ),
        "srchBalanceModels": len(balance),
        "srchHeadwayModels": len(headway),
        "srchDisjModels": len(disjunctive),
        "srchHeadwayDisj": len(headway & disjunctive),
        "srchDisjNoWord": len(disjunctive - headway),
        "srchHeadwayNoDisj": len(headway - disjunctive),
        "srchOrderVarModels": len(ordering),
        "srchOrderVarDisj": len(ordering & disjunctive),
    }
)
assert len(rows_all) == included, (len(rows_all), included)
for k, v in out.items():
    print(f"\\newcommand{{\\{k}}}{{{v}}}")
