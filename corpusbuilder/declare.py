"""Deterministic declarations from the papers' own notation tables and the index discovery.

The question this module answers: *now that the notation tables are parsable,
does feeding them into the declaration sidecars raise the canonical yield?* It
does two things, both pure functions of the corpus artifacts:

1. **Proposals** (:func:`proposals`): for one paper, the ``%@`` lines the
   evidence supports without any model:

   * one ``%@ index`` line per index family the index discovery found
     (``corpus/indices/<key>.json``) that the sidecar does not declare;
   * one ``%@ var`` / ``%@ param`` / ``%@ index`` line per *missing* name of
     the vocabulary check (``corpus/vocab/<key>.json``) that a notation-table
     row or definition-list item states, with the kind read off the row's own
     words ("binary variable", "set of", ...), the domain likewise, and the
     shape from the index letters the name carries in the formulas, resolved
     through the discovered families. A name whose shape cannot be resolved is
     listed as skipped, never guessed.

   Every proposed line carries the ``% --- vocabulary (deterministic: ...``
   marker so ``vocab.classify_sidecar_lines`` attributes it correctly.

2. **The experiment** (:func:`run_experiment`): copy the live sidecars into a
   scratch tree, append the proposals, run ``promote --partial`` into scratch
   output directories (the live corpus is never touched) and compare the row
   coverage with the committed ``corpus/promotion.json``. The result goes to
   ``corpus/declare_experiment.{json,md}`` (counts only).

Run::

    PYTHONPATH=. python3 -m corpusbuilder.declare                  # proposals report
    PYTHONPATH=. python3 -m corpusbuilder.declare --experiment     # + promote in scratch
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from corpusbuilder import indices as ix
from corpusbuilder.discover import DISCOVERY, load_record
from corpusbuilder.dossier import Dossier
from corpusbuilder.fulltext import DOSSIERS, included_dossiers
from corpusbuilder.indices import INDICES, declared_names
from corpusbuilder.promote import CORPUS, DECLARATIONS, promote_all

VOCAB_DIR = CORPUS / "vocab"
REPORT_JSON = CORPUS / "declare.json"
REPORT_MD = CORPUS / "declare.md"
EXPERIMENT_JSON = CORPUS / "declare_experiment.json"
EXPERIMENT_MD = CORPUS / "declare_experiment.md"
SCRATCH = CORPUS / "_declare_experiment"
MARKER = "% --- vocabulary (deterministic: notation tables + index discovery, {date}) ---"

_VAR = re.compile(r"\bvariables?\b|\bdecision\b", re.IGNORECASE)
_SET = re.compile(r"\bsets?\b|\bcollection\b|\bindex set\b", re.IGNORECASE)
_BINARY = re.compile(
    r"\bbinary\b|\b0[-–/]1\b|\bboolean\b|\bequals? (?:1|one) if\b|\b(?:1|one) if\b", re.IGNORECASE
)
_INTEGER = re.compile(r"\binteger\b|\bnumber of\b", re.IGNORECASE)
_NONNEG = re.compile(r"\bnon-?negative\b", re.IGNORECASE)
_BIG_M = re.compile(
    r"\bbig[- ]?M\b|\bsufficiently large\b|\blarge (?:positive )?(?:number|constant)\b",
    re.IGNORECASE,
)
_NOT_A_NAME = re.compile(r"[^A-Za-z0-9_]")


def relaxed_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def symbol_key(symbol: str, index_letters: set[str]) -> str | None:
    """Relaxed key of a table symbol with its index letters dropped (``g_{i}^{train}`` -> ``gtrain``)."""
    n = ix.normalise(symbol.replace("⟨", "").replace("⟩", ""))
    m = ix._IDENT.search(n)
    if not m:
        return None
    sub, sup, _ = ix._scripts(n, m.end())
    labels = [
        tok
        for g in sub + sup
        for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]*", g)
        if not is_index_token(tok, index_letters)
    ]
    return relaxed_key(m.group(1) + "".join(labels))


def is_index_token(tok: str, index_letters: set[str] | dict) -> bool:
    """A script token that stands for an index: a known index letter, or a lower-case/Greek letter.

    A capital letter in a script (``\\Delta^{F}``, ``t_{B}``) is a label unless the
    paper binds it somewhere; the vocabulary names it that way too (``Delta_F``).
    """
    if tok in index_letters:
        return True
    return ix.is_letterish(tok) and (tok.islower() or tok in ix.GREEK) and tok[0].islower()


def kind_from_desc(desc: str) -> tuple[str, dict[str, str]]:
    """``("var", {"domain": ...})`` / ``("index", {})`` / ``("param", {"kind": ...})`` from the row's words."""
    if _VAR.search(desc):
        domain = (
            "binary"
            if _BINARY.search(desc)
            else "integer"
            if _INTEGER.search(desc)
            else "non_negative"
            if _NONNEG.search(desc)
            else "continuous"
        )
        return "var", {"domain": domain}
    if _SET.search(desc) and not _BIG_M.search(desc):
        return "index", {}
    return "param", {"kind": "big_m" if _BIG_M.search(desc) else ""}


