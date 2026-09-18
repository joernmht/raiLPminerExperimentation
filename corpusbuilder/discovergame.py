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
                "runs",
                "desc",
                "multi_family",
            )
        }
        for n, e in idx["letters"].items()
    }
    role_map = propose_roles(rec)
    for mid, span in (model_spans or {}).items():
        if mid not in role_map or role_map[mid]["role"] != "definition":
            continue
        if span.get("defines") is False:
            role_map[mid]["note"] = "model: not a definition"
        elif span.get("text"):
            role_map[mid]["span"] = {
                k: span[k]
                for k in ("para", "start", "end", "text", "position", "source")
                if k in span
            }
            if span.get("differs_from_rule"):
                role_map[mid]["note"] = "model refined the rule's sentence"
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
        k: v
        for k, v in (obj.get("spans") or {}).items()
        if isinstance(v, dict) and v.get("source") == "model"
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
        if not isinstance(v, dict) or v.get("verdict") not in ("index", "not", "unsure", "label"):
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
                "label_proposals": [],
                "files": [],
            },
        )
        m["roles"].update(obj.get("roles") or {})
        m["indices"].update(obj.get("indices") or {})
        m["families"].update(obj.get("families") or {})
        for t in obj.get("label_proposals") or []:
            if isinstance(t, str) and t and t not in m["label_proposals"]:
                m["label_proposals"].append(t)
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
        "letters_label": sum(
            sum(1 for v in m["indices"].values() if v["verdict"] == "label")
            for m in merged.values()
        ),
        "families_confirmed": sum(
            sum(1 for v in m["families"].values() if v["verdict"] == "family")
            for m in merged.values()
        ),
        "label_proposals": sorted({t for m in merged.values() for t in m["label_proposals"]}),
    }


# -- templates --------------------------------------------------------------

