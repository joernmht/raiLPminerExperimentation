"""Canonical-yield probe: the development instrument behind the yield work.

``promote --partial`` is the measurement (rows of promoted papers over the
candidate rows). It takes minutes and says *that* a row failed; this script
runs the same ``promote_paper`` per paper in parallel, never writes the corpus,
and adds per row what the work on new rules needs: the row after M1b, the
parser's message, and the reason lp2graph's structural row reader gave for
leaving the row alone.

    PYTHONPATH=. python3 scripts/yield_probe.py run OUT.json [--declarations DIR] [--only KEY ...]
    PYTHONPATH=. python3 scripts/yield_probe.py reasons OUT.json [NEEDLE [N]]
    PYTHONPATH=. python3 scripts/yield_probe.py compare OLD.json NEW.json [N]

``run`` prints the yield exactly as promote computes it. ``--declarations``
points at a scratch sidecar tree, e.g. one written by
``python -m corpusbuilder.detdecl --quick --out DIR``, so a rule change can be
measured before the live sidecars are touched. ``reasons`` tabulates why rows
stay out (with a needle: samples of that class); ``compare`` lists rows gained
and lost between two runs, which is the regression check for every rule.

The output holds row LaTeX (TDM material): keep it out of the repository.
"""

# ruff: noqa: I001 — ``railpminer._lp2graph`` must run before the ``lp2graph`` imports.

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter, defaultdict
from multiprocessing import Pool
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401
from lp2graph.mining.ingest import ingest_latex, normalize_latex
from lp2graph.mining.ingest import row_canon
from lp2graph.mining.ingest.latex_normalizer import _ANY_ROW_RE, DocContext, _body_span

from corpusbuilder import promote as P
from corpusbuilder.dossier import Dossier

_STATE: dict = {}


def reader_reason(norm_doc: str, tag: str) -> str:
    """Why the structural reader left the row tagged ``tag`` alone."""
    lo, hi = _body_span(norm_doc)
    body = norm_doc[lo:hi]
    ctx = DocContext.build(norm_doc, body)
    for m in _ANY_ROW_RE.finditer(body):
        if "\\tag{" + tag + "}" not in m.group(3):
            continue
        objective = "\\min" in m.group(1) or "\\max" in m.group(1)
        try:
            row_canon._canonicalize(
                m.group(2),
                ctx.row_context(),
                objective=objective,
                widen=True,
                sense="max" if "\\max" in m.group(1) else "min",
                row_id="r",
            )
            return "accepted by the reader"
        except row_canon._Refuse as exc:
            return str(exc)
        except Exception as exc:  # a reader bug must show, not hide
            return f"EXCEPTION {type(exc).__name__}: {exc}"
    return "row not delimited"


def _paper(key: str) -> dict:
    decisions, decl_dir = _STATE["decisions"], _STATE["declarations"]
    dossier_path = P.DOSSIERS / f"{key}.json"
    if not dossier_path.exists():
        return {
            "key": key,
            "outcome": {"promoted": False, "rows": 0, "cause": "no_dossier"},
            "rows": [],
        }
    dossier = Dossier.load(dossier_path)
    out = P.promote_paper(
        dossier, decisions[key], declarations_dir=decl_dir, write=False, partial=True
    )
    outcome = out.to_dict()
    outcome["rows_included"] = out.rows_included
    rows, _counts = P.rows_for(dossier, decisions[key])
    rows, _absorbed = P.absorb_declaration_rows(rows)
    detail: list[dict] = []
    decl_path = decl_dir / f"{key}.tex"
    if decl_path.exists() and rows:
        declarations = decl_path.read_text(encoding="utf-8")
        entry = P.entry_id_for(key)
        # promote's own base: the paper's objective when it states exactly one
        # and the grammar reads it, otherwise the constraint rows stand alone
        objectives = [r for r in rows if r.is_objective]
        usable = (
            objectives
            if len(objectives) == 1
            and ingest_latex(
                P.assemble(dossier, objectives, declarations, entry_id=entry), source="p"
            ).ok
            else []
        )
        for row in rows:
            base = [] if row.is_objective else usable
            doc = P.assemble(dossier, [*base, row], declarations, entry_id=entry)
            tag = row.name.replace("_", "\\_")
            norm, _prov = normalize_latex(doc, source="p")
            line = next((ln for ln in norm.splitlines() if "\\tag{" + tag + "}" in ln), "")
            res = ingest_latex(doc, source="p")
            entry_row = {
                "name": row.name,
                "obj": row.is_objective,
                "raw": row.latex,
                "norm": line.strip(),
                "ok": res.ok,
            }
            if not res.ok:
                entry_row["err"] = " ".join(
                    "; ".join(f"{f.message} || {f.detail or ''}" for f in res.failures).split()
                )
                entry_row["why"] = reader_reason(norm, tag)
            detail.append(entry_row)
    return {"key": key, "outcome": outcome, "rows": detail}


