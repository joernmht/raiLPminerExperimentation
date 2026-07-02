"""**Formula Express** — a mobile-first, gamified HITL review game for the corpus.

Generates ONE self-contained HTML file, ``corpus/review/game.html`` (gitignored,
like the rest of ``corpus/review/``), embedding every dossier's formulas. Open it
on a phone (send via ``~/.claude/loops/notify.sh``) or desktop. Three minigames,
all of which produce *real* pipeline decisions:

* **Formula Rush** — swipe/tap accept ✓ / fix ✎ / reject ✗ per formula
  (same semantics as ``review_view``: *fix* stores the corrected LaTeX in ``note``).
* **Blitz** — 60-second accept/reject sprint over the unreviewed pool.
* **Shell Sorter** — classify each paper into the paper's priority cells
  P1–P5 or out-of-scope/off-topic (fills PRISMA ``per_cell_P1_P5``; prunes the
  medical-noise dossiers).

Gamification: XP + railway ranks, daily streaks with a GitHub-style heatmap
(palette validated light+dark), badges, combos, a Journal of everything done.
Progress persists in ``localStorage``; **Export** produces
``game_decisions_<date>.json`` whose ``formula_decisions`` entries are exactly
the per-paper objects ``review_view`` exports (→ ingest into ``corpus/decisions/``),
plus ``paper_cells`` for the P1–P5 screen. Import merges a previous export, so
state can hop devices.

Run:  PYTHONPATH=. python3 -m corpusbuilder.game
"""

from __future__ import annotations

import json
from pathlib import Path

from corpusbuilder.dossier import Dossier
from corpusbuilder.prisma import _RELEVANT

ROOT = Path(__file__).resolve().parent.parent
DOSS = ROOT / "corpus" / "dossiers"
OUT = ROOT / "corpus" / "review" / "game.html"

_METHOD = {"arxiv-tex": "T1", "mathml": "T2", "ocr": "T3", "llm": "LLM", "human": "H"}


def _payload() -> dict:
    doss = [Dossier.load(p) for p in sorted(DOSS.glob("*.json"))]
    doss.sort(key=lambda d: (-(d.source.cited_by_count or 0), d.key))
    papers = []
    for d in doss:
        s = d.source
        papers.append(
            {
                "k": d.key,
                "t": s.title or d.key,
                "v": s.venue or "",
                "y": s.year or 0,
                "d": s.doi or "",
                "c": s.cited_by_count or 0,
                "ot": 0 if (s.title and _RELEVANT.search(s.title)) else 1,
                "f": [
                    [f.id, f.label or "", f.latex, _METHOD.get(f.method.value, "?"), f.page_start or 0]
                    for f in d.formulas
                ],
            }
        )
    return {"papers": papers, "n_formulas": sum(len(p["f"]) for p in papers)}


_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover,user-scalable=no">
<meta name="theme-color" content="#e8590c">
<meta name="apple-mobile-web-app-capable" content="yes">
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 100 100%22><text y=%22.9em%22 font-size=%2290%22>🚂</text></svg>">
<title>Formula Express — corpus review</title>
<script>window.MathJax={tex:{displayMath:[["\\[","\\]"]]},options:{enableMenu:false},startup:{typeset:false}};</script>
<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-chtml.js"
        onerror="window.__mjfail=1"></script>