_STYLE = r""":root{
  --page1:#f3f7f8;--page2:#e7f1f1;--ink:#0c1f3a;--muted:#566782;
  --card:#ffffff;--card2:#f1f8f8;--line:#d6e6e7;--track:#e6f0f0;
  --accent:#0A777F;--accent2:#2F57B2;--good:#0A777F;--good-soft:#e6f4f4;
  --warn:#C85000;--warn-soft:#fbeada;--bad:#D20F41;--bad-soft:#fae3e9;--tier3:#7369BE;--tier3-soft:#ece9fa;
  --def:#2e8b57;--def-soft:#e7f5ec;--cur:#ffd54a;--cur-soft:#fff3c4;
  --shadow:0 1px 3px rgba(12,40,50,.08),0 6px 18px rgba(12,40,50,.05);
}
@media (prefers-color-scheme:dark){:root{
  --page1:#00103a;--page2:#001a55;--ink:#eaf1ff;--muted:#a0b4d8;
  --card:#0c2766;--card2:#10307c;--line:#2a4a92;--track:#001a55;
  --accent:#36b8bf;--accent2:#7aa2ff;--good:#36b8bf;--good-soft:#0d3350;
  --warn:#f0922e;--warn-soft:#3a2a17;--bad:#ff667e;--bad-soft:#43102a;--tier3:#a98bf0;--tier3-soft:#2a2350;
  --def:#5fcf8f;--def-soft:#0f3a26;--cur:#ffca28;--cur-soft:#4a3d10;
  --shadow:0 1px 3px rgba(0,0,0,.4);}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0;height:100%}
body{background:var(--page1);color:var(--ink);font:17px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;display:grid;grid-template-rows:auto 1fr auto;height:100vh;height:100dvh;overflow:hidden}
#top{background:var(--card);border-bottom:1px solid var(--line);box-shadow:var(--shadow);padding:calc(8px + env(safe-area-inset-top)) 16px 12px;max-height:48vh;overflow:auto;z-index:5;text-align:center}
#midwrap{position:relative;min-height:0}
#mid{position:absolute;inset:0;overflow:auto;padding:10px 14px 110px;background:var(--page2);-webkit-overflow-scrolling:touch}
#navs{position:absolute;left:50%;bottom:16px;transform:translateX(-50%);display:flex;gap:18px;z-index:4}
.nav{width:64px;height:64px;border-radius:50%;border:2px solid var(--accent);background:rgba(255,255,255,.82);color:var(--accent);font-size:32px;font-weight:800;cursor:pointer;padding:0;box-shadow:var(--shadow);backdrop-filter:blur(4px);-webkit-backdrop-filter:blur(4px)}
.nav:active{background:var(--accent);color:#fff}
#walkbox{position:absolute;top:10px;right:14px;background:rgba(255,255,255,.6);color:var(--ink);backdrop-filter:blur(6px);-webkit-backdrop-filter:blur(6px);border:1px solid var(--line);border-radius:10px;padding:4px 10px;font-size:14px;font-weight:700;z-index:4;opacity:.9}
@media (prefers-color-scheme:dark){#walkbox{background:rgba(12,39,102,.6)}.nav{background:rgba(12,39,102,.8)}}
#unsaved{margin-top:10px;font-size:14px;color:var(--warn);background:var(--warn-soft);border:1px solid var(--warn);border-radius:10px;padding:6px 10px}
#bottom{background:var(--card);border-top:1px solid var(--line);padding:16px 18px calc(20px + env(safe-area-inset-bottom));z-index:5;text-align:center}
@media (min-width:900px){body{max-width:960px;margin:0 auto;border-left:1px solid var(--line);border-right:1px solid var(--line)}}
.rounds{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px;justify-content:safe center}
.rounds button{flex:1 0 auto;font:inherit;font-size:14px;font-weight:700;border:1px solid var(--line);background:var(--card2);color:var(--muted);border-radius:999px;padding:6px 12px;white-space:nowrap;min-height:40px}
.rounds button.on{background:var(--accent);border-color:var(--accent);color:#fff}
.rounds .n{font-weight:400;opacity:.85}
.bar{height:5px;border-radius:4px;background:var(--track);overflow:hidden;margin:4px 0 6px}.bar>span{display:block;height:100%;width:0;background:var(--accent);transition:width .3s}
.toprow{display:block}
.actions{display:flex;gap:10px;margin-top:12px;justify-content:center}
.actions button{flex:0 1 190px;font:inherit;font-size:16px;font-weight:700;border:1.5px solid var(--line);background:var(--card2);color:var(--ink);border-radius:14px;padding:8px 12px;min-height:50px}
.actions #menuBtn{flex:0 0 60px}
#menu{margin:0 0 12px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;justify-content:center;border-bottom:1px dashed var(--line);padding-bottom:12px}
#menu button{font:inherit;font-size:15px;font-weight:700;border:1.5px solid var(--line);background:var(--card);color:var(--ink);border-radius:10px;padding:7px 10px}
#menu button.bad{color:var(--bad);border-color:var(--bad)}
.pk{font-size:11.5px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all;flex-basis:100%}
.kicker{font-size:13px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent);font-weight:800}
.big{font-size:42px;font-weight:800;letter-spacing:-.01em;margin:2px 0;line-height:1.15}.big .arrow{color:var(--muted);font-weight:400}.big .q{color:var(--warn)}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.itex{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:16px;overflow-x:auto;padding:6px 0;max-height:18vh;overflow-y:auto;text-align:center}
.itex mjx-container{margin:2px 0 !important;font-size:135%}
.prop{font-size:16px;margin-top:4px;line-height:1.4}.prop b.you{color:var(--accent2)}
.prop b.r-formula{color:var(--accent)}.prop b.r-definition{color:var(--def)}.prop b.r-index{color:var(--tier3)}.prop b.r-domain{color:var(--accent2)}.prop b.r-mention,.prop b.r-other{color:var(--muted)}
.ev{font-size:14px;color:var(--muted)}.pad{padding:20px 0;text-align:center}
.span{background:var(--def-soft);border-left:4px solid var(--def);border-radius:0 8px 8px 0;padding:8px 10px;margin:8px auto 0;text-align:left;max-width:640px}
.deftext{font-size:16px;line-height:1.45;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.walk{display:flex;gap:12px;align-items:center;justify-content:center;margin-top:10px;font-size:15px}
.walk button{font:inherit;font-size:18px;font-weight:800;border:2px solid var(--accent);background:var(--card);color:var(--accent);border-radius:12px;padding:6px 18px;min-width:64px;min-height:44px}
.walk input{font:inherit;font-size:13px;padding:6px 9px;border:1.5px solid var(--line);border-radius:10px;background:var(--card2);color:var(--ink);width:100%}
.flag{display:inline-block;font-size:10.5px;border-radius:999px;padding:0 6px;border:1px solid var(--line);color:var(--muted)}.flag.warn{border-color:var(--warn);color:var(--warn)}
.done{font-size:26px;font-weight:800;padding:8px 0}
h4.sec{font-size:14px;margin:16px 0 4px;color:var(--accent2)}h3.sec{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:18px 0 6px}
p.para{margin:0 0 12px;line-height:1.8;font-size:17px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:6px;padding:0 5px;margin:0 1px;background:var(--card);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:15px;vertical-align:baseline}
.chip mjx-container{margin:0 !important;font-size:105%}
.chip.r-formula{border-color:var(--accent);background:var(--good-soft)}.chip.r-definition{border-color:var(--def);background:var(--def-soft)}.chip.r-index{border-color:var(--tier3);background:var(--tier3-soft)}.chip.r-domain{border-color:var(--accent2)}.chip.r-mention{border-style:dashed}.chip.r-other{opacity:.7}
.chip.h{box-shadow:inset 0 0 0 2px currentColor}
.disp{display:block;border-left:4px solid var(--accent);background:var(--card);border-radius:0 10px 10px 0;padding:6px 10px;margin:8px 0;overflow-x:auto}
.disp.r-definition{border-left-color:var(--def)}.disp.r-other{border-left-color:var(--muted);opacity:.7}
.disp .eqid{font:11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted)}.disp .tex{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;white-space:pre-wrap;word-break:break-all}
.hasL{background:var(--cur-soft) !important;border-color:var(--cur) !important}
.cur{background:var(--cur) !important;color:#111 !important;border-color:#b58900 !important;box-shadow:0 0 0 4px rgba(255,213,74,.45);animation:pulse 1.2s ease-in-out 3}
.disp.cur{background:var(--cur-soft) !important;border-left:6px solid var(--cur) !important;color:inherit !important;box-shadow:0 0 0 3px var(--cur)}
@keyframes pulse{0%{box-shadow:0 0 0 2px rgba(255,213,74,.8)}50%{box-shadow:0 0 0 10px rgba(255,213,74,.15)}100%{box-shadow:0 0 0 4px rgba(255,213,74,.45)}}
mark.defspan{background:var(--def-soft);color:inherit;border-bottom:2px solid var(--def);padding:0 1px}
.chip.in-span{box-shadow:0 0 0 2px var(--def)}
table.grid{border-collapse:collapse;width:100%;font-size:13px}table.grid th,table.grid td{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}table.grid th{color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.notation td:first-child{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.grid3{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.dec.lab2{color:var(--warn)}.dec.lab2.on{background:var(--warn);border-color:var(--warn);color:#fff}.grid1{display:grid;grid-template-columns:1fr}
.dec{font:inherit;font-size:17px;font-weight:800;border:2px solid var(--line);background:var(--card2);color:var(--ink);border-radius:16px;min-height:66px;padding:8px 6px;cursor:pointer;line-height:1.2}
.dec.big{min-height:66px;font-size:18px;background:var(--accent);border-color:var(--accent);color:#fff}
.dec.glow{border-color:var(--accent);box-shadow:0 0 0 3px rgba(10,119,127,.25)}
.dec.on{background:var(--ink);border-color:var(--ink);color:var(--card)}
.dec.ok{color:var(--accent)}.dec.ok.on{background:var(--accent);border-color:var(--accent);color:#fff}
.dec.bad{color:var(--bad)}.dec.bad.on{background:var(--bad);border-color:var(--bad);color:#fff}
.dec.mid{color:var(--muted)}.dec.mid.on{background:var(--muted);border-color:var(--muted);color:#fff}
.dec.r-formula{color:var(--accent)}.dec.r-definition{color:var(--def)}.dec.r-index{color:var(--tier3)}.dec.r-domain{color:var(--accent2)}.dec.r-mention,.dec.r-other{color:var(--muted)}
.dec.r-formula.on{background:var(--accent);border-color:var(--accent);color:#fff}.dec.r-definition.on{background:var(--def);border-color:var(--def);color:#fff}.dec.r-index.on{background:var(--tier3);border-color:var(--tier3);color:#fff}.dec.r-domain.on{background:var(--accent2);border-color:var(--accent2);color:#fff}.dec.r-mention.on,.dec.r-other.on{background:var(--muted);border-color:var(--muted);color:#fff}
.fams{display:flex;gap:10px;overflow-x:auto;margin-top:12px;padding-bottom:4px;justify-content:safe center}
.dec.lab{min-height:44px;font-size:15px;border-radius:999px;padding:4px 14px;white-space:nowrap;color:var(--warn);border-color:var(--warn)}
.dec.lab.on{background:var(--warn);color:#fff}
.dec.fam{min-height:52px;font-size:17px;border-radius:999px;padding:6px 18px;white-space:nowrap;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
#pill{position:fixed;left:50%;bottom:calc(230px + env(safe-area-inset-bottom));transform:translateX(-50%);display:flex;gap:8px;z-index:9;background:var(--card);border:1px solid var(--line);border-radius:999px;padding:6px;box-shadow:var(--shadow)}
#pill button{font:inherit;font-size:16px;font-weight:800;border:0;border-radius:999px;padding:12px 16px;background:var(--def);color:#fff}
#pill #markSel{background:var(--accent)}
#toast{position:fixed;left:50%;bottom:calc(245px + env(safe-area-inset-bottom));transform:translateX(-50%);background:#0c1f3a;color:#fff;padding:8px 14px;border-radius:999px;font-size:13px;opacity:0;transition:opacity .25s;pointer-events:none;z-index:10}
#toast.show{opacity:1}@media (prefers-color-scheme:dark){#toast{background:#eaf1ff;color:#00103a}}
.hidden{display:none !important}
.tex.raw{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
"""