def summary(results: list[dict]) -> str:
    total = sum(r["outcome"]["rows"] for r in results)
    promoted = [r["outcome"] for r in results if r["outcome"]["promoted"]]
    included = sum(o["rows_included"] for o in promoted)
    constraint_rows = [x for r in results for x in r["rows"] if not x["obj"]]
    objectives = [x for r in results for x in r["rows"] if x["obj"]]
    return (
        f"canonical yield {included}/{total} = {100 * included / max(total, 1):.2f} % | "
        f"promoted {len(promoted)}/{len(results)} papers | "
        f"objectives parsing {sum(1 for x in objectives if x['ok'])}/{len(objectives)} | "
        f"constraint rows parsing {sum(1 for x in constraint_rows if x['ok'])}/{len(constraint_rows)}"
    )


def run(out: Path, declarations: Path, only: list[str], jobs: int) -> None:
    decisions, _ = P.load_decisions(sorted(P.DECISIONS.glob("*.json")))
    _STATE.update(decisions=decisions, declarations=declarations)
    keys = [k for k in decisions if not only or any(o in k for o in only)]
    with Pool(jobs) as pool:
        results = pool.map(_paper, keys, chunksize=2)
    out.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8", newline="\n")
    print(summary(results))


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _class(reason: str) -> str:
    return re.sub(r"'[^']*'|\"[^\"]*\"", "'…'", reason)[:70]


def reasons(path: Path, needle: str | None, n: int) -> None:
    results = _load(path)
    print(summary(results))
    by_class: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for r in results:
        for x in r["rows"]:
            if not x["ok"]:
                by_class[_class(x.get("why", "no probe base"))].append((r["key"], x))
    if needle is None:
        for cls, rows in sorted(by_class.items(), key=lambda kv: -len(kv[1]))[:45]:
            objectives = sum(1 for _k, x in rows if x["obj"])
            print(f"{len(rows):5d}  ({objectives} objectives)  {cls}")
        return
    rows = [e for cls, es in sorted(by_class.items()) if needle in cls for e in es]
    random.Random(1).shuffle(rows)
    for key, x in rows[:n]:
        print(f"\n# {key} {x['name']}{' OBJECTIVE' if x['obj'] else ''} | {x.get('why', '')}")
        print("  raw ", x["raw"][:320])
        print("  norm", x.get("norm", "")[:320])
        print("  err ", x.get("err", "")[:240])


def compare(old: Path, new: Path, n: int) -> None:
    a = {(r["key"], x["name"]): x for r in _load(old) for x in r["rows"]}
    b = {(r["key"], x["name"]): x for r in _load(new) for x in r["rows"]}
    lost = [k for k in a if a[k]["ok"] and k in b and not b[k]["ok"]]
    gained = [k for k in b if b[k]["ok"] and not (k in a and a[k]["ok"])]
    print(f"gained {len(gained)} rows, lost {len(lost)}")
    reasons_lost = Counter(_class(b[k].get("why", "")) for k in lost)
    for cls, count in reasons_lost.most_common(8):
        print(f"  lost {count:4d}  {cls}")
    for k in lost[:n]:
        print(f"\nLOST {k[0]} {k[1]}")
        print("  raw ", a[k]["raw"][:300])
        print("  was ", a[k].get("norm", "")[:300])
        print("  now ", b[k].get("norm", "")[:300])
        print("  err ", b[k].get("err", "")[:240])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_run = sub.add_parser("run")
    p_run.add_argument("out", type=Path)
    p_run.add_argument("--declarations", type=Path, default=P.DECLARATIONS)
    p_run.add_argument("--only", nargs="*", default=[])
    p_run.add_argument("--jobs", type=int, default=3)
    p_reasons = sub.add_parser("reasons")
    p_reasons.add_argument("path", type=Path)
    p_reasons.add_argument("needle", nargs="?")
    p_reasons.add_argument("n", nargs="?", type=int, default=12)
    p_cmp = sub.add_parser("compare")
    p_cmp.add_argument("old", type=Path)
    p_cmp.add_argument("new", type=Path)
    p_cmp.add_argument("n", nargs="?", type=int, default=6)
    args = ap.parse_args(argv)
    if args.cmd == "run":
        run(args.out, args.declarations, args.only, args.jobs)
    elif args.cmd == "reasons":
        reasons(args.path, args.needle, args.n)
    else:
        compare(args.old, args.new, args.n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
