"""Generate a static HTML **review view** of the mined dossiers for HITL formula review.

Reads ``corpus/dossiers/*.json`` and writes a self-contained site under
``corpus/review/``:

* ``index.html`` — one row per paper (title, venue/year, formula counts, DOI link,
  link to the per-paper review page).
* ``papers/<key>.html`` — full per-paper review: source metadata + DOI link, and
  every formula rendered with MathJax *and* shown as raw LaTeX, with
  accept / correct / reject controls. Decisions persist in the browser
  (localStorage) and export to a JSON the corpus pipeline can ingest later.

Run:  PYTHONPATH=. python3 -m corpusbuilder.review_view
"""

from __future__ import annotations

import html
import json
from pathlib import Path

from corpusbuilder.dossier import Dossier

ROOT = Path(__file__).resolve().parent.parent
DOSS = ROOT / "corpus" / "dossiers"
OUT = ROOT / "corpus" / "review"

_MATHJAX = (
    '<script>window.MathJax={tex:{inlineMath:[["$","$"]],'
    'displayMath:[["$$","$$"]]},svg:{fontCache:"global"}};</script>'
    '<script async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>'
)

_CSS = """
:root{--bg:#0f1115;--card:#1a1d24;--fg:#e6e6e6;--mut:#9aa4b2;--acc:#4da3ff;
--ok:#3fb950;--warn:#d29922;--bad:#f85149;--line:#2a2f3a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 system-ui,Segoe UI,Roboto,sans-serif}
a{color:var(--acc)}header{padding:18px 24px;border-bottom:1px solid var(--line)}
.wrap{max-width:1080px;margin:0 auto;padding:24px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 10px;
border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mut);font-weight:600}
tr:hover td{background:#161922}
.badge{display:inline-block;padding:1px 7px;border-radius:10px;font-size:12px;
background:#222733;color:var(--mut);border:1px solid var(--line)}
.meta{color:var(--mut);font-size:13px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;
padding:14px 16px;margin:14px 0}
.eqhead{display:flex;justify-content:space-between;align-items:center;gap:10px;
flex-wrap:wrap}
.tex{white-space:pre-wrap;background:#11141b;border:1px solid var(--line);
border-radius:6px;padding:8px 10px;font:13px/1.45 ui-monospace,Menlo,monospace;
color:#cbd5e1;margin:8px 0;overflow-x:auto}
.render{background:#fff;color:#000;border-radius:6px;padding:8px 12px;overflow-x:auto}
button{cursor:pointer;border:1px solid var(--line);background:#222733;color:var(--fg);
border-radius:6px;padding:5px 11px;font-size:13px}
button:hover{border-color:var(--acc)}
.st-accepted{outline:2px solid var(--ok)}.st-rejected{outline:2px solid var(--bad);opacity:.6}
.st-corrected{outline:2px solid var(--warn)}
.bar{position:sticky;top:0;background:var(--bg);padding:10px 0;border-bottom:1px solid var(--line);
z-index:5;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
textarea{width:100%;min-height:56px;background:#11141b;color:#cbd5e1;border:1px solid var(--line);
border-radius:6px;font:13px ui-monospace,monospace;padding:6px}
.count{font-variant-numeric:tabular-nums}
"""


def _doi_link(s) -> str:
    if s.doi:
        return f'<a href="https://doi.org/{html.escape(s.doi)}" target="_blank" rel="noopener">{html.escape(s.doi)}</a>'
    if s.landing_url:
        return f'<a href="{html.escape(s.landing_url)}" target="_blank" rel="noopener">link</a>'
    if s.arxiv_id:
        return f'<a href="https://arxiv.org/abs/{html.escape(s.arxiv_id)}" target="_blank" rel="noopener">arXiv:{html.escape(s.arxiv_id)}</a>'
    return "—"


def _paper_page(d: Dossier) -> str:
    s = d.source
    data = {
        "key": d.key,
        "title": s.title,
        "doi": s.doi,
        "formulas": [
            {
                "id": f.id,
                "label": f.label,
                "latex": f.latex,
                "method": f.method.value,
                "status": f.status.value,
                "page_start": f.page_start,
                "page_end": f.page_end,
            }
            for f in d.formulas
        ],
    }
    meta_rows = "".join(
        f"<tr><th>{k}</th><td>{v}</td></tr>"
        for k, v in [
            ("Authors", html.escape(", ".join(s.authors)) if s.authors else "—"),
            ("Venue", html.escape(s.venue or "—")),
            ("Year", s.year or "—"),
            ("Publisher", html.escape(s.publisher or "—")),
            ("DOI", _doi_link(s)),
            ("Entitlement", html.escape(s.entitlement or "—")),
            (
                "Cited by (OpenAlex / Scopus)",
                f"{s.cited_by_count or '—'} / {s.scopus_cited_by_count or '—'}",
            ),
            ("Formulas", len(d.formulas)),
        ]
    )
    eqs = []
    for f in d.formulas:
        pg = (
            f" · p.{f.page_start}"
            + (f"–{f.page_end}" if f.page_end and f.page_end != f.page_start else "")
            if f.page_start
            else ""
        )
        eqs.append(f"""
<div class="card eq" id="eq-card-{html.escape(f.id)}" data-id="{html.escape(f.id)}">
  <div class="eqhead">
    <div><b>{html.escape(f.id)}</b> {('<span class="badge">' + html.escape(f.label) + "</span>") if f.label else ""}
      <span class="badge">{f.method.value}</span><span class="meta">{pg}</span></div>
    <div>
      <button onclick="setStatus('{html.escape(f.id)}','accepted')">✓ accept</button>
      <button onclick="setStatus('{html.escape(f.id)}','corrected')">✎ correct</button>
      <button onclick="setStatus('{html.escape(f.id)}','rejected')">✗ reject</button>
    </div>
  </div>
  <div class="render">$$ {html.escape(f.latex)} $$</div>
  <div class="tex">{html.escape(f.latex)}</div>
  <textarea placeholder="corrected LaTeX (only if you hit correct) / note" data-note="{html.escape(f.id)}"></textarea>
</div>""")
    body = "\n".join(eqs) if eqs else '<p class="meta">No formulas extracted for this paper.</p>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Review · {html.escape(s.title)}</title>{_MATHJAX}<style>{_CSS}</style></head><body>