_INDEX_STYLE = r"""
:root{--page1:#f3f7f8;--ink:#0c1f3a;--muted:#566782;--card:#fff;--card2:#f1f8f8;--line:#d6e6e7;--accent:#0A777F;--accent2:#2F57B2;--warn:#C85000;--warn-soft:#fbeada;--shadow:0 1px 3px rgba(12,40,50,.08)}
@media (prefers-color-scheme:dark){:root{--page1:#00103a;--ink:#eaf1ff;--muted:#a0b4d8;--card:#0c2766;--card2:#10307c;--line:#2a4a92;--accent:#36b8bf;--accent2:#7aa2ff;--warn:#f0922e;--warn-soft:#3a2a17}.brandlogo svg text,.brandlogo svg path{fill:#fff !important}}
*{box-sizing:border-box}body{margin:0;padding:12px 16px 40px;background:var(--page1);color:var(--ink);font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;max-width:1080px;margin:0 auto}
.brandlogo{margin:2px 0 10px}.brandlogo svg{height:28px;width:auto;display:block}
.proto{background:var(--warn-soft);color:var(--warn);border:1.5px solid var(--warn);border-radius:10px;padding:5px 10px;font-size:12px;font-weight:650;margin:0 0 10px;text-align:center}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:800}h1{font-size:20px;margin:4px 0 2px}.sub{color:var(--muted);font-size:13px}a{color:var(--accent2)}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);margin-top:12px;overflow-x:auto}
table.grid{border-collapse:collapse;width:100%;font-size:13px}table.grid th,table.grid td{border-top:1px solid var(--line);padding:5px 6px;text-align:left;vertical-align:top}table.grid th{color:var(--muted);font-size:11px;letter-spacing:.08em;text-transform:uppercase}
.ev{font-size:12px;color:var(--muted)}.prog{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px}
"""

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🔎</text></svg>">
<title>Discovery run — __KEY__</title>
<style>__STYLE__</style>
<script>
window.MathJax = {tex: {inlineMath: [["\\(", "\\)"]], displayMath: [["\\[", "\\]"]], packages: {"[+]": ["ams"]}}, svg: {fontCache: "global"}, startup: {typeset: false}};
</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-svg.js"></script>
</head>
<body>
<header id="top">
  <div class="rounds" id="rounds"></div>
  <div class="bar"><span id="barFill"></span></div>
  <div class="toprow"><div id="item"></div></div>
</header>
<div id="midwrap">
  <main id="mid">
    <div id="text"></div>
    <h3 class="sec">Notation tables and definition lists</h3>
    <div id="tables"></div>
    <div class="ev pad">end of paper</div>
  </main>
  <div id="navs" class="hidden"><button id="navL" class="nav" title="previous formula with this letter">◂</button><button id="navR" class="nav" title="next formula with this letter">▸</button></div>
  <div id="walkbox" class="hidden"></div>
</div>
<div id="pill" class="hidden"><button id="useSel">✎ cite selection</button><button id="markSel">＋ formula</button></div>
<footer id="bottom">
  <div id="menu" class="hidden">
    <div class="pk" id="pk"></div>
    <button id="exportBtn">⬇ Export decisions</button>
    <button id="importBtn">⬆ Import</button><input type="file" id="importFile" accept="application/json" class="hidden">
    <button id="labelBtn">＋ propose a label word</button>
    <button id="clearBtn" class="bad">✕ Clear this paper</button>
    <a href="../discover.html">← worklist</a>
  </div>
  <div id="decide"></div>
  <div class="actions"><button id="menuBtn" title="menu">☰</button><button id="undoBtn" title="undo">↶ undo</button><button id="skipBtn" title="skip">skip ▸</button></div>
  <div id="unsaved" class="hidden">⚠ not stored in this browser · export (☰) before closing</div>
</footer>
<div id="toast"></div>
<script id="data" type="application/json">__DATA__</script>
<script>
"use strict";
const D = JSON.parse(document.getElementById("data").textContent);
const KEY = D.paper.key;
const LSK = "discover:state:v2:" + KEY, LSK1 = "discover:state:v1:" + KEY;
const ROLES = ["formula", "definition", "index", "domain", "mention", "other"];
const ROUNDS = [["indices", "Indices"], ["defs", "Definitions"], ["forms", "Formulas"], ["families", "Families"]];
const $ = id => document.getElementById(id);
function esc(s){ return String(s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function toast(m){ const t = $("toast"); t.textContent = m; t.classList.add("show"); clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), 1400); }
function fresh(){ return {roles: {}, marks: [], indices: {}, families: {}, labels: {}, sel: null, round: "indices", pos: {}}; }
function migrate(v1){ const s = fresh(); for (const [id, v] of Object.entries(v1.formulas || {})) s.roles[id] = {role: v === "not" ? "other" : "formula"}; s.marks = (v1.spans || []).map(x => ({para: x.para, text: x.text, latex: x.latex || x.text})); s.indices = v1.indices || {}; s.families = v1.families || {}; return s; }
function load(){ try{ const s = JSON.parse(localStorage.getItem(LSK) || "null"); if (s && s.roles){ if (!s.round) s.round = "indices"; if (!s.pos) s.pos = {}; if (!s.labels) s.labels = {}; return s; } const v1 = JSON.parse(localStorage.getItem(LSK1) || "null"); if (v1 && v1.formulas) return migrate(v1); }catch(e){} return fresh(); }
let storeFailed = false;
function save(){ try{ localStorage.setItem(LSK, JSON.stringify(S)); storeFailed = false; }catch(e){ if (!storeFailed) toast("this browser cannot store decisions for a file opened this way — export before you close the page"); storeFailed = true; } const b = $("unsaved"); if (b) b.classList.toggle("hidden", !storeFailed); }
let S = load();
const undoStack = [];
function snap(){ undoStack.push(JSON.stringify(S)); if (undoStack.length > 300) undoStack.shift(); }