def shape_for(
    key: str, rows: list[str], letter_family: dict[str, str]
) -> tuple[list[str] | None, str]:
    """Families of the index letters a name carries in the rows (most common pattern).

    Returns ``(shape, note)``; ``shape`` is None when a carried letter has no
    family (the note says which), so nothing is guessed.
    """
    patterns: Counter[tuple[str, ...]] = Counter()
    unresolved: Counter[str] = Counter()
    for norm in rows:
        i = 0
        while True:
            m = ix._IDENT.search(norm, i)
            if not m:
                break
            sub, sup, j = ix._scripts(norm, m.end())
            i = j
            labels = []
            letters = []
            for g in sub:
                for tok, _jux in ix._pieces(g):
                    if is_index_token(tok, letter_family):
                        letters.append(tok)
                for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]*", g):
                    if not is_index_token(tok, letter_family):
                        labels.append(tok)
            for g in sup:
                for tok in re.findall(r"[A-Za-z][A-Za-z0-9_]*", g):
                    if tok in letter_family:
                        letters.append(
                            tok
                        )  # a superscript index (x^{k}) counts like a subscript one
                    elif not is_index_token(tok, letter_family):
                        labels.append(tok)
            if relaxed_key(m.group(1) + "".join(labels)) != key:
                continue
            fams = []
            for letter in letters:
                fam = letter_family.get(letter)
                if not fam:
                    unresolved[letter] += 1
                    fams = None
                    break
                fams.append(fam)
            if fams is not None:
                patterns[tuple(fams)] += 1
    if patterns:
        return list(patterns.most_common(1)[0][0]), ""
    if unresolved:
        return None, "letters without family: " + ", ".join(
            f"{k}×{n}" for k, n in unresolved.most_common(3)
        )
    return [], "no indexed use found (declared as scalar)"


def _index_letters(idx: dict) -> dict[str, str]:
    return {
        n: e["family"]
        for n, e in idx.get("letters", {}).items()
        if e["verdict"] in ("index", "alias") and e.get("family")
    }


def _table_items(disc: dict, index_letters: set[str]) -> dict[str, tuple[str, str]]:
    items: dict[str, tuple[str, str]] = {}
    for t in disc.get("tables", []):
        if not t.get("notation"):
            continue
        for r in t["rows"]:
            if r.get("symbol") and not r.get("header"):
                k = symbol_key(r["symbol"], index_letters)
                if k:
                    items.setdefault(k, (r["symbol"], r.get("desc", "")))
    for it in disc.get("deflists", []):
        k = symbol_key(it["term"], index_letters) if it.get("term") else None
        if k:
            items.setdefault(k, (it["term"], it.get("def", "")))
    return items


