"""corpusbuilder.discovergame — the discovery round: the paper's text with every formula marked.

The human-in-the-loop view for the bottom-up pipeline (``discover`` + ``indices``).
One page per paper (``corpus/review/discover/<key>.html``, gitignored: it carries
the paper's prose) plus an index page (``corpus/review/discover.html``). The
reader sees the article text with every mathematical element marked by class
(statement / operator / symbol mention / expression), display formulas as blocks
with their ``eq-NNNN`` ids, the notation tables and definition lists, and the
index table the deterministic pass derived (letters, families, the rule that
decided each). The reader can

* mark an inline element as a formula the pipeline must take (or reject a
  display formula that is none), and mark any selected text span as a formula
  the walker did not see;
* confirm, reject or re-assign every index letter and family (edit the family
  name to merge two letters into one family);

and export everything as ``discover-decisions-1`` JSON (one file per paper),
which ``load_decisions`` merges back (last wins, ADR-0008). Nothing here is
proposed by a model; every pre-filled verdict names the deterministic rule.

Formulas are typeset in the browser (MathJax from the CDN, raw LaTeX stays
visible if the CDN is unreachable): open the downloaded page in a desktop
browser.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.discovergame [--only KEY ...] [--zip]
    PYTHONPATH=. python3 -m corpusbuilder.discovergame --decisions FILE ...   # summarise exports
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

from corpusbuilder.discover import DISCOVERY, load_record
from corpusbuilder.dossier import Dossier
from corpusbuilder.fulltext import DOSSIERS, included_dossiers
from corpusbuilder.game import render_latex
from corpusbuilder.indices import INDICES
from corpusbuilder.promote import CORPUS
from corpusbuilder.vocabgame import LOGO

REVIEW = CORPUS / "review"
OUT_DIR = REVIEW / "discover"
INDEX_PAGE = REVIEW / "discover.html"
ZIP_PATH = REVIEW / "discover.zip"
PAGE_SCHEMA = "discover-page-1"
EXPORT_SCHEMA = "discover-decisions-1"

_PLACEHOLDER = re.compile(r"⟨([^⟨⟩]*)⟩")


# -- payload ----------------------------------------------------------------


def _meta(d: Dossier | None, rec: dict) -> dict:
    src = d.source if d is not None else None
    meta = rec.get("meta") or {}
    return {
        "key": rec.get("paper_key", ""),
        "title": (getattr(src, "title", None) or meta.get("title") or "")[:300],
        "year": getattr(src, "year", None) or meta.get("year"),
        "venue": getattr(src, "venue", None) or meta.get("journal") or "",
        "doi": getattr(src, "doi", None) or meta.get("doi") or "",
        "counts": rec.get("counts", {}),
    }


def page_payload(rec: dict, idx: dict | None, d: Dossier | None = None) -> dict:
    """What one paper page embeds (pure function of the two records)."""
    maths = {}
    for m in rec["maths"]:
        entry = {
            "where": m["where"],
            "cls": m["cls"],
            "latex": m["latex"],
            "show": render_latex(m["latex"]) if m["ok"] and m["latex"] else "",
            "near": m.get("near", []),
        }
        if m["where"] == "display":
            entry["eq"] = m.get("eq")
            entry["tag"] = m.get("tag", "")
            entry["dups"] = m.get("dups", [])
        if m["where"] == "inline":
            entry["block"] = m.get("block")
        maths[m["id"]] = entry
    paras = [
        {"i": p["i"], "region": p["region"], "section": p["section"], "segments": p["segments"]}
        for p in rec["paras"]
        if p["region"] in ("abstract", "body", "appendix", "other")
    ]
    tables = [
        {
            "id": t["id"],
            "label": t.get("label", ""),
            "caption": t["caption"],
            "rows": [
                {
                    "header": r["header"],
                    "cells": [c["text"] for c in r["cells"]],
                    "symbol": r.get("symbol"),
                }
                for r in t["rows"]
            ],
        }
        for t in rec["tables"]
        if t.get("notation")
    ]
    statements = [
        m["id"]
        for m in rec["maths"]
        if m["where"] == "inline" and m["cls"] in ("statement", "operator") and m["ok"]
    ]
    idx = idx or {"letters": {}, "families": {}, "declared_index": [], "counts": {}, "rules": {}}
    letters = {
        n: {
            k: e[k]
            for k in (
                "verdict",
                "rule",
                "family",
                "families",
                "bound_rows",
                "capped",
                "prose_rows",
                "table_rows",
                "sub_rows",
                "sup_rows",
                "bases",
                "alias_votes",
                "rows",
                "desc",
                "multi_family",
            )
        }
        for n, e in idx["letters"].items()
    }
    return {
        "schema_version": PAGE_SCHEMA,
        "paper": _meta(d, rec),
        "paras": paras,
        "maths": maths,
        "tables": tables,
        "deflists": rec.get("deflists", []),
        "statements": statements,
        "indices": {
            "letters": letters,
            "families": idx["families"],
            "declared_index": idx.get("declared_index", []),
            "declared_index_are_letters": idx.get("declared_index_are_letters", []),
            "counts": idx.get("counts", {}),
            "rows": idx.get("rows", {}),
        },
    }


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )


def build_page(payload: dict, out: Path) -> Path:
    html = (
        TEMPLATE.replace("__DATA__", _json(payload))
        .replace("__LOGO__", LOGO)
        .replace("__KEY__", payload["paper"]["key"])
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return out


def build_index(rows: list[dict], out: Path) -> Path:
    html = INDEX_TEMPLATE.replace("__ROWS__", _json({"papers": rows})).replace("__LOGO__", LOGO)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return out


def build_all(
    only: set[str] | None = None,
    *,
    dossier_dir: Path = DOSSIERS,
    discovery_dir: Path = DISCOVERY,
    indices_dir: Path = INDICES,
    out_dir: Path = OUT_DIR,
    index_page: Path = INDEX_PAGE,
    promotion_path: Path = CORPUS / "promotion.json",
) -> list[dict]:
    rows: list[dict] = []
    stake = index_rows_at_stake(promotion_path)
    for d in included_dossiers(dossier_dir):
        rec = load_record(d.key, discovery_dir)
        if rec is None:
            continue
        ip = indices_dir / f"{d.key}.json"
        idx = json.loads(ip.read_text(encoding="utf-8")) if ip.exists() else None
        payload = page_payload(rec, idx, d)
        if not only or d.key in only:
            build_page(payload, out_dir / f"{d.key}.html")
        c = payload["paper"]["counts"]
        ic = payload["indices"]["counts"]
        rows.append(
            {
                "key": d.key,
                "title": payload["paper"]["title"],
                "year": payload["paper"]["year"],
                "display": c.get("display", 0),
                "statements": c.get("inline_statement", 0) + c.get("inline_operator", 0),
                "notation_rows": c.get("notation_rows", 0),
                "letters_index": ic.get("letters_index", 0),
                "letters_alias": ic.get("letters_alias", 0),
                "letters_candidate": ic.get("letters_candidate", 0),
                "families_new": ic.get("families_new", 0),
                "rows_probed": stake.get(d.key, (0, 0))[0],
                "at_stake": stake.get(d.key, (0, 0))[1],
            }
        )
    # the worklist order: papers where confirmed indices could flip the most rows first
    rows.sort(key=lambda r: (-r["at_stake"], -r["rows_probed"], r["key"]))
    build_index(rows, index_page)
    return rows


#: Row-failure classes of ``promote --partial`` that confirmed indices and families can flip.
INDEX_CLASSES = (
    "quantifier: clause not understood",
    "other",
    "binder: range",
    "binder: tuple",
    "quantifier: tuple",
    "quantifier: subscripted set",
    "binder: subscripted set",
)


def index_rows_at_stake(promotion_path: Path) -> dict[str, tuple[int, int]]:
    """``{paper_key: (rows probed, rows failing in an index-related class)}`` from the promotion report."""
    if not promotion_path.exists():
        return {}
    report = json.loads(promotion_path.read_text(encoding="utf-8"))
    out: dict[str, tuple[int, int]] = {}
    for outcome in report.get("papers") or []:
        cov = outcome.get("coverage") or {}
        failures = cov.get("row_failures") or {}
        out[outcome["paper_key"]] = (
            int(cov.get("rows_probed") or 0),
            sum(int(failures.get(c, 0)) for c in INDEX_CLASSES),
        )
    return out


def zip_pages(
    out_dir: Path = OUT_DIR, index_page: Path = INDEX_PAGE, zip_path: Path = ZIP_PATH
) -> Path:
    """One archive with the index page and every paper page (for delivery)."""
    stage_root = zip_path.parent / "_zip_stage"
    stage = stage_root / "discover_round"
    if stage_root.exists():
        shutil.rmtree(stage_root)
    stage.mkdir(parents=True)
    shutil.copy2(index_page, stage / index_page.name)
    shutil.copytree(out_dir, stage / out_dir.name)
    archive = shutil.make_archive(
        str(zip_path.with_suffix("")), "zip", root_dir=stage_root, base_dir=stage.name
    )
    shutil.rmtree(stage_root)
    return Path(archive)


# -- decisions --------------------------------------------------------------


def validate_export(obj: dict) -> list[str]:
    problems: list[str] = []
    if obj.get("schema_version") != EXPORT_SCHEMA:
        problems.append(f"schema_version is not {EXPORT_SCHEMA}")
    if not obj.get("paper_key"):
        problems.append("paper_key missing")
    for mid, v in (obj.get("formulas") or {}).items():
        if v not in ("formula", "not"):
            problems.append(f"formulas[{mid}] = {v!r}")
    for i, s in enumerate(obj.get("spans") or []):
        if not isinstance(s, dict) or "text" not in s:
            problems.append(f"spans[{i}] has no text")
    for name, v in (obj.get("indices") or {}).items():
        if not isinstance(v, dict) or v.get("verdict") not in ("index", "not", "unsure"):
            problems.append(f"indices[{name}] verdict")
    for name, v in (obj.get("families") or {}).items():
        if not isinstance(v, dict) or v.get("verdict") not in ("family", "not", "unsure"):
            problems.append(f"families[{name}] verdict")
    return problems


def load_decisions(paths: list[Path]) -> dict[str, dict]:
    """Merge exports per paper: sorted-file order, last wins (ADR-0008)."""
    merged: dict[str, dict] = {}
    for p in sorted(paths):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if validate_export(obj):
            raise ValueError(f"{p}: " + "; ".join(validate_export(obj)))
        key = obj["paper_key"]
        m = merged.setdefault(
            key,
            {
                "paper_key": key,
                "formulas": {},
                "spans": [],
                "indices": {},
                "families": {},
                "files": [],
            },
        )
        m["formulas"].update(obj.get("formulas") or {})
        m["indices"].update(obj.get("indices") or {})
        m["families"].update(obj.get("families") or {})
        seen = {(s.get("para"), s["text"]) for s in m["spans"]}
        for s in obj.get("spans") or []:
            if (s.get("para"), s["text"]) not in seen:
                m["spans"].append(s)
                seen.add((s.get("para"), s["text"]))
        m["files"].append(p.name)
    return merged


def summarize_decisions(merged: dict[str, dict]) -> dict:
    return {
        "papers": len(merged),
        "formulas_marked": sum(
            sum(1 for v in m["formulas"].values() if v == "formula") for m in merged.values()
        ),
        "formulas_rejected": sum(
            sum(1 for v in m["formulas"].values() if v == "not") for m in merged.values()
        ),
        "spans": sum(len(m["spans"]) for m in merged.values()),
        "letters_confirmed": sum(
            sum(1 for v in m["indices"].values() if v["verdict"] == "index")
            for m in merged.values()
        ),
        "letters_rejected": sum(
            sum(1 for v in m["indices"].values() if v["verdict"] == "not") for m in merged.values()
        ),
        "families_confirmed": sum(
            sum(1 for v in m["families"].values() if v["verdict"] == "family")
            for m in merged.values()
        ),
    }


# -- templates --------------------------------------------------------------

_STYLE = r"""
:root{
  --page1:#f3f7f8;--page2:#e7f1f1;--ink:#0c1f3a;--muted:#566782;
  --card:#ffffff;--card2:#f1f8f8;--line:#d6e6e7;--track:#e6f0f0;
  --accent:#0A777F;--accent2:#2F57B2;--good:#0A777F;--good-soft:#e6f4f4;
  --warn:#C85000;--warn-soft:#fbeada;--bad:#D20F41;--bad-soft:#fae3e9;--tier3:#7369BE;--tier3-soft:#ece9fa;
  --shadow:0 1px 3px rgba(12,40,50,.08),0 6px 18px rgba(12,40,50,.05);
}
@media (prefers-color-scheme:dark){:root{
  --page1:#00103a;--page2:#001a55;--ink:#eaf1ff;--muted:#a0b4d8;
  --card:#0c2766;--card2:#10307c;--line:#2a4a92;--track:#001a55;
  --accent:#36b8bf;--accent2:#7aa2ff;--good:#36b8bf;--good-soft:#0d3350;
  --warn:#f0922e;--warn-soft:#3a2a17;--bad:#ff667e;--bad-soft:#43102a;--tier3:#a98bf0;--tier3-soft:#2a2350;
  --shadow:0 1px 3px rgba(0,0,0,.4);}
  .brandlogo svg text,.brandlogo svg path{fill:#fff !important}
}
*{box-sizing:border-box}
html,body{margin:0;padding:0}
body{background:linear-gradient(180deg,var(--page1),var(--page2));background-attachment:fixed;color:var(--ink);
  font:15px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;padding:12px 16px 40px;max-width:1080px;margin:0 auto}
.brandlogo{margin:2px 0 10px}.brandlogo svg{height:28px;width:auto;display:block}
.proto{background:var(--warn-soft);color:var(--warn);border:1.5px solid var(--warn);border-radius:10px;padding:5px 10px;font-size:12px;font-weight:650;margin:0 0 10px;text-align:center}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:800}
h1{font-size:20px;margin:4px 0 2px;font-weight:800;letter-spacing:-.01em}
.sub,.pk{color:var(--muted);font-size:13px}.pk{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
a{color:var(--accent2)}
.tools{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0}
button{font:inherit;border:1.5px solid var(--line);background:var(--card);color:var(--ink);border-radius:10px;padding:7px 11px;cursor:pointer;font-weight:700;font-size:13px}
button.on{background:var(--accent);border-color:var(--accent);color:#fff}
button.bad.on{background:var(--bad);border-color:var(--bad)}
button.mid.on{background:var(--muted);border-color:var(--muted)}
.tabs{display:flex;gap:6px;margin:8px 0 12px;flex-wrap:wrap;position:sticky;top:0;background:var(--page1);padding:6px 0;z-index:5}
.tabs button{border-radius:999px}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);margin-top:12px}
.legend{display:flex;flex-wrap:wrap;gap:10px;font-size:12px;color:var(--muted);margin:6px 0 10px}
.legend span{display:inline-flex;align-items:center;gap:5px}
h3{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:16px 0 6px;font-weight:700}
h4.sec{font-size:15px;margin:18px 0 4px;color:var(--accent2)}
p.para{margin:0 0 10px;line-height:1.75}
.chip{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 5px;margin:0 1px;background:var(--card2);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;cursor:default;vertical-align:baseline}
.chip.c-statement{border-color:var(--warn);background:var(--warn-soft);cursor:pointer}
.chip.c-operator{border-color:var(--tier3);background:var(--tier3-soft);cursor:pointer}
.chip.c-expr{cursor:pointer}
.chip.c-symbol{border-style:dashed}
.chip.f-formula{outline:2px solid var(--good);outline-offset:1px}
.chip.f-not{text-decoration:line-through;opacity:.55}
.chip mjx-container{margin:0 !important;font-size:105%}
.disp{display:block;border-left:4px solid var(--accent);background:var(--card2);border-radius:0 10px 10px 0;padding:6px 10px;margin:8px 0;overflow-x:auto}
.disp.f-not{border-left-color:var(--bad);opacity:.6;text-decoration:line-through}
.disp .eqid{font:11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted);display:flex;justify-content:space-between;align-items:center;gap:8px}
.disp .tex{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;white-space:pre-wrap;word-break:break-all}
.disp .eqid button{padding:2px 8px;font-size:11px}
.tex.raw{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
table.grid{border-collapse:collapse;width:100%;font-size:13px}
table.grid th,table.grid td{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}
table.grid th{color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}
td.mono,span.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
tr.v-index td.mono{color:var(--accent)}tr.v-alias td.mono{color:var(--tier3)}tr.v-candidate td.mono{color:var(--warn)}
tr.h-not td{opacity:.5;text-decoration:line-through}
input[type=text]{font:inherit;font-size:13px;padding:4px 7px;border:1.5px solid var(--line);border-radius:8px;background:var(--card2);color:var(--ink);width:110px}
input.wide{width:100%}
.ev{font-size:12px;color:var(--muted)}
.rowref{display:inline-block;font:11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:0 7px;margin:1px 2px;cursor:pointer}
.stmt{border-top:1px solid var(--line);padding:8px 0;display:grid;grid-template-columns:1fr auto;gap:8px;align-items:start}
.stmt .ctx{font-size:13px;color:var(--muted)}
.stmt .ctx b{color:var(--ink);font-weight:600}
.btns{display:flex;gap:6px;flex-wrap:wrap}
#selbar{position:fixed;left:50%;bottom:24px;transform:translateX(-50%);z-index:9;box-shadow:var(--shadow)}
#selbar button{background:var(--accent);color:#fff;border-color:var(--accent)}
#toast{position:fixed;left:50%;bottom:70px;transform:translateX(-50%);background:#0c1f3a;color:#fff;padding:8px 14px;border-radius:999px;font-size:13px;opacity:0;transition:opacity .25s;pointer-events:none;z-index:10}
#toast.show{opacity:1}
@media (prefers-color-scheme:dark){#toast{background:#eaf1ff;color:#00103a}}
.hidden{display:none !important}
.counter{font-size:12.5px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap;margin-top:6px}
.notation td:first-child{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.flag{display:inline-block;font-size:10.5px;border-radius:999px;padding:0 6px;border:1px solid var(--line);color:var(--muted);margin-left:4px}
.flag.warn{border-color:var(--warn);color:var(--warn)}
.kbd{font-size:11.5px;color:var(--muted);margin-top:8px}
.frow{border-top:1px solid var(--line);padding:8px 0}
.frow .fid{font:11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted)}
.frow .ftex{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;overflow-x:auto;padding:4px 0}
.lchips{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
button.lchip{border-radius:999px;padding:3px 9px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12.5px;font-weight:700}
button.lchip small{font-weight:400;color:var(--muted);margin-left:4px}
button.lchip.v-index{border-color:var(--accent);color:var(--accent)}
button.lchip.v-alias{border-color:var(--tier3);color:var(--tier3)}
button.lchip.v-candidate,button.lchip.v-juxtaposed,button.lchip.v-label{border-color:var(--warn);color:var(--warn)}
button.lchip.h-index{background:var(--accent);border-color:var(--accent);color:#fff}
button.lchip.h-not{background:var(--bad);border-color:var(--bad);color:#fff;text-decoration:line-through}
button.lchip.h-unsure{background:var(--muted);border-color:var(--muted);color:#fff}
button.lchip.h-index small,button.lchip.h-not small,button.lchip.h-unsure small{color:#fff}
button.fam{padding:3px 7px;font-size:12px}
"""

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🔎</text></svg>">
<title>Discovery round — __KEY__</title>
<style>__STYLE__</style>
<script>
window.MathJax = {tex: {inlineMath: [["\\(", "\\)"]], displayMath: [["\\[", "\\]"]], packages: {"[+]": ["ams"]}}, svg: {fontCache: "global"}, startup: {typeset: false}};
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>
</head>
<body>
<div class="brandlogo">__LOGO__</div>
<div class="proto">working prototype — discovery round: deterministic marks, human decisions</div>
<div class="eyebrow">Discovery round · <a href="../discover.html">all papers</a></div>
<h1 id="title"></h1>
<div class="pk" id="pk"></div>
<div class="counter" id="counter"></div>
<div class="tools">
  <button id="exportBtn">⬇ Export decisions</button>
  <button id="importBtn">⬆ Import</button><input type="file" id="importFile" accept="application/json" class="hidden">
  <button id="undoBtn">↶ Undo</button>
  <button id="clearBtn" class="bad">✕ Clear this paper</button>
</div>
<nav class="tabs">
  <button data-tab="text" class="on">Text with formulas</button>
  <button data-tab="idx">Indices</button>
  <button data-tab="frows">Formulas × indices</button>
  <button data-tab="stmts">Statements in prose</button>
  <button data-tab="tables">Notation tables</button>
</nav>
<section id="tab-text" class="card">
  <div class="legend">
    <span><span class="chip c-statement">statement</span> relation / membership</span>
    <span><span class="chip c-operator">operator</span> sum, min, max</span>
    <span><span class="chip c-symbol">symbol</span> mention</span>
    <span><span class="chip c-expr">expr</span> other</span>
    <span>click a chip to mark it ✓ formula → ✗ not → clear · select text to mark a formula the walker missed</span>
  </div>
  <div id="text"></div>
</section>
<section id="tab-idx" class="card hidden">
  <div class="sub">Every index-like letter with the rule that decided it. Confirm or reject; edit the family to re-assign (two letters with the same family name are one family). Verdicts pre-filled by rules are marked; nothing is model-proposed.</div>
  <h3>Letters</h3>
  <table class="grid" id="letters"></table>
  <h3>Families</h3>
  <table class="grid" id="families"></table>
  <div class="kbd" id="declared"></div>
</section>
<section id="tab-frows" class="card hidden">
  <div class="sub">Every formula with the index letters found in it: what the binders bind (∑, ∀, ranges) and which letters sit in subscripts. Click a letter to decide it (✓ index → ✗ not → ? → clear); ✎ sets its family. A decision applies to the letter everywhere in this paper.</div>
  <label class="ev"><input type="checkbox" id="onlyOpen"> only formulas with undecided letters (candidate / label / juxtaposed / unsure)</label>
  <div id="frows"></div>
</section>
<section id="tab-stmts" class="card hidden">
  <div class="sub">Inline statements and operators in document order: the formulas the display-only extraction never saw. Mark what the pipeline must take.</div>
  <div id="stmts"></div>
  <h3>Marked by hand</h3>
  <div id="spans"></div>
</section>
<section id="tab-tables" class="card hidden">
  <div id="tables"></div>
</section>
<div id="selbar" class="hidden"><button id="markSel">＋ mark selection as formula</button></div>
<div id="toast"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const KEY = D.paper.key;
const LSK = "discover:state:v1:" + KEY;
const $ = id => document.getElementById(id);
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function toast(m){ const t = $("toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 1600); }
function load(){ try{ const s = JSON.parse(localStorage.getItem(LSK) || "null"); if (s && s.formulas) return s; }catch(e){} return {formulas: {}, spans: [], indices: {}, families: {}, steps: []}; }
function save(){ try{ localStorage.setItem(LSK, JSON.stringify(S)); }catch(e){ toast("⚠ could not save — export!"); } paintCounter(); }
let S = load();
const undoStack = [];
function snap(){ undoStack.push(JSON.stringify(S)); if (undoStack.length > 200) undoStack.shift(); }
function undo(){ const u = undoStack.pop(); if (!u){ toast("nothing to undo"); return; } S = JSON.parse(u); save(); paintAll(); toast("undone"); }

/* ---------- header ---------- */
$("title").textContent = D.paper.title || KEY;
$("pk").innerHTML = esc(KEY) + (D.paper.year ? " · " + esc(D.paper.year) : "") + (D.paper.venue ? " · " + esc(D.paper.venue) : "") + (D.paper.doi ? ' · <a href="https://doi.org/' + esc(D.paper.doi) + '" target="_blank" rel="noopener">doi</a>' : "");
function paintCounter(){
  const c = D.paper.counts, ic = D.indices.counts;
  const marked = Object.values(S.formulas).filter(v => v === "formula").length, rejected = Object.values(S.formulas).filter(v => v === "not").length;
  const li = Object.keys(S.indices).length, nl = Object.keys(D.indices.letters).length;
  $("counter").innerHTML = "<span>display " + (c.display||0) + "</span><span>inline statements " + ((c.inline_statement||0)+(c.inline_operator||0)) + "</span><span>symbol mentions " + (c.inline_symbol||0) + "</span><span>notation rows " + (c.notation_rows||0) + "</span><span>index letters " + (ic.letters_index||0) + " + alias " + (ic.letters_alias||0) + " + candidate " + (ic.letters_candidate||0) + "</span><span><b>you:</b> " + marked + " marked · " + rejected + " rejected · " + S.spans.length + " by hand · " + li + "/" + nl + " letters decided</span>";
}

/* ---------- tabs ---------- */
document.querySelectorAll(".tabs button").forEach(b => b.addEventListener("click", () => {
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x === b));
  ["text","idx","frows","stmts","tables"].forEach(t => $("tab-" + t).classList.toggle("hidden", t !== b.dataset.tab));
  typeset($("tab-" + b.dataset.tab));
}));

/* ---------- text ---------- */
function chipHTML(id){
  const m = D.maths[id]; if (!m) return "";
  const st = S.formulas[id] ? " f-" + S.formulas[id] : "";
  return '<span class="chip c-' + m.cls + st + '" data-id="' + id + '" title="' + esc(m.cls + (m.near && m.near.length ? " · near: " + m.near.join(", ") : "")) + '"><span class="tex" data-tex="' + esc(m.show || m.latex) + '">' + esc(m.show || m.latex) + '</span></span>';
}
function dispHTML(id){
  const m = D.maths[id]; if (!m) return "";
  const key = m.eq || id;
  const st = S.formulas[key] === "not" ? " f-not" : "";
  const label = (m.eq ? m.eq : "not in the dossier") + (m.tag ? " " + m.tag : "") + (m.dups && m.dups.length ? " (also " + m.dups.join(", ") + ")" : "");
  return '<span class="disp' + st + '" id="d-' + key + '" data-key="' + key + '"><span class="eqid"><span>' + esc(label) + '</span><button class="dtoggle bad' + (S.formulas[key] === "not" ? " on" : "") + '" data-key="' + key + '">' + (S.formulas[key] === "not" ? "✗ not a formula" : "✗ reject") + '</button></span><div class="tex" data-tex="' + esc(m.show || m.latex) + '" data-display="1">' + esc(m.show || m.latex) + '</div></span>';
}
function paintText(){
  let html = "", lastSec = null;
  for (const p of D.paras){
    if (p.section !== lastSec){ lastSec = p.section; if (p.section) html += '<h4 class="sec">' + esc(p.section) + "</h4>"; }
    let inner = "";
    for (const s of p.segments){
      if (s[0] === "t") inner += esc(s[1]).replace(/ ¶ /g, "<br>");
      else if (s[0] === "m") inner += chipHTML(s[1]);
      else if (s[0] === "d") inner += dispHTML(s[1]);
    }
    html += '<p class="para" data-i="' + p.i + '">' + (p.region === "abstract" ? "<b>Abstract.</b> " : "") + inner + "</p>";
  }
  $("text").innerHTML = html;
}
function cycle(id){
  snap();
  const cur = S.formulas[id];
  if (!cur) S.formulas[id] = "formula"; else if (cur === "formula") S.formulas[id] = "not"; else delete S.formulas[id];
  save(); refreshMarks(); toast(S.formulas[id] ? id + " → " + S.formulas[id] : id + " cleared");
}
function refreshMarks(){
  document.querySelectorAll(".chip[data-id]").forEach(el => { el.classList.remove("f-formula", "f-not"); const v = S.formulas[el.dataset.id]; if (v) el.classList.add("f-" + v); });
  document.querySelectorAll(".disp[data-key]").forEach(el => { const v = S.formulas[el.dataset.key] === "not"; el.classList.toggle("f-not", v); const b = el.querySelector(".dtoggle"); b.classList.toggle("on", v); b.textContent = v ? "✗ not a formula" : "✗ reject"; });
  document.querySelectorAll("#stmts .stmt").forEach(el => { const v = S.formulas[el.dataset.id]; el.querySelectorAll("button[data-v]").forEach(b => b.classList.toggle("on", b.dataset.v === v)); });
}
$("text").addEventListener("click", e => {
  const b = e.target.closest("button.dtoggle");
  if (b){ snap(); if (S.formulas[b.dataset.key] === "not") delete S.formulas[b.dataset.key]; else S.formulas[b.dataset.key] = "not"; save(); refreshMarks(); return; }
  const chip = e.target.closest(".chip[data-id]");
  if (chip){ const m = D.maths[chip.dataset.id]; if (m.cls !== "symbol") cycle(chip.dataset.id); else toast("symbol mention" + (m.near && m.near.length ? " · " + m.near.join(", ") : "")); }
});

/* ---------- selection → formula ---------- */
function selectionInfo(){
  const sel = window.getSelection(); if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const text = sel.toString().trim(); if (text.length < 2) return null;
  const r = sel.getRangeAt(0);
  const p = (r.commonAncestorContainer.nodeType === 1 ? r.commonAncestorContainer : r.commonAncestorContainer.parentElement).closest("p.para");
  if (!p) return null;
  return {para: parseInt(p.dataset.i, 10), text: text};
}
document.addEventListener("selectionchange", () => { const info = selectionInfo(); $("selbar").classList.toggle("hidden", !info); });
$("markSel").addEventListener("click", () => {
  const info = selectionInfo(); if (!info) return;
  snap(); S.spans.push({para: info.para, text: info.text, latex: info.text}); save(); paintSpans(); window.getSelection().removeAllRanges(); $("selbar").classList.add("hidden"); toast("marked: " + info.text.slice(0, 40));
});

/* ---------- statements ---------- */
function ctxHTML(id){
  const m = D.maths[id]; const p = D.paras.find(x => x.i === m.block); if (!p) return "";
  let out = "", hit = false;
  for (const s of p.segments){
    if (s[0] === "t") out += esc(s[1]);
    else if (s[0] === "m"){ const mm = D.maths[s[1]]; out += s[1] === id ? "<b>⟨" + esc(mm.show || mm.latex) + "⟩</b>" : "⟨" + esc((mm.show || mm.latex).slice(0, 30)) + "⟩"; if (s[1] === id) hit = true; }
    else if (s[0] === "d") out += " [display] ";
  }
  const i = out.indexOf("<b>");
  return out.slice(Math.max(0, i - 220), i + 400) + (hit ? "" : "");
}
function paintStmts(){
  $("stmts").innerHTML = D.statements.map(id => {
    const m = D.maths[id]; const v = S.formulas[id];
    return '<div class="stmt" data-id="' + id + '"><div><div class="chip c-' + m.cls + '"><span class="tex" data-tex="' + esc(m.show || m.latex) + '">' + esc(m.show || m.latex) + '</span></div> <span class="ev">' + esc(id) + (m.near && m.near.length ? " · near: " + esc(m.near.join(", ")) : "") + '</span><div class="ctx">' + ctxHTML(id) + '</div></div><div class="btns"><button data-v="formula"' + (v === "formula" ? ' class="on"' : "") + '>✓ formula</button><button data-v="not" class="bad' + (v === "not" ? " on" : "") + '">✗ not</button></div></div>';
  }).join("") || '<div class="ev">no inline statements found</div>';
}
$("stmts").addEventListener("click", e => {
  const b = e.target.closest("button[data-v]"); if (!b) return;
  const id = b.closest(".stmt").dataset.id; snap();
  if (S.formulas[id] === b.dataset.v) delete S.formulas[id]; else S.formulas[id] = b.dataset.v;
  save(); refreshMarks();
});
function paintSpans(){
  $("spans").innerHTML = S.spans.map((s, i) => '<div class="stmt" data-i="' + i + '"><div><div class="ev">paragraph ' + s.para + ' · “' + esc(s.text.slice(0, 120)) + '”</div><input type="text" class="wide" data-i="' + i + '" value="' + esc(s.latex || s.text) + '" placeholder="LaTeX of the formula"></div><div class="btns"><button class="bad" data-rm="' + i + '">✕ remove</button></div></div>').join("") || '<div class="ev">nothing marked by hand yet — select text in the paper and press the button</div>';
}
$("spans").addEventListener("click", e => { const b = e.target.closest("button[data-rm]"); if (!b) return; snap(); S.spans.splice(parseInt(b.dataset.rm, 10), 1); save(); paintSpans(); });
$("spans").addEventListener("change", e => { const inp = e.target.closest("input[data-i]"); if (!inp) return; snap(); S.spans[parseInt(inp.dataset.i, 10)].latex = inp.value; save(); });

/* ---------- indices ---------- */
const ORDER = {index: 0, alias: 1, candidate: 2, juxtaposed: 3};
function evHTML(e){
  const parts = [];
  if (e.bound_rows) parts.push("bound " + e.bound_rows + "×");
  if (e.capped && Object.keys(e.capped).length) parts.push("capped by " + Object.keys(e.capped).join("/"));
  if (e.prose_rows) parts.push("prose " + e.prose_rows + "×");
  if (e.table_rows) parts.push("table" + (e.desc ? ": “" + e.desc.slice(0, 50) + "”" : ""));
  if (e.sub_rows) parts.push("subscript in " + e.sub_rows + " rows (" + Object.keys(e.bases).slice(0, 5).join(", ") + ")");
  if (e.sup_rows) parts.push("superscript " + e.sup_rows + "×");
  const fams = Object.entries(e.families || {}); if (fams.length > 1) parts.push('<span class="flag warn">ranges over ' + fams.map(([f, n]) => f + "×" + n).join(", ") + "</span>");
  const av = Object.entries(e.alias_votes || {}); if (av.length) parts.push("position votes " + av.map(([f, n]) => f + "×" + n).join(", "));
  return parts.join(" · ");
}
function paintLetters(){
  const names = Object.keys(D.indices.letters).sort((a, b) => (ORDER[D.indices.letters[a].verdict] - ORDER[D.indices.letters[b].verdict]) || a.localeCompare(b));
  let html = "<tr><th>letter</th><th>rule</th><th>family</th><th>evidence</th><th>rows</th><th>your verdict</th></tr>";
  for (const n of names){
    const e = D.indices.letters[n]; const h = S.indices[n] || {};
    const fam = h.family !== undefined ? h.family : (e.family || "");
    html += '<tr class="v-' + e.verdict + (h.verdict === "not" ? " h-not" : "") + '" data-n="' + esc(n) + '"><td class="mono">' + esc(n) + '</td><td class="ev">' + esc(e.verdict) + '<br><span class="flag">' + esc(e.rule || "") + '</span></td><td><input type="text" data-fam="' + esc(n) + '" value="' + esc(fam) + '" placeholder="family"></td><td class="ev">' + evHTML(e) + '</td><td>' + (e.rows || []).slice(0, 6).map(r => '<span class="rowref" data-row="' + esc(r) + '">' + esc(r) + '</span>').join("") + '</td><td class="btns"><button data-v="index"' + (h.verdict === "index" ? ' class="on"' : "") + '>✓ index</button><button data-v="not" class="bad' + (h.verdict === "not" ? " on" : "") + '">✗ not</button><button data-v="unsure" class="mid' + (h.verdict === "unsure" ? " on" : "") + '">?</button></td></tr>';
  }
  $("letters").innerHTML = html;
  const fams = Object.values(D.indices.families).sort((a, b) => a.name.localeCompare(b.name));
  let fh = "<tr><th>family</th><th>letters</th><th>declared</th><th>the paper says</th><th>rename</th><th>your verdict</th></tr>";
  for (const f of fams){
    const h = S.families[f.name] || {};
    fh += '<tr' + (h.verdict === "not" ? ' class="h-not"' : "") + ' data-f="' + esc(f.name) + '"><td class="mono">' + esc(f.name) + (f.cap ? ' <span class="flag">range 1..' + esc(f.name) + '</span>' : "") + '</td><td class="mono">' + Object.entries(f.letters || {}).map(([l, n]) => l + "×" + n).join(", ") + '</td><td class="ev">' + (f.declared_as ? "as " + esc(f.declared_as) : '<span class="flag warn">new</span>') + '</td><td class="ev">' + esc(f.desc || "") + '</td><td><input type="text" data-ren="' + esc(f.name) + '" value="' + esc(h.rename || "") + '" placeholder="same as…"></td><td class="btns"><button data-v="family"' + (h.verdict === "family" ? ' class="on"' : "") + '>✓ family</button><button data-v="not" class="bad' + (h.verdict === "not" ? " on" : "") + '">✗ not</button><button data-v="unsure" class="mid' + (h.verdict === "unsure" ? " on" : "") + '">?</button></td></tr>';
  }
  $("families").innerHTML = fh;
  const dl = D.indices.declared_index || [], asl = D.indices.declared_index_are_letters || [];
  $("declared").textContent = dl.length ? "sidecar declares %@ index: " + dl.join(", ") + (asl.length ? " — of which dummy letters, not families: " + asl.join(", ") : "") : "no %@ index lines in the sidecar yet";
}
$("letters").addEventListener("click", e => {
  const ref = e.target.closest(".rowref"); if (ref){ jump(ref.dataset.row); return; }
  const b = e.target.closest("button[data-v]"); if (!b) return;
  const n = b.closest("tr").dataset.n; snap();
  const cur = S.indices[n] || {}; const fam = b.closest("tr").querySelector("input[data-fam]").value.trim();
  if (cur.verdict === b.dataset.v) delete S.indices[n]; else S.indices[n] = {verdict: b.dataset.v, family: fam};
  save(); paintLetters(); paintFormulas();
});
$("letters").addEventListener("change", e => {
  const inp = e.target.closest("input[data-fam]"); if (!inp) return; snap();
  const n = inp.dataset.fam; const cur = S.indices[n] || {verdict: "index"}; S.indices[n] = {verdict: cur.verdict, family: inp.value.trim()}; save(); paintLetters();
});
$("families").addEventListener("click", e => {
  const b = e.target.closest("button[data-v]"); if (!b) return;
  const f = b.closest("tr").dataset.f; snap();
  const cur = S.families[f] || {}; const ren = b.closest("tr").querySelector("input[data-ren]").value.trim();
  if (cur.verdict === b.dataset.v) delete S.families[f]; else S.families[f] = {verdict: b.dataset.v, rename: ren};
  save(); paintLetters();
});
$("families").addEventListener("change", e => {
  const inp = e.target.closest("input[data-ren]"); if (!inp) return; snap();
  const f = inp.dataset.ren; const cur = S.families[f] || {verdict: "family"}; S.families[f] = {verdict: cur.verdict, rename: inp.value.trim()}; save(); paintLetters();
});
function jump(row){
  const el = row.startsWith("eq-") ? $("d-" + row) : document.querySelector('.chip[data-id="' + row + '"]');
  if (!el){ toast(row + " is not on this page"); return; }
  document.querySelectorAll(".tabs button").forEach(x => x.classList.toggle("on", x.dataset.tab === "text"));
  ["text","idx","frows","stmts","tables"].forEach(t => $("tab-" + t).classList.toggle("hidden", t !== "text"));
  if (el.scrollIntoView) el.scrollIntoView({block: "center"}); el.style.outline = "3px solid var(--accent2)"; setTimeout(() => el.style.outline = "", 1500);
}

/* ---------- formulas × indices ---------- */
const OPEN = new Set(["candidate", "juxtaposed", "label"]);
function letterState(l){ const e = D.indices.letters[l] || {}; const h = S.indices[l] || {}; return {e, h, fam: h.family !== undefined && h.family !== "" ? h.family : (e.family || "?"), open: h.verdict ? h.verdict === "unsure" : (!e.verdict || OPEN.has(e.verdict))}; }
function lchipHTML(l, role){
  const st = letterState(l);
  return '<button class="lchip v-' + esc(st.e.verdict || "none") + (st.h.verdict ? " h-" + esc(st.h.verdict) : "") + '" data-l="' + esc(l) + '" title="' + esc((st.e.rule || "no rule") + (st.e.desc ? " · " + st.e.desc : "")) + '">' + esc(l) + ' → ' + esc(st.fam) + '<small>' + esc(role) + (st.e.verdict ? " · " + esc(st.e.verdict) : "") + '</small></button><button class="fam" data-f="' + esc(l) + '" title="set the family of ' + esc(l) + '">✎</button>';
}
function binderText(b){
  if (b.kind === "range") return b.letters.join(", ") + " = " + (b.lo || "?") + " … " + (b.hi || "?") + (b.family ? " (family " + b.family + ")" : " (no family: not a symbol)");
  return (b.kind === "tuple" ? "(" + b.letters.join(", ") + ")" : b.letters.join(", ")) + " ∈ " + (b.family || "?");
}
function paintFormulas(){
  const rows = D.indices.rows || {}; const only = $("onlyOpen").checked;
  const order = [];
  for (const id of Object.keys(D.maths).sort()){
    const m = D.maths[id];
    const key = m.where === "display" ? (m.eq || null) : (m.where === "inline" && (m.cls === "statement" || m.cls === "operator") ? id : null);
    if (key && rows[key]) order.push([key, id]);
  }
  let html = "", shown = 0;
  for (const [key, id] of order){
    const m = D.maths[id]; const r = rows[key];
    const letters = Object.entries(r.letters).sort((a, b) => (a[1] === "binder" ? 0 : 1) - (b[1] === "binder" ? 0 : 1) || a[0].localeCompare(b[0]));
    if (only && !letters.some(([l]) => letterState(l).open)) continue;
    shown++;
    html += '<div class="frow" data-key="' + esc(key) + '"><div class="fid">' + esc(key) + (m.tag ? " " + esc(m.tag) : "") + (m.where === "inline" ? " · in the prose" : "") + ' <span class="rowref" data-row="' + esc(key) + '">show in text</span></div><div class="ftex tex" data-tex="' + esc(m.show || m.latex) + '"' + (m.where === "display" ? ' data-display="1"' : "") + '>' + esc(m.show || m.latex) + '</div>' + (r.binders.length ? '<div class="ev">binds: ' + esc(r.binders.map(binderText).join(" · ")) + "</div>" : '<div class="ev">no binder in this row</div>') + '<div class="lchips">' + letters.map(([l, role]) => lchipHTML(l, role)).join("") + "</div></div>";
  }
  $("frows").innerHTML = html || '<div class="ev">' + (only ? "no formula with undecided letters — done here" : "no formulas with index letters") + '</div>';
  $("frows").dataset.shown = shown;
}
function cycleLetter(l){
  snap(); const cur = (S.indices[l] || {}).verdict; const fam = (S.indices[l] || {}).family;
  const next = !cur ? "index" : cur === "index" ? "not" : cur === "not" ? "unsure" : null;
  if (next) S.indices[l] = {verdict: next, family: fam !== undefined ? fam : ((D.indices.letters[l] || {}).family || "")}; else delete S.indices[l];
  save(); paintFormulas(); paintLetters(); toast(l + (next ? " → " + next : " cleared"));
}
$("frows").addEventListener("click", e => {
  const ref = e.target.closest(".rowref"); if (ref){ jump(ref.dataset.row); return; }
  const f = e.target.closest("button.fam");
  if (f){ const l = f.dataset.f; const cur = S.indices[l] || {}; const v = window.prompt ? window.prompt("family of " + l + " (the set it ranges over)", cur.family !== undefined ? cur.family : ((D.indices.letters[l] || {}).family || "")) : null; if (v === null) return; snap(); S.indices[l] = {verdict: cur.verdict || "index", family: v.trim()}; save(); paintFormulas(); paintLetters(); return; }
  const b = e.target.closest("button.lchip"); if (b) cycleLetter(b.dataset.l);
});
$("onlyOpen").addEventListener("change", () => { paintFormulas(); typeset($("tab-frows")); });

/* ---------- notation tables ---------- */
function cellHTML(text){
  return esc(text).replace(/⟨([^⟨⟩]*)⟩/g, (_, t) => '<span class="tex" data-tex="' + t + '">' + t + "</span>");
}
function paintTables(){
  let html = "";
  for (const t of D.tables){
    html += "<h3>" + esc(t.label || t.id) + " · " + esc(t.caption) + "</h3><table class='grid notation'>";
    for (const r of t.rows) html += "<tr>" + r.cells.map(c => (r.header ? "<th>" : "<td>") + cellHTML(c) + (r.header ? "</th>" : "</td>")).join("") + "</tr>";
    html += "</table>";
  }
  if (D.deflists.length){ html += "<h3>Definition list</h3><table class='grid notation'>" + D.deflists.map(it => "<tr><td>" + cellHTML(it.term) + "</td><td>" + cellHTML(it.def) + "</td></tr>").join("") + "</table>"; }
  $("tables").innerHTML = html || '<div class="ev">no notation table detected in this paper</div>';
}

/* ---------- typesetting ---------- */
const typesetDone = new WeakSet();
function typeset(root){
  if (!window.MathJax || !MathJax.typesetPromise){ return; }
  const els = Array.from((root || document).querySelectorAll(".tex")).filter(el => !typesetDone.has(el));
  if (!els.length) return;
  els.forEach(el => { typesetDone.add(el); const tex = el.dataset.tex || ""; el.textContent = (el.dataset.display ? "\\[" : "\\(") + tex + (el.dataset.display ? "\\]" : "\\)"); });
  MathJax.typesetPromise(els).catch(() => els.forEach(el => { el.textContent = el.dataset.tex || ""; el.classList.add("raw"); }));
}
function whenMathJax(fn){ let n = 0; const t = setInterval(() => { if (window.MathJax && MathJax.typesetPromise){ clearInterval(t); fn(); } else if (++n > 50) clearInterval(t); }, 200); }

/* ---------- export / import ---------- */
function exportPayload(){
  return {schema_version: "discover-decisions-1", paper_key: KEY, exported: new Date().toISOString(), labeller: "human", formulas: S.formulas, spans: S.spans, indices: S.indices, families: S.families};
}
function exportJSON(){
  const blob = new Blob([JSON.stringify(exportPayload(), null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "discover_decisions_" + KEY + "_" + new Date().toISOString().slice(0, 10) + ".json";
  document.body.appendChild(a); a.click(); a.remove(); toast("exported");
}
function importJSON(text){
  let obj; try{ obj = JSON.parse(text); }catch(e){ toast("not JSON"); return 0; }
  if (!obj || obj.schema_version !== "discover-decisions-1"){ toast("not a discover-decisions-1 file"); return 0; }
  if (obj.paper_key !== KEY){ toast("that file is for " + obj.paper_key); return 0; }
  snap(); Object.assign(S.formulas, obj.formulas || {}); Object.assign(S.indices, obj.indices || {}); Object.assign(S.families, obj.families || {});
  const seen = new Set(S.spans.map(s => s.para + "|" + s.text)); for (const s of obj.spans || []) if (!seen.has(s.para + "|" + s.text)) S.spans.push(s);
  save(); paintAll(); toast("imported (last wins)"); return 1;
}
$("exportBtn").addEventListener("click", exportJSON);
$("importBtn").addEventListener("click", () => $("importFile").click());
$("importFile").addEventListener("change", e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => importJSON(String(r.result)); r.readAsText(f); e.target.value = ""; });
$("undoBtn").addEventListener("click", undo);
$("clearBtn").addEventListener("click", () => { if (!confirm("Clear every decision for this paper?")) return; snap(); S = {formulas: {}, spans: [], indices: {}, families: {}, steps: []}; save(); paintAll(); });

function paintAll(){ paintText(); paintStmts(); paintSpans(); paintLetters(); paintFormulas(); paintTables(); paintCounter(); typeset($("tab-text")); }
paintAll();
whenMathJax(() => typeset($("tab-text")));
window.__discover = {S: () => S, cycle, cycleLetter, exportPayload, importJSON, jump, typeset, paintFormulas};
</script>
</body>
</html>
""".replace("__STYLE__", _STYLE)

INDEX_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Discovery round — all papers</title>
<style>__STYLE__ table.grid td{font-size:13px} .prog{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}</style>
</head>
<body>
<div class="brandlogo">__LOGO__</div>
<div class="proto">working prototype — discovery round: deterministic marks, human decisions</div>
<div class="eyebrow">Discovery round</div>
<h1>Every paper, every formula</h1>
<div class="sub">Worklist order: papers where confirmed indices and families could flip the most rows come first ("rows at stake" = rows that fail in an index-related class of the last promotion run). Open a paper, decide its letters in "Formulas × indices", skim "Statements in prose", export. Progress comes from this browser's storage.</div>
<div class="card"><table class="grid" id="papers"></table></div>
<script id="rows" type="application/json">__ROWS__</script>
<script>
"use strict";
const R = JSON.parse(document.getElementById("rows").textContent).papers;
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;"); }
function prog(key){ try{ const s = JSON.parse(localStorage.getItem("discover:state:v1:" + key) || "null"); if (!s) return "—"; return Object.keys(s.formulas).length + " f · " + s.spans.length + " hand · " + Object.keys(s.indices).length + " idx"; }catch(e){ return "—"; } }
let html = "<tr><th>#</th><th>paper</th><th>year</th><th>rows at stake</th><th>display</th><th>inline stmts</th><th>notation rows</th><th>index / alias / cand.</th><th>new families</th><th>progress</th></tr>";
R.forEach((r, i) => { html += '<tr><td class="ev">' + (i + 1) + '</td><td><a href="discover/' + esc(r.key) + '.html">' + esc(r.key) + '</a><div class="ev">' + esc((r.title || "").slice(0, 90)) + '</div></td><td>' + esc(r.year || "") + '</td><td><b>' + (r.at_stake || 0) + '</b> <span class="ev">of ' + (r.rows_probed || 0) + '</span></td><td>' + r.display + '</td><td>' + r.statements + '</td><td>' + r.notation_rows + '</td><td>' + r.letters_index + " / " + r.letters_alias + " / " + r.letters_candidate + '</td><td>' + r.families_new + '</td><td class="prog">' + prog(r.key) + '</td></tr>'; });
document.getElementById("papers").innerHTML = html;
</script>
</body>
</html>
""".replace("__STYLE__", _STYLE)


# -- CLI --------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", nargs="*", help="paper keys to build (index page always lists all)")
    ap.add_argument("--zip", action="store_true", help="also write corpus/review/discover.zip")
    ap.add_argument(
        "--decisions", nargs="*", help="discover-decisions-1 exports to merge and summarise"
    )
    args = ap.parse_args(argv)
    if args.decisions:
        merged = load_decisions([Path(p) for p in args.decisions])
        print(json.dumps(summarize_decisions(merged), indent=1))
        return 0
    rows = build_all(set(args.only) if args.only else None)
    print(f"discover pages: {len(rows)} papers listed in {INDEX_PAGE}", file=sys.stderr)
    if args.zip:
        print(f"zip: {zip_pages()}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