/* ---------- data helpers ---------- */
const IDS = Object.keys(D.maths).sort();
const EQ2M = {}; for (const id of IDS){ const m = D.maths[id]; if (m.where === "display" && m.eq) EQ2M[m.eq] = id; }
const PARA = {}; for (const p of D.paras) PARA[p.i] = p;
function plainOf(p){ let out = ""; for (const s of p.segments) out += s[0] === "t" ? s[1] : "⟨" + (D.maths[s[1]] ? D.maths[s[1]].latex : "") + "⟩"; return out; }
function proposed(id){ return D.roles[id] || {role: "other", rule: "", span: null}; }
function roleOf(id){ const h = S.roles[id]; return h ? h.role : proposed(id).role; }
function decided(id){ return !!S.roles[id]; }
function spanOf(id){ const h = S.roles[id]; if (h && h.span) return h.span; return proposed(id).span || null; }
function letterState(l){ const e = D.indices.letters[l] || {}; const h = S.indices[l] || {}; return {e, h, fam: h.family !== undefined && h.family !== "" ? h.family : (e.family || "")}; }
function rowsWith(l){ const out = []; for (const [k, r] of Object.entries(D.indices.rows || {})) if (r.letters && r.letters[l] !== undefined){ const id = k.startsWith("eq-") ? EQ2M[k] : k; if (id && D.maths[id]) out.push(id); } return out.sort(); }
const FAMS = Object.keys(D.indices.families).sort((a, b) => Object.values(D.indices.families[b].letters || {}).reduce((x, y) => x + y, 0) - Object.values(D.indices.families[a].letters || {}).reduce((x, y) => x + y, 0));

/* ---------- queue ---------- */
const VORDER = {candidate: 0, label: 1, juxtaposed: 2, alias: 3, index: 4};
const Q = {
  indices: Object.keys(D.indices.letters).sort((a, b) => ((VORDER[D.indices.letters[a].verdict] ?? 9) - (VORDER[D.indices.letters[b].verdict] ?? 9)) || a.localeCompare(b)),
  defs: IDS.filter(id => proposed(id).role === "definition" && D.maths[id].where === "inline"),
  forms: IDS.filter(id => proposed(id).role === "formula" && D.maths[id].where === "inline"),
  families: Object.keys(D.indices.families).sort(),
};
function isDone(round, item){ return round === "indices" ? !!S.indices[item] : round === "families" ? !!S.families[item] : decided(item); }
function doneCount(round){ return Q[round].filter(it => isDone(round, it)).length; }
function pos(){ const p = S.pos[S.round]; return p === undefined ? -1 : p; }
function current(){ const list = Q[S.round]; const p = pos(); return p >= 0 && p < list.length ? list[p] : null; }
function nextOpen(from){ const list = Q[S.round]; for (let i = from + 1; i < list.length; i++) if (!isDone(S.round, list[i])) return i; for (let i = 0; i <= Math.min(from, list.length - 1); i++) if (!isDone(S.round, list[i])) return i; return -1; }
function goTo(i){ S.pos[S.round] = i; save(); paintAll(true); }
function setRound(r){ S.round = r; if (S.pos[r] === undefined || S.pos[r] < 0) S.pos[r] = nextOpen(-1); save(); paintAll(true); }
let adhoc = null; // an element tapped in the text outside the queue

/* ---------- text ---------- */
function chipHTML(id, inSpan){ const m = D.maths[id]; if (!m) return ""; return '<span class="chip r-' + roleOf(id) + (decided(id) ? " h" : "") + (inSpan ? " in-span" : "") + '" data-id="' + id + '"><span class="tex" data-tex="' + esc(m.show || m.latex) + '">' + esc(m.show || m.latex) + '</span></span>'; }
function dispHTML(id){ const m = D.maths[id]; if (!m) return ""; return '<span class="disp r-' + roleOf(id) + (decided(id) ? " h" : "") + '" data-id="' + id + '"><span class="eqid">' + esc((m.eq || "not in the dossier") + (m.tag ? " " + m.tag : "")) + '</span><div class="tex" data-tex="' + esc(m.show || m.latex) + '" data-display="1">' + esc(m.show || m.latex) + '</div></span>'; }
function paraHTML(p, mark){
  let inner = "", pos = 0;
  for (const s of p.segments){
    if (s[0] === "t"){ const t = s[1], a = pos, b = pos + t.length;
      if (mark && mark.end > a && mark.start < b){ const ms = Math.max(mark.start, a) - a, me = Math.min(mark.end, b) - a; inner += esc(t.slice(0, ms)).replace(/ ¶ /g, "<br>") + '<mark class="defspan">' + esc(t.slice(ms, me)) + "</mark>" + esc(t.slice(me)).replace(/ ¶ /g, "<br>"); }
      else inner += esc(t).replace(/ ¶ /g, "<br>");
      pos = b;
    } else { const len = ("⟨" + (D.maths[s[1]] ? D.maths[s[1]].latex : "") + "⟩").length; const inSpan = !!(mark && mark.start <= pos && pos + len <= mark.end); inner += s[0] === "m" ? chipHTML(s[1], inSpan) : dispHTML(s[1]); pos += len; }
  }
  return '<p class="para" data-i="' + p.i + '">' + (p.region === "abstract" ? "<b>Abstract.</b> " : "") + inner + "</p>";
}
function focusId(){ if (adhoc) return adhoc; if (S.round === "defs" || S.round === "forms") return current(); return null; }
function markFor(p){ const id = focusId(); if (!id) return null; const m = D.maths[id]; if (!m || roleOf(id) !== "definition") return null; const sp = spanOf(id); return sp && sp.para === p.i ? sp : null; }
function paintText(){ let html = "", lastSec = null; for (const p of D.paras){ if (p.section !== lastSec){ lastSec = p.section; if (p.section) html += '<h4 class="sec">' + esc(p.section) + "</h4>"; } html += paraHTML(p, markFor(p)); } $("text").innerHTML = html; }
function repaintPara(i){ const p = PARA[i]; if (!p) return; const el = document.querySelector('p.para[data-i="' + i + '"]'); if (!el) return; const tmp = document.createElement("div"); tmp.innerHTML = paraHTML(p, markFor(p)); el.replaceWith(tmp.firstChild); typeset(document.querySelector('p.para[data-i="' + i + '"]')); }
function refreshEl(id){ const m = D.maths[id]; if (m.block !== undefined && PARA[m.block]) repaintPara(m.block); else document.querySelectorAll('[data-id="' + id + '"]').forEach(el => { el.className = el.className.replace(/\br-\w+/, "r-" + roleOf(id)).replace(/ h\b/, "") + (decided(id) ? " h" : ""); }); }
let lastMarked = null, walk = {letter: null, i: 0};
function light(ids, curId){
  document.querySelectorAll(".cur, .hasL").forEach(el => el.classList.remove("cur", "hasL"));
  ids.forEach(id => document.querySelectorAll('#mid [data-id="' + id + '"]').forEach(el => el.classList.add("hasL")));
  if (curId) document.querySelectorAll('#mid [data-id="' + curId + '"]').forEach(el => el.classList.add("cur"));
  const el = curId ? document.querySelector('#mid [data-id="' + curId + '"]') : null;
  if (el && el.scrollIntoView) el.scrollIntoView({block: "center"});
}
function cellHTML(text, ids){ let k = 0; return esc(text).replace(/⟨([^⟨⟩]*)⟩/g, (_, t) => { const id = ids && ids[k] ? ids[k] : null; k++; return id ? chipHTML(id, false) : '<span class="tex" data-tex="' + t + '">' + t + "</span>"; }); }
function paintTables(){ let html = ""; for (const t of D.tables){ html += "<h4 class='sec'>" + esc(t.label || t.id) + " · " + esc(t.caption) + "</h4><table class='grid notation'>"; for (const r of t.rows) html += "<tr>" + r.cells.map((c, ci) => (r.header ? "<th>" : "<td>") + cellHTML(c, r.maths ? r.maths[ci] : null) + (r.header ? "</th>" : "</td>")).join("") + "</tr>"; html += "</table>"; } if (D.deflists.length){ html += "<h4 class='sec'>Definition list</h4><table class='grid notation'>" + D.deflists.map(it => "<tr><td>" + cellHTML(it.term, it.term_maths) + "</td><td>" + cellHTML(it.def, it.def_maths) + "</td></tr>").join("") + "</table>"; } $("tables").innerHTML = html || '<div class="ev">no notation table detected in this paper</div>'; }
$("mid").addEventListener("click", e => { const el = e.target.closest("[data-id]"); if (!el) return; adhoc = el.dataset.id; paintAll(false); light([], adhoc); });

