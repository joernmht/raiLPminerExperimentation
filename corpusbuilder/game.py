"""**Formula Express** — a mobile-first, gamified HITL review game for the corpus.

Generates ONE self-contained HTML file, ``corpus/review/game.html`` (gitignored,
like the rest of ``corpus/review/``), embedding every dossier's formulas, in the
**TU Dresden Chair of Railway Operations CD** (source of truth: the ``tud-mobile``
skill / ``tud_cro_chaircd`` — türkis accent on a light field, Dunkelblau dark
mode, inline chair logo). Open it on a phone (send via
``~/.claude/loops/notify.sh``) or desktop. Minigames, all of which produce
*real* pipeline decisions:

* **Paper Run** (primary) — review one *paper* at a time: a deterministic
  **symbol graph** (formulas ↔ shared symbols; coherent models show a connected
  hub structure, extraction noise falls apart) plus the formula list with
  per-row accept ✓ / fix ✎ / reject ✗ and bulk actions ("accept rest",
  "reject all"). Finishing a paper asks for its P1–P5 cell. Each formula row
  expands to raw LaTeX and a **formula mini-graph** (relation + operators +
  symbols). Decision semantics identical to ``review_view`` (corrected LaTeX
  is stored in ``note``).
* **Blitz** — 60-second accept/reject sprint over the unreviewed pool.
* **Shell Sorter** — classify papers into P1–P5 / out-of-scope (fills PRISMA
  ``per_cell_P1_P5``; prunes the medical-noise dossiers).

The symbol graphs are *review aids* derived by a deterministic regex-level
tokenizer (this module), NOT the canonical lp2graph representation — that
requires the extraction step (``lp2graph.mining.ingest``) that HITL review
feeds. Gamification: XP + railway ranks, daily streaks with a heatmap
(türkis ramps validated light+dark), badges, a Journal. Progress persists in
``localStorage`` (same key as v1 — existing progress survives); **Export**
produces ``game_decisions_<date>.json`` (``formula_decisions`` = the
``review_view`` per-paper format, plus ``paper_cells``); Import merges.

Run:  PYTHONPATH=. python3 -m corpusbuilder.game
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from corpusbuilder.dossier import Dossier
from corpusbuilder.prisma import _RELEVANT

ROOT = Path(__file__).resolve().parent.parent
DOSS = ROOT / "corpus" / "dossiers"
OUT = ROOT / "corpus" / "review" / "game.html"
LOGO = Path.home() / ".claude" / "skills" / "tud-mobile" / "assets" / "logos" / "Chairlogo_new_engl.svg"

_METHOD = {"arxiv-tex": "T1", "mathml": "T2", "ocr": "T3", "llm": "LLM", "human": "H"}

# --------------------------------------------------------------------------
# Deterministic LaTeX symbol extraction (review aid — NOT canonical lp2graph)
# --------------------------------------------------------------------------

_GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ε",
    "varepsilon": "ε", "zeta": "ζ", "eta": "η", "theta": "θ", "vartheta": "θ",
    "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν", "xi": "ξ",
    "pi": "π", "rho": "ρ", "varrho": "ρ", "sigma": "σ", "tau": "τ",
    "upsilon": "υ", "phi": "φ", "varphi": "φ", "chi": "χ", "psi": "ψ",
    "omega": "ω", "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ",
    "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ", "Phi": "Φ",
    "Psi": "Ψ", "Omega": "Ω",
}
_OPS = {
    "sum": "∑", "prod": "∏", "int": "∫", "min": "min", "max": "max",
    "frac": "÷", "sqrt": "√", "cup": "∪", "cap": "∩", "partial": "∂",
    "nabla": "∇", "log": "log", "exp": "exp", "cdot": "·", "times": "×",
}
_RELS = [
    ("leq", "≤"), ("le", "≤"), ("geq", "≥"), ("ge", "≥"), ("neq", "≠"),
    ("ne", "≠"), ("subseteq", "⊆"), ("in", "∈"), ("forall", "∀"),
]
_DECOR = {"hat": "̂", "bar": "̄", "tilde": "̃", "dot": "̇",
          "vec": "⃗", "overline": "̄", "widehat": "̂"}
_NOISE_CMDS = {
    "left", "right", "big", "bigl", "bigr", "Big", "Bigl", "Bigr", "bigg",
    "Bigg", "quad", "qquad", "limits", "nolimits", "displaystyle",
    "textstyle", "nonumber", "label", "tag", "mathstrut", "hspace", "vspace",
    ",", ";", "!", ":", " ", "\\", "allowbreak", "prime", "ldots", "cdots",
    "dots", "dotsb", "colon", "%",
}
_WRAP_CMDS = {"mathrm", "mathit", "mathbf", "mathsf", "mathcal", "mathbb",
              "boldsymbol", "bm", "text", "textrm", "textit", "operatorname",
              "mbox", "mathord", "mathop", "textnormal"}
_STOPWORDS = {
    "if", "for", "all", "and", "or", "st", "otherwise", "where", "then",
    "else", "is", "to", "the", "of", "a", "an", "with", "subject", "such",
    "that", "minimize", "maximize", "min", "max", "s", "t", "et", "al",
}
# runs of >=3 single letters separated by single spaces (MathML word noise)
_SPACED_WORD = re.compile(r"(?<![A-Za-z\\])([A-Za-z](?: [A-Za-z]){2,})(?![A-Za-z])")


def _collapse_words(s: str) -> str:
    return _SPACED_WORD.sub(lambda m: m.group(1).replace(" ", ""), s)


def _group_end(s: str, i: int) -> int:
    """i points at '{'; return index just past the matching '}'."""
    depth = 0
    while i < len(s):
        if s[i] == "{":
            depth += 1
        elif s[i] == "}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return len(s)


def extract_symbols(latex: str) -> tuple[list[list], list[list], str]:
    """Deterministically extract (symbols, operators, relation) from LaTeX.

    Returns ``(syms, ops, rel)`` where ``syms``/``ops`` are
    ``[[display_name, count], ...]`` sorted by count desc then name, and
    ``rel`` is the first top-level relation's display symbol (or ``""``).
    Sub-/superscript contents are treated as indices and skipped, so
    ``x_{ij}`` and ``x_{ik}`` are the same symbol ``x``.
    """
    s = _collapse_words(latex)
    syms: dict[str, int] = {}
    ops: dict[str, int] = {}
    rel = ""
    i, n = 0, len(s)
    pending_decor = ""

    def add_sym(name: str) -> None:
        nonlocal pending_decor
        if pending_decor:
            name += pending_decor
            pending_decor = ""
        syms[name] = syms.get(name, 0) + 1

    while i < n:
        c = s[i]
        if c == "\\":
            m = re.match(r"\\([a-zA-Z]+|.)", s[i:])
            cmd = m.group(1) if m else ""
            i += len(m.group(0)) if m else 1
            if cmd in _GREEK:
                add_sym(_GREEK[cmd])
            elif cmd in _OPS:
                ops[_OPS[cmd]] = ops.get(_OPS[cmd], 0) + 1
            elif cmd in _DECOR:
                pending_decor = _DECOR[cmd]
            elif cmd in _WRAP_CMDS:
                # transparent wrapper: skip an immediately following '{'
                while i < n and s[i] == " ":
                    i += 1
                if i < n and s[i] == "{":
                    i += 1  # descend into the group; matching '}' is ignored
            elif not rel:
                for r, disp in _RELS:
                    if cmd == r:
                        rel = disp
                        break
            # every other command (incl. _NOISE_CMDS) is dropped
        elif c in "_^":
            # index/exponent: skip a group or a single token
            i += 1
            while i < n and s[i] == " ":
                i += 1
            if i < n and s[i] == "{":
                i = _group_end(s, i)
            elif i < n and s[i] == "\\":
                m = re.match(r"\\[a-zA-Z]+|\\.", s[i:])
                i += len(m.group(0)) if m else 1
            else:
                i += 1
        elif c.isalpha():
            j = i
            while j < n and s[j].isalpha():
                j += 1
            word = s[i:j]
            i = j
            if len(word) == 1:
                add_sym(word)
            elif word.lower() not in _STOPWORDS:
                add_sym(word)
        elif not rel and c in "=<>":
            rel = {"=": "=", "<": "<", ">": ">"}[c]
            i += 1
        else:
            i += 1

    top_syms = sorted(syms.items(), key=lambda kv: (-kv[1], kv[0]))[:12]
    top_ops = sorted(ops.items(), key=lambda kv: (-kv[1], kv[0]))[:6]
    return ([[k, v] for k, v in top_syms], [[k, v] for k, v in top_ops], rel)


# --------------------------------------------------------------------------
# Payload
# --------------------------------------------------------------------------


def _logo_svg() -> str:
    """Inline chair logo with viewBox (per tud-mobile skill), or empty."""
    try:
        svg = LOGO.read_text(encoding="utf-8")
    except OSError:
        return ""
    svg = re.sub(r'\s(?:width|height)="[^"]*"', "", svg, count=2)
    if "viewBox" not in svg:
        svg = svg.replace("<svg ", '<svg viewBox="0 0 1014 321" ', 1)
    return svg


def _payload() -> dict:
    doss = [Dossier.load(p) for p in sorted(DOSS.glob("*.json"))]
    doss.sort(key=lambda d: (-(d.source.cited_by_count or 0), d.key))
    papers = []
    for d in doss:
        s = d.source
        fs = []
        for f in d.formulas:
            syms, ops, rel = extract_symbols(f.latex)
            fs.append([f.id, f.label or "", f.latex,
                       _METHOD.get(f.method.value, "?"), f.page_start or 0,
                       syms, ops, rel])
        papers.append({
            "k": d.key, "t": s.title or d.key, "v": s.venue or "",
            "y": s.year or 0, "d": s.doi or "", "c": s.cited_by_count or 0,
            "ot": 0 if (s.title and _RELEVANT.search(s.title)) else 1,
            "f": fs,
        })
    return {"papers": papers, "n_formulas": sum(len(p["f"]) for p in papers)}


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f3f7f8" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#00103a" media="(prefers-color-scheme: dark)">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🚂</text></svg>">
<title>Formula Express — CRO corpus review</title>
<script>window.MathJax={tex:{displayMath:[["\\[","\\]"]]},options:{enableMenu:false},startup:{typeset:false}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
        onerror="window.__mjfail=1"></script>
<style>
/* =========================================================================
   TUD Chair of Railway Operations — mobile design system (tud-mobile skill)
   Light: turquoise (Türkis #0A777F) accent on a light field.
   Dark : TUD dark-blue (Dunkelblau) field, lighter türkis accent, white logo.
   ========================================================================= */
:root{
  --page1:#f3f7f8;--page2:#e7f1f1;
  --ink:#0c1f3a;--muted:#566782;
  --card:#ffffff;--card2:#f1f8f8;--line:#d6e6e7;--track:#e6f0f0;
  --accent:#0A777F;--accent2:#2F57B2;
  --good:#0A777F;--good-soft:#e6f4f4;
  --warn:#C85000;--warn-soft:#fbeada;
  --bad:#D20F41;--bad-soft:#fae3e9;
  --g1:#5fb6bc;--g2:#2f979e;--g3:#0a777f;--g4:#06555b;
  --shadow:0 1px 3px rgba(12,40,50,.08),0 6px 18px rgba(12,40,50,.05);
}
@media (prefers-color-scheme:dark){:root{
  --page1:#00103a;--page2:#001a55;
  --ink:#eaf1ff;--muted:#a0b4d8;
  --card:#0c2766;--card2:#10307c;--line:#2a4a92;--track:#001a55;
  --accent:#36b8bf;--accent2:#7aa2ff;
  --good:#36b8bf;--good-soft:#0d3350;
  --warn:#f0922e;--warn-soft:#3a2a17;
  --bad:#ff667e;--bad-soft:#43102a;
  --g1:#1f6b74;--g2:#2b9199;--g3:#36b8bf;--g4:#7fd7db;
  --shadow:0 1px 3px rgba(0,0,0,.4);
}
.brandlogo svg text,.brandlogo svg path{fill:#fff !important}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;padding:0}
body{background:linear-gradient(180deg,var(--page1),var(--page2));background-attachment:fixed;
  color:var(--ink);font:16px/1.45 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;overscroll-behavior-y:contain}
.app{max-width:480px;margin:0 auto;padding:max(12px,env(safe-area-inset-top)) 14px calc(28px + env(safe-area-inset-bottom))}
.brandlogo{margin:2px 0 10px}.brandlogo svg{height:26px;width:auto;display:block}
.eyebrow{font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:800}
h1{font-size:23px;line-height:1.2;margin:4px 0 2px;font-weight:800;letter-spacing:-.01em}
h2{font-size:17px;margin:18px 0 8px;font-weight:750}
.sub{color:var(--muted);font-size:13px;margin:0 0 14px}
section{display:none}section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:14px;box-shadow:var(--shadow)}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:12px 14px;box-shadow:var(--shadow)}
.tile .big{font-size:26px;font-weight:800;line-height:1.15}
.tile .lbl{color:var(--muted);font-size:12.5px;margin-top:2px}
.tile .note{color:var(--muted);opacity:.8;font-size:11.5px;margin-top:2px}
.bar{height:10px;background:var(--track);border-radius:6px;overflow:hidden;display:flex;margin-top:8px}
.bar i{display:block;height:100%}
.bar .sa{background:var(--good)}.bar .sc{background:var(--warn)}.bar .sr{background:var(--bad)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;font-size:12.5px;color:var(--muted)}
.chip{background:var(--card2);border:1px solid var(--line);border-radius:10px;padding:2px 9px}
.chip b{font-variant-numeric:tabular-nums}
.games{display:flex;flex-direction:column;gap:10px;margin-top:10px}
.game{display:flex;align-items:center;gap:12px;background:var(--card);
  border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow);
  cursor:pointer;user-select:none}
.game:active{transform:scale(.985)}
.game .ico{font-size:28px}
.game .ttl{font-weight:700}
.game .dsc{color:var(--muted);font-size:13px}
.game .go{margin-left:auto;color:var(--accent);font-weight:800}
button{font:inherit;cursor:pointer;border:1px solid var(--line);background:var(--card);
  color:var(--ink);border-radius:14px;padding:10px 14px;user-select:none}
button:active{transform:scale(.97)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.top{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.top .back{border:none;background:none;font-size:15px;color:var(--accent);
  font-weight:700;padding:8px 8px 8px 0}
.top .info{margin-left:auto;color:var(--muted);font-size:13px;font-variant-numeric:tabular-nums}
.combo{margin-left:8px;color:var(--accent);font-weight:800;font-size:14px}
/* paper run */
.ptitle{font-size:16px;font-weight:750;line-height:1.3}
.pmeta{color:var(--muted);font-size:12.5px;margin-top:3px}
.hint-ot{display:inline-block;background:var(--bad-soft);color:var(--bad);border-radius:10px;
  padding:2px 9px;font-size:12.5px;font-weight:650;margin-top:6px}
.cellchip{display:inline-block;background:var(--good-soft);color:var(--good);border-radius:10px;
  padding:2px 9px;font-size:12.5px;font-weight:700;margin-top:6px}
.gwrap{margin-top:10px}
.gwrap svg{width:100%;height:auto;display:block;background:var(--card2);
  border:1px solid var(--line);border-radius:12px;touch-action:manipulation}
.glegend{color:var(--muted);font-size:11.5px;margin-top:6px}
.gnode-f{fill:var(--card);stroke:var(--muted);stroke-width:1.4}
.gnode-f.sa{fill:var(--good);stroke:var(--good)}
.gnode-f.sc{fill:var(--warn);stroke:var(--warn)}
.gnode-f.sr{fill:var(--bad);stroke:var(--bad)}
.gnode-f.hl{stroke:var(--accent2);stroke-width:3}
.gnode-s{fill:var(--accent);opacity:.92}
.gnode-s.dim{opacity:.25}
.gedge{stroke:var(--line);stroke-width:1}
.gedge.hl{stroke:var(--accent2);stroke-width:1.8}
.glabel{font-size:10px;fill:var(--ink);font-weight:600}
.bulk{position:sticky;top:0;z-index:10;background:var(--card);border:1px solid var(--line);
  border-radius:14px;box-shadow:var(--shadow);padding:8px;display:flex;gap:8px;margin:10px 0}
.bulk button{flex:1;font-size:13.5px;font-weight:700;min-height:44px;padding:6px 8px}
.b-rej{background:var(--bad-soft);border-color:var(--bad);color:var(--bad)}
.b-fix{background:var(--warn-soft);border-color:var(--warn);color:var(--warn)}
.b-acc{background:var(--good-soft);border-color:var(--good);color:var(--good)}
.b-fin{background:var(--accent);border-color:var(--accent);color:#fff}
.frow{background:var(--card);border:1px solid var(--line);border-left:5px solid var(--line);
  border-radius:14px;padding:10px 12px;margin-bottom:8px;box-shadow:var(--shadow)}
.frow.sa{border-left-color:var(--good)}
.frow.sc{border-left-color:var(--warn)}
.frow.sr{border-left-color:var(--bad);opacity:.75}
.frow.flash{outline:2px solid var(--accent2)}
.frow .fhead{display:flex;gap:6px;align-items:center;font-size:12px;color:var(--muted);flex-wrap:wrap}
.frow .st{margin-left:auto;font-weight:800;font-size:13px}
.frow .st.sa{color:var(--good)}.frow .st.sc{color:var(--warn)}.frow .st.sr{color:var(--bad)}
.render{background:#fff;color:#0c1f3a;border-radius:10px;border:1px solid var(--line);
  padding:10px;overflow-x:auto;min-height:44px;font-size:14px;margin-top:8px}
.render .err{color:#566782;font:12px ui-monospace,Menlo,monospace;white-space:pre-wrap}
.fx{display:none;margin-top:8px}
.frow.open .fx{display:block}
.tex{white-space:pre-wrap;background:var(--card2);border:1px solid var(--line);border-radius:10px;
  padding:8px 10px;font:12px/1.5 ui-monospace,Menlo,monospace;color:var(--muted);
  overflow-x:auto;margin:6px 0}
.minig{width:100%;background:var(--card2);border:1px solid var(--line);border-radius:10px;margin:6px 0}
.facts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:8px;margin-top:8px}
.facts button{min-height:46px;font-weight:750;font-size:14px}
/* sorter */
.cells{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.cells button{min-height:58px;text-align:left;padding:9px 12px;border-radius:16px}
.cells .p{font-weight:800;font-size:15px}
.cells .d{color:var(--muted);font-size:12px;margin-top:1px}
.cells .x{grid-column:1/-1;background:var(--bad-soft);border-color:var(--bad)}
/* blitz */
.timer{font-size:34px;font-weight:800;font-variant-numeric:tabular-nums;text-align:center}
.timer.low{color:var(--bad)}
.fcard{position:relative;transition:transform .18s ease,opacity .18s ease}
.fcard.gone-r{transform:translateX(120%) rotate(8deg);opacity:0}
.fcard.gone-l{transform:translateX(-120%) rotate(-8deg);opacity:0}
.fmeta{color:var(--muted);font-size:13px;margin-bottom:6px}
.fmeta b{color:var(--ink)}
.acts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:12px}
.acts button{min-height:56px;font-weight:750;font-size:15.5px;border-radius:16px}
.under{display:flex;justify-content:space-between;margin-top:10px}
.under button{border:none;background:none;color:var(--muted);font-size:13.5px;padding:6px}
details.raw{margin-top:8px}
details.raw summary{color:var(--muted);font-size:12.5px;cursor:pointer}
/* heatmap (türkis ramp, validated light+dark) */
.hm{display:grid;grid-auto-flow:column;grid-template-rows:repeat(7,12px);gap:3px;
  justify-content:start;margin-top:8px}
.hm i{width:12px;height:12px;border-radius:3px;background:var(--track)}
.hm i.l1{background:var(--g1)}.hm i.l2{background:var(--g2)}
.hm i.l3{background:var(--g3)}.hm i.l4{background:var(--g4)}
.hmleg{display:flex;align-items:center;gap:4px;color:var(--muted);font-size:11.5px;margin-top:6px}
.hmleg i{width:11px;height:11px;border-radius:3px;background:var(--track);display:inline-block}
/* journal */
.days{list-style:none;margin:6px 0;padding:0}
.days li{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line);font-size:14px}
.badges{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:8px}
.badge{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 8px;
  text-align:center;box-shadow:var(--shadow)}
.badge .e{font-size:26px;filter:grayscale(1);opacity:.35}
.badge.won .e{filter:none;opacity:1}
.badge .n{font-size:11.5px;font-weight:700;margin-top:3px}
.badge .d{font-size:10.5px;color:var(--muted)}
/* paper list */
.plist{list-style:none;margin:8px 0;padding:0}
.plist li{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:10px 12px;margin-bottom:8px;box-shadow:var(--shadow)}
.plist .t{font-size:14px;font-weight:650}
.plist .m{color:var(--muted);font-size:12px;margin-top:2px}
.pbar{height:6px;background:var(--track);border-radius:4px;overflow:hidden;display:flex;margin-top:7px}
.pbar i{display:block;height:100%}
/* sheets & toast */
.sheet{position:fixed;inset:auto 0 0 0;background:var(--card);border-radius:20px 20px 0 0;
  border:1px solid var(--line);box-shadow:0 -8px 30px rgba(0,0,0,.3);padding:16px 16px
  calc(16px + env(safe-area-inset-bottom));transform:translateY(105%);transition:transform .22s ease;
  z-index:40;max-height:86vh;overflow-y:auto;max-width:480px;margin:0 auto}
.sheet.on{transform:none}
.scrim{position:fixed;inset:0;background:rgba(0,16,58,.45);opacity:0;pointer-events:none;
  transition:opacity .2s;z-index:30}
.scrim.on{opacity:1;pointer-events:auto}
textarea{width:100%;min-height:110px;background:var(--card2);color:var(--ink);
  border:1px solid var(--line);border-radius:12px;font:13px/1.5 ui-monospace,Menlo,monospace;
  padding:10px}
#toast{position:fixed;left:50%;bottom:calc(86px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(20px);
  background:var(--ink);color:var(--card);border-radius:20px;padding:8px 18px;font-size:14.5px;
  font-weight:650;opacity:0;transition:.25s;z-index:50;pointer-events:none;white-space:nowrap}
@media (prefers-color-scheme:dark){#toast{background:#eaf1ff;color:#00103a}}
#toast.on{opacity:1;transform:translateX(-50%)}
#xpfly{position:fixed;pointer-events:none;font-weight:800;color:var(--accent);z-index:50;
  opacity:0;font-size:17px;transition:transform .8s ease-out,opacity .8s}
.confetti{position:fixed;top:-24px;font-size:22px;z-index:60;pointer-events:none;
  animation:fall 1.6s ease-in forwards}
@keyframes fall{to{transform:translateY(105vh) rotate(540deg);opacity:.2}}
a{color:var(--accent)}
.exp-btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.exp-btns .wide{grid-column:1/-1;background:var(--accent);border-color:var(--accent);color:#fff;
  font-weight:750;min-height:52px}
.mut{color:var(--muted);font-size:12.5px}
</style>
</head>
<body>
<div class="app">

<!-- ============ HOME ============ -->
<section id="home" class="on">
  <header>
    <div class="brandlogo">__LOGO__</div>
    <div class="eyebrow">Chair of Railway Operations · Paper 1</div>
    <h1>🚂 Formula Express</h1>
    <p class="sub">corpus review · __NPAPERS__ papers · __NFORM__ formulas</p>
  </header>
  <div class="tiles">
    <div class="tile"><div class="big" id="t-streak">–</div><div class="lbl">🔥 day streak</div><div class="note" id="t-streak-note"></div></div>
    <div class="tile"><div class="big" id="t-xp">0</div><div class="lbl" id="t-rank">XP</div><div class="note" id="t-next"></div></div>
    <div class="tile"><div class="big" id="t-today">0</div><div class="lbl">decisions today</div><div class="note" id="t-today-xp"></div></div>
    <div class="tile"><div class="big" id="t-total">0%</div><div class="lbl">formulas reviewed</div><div class="note" id="t-total-n"></div></div>
  </div>
  <div class="card" style="margin-top:10px">
    <div style="font-weight:700;font-size:14px">Overall progress</div>
    <div class="bar" id="mainbar" role="img" aria-label="review progress"></div>
    <div class="chips" id="statchips"></div>
  </div>
  <div class="games">
    <div class="game" data-go="run"><span class="ico">📄</span>
      <div><div class="ttl">Paper Run</div><div class="dsc">a whole paper + its symbol graph · <span id="run-left"></span> papers left</div></div><span class="go">›</span></div>
    <div class="game" data-go="blitz"><span class="ico">⏱️</span>
      <div><div class="ttl">Blitz</div><div class="dsc">60-second sprint — best: <b id="blitz-best">0</b></div></div><span class="go">›</span></div>
    <div class="game" data-go="sort"><span class="ico">🧭</span>
      <div><div class="ttl">Shell Sorter</div><div class="dsc">file papers into P1–P5 · <span id="sort-left"></span> left</div></div><span class="go">›</span></div>
    <div class="game" data-go="journal"><span class="ico">📔</span>
      <div><div class="ttl">Journal</div><div class="dsc">streak calendar, badges, day log</div></div><span class="go">›</span></div>
    <div class="game" data-go="papers"><span class="ico">📚</span>
      <div><div class="ttl">Papers</div><div class="dsc">jump to a specific paper</div></div><span class="go">›</span></div>
  </div>
  <div class="exp-btns">
    <button class="wide" onclick="openExport()">⤴ Export decisions</button>
  </div>
  <p class="mut" style="text-align:center">progress autosaves in this browser · export often</p>
</section>

<!-- ============ PAPER RUN ============ -->
<section id="run">
  <div class="top"><button class="back" data-go="home">‹ Home</button>
    <span class="combo" id="combo"></span>
    <span class="info" id="run-info"></span></div>
  <div id="run-slot"></div>
</section>

<!-- ============ BLITZ ============ -->
<section id="blitz">
  <div class="top"><button class="back" data-go="home">‹ Home</button>
    <span class="info" id="blitz-score"></span></div>
  <div class="timer" id="blitz-timer">60</div>
  <div id="blitz-slot"></div>
  <div id="blitz-start" class="card" style="text-align:center;margin-top:14px">
    <div style="font-size:40px">⏱️</div>
    <p>60 seconds. Accept or reject as many formulas as you can.<br>
    <span class="mut">Unsure? Skip — no penalty.</span></p>
    <button class="b-acc" style="min-height:52px;width:100%;font-weight:750"
      onclick="startBlitz()">Start!</button>
  </div>
</section>

<!-- ============ SORTER ============ -->
<section id="sort">
  <div class="top"><button class="back" data-go="home">‹ Home</button>
    <span class="info" id="sort-info"></span></div>
  <div id="sort-slot"></div>
</section>

<!-- ============ JOURNAL ============ -->
<section id="journal">
  <div class="top"><button class="back" data-go="home">‹ Home</button></div>
  <h2 style="margin-top:0">📔 Journal</h2>
  <div class="card">
    <div style="font-weight:700;font-size:14px">Activity — last 12 weeks</div>
    <div class="hm" id="heatmap"></div>
    <div class="hmleg">less <i></i><i style="background:var(--g1)"></i><i style="background:var(--g2)"></i><i style="background:var(--g3)"></i><i style="background:var(--g4)"></i> more</div>
  </div>
  <h2>Badges</h2>
  <div class="badges" id="badges"></div>
  <h2>Day log</h2>
  <ul class="days" id="daylog"></ul>
  <h2>Completed papers</h2>
  <ul class="days" id="donepapers"></ul>
</section>

<!-- ============ PAPER LIST ============ -->
<section id="papers">
  <div class="top"><button class="back" data-go="home">‹ Home</button></div>
  <h2 style="margin-top:0">📚 Papers <span class="mut" id="plist-sub"></span></h2>
  <ul class="plist" id="plist"></ul>
</section>

</div>

<!-- fix sheet -->
<div class="scrim" id="scrim" onclick="closeSheets()"></div>
<div class="sheet" id="fixsheet">
  <h2 style="margin-top:0">✎ Fix LaTeX</h2>
  <textarea id="fix-ta" spellcheck="false" autocapitalize="off"></textarea>
  <div class="render" id="fix-prev" style="margin-top:10px"></div>
  <div class="row" style="margin-top:12px">
    <button onclick="closeSheets()">Cancel</button>
    <button class="b-fix" style="flex:1;font-weight:750;min-height:48px" onclick="saveFix()">Save fix ✎ (+25 XP)</button>
  </div>
</div>

<!-- cell sheet (on finishing a paper) -->
<div class="sheet" id="cellsheet">
  <h2 style="margin-top:0">🧭 File this paper</h2>
  <p class="mut" id="cell-title"></p>
  <div class="cells" id="cell-btns"></div>
  <div class="row" style="margin-top:12px"><button onclick="skipCell()" style="flex:1">Skip for now</button></div>
</div>

<!-- export sheet -->
<div class="sheet" id="expsheet">
  <h2 style="margin-top:0">⤴ Export decisions</h2>
  <p class="mut" id="exp-sum"></p>
  <div class="exp-btns">
    <button class="wide" onclick="shareExport()">📲 Share (Telegram…)</button>
    <button onclick="downloadExport()">⬇ Download JSON</button>
    <button onclick="copyExport()">📋 Copy JSON</button>
  </div>
  <h2>Import / restore</h2>
  <p class="mut">Merge a previous export (decisions from another device).</p>
  <input type="file" id="imp-file" accept=".json,application/json">
  <div class="row" style="margin-top:14px"><button onclick="closeSheets()" style="flex:1">Close</button></div>
</div>

<div id="toast"></div>
<div id="xpfly"></div>

<script id="corpus-data" type="application/json">__DATA__</script>
<script>
"use strict";
/* ---------- data ---------- */
/* formula record: [0]=id [1]=label [2]=latex [3]=method [4]=page [5]=syms [6]=ops [7]=rel */
const RAW = JSON.parse(document.getElementById("corpus-data").textContent);
const PAPERS = RAW.papers;
const RUSHP  = PAPERS.filter(p => p.f.length);
const NFORM  = RAW.n_formulas;
const CELLS = [
  ["P1","🚄","Railway × Rescheduling","the target cell"],
  ["P2","🚌","Transport × Rescheduling","disruption response, other modes"],
  ["P3","🛤️","Railway × Operations","planning / dispatching, rail"],
  ["P4","🚦","Transport × Operations","planning, other modes"],
  ["P5","🏭","Production × Rescheduling","outer analogical shell"],
];
const RANKS = ["Platform Rookie","Signal Apprentice","Track Inspector","Conductor",
  "Dispatcher","Timetable Tactician","Network Controller","Chief of Operations",
  "Rescheduling Sage","Dispatch Legend"];
const PRAISE = ["Nice! 🚆","On track! 🛤️","Signal clear ✅","Full steam ahead! 🚂",
  "Sharp eye! 👀","Crisp call!","Rolling on! 🎯","Great pace! ⚡","Clean sweep!","Keep it up! 🌟"];
const BADGES = [
  ["b1","🎫","First stop","1 decision", s=>decCount()>=1],
  ["b100","🚉","Century","100 formulas", s=>decCount()>=100],
  ["b500","🚈","Half-K","500 formulas", s=>decCount()>=500],
  ["b1k","🚄","K-Club","1,000 formulas", s=>decCount()>=1000],
  ["b5k","🌟","Marathon","5,000 formulas", s=>decCount()>=5000],
  ["ball","🏆","Terminus","every formula", s=>decCount()>=NFORM],
  ["bp1","📄","Paper clear","finish a paper", s=>donePapers().length>=1],
  ["bp10","📚","Ten down","finish 10 papers", s=>donePapers().length>=10],
  ["bs3","🔥","Warm streak","3-day streak", s=>streak()>=3],
  ["bs7","🔥","Week of fire","7-day streak", s=>streak()>=7],
  ["bs14","🌋","Fortnight","14-day streak", s=>streak()>=14],
  ["bfix","🔧","Fixer","10 corrections", s=>statusCounts().c>=10],
  ["bsort","🧭","Navigator","25 papers sorted", s=>Object.keys(S.cells).length>=25],
  ["bsall","🗺️","Cartographer","all papers sorted", s=>Object.keys(S.cells).length>=PAPERS.length],
  ["bz20","⏱️","Blitz 20","20 in one blitz", s=>S.best>=20],
  ["bz40","🚀","Blitz 40","40 in one blitz", s=>S.best>=40],
];

/* ---------- state (same key & shape as v1 — progress survives) ---------- */
const LSK = "fx:state:v1";
let S = load();
function load(){
  try{ const s = JSON.parse(localStorage.getItem(LSK) || "null"); if (s) return s; }catch(e){}
  const s = {dec:{}, cells:{}, xp:0, days:{}, best:0, badges:{}};
  try{
    for (let i=0;i<localStorage.length;i++){
      const k = localStorage.key(i);
      if (k && k.startsWith("review:")){
        const pk = k.slice(7), o = JSON.parse(localStorage.getItem(k)||"{}");
        for (const fid in o){ if (o[fid].status){
          (s.dec[pk] = s.dec[pk] || {})[fid] = {s:o[fid].status[0], n:o[fid].note||null}; }}
      }
    }
  }catch(e){}
  return s;
}
function save(){ try{ localStorage.setItem(LSK, JSON.stringify(S)); }catch(e){ toast("⚠ could not save — export!"); } }
function dayKey(off){ const d = new Date(); d.setDate(d.getDate()-off);
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); }
function today(){ return dayKey(0); }

/* ---------- derived ---------- */
function decCount(){ let n=0; for (const pk in S.dec) n += Object.keys(S.dec[pk]).length; return n; }
function statusCounts(){ let a=0,c=0,r=0;
  for (const pk in S.dec) for (const id in S.dec[pk]){
    const st=S.dec[pk][id].s; if(st==="a")a++; else if(st==="c")c++; else if(st==="r")r++; }
  return {a,c,r}; }
function paperProgress(p){ const d=S.dec[p.k]||{}; let n=0;
  for (const f of p.f) if (d[f[0]]) n++; return n; }
function donePapers(){ return RUSHP.filter(p => paperProgress(p) === p.f.length); }
function streak(){
  let n=0, off=0;
  if (!S.days[dayKey(0)]) off = 1;
  while (S.days[dayKey(off)]) { n++; off++; }
  return n;
}
function level(){ return Math.min(RANKS.length, Math.floor(Math.sqrt(S.xp/150)) + 1); }
function nextLevelXp(){ const l=level(); return l>=RANKS.length ? null : 150*l*l; }

/* ---------- navigation ---------- */
function go(id){
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on", s.id===id));
  window.scrollTo(0,0);
  if (id==="home") paintHome();
  if (id==="run") paintRun();
  if (id==="sort") paintSort();
  if (id==="journal") paintJournal();
  if (id==="papers") paintPapers();
  if (id==="blitz"){ stopBlitz(); document.getElementById("blitz-start").style.display="block";
    document.getElementById("blitz-slot").innerHTML=""; document.getElementById("blitz-timer").textContent="60";
    document.getElementById("blitz-score").textContent="best "+S.best; }
}
document.addEventListener("click", e=>{
  const g = e.target.closest("[data-go]"); if (g) go(g.dataset.go);
});

/* ---------- feedback ---------- */
let toastT;
function toast(msg){ const t=document.getElementById("toast"); t.textContent=msg;
  t.classList.add("on"); clearTimeout(toastT); toastT=setTimeout(()=>t.classList.remove("on"),1300); }
function xpFly(amount, x, y){
  const el=document.getElementById("xpfly");
  el.textContent="+"+amount+" XP"; el.style.left=(x-30)+"px"; el.style.top=(y-30)+"px";
  el.style.opacity="1"; el.style.transform="none";
  requestAnimationFrame(()=>requestAnimationFrame(()=>{
    el.style.transform="translateY(-70px)"; el.style.opacity="0"; }));
}
function confetti(n){
  for (let i=0;i<(n||18);i++){
    const c=document.createElement("div"); c.className="confetti";
    c.textContent=["🎉","✨","🎊","⭐","🍀"][i%5];
    c.style.left=(5+Math.random()*90)+"vw";
    c.style.animationDelay=(Math.random()*0.4)+"s";
    document.body.appendChild(c); setTimeout(()=>c.remove(),2200);
  }
}
function vibrate(ms){ if (navigator.vibrate) try{navigator.vibrate(ms);}catch(e){} }

/* ---------- XP / combo / badges ---------- */
let combo=0, lastDecT=0;
function award(base, ev, count){
  count = count || 1;
  const now=Date.now();
  combo = (now-lastDecT<20000) ? combo+count : count; lastDecT=now;
  const mult = Math.min(4, 1+Math.floor(combo/8));
  const xp = base*mult;
  S.xp += xp;
  const d = S.days[today()] = S.days[today()] || {n:0,xp:0};
  d.n+=count; d.xp+=xp;
  const before=level();
  checkBadges();
  save();
  const c=document.getElementById("combo");
  if (c) c.textContent = combo>=8 ? "×"+mult+" combo "+combo : "";
  if (ev) xpFly(xp, ev.clientX||innerWidth/2, ev.clientY||innerHeight/2);
  if (level()>before){ confetti(24); toast("🎖 Rank up — "+RANKS[level()-1]+"!"); }
  return xp;
}
function checkBadges(){
  for (const b of BADGES){
    if (!S.badges[b[0]] && b[4](S)){ S.badges[b[0]]=today(); confetti(20); toast(b[1]+" Badge: "+b[2]+"!"); }
  }
}

/* ---------- MathJax (lazy) ---------- */
function renderMath(el, latex){
  el.textContent=""; const d=document.createElement("div");
  d.textContent="\\[ "+latex+" \\]"; el.appendChild(d);
  if (window.MathJax && MathJax.typesetPromise && !window.__mjfail){
    MathJax.typesetPromise([el]).catch(()=>{ el.innerHTML='<div class="err"></div>';
      el.firstChild.textContent=latex; });
  } else {
    el.innerHTML='<div class="err"></div>'; el.firstChild.textContent=latex;
  }
}
const lazyIO = ("IntersectionObserver" in window) ? new IntersectionObserver(es=>{
  for (const e of es){ if (e.isIntersecting){
    lazyIO.unobserve(e.target);
    renderMath(e.target, e.target.dataset.tex);
  }}
},{rootMargin:"200px"}) : null;
function lazyMath(el, latex){
  el.dataset.tex = latex;
  if (lazyIO) lazyIO.observe(el);
  else renderMath(el, latex);
}

/* ---------- symbol graphs (deterministic, no randomness) ---------- */
function buildPaperGraph(p){
  // symbol -> [formula indexes]
  const symTo = {};
  p.f.forEach((f,fi)=>{ for (const [name] of f[5]) (symTo[name]=symTo[name]||[]).push(fi); });
  let shared = Object.entries(symTo).filter(([,l])=>l.length>=2);
  if (!shared.length) shared = Object.entries(symTo);          // tiny papers: show all
  shared.sort((a,b)=> b[1].length-a[1].length || (a[0]<b[0]?-1:1));
  shared = shared.slice(0,28);
  const nodes=[], edges=[];
  p.f.forEach((f,fi)=> nodes.push({t:"f", fi, id:f[0]}));
  shared.forEach(([name,fis])=>{
    const si=nodes.length; nodes.push({t:"s", name, deg:fis.length});
    for (const fi of fis) edges.push([si, fi]);
  });
  return {nodes, edges};
}
function layoutGraph(g, W, H){
  const n=g.nodes.length;
  const pos=g.nodes.map((nd,i)=>{                      // deterministic init: two rings
    const ang = 2*Math.PI*i/n + (nd.t==="s"?0.5:0);
    const r = nd.t==="s" ? 0.28 : 0.42;
    return [W/2 + W*r*Math.cos(ang), H/2 + H*r*Math.sin(ang)];
  });
  const adj=g.edges;
  const K = Math.sqrt(W*H/Math.max(1,n)) * 0.9;
  for (let it=0; it<120; it++){
    const disp = pos.map(()=>[0,0]);
    for (let i=0;i<n;i++) for (let j=i+1;j<n;j++){       // repulsion
      let dx=pos[i][0]-pos[j][0], dy=pos[i][1]-pos[j][1];
      let d2=dx*dx+dy*dy; if (d2<0.01) { dx=(i-j)*0.1; dy=0.1; d2=dx*dx+dy*dy; }
      const f=K*K/d2;
      disp[i][0]+=dx*f; disp[i][1]+=dy*f; disp[j][0]-=dx*f; disp[j][1]-=dy*f;
    }
    for (const [a,b] of adj){                            // attraction
      const dx=pos[a][0]-pos[b][0], dy=pos[a][1]-pos[b][1];
      const d=Math.sqrt(dx*dx+dy*dy)||0.1, f=d/K*0.06;
      disp[a][0]-=dx*f; disp[a][1]-=dy*f; disp[b][0]+=dx*f; disp[b][1]+=dy*f;
    }
    const T = 8*(1-it/120)+1;
    for (let i=0;i<n;i++){
      const d=Math.sqrt(disp[i][0]**2+disp[i][1]**2)||1;
      pos[i][0]+=disp[i][0]/d*Math.min(d,T); pos[i][1]+=disp[i][1]/d*Math.min(d,T);
      pos[i][0]=Math.max(16,Math.min(W-16,pos[i][0]));
      pos[i][1]=Math.max(14,Math.min(H-14,pos[i][1]));
    }
  }
  return pos;
}
const SVGNS="http://www.w3.org/2000/svg";
function el(tag, attrs){ const e=document.createElementNS(SVGNS,tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]); return e; }
function drawPaperGraph(host, p){
  const g=buildPaperGraph(p);
  const W=440, H=Math.max(220, Math.min(430, 90+g.nodes.length*3.2));
  const pos=layoutGraph(g, W, H);
  const svg=el("svg",{viewBox:"0 0 "+W+" "+H,role:"img","aria-label":"paper symbol graph"});
  for (let ei=0; ei<g.edges.length; ei++){
    const [a,b]=g.edges[ei];
    svg.appendChild(el("line",{class:"gedge","data-s":a,"data-f":b,
      x1:pos[a][0],y1:pos[a][1],x2:pos[b][0],y2:pos[b][1]}));
  }
  g.nodes.forEach((nd,i)=>{
    if (nd.t==="f"){
      const d=(S.dec[p.k]||{})[nd.id];
      const c=el("circle",{class:"gnode-f"+(d?" s"+d.s:""),cx:pos[i][0],cy:pos[i][1],r:5.5,
        "data-node":i,"data-fid":nd.id});
      svg.appendChild(c);
    } else {
      const r=4+Math.min(6,nd.deg*0.8);
      svg.appendChild(el("circle",{class:"gnode-s",cx:pos[i][0],cy:pos[i][1],r:r,"data-node":i,"data-sym":nd.name}));
      const t=el("text",{class:"glabel",x:pos[i][0]+r+2,y:pos[i][1]+3});
      t.textContent=nd.name; svg.appendChild(t);
    }
  });
  host.innerHTML=""; host.appendChild(svg);
  let hlSym=null;
  svg.addEventListener("click", e=>{
    const fEl=e.target.closest(".gnode-f");
    if (fEl){ const row=document.getElementById("fr-"+fEl.dataset.fid);
      if (row){ row.scrollIntoView({behavior:"smooth",block:"center"});
        row.classList.add("flash"); setTimeout(()=>row.classList.remove("flash"),1200); }
      return; }
    const sEl=e.target.closest(".gnode-s");
    if (!sEl) return;
    const si=sEl.getAttribute("data-node");
    hlSym = (hlSym===si) ? null : si;
    svg.querySelectorAll(".gedge").forEach(l=>l.classList.toggle("hl", hlSym!==null && l.getAttribute("data-s")===hlSym));
    const linked=new Set();
    if (hlSym!==null) g.edges.forEach(([a,b])=>{ if(String(a)===hlSym) linked.add(b); });
    svg.querySelectorAll(".gnode-f").forEach(c=>c.classList.toggle("hl", linked.has(+c.getAttribute("data-node"))));
    svg.querySelectorAll(".gnode-s").forEach(c=>c.classList.toggle("dim", hlSym!==null && c.getAttribute("data-node")!==hlSym));
    toast(hlSym!==null ? "symbol "+sEl.getAttribute("data-sym")+" — in "+linked.size+" formulas" : "highlight off");
  });
  return svg;
}
function drawMiniGraph(host, f){
  // star: relation core, operators inner ring, symbols outer ring
  const syms=f[5], ops=f[6], rel=f[7]||"·";
  const W=300, H=150, cx=W/2, cy=H/2;
  const svg=el("svg",{viewBox:"0 0 "+W+" "+H,class:"minig",role:"img","aria-label":"formula structure"});
  const outer=syms.map((s,i)=>{
    const ang=2*Math.PI*i/Math.max(1,syms.length)-Math.PI/2;
    return [cx+108*Math.cos(ang), cy+56*Math.sin(ang), s];
  });
  const inner=ops.map((o,i)=>{
    const ang=2*Math.PI*i/Math.max(1,ops.length)-Math.PI/2+0.4;
    return [cx+52*Math.cos(ang), cy+30*Math.sin(ang), o];
  });
  for (const [x,y] of outer) svg.appendChild(el("line",{class:"gedge",x1:cx,y1:cy,x2:x,y2:y}));
  for (const [x,y] of inner) svg.appendChild(el("line",{class:"gedge",x1:cx,y1:cy,x2:x,y2:y}));
  for (const [x,y,o] of inner){
    svg.appendChild(el("rect",{x:x-11,y:y-9,width:22,height:18,rx:5,class:"gnode-s"}));
    const t=el("text",{class:"glabel",x:x,y:y+3.5,"text-anchor":"middle","font-size":"10"});
    t.textContent=o[0]; t.setAttribute("fill","#fff"); svg.appendChild(t);
  }
  for (const [x,y,s] of outer){
    const r=4+Math.min(5,s[1]);
    svg.appendChild(el("circle",{class:"gnode-f",cx:x,cy:y,r:r}));
    const t=el("text",{class:"glabel",x:x,y:y-r-3,"text-anchor":"middle"});
    t.textContent=s[0]+(s[1]>1?" ×"+s[1]:""); svg.appendChild(t);
  }
  svg.appendChild(el("circle",{cx:cx,cy:cy,r:13,class:"gnode-s"}));
  const t=el("text",{x:cx,y:cy+4,"text-anchor":"middle","font-size":"12","font-weight":"800"});
  t.textContent=rel; t.setAttribute("fill","#fff"); svg.appendChild(t);
  host.innerHTML=""; host.appendChild(svg);
}

/* ---------- decisions core ---------- */
const XP_BASE = {a:10, r:10, c:25, cell:15, blitz:8, finish:50};
let undoStack=[];
function setDec(p, fid, st, note){
  const prev = (S.dec[p.k]||{})[fid] || null;
  (S.dec[p.k] = S.dec[p.k] || {})[fid] = {s:st, n:note||null};
  return prev;
}
function decide(p, f, st, note, ev, mode){
  const prev = setDec(p, f[0], st, note);
  const xp = award(mode==="blitz" ? XP_BASE.blitz : XP_BASE[st], ev);
  undoStack.push({kind:"dec", pk:p.k, items:[[f[0],prev]], xp, n:1});
  if (undoStack.length>25) undoStack.shift();
  vibrate(12);
  if (Math.random()<0.3) toast(PRAISE[Math.floor(Math.random()*PRAISE.length)]);
  save();
}
function bulkDecide(p, fids, st, ev){
  const items = fids.map(fid=>[fid, setDec(p, fid, st, null)]);
  const xp = award(XP_BASE[st]*fids.length, ev, fids.length);
  undoStack.push({kind:"dec", pk:p.k, items, xp, n:fids.length});
  if (undoStack.length>25) undoStack.shift();
  vibrate(18); save();
  toast((st==="a"?"✓ accepted ":"✗ rejected ")+fids.length+" formulas");
}
function undo(){
  const u=undoStack.pop(); if(!u){ toast("nothing to undo"); return; }
  if (u.kind==="dec"){
    for (const [fid,prev] of u.items){
      if (prev) S.dec[u.pk][fid]=prev; else delete S.dec[u.pk][fid];
    }
  } else if (u.kind==="cell"){
    if (u.prev) S.cells[u.pk]=u.prev; else delete S.cells[u.pk];
  }
  S.xp=Math.max(0,S.xp-u.xp);
  const d=S.days[today()]; if(d){ d.n=Math.max(0,d.n-(u.n||1)); d.xp=Math.max(0,d.xp-u.xp); }
  combo=Math.max(0,combo-(u.n||1)); save(); toast("↶ undone");
}

/* ---------- PAPER RUN ---------- */
let runIdx=0, fixCtx=null, cellCtx=null;
function nextRunPaper(){
  for (let i=0;i<RUSHP.length;i++){
    const p=RUSHP[(runIdx+i)%RUSHP.length];
    if (paperProgress(p) < p.f.length){ runIdx=(runIdx+i)%RUSHP.length; return p; }
  }
  return null;
}
function escapeHtml(s){ return s.replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function statSym(st){ return st==="a"?"✓":st==="c"?"✎":st==="r"?"✗":""; }
function paintRun(){
  const slot=document.getElementById("run-slot");
  const p=nextRunPaper();
  document.getElementById("run-info").textContent =
    donePapers().length+"/"+RUSHP.length+" papers";
  if (!p){ slot.innerHTML='<div class="card" style="text-align:center"><div style="font-size:44px">🏆</div><p>Every paper reviewed. Legendary.</p></div>'; confetti(40); return; }
  const rows = p.f.map(f=>`
    <div class="frow" id="fr-${f[0]}" data-fid="${f[0]}">
      <div class="fhead"><span class="chip">${f[0]}</span>
        ${f[1]?'<span class="chip">'+escapeHtml(f[1])+"</span>":""}
        <span class="chip">${f[3]}</span>${f[4]?'<span class="mut">p.'+f[4]+"</span>":""}
        <span class="st"></span></div>
      <div class="render"></div>
      <div class="fx">
        <div class="minig-slot"></div>
        <div class="tex">${escapeHtml(f[2])}</div>
        <div class="facts">
          <button class="b-rej" data-a="r">✗ reject</button>
          <button class="b-fix" data-a="c">✎ fix</button>
          <button class="b-acc" data-a="a">✓ accept</button>
        </div>
      </div>
    </div>`).join("");
  slot.innerHTML=`
    <div class="card">
      <div class="ptitle">${escapeHtml(p.t)}</div>
      <div class="pmeta">${escapeHtml(p.v)}${p.y?" · "+p.y:""} · ${p.c} citations · ${p.f.length} formulas
        ${p.d?` · <a href="https://doi.org/${encodeURIComponent(p.d)}" target="_blank" rel="noopener">DOI</a>`:""}</div>
      ${p.ot?'<span class="hint-ot">⚠ topical screen: looks off-topic</span>':""}
      ${S.cells[p.k]?'<span class="cellchip">🧭 '+(S.cells[p.k]==="X"?"out of scope":S.cells[p.k])+"</span>":""}
      <div class="gwrap" id="pgraph"></div>
      <div class="glegend">● formula (coloured by your ✓✎✗) · <span style="color:var(--accent)">●</span> shared symbol — tap a symbol to trace it, tap a formula dot to jump</div>
    </div>
    <div class="bulk">
      <button class="b-acc" id="bk-acc">✓ accept rest</button>
      <button class="b-rej" id="bk-rej">✗ reject all</button>
      <button class="b-fin" id="bk-fin">🏁 finish</button>
    </div>
    <div id="frows">${rows}</div>
    <div class="under"><button id="run-undo">↶ undo</button>
      <button id="run-skip">skip paper ›</button></div>`;
  drawPaperGraph(document.getElementById("pgraph"), p);
  for (const f of p.f){
    lazyMath(slot.querySelector("#fr-"+f[0]+" .render"), f[2]);
  }
  refreshRun(p);
  document.getElementById("frows").addEventListener("click", e=>{
    const row=e.target.closest(".frow"); if(!row) return;
    const f=p.f.find(x=>x[0]===row.dataset.fid);
    const btn=e.target.closest("[data-a]");
    if (btn){
      const a=btn.dataset.a;
      if (a==="c"){
        fixCtx={p,f};
        document.getElementById("fix-ta").value=((S.dec[p.k]||{})[f[0]]||{}).n||f[2];
        renderMath(document.getElementById("fix-prev"), document.getElementById("fix-ta").value);
        openSheet("fixsheet");
      } else {
        decide(p,f,a,null,e,"run"); refreshRun(p);
      }
      return;
    }
    row.classList.toggle("open");
    if (row.classList.contains("open")){
      const ms=row.querySelector(".minig-slot");
      if (!ms.firstChild) drawMiniGraph(ms, f);
    }
  });
  document.getElementById("bk-acc").onclick=(e)=>{
    const rest=p.f.filter(f=>!(S.dec[p.k]||{})[f[0]]).map(f=>f[0]);
    if (!rest.length){ toast("nothing left"); return; }
    bulkDecide(p, rest, "a", e); refreshRun(p);
  };
  document.getElementById("bk-rej").onclick=(e)=>{
    if (!confirm("Reject ALL "+p.f.length+" formulas of this paper?")) return;
    bulkDecide(p, p.f.map(f=>f[0]), "r", e); refreshRun(p);
  };
  document.getElementById("bk-fin").onclick=()=>finishPaper(p);
  document.getElementById("run-undo").onclick=()=>{ undo(); refreshRun(p); };
  document.getElementById("run-skip").onclick=()=>{ runIdx=(runIdx+1)%RUSHP.length; paintRun(); };
}
function refreshRun(p){
  const d=S.dec[p.k]||{};
  let done=0;
  for (const f of p.f){
    const row=document.getElementById("fr-"+f[0]); if(!row) continue;
    const st=(d[f[0]]||{}).s||"";
    row.classList.remove("sa","sc","sr"); if (st) row.classList.add("s"+st);
    const stEl=row.querySelector(".st");
    stEl.textContent=statSym(st); stEl.className="st"+(st?" s"+st:"");
    if (st) done++;
  }
  const host=document.getElementById("pgraph");
  if (host && host.firstChild){
    host.querySelectorAll(".gnode-f").forEach(c=>{
      const st=(d[c.dataset.fid]||{}).s;
      c.classList.remove("sa","sc","sr"); if (st) c.classList.add("s"+st);
    });
  }
  const acc=document.getElementById("bk-acc");
  if (acc) acc.textContent="✓ accept rest ("+(p.f.length-done)+")";
  const fin=document.getElementById("bk-fin");
  if (fin) fin.style.opacity = done===p.f.length ? 1 : .55;
}
function finishPaper(p){
  const missing=p.f.filter(f=>!(S.dec[p.k]||{})[f[0]]).length;
  if (missing){ toast(missing+" formulas still undecided"); return; }
  if (!S.cells[p.k]){ cellCtx=p; openCellSheet(p); return; }
  completePaper(p);
}
function completePaper(p){
  award(XP_BASE.finish, null, 0);
  confetti(28); toast("📄 Paper complete! +"+XP_BASE.finish+" XP");
  save();
  runIdx=(runIdx+1)%RUSHP.length;
  setTimeout(paintRun, 500);
}
function openCellSheet(p){
  document.getElementById("cell-title").textContent=p.t;
  const host=document.getElementById("cell-btns");
  host.innerHTML = CELLS.map(c=>
    `<button data-cell="${c[0]}"><div class="p">${c[1]} ${c[0]}</div><div class="d">${c[2]}</div></button>`).join("")+
    '<button class="x" data-cell="X"><div class="p">🗑️ Out of scope</div><div class="d">off-topic / production operations</div></button>';
  host.onclick=(e)=>{
    const b=e.target.closest("[data-cell]"); if(!b) return;
    const prev=S.cells[cellCtx.k]||null;
    S.cells[cellCtx.k]=b.dataset.cell;
    const xp=award(XP_BASE.cell, e);
    undoStack.push({kind:"cell", pk:cellCtx.k, prev, xp, n:1});
    closeSheets(); save();
    completePaper(cellCtx); cellCtx=null;
  };
  openSheet("cellsheet");
}
function skipCell(){ closeSheets(); if (cellCtx){ completePaper(cellCtx); cellCtx=null; } }

/* ---------- fix sheet ---------- */
let fixDeb;
document.getElementById("fix-ta").addEventListener("input", ()=>{
  clearTimeout(fixDeb);
  fixDeb=setTimeout(()=>renderMath(document.getElementById("fix-prev"),
    document.getElementById("fix-ta").value), 350);
});
function saveFix(){
  if (!fixCtx) return;
  const v=document.getElementById("fix-ta").value.trim();
  decide(fixCtx.p, fixCtx.f, "c", v, null, "run");
  closeSheets();
  refreshRun(fixCtx.p);
  const row=document.getElementById("fr-"+fixCtx.f[0]);
  if (row) renderMath(row.querySelector(".render"), v);
  fixCtx=null;
}

/* ---------- BLITZ ---------- */
function cardHTML(p,f){
  const pg = f[4] ? " · p."+f[4] : "";
  const t = (p.t.length>90 ? p.t.slice(0,88)+"…" : p.t) + (p.y? " ("+p.y+")":"");
  return `<div class="card fcard">
    <div class="fmeta"><b>${escapeHtml(t)}</b><br>
      <span class="chip">${f[0]}</span> ${f[1]?'<span class="chip">'+escapeHtml(f[1])+"</span>":""}
      <span class="chip">${f[3]}</span><span class="mut">${pg}</span></div>
    <div class="render fr"></div>
    <details class="raw"><summary>raw LaTeX</summary><div class="tex"></div></details>
    <div class="acts">
      <button class="b-rej" data-act="r">✗<br>reject</button>
      <button data-act="skip">↷<br>skip</button>
      <button class="b-acc" data-act="a">✓<br>accept</button>
    </div>
    <div class="under">
      <button data-act="undo">↶ undo</button>
      <span class="mut" style="align-self:center">swipe → accept · ← reject</span>
      <span></span>
    </div></div>`;
}
function mountCard(slot, p, f, onAct){
  slot.innerHTML = cardHTML(p,f);
  const card = slot.querySelector(".fcard");
  card.querySelector(".tex").textContent = f[2];
  renderMath(card.querySelector(".fr"), f[2]);
  card.addEventListener("click", e=>{
    const b = e.target.closest("[data-act]"); if (b) onAct(b.dataset.act, e);
  });
  let sx=0, sy=0, dx=0, swiping=false;
  card.addEventListener("touchstart", e=>{
    if (e.target.closest(".render")||e.target.closest(".tex")) return;
    sx=e.touches[0].clientX; sy=e.touches[0].clientY; dx=0; swiping=true;
  },{passive:true});
  card.addEventListener("touchmove", e=>{
    if (!swiping) return;
    dx=e.touches[0].clientX-sx;
    const dy=e.touches[0].clientY-sy;
    if (Math.abs(dy)>Math.abs(dx)*1.2){ swiping=false; card.style.transform=""; return; }
    card.style.transform="translateX("+dx+"px) rotate("+(dx/28)+"deg)";
  },{passive:true});
  card.addEventListener("touchend", ()=>{
    if (!swiping) return; swiping=false;
    if (dx>90) onAct("a", {clientX:innerWidth-60, clientY:innerHeight/2});
    else if (dx<-90) onAct("r", {clientX:60, clientY:innerHeight/2});
    else card.style.transform="";
  });
  return card;
}
let blitzTimer=null, blitzLeft=60, blitzScore=0, blitzPool=[];
function startBlitz(){
  blitzPool=[];
  for (const p of RUSHP){ const d=S.dec[p.k]||{};
    for (const f of p.f) if (!d[f[0]]) blitzPool.push({p,f}); }
  for (let i=blitzPool.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1));
    [blitzPool[i],blitzPool[j]]=[blitzPool[j],blitzPool[i]]; }
  if (!blitzPool.length){ toast("nothing left to review 🏆"); return; }
  blitzScore=0; blitzLeft=60;
  document.getElementById("blitz-start").style.display="none";
  blitzTimer=setInterval(()=>{
    blitzLeft--;
    const t=document.getElementById("blitz-timer");
    t.textContent=blitzLeft; t.classList.toggle("low", blitzLeft<=10);
    if (blitzLeft<=0) endBlitz();
  },1000);
  paintBlitz();
}
function stopBlitz(){ if (blitzTimer){ clearInterval(blitzTimer); blitzTimer=null; } }
function paintBlitz(){
  const slot=document.getElementById("blitz-slot");
  const nx=blitzPool.shift();
  document.getElementById("blitz-score").textContent="score "+blitzScore+" · best "+S.best;
  if (!nx){ endBlitz(); return; }
  mountCard(slot, nx.p, nx.f, (act,ev)=>{
    if (!blitzTimer) return;
    const card=slot.querySelector(".fcard");
    if (act==="a"||act==="r"){
      decide(nx.p,nx.f,act,null,ev,"blitz"); blitzScore++;
      card.classList.add(act==="a"?"gone-r":"gone-l");
      setTimeout(paintBlitz,110);
    } else if (act==="skip"){ blitzPool.push(nx); paintBlitz(); }
    else if (act==="undo"){ undo(); blitzScore=Math.max(0,blitzScore-1); paintBlitz(); }
  });
}
function endBlitz(){
  stopBlitz();
  const slot=document.getElementById("blitz-slot");
  const isBest = blitzScore>S.best;
  if (isBest){ S.best=blitzScore; checkBadges(); save(); confetti(30); }
  slot.innerHTML='<div class="card" style="text-align:center"><div style="font-size:44px">'+
    (isBest?"🏅":"⏱️")+'</div><div class="timer">'+blitzScore+"</div><p>"+
    (isBest?"New best!":"formulas in 60s · best "+S.best)+'</p>'+
    '<button class="b-acc" style="min-height:52px;width:100%;font-weight:750" onclick="startBlitz()">Again!</button></div>';
  document.getElementById("blitz-timer").textContent="60";
  document.getElementById("blitz-timer").classList.remove("low");
}

/* ---------- SORTER ---------- */
function paintSort(){
  const slot=document.getElementById("sort-slot");
  const left=PAPERS.filter(p=>!S.cells[p.k]).length;
  document.getElementById("sort-info").textContent=(PAPERS.length-left)+"/"+PAPERS.length+" sorted";
  const p=PAPERS.find(x=>!S.cells[x.k]);
  if (!p){ slot.innerHTML='<div class="card" style="text-align:center"><div style="font-size:44px">🗺️</div><p>Every paper filed. Cartographer!</p></div>'; return; }
  const cells = CELLS.map(c=>
    `<button data-cell="${c[0]}"><div class="p">${c[1]} ${c[0]}</div><div class="d">${c[2]}</div></button>`).join("");
  slot.innerHTML=`<div class="card">
    <div class="ptitle">${escapeHtml(p.t)}</div>
    <div class="pmeta">${escapeHtml(p.v)}${p.y?" · "+p.y:""} · ${p.c} citations · ${p.f.length} formulas
      ${p.d?` · <a href="https://doi.org/${encodeURIComponent(p.d)}" target="_blank" rel="noopener">DOI</a>`:""}</div>
    ${p.ot?'<span class="hint-ot">⚠ topical screen: looks off-topic</span>':""}
    <div class="cells">${cells}
      <button class="x" data-cell="X"><div class="p">🗑️ Out of scope</div><div class="d">off-topic / production operations</div></button>
    </div>
    <div class="under"><button data-act="undo">↶ undo</button><span></span></div>
  </div>`;
  slot.querySelector(".card").addEventListener("click", e=>{
    const b=e.target.closest("[data-cell]");
    if (b){
      const prev=S.cells[p.k]||null;
      S.cells[p.k]=b.dataset.cell;
      const xp=award(XP_BASE.cell, e);
      undoStack.push({kind:"cell", pk:p.k, prev, xp, n:1});
      vibrate(12); save(); paintSort(); return;
    }
    if (e.target.closest('[data-act="undo"]')){ undo(); paintSort(); }
  });
}

/* ---------- HOME ---------- */
function paintHome(){
  const sc=statusCounts(), done=sc.a+sc.c+sc.r;
  const st=streak();
  document.getElementById("t-streak").textContent=st;
  document.getElementById("t-streak-note").textContent =
    S.days[today()] ? "played today ✅" : (st>0 ? "review 1 formula to keep it! 🔥" : "start one today!");
  document.getElementById("t-xp").textContent=S.xp.toLocaleString("en");
  document.getElementById("t-rank").textContent="XP · "+RANKS[level()-1];
  const nl=nextLevelXp();
  document.getElementById("t-next").textContent= nl? (nl-S.xp).toLocaleString("en")+" XP to next rank" : "max rank!";
  const td=S.days[today()]||{n:0,xp:0};
  document.getElementById("t-today").textContent=td.n;
  document.getElementById("t-today-xp").textContent="+"+td.xp+" XP today";
  document.getElementById("t-total").textContent=(100*done/NFORM).toFixed(done?1:0)+"%";
  document.getElementById("t-total-n").textContent=done.toLocaleString("en")+" / "+NFORM.toLocaleString("en");
  const bar=document.getElementById("mainbar");
  bar.innerHTML='<i class="sa" style="width:'+(100*sc.a/NFORM)+'%"></i>'+
    '<i class="sc" style="width:'+(100*sc.c/NFORM)+'%"></i>'+
    '<i class="sr" style="width:'+(100*sc.r/NFORM)+'%"></i>';
  const sorted=Object.keys(S.cells).length;
  document.getElementById("statchips").innerHTML=
    '<span class="chip" style="color:var(--good)">✓ accepted <b>'+sc.a+"</b></span>"+
    '<span class="chip" style="color:var(--warn)">✎ fixed <b>'+sc.c+"</b></span>"+
    '<span class="chip" style="color:var(--bad)">✗ rejected <b>'+sc.r+"</b></span>"+
    '<span class="chip">🧭 sorted <b>'+sorted+"/"+PAPERS.length+"</b></span>";
  document.getElementById("blitz-best").textContent=S.best;
  document.getElementById("sort-left").textContent=(PAPERS.length-sorted);
  document.getElementById("run-left").textContent=(RUSHP.length-donePapers().length);
}

/* ---------- JOURNAL ---------- */
function paintJournal(){
  const hm=document.getElementById("heatmap"); hm.innerHTML="";
  const now=new Date();
  const dow=(now.getDay()+6)%7;
  const totalDays = 11*7 + dow + 1;
  for (let off=totalDays-1; off>=0; off--){
    const k=dayKey(off), n=(S.days[k]||{}).n||0;
    const lvl = n===0?0 : n<20?1 : n<60?2 : n<150?3 : 4;
    const i=document.createElement("i");
    if (lvl) i.className="l"+lvl;
    i.title=k+" — "+n+" decisions";
    hm.appendChild(i);
  }
  document.getElementById("badges").innerHTML = BADGES.map(b=>
    `<div class="badge ${S.badges[b[0]]?"won":""}"><div class="e">${b[1]}</div>
     <div class="n">${b[2]}</div><div class="d">${S.badges[b[0]]||b[3]}</div></div>`).join("");
  const days=Object.keys(S.days).sort().reverse().slice(0,30);
  document.getElementById("daylog").innerHTML = days.length
    ? days.map(k=>`<li><span>${k}</span><span><b>${S.days[k].n}</b> decisions · +${S.days[k].xp} XP</span></li>`).join("")
    : '<li><span class="mut">nothing yet — play Paper Run!</span></li>';
  const dp=donePapers();
  document.getElementById("donepapers").innerHTML = dp.length
    ? dp.map(p=>`<li><span style="max-width:75%">${escapeHtml(p.t.slice(0,70))}</span><span>✓ ${p.f.length}</span></li>`).join("")
    : '<li><span class="mut">none complete yet</span></li>';
}

/* ---------- PAPERS ---------- */
function paintPapers(){
  document.getElementById("plist-sub").textContent="· "+RUSHP.length+" with formulas";
  document.getElementById("plist").innerHTML = RUSHP.map((p,i)=>{
    const d=S.dec[p.k]||{}; let a=0,c=0,r=0;
    for (const f of p.f){ const s=(d[f[0]]||{}).s; if(s==="a")a++; else if(s==="c")c++; else if(s==="r")r++; }
    const done=a+c+r;
    return `<li data-pi="${i}"><div class="t">${escapeHtml(p.t.slice(0,90))}${p.y?" ("+p.y+")":""}</div>
      <div class="m">${escapeHtml(p.v)} · ${p.c} cites · ${done}/${p.f.length} reviewed ${S.cells[p.k]?"· 🧭 "+S.cells[p.k]:""}</div>
      <div class="pbar"><i style="background:var(--good);width:${100*a/p.f.length}%"></i><i style="background:var(--warn);width:${100*c/p.f.length}%"></i><i style="background:var(--bad);width:${100*r/p.f.length}%"></i></div></li>`;
  }).join("");
  document.getElementById("plist").onclick=(e)=>{
    const li=e.target.closest("li[data-pi]"); if(!li) return;
    runIdx=parseInt(li.dataset.pi,10); go("run");
  };
}

/* ---------- sheets ---------- */
function openSheet(id){ document.getElementById(id).classList.add("on");
  document.getElementById("scrim").classList.add("on"); }
function closeSheets(){ document.querySelectorAll(".sheet").forEach(s=>s.classList.remove("on"));
  document.getElementById("scrim").classList.remove("on"); }

/* ---------- export / import (schema unchanged: game-decisions-1) ---------- */
function buildExport(){
  const fd=[];
  for (const p of PAPERS){
    const d=S.dec[p.k]; if (!d || !Object.keys(d).length) continue;
    fd.push({paper_key:p.k, doi:p.d||null, decisions:p.f.map(f=>{
      const e=d[f[0]]||{};
      const st = e.s==="a"?"accepted" : e.s==="c"?"corrected" : e.s==="r"?"rejected" : "unreviewed";
      return {id:f[0], status:st, note:e.n||null};
    })});
  }
  const cells=Object.keys(S.cells).sort().map(k=>{
    const p=PAPERS.find(x=>x.k===k);
    return {paper_key:k, doi:(p&&p.d)||null, cell:S.cells[k]==="X"?"out_of_scope":S.cells[k]};
  });
  const sc=statusCounts();
  return {schema_version:"game-decisions-1", exported:new Date().toISOString(),
    totals:{reviewed:sc.a+sc.c+sc.r, accepted:sc.a, corrected:sc.c, rejected:sc.r,
      papers_sorted:cells.length, xp:S.xp},
    formula_decisions:fd, paper_cells:cells,
    stats:{xp:S.xp, days:S.days, best_blitz:S.best, badges:S.badges}};
}
function expName(){ return "game_decisions_"+today()+".json"; }
function openExport(){
  const sc=statusCounts();
  document.getElementById("exp-sum").textContent =
    (sc.a+sc.c+sc.r)+" formula decisions ("+sc.a+" ✓ / "+sc.c+" ✎ / "+sc.r+" ✗) · "+
    Object.keys(S.cells).length+" papers sorted";
  openSheet("expsheet");
}
async function shareExport(){
  const j=JSON.stringify(buildExport(),null,1);
  try{
    const file=new File([j], expName(), {type:"application/json"});
    if (navigator.canShare && navigator.canShare({files:[file]})){
      await navigator.share({files:[file], title:expName()}); toast("shared ✅"); return; }
  }catch(e){ if (e.name==="AbortError") return; }
  downloadExport();
}
function downloadExport(){
  const j=JSON.stringify(buildExport(),null,1);
  const a=document.createElement("a");
  a.href=URL.createObjectURL(new Blob([j],{type:"application/json"}));
  a.download=expName(); a.click(); toast("downloaded ⬇");
}
async function copyExport(){
  try{ await navigator.clipboard.writeText(JSON.stringify(buildExport())); toast("copied 📋"); }
  catch(e){ toast("copy failed — use download"); }
}
document.getElementById("imp-file").addEventListener("change", async e=>{
  const f=e.target.files[0]; if(!f) return;
  try{
    const j=JSON.parse(await f.text());
    let merged=0;
    for (const pd of (j.formula_decisions||[])){
      for (const d of (pd.decisions||[])){
        if (d.status && d.status!=="unreviewed"){
          const cur=(S.dec[pd.paper_key]||{})[d.id];
          if (!cur){ (S.dec[pd.paper_key]=S.dec[pd.paper_key]||{})[d.id]=
            {s:d.status[0], n:d.note||null}; merged++; }
        }
      }
    }
    for (const c of (j.paper_cells||[])){
      if (!S.cells[c.paper_key]) { S.cells[c.paper_key]= c.cell==="out_of_scope"?"X":c.cell; merged++; }
    }
    const st=(j.stats||{});
    if (st.xp>S.xp) S.xp=st.xp;
    if (st.best_blitz>S.best) S.best=st.best_blitz;
    for (const k in (st.days||{})){ if(!S.days[k]) S.days[k]=st.days[k]; }
    for (const k in (st.badges||{})){ if(!S.badges[k]) S.badges[k]=st.badges[k]; }
    save(); toast("merged "+merged+" decisions ✅"); paintHome();
  }catch(err){ toast("⚠ import failed: bad JSON"); }
  e.target.value="";
});

/* ---------- boot ---------- */
paintHome();
</script>
</body>
</html>
"""


def main() -> None:
    data = _payload()
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html = (
        _HTML.replace("__DATA__", blob)
        .replace("__NPAPERS__", str(len(data["papers"])))
        .replace("__NFORM__", str(data["n_formulas"]))
        .replace("__LOGO__", _logo_svg())
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, "
          f"{len(data['papers'])} papers, {data['n_formulas']} formulas)")


if __name__ == "__main__":
    main()
