"""Vocabulary round: the human instrument of the vocabulary work (HITL).

Two single-file mobile pages, one item (paper, symbol) at a time:

* ``--mode blind``   -> ``corpus/review/vocab_blind.html``: the fixed-seed gold
  sample (``corpus/vocab_gold.json``). Shows the rows the name occurs in, the
  paper's index families and the abstract — nothing a model proposed, so the
  labels can be compared with the model's proposals afterwards without
  anchoring.
* ``--mode confirm`` -> ``corpus/review/vocab_confirm.html``: the model-proposed
  declarations of the sidecars (assist-v before assist-c, papers with the
  highest row coverage first). Every field is pre-filled from the proposal;
  one tap confirms, any edit is recorded as a change.

Both export ``vocab-decisions-1`` JSON (``labeller: human``) that
``corpusbuilder.vocab --apply-decisions`` writes into the sidecars under a
human block. State lives in ``localStorage`` (``vocab:state:v1:<mode>``);
export/import merge by item id, last wins. The pages carry publisher-derived
text (rows, abstracts) and live in the gitignored ``corpus/review/``: never
copy them into ``docs/``.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.vocabgame --mode blind
    PYTHONPATH=. python3 -m corpusbuilder.vocabgame --mode confirm --limit 20
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from corpusbuilder.game import render_latex
from corpusbuilder.promote import CORPUS, DECLARATIONS, PROMOTED, _rel
from corpusbuilder.vocab import (
    GOLD_PATH,
    _rows_for_names,
    classify_sidecar_lines,
    doc_with_sidecar,
    proposals,
)

REVIEW_DIR = CORPUS / "review"
PROMOTION_PATH = CORPUS / "promotion.json"
SCHEMA = "vocab-items-1"
EXPORT_SCHEMA = "vocab-decisions-1"
KINDS = ("index", "param", "var")
VAR_DOMAINS = ("binary", "integer", "non_negative", "continuous")
PARAM_KINDS = ("scalar", "vector", "matrix", "big_m", "tolerance")
VAR_ROLES = ("primary", "auxiliary", "slack", "indicator")

_NAME_UNDERSCORE_RE = re.compile(
    r"(?<![\\A-Za-z0-9_{])([A-Za-z][A-Za-z0-9]*(?:_[A-Za-z0-9]+)+)(?![A-Za-z0-9_])"
)


def _token_re(name: str) -> re.Pattern[str]:
    """One occurrence of ``name`` as a symbol: bare or ``\\mathit{name}``, not
    glued to another identifier; a following ``_{`` (its subscript) is fine."""
    esc = re.escape(name)
    return re.compile(rf"(?<![A-Za-z0-9_\\])(?:\\mathit\{{{esc}\}}|{esc})(?![A-Za-z0-9]|_(?!\{{))")


def display_latex(row: str, name: str) -> str:
    """A normalized row made displayable: canonical names with underscores
    become ``\\mathit{a\\_b}`` (MathJax would read ``_`` as a subscript), the
    reviewed symbol is set bold, and the game's display repairs apply."""
    out = _NAME_UNDERSCORE_RE.sub(lambda m: r"\mathit{" + m.group(1).replace("_", r"\_") + "}", row)
    esc_name = name.replace("_", r"\_")
    shown = r"\mathbf{" + esc_name + "}"
    pat = re.compile(
        rf"\\mathit\{{{re.escape(esc_name)}\}}"
        rf"|(?<![A-Za-z0-9_\\{{])\\mathit\{{{re.escape(name)}\}}"
        rf"|(?<![A-Za-z0-9_\\{{])(?:{re.escape(name)})(?![A-Za-z0-9]|_(?!\{{))"
    )
    out = pat.sub(lambda m: shown, out)
    return render_latex(out)


def blind_items(gold: dict) -> list[dict]:
    """The gold items with everything model-derived stripped (``kind_guess*``)."""
    items: list[dict] = []
    for it in gold.get("items", []):
        rows = [
            {"name": r["name"], "latex": display_latex(r["latex"], it["name"])}
            for r in it.get("rows", [])
            if r.get("latex")
        ]
        items.append(
            {
                "id": it["id"],
                "paper_key": it["paper_key"],
                "name": it["name"],
                "evidence": it.get("evidence", ""),
                "arities": it.get("arities", {}),
                "rows": rows,
                "families": list(it.get("families", [])),
                "abstract": it.get("abstract", ""),
                "definitions": list(it.get("definitions", [])),
                "mentions": list(it.get("mentions", [])),
                "proposal": None,
            }
        )
    return items


def _coverage_order(promotion_path: Path) -> dict[str, tuple[int, float]]:
    """paper_key -> (promoted, row coverage share) for ordering."""
    if not promotion_path.exists():
        return {}
    report = json.loads(promotion_path.read_text(encoding="utf-8"))
    out: dict[str, tuple[int, float]] = {}
    for p in report.get("papers", []):
        cov = p.get("coverage") or {}
        probed = cov.get("rows_probed") or 0
        share = (cov.get("rows_ok", 0) / probed) if probed else 0.0
        out[p["paper_key"]] = (1 if p.get("promoted") else 0, share)
    return out


def confirm_items(
    *,
    keys: list[str] | None = None,
    limit: int | None = None,
    promoted_dir: Path = PROMOTED,
    declarations_dir: Path = DECLARATIONS,
    promotion_path: Path = PROMOTION_PATH,
    prose_dir: Path = CORPUS / "prose",
) -> list[dict]:
    """Model-proposed declarations as review items, promoted papers and the
    highest row coverage first, stage V lines before stage c lines."""
    order = _coverage_order(promotion_path)
    candidates = sorted(
        p.stem
        for p in declarations_dir.glob("*.tex")
        if not p.name.endswith((".stub.tex", ".vocab.tex"))
    )
    if keys:
        wanted = set(keys)
        candidates = [k for k in candidates if k in wanted]
    candidates.sort(key=lambda k: (-order.get(k, (0, 0.0))[0], -order.get(k, (0, 0.0))[1], k))
    if limit is not None:
        candidates = candidates[:limit]
    items: list[dict] = []
    for key in candidates:
        sidecar = (declarations_dir / f"{key}.tex").read_text(encoding="utf-8")
        props = proposals(sidecar)
        if not props:
            continue
        doc_path = promoted_dir / f"{key}.tex"
        rows: dict[str, str] = {}
        if doc_path.exists():
            rows = _rows_for_names(doc_with_sidecar(doc_path.read_text(encoding="utf-8"), sidecar))
        families = [
            name for record, name, _src in classify_sidecar_lines(sidecar) if record == "index"
        ]
        abstract = ""
        prose_path = prose_dir / f"{key}.json"
        if prose_path.exists():
            try:
                abstract = str(
                    json.loads(prose_path.read_text(encoding="utf-8")).get("abstract") or ""
                )[:2000]
            except (OSError, json.JSONDecodeError):
                abstract = ""
        props.sort(key=lambda p: 0 if p["source"] == "assist-v" else 1)
        for p in props:
            tok = _token_re(p["name"])
            used = [
                {"name": rn, "latex": display_latex(rt, p["name"])}
                for rn, rt in rows.items()
                if tok.search(rt)
            ][:6]
            items.append(
                {
                    "id": f"{key}::{p['name']}",
                    "paper_key": key,
                    "name": p["name"],
                    "evidence": f"proposed by {p['source']}",
                    "arities": {},
                    "rows": used,
                    "families": families,
                    "abstract": abstract,
                    "proposal": p,
                }
            )
    return items


RENDER_DIR = Path(__file__).resolve().parents[1] / "scripts" / "render"


def _node_path() -> str | None:
    """Where mathjax-full lives: the repo-local install first, else NODE_PATH."""
    local = RENDER_DIR / "node_modules"
    if (local / "mathjax-full").exists():
        return str(local)
    for entry in (os.environ.get("NODE_PATH") or "").split(os.pathsep):
        if entry and (Path(entry) / "mathjax-full").exists():
            return entry
    return None


def prerender_svg(texts: list[str]) -> list[dict]:
    """TeX -> self-contained SVG for every string, in one node process, at
    build time. The pages then render with no network and without relying
    on the viewer's MathML support (glyphs are paths). Without node or
    mathjax-full the result is empty dicts and the page shows each row's
    TeX in a code box instead.
    Install once: ``npm install --prefix scripts/render``.
    """
    if not texts:
        return []
    node = shutil.which("node")
    node_path = _node_path()
    if node is None or node_path is None:
        print(
            "vocabgame: node or mathjax-full not found (npm install --prefix scripts/render); "
            "rows will show their TeX unrendered",
            file=sys.stderr,
        )
        return [{} for _ in texts]
    env = {**os.environ, "NODE_PATH": node_path}
    proc = subprocess.run(
        [node, str(RENDER_DIR / "tex2svg.js")],
        input=json.dumps(texts, ensure_ascii=False),
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode != 0:
        print(f"vocabgame: tex2svg failed: {proc.stderr[:200]}", file=sys.stderr)
        return [{} for _ in texts]
    out = json.loads(proc.stdout)
    items = out.get("items", []) if isinstance(out, dict) else out
    if len(items) != len(texts):
        return [{} for _ in texts]
    rendered = [dict(o) for o in items]
    if isinstance(out, dict) and out.get("cache") and rendered:
        rendered[0]["font_cache"] = out["cache"]  # carried once, hoisted by attach_svg
    return rendered


def attach_svg(items: list[dict]) -> dict[str, int]:
    """Pre-render every row of every item in place; returns counts."""
    refs: list[tuple[int, int]] = []
    texts: list[str] = []
    for i, it in enumerate(items):
        for j, r in enumerate(it.get("rows", [])):
            refs.append((i, j))
            texts.append(r["latex"])
    rendered = prerender_svg(texts)
    counts = {"rows": len(texts), "svg": 0, "error": 0, "unrendered": 0}
    font_cache = rendered[0].pop("font_cache", "") if rendered else ""
    if font_cache and not font_cache.lstrip().startswith("<svg"):
        # a bare <defs> block must sit inside an <svg> element to be SVG at all
        font_cache = (
            '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
            'aria-hidden="true" style="position:absolute;width:0;height:0;overflow:hidden">'
            + font_cache
            + "</svg>"
        )
    counts["font_cache"] = len(font_cache)
    for (i, j), res in zip(refs, rendered, strict=True):
        row = items[i]["rows"][j]
        if res.get("svg"):
            row["svg"] = res["svg"]
            counts["svg"] += 1
        elif res.get("error"):
            row["error"] = res["error"]
            counts["error"] += 1
        else:
            counts["unrendered"] += 1
    attach_svg.font_cache = font_cache  # type: ignore[attr-defined]
    return counts


def build(mode: str, items: list[dict], out: Path) -> Path:
    """Render the page for ``mode`` (byte-identical for identical inputs)."""
    if mode not in ("blind", "confirm"):
        raise ValueError("mode must be 'blind' or 'confirm'")
    attach_svg(items)
    font_cache = getattr(attach_svg, "font_cache", "")
    data = {
        "font_cache": font_cache,
        "schema_version": SCHEMA,
        "mode": mode,
        "kinds": list(KINDS),
        "var_domains": list(VAR_DOMAINS),
        "param_kinds": list(PARAM_KINDS),
        "var_roles": list(VAR_ROLES),
        "items": items,
    }
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    html = TEMPLATE.replace("__DATA__", payload).replace("__MODE__", mode).replace("__LOGO__", LOGO)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8", newline="\n")
    return out


LOGO = """<svg viewBox="0 0 1014 321" xmlns="http://www.w3.org/2000/svg" role="img">'
    '<g transform="translate(-1485 -585)">'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="700" '
    'font-size="83" transform="matrix(1 0 0 1 1728.92 690)">TUD | Chair</text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1728.92 763)">o</text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1774.75 763)">f</text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1820.59 763)">Railway </text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1728.92 837)">Operations</text>'
    '<path d="M1600.51 630 1693 630 1693 722.49 1670.62 744.874 1670.62 674.888 1670.62 674.888 '
    "1670.62 651.685 1670.62 651.685 1647.41 651.685 1612.6 651.685 1589.4 674.876 1589.4 674.888 "
    "1647.41 674.888 1647.41 768.077 1577.49 838 1577.49 745.511 1485 745.511 1508.77 721.74 "
    "1601.01 721.74 1601.01 779.747 1601.02 779.747 1624.21 756.556 1624.21 721.74 1624.21 698.536 "
    '1624.21 698.536 1601.01 698.536 1601.01 698.536 1531.97 698.536Z" fill="#00008C" '
    'fill-rule="evenodd"/>'
    '<path d="M1485 628 1554 628 1530.99 651.345 1508.01 651.345 1508.01 674.655 1485 698Z" '
    'fill="#C00000" fill-rule="evenodd"/>'
    '<path d="M1485 768 1508.01 768 1508.01 813.988 1554 813.988 1554 837 1485 837Z" '
    'fill="#C00000" fill-rule="evenodd"/>'
    '<path d="M1694 768 1694 837 1624 837 1647.32 814.012 1670.65 814.012 1670.65 791.012Z" '
    'fill="#C00000" fill-rule="evenodd"/></g></svg>"""

TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f3f7f8" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#00103a" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🔤</text></svg>">
<title>Vocabulary round — __MODE__</title>
<style>
:root{
  --page1:#f3f7f8;--page2:#e7f1f1;--ink:#0c1f3a;--muted:#566782;
  --card:#ffffff;--card2:#f1f8f8;--line:#d6e6e7;--track:#e6f0f0;
  --accent:#0A777F;--accent2:#2F57B2;--good:#0A777F;--good-soft:#e6f4f4;
  --warn:#C85000;--warn-soft:#fbeada;--bad:#D20F41;--bad-soft:#fae3e9;--tier3:#7369BE;
  --shadow:0 1px 3px rgba(12,40,50,.08),0 6px 18px rgba(12,40,50,.05);
}
@media (prefers-color-scheme:dark){:root{
  --page1:#00103a;--page2:#001a55;--ink:#eaf1ff;--muted:#a0b4d8;
  --card:#0c2766;--card2:#10307c;--line:#2a4a92;--track:#001a55;
  --accent:#36b8bf;--accent2:#7aa2ff;--good:#36b8bf;--good-soft:#0d3350;
  --warn:#f0922e;--warn-soft:#3a2a17;--bad:#ff667e;--bad-soft:#43102a;--tier3:#a98bf0;
  --shadow:0 1px 3px rgba(0,0,0,.4);}
  .brandlogo svg text,.brandlogo svg path{fill:#fff !important}
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{background:linear-gradient(180deg,var(--page1),var(--page2));background-attachment:fixed;color:var(--ink);
  font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  padding:max(12px,env(safe-area-inset-top)) 14px calc(30px + env(safe-area-inset-bottom));max-width:640px;margin:0 auto}
.brandlogo{margin:2px 0 10px}.brandlogo svg{height:28px;width:auto;display:block}
.proto{background:var(--warn-soft);color:var(--warn);border:1.5px solid var(--warn);border-radius:10px;padding:5px 10px;
  font-size:12px;font-weight:650;margin:0 0 10px;text-align:center;line-height:1.45}
.eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:800}
h1{font-size:21px;margin:4px 0 2px;font-weight:800;letter-spacing:-.01em}
.sub{color:var(--muted);font-size:13px}
.bar{height:8px;border-radius:6px;background:var(--track);overflow:hidden;margin:10px 0 4px}
.bar>span{display:block;height:100%;width:0;background:var(--accent);transition:width .4s}
.counter{font-size:12.5px;color:var(--muted);display:flex;justify-content:space-between}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow);margin-top:12px}
.pk{font-size:12px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
.sym{font:800 30px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;letter-spacing:-.01em;margin:2px 0 6px;word-break:break-all}
.ev{display:inline-block;background:var(--card2);border:1px solid var(--line);border-radius:999px;padding:2px 10px;font-size:12px;color:var(--muted);margin:2px 4px 6px 0}
.rows{margin-top:6px}.row{border-top:1px solid var(--line);padding:6px 0}
.row .rn{font-size:11px;color:var(--muted);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.math{overflow-x:auto;font-size:16px;color:var(--ink);padding:4px 0}.math svg{display:block;max-width:none;overflow:visible}.math .err{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;color:var(--bad);white-space:pre-wrap}
mjx-container{margin:4px 0 !important}
details{margin-top:8px}summary{cursor:pointer;color:var(--accent);font-weight:700;font-size:13px}
.abs{font-size:13.5px;color:var(--ink);margin-top:6px;line-height:1.5}
h3{font-size:12px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:14px 0 6px;font-weight:700}
.btns{display:flex;flex-wrap:wrap;gap:8px}
button{font:inherit;border:1.5px solid var(--line);background:var(--card);color:var(--ink);border-radius:12px;padding:9px 13px;cursor:pointer;font-weight:700}
button.on{background:var(--accent);border-color:var(--accent);color:#fff}
button.k-not.on{background:var(--bad);border-color:var(--bad)}
button.k-skip.on{background:var(--muted);border-color:var(--muted)}
button.chip{border-radius:999px;padding:6px 12px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:13px;position:relative}
button.chip .ord{position:absolute;top:-7px;right:-6px;background:var(--accent2);color:#fff;font-size:10px;border-radius:999px;min-width:16px;height:16px;line-height:16px;text-align:center;padding:0 4px}
button.chip.on{background:var(--good-soft);color:var(--accent);border-color:var(--accent)}
.radio{display:flex;flex-wrap:wrap;gap:8px}
input[type=text]{width:100%;font:inherit;padding:9px 11px;border:1.5px solid var(--line);border-radius:12px;background:var(--card2);color:var(--ink)}
.nav{display:flex;gap:8px;margin-top:14px}
.nav button{flex:1;padding:12px}
.nav .go{background:var(--accent);border-color:var(--accent);color:#fff}
.nav .ok{background:var(--good);border-color:var(--good);color:#fff}
.prop{background:var(--card2);border:1px dashed var(--line);border-radius:10px;padding:8px 10px;font:12px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:var(--muted);word-break:break-all;margin-top:8px}
.tools{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.tools button{font-size:13px;padding:7px 11px}
#toast{position:fixed;left:50%;bottom:calc(16px + env(safe-area-inset-bottom));transform:translateX(-50%);background:#0c1f3a;color:#fff;
  padding:8px 14px;border-radius:999px;font-size:13px;opacity:0;transition:opacity .25s;pointer-events:none;max-width:90vw}
#toast.show{opacity:1}
@media (prefers-color-scheme:dark){#toast{background:#eaf1ff;color:#00103a}}
.done{text-align:center;padding:24px 8px}.done .big{font-size:40px}
.kbd{font-size:11.5px;color:var(--muted);margin-top:10px}
.kbd kbd{background:var(--card2);border:1px solid var(--line);border-radius:5px;padding:0 5px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.hidden{display:none !important}
</style>
</head>
<body>
<div class="proto">⚠️ working prototype — your labels are the ground truth; export them, this page stores them only in this browser</div>
<header>
  <div class="brandlogo" aria-label="TUD — Chair of Railway Operations">__LOGO__</div>
  <div class="eyebrow">raiLPminer · vocabulary round · <span id="modeLabel">__MODE__</span></div>
  <h1 id="title">Vocabulary round</h1>
  <div class="sub" id="subtitle"></div>
  <div class="bar"><span id="progress"></span></div>
  <div class="counter"><span id="counter"></span><span id="doneCount"></span></div>
</header>

<section id="work" class="card">
  <div class="pk" id="paperKey"></div>
  <div class="sym" id="symbol"></div>
  <div id="evidence"></div>
  <div id="proposalBox" class="prop hidden"></div>
  <div class="rows" id="rows"></div>
  <div id="fontCache" hidden></div>
  <details id="mentBox" open><summary>in the paper</summary><div class="abs" id="mentions"></div></details>
  <details id="absBox"><summary>abstract</summary><div class="abs" id="abstract"></div></details>

  <h3>kind</h3>
  <div class="btns" id="kindBtns">
    <button type="button" data-k="index" class="k-index">index <small>(i)</small></button>
    <button type="button" data-k="param" class="k-param">param <small>(p)</small></button>
    <button type="button" data-k="var" class="k-var">var <small>(v)</small></button>
    <button type="button" data-k="not_a_symbol" class="k-not">not a symbol <small>(n)</small></button>
    <button type="button" data-k="skip" class="k-skip">skip <small>(s)</small></button>
  </div>

  <div id="shapeBlock">
    <h3>shape · tap families in order (digits)</h3>
    <div class="btns" id="familyChips"></div>
    <div class="sub" id="shapeText"></div>
  </div>

  <div id="domainBlock"><h3>domain</h3><div class="radio" id="domainBtns"></div></div>
  <div id="pkindBlock"><h3>parameter kind</h3><div class="radio" id="pkindBtns"></div></div>
  <div id="roleBlock"><h3>role</h3><div class="radio" id="roleBtns"></div></div>

  <h3>description <span style="font-weight:400;text-transform:none;letter-spacing:0">(optional)</span></h3>
  <input type="text" id="desc" placeholder="one line: what the symbol means" autocomplete="off">

  <div class="nav">
    <button type="button" id="backBtn">◀ back</button>
    <button type="button" id="confirmBtn" class="ok hidden">✓ confirm as proposed</button>
    <button type="button" id="nextBtn" class="go">save &amp; next ▶</button>
  </div>
  <div class="kbd">keys: <kbd>i</kbd> <kbd>p</kbd> <kbd>v</kbd> <kbd>n</kbd> <kbd>s</kbd> kind · <kbd>1</kbd>–<kbd>9</kbd> families · <kbd>Enter</kbd> next · <kbd>Backspace</kbd> back · <kbd>c</kbd> confirm</div>
</section>

<section id="doneScreen" class="card done hidden">
  <div class="big">🏁</div>
  <div style="font-weight:800;font-size:18px">round complete</div>
  <div class="sub" id="doneSummary"></div>
  <div class="nav"><button type="button" id="exportBtn2" class="go">⬇ export decisions</button><button type="button" id="reviewBtn">review again</button></div>
</section>

<div class="tools">
  <button type="button" id="exportBtn">⬇ export JSON</button>
  <button type="button" id="importBtn">⬆ import JSON</button>
  <input type="file" id="importFile" accept="application/json" class="hidden">
  <button type="button" id="undoBtn">↶ undo last</button>
  <button type="button" id="jumpBtn">next undecided</button>
</div>
<div id="toast"></div>

<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
"use strict";
const DATA = JSON.parse(document.getElementById("data").textContent);
const MODE = DATA.mode;
const ITEMS = DATA.items;
const LSK = "vocab:state:v1:" + MODE;
const $ = id => document.getElementById(id);
let S = load();
let form = null;   // the working form of the current item
let undoStack = [];

function load(){
  try{ const s = JSON.parse(localStorage.getItem(LSK) || "null"); if (s && s.decisions) return s; }catch(e){}
  return {mode: MODE, i: 0, decisions: {}, history: []};
}
function save(){ try{ localStorage.setItem(LSK, JSON.stringify(S)); }catch(e){ toast("⚠ could not save — export!"); } }
function toast(t){ const el=$("toast"); el.textContent=t; el.classList.add("show"); clearTimeout(toast._t); toast._t=setTimeout(()=>el.classList.remove("show"),1600); }

/* ---------- formulas: SVG pre-rendered at build time (no network, no MathML needed) ---------- */
(function(){ const fc = document.getElementById("fontCache"); if (fc && DATA.font_cache){ fc.innerHTML = DATA.font_cache; fc.hidden = false; fc.style.cssText = "position:absolute;width:0;height:0;overflow:hidden"; } })();
function renderMath(el, row){
  el.innerHTML = "";
  if (row.svg){ el.innerHTML = row.svg; return; }
  const d = document.createElement("div"); d.className = "err"; d.textContent = row.latex; el.appendChild(d);
  if (row.error){ const e = document.createElement("div"); e.className = "sub"; e.textContent = "not rendered: " + row.error; el.appendChild(e); }
}

/* ---------- form ---------- */
function emptyForm(item){
  const p = item.proposal;
  if (MODE === "confirm" && p){
    return {kind: p.kind, shape: p.shape.slice(), domain: p.kind==="var" ? (p.domain==="-" ? null : p.domain) : null,
            pkind: p.kind==="param" ? (p.pkind==="-" ? null : p.pkind) : null, role: p.kind==="var" ? (p.role==="-" ? "primary" : p.role) : null, desc: p.desc || ""};
  }
  return {kind: null, shape: [], domain: null, pkind: null, role: "primary", desc: ""};
}
function formFromDecision(d){
  return {kind: d.verdict === "declare" ? d.kind : d.verdict, shape: (d.shape||[]).slice(), domain: d.domain, pkind: d.pkind, role: d.role || "primary", desc: d.desc || ""};
}
function decisionFromForm(item, f){
  const isDecl = f.kind === "index" || f.kind === "param" || f.kind === "var";
  const verdict = isDecl ? "declare" : (f.kind === "not_a_symbol" ? "not_a_symbol" : "skip");
  const d = {
    id: item.id, paper_key: item.paper_key, name: item.name, verdict: verdict,
    kind: isDecl ? f.kind : null,
    shape: isDecl && f.kind !== "index" ? f.shape.slice() : [],
    domain: f.kind === "var" ? f.domain : null,
    pkind: f.kind === "param" ? f.pkind : null,
    role: f.kind === "var" ? (f.role || "primary") : null,
    desc: f.desc || "",
    proposal: MODE === "confirm" ? (item.proposal || null) : null,
    changed: false
  };
  if (MODE === "confirm" && item.proposal){
    const p = item.proposal;
    const same = d.verdict === "declare" && d.kind === p.kind && JSON.stringify(d.shape) === JSON.stringify(p.shape) &&
      (p.kind !== "var" || (d.domain || null) === (p.domain === "-" ? null : p.domain)) &&
      (p.kind !== "param" || (d.pkind || null) === (p.pkind === "-" ? null : p.pkind));
    d.changed = !same;
  }
  return d;
}

/* ---------- rendering ---------- */
function paintProgress(){
  const n = ITEMS.length, done = Object.keys(S.decisions).length;
  $("progress").style.width = (n ? 100*done/n : 0) + "%";
  $("counter").textContent = "item " + Math.min(S.i+1, n) + " / " + n;
  $("doneCount").textContent = done + " decided";
}
function paintItem(){
  const item = ITEMS[S.i];
  if (!item){ showDone(); return; }
  $("work").classList.remove("hidden"); $("doneScreen").classList.add("hidden");
  form = S.decisions[item.id] ? formFromDecision(S.decisions[item.id]) : emptyForm(item);
  $("paperKey").textContent = item.paper_key;
  $("symbol").textContent = item.name;
  const ev = [];
  if (item.evidence) ev.push(item.evidence);
  for (const [a, n] of Object.entries(item.arities || {})) ev.push("written with " + a + (a==="1"?" index":" indices") + " · " + n + "×");
  ev.push(item.rows.length + " row" + (item.rows.length===1?"":"s"));
  $("evidence").innerHTML = ev.map(t => '<span class="ev">' + esc(t) + '</span>').join("");
  const pb = $("proposalBox");
  if (MODE === "confirm" && item.proposal){ pb.textContent = item.proposal.line; pb.classList.remove("hidden"); } else { pb.classList.add("hidden"); pb.textContent=""; }
  const rows = $("rows"); rows.innerHTML = "";
  for (const r of item.rows){
    const div = document.createElement("div"); div.className = "row";
    const rn = document.createElement("div"); rn.className = "rn"; rn.textContent = r.name; div.appendChild(rn);
    const m = document.createElement("div"); m.className = "math"; div.appendChild(m); rows.appendChild(div);
    renderMath(m, r);
  }
  const mb = $("mentions"); mb.innerHTML = "";
  for (const d of (item.definitions || [])){ const p = document.createElement("p"); p.innerHTML = "<b>" + esc(d.term) + "</b> — " + esc(d.def); mb.appendChild(p); }
  for (const m of (item.mentions || [])){ const p = document.createElement("p"); p.innerHTML = (m.labels && m.labels.length ? '<span class="ev">' + esc(m.labels.join(" ")) + "</span> " : "") + esc(m.text); mb.appendChild(p); }
  $("mentBox").classList.toggle("hidden", !mb.childElementCount);
  if (item.abstract){ $("abstract").textContent = item.abstract; $("absBox").classList.remove("hidden"); } else { $("absBox").classList.add("hidden"); }
  $("familyChips").innerHTML = item.families.map((f, k) => '<button type="button" class="chip" data-f="' + esc(f) + '" data-n="' + (k+1) + '">' + esc(f) + '<span class="ord hidden"></span></button>').join("") || '<span class="sub">no index family declared for this paper</span>';
  $("domainBtns").innerHTML = DATA.var_domains.map(d => '<button type="button" data-d="' + d + '">' + d + '</button>').join("");
  $("pkindBtns").innerHTML = DATA.param_kinds.map(d => '<button type="button" data-p="' + d + '">' + d + '</button>').join("");
  $("roleBtns").innerHTML = DATA.var_roles.map(d => '<button type="button" data-r="' + d + '">' + d + '</button>').join("");
  $("desc").value = form.desc || "";
  $("confirmBtn").classList.toggle("hidden", !(MODE === "confirm" && item.proposal));
  paintForm(); paintProgress();
  window.scrollTo(0, 0);
}
function paintForm(){
  document.querySelectorAll("#kindBtns button").forEach(b => b.classList.toggle("on", b.dataset.k === form.kind));
  const decl = form.kind === "index" || form.kind === "param" || form.kind === "var";
  $("shapeBlock").classList.toggle("hidden", !(decl && form.kind !== "index"));
  $("domainBlock").classList.toggle("hidden", form.kind !== "var");
  $("roleBlock").classList.toggle("hidden", form.kind !== "var");
  $("pkindBlock").classList.toggle("hidden", form.kind !== "param");
  document.querySelectorAll("#familyChips .chip").forEach(b => {
    const pos = form.shape.indexOf(b.dataset.f);
    b.classList.toggle("on", pos >= 0);
    const o = b.querySelector(".ord"); if (o){ o.textContent = pos >= 0 ? String(pos+1) : ""; o.classList.toggle("hidden", pos < 0); }
  });
  $("shapeText").textContent = form.shape.length ? "shape=" + form.shape.join(",") : "shape=- (no index)";
  document.querySelectorAll("#domainBtns button").forEach(b => b.classList.toggle("on", b.dataset.d === form.domain));
  document.querySelectorAll("#pkindBtns button").forEach(b => b.classList.toggle("on", b.dataset.p === form.pkind));
  document.querySelectorAll("#roleBtns button").forEach(b => b.classList.toggle("on", b.dataset.r === form.role));
}
function esc(s){ return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); }
function showDone(){
  $("work").classList.add("hidden"); $("doneScreen").classList.remove("hidden");
  const ds = Object.values(S.decisions);
  const decl = ds.filter(d => d.verdict === "declare").length, nots = ds.filter(d => d.verdict === "not_a_symbol").length, sk = ds.filter(d => d.verdict === "skip").length;
  const ch = ds.filter(d => d.changed).length;
  $("doneSummary").textContent = decl + " declared · " + nots + " not a symbol · " + sk + " skipped" + (MODE === "confirm" ? " · " + ch + " changed from the proposal" : "");
  paintProgress();
}

/* ---------- actions ---------- */
function setKind(k){ form.kind = k; if (k === "index") form.shape = []; paintForm(); }
function toggleFamily(f){ const i = form.shape.indexOf(f); if (i >= 0) form.shape.splice(i, 1); else form.shape.push(f); paintForm(); }
function commit(){
  const item = ITEMS[S.i]; if (!item) return false;
  if (!form.kind){ toast("pick a kind first"); return false; }
  if (form.kind === "var" && !form.domain){ toast("pick a domain"); return false; }
  if (form.kind === "param" && !form.pkind){ toast("pick a parameter kind"); return false; }
  form.desc = $("desc").value.trim();
  const d = decisionFromForm(item, form);
  undoStack.push({i: S.i, prev: S.decisions[item.id] ? JSON.parse(JSON.stringify(S.decisions[item.id])) : null});
  S.decisions[item.id] = d; S.history.push({id: item.id, verdict: d.verdict, at: new Date().toISOString()});
  save(); return true;
}
function next(){ if (!commit()) return; S.i = nextUndecided(S.i + 1); save(); paintItem(); }
function nextUndecided(from){
  for (let k = from; k < ITEMS.length; k++) if (!S.decisions[ITEMS[k].id]) return k;
  for (let k = 0; k < from; k++) if (!S.decisions[ITEMS[k].id]) return k;
  return ITEMS.length;  // all decided
}
function back(){ if (S.i > 0){ S.i = Math.min(S.i - 1, ITEMS.length - 1); save(); paintItem(); } }
function confirmAsProposed(){ const item = ITEMS[S.i]; if (!item || !item.proposal) return; form = emptyForm(item); $("desc").value = form.desc; next(); }
function undo(){
  const u = undoStack.pop(); if (!u){ toast("nothing to undo"); return; }
  const item = ITEMS[u.i]; if (u.prev) S.decisions[item.id] = u.prev; else delete S.decisions[item.id];
  S.i = u.i; save(); paintItem(); toast("undone");
}
function exportJSON(){
  const decisions = ITEMS.filter(it => S.decisions[it.id]).map(it => S.decisions[it.id]);
  const payload = {schema_version: "vocab-decisions-1", mode: MODE, exported: new Date().toISOString(), labeller: "human", decisions: decisions};
  const blob = new Blob([JSON.stringify(payload, null, 1)], {type: "application/json"});
  const a = document.createElement("a"); a.href = URL.createObjectURL(blob);
  a.download = "vocab_decisions_" + MODE + "_" + new Date().toISOString().slice(0,10) + ".json";
  document.body.appendChild(a); a.click(); a.remove();
  toast("exported " + decisions.length + " decisions");
}
function importJSON(text){
  let obj; try{ obj = JSON.parse(text); }catch(e){ toast("not JSON"); return 0; }
  if (!obj || obj.schema_version !== "vocab-decisions-1" || !Array.isArray(obj.decisions)){ toast("not a vocab-decisions-1 file"); return 0; }
  let n = 0; const known = new Set(ITEMS.map(it => it.id));
  for (const d of obj.decisions){ if (d && d.id && known.has(d.id)){ S.decisions[d.id] = d; n++; } }
  save(); S.i = nextUndecided(0); paintItem(); toast("imported " + n + " (last wins)"); return n;
}

/* ---------- wiring ---------- */
$("kindBtns").addEventListener("click", e => { const b = e.target.closest("button"); if (b) setKind(b.dataset.k); });
$("familyChips").addEventListener("click", e => { const b = e.target.closest("button.chip"); if (b) toggleFamily(b.dataset.f); });
$("domainBtns").addEventListener("click", e => { const b = e.target.closest("button"); if (b){ form.domain = b.dataset.d; paintForm(); } });
$("pkindBtns").addEventListener("click", e => { const b = e.target.closest("button"); if (b){ form.pkind = b.dataset.p; paintForm(); } });
$("roleBtns").addEventListener("click", e => { const b = e.target.closest("button"); if (b){ form.role = b.dataset.r; paintForm(); } });
$("nextBtn").addEventListener("click", next);
$("backBtn").addEventListener("click", back);
$("confirmBtn").addEventListener("click", confirmAsProposed);
$("undoBtn").addEventListener("click", undo);
$("jumpBtn").addEventListener("click", () => { S.i = nextUndecided(S.i + 1); save(); paintItem(); });
$("exportBtn").addEventListener("click", exportJSON);
$("exportBtn2").addEventListener("click", exportJSON);
$("reviewBtn").addEventListener("click", () => { S.i = 0; save(); paintItem(); });
$("importBtn").addEventListener("click", () => $("importFile").click());
$("importFile").addEventListener("change", e => { const f = e.target.files[0]; if (!f) return; const r = new FileReader(); r.onload = () => importJSON(String(r.result)); r.readAsText(f); e.target.value = ""; });
document.addEventListener("keydown", e => {
  if (e.target && e.target.tagName === "INPUT"){ if (e.key === "Enter"){ e.preventDefault(); next(); } return; }
  const item = ITEMS[S.i]; if (!item) return;
  const k = e.key;
  if (k === "i") setKind("index"); else if (k === "p") setKind("param"); else if (k === "v") setKind("var");
  else if (k === "n") setKind("not_a_symbol"); else if (k === "s") setKind("skip");
  else if (k === "c" && MODE === "confirm") confirmAsProposed();
  else if (k === "Enter"){ e.preventDefault(); next(); }
  else if (k === "Backspace"){ e.preventDefault(); back(); }
  else if (/^[1-9]$/.test(k)){ const f = item.families[parseInt(k,10)-1]; if (f) toggleFamily(f); }
});
$("title").textContent = MODE === "blind" ? "Blind labels (gold set)" : "Confirm the proposals";
$("subtitle").textContent = MODE === "blind"
  ? "What is this symbol in this paper? Decide kind, shape and domain from the rows, the paper's own words around them and the abstract. Nothing here was proposed by a model."
  : "A model proposed each line. Confirm it as is, or fix kind, shape or domain; every edit is recorded.";
window.__vocab = {S: () => S, form: () => form, commit, next, back, exportPayload: () => ({schema_version: "vocab-decisions-1", mode: MODE, labeller: "human", decisions: ITEMS.filter(it => S.decisions[it.id]).map(it => S.decisions[it.id])}), importJSON, setKind, toggleFamily, confirmAsProposed};
S.i = Math.min(S.i, ITEMS.length);
if (S.i >= ITEMS.length && ITEMS.length) S.i = nextUndecided(0);
paintItem();
})();
</script>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusbuilder.vocabgame",
        description="Build the vocabulary-round page (blind gold labels or proposal confirmation).",
    )
    parser.add_argument("--mode", choices=("blind", "confirm"), required=True)
    parser.add_argument("--gold", type=Path, default=GOLD_PATH, help="gold sample (blind mode)")
    parser.add_argument("--keys", nargs="*", default=None, help="confirm mode: only these papers")
    parser.add_argument("--limit", type=int, default=None, help="confirm mode: at most N papers")
    parser.add_argument("--out", type=Path, default=None, help="HTML path")
    args = parser.parse_args(argv)
    if args.mode == "blind":
        gold = json.loads(args.gold.read_text(encoding="utf-8"))
        items = blind_items(gold)
    else:
        items = confirm_items(keys=args.keys, limit=args.limit)
    out = args.out or (REVIEW_DIR / f"vocab_{args.mode}.html")
    build(args.mode, items, out)
    papers = len({it["paper_key"] for it in items})
    print(f"wrote {_rel(out)} ({args.mode}: {len(items)} items over {papers} papers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