/* ---------- top card ---------- */
function citation(sp){ if (!sp) return ""; if (sp.table) return sp.table + " · row " + sp.row; if (sp.deflist !== undefined) return "definition list · item " + sp.deflist; return "paragraph " + sp.para + " · chars " + sp.start + "–" + sp.end; }
function binderText(b){ if (b.kind === "range") return b.letters.join(", ") + " = " + (b.lo || "?") + "…" + (b.hi || "?") + (b.family ? " (" + b.family + ")" : ""); return (b.kind === "tuple" ? "(" + b.letters.join(", ") + ")" : b.letters.join(", ")) + " ∈ " + (b.family || "?") + (b.subset ? " [" + b.subset + "]" : ""); }
function evShort(e){ const p = []; if (e.bound_rows) p.push("bound " + e.bound_rows + "×"); if (e.capped && Object.keys(e.capped).length) p.push("capped by " + Object.keys(e.capped).join("/")); if (e.prose_rows) p.push("prose " + e.prose_rows + "×"); if (e.table_rows) p.push("table"); if (e.sub_rows) p.push("in " + e.sub_rows + " rows"); if (e.sup_rows) p.push("as superscript in " + e.sup_rows + " rows"); const fams = Object.entries(e.families || {}); if (fams.length > 1) p.push("ranges over " + fams.map(([f, n]) => f + "×" + n).join(", ")); const av = Object.entries(e.alias_votes || {}); if (av.length) p.push("position votes " + av.map(([f, n]) => f + "×" + n).join(", ")); return p.join(" · "); }
function paintRounds(){
  $("rounds").innerHTML = ROUNDS.map(([r, label]) => '<button data-r="' + r + '" class="' + (S.round === r ? "on" : "") + '">' + label + ' <span class="n">' + doneCount(r) + "/" + Q[r].length + "</span></button>").join("");
  const total = ROUNDS.reduce((a, [r]) => a + Q[r].length, 0), done = ROUNDS.reduce((a, [r]) => a + doneCount(r), 0);
  $("barFill").style.width = (total ? Math.round(100 * done / total) : 0) + "%";
}
function paintTop(){
  const id = adhoc, it = current(); let html = "";
  if (id){
    const m = D.maths[id], pr = proposed(id), h = S.roles[id];
    html += '<div class="kicker">tapped in the text · <span class="mono">' + esc(id) + (m.eq ? " = " + esc(m.eq) : "") + "</span> · " + esc(m.where + " · " + m.cls) + '</div>';
    html += '<div class="itex tex" data-tex="' + esc(m.show || m.latex) + '"' + (m.where === "display" ? ' data-display="1"' : "") + ">" + esc(m.show || m.latex) + "</div>";
    html += propLine(pr, h ? h.role : null);
    if (roleOf(id) === "definition") html += spanLine(spanOf(id));
    html += lettersLine(id);
  } else if (!it){
    html += '<div class="done">' + (Q[S.round].length ? "🎉 round complete" : "nothing to decide in this round") + '<div class="ev">' + (nextRoundWithOpen() ? "next: " + ROUNDS.find(x => x[0] === nextRoundWithOpen())[1] : "all rounds done — export your decisions (☰)") + "</div></div>";
  } else if (S.round === "indices"){
    const st = letterState(it), e = st.e; const ids = rowsWith(it); const famDesc = st.fam && D.indices.families[st.fam] ? D.indices.families[st.fam].desc : "";
    html += '<div class="kicker">index letter ' + (pos() + 1) + " of " + Q.indices.length + "</div>";
    html += '<div class="big mono">' + esc(it) + ' <span class="arrow">→</span> ' + (st.fam ? esc(st.fam) : '<span class="q">?</span>') + "</div>";
    html += '<div class="prop">proposed: <b>' + esc(e.verdict || "?") + "</b> (" + esc(e.rule || "no rule") + ")" + (e.family ? " · family " + esc(e.family) : "") + (famDesc ? ' · <i>' + esc(famDesc.slice(0, 70)) + "</i>" : "") + (e.desc ? ' · paper: “' + esc(e.desc.slice(0, 60)) + "”" : "") + (st.h.verdict ? ' · <b class="you">yours: ' + esc(st.h.verdict) + (st.h.family ? " → " + esc(st.h.family) : "") + "</b>" : "") + "</div>";
    html += '<div class="ev">' + esc(evShort(e)) + "</div>";
    const runs = Object.keys(e.runs || {});
    if (runs.length){
      const partners = [...new Set(runs.flatMap(r => r.split("").filter(ch => ch !== it)))];
      const ptxt = partners.map(pl => { const ps = letterState(pl); return pl + (ps.fam ? " → " + ps.fam : " (unbound)"); }).join(", ");
      html += '<div class="ev">written glued as <span class="mono">' + runs.map(esc).join(", ") + "</span>" + (ptxt ? " · glued to " + esc(ptxt) : "") + "</div>";
      html += '<div class="ev">' + (e.verdict === "juxtaposed" ? "Two indices without a comma? Then ✓ index and pick its family. One word (a label)? Then the chip below." : "No letter of this word is bound anywhere: read as a label word; ✓ index only if it really ranges over a set.") + "</div>";
    }
    if (!ids.length) html += '<div class="ev">no formula carries it in a subscript</div>';
  } else if (S.round === "families"){
    const f = D.indices.families[it], h = S.families[it] || {};
    html += '<div class="kicker">family ' + (pos() + 1) + " of " + Q.families.length + "</div>";
    html += '<div class="big mono">' + esc(it) + (f.cap ? ' <span class="flag">range 1..' + esc(it) + "</span>" : "") + "</div>";
    html += '<div class="prop">letters: <span class="mono">' + esc(Object.entries(f.letters || {}).map(([l, n]) => l + "×" + n).join(", ") || "-") + "</span> · " + (f.declared_as ? "declared as " + esc(f.declared_as) : '<span class="flag warn">not declared</span>') + (f.desc ? ' · paper: “' + esc(f.desc.slice(0, 80)) + "”" : "") + (h.verdict ? ' · <b class="you">yours: ' + esc(h.verdict) + "</b>" : "") + "</div>";
    const subs = Object.entries(f.subsets || {});
    if (subs.length) html += '<div class="ev">used with subsets: <span class="mono">' + subs.map(([k, n]) => esc(k) + "×" + n).join(", ") + "</span> — one family, the subsets become attributes (predicates) of it</div>";
    html += '<div class="walk"><input type="text" id="renameInp" placeholder="same as… (rename)" value="' + esc(h.rename || "") + '"></div>';
  } else {
    const m = D.maths[it], pr = proposed(it), h = S.roles[it];
    html += '<div class="kicker">' + (S.round === "defs" ? "definition candidate " : "inline formula candidate ") + (pos() + 1) + " of " + Q[S.round].length + ' · <span class="mono">' + esc(it) + "</span></div>";
    html += '<div class="itex tex" data-tex="' + esc(m.show || m.latex) + '">' + esc(m.show || m.latex) + "</div>";
    html += propLine(pr, h ? h.role : null);
    if (roleOf(it) === "definition") html += spanLine(spanOf(it));
  }
  $("item").innerHTML = html; typeset($("item"));
  const ri = $("renameInp"); if (ri) ri.addEventListener("change", () => { snap(); const cur = S.families[it] || {verdict: "family"}; S.families[it] = {verdict: cur.verdict, rename: ri.value.trim()}; save(); paintRounds(); });
}
function propLine(pr, yours){ return '<div class="prop">proposed: <b class="r-' + esc(pr.role) + '">' + esc(pr.role) + "</b> (" + esc(pr.rule || "no rule") + ")" + (pr.note ? ' · <span class="flag warn">' + esc(pr.note) + "</span>" : "") + (yours ? ' · <b class="you">yours: ' + esc(yours) + "</b>" : "") + "</div>"; }
function spanLine(sp){ if (!sp) return '<div class="ev">no defining sentence proposed — select it in the text, then “cite selection”</div>'; return '<div class="span"><span class="deftext">“' + esc(sp.text) + '”</span><div class="ev">' + esc(citation(sp)) + " · words " + esc(sp.position || "?") + " · source <b>" + esc(sp.source || "rule") + "</b></div></div>"; }
function lettersLine(id){ const m = D.maths[id]; const rk = m.where === "display" ? (m.eq || null) : id; const row = rk && D.indices.rows ? D.indices.rows[rk] : null; if (!row) return ""; const letters = Object.keys(row.letters).sort(); return '<div class="ev">' + (row.binders.length ? "binds: " + esc(row.binders.map(binderText).join(" · ")) + " · " : "") + "letters: " + letters.map(l => { const st = letterState(l); return '<span class="mono">' + esc(l) + "→" + esc(st.fam || "?") + "</span>"; }).join(", ") + "</div>"; }
function nextRoundWithOpen(){ const i = ROUNDS.findIndex(x => x[0] === S.round); for (let k = 1; k <= ROUNDS.length; k++){ const r = ROUNDS[(i + k) % ROUNDS.length][0]; if (Q[r].some(it => !isDone(r, it))) return r; } return null; }
function walkTo(dir){ const it = current(); if (!it || S.round !== "indices" || adhoc) return; const ids = rowsWith(it); if (!ids.length) return; if (walk.letter !== it){ walk = {letter: it, i: 0}; } walk.i = (walk.i + dir + ids.length) % ids.length; light(ids, ids[walk.i]); paintWalk(); }
function paintWalk(){
  const it = current(); const ids = (!adhoc && it && S.round === "indices") ? rowsWith(it) : [];
  const on = ids.length > 0;
  $("navs").classList.toggle("hidden", !on); $("walkbox").classList.toggle("hidden", !on);
  if (on) $("walkbox").textContent = "formula " + ((walk.letter === it ? walk.i : 0) + 1) + " of " + ids.length;
}
$("navL").addEventListener("click", () => walkTo(-1)); $("navR").addEventListener("click", () => walkTo(1));

