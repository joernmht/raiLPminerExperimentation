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

and export everything as ``discover-decisions-2`` JSON (one file per paper),
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
from corpusbuilder.roles import propose_roles
from corpusbuilder.vocabgame import LOGO

REVIEW = CORPUS / "review"
OUT_DIR = REVIEW / "discover"
INDEX_PAGE = REVIEW / "discover.html"
ZIP_PATH = REVIEW / "discover.zip"
PAGE_SCHEMA = "discover-page-2"
EXPORT_SCHEMA = "discover-decisions-2"
EXPORT_SCHEMAS = ("discover-decisions-1", "discover-decisions-2")
ROLE_VERDICTS = ("formula", "definition", "index", "domain", "mention", "other")

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


def page_payload(
    rec: dict,
    idx: dict | None,
    d: Dossier | None = None,
    model_spans: dict[str, dict] | None = None,
) -> dict:
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
                    "maths": [c["maths"] for c in r["cells"]],
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
    role_map = propose_roles(rec)
    if model_spans:
        for mid, span in model_spans.items():
            if mid in role_map and role_map[mid]["role"] == "definition" and span:
                role_map[mid]["span"] = span
    return {
        "schema_version": PAGE_SCHEMA,
        "paper": _meta(d, rec),
        "roles": role_map,
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


DEFINITIONS_DIR = CORPUS / "assist" / "definitions"


def load_model_spans(key: str, definitions_dir: Path = DEFINITIONS_DIR) -> dict[str, dict]:
    """Model-refined definition spans (assist stage d), ``{math id: span}``; empty when none."""
    path = definitions_dir / f"{key}.json"
    if not path.exists():
        return {}
    obj = json.loads(path.read_text(encoding="utf-8"))
    return {
        k: v for k, v in (obj.get("spans") or {}).items() if isinstance(v, dict) and v.get("text")
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
        payload = page_payload(rec, idx, d, load_model_spans(d.key))
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
    if obj.get("schema_version") not in EXPORT_SCHEMAS:
        problems.append(f"schema_version is not one of {EXPORT_SCHEMAS}")
    if not obj.get("paper_key"):
        problems.append("paper_key missing")
    for mid, v in (obj.get("formulas") or {}).items():  # v1
        if v not in ("formula", "not"):
            problems.append(f"formulas[{mid}] = {v!r}")
    for mid, v in (obj.get("roles") or {}).items():  # v2
        if not isinstance(v, dict) or v.get("role") not in ROLE_VERDICTS:
            problems.append(f"roles[{mid}] role")
        elif v.get("span") is not None and not (
            isinstance(v["span"], dict) and v["span"].get("text")
        ):
            problems.append(f"roles[{mid}] span")
    for i, s_ in enumerate(obj.get("spans") or obj.get("marks") or []):
        if not isinstance(s_, dict) or "text" not in s_:
            problems.append(f"marks[{i}] has no text")
    for name, v in (obj.get("indices") or {}).items():
        if not isinstance(v, dict) or v.get("verdict") not in ("index", "not", "unsure"):
            problems.append(f"indices[{name}] verdict")
    for name, v in (obj.get("families") or {}).items():
        if not isinstance(v, dict) or v.get("verdict") not in ("family", "not", "unsure"):
            problems.append(f"families[{name}] verdict")
    return problems


def _as_v2(obj: dict) -> dict:
    """A v1 export in v2 terms: formula/not marks become roles, spans become marks."""
    if obj.get("schema_version") == EXPORT_SCHEMA:
        return obj
    roles = {
        mid: {"role": "formula" if v == "formula" else "other", "span": None, "source": "human"}
        for mid, v in (obj.get("formulas") or {}).items()
    }
    return {**obj, "roles": roles, "marks": obj.get("spans") or []}


def load_decisions(paths: list[Path]) -> dict[str, dict]:
    """Merge exports per paper: sorted-file order, last wins (ADR-0008)."""
    merged: dict[str, dict] = {}
    for p in sorted(paths):
        obj = json.loads(p.read_text(encoding="utf-8"))
        if validate_export(obj):
            raise ValueError(f"{p}: " + "; ".join(validate_export(obj)))
        obj = _as_v2(obj)
        key = obj["paper_key"]
        m = merged.setdefault(
            key,
            {
                "paper_key": key,
                "roles": {},
                "marks": [],
                "indices": {},
                "families": {},
                "files": [],
            },
        )
        m["roles"].update(obj.get("roles") or {})
        m["indices"].update(obj.get("indices") or {})
        m["families"].update(obj.get("families") or {})
        seen = {(x.get("para"), x["text"]) for x in m["marks"]}
        for x in obj.get("marks") or []:
            if (x.get("para"), x["text"]) not in seen:
                m["marks"].append(x)
                seen.add((x.get("para"), x["text"]))
        m["files"].append(p.name)
    return merged


def summarize_decisions(merged: dict[str, dict]) -> dict:
    def n(pred) -> int:
        return sum(sum(1 for v in m["roles"].values() if pred(v)) for m in merged.values())

    return {
        "papers": len(merged),
        "formulas": n(lambda v: v["role"] == "formula"),
        "definitions": n(lambda v: v["role"] == "definition"),
        "definitions_with_span": n(lambda v: v["role"] == "definition" and v.get("span")),
        "definitions_span_by_human": n(
            lambda v: (
                v["role"] == "definition" and v.get("span") and v["span"].get("source") == "human"
            )
        ),
        "index_statements": n(lambda v: v["role"] == "index"),
        "domain_statements": n(lambda v: v["role"] == "domain"),
        "mentions": n(lambda v: v["role"] == "mention"),
        "other": n(lambda v: v["role"] == "other"),
        "marks": sum(len(m["marks"]) for m in merged.values()),
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

header.top{margin-bottom:8px}.brandrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.brandrow .proto{margin:0;flex:1}
.tools .grow{flex:1}
.split{display:grid;grid-template-columns:minmax(0,3fr) minmax(340px,2fr);gap:14px;align-items:start}
.pane{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);min-width:0}
.pane.side{position:sticky;top:8px;max-height:calc(100vh - 16px);overflow:auto}
.handle{display:none}
.stabs{display:flex;gap:6px;flex-wrap:wrap;margin:0 0 10px;position:sticky;top:-14px;background:var(--card);padding:6px 0;z-index:3}
.stabs button{border-radius:999px;padding:5px 10px}
.roleCount{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:0 8px;margin-right:4px}
.chip.r-formula{border-color:var(--accent);background:var(--good-soft);cursor:pointer}
.chip.r-definition{border-color:#2e8b57;background:#e7f5ec;cursor:pointer}
.chip.r-index{border-color:var(--tier3);background:var(--tier3-soft);cursor:pointer}
.chip.r-domain{border-color:var(--accent2);background:#e8eefc;cursor:pointer}
.chip.r-mention{border-style:dashed;cursor:pointer}
.chip.r-other{opacity:.7;cursor:pointer}
@media (prefers-color-scheme:dark){.chip.r-definition{background:#0f3a26}.chip.r-domain{background:#1a2a5c}}
.chip.h{box-shadow:inset 0 0 0 2px currentColor}
.chip.sel,.disp.sel{outline:3px solid var(--accent2);outline-offset:2px}
.chip.in-span{box-shadow:0 0 0 2px #2e8b57}
.chip.hasL,.disp.hasL{background:#fff3c4}
@media (prefers-color-scheme:dark){.chip.hasL,.disp.hasL{background:#4a3d10}}
mark.defspan{background:#d9f2e3;color:inherit;border-radius:3px;padding:0 1px}
@media (prefers-color-scheme:dark){mark.defspan{background:#1e5a3a}}
.disp{cursor:pointer}.disp.r-definition{border-left-color:#2e8b57}.disp.r-other{border-left-color:var(--muted);opacity:.7}.disp.h .rolebadge{font-weight:800}
.rolebadge{font-size:11px;border:1px solid var(--line);border-radius:999px;padding:0 7px}
.dhead{display:flex;justify-content:space-between;gap:8px;align-items:baseline}
.dtex{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;overflow-x:auto;padding:6px 0;border-bottom:1px solid var(--line);margin-bottom:6px}
.btns.roles button{border-radius:999px;padding:5px 10px;font-size:12.5px}
button.rb.on{color:#fff;background:var(--muted);border-color:var(--muted)}button.rb.on.r-formula{background:var(--accent);border-color:var(--accent)}button.rb.on.r-definition{background:#2e8b57;border-color:#2e8b57}button.rb.on.r-index{background:var(--tier3);border-color:var(--tier3)}button.rb.on.r-domain{background:var(--accent2);border-color:var(--accent2)}
button.rb.hh{box-shadow:0 0 0 2px var(--ink)}
.defbox{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin:6px 0}
.deftext{font-size:13.5px;line-height:1.5}
.qi{border-top:1px solid var(--line);padding:6px 0}.qi .btns{margin-top:4px}.qi .btns button{padding:3px 8px;font-size:12px}
td.nowrap{white-space:nowrap}button.walk{padding:2px 7px;font-size:12px}
#letters td.btns button,#families td.btns button{padding:3px 7px;font-size:12px}
@media (max-width:999px){
  .split{display:block}
  .pane.side{position:fixed;left:0;right:0;bottom:0;top:auto;max-height:none;height:52vh;transform:translateY(calc(100% - 44px));transition:transform .25s;border-radius:14px 14px 0 0;z-index:8;padding-top:0}
  .pane.side.open{transform:none}
  .handle{display:block;position:sticky;top:0;background:var(--accent);color:#fff;font-weight:800;text-align:center;padding:10px;margin:0 -16px 8px;border-radius:14px 14px 0 0;cursor:pointer;z-index:4}
  .stabs{top:44px}
  body{padding-bottom:60px}
  #selbar{bottom:56px}
}
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
<header class="top">
  <div class="brandrow"><div class="brandlogo">__LOGO__</div><div class="proto">working prototype — discovery round: deterministic marks, human decisions</div></div>
  <div class="eyebrow">Discovery round · <a href="../discover.html">worklist</a></div>
  <h1 id="title"></h1>
  <div class="pk" id="pk"></div>
  <div class="counter" id="counter"></div>
  <div class="tools">
    <button id="prevBtn" title="previous element">◂</button><button id="nextBtn" title="next element">▸</button><button id="nextOpenBtn" title="next undecided candidate">▸ next open</button>
    <span class="grow"></span>
    <button id="exportBtn">⬇ Export</button>
    <button id="importBtn">⬆ Import</button><input type="file" id="importFile" accept="application/json" class="hidden">
    <button id="undoBtn">↶ Undo</button>
    <button id="clearBtn" class="bad">✕ Clear</button>
  </div>
</header>
<div class="split">
  <main class="pane" id="textPane">
    <div class="legend">
      <span><span class="chip r-formula">formula</span></span><span><span class="chip r-definition">definition</span></span><span><span class="chip r-index">index</span></span><span><span class="chip r-domain">domain</span></span><span><span class="chip r-mention">mention</span></span><span><span class="chip r-other">other</span></span>
      <span class="ev">click an element to decide it in the side pane · select text to cite it as the definition or mark a formula</span>
    </div>
    <div id="text"></div>
    <h3>Notation tables and definition lists</h3>
    <div id="tables"></div>
  </main>
  <aside class="pane side" id="sidePane">
    <div class="handle" id="handle"><span id="handleText">▲ Details &amp; indices</span></div>
    <nav class="stabs"><button data-s="details" class="on">Details</button><button data-s="idx">Indices</button><button data-s="open">Open items <span id="openCount"></span></button></nav>
    <section id="s-details"><div id="detail" class="ev">Click a formula or a symbol in the text.</div></section>
    <section id="s-idx" class="hidden">
      <div class="ev">Every index-like letter with the rule that decided it. ◂ ▸ walks the formulas that carry the letter; edit the family to re-assign (same family name = same family).</div>
      <table class="grid" id="letters"></table>
      <h3>Families</h3>
      <table class="grid" id="families"></table>
      <div class="ev" id="declared"></div>
    </section>
    <section id="s-open" class="hidden"><div id="open"></div></section>
  </aside>
</div>
<div id="selbar" class="hidden"><button id="useSel">✎ cite selection as the definition</button><button id="markSel">＋ mark selection as formula</button></div>
<div id="toast"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const KEY = D.paper.key;
const LSK = "discover:state:v2:" + KEY, LSK1 = "discover:state:v1:" + KEY;
const ROLES = ["formula", "definition", "index", "domain", "mention", "other"];
const $ = id => document.getElementById(id);
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function toast(m){ const t = $("toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 1600); }
function fresh(){ return {roles: {}, marks: [], indices: {}, families: {}, sel: null}; }
function migrate(v1){ const s = fresh(); for (const [id, v] of Object.entries(v1.formulas || {})) s.roles[id] = {role: v === "not" ? "other" : "formula"}; s.marks = (v1.spans || []).map(x => ({para: x.para, text: x.text, latex: x.latex || x.text})); s.indices = v1.indices || {}; s.families = v1.families || {}; return s; }
function load(){ try{ const s = JSON.parse(localStorage.getItem(LSK) || "null"); if (s && s.roles) return s; const v1 = JSON.parse(localStorage.getItem(LSK1) || "null"); if (v1 && v1.formulas) return migrate(v1); }catch(e){} return fresh(); }
function save(){ try{ localStorage.setItem(LSK, JSON.stringify(S)); }catch(e){ toast("⚠ could not save — export!"); } paintCounter(); }
let S = load();
const undoStack = [];
function snap(){ undoStack.push(JSON.stringify(S)); if (undoStack.length > 300) undoStack.shift(); }
function undo(){ const u = undoStack.pop(); if (!u){ toast("nothing to undo"); return; } S = JSON.parse(u); save(); paintAll(); toast("undone"); }

/* ---------- element order, plain text, roles ---------- */
const IDS = Object.keys(D.maths).sort();
const EQ2M = {}; for (const id of IDS){ const m = D.maths[id]; if (m.where === "display" && m.eq) EQ2M[m.eq] = id; }
const PARA = {}; for (const p of D.paras) PARA[p.i] = p;
function plainOf(p){ let out = ""; for (const s of p.segments) out += s[0] === "t" ? s[1] : "⟨" + (D.maths[s[1]] ? D.maths[s[1]].latex : "") + "⟩"; return out; }
function paraOf(id){ const m = D.maths[id]; return (m.where === "inline" || m.where === "display") && m.block !== undefined ? PARA[m.block] : null; }
function proposed(id){ return D.roles[id] || {role: "other", rule: "", span: null}; }
function roleOf(id){ const h = S.roles[id]; return h ? h.role : proposed(id).role; }
function decided(id){ return !!S.roles[id]; }
function spanOf(id){ const h = S.roles[id]; if (h && h.span) return h.span; const p = proposed(id); return p.span || null; }
function candidate(id){ const r = proposed(id).role; return r === "definition" || (r === "formula" && D.maths[id].where === "inline"); }

/* ---------- header ---------- */
$("title").textContent = D.paper.title || KEY;
$("pk").innerHTML = esc(KEY) + (D.paper.year ? " · " + esc(D.paper.year) : "") + (D.paper.venue ? " · " + esc(D.paper.venue) : "") + (D.paper.doi ? ' · <a href="https://doi.org/' + esc(D.paper.doi) + '" target="_blank" rel="noopener">doi</a>' : "");
function paintCounter(){
  const c = {}; for (const id of IDS){ const r = roleOf(id); c[r] = (c[r] || 0) + 1; }
  const dec = Object.keys(S.roles).length, li = Object.keys(S.indices).length, nl = Object.keys(D.indices.letters).length;
  $("counter").innerHTML = ROLES.map(r => '<span class="roleCount r-' + r + '">' + r + " " + (c[r] || 0) + "</span>").join("") + '<span><b>you:</b> ' + dec + "/" + IDS.length + " elements · " + li + "/" + nl + " letters · " + S.marks.length + " by hand</span>";
  const open = openItems(); $("openCount").textContent = open.letters.length + open.defs.length + open.forms.length ? "(" + (open.letters.length + open.defs.length + open.forms.length) + ")" : "";
}

/* ---------- text pane ---------- */
function chipHTML(id, inSpan){
  const m = D.maths[id]; if (!m) return "";
  return '<span class="chip r-' + roleOf(id) + (decided(id) ? " h" : "") + (S.sel === id ? " sel" : "") + (inSpan ? " in-span" : "") + '" data-id="' + id + '" title="' + esc(id + " · " + m.cls + " · " + proposed(id).rule) + '"><span class="tex" data-tex="' + esc(m.show || m.latex) + '">' + esc(m.show || m.latex) + '</span></span>';
}
function dispHTML(id){
  const m = D.maths[id]; if (!m) return "";
  const label = (m.eq ? m.eq : "not in the dossier") + (m.tag ? " " + m.tag : "") + (m.dups && m.dups.length ? " (also " + m.dups.join(", ") + ")" : "");
  return '<span class="disp r-' + roleOf(id) + (decided(id) ? " h" : "") + (S.sel === id ? " sel" : "") + '" id="d-' + (m.eq || id) + '" data-id="' + id + '"><span class="eqid"><span>' + esc(label) + '</span><span class="rolebadge">' + esc(roleOf(id)) + '</span></span><div class="tex" data-tex="' + esc(m.show || m.latex) + '" data-display="1">' + esc(m.show || m.latex) + '</div></span>';
}
function paraHTML(p, mark){
  let inner = "", pos = 0;
  for (const s of p.segments){
    if (s[0] === "t"){
      const t = s[1], a = pos, b = pos + t.length;
      if (mark && mark.end > a && mark.start < b){
        const ms = Math.max(mark.start, a) - a, me = Math.min(mark.end, b) - a;
        inner += esc(t.slice(0, ms)).replace(/ ¶ /g, "<br>") + '<mark class="defspan">' + esc(t.slice(ms, me)) + "</mark>" + esc(t.slice(me)).replace(/ ¶ /g, "<br>");
      } else inner += esc(t).replace(/ ¶ /g, "<br>");
      pos = b;
    } else {
      const len = ("⟨" + (D.maths[s[1]] ? D.maths[s[1]].latex : "") + "⟩").length;
      const inSpan = !!(mark && mark.start <= pos && pos + len <= mark.end);
      inner += s[0] === "m" ? chipHTML(s[1], inSpan) : dispHTML(s[1]);
      pos += len;
    }
  }
  return '<p class="para" data-i="' + p.i + '">' + (p.region === "abstract" ? "<b>Abstract.</b> " : "") + inner + "</p>";
}
let markedPara = null;
function markFor(p){ if (S.sel === null) return null; const m = D.maths[S.sel]; if (!m || m.block !== p.i) return null; if (roleOf(S.sel) !== "definition") return null; const sp = spanOf(S.sel); return sp && sp.para === p.i ? sp : null; }
function paintText(){
  let html = "", lastSec = null;
  for (const p of D.paras){
    if (p.section !== lastSec){ lastSec = p.section; if (p.section) html += '<h4 class="sec">' + esc(p.section) + "</h4>"; }
    html += paraHTML(p, markFor(p));
  }
  $("text").innerHTML = html;
}
function repaintPara(i){ const p = PARA[i]; if (!p) return; const el = document.querySelector('p.para[data-i="' + i + '"]'); if (!el) return; const tmp = document.createElement("div"); tmp.innerHTML = paraHTML(p, markFor(p)); el.replaceWith(tmp.firstChild); typeset(document.querySelector('p.para[data-i="' + i + '"]')); }
function refreshEl(id){ const m = D.maths[id]; if (m.block !== undefined && PARA[m.block]){ repaintPara(m.block); } else { document.querySelectorAll('[data-id="' + id + '"]').forEach(el => { el.className = el.className.replace(/\br-\w+/, "r-" + roleOf(id)).replace(/ h\b/, "") + (decided(id) ? " h" : ""); }); } }

/* ---------- selection of an element ---------- */
function select(id, scroll){
  const prev = S.sel; S.sel = id; save();
  if (prev !== null && D.maths[prev] && D.maths[prev].block !== undefined) repaintPara(D.maths[prev].block);
  if (id !== null && D.maths[id] && D.maths[id].block !== undefined) repaintPara(D.maths[id].block);
  document.querySelectorAll(".sel").forEach(el => el.classList.remove("sel"));
  if (id !== null) document.querySelectorAll('[data-id="' + id + '"]').forEach(el => el.classList.add("sel"));
  paintDetails(); showSide("details");
  if (scroll && id !== null){ const el = document.querySelector('#textPane [data-id="' + id + '"]'); if (el && el.scrollIntoView) el.scrollIntoView({block: "center"}); }
}
$("textPane").addEventListener("click", e => {
  const el = e.target.closest("[data-id]"); if (!el) return;
  select(el.dataset.id, false);
});
function step(dir){ const i = S.sel === null ? -1 : IDS.indexOf(S.sel); let j = i + dir; while (j >= 0 && j < IDS.length && D.maths[IDS[j]].where === "other") j += dir; if (j < 0 || j >= IDS.length){ toast("end"); return; } select(IDS[j], true); }
function nextOpen(){ const i = S.sel === null ? -1 : IDS.indexOf(S.sel); for (let j = i + 1; j < IDS.length; j++){ if (candidate(IDS[j]) && !decided(IDS[j])){ select(IDS[j], true); return; } } toast("no open candidate after this one"); }
$("prevBtn").addEventListener("click", () => step(-1)); $("nextBtn").addEventListener("click", () => step(1)); $("nextOpenBtn").addEventListener("click", nextOpen);

/* ---------- details pane ---------- */
function setRole(id, role){
  snap(); const cur = S.roles[id] || {};
  if (cur.role === role) delete S.roles[id]; else S.roles[id] = {role: role, span: cur.span};
  save(); refreshEl(id); paintDetails(); paintOpen(); toast(id + (S.roles[id] ? " → " + role : " cleared"));
}
function citation(sp){ if (!sp) return ""; if (sp.table) return KEY + " · " + sp.table + " · row " + sp.row; if (sp.deflist !== undefined) return KEY + " · definition list · item " + sp.deflist; return KEY + " · paragraph " + sp.para + " · chars " + sp.start + "–" + sp.end; }
function binderText(b){ if (b.kind === "range") return b.letters.join(", ") + " = " + (b.lo || "?") + " … " + (b.hi || "?") + (b.family ? " (family " + b.family + ")" : " (no family)"); return (b.kind === "tuple" ? "(" + b.letters.join(", ") + ")" : b.letters.join(", ")) + " ∈ " + (b.family || "?"); }
const OPENV = new Set(["candidate", "juxtaposed", "label"]);
function letterState(l){ const e = D.indices.letters[l] || {}; const h = S.indices[l] || {}; return {e, h, fam: h.family !== undefined && h.family !== "" ? h.family : (e.family || "?"), open: h.verdict ? h.verdict === "unsure" : (!e.verdict || OPENV.has(e.verdict))}; }
function lchipHTML(l, role){ const st = letterState(l); return '<button class="lchip v-' + esc(st.e.verdict || "none") + (st.h.verdict ? " h-" + esc(st.h.verdict) : "") + '" data-l="' + esc(l) + '" title="' + esc((st.e.rule || "no rule") + (st.e.desc ? " · " + st.e.desc : "")) + '">' + esc(l) + " → " + esc(st.fam) + "<small>" + esc(role || "") + (st.e.verdict ? " · " + esc(st.e.verdict) : "") + "</small></button>" + '<button class="fam" data-f="' + esc(l) + '" title="set the family of ' + esc(l) + '">✎</button>'; }
function rowKeyOf(id){ const m = D.maths[id]; return m.where === "display" ? (m.eq || null) : id; }
function paintDetails(){
  const id = S.sel; const box = $("detail");
  if (id === null || !D.maths[id]){ box.innerHTML = '<div class="ev">Click a formula or a symbol in the text.</div>'; return; }
  const m = D.maths[id], pr = proposed(id), role = roleOf(id), h = S.roles[id];
  let html = '<div class="dhead"><span class="mono">' + esc(id) + (m.eq ? " = " + esc(m.eq) : "") + '</span> <span class="ev">' + esc(m.where + " · " + m.cls) + '</span></div>';
  html += '<div class="dtex tex" data-tex="' + esc(m.show || m.latex) + '"' + (m.where === "display" ? ' data-display="1"' : "") + ">" + esc(m.show || m.latex) + "</div>";
  html += '<div class="ev">proposed: <b>' + esc(pr.role) + "</b> (" + esc(pr.rule || "no rule") + ")" + (h ? ' · <b>yours: ' + esc(h.role) + "</b>" : "") + "</div>";
  html += '<div class="btns roles">' + ROLES.map(r => '<button data-role="' + r + '" class="rb r-' + r + (role === r ? " on" : "") + (h && h.role === r ? " hh" : "") + '">' + r + "</button>").join("") + "</div>";
  if (role === "definition"){
    const sp = spanOf(id);
    html += '<h3>Citable definition</h3>';
    if (sp) html += '<div class="defbox"><div class="deftext">' + esc(sp.text) + '</div><div class="ev">' + esc(citation(sp)) + " · defining words " + esc(sp.position || "?") + " the element · source <b>" + esc(sp.source || "rule") + "</b></div></div>";
    else html += '<div class="ev">no span proposed — select the defining sentence in the text and press “cite selection”.</div>';
    html += '<div class="btns"><button id="resetSpan"' + (h && h.span ? "" : " disabled") + '>↺ back to the proposed span</button></div>';
  }
  const rk = rowKeyOf(id); const row = rk && D.indices.rows ? D.indices.rows[rk] : null;
  if (row){
    html += "<h3>Index letters in this formula</h3>";
    html += row.binders.length ? '<div class="ev">binds: ' + esc(row.binders.map(binderText).join(" · ")) + "</div>" : '<div class="ev">no binder in this row</div>';
    const letters = Object.entries(row.letters).sort((a, b) => (a[1] === "binder" ? 0 : 1) - (b[1] === "binder" ? 0 : 1) || a[0].localeCompare(b[0]));
    html += '<div class="lchips">' + letters.map(([l, r]) => lchipHTML(l, r)).join("") + "</div>";
  }
  box.innerHTML = html; typeset(box);
}
$("detail").addEventListener("click", e => {
  const id = S.sel; if (id === null) return;
  const rb = e.target.closest("button.rb"); if (rb){ setRole(id, rb.dataset.role); return; }
  if (e.target.closest("#resetSpan")){ snap(); if (S.roles[id]) delete S.roles[id].span; save(); repaintPara(D.maths[id].block); paintDetails(); return; }
  const f = e.target.closest("button.fam"); if (f){ editFamily(f.dataset.f); return; }
  const b = e.target.closest("button.lchip"); if (b) cycleLetter(b.dataset.l);
});

/* ---------- selection → citation / formula ---------- */
function plainOffset(paraEl, node, offset){
  let pos = 0;
  const walker = document.createTreeWalker(paraEl, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, null);
  let cur = walker.nextNode();
  while (cur){
    if (cur === node) return pos + (cur.nodeType === 3 ? offset : 0);
    if (cur.nodeType === 1 && cur.dataset && cur.dataset.id){
      if (cur.contains(node)) return pos;
      pos += ("⟨" + D.maths[cur.dataset.id].latex + "⟩").length;
      // skip the chip's subtree
      let nxt = walker.nextNode(); while (nxt && cur.contains(nxt)) nxt = walker.nextNode(); cur = nxt; continue;
    }
    if (cur.nodeType === 3) pos += cur.nodeValue.length;
    if (cur.nodeType === 1 && cur.tagName === "BR") pos += 3;
    cur = walker.nextNode();
  }
  return pos;
}
function selectionInfo(){
  const sel = window.getSelection(); if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0);
  const anc = r.commonAncestorContainer.nodeType === 1 ? r.commonAncestorContainer : r.commonAncestorContainer.parentElement;
  const p = anc && anc.closest ? anc.closest("p.para") : null; if (!p) return null;
  const a = plainOffset(p, r.startContainer, r.startOffset), b = plainOffset(p, r.endContainer, r.endOffset);
  if (b - a < 2) return null;
  const plain = plainOf(PARA[parseInt(p.dataset.i, 10)]);
  return {para: parseInt(p.dataset.i, 10), start: a, end: b, text: plain.slice(a, b), raw: sel.toString()};
}
document.addEventListener("selectionchange", () => { const info = selectionInfo(); $("selbar").classList.toggle("hidden", !info); $("useSel").disabled = !(info && S.sel !== null); });
$("useSel").addEventListener("click", () => {
  const info = selectionInfo(); if (!info || S.sel === null) return;
  const id = S.sel, m = D.maths[id]; const plain = plainOf(PARA[info.para]);
  const span = {para: info.para, start: info.start, end: info.end, text: info.text, position: m.block === info.para && spanOf(id) ? spanOf(id).position : "around", source: "human"};
  snap(); S.roles[id] = {role: "definition", span: span}; save();
  window.getSelection().removeAllRanges(); $("selbar").classList.add("hidden");
  repaintPara(info.para); if (m.block !== info.para) repaintPara(m.block); refreshEl(id); paintDetails(); paintOpen(); toast("cited: " + info.text.slice(0, 40));
});
$("markSel").addEventListener("click", () => {
  const info = selectionInfo(); if (!info) return;
  snap(); S.marks.push({para: info.para, start: info.start, end: info.end, text: info.text, latex: info.text}); save(); paintOpen(); window.getSelection().removeAllRanges(); $("selbar").classList.add("hidden"); toast("marked as formula: " + info.text.slice(0, 40));
});

/* ---------- indices pane ---------- */
const ORDER = {index: 0, alias: 1, candidate: 2, label: 3, juxtaposed: 4};
const FOCUS = {};
function rowsWith(l){ const out = []; for (const [k, r] of Object.entries(D.indices.rows || {})) if (r.letters && r.letters[l] !== undefined){ const id = k.startsWith("eq-") ? EQ2M[k] : k; if (id && D.maths[id]) out.push(id); } return out.sort(); }
function focusLetter(l, dir){ const ids = rowsWith(l); if (!ids.length){ toast(l + ": no formula carries it"); return; } let i = FOCUS[l] === undefined ? (dir < 0 ? ids.length - 1 : 0) : (FOCUS[l] + dir + ids.length) % ids.length; FOCUS[l] = i; document.querySelectorAll(".hasL").forEach(el => el.classList.remove("hasL")); ids.forEach(id => document.querySelectorAll('#textPane [data-id="' + id + '"]').forEach(el => el.classList.add("hasL"))); select(ids[i], true); toast(l + ": formula " + (i + 1) + " of " + ids.length); }
function evHTML(e){ const parts = []; if (e.bound_rows) parts.push("bound " + e.bound_rows + "×"); if (e.capped && Object.keys(e.capped).length) parts.push("capped by " + Object.keys(e.capped).join("/")); if (e.prose_rows) parts.push("prose " + e.prose_rows + "×"); if (e.table_rows) parts.push("table" + (e.desc ? ": “" + e.desc.slice(0, 40) + "”" : "")); if (e.sub_rows) parts.push("subscript in " + e.sub_rows + " rows"); const fams = Object.entries(e.families || {}); if (fams.length > 1) parts.push('<span class="flag warn">' + fams.map(([f, n]) => f + "×" + n).join(", ") + "</span>"); const av = Object.entries(e.alias_votes || {}); if (av.length) parts.push("position votes " + av.map(([f, n]) => f + "×" + n).join(", ")); return parts.join(" · "); }
function paintLetters(){
  const names = Object.keys(D.indices.letters).sort((a, b) => ((ORDER[D.indices.letters[a].verdict] ?? 9) - (ORDER[D.indices.letters[b].verdict] ?? 9)) || a.localeCompare(b));
  let html = "<tr><th>letter</th><th>rule</th><th>family</th><th>evidence</th><th>walk</th><th>verdict</th></tr>";
  for (const n of names){
    const e = D.indices.letters[n]; const h = S.indices[n] || {}; const fam = h.family !== undefined ? h.family : (e.family || "");
    html += '<tr class="v-' + e.verdict + (h.verdict === "not" ? " h-not" : "") + '" data-n="' + esc(n) + '"><td class="mono">' + esc(n) + '</td><td class="ev">' + esc(e.verdict) + '<br><span class="flag">' + esc(e.rule || "") + '</span></td><td><input type="text" data-fam="' + esc(n) + '" value="' + esc(fam) + '" placeholder="family"></td><td class="ev">' + evHTML(e) + '</td><td class="nowrap"><button class="walk" data-w="' + esc(n) + '" data-d="-1">◂</button><button class="walk" data-w="' + esc(n) + '" data-d="1">▸</button></td><td class="btns"><button data-v="index"' + (h.verdict === "index" ? ' class="on"' : "") + '>✓</button><button data-v="not" class="bad' + (h.verdict === "not" ? " on" : "") + '">✗</button><button data-v="unsure" class="mid' + (h.verdict === "unsure" ? " on" : "") + '">?</button></td></tr>';
  }
  $("letters").innerHTML = html;
  const fams = Object.values(D.indices.families).sort((a, b) => a.name.localeCompare(b.name));
  let fh = "<tr><th>family</th><th>letters</th><th>declared</th><th>the paper says</th><th>rename</th><th>verdict</th></tr>";
  for (const f of fams){ const h = S.families[f.name] || {}; fh += '<tr' + (h.verdict === "not" ? ' class="h-not"' : "") + ' data-f="' + esc(f.name) + '"><td class="mono">' + esc(f.name) + (f.cap ? ' <span class="flag">range</span>' : "") + '</td><td class="mono">' + Object.entries(f.letters || {}).map(([l, n]) => l + "×" + n).join(", ") + '</td><td class="ev">' + (f.declared_as ? "as " + esc(f.declared_as) : '<span class="flag warn">new</span>') + '</td><td class="ev">' + esc(f.desc || "") + '</td><td><input type="text" data-ren="' + esc(f.name) + '" value="' + esc(h.rename || "") + '" placeholder="same as…"></td><td class="btns"><button data-v="family"' + (h.verdict === "family" ? ' class="on"' : "") + '>✓</button><button data-v="not" class="bad' + (h.verdict === "not" ? " on" : "") + '">✗</button><button data-v="unsure" class="mid' + (h.verdict === "unsure" ? " on" : "") + '">?</button></td></tr>'; }
  $("families").innerHTML = fh;
  const dl = D.indices.declared_index || [], asl = D.indices.declared_index_are_letters || [];
  $("declared").textContent = dl.length ? "sidecar declares %@ index: " + dl.join(", ") + (asl.length ? " — dummy letters among them: " + asl.join(", ") : "") : "no %@ index lines in the sidecar yet";
}
function cycleLetter(l){ snap(); const cur = (S.indices[l] || {}).verdict; const fam = (S.indices[l] || {}).family; const next = !cur ? "index" : cur === "index" ? "not" : cur === "not" ? "unsure" : null; if (next) S.indices[l] = {verdict: next, family: fam !== undefined ? fam : ((D.indices.letters[l] || {}).family || "")}; else delete S.indices[l]; save(); paintLetters(); paintDetails(); paintOpen(); toast(l + (next ? " → " + next : " cleared")); }
function editFamily(l){ const cur = S.indices[l] || {}; const v = window.prompt ? window.prompt("family of " + l + " (the set it ranges over)", cur.family !== undefined ? cur.family : ((D.indices.letters[l] || {}).family || "")) : null; if (v === null || v === undefined) return; snap(); S.indices[l] = {verdict: cur.verdict || "index", family: String(v).trim()}; save(); paintLetters(); paintDetails(); paintOpen(); }
$("letters").addEventListener("click", e => {
  const wb = e.target.closest("button.walk"); if (wb){ focusLetter(wb.dataset.w, parseInt(wb.dataset.d, 10)); return; }
  const b = e.target.closest("button[data-v]"); if (!b) return;
  const n = b.closest("tr").dataset.n; snap(); const cur = S.indices[n] || {}; const fam = b.closest("tr").querySelector("input[data-fam]").value.trim();
  if (cur.verdict === b.dataset.v) delete S.indices[n]; else S.indices[n] = {verdict: b.dataset.v, family: fam};
  save(); paintLetters(); paintDetails(); paintOpen();
});
$("letters").addEventListener("change", e => { const inp = e.target.closest("input[data-fam]"); if (!inp) return; snap(); const n = inp.dataset.fam; const cur = S.indices[n] || {verdict: "index"}; S.indices[n] = {verdict: cur.verdict, family: inp.value.trim()}; save(); paintLetters(); paintDetails(); });
$("families").addEventListener("click", e => { const b = e.target.closest("button[data-v]"); if (!b) return; const f = b.closest("tr").dataset.f; snap(); const cur = S.families[f] || {}; const ren = b.closest("tr").querySelector("input[data-ren]").value.trim(); if (cur.verdict === b.dataset.v) delete S.families[f]; else S.families[f] = {verdict: b.dataset.v, rename: ren}; save(); paintLetters(); });
$("families").addEventListener("change", e => { const inp = e.target.closest("input[data-ren]"); if (!inp) return; snap(); const f = inp.dataset.ren; const cur = S.families[f] || {verdict: "family"}; S.families[f] = {verdict: cur.verdict, rename: inp.value.trim()}; save(); paintLetters(); });

/* ---------- open items ---------- */
function openItems(){
  const letters = Object.keys(D.indices.letters).filter(l => letterState(l).open).sort();
  const defs = IDS.filter(id => proposed(id).role === "definition" && D.maths[id].where === "inline" && !decided(id));
  const forms = IDS.filter(id => proposed(id).role === "formula" && D.maths[id].where === "inline" && !decided(id));
  return {letters, defs, forms};
}
function quick(id){ const m = D.maths[id]; return '<div class="qi" data-id="' + id + '"><span class="chip r-' + proposed(id).role + '"><span class="tex" data-tex="' + esc(m.show || m.latex) + '">' + esc(m.show || m.latex) + '</span></span> <span class="ev">' + esc((spanOf(id) && spanOf(id).text ? spanOf(id).text : (m.near || []).join(", ")).slice(0, 110)) + '</span><div class="btns"><button class="q" data-role="definition">definition</button><button class="q" data-role="formula">formula</button><button class="q" data-role="mention">mention</button><button class="q" data-role="other">other</button><button class="show">show</button></div></div>'; }
function paintOpen(){
  const o = openItems();
  let html = "<h3>Undecided letters (" + o.letters.length + ")</h3><div class='lchips'>" + (o.letters.map(l => lchipHTML(l, "")).join("") || '<span class="ev">none</span>') + "</div>";
  html += "<h3>Definition candidates (" + o.defs.length + ")</h3>" + (o.defs.slice(0, 40).map(quick).join("") || '<div class="ev">none open</div>') + (o.defs.length > 40 ? '<div class="ev">… ' + (o.defs.length - 40) + " more; use ▸ next open</div>" : "");
  html += "<h3>Inline formula candidates (" + o.forms.length + ")</h3>" + (o.forms.slice(0, 40).map(quick).join("") || '<div class="ev">none open</div>');
  html += "<h3>Marked by hand (" + S.marks.length + ")</h3>" + (S.marks.map((s, i) => '<div class="qi"><div class="ev">paragraph ' + s.para + ' · “' + esc(s.text.slice(0, 100)) + '”</div><input type="text" class="wide" data-i="' + i + '" value="' + esc(s.latex || s.text) + '" placeholder="LaTeX"><div class="btns"><button class="bad" data-rm="' + i + '">✕ remove</button></div></div>').join("") || '<div class="ev">nothing yet — select text in the paper and press “mark selection as formula”</div>');
  $("open").innerHTML = html; typeset($("open"));
}
$("open").addEventListener("click", e => {
  const rm = e.target.closest("button[data-rm]"); if (rm){ snap(); S.marks.splice(parseInt(rm.dataset.rm, 10), 1); save(); paintOpen(); return; }
  const f = e.target.closest("button.fam"); if (f){ editFamily(f.dataset.f); return; }
  const lc = e.target.closest("button.lchip"); if (lc){ cycleLetter(lc.dataset.l); return; }
  const qi = e.target.closest(".qi[data-id]"); if (!qi) return;
  const id = qi.dataset.id;
  if (e.target.closest("button.show")){ select(id, true); return; }
  const q = e.target.closest("button.q"); if (q){ S.sel = id; setRole(id, q.dataset.role); }
});
$("open").addEventListener("change", e => { const inp = e.target.closest("input[data-i]"); if (!inp) return; snap(); S.marks[parseInt(inp.dataset.i, 10)].latex = inp.value; save(); });

/* ---------- tables ---------- */
function cellHTML(text, ids){ let k = 0; return esc(text).replace(/⟨([^⟨⟩]*)⟩/g, (_, t) => { const id = ids && ids[k] ? ids[k] : null; k++; return id ? chipHTML(id, false) : '<span class="tex" data-tex="' + t + '">' + t + "</span>"; }); }
function paintTables(){
  let html = "";
  for (const t of D.tables){ html += "<h4 class='sec'>" + esc(t.label || t.id) + " · " + esc(t.caption) + "</h4><table class='grid notation'>"; for (const r of t.rows) html += "<tr>" + r.cells.map((c, ci) => (r.header ? "<th>" : "<td>") + cellHTML(c, r.maths ? r.maths[ci] : null) + (r.header ? "</th>" : "</td>")).join("") + "</tr>"; html += "</table>"; }
  if (D.deflists.length){ html += "<h4 class='sec'>Definition list</h4><table class='grid notation'>" + D.deflists.map(it => "<tr><td>" + cellHTML(it.term, it.term_maths) + "</td><td>" + cellHTML(it.def, it.def_maths) + "</td></tr>").join("") + "</table>"; }
  $("tables").innerHTML = html || '<div class="ev">no notation table detected in this paper</div>';
}

/* ---------- side pane + mobile sheet ---------- */
function showSide(name){ document.querySelectorAll(".stabs button").forEach(b => b.classList.toggle("on", b.dataset.s === name)); ["details","idx","open"].forEach(n => $("s-" + n).classList.toggle("hidden", n !== name)); if (name === "idx") typeset($("s-idx")); if (name === "open") typeset($("s-open")); }
document.querySelectorAll(".stabs button").forEach(b => b.addEventListener("click", () => { showSide(b.dataset.s); $("sidePane").classList.add("open"); }));
$("handle").addEventListener("click", () => { const open = $("sidePane").classList.toggle("open"); $("handleText").textContent = open ? "▼ Details & indices" : "▲ Details & indices"; });

/* ---------- typesetting ---------- */
const typesetDone = new WeakSet();
function typeset(root){
  if (!window.MathJax || !MathJax.typesetPromise) return;
  const els = Array.from((root || document).querySelectorAll(".tex")).filter(el => !typesetDone.has(el));
  if (!els.length) return;
  els.forEach(el => { typesetDone.add(el); const tex = el.dataset.tex || ""; el.textContent = (el.dataset.display ? "\\[" : "\\(") + tex + (el.dataset.display ? "\\]" : "\\)"); });
  MathJax.typesetPromise(els).catch(() => els.forEach(el => { el.textContent = el.dataset.tex || ""; el.classList.add("raw"); }));
}
function whenMathJax(fn){ let n = 0; const t = setInterval(() => { if (window.MathJax && MathJax.typesetPromise){ clearInterval(t); fn(); } else if (++n > 50) clearInterval(t); }, 200); }

/* ---------- export / import ---------- */
function resolvedRoles(){ const out = {}; for (const [id, h] of Object.entries(S.roles)){ const sp = h.role === "definition" ? (h.span || proposed(id).span || null) : null; out[id] = {role: h.role, span: sp, source: sp ? (sp.source || "rule") : "human"}; } return out; }
function exportPayload(){ return {schema_version: "discover-decisions-2", paper_key: KEY, exported: new Date().toISOString(), labeller: "human", roles: resolvedRoles(), marks: S.marks, indices: S.indices, families: S.families}; }
function exportJSON(){ const blob = new Blob([JSON.stringify(exportPayload(), null, 1)], {type: "application/json"}); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = "discover_decisions_" + KEY + "_" + new Date().toISOString().slice(0, 10) + ".json"; document.body.appendChild(a); a.click(); a.remove(); toast("exported"); }
function importJSON(text){
  let obj; try{ obj = JSON.parse(text); }catch(e){ toast("not JSON"); return 0; }
  if (!obj || !/^discover-decisions-[12]$/.test(obj.schema_version || "")){ toast("not a discover-decisions file"); return 0; }
  if (obj.paper_key !== KEY){ toast("that file is for " + obj.paper_key); return 0; }
  snap();
  if (obj.schema_version === "discover-decisions-1"){ const m = migrate(obj); Object.assign(S.roles, m.roles); for (const x of m.marks) if (!S.marks.some(y => y.para === x.para && y.text === x.text)) S.marks.push(x); Object.assign(S.indices, m.indices); Object.assign(S.families, m.families); }
  else { for (const [id, v] of Object.entries(obj.roles || {})) S.roles[id] = {role: v.role, span: v.span && v.span.source === "human" ? v.span : undefined}; for (const x of obj.marks || []) if (!S.marks.some(y => y.para === x.para && y.text === x.text)) S.marks.push(x); Object.assign(S.indices, obj.indices || {}); Object.assign(S.families, obj.families || {}); }
  save(); paintAll(); toast("imported (last wins)"); return 1;
}
$("exportBtn").addEventListener("click", exportJSON);
$("importBtn").addEventListener("click", () => $("importFile").click());
$("importFile").addEventListener("change", e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => importJSON(String(r.result)); r.readAsText(f); e.target.value = ""; });
$("undoBtn").addEventListener("click", undo);
$("clearBtn").addEventListener("click", () => { if (!confirm("Clear every decision for this paper?")) return; snap(); S = fresh(); save(); paintAll(); });
document.addEventListener("keydown", e => { if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return; if (e.key === "ArrowRight" || e.key === "j") step(1); else if (e.key === "ArrowLeft" || e.key === "k") step(-1); else if (e.key === "n") nextOpen(); else if (S.sel !== null && /^[1-6]$/.test(e.key)) setRole(S.sel, ROLES[parseInt(e.key, 10) - 1]); });

function paintAll(){ paintText(); paintTables(); paintLetters(); paintDetails(); paintOpen(); paintCounter(); document.querySelectorAll(".sel").forEach(el => el.classList.remove("sel")); if (S.sel !== null) document.querySelectorAll('[data-id="' + S.sel + '"]').forEach(el => el.classList.add("sel")); typeset($("textPane")); }
paintAll();
whenMathJax(() => typeset(document));
window.__discover = {S: () => S, select, setRole, cycleLetter, exportPayload, importJSON, typeset, plainOf, plainOffset, selectionInfo, focusLetter, roleOf, spanOf, openItems};
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