<style>
:root{
  --page:#f7f5f0;--card:#fdfcf9;--ink:#1c1b18;--ink2:#57544d;--mut:#8d8a82;
  --line:#e6e2d8;--accent:#e8590c;--accent-soft:#fdeadd;
  --ok:#0ca30c;--ok-soft:#e3f4e3;--warn:#b47300;--warn-soft:#fbeecb;
  --bad:#d03b3b;--bad-soft:#fae3e3;
  --g0:#ece9e2;--g1:#7fc476;--g2:#4fae47;--g3:#2b8f2b;--g4:#0f6b12;
  --shadow:0 1px 3px rgba(28,27,24,.08),0 4px 14px rgba(28,27,24,.06);
}
@media (prefers-color-scheme:dark){:root{
  --page:#151412;--card:#23221e;--ink:#f4f2ec;--ink2:#c6c3b8;--mut:#8d8a82;
  --line:#3a3833;--accent:#ff7a33;--accent-soft:#3a2517;
  --ok:#3cb85e;--ok-soft:#1b2f1f;--warn:#fab219;--warn-soft:#33290f;
  --bad:#e66767;--bad-soft:#331a1a;
  --g0:#2c2b27;--g1:#2b7a42;--g2:#31984e;--g3:#3cb85e;--g4:#55d977;
  --shadow:0 1px 3px rgba(0,0,0,.4);
}}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;background:var(--page);color:var(--ink);
  font:16px/1.45 system-ui,-apple-system,"Segoe UI",sans-serif;
  overscroll-behavior-y:contain}