/* ---------- bottom: decisions only ---------- */
function paintBottom(){
  const id = adhoc, it = current(); let html = "";
  const roleBtns = (cur, pr) => '<div class="grid3">' + ROLES.map(r => '<button class="dec r-' + r + (cur === r ? " on" : "") + (pr === r ? " glow" : "") + '" data-role="' + r + '">' + (pr === r ? "✓ " : "") + r + "</button>").join("") + "</div>";
  if (id){ html = roleBtns(S.roles[id] ? S.roles[id].role : null, proposed(id).role); }
  else if (!it){ const nr = nextRoundWithOpen(); html = '<div class="grid1">' + (nr ? '<button class="dec big" id="nextRound">▸ ' + esc(ROUNDS.find(x => x[0] === nr)[1]) + "</button>" : '<button class="dec big" id="exportNow">⬇ export decisions</button>') + "</div>"; }
  else if (S.round === "indices"){
    const st = letterState(it); const chips = FAMS.slice(0, 5);
    html = '<div class="grid2"><button class="dec ok' + (st.e.verdict === "label" ? "" : " glow") + '" data-v="index">✓ index' + (st.fam ? " → " + esc(st.fam) : "") + '</button><button class="dec lab2' + (st.e.verdict === "label" ? " glow" : "") + '" data-v="label">label (part of a name)</button><button class="dec bad" data-v="not">✗ not an index</button><button class="dec mid" data-v="unsure">? unsure</button></div>';
    html += '<div class="fams">' + chips.map(f => '<button class="dec fam' + (st.fam === f ? " on" : "") + '" data-fam="' + esc(f) + '">' + esc(f) + "</button>").join("") + '<button class="dec fam" data-fam="…">other…</button></div>';
    const runs = Object.keys(st.e.runs || {});
    if (runs.length) html += '<div class="fams">' + runs.map(r => '<button class="dec lab' + (S.labels[r] ? " on" : "") + '" data-lab="' + esc(r) + '">＋ “' + esc(r) + '” is a label word</button>').join("") + "</div>";
  } else if (S.round === "families"){
    html = '<div class="grid3"><button class="dec ok glow" data-fv="family">✓ family</button><button class="dec bad" data-fv="not">✗ not</button><button class="dec mid" data-fv="unsure">? unsure</button></div>';
  } else { html = roleBtns(S.roles[it] ? S.roles[it].role : null, proposed(it).role); }
  $("decide").innerHTML = html;
}
function advance(){ adhoc = null; const i = nextOpen(pos()); S.pos[S.round] = i; save(); setTimeout(() => paintAll(true), 120); }
function decideRole(id, role){ snap(); const cur = S.roles[id] || {}; S.roles[id] = {role: role, span: cur.span}; save(); refreshEl(id); toast(role); if (adhoc){ adhoc = null; paintAll(false); } else advance(); }
function decideLetter(l, verdict, fam){ snap(); const st = letterState(l); S.indices[l] = {verdict: verdict, family: fam !== undefined ? fam : (st.fam || "")}; save(); toast(l + " → " + verdict + (S.indices[l].family ? " " + S.indices[l].family : "")); advance(); }
$("decide").addEventListener("click", e => {
  const b = e.target.closest("button"); if (!b) return;
  if (b.id === "nextRound"){ setRound(nextRoundWithOpen()); return; }
  if (b.id === "exportNow"){ exportJSON(); return; }
  const id = adhoc || ((S.round === "defs" || S.round === "forms") ? current() : null);
  if (b.dataset.role && id){ decideRole(id, b.dataset.role); return; }
  const it = current();
  if (S.round === "indices" && it){
    if (b.dataset.v){ decideLetter(it, b.dataset.v); return; }
    if (b.dataset.lab){ snap(); if (S.labels[b.dataset.lab]) delete S.labels[b.dataset.lab]; else S.labels[b.dataset.lab] = true; save(); paintBottom(); toast(S.labels[b.dataset.lab] ? "proposed “" + b.dataset.lab + "” for the standard label list" : "proposal withdrawn"); return; }
    if (b.dataset.fam){ let f = b.dataset.fam; if (f === "…"){ f = window.prompt ? window.prompt("family of " + it + " (the set it ranges over)", letterState(it).fam) : null; if (f === null || f === undefined) return; f = String(f).trim(); } decideLetter(it, "index", f); return; }
  }
  if (S.round === "families" && it && b.dataset.fv){ snap(); const ren = $("renameInp") ? $("renameInp").value.trim() : ""; S.families[it] = {verdict: b.dataset.fv, rename: ren}; save(); toast(it + " → " + b.dataset.fv); advance(); }
});
$("rounds").addEventListener("click", e => { const b = e.target.closest("button[data-r]"); if (b){ adhoc = null; setRound(b.dataset.r); } });
$("skipBtn").addEventListener("click", () => { if (adhoc){ adhoc = null; paintAll(true); return; } const i = nextOpen(pos()); if (i < 0 || i === pos()){ toast("nothing else open here"); return; } goTo(i); });
$("undoBtn").addEventListener("click", () => { const u = undoStack.pop(); if (!u){ toast("nothing to undo"); return; } S = JSON.parse(u); save(); paintAll(true); toast("undone"); });
$("menuBtn").addEventListener("click", () => $("menu").classList.toggle("hidden"));