def proposals(
    key: str,
    *,
    decl_dir: Path = DECLARATIONS,
    discovery_dir: Path = DISCOVERY,
    indices_dir: Path = INDICES,
    vocab_dir: Path = VOCAB_DIR,
    dossier_dir: Path = DOSSIERS,
) -> dict:
    """The deterministic ``%@`` lines one paper's evidence supports, plus what was skipped and why."""
    disc = load_record(key, discovery_dir) or {}
    ip = indices_dir / f"{key}.json"
    idx = (
        json.loads(ip.read_text(encoding="utf-8"))
        if ip.exists()
        else {"letters": {}, "families": {}}
    )
    vp = vocab_dir / f"{key}.json"
    vocab = json.loads(vp.read_text(encoding="utf-8")) if vp.exists() else {}
    declared = declared_names(key, decl_dir)
    letter_family = _index_letters(idx)
    lines: list[str] = []
    skipped: list[dict] = []
    # 1. families
    for fam in idx.get("families", {}).values():
        name = fam["name"]
        if (
            fam.get("declared_as") == "index"
            or _NOT_A_NAME.search(name)
            or (name[0].islower() and len(name) == 1)
        ):
            continue
        if name in declared["param"] or name in declared["var"]:
            skipped.append(
                {
                    "name": name,
                    "why": f"family declared as {'param' if name in declared['param'] else 'var'} (a cap); needs a decision",
                }
            )
            continue
        letters = ", ".join(fam.get("letters", {})) or "-"
        desc = fam.get("desc") or f"index family ranged over by {letters}"
        lines.append(f"%@ index {name} ordered=0 cyclic=0 :: {desc}")
    # 2. missing names stated in a notation table
    rows_norm = []
    dp = dossier_dir / f"{key}.json"
    if dp.exists():
        d = Dossier.load(dp)
        rows_norm = [ix.normalise(f.latex) for f in d.formulas if f.latex]
    items = _table_items(disc, set(letter_family))
    for name in sorted(vocab.get("missing", {})):
        item = items.get(relaxed_key(name))
        if not item:
            continue
        symbol, desc = item
        if name in declared["index"] or name in declared["param"] or name in declared["var"]:
            continue
        kind, extra = kind_from_desc(desc)
        if kind == "index":
            lines.append(f"%@ index {name} ordered=0 cyclic=0 :: {desc[:120]}")
            continue
        shape, note = shape_for(relaxed_key(name), rows_norm, letter_family)
        if shape is None:
            skipped.append({"name": name, "why": note, "table": symbol})
            continue
        shape_s = ",".join(shape) if shape else "-"
        if kind == "var":
            lines.append(
                f"%@ var {name} shape={shape_s} domain={extra['domain']} role=primary drole=- lo=- hi=- :: {desc[:120]}"
            )
        else:
            pk = extra["kind"] or (
                "scalar" if not shape else "vector" if len(shape) == 1 else "matrix"
            )
            lines.append(f"%@ param {name} shape={shape_s} kind={pk} domain=- :: {desc[:120]}")
    return {
        "paper_key": key,
        "lines": lines,
        "skipped": skipped,
        "counts": {
            "index_lines": sum(1 for line in lines if line.startswith("%@ index")),
            "var_lines": sum(1 for line in lines if line.startswith("%@ var")),
            "param_lines": sum(1 for line in lines if line.startswith("%@ param")),
            "skipped": len(skipped),
            "missing_names": len(vocab.get("missing", {})),
            "table_items": len(items),
        },
    }


def propose_all(only: set[str] | None = None, **kw) -> dict[str, dict]:
    out = {}
    for d in included_dossiers(kw.get("dossier_dir", DOSSIERS)):
        if only and d.key not in only:
            continue
        out[d.key] = proposals(d.key, **kw)
    return out


def build_report(props: dict[str, dict]) -> dict:
    totals: Counter[str] = Counter()
    for p in props.values():
        totals.update(p["counts"])
    why: Counter[str] = Counter()
    for p in props.values():
        for s_ in p["skipped"]:
            why[s_["why"].split(":")[0]] += 1
    return {
        "papers": len(props),
        "papers_with_lines": sum(1 for p in props.values() if p["lines"]),
        "totals": dict(sorted(totals.items())),
        "skipped_why": dict(why.most_common()),
        "per_paper": {k: props[k]["counts"] for k in sorted(props)},
    }


def render_report_md(report: dict) -> str:
    t = report["totals"]
    lines = [
        "# Deterministic declarations from notation tables and index discovery",
        "",
        f"Papers: **{report['papers']}**, with at least one proposed line: **{report['papers_with_lines']}**.",
        "",
        "| proposed lines | count |",
        "|---|---:|",
        f"| `%@ index` (families found, undeclared) | {t.get('index_lines', 0)} |",
        f"| `%@ var` (missing names a table calls a variable) | {t.get('var_lines', 0)} |",
        f"| `%@ param` (missing names a table describes) | {t.get('param_lines', 0)} |",
        f"| skipped (no resolvable shape / cap declared as parameter) | {t.get('skipped', 0)} |",
        f"| missing names in the vocabulary check | {t.get('missing_names', 0)} |",
        "",
        "Skipped, by reason: " + ", ".join(f"{k} ({n})" for k, n in report["skipped_why"].items()),
        "",
    ]
    return "\n".join(lines) + "\n"


# -- the experiment ----------------------------------------------------------