.app{max-width:480px;margin:0 auto;padding:12px 14px calc(24px + env(safe-area-inset-bottom))}
h1{font-size:22px;margin:6px 0 2px}
h2{font-size:17px;margin:18px 0 8px}
.sub{color:var(--ink2);font-size:13px;margin:0 0 14px}
section{display:none}section.on{display:block}
.card{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:14px;box-shadow:var(--shadow)}
.tiles{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.tile{background:var(--card);border:1px solid var(--line);border-radius:16px;
  padding:12px 14px;box-shadow:var(--shadow)}
.tile .big{font-size:26px;font-weight:700;line-height:1.15}
.tile .lbl{color:var(--ink2);font-size:12.5px;margin-top:2px}
.tile .note{color:var(--mut);font-size:11.5px;margin-top:2px}
.bar{height:10px;background:var(--g0);border-radius:6px;overflow:hidden;display:flex;margin-top:8px}
.bar i{display:block;height:100%}
.bar .sa{background:var(--ok)}.bar .sc{background:var(--warn)}.bar .sr{background:var(--bad)}
.chips{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px;font-size:12.5px;color:var(--ink2)}
.chip{background:var(--page);border:1px solid var(--line);border-radius:10px;padding:2px 9px}
.chip b{font-variant-numeric:tabular-nums}
.games{display:flex;flex-direction:column;gap:10px;margin-top:10px}
.game{display:flex;align-items:center;gap:12px;background:var(--card);
  border:1px solid var(--line);border-radius:16px;padding:14px;box-shadow:var(--shadow);
  cursor:pointer;user-select:none}
.game:active{transform:scale(.985)}
.game .ico{font-size:28px}
.game .ttl{font-weight:650}
.game .dsc{color:var(--ink2);font-size:13px}
.game .go{margin-left:auto;color:var(--accent);font-weight:700}
button{font:inherit;cursor:pointer;border:1px solid var(--line);background:var(--card);
  color:var(--ink);border-radius:14px;padding:10px 14px;user-select:none}
button:active{transform:scale(.97)}
.row{display:flex;gap:8px;align-items:center;flex-wrap:wrap}
.top{display:flex;align-items:center;gap:10px;margin-bottom:10px}
.top .back{border:none;background:none;font-size:15px;color:var(--accent);
  font-weight:650;padding:8px 8px 8px 0}
.top .info{margin-left:auto;color:var(--ink2);font-size:13px;font-variant-numeric:tabular-nums}
.combo{margin-left:8px;color:var(--accent);font-weight:800;font-size:14px}
/* formula card */
.fcard{position:relative;transition:transform .18s ease,opacity .18s ease}
.fcard.gone-r{transform:translateX(120%) rotate(8deg);opacity:0}
.fcard.gone-l{transform:translateX(-120%) rotate(-8deg);opacity:0}
.fmeta{color:var(--ink2);font-size:13px;margin-bottom:6px}
.fmeta b{color:var(--ink)}
.render{background:#fff;color:#000;border-radius:12px;border:1px solid var(--line);
  padding:14px 12px;overflow-x:auto;min-height:64px;font-size:15px}
@media (prefers-color-scheme:dark){.render{background:#fdfcf9}}
.render .err{color:#8d8a82;font:12.5px ui-monospace,Menlo,monospace;white-space:pre-wrap}
details.raw{margin-top:8px}
details.raw summary{color:var(--mut);font-size:12.5px;cursor:pointer}
.tex{white-space:pre-wrap;background:var(--page);border:1px solid var(--line);border-radius:10px;
  padding:8px 10px;font:12.5px/1.5 ui-monospace,Menlo,monospace;color:var(--ink2);
  overflow-x:auto;margin-top:6px}
.acts{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin-top:12px}
.acts button{min-height:56px;font-weight:700;font-size:15.5px;border-radius:16px}
.b-rej{background:var(--bad-soft);border-color:var(--bad);color:var(--bad)}
.b-fix{background:var(--warn-soft);border-color:var(--warn);color:var(--warn)}
.b-acc{background:var(--ok-soft);border-color:var(--ok);color:var(--ok)}
.under{display:flex;justify-content:space-between;margin-top:10px}
.under button{border:none;background:none;color:var(--mut);font-size:13.5px;padding:6px}
/* sorter */
.cells{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.cells button{min-height:58px;text-align:left;padding:9px 12px;border-radius:16px}
.cells .p{font-weight:750;font-size:15px}
.cells .d{color:var(--ink2);font-size:12px;margin-top:1px}
.cells .x{grid-column:1/-1;background:var(--bad-soft);border-color:var(--bad)}
.hint-ot{display:inline-block;background:var(--bad-soft);color:var(--bad);border-radius:10px;
  padding:2px 9px;font-size:12.5px;font-weight:650;margin-top:6px}
/* blitz */
.timer{font-size:34px;font-weight:800;font-variant-numeric:tabular-nums;text-align:center}
.timer.low{color:var(--bad)}
/* heatmap */
.hm{display:grid;grid-auto-flow:column;grid-template-rows:repeat(7,12px);gap:3px;
  justify-content:start;margin-top:8px}
.hm i{width:12px;height:12px;border-radius:3px;background:var(--g0)}
.hm i.l1{background:var(--g1)}.hm i.l2{background:var(--g2)}
.hm i.l3{background:var(--g3)}.hm i.l4{background:var(--g4)}
.hmleg{display:flex;align-items:center;gap:4px;color:var(--mut);font-size:11.5px;margin-top:6px}
.hmleg i{width:11px;height:11px;border-radius:3px;background:var(--g0);display:inline-block}
/* journal */
.days li{display:flex;justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--line);
  font-size:14px}
.days{list-style:none;margin:6px 0;padding:0}
.badges{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-top:8px}
.badge{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:10px 8px;
  text-align:center;box-shadow:var(--shadow)}
.badge .e{font-size:26px;filter:grayscale(1);opacity:.35}
.badge.won .e{filter:none;opacity:1}
.badge .n{font-size:11.5px;font-weight:650;margin-top:3px}
.badge .d{font-size:10.5px;color:var(--mut)}
/* paper list */
.plist{list-style:none;margin:8px 0;padding:0}
.plist li{background:var(--card);border:1px solid var(--line);border-radius:14px;
  padding:10px 12px;margin-bottom:8px;box-shadow:var(--shadow)}
.plist .t{font-size:14px;font-weight:600}
.plist .m{color:var(--ink2);font-size:12px;margin-top:2px}
.pbar{height:6px;background:var(--g0);border-radius:4px;overflow:hidden;display:flex;margin-top:7px}
.pbar i{display:block;height:100%}
/* sheets & toast */
.sheet{position:fixed;inset:auto 0 0 0;background:var(--card);border-radius:20px 20px 0 0;
  border:1px solid var(--line);box-shadow:0 -8px 30px rgba(0,0,0,.25);padding:16px 16px
  calc(16px + env(safe-area-inset-bottom));transform:translateY(105%);transition:transform .22s ease;
  z-index:40;max-height:86vh;overflow-y:auto;max-width:480px;margin:0 auto}
.sheet.on{transform:none}
.scrim{position:fixed;inset:0;background:rgba(0,0,0,.35);opacity:0;pointer-events:none;
  transition:opacity .2s;z-index:30}
.scrim.on{opacity:1;pointer-events:auto}
textarea{width:100%;min-height:110px;background:var(--page);color:var(--ink);
  border:1px solid var(--line);border-radius:12px;font:13px/1.5 ui-monospace,Menlo,monospace;
  padding:10px}
#toast{position:fixed;left:50%;bottom:calc(86px + env(safe-area-inset-bottom));transform:translateX(-50%) translateY(20px);
  background:var(--ink);color:var(--page);border-radius:20px;padding:8px 18px;font-size:14.5px;
  font-weight:650;opacity:0;transition:.25s;z-index:50;pointer-events:none;white-space:nowrap}
#toast.on{opacity:1;transform:translateX(-50%)}
#xpfly{position:fixed;pointer-events:none;font-weight:800;color:var(--accent);z-index:50;
  opacity:0;font-size:17px;transition:transform .8s ease-out,opacity .8s}
.confetti{position:fixed;top:-24px;font-size:22px;z-index:60;pointer-events:none;
  animation:fall 1.6s ease-in forwards}
@keyframes fall{to{transform:translateY(105vh) rotate(540deg);opacity:.2}}
.big-accent{color:var(--accent)}
a{color:var(--accent)}
.exp-btns{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}
.exp-btns .wide{grid-column:1/-1;background:var(--accent);border-color:var(--accent);color:#fff;
  font-weight:700;min-height:52px}
.mut{color:var(--mut);font-size:12.5px}
.streakflame{font-size:15px}
</style>
</head>
<body>
<div class="app">

<!-- ============ HOME ============ -->
<section id="home" class="on">
  <h1>🚂 Formula Express</h1>
  <p class="sub">Paper 1 corpus review · __NPAPERS__ papers · __NFORM__ formulas</p>
  <div class="tiles">
    <div class="tile"><div class="big" id="t-streak">–</div><div class="lbl">🔥 day streak</div><div class="note" id="t-streak-note"></div></div>
    <div class="tile"><div class="big" id="t-xp">0</div><div class="lbl" id="t-rank">XP</div><div class="note" id="t-next"></div></div>
    <div class="tile"><div class="big" id="t-today">0</div><div class="lbl">decisions today</div><div class="note" id="t-today-xp"></div></div>
    <div class="tile"><div class="big" id="t-total">0%</div><div class="lbl">formulas reviewed</div><div class="note" id="t-total-n"></div></div>
  </div>
  <div class="card" style="margin-top:10px">
    <div style="font-weight:650;font-size:14px">Overall progress</div>
    <div class="bar" id="mainbar" role="img" aria-label="review progress"></div>
    <div class="chips" id="statchips"></div>
  </div>
  <div class="games">
    <div class="game" data-go="rush"><span class="ico">⚡</span>
      <div><div class="ttl">Formula Rush</div><div class="dsc">accept · fix · reject, one by one</div></div><span class="go">›</span></div>
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

<!-- ============ RUSH / BLITZ shared card ============ -->
<section id="rush">
  <div class="top"><button class="back" data-go="home">‹ Home</button>
    <span class="combo" id="combo"></span>
    <span class="info" id="rush-info"></span></div>
  <div id="rush-slot"></div>
</section>

<section id="blitz">
  <div class="top"><button class="back" data-go="home">‹ Home</button>
    <span class="info" id="blitz-score"></span></div>
  <div class="timer" id="blitz-timer">60</div>
  <div id="blitz-slot"></div>
  <div id="blitz-start" class="card" style="text-align:center;margin-top:14px">
    <div style="font-size:40px">⏱️</div>
    <p>60 seconds. Accept or reject as many formulas as you can.<br>
    <span class="mut">Unsure? Skip — no penalty.</span></p>
    <button class="b-acc" style="min-height:52px;width:100%;font-weight:700"
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
    <div style="font-weight:650;font-size:14px">Activity — last 12 weeks</div>
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

<!-- editor sheet -->
<div class="scrim" id="scrim" onclick="closeSheets()"></div>
<div class="sheet" id="fixsheet">
  <h2 style="margin-top:0">✎ Fix LaTeX</h2>
  <textarea id="fix-ta" spellcheck="false" autocapitalize="off"></textarea>
  <div class="render" id="fix-prev" style="margin-top:10px"></div>
  <div class="row" style="margin-top:12px">
    <button onclick="closeSheets()">Cancel</button>
    <button class="b-warn b-fix" style="flex:1;font-weight:700;min-height:48px" onclick="saveFix()">Save fix ✎ (+25 XP)</button>
  </div>
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
const RAW = JSON.parse(document.getElementById("corpus-data").textContent);
const PAPERS = RAW.papers;                    // sorted by citations desc
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

/* ---------- state ---------- */
const LSK = "fx:state:v1";
let S = load();
function load(){
  try{ const s = JSON.parse(localStorage.getItem(LSK) || "null"); if (s) return s; }catch(e){}
  const s = {dec:{}, cells:{}, xp:0, days:{}, best:0, badges:{}};
  // migrate decisions saved by the older review_view pages, if same browser
  try{
    for (let i=0;i<localStorage.length;i++){
      const k = localStorage.key(i);
      if (k && k.startsWith("review:")){
        const pk = k.slice(7), o = JSON.parse(localStorage.getItem(k)||"{}");
        for (const fid in o){ if (o[fid].status){
          (s.dec[pk] = s.dec[pk] || {})[fid] =
            {s:o[fid].status[0], n:o[fid].note||null}; }}
      }
    }
  }catch(e){}
  return s;
}
function save(){ try{ localStorage.setItem(LSK, JSON.stringify(S)); }catch(e){ toast("⚠ could not save — export!"); } }
function today(){ const d = new Date(); return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); }
function dayKey(off){ const d = new Date(); d.setDate(d.getDate()-off);
  return d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0"); }

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
  if (!S.days[dayKey(0)]) off = 1;              // today not yet played → count up to yesterday
  while (S.days[dayKey(off)] ) { n++; off++; }
  return n;
}
function level(){ return Math.min(RANKS.length, Math.floor(Math.sqrt(S.xp/150)) + 1); }
function nextLevelXp(){ const l=level(); return l>=RANKS.length ? null : 150*l*l; }

/* ---------- navigation ---------- */
function go(id){
  document.querySelectorAll("section").forEach(s=>s.classList.toggle("on", s.id===id));
  window.scrollTo(0,0);
  if (id==="home") paintHome();
  if (id==="rush") paintRush();
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
function award(base, ev){
  const now=Date.now();
  combo = (now-lastDecT<20000) ? combo+1 : 1; lastDecT=now;
  const mult = Math.min(4, 1+Math.floor(combo/8));
  const xp = base*mult;
  S.xp += xp;
  const d = S.days[today()] = S.days[today()] || {n:0,xp:0};
  d.n++; d.xp+=xp;
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

/* ---------- MathJax rendering ---------- */
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

/* ---------- formula card (shared by rush & blitz) ---------- */
function fmtPaper(p){ return (p.t.length>90 ? p.t.slice(0,88)+"…" : p.t) + (p.y? " ("+p.y+")":""); }
function cardHTML(p,f,mode){
  const pg = f[4] ? " · p."+f[4] : "";
  return `<div class="card fcard">
    <div class="fmeta"><b>${escapeHtml(fmtPaper(p))}</b><br>
      <span class="chip">${f[0]}</span> ${f[1]?'<span class="chip">'+escapeHtml(f[1])+"</span>":""}
      <span class="chip">${f[3]}</span><span class="mut">${pg}</span></div>
    <div class="render fr"></div>
    <details class="raw"><summary>raw LaTeX</summary><div class="tex"></div></details>
    <div class="acts">
      <button class="b-rej" data-act="r">✗<br>reject</button>
      ${mode==="blitz"
        ? '<button data-act="skip">↷<br>skip</button>'
        : '<button class="b-fix" data-act="c">✎<br>fix</button>'}
      <button class="b-acc" data-act="a">✓<br>accept</button>
    </div>
    <div class="under">
      <button data-act="undo">↶ undo</button>
      <span class="mut" style="align-self:center">swipe → accept · ← reject</span>
      ${mode==="rush"?'<button data-act="skippaper">skip paper ›</button>':"<span></span>"}
    </div></div>`;
}
function escapeHtml(s){ return s.replace(/[&<>"]/g, c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function mountCard(slot, p, f, mode, onAct){
  slot.innerHTML = cardHTML(p,f,mode);
  const card = slot.querySelector(".fcard");
  card.querySelector(".tex").textContent = f[2];
  renderMath(card.querySelector(".fr"), f[2]);
  card.addEventListener("click", e=>{
    const b = e.target.closest("[data-act]"); if (b) onAct(b.dataset.act, e);
  });
  // swipe
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
  card.addEventListener("touchend", e=>{
    if (!swiping) return; swiping=false;
    if (dx>90) onAct("a", {clientX:innerWidth-60, clientY:innerHeight/2});
    else if (dx<-90) onAct("r", {clientX:60, clientY:innerHeight/2});
    else card.style.transform="";
  });
  return card;
}

/* ---------- decisions core ---------- */
const XP_BASE = {a:10, r:10, c:25, cell:15, blitz:8};
let undoStack=[];
function decide(p, f, st, note, ev, mode){
  const pk=p.k, fid=f[0];
  const prev = (S.dec[pk]||{})[fid] || null;
  (S.dec[pk] = S.dec[pk] || {})[fid] = {s:st, n:note||null};
  const xp = award(mode==="blitz" ? XP_BASE.blitz : XP_BASE[st], ev);
  undoStack.push({kind:"dec", pk, fid, prev, xp});
  if (undoStack.length>25) undoStack.shift();
  vibrate(12);
  if (Math.random()<0.34) toast(PRAISE[Math.floor(Math.random()*PRAISE.length)]);
  if (paperProgress(p)===p.f.length){ confetti(26); toast("📄 Paper complete! "+fmtPaper(p)); }
  save();
}
function undo(){
  const u=undoStack.pop(); if(!u){ toast("nothing to undo"); return; }
  if (u.kind==="dec"){
    if (u.prev) S.dec[u.pk][u.fid]=u.prev; else delete S.dec[u.pk][u.fid];
  } else if (u.kind==="cell"){
    if (u.prev) S.cells[u.pk]=u.prev; else delete S.cells[u.pk];
  }
  S.xp=Math.max(0,S.xp-u.xp);
  const d=S.days[today()]; if(d){ d.n=Math.max(0,d.n-1); d.xp=Math.max(0,d.xp-u.xp); }
  combo=Math.max(0,combo-1); save(); toast("↶ undone");
}

/* ---------- RUSH ---------- */
let rushCursor=0, fixCtx=null;
function nextRush(){
  for (let i=0;i<RUSHP.length;i++){
    const p=RUSHP[(rushCursor+i)%RUSHP.length];
    const d=S.dec[p.k]||{};
    for (const f of p.f) if (!d[f[0]]) { rushCursor=(rushCursor+i)%RUSHP.length; return {p,f}; }
  }
  return null;
}
function paintRush(){
  const slot=document.getElementById("rush-slot");
  const nx=nextRush();
  const done=decCount();
  document.getElementById("rush-info").textContent=done+"/"+NFORM;
  if (!nx){ slot.innerHTML='<div class="card" style="text-align:center"><div style="font-size:44px">🏆</div><p>Every formula reviewed. Legendary.</p></div>'; confetti(40); return; }
  const {p,f}=nx;
  mountCard(slot, p, f, "rush", (act,ev)=>{
    const card=slot.querySelector(".fcard");
    if (act==="a"||act==="r"){
      decide(p,f,act,null,ev,"rush");
      card.classList.add(act==="a"?"gone-r":"gone-l");
      setTimeout(paintRush,140);
    } else if (act==="c"){
      fixCtx={p,f,from:"rush"};
      document.getElementById("fix-ta").value=f[2];
      renderMath(document.getElementById("fix-prev"), f[2]);
      openSheet("fixsheet");
    } else if (act==="undo"){ undo(); paintRush(); }
    else if (act==="skippaper"){ rushCursor=(rushCursor+1)%RUSHP.length; paintRush(); }
  });
}
let fixDeb;
document.getElementById("fix-ta").addEventListener("input", ()=>{
  clearTimeout(fixDeb);
  fixDeb=setTimeout(()=>renderMath(document.getElementById("fix-prev"),
    document.getElementById("fix-ta").value), 350);
});
function saveFix(){
  if (!fixCtx) return;
  const v=document.getElementById("fix-ta").value.trim();
  decide(fixCtx.p, fixCtx.f, "c", v, null, "rush");
  closeSheets(); fixCtx=null; paintRush();
}

/* ---------- BLITZ ---------- */
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
  mountCard(slot, nx.p, nx.f, "blitz", (act,ev)=>{
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
    '<button class="b-acc" style="min-height:52px;width:100%;font-weight:700" onclick="startBlitz()">Again!</button></div>';
  document.getElementById("blitz-timer").textContent="60";
  document.getElementById("blitz-timer").classList.remove("low");
}

/* ---------- SORTER ---------- */
function nextSort(){ return PAPERS.find(p=>!S.cells[p.k]) || null; }
function paintSort(){
  const slot=document.getElementById("sort-slot");
  const left=PAPERS.filter(p=>!S.cells[p.k]).length;
  document.getElementById("sort-info").textContent=(PAPERS.length-left)+"/"+PAPERS.length+" sorted";
  const p=nextSort();
  if (!p){ slot.innerHTML='<div class="card" style="text-align:center"><div style="font-size:44px">🗺️</div><p>Every paper filed. Cartographer!</p></div>'; return; }
  const cells = CELLS.map(c=>
    `<button data-cell="${c[0]}"><div class="p">${c[1]} ${c[0]}</div><div class="d">${c[2]}</div></button>`).join("");
  slot.innerHTML=`<div class="card">
    <div class="fmeta"><b>${escapeHtml(p.t)}</b><br>
      <span class="mut">${escapeHtml(p.v)}${p.y?" · "+p.y:""} · ${p.c} citations · ${p.f.length} formulas</span>
      ${p.d?` · <a href="https://doi.org/${encodeURIComponent(p.d)}" target="_blank" rel="noopener">DOI</a>`:""}
      ${p.ot?'<br><span class="hint-ot">⚠ topical screen: looks off-topic</span>':""}</div>
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
      undoStack.push({kind:"cell", pk:p.k, prev, xp});
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
    '<span class="chip" style="color:var(--ok)">✓ accepted <b>'+sc.a+"</b></span>"+
    '<span class="chip" style="color:var(--warn)">✎ fixed <b>'+sc.c+"</b></span>"+
    '<span class="chip" style="color:var(--bad)">✗ rejected <b>'+sc.r+"</b></span>"+
    '<span class="chip">🧭 sorted <b>'+sorted+"/"+PAPERS.length+"</b></span>";
  document.getElementById("blitz-best").textContent=S.best;
  document.getElementById("sort-left").textContent=(PAPERS.length-sorted);
}

/* ---------- JOURNAL ---------- */
function paintJournal(){
  // heatmap: 12 weeks, columns = weeks, rows = Mon..Sun, today in last column
  const hm=document.getElementById("heatmap"); hm.innerHTML="";
  const now=new Date();
  const dow=(now.getDay()+6)%7;                 // 0 = Monday
  const cells=[];
  const totalDays = 11*7 + dow + 1;
  for (let off=totalDays-1; off>=0; off--){
    const k=dayKey(off), n=(S.days[k]||{}).n||0;
    const lvl = n===0?0 : n<20?1 : n<60?2 : n<150?3 : 4;
    cells.push([k,n,lvl]);
  }
  for (const [k,n,lvl] of cells){
    const i=document.createElement("i");
    if (lvl) i.className="l"+lvl;
    i.title=k+" — "+n+" decisions";
    hm.appendChild(i);
  }
  // badges
  document.getElementById("badges").innerHTML = BADGES.map(b=>
    `<div class="badge ${S.badges[b[0]]?"won":""}"><div class="e">${b[1]}</div>
     <div class="n">${b[2]}</div><div class="d">${S.badges[b[0]]||b[3]}</div></div>`).join("");
  // day log
  const days=Object.keys(S.days).sort().reverse().slice(0,30);
  document.getElementById("daylog").innerHTML = days.length
    ? days.map(k=>`<li><span>${k}</span><span><b>${S.days[k].n}</b> decisions · +${S.days[k].xp} XP</span></li>`).join("")
    : '<li><span class="mut">nothing yet — play Formula Rush!</span></li>';
  // done papers
  const dp=donePapers();
  document.getElementById("donepapers").innerHTML = dp.length
    ? dp.map(p=>`<li><span style="max-width:75%">${escapeHtml(fmtPaper(p))}</span><span>✓ ${p.f.length}</span></li>`).join("")
    : '<li><span class="mut">none complete yet</span></li>';
}

/* ---------- PAPERS ---------- */
function paintPapers(){
  document.getElementById("plist-sub").textContent="· "+RUSHP.length+" with formulas";
  document.getElementById("plist").innerHTML = RUSHP.map((p,i)=>{
    const d=S.dec[p.k]||{}; let a=0,c=0,r=0;
    for (const f of p.f){ const s=(d[f[0]]||{}).s; if(s==="a")a++; else if(s==="c")c++; else if(s==="r")r++; }
    const done=a+c+r;
    return `<li data-pi="${i}"><div class="t">${escapeHtml(fmtPaper(p))}</div>
      <div class="m">${escapeHtml(p.v)} · ${p.c} cites · ${done}/${p.f.length} reviewed ${S.cells[p.k]?"· 🧭 "+S.cells[p.k]:""}</div>
      <div class="pbar"><i class="sa" style="background:var(--ok);width:${100*a/p.f.length}%"></i><i style="background:var(--warn);width:${100*c/p.f.length}%"></i><i style="background:var(--bad);width:${100*r/p.f.length}%"></i></div></li>`;
  }).join("");
  document.getElementById("plist").onclick=(e)=>{
    const li=e.target.closest("li[data-pi]"); if(!li) return;
    rushCursor=parseInt(li.dataset.pi,10); go("rush");
  };
}

/* ---------- sheets ---------- */
function openSheet(id){ document.getElementById(id).classList.add("on");
  document.getElementById("scrim").classList.add("on"); }
function closeSheets(){ document.querySelectorAll(".sheet").forEach(s=>s.classList.remove("on"));
  document.getElementById("scrim").classList.remove("on"); }

/* ---------- export / import ---------- */
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
    )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"wrote {OUT} ({OUT.stat().st_size / 1e6:.1f} MB, "
          f"{len(data['papers'])} papers, {data['n_formulas']} formulas)")


if __name__ == "__main__":
    main()