/* ---------- selection → cite / mark ---------- */
function plainOffset(paraEl, node, offset){
  let pos = 0; const walker = document.createTreeWalker(paraEl, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT, null); let cur = walker.nextNode();
  while (cur){
    if (cur === node) return pos + (cur.nodeType === 3 ? offset : 0);
    if (cur.nodeType === 1 && cur.dataset && cur.dataset.id){ if (cur.contains(node)) return pos; pos += ("⟨" + D.maths[cur.dataset.id].latex + "⟩").length; let nxt = walker.nextNode(); while (nxt && cur.contains(nxt)) nxt = walker.nextNode(); cur = nxt; continue; }
    if (cur.nodeType === 3) pos += cur.nodeValue.length;
    if (cur.nodeType === 1 && cur.tagName === "BR") pos += 3;
    cur = walker.nextNode();
  }
  return pos;
}
function selectionInfo(){
  const sel = window.getSelection(); if (!sel || sel.isCollapsed || !sel.rangeCount) return null;
  const r = sel.getRangeAt(0); const anc = r.commonAncestorContainer.nodeType === 1 ? r.commonAncestorContainer : r.commonAncestorContainer.parentElement;
  const p = anc && anc.closest ? anc.closest("p.para") : null; if (!p) return null;
  const a = plainOffset(p, r.startContainer, r.startOffset), b = plainOffset(p, r.endContainer, r.endOffset); if (b - a < 2) return null;
  const pi = parseInt(p.dataset.i, 10); return {para: pi, start: a, end: b, text: plainOf(PARA[pi]).slice(a, b)};
}
document.addEventListener("selectionchange", () => { const info = selectionInfo(); $("pill").classList.toggle("hidden", !info); });
function citeTarget(){ return adhoc || ((S.round === "defs" || S.round === "forms") ? current() : null); }
$("useSel").addEventListener("click", () => {
  const info = selectionInfo(); const id = citeTarget(); if (!info || !id){ toast("pick the element first (tap it), then select its definition"); return; }
  const m = D.maths[id]; snap(); S.roles[id] = {role: "definition", span: {para: info.para, start: info.start, end: info.end, text: info.text, position: (m.block === info.para && spanOf(id)) ? spanOf(id).position : "around", source: "human"}}; save();
  window.getSelection().removeAllRanges(); $("pill").classList.add("hidden"); refreshEl(id); if (m.block !== info.para) repaintPara(info.para); paintTop(); paintBottom(); paintRounds(); toast("cited");
});
$("markSel").addEventListener("click", () => { const info = selectionInfo(); if (!info) return; snap(); S.marks.push({para: info.para, start: info.start, end: info.end, text: info.text, latex: info.text}); save(); window.getSelection().removeAllRanges(); $("pill").classList.add("hidden"); toast("marked as formula: " + info.text.slice(0, 30)); });