def stage_sidecars(
    props: dict[str, dict], *, decl_dir: Path = DECLARATIONS, scratch: Path = SCRATCH
) -> Path:
    """Copy the live sidecars into scratch and append the proposals (live tree untouched)."""
    target = scratch / "declarations"
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    for p in sorted(decl_dir.glob("*.tex")):
        if p.name.endswith(".stub.tex") or p.name.endswith(".vocab.tex"):
            continue
        shutil.copy2(p, target / p.name)
    marker = MARKER.format(date=date.today().isoformat())
    for key, prop in props.items():
        if not prop["lines"]:
            continue
        path = target / f"{key}.tex"
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        block = "\n".join([marker, *prop["lines"]]) + "\n"
        path.write_text(
            (existing.rstrip("\n") + "\n" if existing else "") + block,
            encoding="utf-8",
            newline="\n",
        )
    return target


def run_experiment(
    props: dict[str, dict],
    *,
    scratch: Path = SCRATCH,
    baseline: Path = CORPUS / "promotion.json",
    progress=None,
) -> dict:
    decl = stage_sidecars(props, scratch=scratch)
    out_dirs = {name: scratch / name for name in ("formulations", "provenance", "promoted")}
    for d in out_dirs.values():
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
    report = promote_all(
        declarations_dir=decl, out_dirs=out_dirs, partial=True, write=True, progress=progress
    )
    base = json.loads(baseline.read_text(encoding="utf-8")) if baseline.exists() else {}

    def cov(r: dict) -> dict:
        c = r.get("row_coverage") or {}
        return {
            "promoted": r.get("promoted")
            if isinstance(r.get("promoted"), int)
            else len(r.get("promoted") or []),
            "objective_ok": c.get("objective_ok"),
            "rows_ok": c.get("rows_ok"),
            "rows_probed": c.get("rows_probed"),
            "rows_ok_share": c.get("rows_ok_share"),
            "papers_half_or_more": c.get("papers_half_or_more"),
            "row_failures": c.get("row_failures") or {},
        }

    return {
        "baseline": cov(base),
        "with_tables": cov(report),
        "baseline_rules": base.get("rewrite_rules_version"),
        "rules": report.get("rewrite_rules_version"),
    }


def render_experiment_md(x: dict) -> str:
    b, w = x["baseline"], x["with_tables"]
    lines = [
        "# Experiment: sidecars + deterministic table/index declarations, promote --partial in scratch",
        "",
        f"Rewrite rules: baseline {x.get('baseline_rules')}, experiment {x.get('rules')}. The live corpus was not touched.",
        "",
        "| measure | baseline (committed) | with table + index declarations |",
        "|---|---:|---:|",
    ]
    for k, label in (
        ("promoted", "papers promoted"),
        ("objective_ok", "objective rows parsing"),
        ("rows_ok", "constraint rows parsing"),
        ("rows_probed", "rows probed"),
        ("rows_ok_share", "share of rows parsing"),
        ("papers_half_or_more", "papers with ≥ half their rows"),
    ):
        lines.append(f"| {label} | {b.get(k)} | {w.get(k)} |")
    lines += ["", "| row failure class | baseline | with tables | delta |", "|---|---:|---:|---:|"]
    keys = sorted(
        set(b["row_failures"]) | set(w["row_failures"]),
        key=lambda k: -(b["row_failures"].get(k, 0)),
    )
    for k in keys:
        lines.append(
            f"| {k} | {b['row_failures'].get(k, 0)} | {w['row_failures'].get(k, 0)} | {w['row_failures'].get(k, 0) - b['row_failures'].get(k, 0):+d} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*")
    ap.add_argument(
        "--experiment",
        action="store_true",
        help="promote in scratch with the proposals appended and compare",
    )
    args = ap.parse_args(argv)
    props = propose_all(set(args.only) if args.only else None)
    report = build_report(props)
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    REPORT_MD.write_text(render_report_md(report), encoding="utf-8", newline="\n")
    t = report["totals"]
    print(
        f"declare: {report['papers_with_lines']}/{report['papers']} papers get lines: index {t.get('index_lines', 0)}, var {t.get('var_lines', 0)}, param {t.get('param_lines', 0)}; skipped {t.get('skipped', 0)}",
        file=sys.stderr,
    )
    if args.experiment:

        def progress(done: int, total: int, key: str) -> None:
            if done % 20 == 0:
                print(f"  promote {done}/{total} {key}", file=sys.stderr)

        x = run_experiment(props, progress=progress)
        EXPERIMENT_JSON.write_text(
            json.dumps(x, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        EXPERIMENT_MD.write_text(render_experiment_md(x), encoding="utf-8", newline="\n")
        print(render_experiment_md(x))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