<header><a href="../index.html">← all papers</a></header>
<div class="wrap">
<h1>{html.escape(s.title)}</h1>
<table>{meta_rows}</table>
<div class="bar">
  <b class="count" id="progress"></b>
  <button onclick="exportDecisions()">⤓ export decisions JSON</button>
  <span class="meta">decisions auto-save in this browser</span>
</div>
{body}
</div>
<script>
const DATA = {json.dumps(data, ensure_ascii=False)};
const LSK = "review:"+DATA.key;
function load(){{return JSON.parse(localStorage.getItem(LSK)||"{{}}");}}
function save(o){{localStorage.setItem(LSK,JSON.stringify(o));}}
function setStatus(id,st){{
  const o=load(); o[id]=o[id]||{{}}; o[id].status=st;
  const ta=document.querySelector('[data-note="'+id+'"]');
  if(ta&&ta.value.trim()) o[id].note=ta.value.trim();
  save(o); paint();
}}
function paint(){{
  const o=load(); let acc=0,rej=0,cor=0;
  DATA.formulas.forEach(f=>{{
    const card=document.getElementById("eq-card-"+f.id);
    card.classList.remove("st-accepted","st-rejected","st-corrected");
    const st=(o[f.id]||{{}}).status;
    if(st){{card.classList.add("st-"+st); if(st=="accepted")acc++; if(st=="rejected")rej++; if(st=="corrected")cor++;}}
    const ta=card.querySelector("textarea"); if((o[f.id]||{{}}).note&&!ta.value) ta.value=o[f.id].note;
  }});
  document.getElementById("progress").textContent =
    `${{acc+rej+cor}}/${{DATA.formulas.length}} reviewed  ·  ✓${{acc}} ✎${{cor}} ✗${{rej}}`;
}}
function exportDecisions(){{
  const o=load();
  const out={{paper_key:DATA.key,doi:DATA.doi,decisions:DATA.formulas.map(f=>({{
    id:f.id, status:(o[f.id]||{{}}).status||"unreviewed", note:(o[f.id]||{{}}).note||null
  }}))}};
  const blob=new Blob([JSON.stringify(out,null,2)],{{type:"application/json"}});
  const a=document.createElement("a"); a.href=URL.createObjectURL(blob);
  a.download="decisions_"+DATA.key+".json"; a.click();
}}
paint();
</script></body></html>"""


def _index_page(doss: list[Dossier]) -> str:
    rows = []
    for d in doss:
        s = d.source
        nf = len(d.formulas)
        rows.append(
            f'<tr><td><a href="papers/{html.escape(d.key)}.html">{html.escape(s.title)}</a>'
            f'<div class="meta">{html.escape(s.venue or "")} {s.year or ""}</div></td>'
            f'<td class="count">{s.cited_by_count or "—"}</td>'
            f'<td class="count">{nf}</td>'
            f"<td>{_doi_link(s)}</td></tr>"
        )
    total_f = sum(len(d.formulas) for d in doss)
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Corpus review</title><style>{_CSS}</style></head><body>
<header><b>Paper 1 corpus — formula review</b></header>
<div class="wrap">
<p class="meta">{len(doss)} papers · {total_f} formulas extracted. Click a paper to review its formulas.</p>
<table><thead><tr><th>Paper</th><th>Cites</th><th>Formulas</th><th>DOI</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
</div></body></html>"""


def main() -> None:
    doss = [Dossier.load(p) for p in sorted(DOSS.glob("*.json"))]
    doss.sort(key=lambda d: d.source.cited_by_count or 0, reverse=True)
    (OUT / "papers").mkdir(parents=True, exist_ok=True)
    for d in doss:
        (OUT / "papers" / f"{d.key}.html").write_text(_paper_page(d), encoding="utf-8")
    (OUT / "index.html").write_text(_index_page(doss), encoding="utf-8")
    print(
        f"wrote {OUT / 'index.html'} + {len(doss)} paper pages "
        f"({sum(len(d.formulas) for d in doss)} formulas)"
    )


if __name__ == "__main__":
    main()