/* ---------- typesetting ---------- */
const typesetDone = new WeakSet();
function typeset(root){ if (!window.MathJax || !MathJax.typesetPromise) return; const els = Array.from((root || document).querySelectorAll(".tex")).filter(el => !typesetDone.has(el)); if (!els.length) return; els.forEach(el => { typesetDone.add(el); const tex = el.dataset.tex || ""; el.textContent = (el.dataset.display ? "\\[" : "\\(") + tex + (el.dataset.display ? "\\]" : "\\)"); }); MathJax.typesetPromise(els).catch(() => els.forEach(el => { el.textContent = el.dataset.tex || ""; el.classList.add("raw"); })); }
function whenMathJax(fn){ let n = 0; const t = setInterval(() => { if (window.MathJax && MathJax.typesetPromise){ clearInterval(t); fn(); } else if (++n > 50) clearInterval(t); }, 200); }

/* ---------- export / import ---------- */
function resolvedRoles(){ const out = {}; for (const [id, h] of Object.entries(S.roles)){ const sp = h.role === "definition" ? (h.span || proposed(id).span || null) : null; out[id] = {role: h.role, span: sp, source: sp ? (sp.source || "rule") : "human"}; } return out; }
function exportPayload(){ return {schema_version: "discover-decisions-2", paper_key: KEY, exported: new Date().toISOString(), labeller: "human", roles: resolvedRoles(), marks: S.marks, indices: S.indices, families: S.families, label_proposals: Object.keys(S.labels).sort()}; }
function exportJSON(){
  const name = "discover_decisions_" + KEY + "_" + new Date().toISOString().slice(0, 10) + ".json";
  const text = JSON.stringify(exportPayload(), null, 1);
  if (navigator.share && navigator.canShare && navigator.maxTouchPoints > 0){
    try{ const file = new File([text], name, {type: "application/json"}); if (navigator.canShare({files: [file]})){ navigator.share({files: [file], title: name}).then(() => toast("shared")).catch(() => download(text, name)); return; } }catch(e){}
  }
  download(text, name);
}
function download(text, name){ const blob = new Blob([text], {type: "application/json"}); const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; document.body.appendChild(a); a.click(); a.remove(); toast("exported"); }
function importJSON(text){
  let obj; try{ obj = JSON.parse(text); }catch(e){ toast("not JSON"); return 0; }
  if (!obj || !/^discover-decisions-[12]$/.test(obj.schema_version || "")){ toast("not a discover-decisions file"); return 0; }
  if (obj.paper_key !== KEY){ toast("that file is for " + obj.paper_key); return 0; }
  snap();
  if (obj.schema_version === "discover-decisions-1"){ const m = migrate(obj); Object.assign(S.roles, m.roles); for (const x of m.marks) if (!S.marks.some(y => y.para === x.para && y.text === x.text)) S.marks.push(x); Object.assign(S.indices, m.indices); Object.assign(S.families, m.families); }
  else { for (const [id, v] of Object.entries(obj.roles || {})) S.roles[id] = {role: v.role, span: v.span && v.span.source === "human" ? v.span : undefined}; for (const x of obj.marks || []) if (!S.marks.some(y => y.para === x.para && y.text === x.text)) S.marks.push(x); Object.assign(S.indices, obj.indices || {}); Object.assign(S.families, obj.families || {}); for (const t of obj.label_proposals || []) S.labels[t] = true; }
  save(); paintAll(true); toast("imported (last wins)"); return 1;
}
$("exportBtn").addEventListener("click", exportJSON);
$("importBtn").addEventListener("click", () => $("importFile").click());
$("importFile").addEventListener("change", e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => importJSON(String(r.result)); r.readAsText(f); e.target.value = ""; });
$("labelBtn").addEventListener("click", () => { const v = window.prompt ? window.prompt("label word to add to the standard scanning list (e.g. end, max, st)", "") : null; if (!v) return; snap(); S.labels[String(v).trim().toLowerCase()] = true; save(); toast("proposed “" + String(v).trim() + "”"); });
$("clearBtn").addEventListener("click", () => { if (!confirm("Clear every decision for this paper?")) return; snap(); S = fresh(); save(); paintAll(true); });
document.addEventListener("keydown", e => { if (e.target && (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA")) return; const id = adhoc || ((S.round === "defs" || S.round === "forms") ? current() : null); if (/^[1-6]$/.test(e.key) && id) decideRole(id, ROLES[parseInt(e.key, 10) - 1]); else if (e.key === "ArrowRight") $("skipBtn").click(); else if (e.key === "ArrowLeft" && S.round === "indices") walkTo(-1); else if (e.key === "i" && S.round === "indices" && current()) decideLetter(current(), "index"); else if (e.key === "l" && S.round === "indices" && current()) decideLetter(current(), "label"); else if (e.key === "x" && S.round === "indices" && current()) decideLetter(current(), "not"); });

/* ---------- paint ---------- */
function paintAll(scroll){
  $("pk").innerHTML = esc(KEY) + (D.paper.title ? " · " + esc(D.paper.title.slice(0, 80)) : "");
  if (S.pos[S.round] === undefined || S.pos[S.round] < 0) S.pos[S.round] = nextOpen(-1);
  paintRounds(); paintText(); paintTables(); paintTop(); paintBottom();
  const it = current();
  if (!adhoc && it && S.round === "indices" && walk.letter !== it) walk = {letter: it, i: 0};
  paintWalk();
  if (adhoc) light([], adhoc);
  else if (it && S.round === "indices"){ const ids = rowsWith(it); if (walk.letter !== it) walk = {letter: it, i: 0}; light(ids, ids[walk.i] || null); }
  else if (it && (S.round === "defs" || S.round === "forms")) light([], it);
  else light([], null);
  typeset($("mid"));
}
paintAll(true);
whenMathJax(() => typeset(document));
window.__discover = {S: () => S, Q, current, setRound, goTo, decideRole, decideLetter, exportPayload, importJSON, typeset, plainOf, plainOffset, selectionInfo, roleOf, spanOf, walkTo, rowsWith, isDone, pos};
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
""".replace("__STYLE__", _INDEX_STYLE)


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
