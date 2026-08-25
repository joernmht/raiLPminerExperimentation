"""Slide figures: the LP-mining pipeline traced end to end on two examples.

Figure A (``fig_example_real``) walks ONE real corpus paper —
10.1016/j.trb.2017.06.018, "Train timetabling by skip-stop planning in
highly congested lines" (Jiang, Cacchiani & Toth 2017) — through every
pipeline stage, showing the actual artifact each stage produced: the raw
MathML->LaTeX extraction damage, the LLM triage verdicts, the symbol table,
the ``%@`` declaration sidecar, an LLM repair pair, the assembled canonical
model, the real typed schema graph, and the WL/fingerprint relations.
Everything shown is read live from the corpus artifacts; nothing is typed in.

Figure B (``fig_example_toy``) runs the same stage sequence on a fully
legible toy model (two trains, one single-track section). The toy canonical
document is ACTUALLY ingested via ``lp2graph.mining.ingest.ingest_latex``
at render time and the script refuses to draw unless ``result.ok``; the toy
graph panel is the real ``schema_nx`` output of that ingest, hand-positioned.

Style matches ``corpusbuilder.talkpack``: chair CD palette, white field,
base font >= 13, 300 dpi PNG + SVG twins with the date stripped so a rerun
over unchanged inputs is byte-identical.

Run:  PYTHONPATH=. python3 scripts/example_figures.py
Out:  corpus/talkpack/figures/fig_example_{real,toy}.{png,svg}
"""

from __future__ import annotations

# ruff: noqa: I001 — the ``railpminer._lp2graph`` import below is a side effect
# (it puts the sibling lp2graph checkout on ``sys.path``) and must run before
# the ``lp2graph`` imports; import sorting would break that (house pattern of
# corpusbuilder/promote.py).

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch

from railpminer import _lp2graph  # noqa: F401  (must precede lp2graph imports)

from lp2graph import load
from lp2graph.mining.ingest import ingest_latex
from lp2graph.mining.isomorphism.report import schema_nx

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
OUTDIR = CORPUS / "talkpack" / "figures"
KEY = "10.1016_j.trb.2017.06.018"

# ---------------------------------------------------------------------------
# Chair CD palette (mirrors corpusbuilder.talkpack).
# ---------------------------------------------------------------------------
CD = {
    "tuerkis": "#0A777F",
    "orange": "#C85000",
    "midblau": "#2F57B2",
    "rot": "#D20F41",
    "violett": "#7369BE",
    "gelb": "#FFC700",
    "dunkelblau": "#001450",
}
INK = "#14202b"
MUTED = "#5f6b76"
GRID = "#d9dee3"
MONO = "DejaVu Sans Mono"

#: node colour per schema-graph class, shared by both figures' graph panels.
CLS_COLOR = {
    "objective": CD["orange"],
    "constraint": CD["midblau"],
    "variable": CD["tuerkis"],
    "parameter": CD["violett"],
    "index": CD["gelb"],
    "operator": "#8a949e",
}
CLS_ORDER = ["objective", "constraint", "variable", "parameter", "index", "operator"]

TAG_DET = "deterministic"
TAG_LLM = "LLM-assisted, parser-gated"

#: characters per line inside a card, by text kind (measured for 6.2in cards).
MONO_W = 50
BODY_W = 57
BOLD_W = 52


def _style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.family": "DejaVu Sans",
            "font.size": 14,
            "text.color": INK,
            "svg.hashsalt": "example-figures",
            "svg.fonttype": "none",
        }
    )


def _save(fig, name: str) -> Path:
    OUTDIR.mkdir(parents=True, exist_ok=True)
    png = OUTDIR / f"{name}.png"
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(OUTDIR / f"{name}.svg", bbox_inches="tight", metadata={"Date": None})
    plt.close(fig)
    return png


def _trunc(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def _wrap(s: str, width: int, maxlines: int = 2, indent: str = "  ") -> list[str]:
    """Word-wrap ``s`` to ``width`` chars, at most ``maxlines`` lines; the
    last line is ellipsis-truncated if content remains. Verbatim tokens."""
    words = s.split()
    lines: list[str] = []
    cur = ""
    for k, word in enumerate(words):
        pre = indent if lines else ""
        cand = f"{cur} {word}".strip()
        if len(pre + cand) <= width:
            cur = cand
            continue
        lines.append(pre + cur)
        if len(lines) == maxlines:
            rest = " ".join(words[k:])
            lines[-1] = _trunc(lines[-1] + " " + rest, width)
            return lines
        cur = word
    if cur:
        lines.append((indent if lines else "") + cur)
    return lines


def _mwrap(kind: str, s: str, maxlines: int = 2) -> list[tuple[str, str]]:
    return [(kind, line) for line in _wrap(s, MONO_W, maxlines)]


# ---------------------------------------------------------------------------
# Card / flow layout.  The main axes uses data coords == inches (y down).
# ---------------------------------------------------------------------------

#: kind -> (fontsize, line height in inches, family, colour, weight, style)
LINE = {
    "body": (13.5, 0.30, "DejaVu Sans", INK, "normal", "normal"),
    "bold": (13.5, 0.30, "DejaVu Sans", INK, "bold", "normal"),
    "mono": (13.0, 0.285, MONO, CD["dunkelblau"], "normal", "normal"),
    "good": (13.0, 0.285, MONO, CD["tuerkis"], "normal", "normal"),
    "bad": (13.0, 0.285, MONO, CD["rot"], "normal", "normal"),
    "muted": (13.0, 0.285, "DejaVu Sans", MUTED, "normal", "italic"),
}
HEAD_H = 0.50
PAD_X = 0.28
PAD_BOT = 0.20


def card_height(lines, extra: float = 0.0) -> float:
    h = HEAD_H + 0.08 + extra + PAD_BOT
    for kind, _text in lines:
        h += LINE[kind][1]
    return h


def draw_card(ax, x, y, w, number, title, tag, lines, extra: float = 0.0) -> float:
    """Draw one stage card at (x, y) top-left; return its bottom y."""
    h = card_height(lines, extra)
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0,rounding_size=0.10",
            linewidth=1.4, edgecolor=GRID, facecolor="white", zorder=2,
        )
    )
    cx, cy = x + 0.34, y + HEAD_H / 2 + 0.02
    ax.add_patch(Circle((cx, cy), 0.16, facecolor=CD["dunkelblau"], edgecolor="none", zorder=3))
    ax.text(cx, cy, str(number), color="white", fontsize=13, fontweight="bold",
            ha="center", va="center", zorder=4)
    ax.text(x + 0.62, cy, title, fontsize=15, fontweight="bold", color=INK,
            ha="left", va="center", zorder=4)
    llm = tag == TAG_LLM
    tcol = CD["orange"] if llm else CD["tuerkis"]
    tfill = "#FAEDE4" if llm else "#E4F1F0"
    tw = 0.098 * len(tag) + 0.30
    tx = x + w - PAD_X * 0.8 - tw
    ax.add_patch(
        FancyBboxPatch(
            (tx, cy - 0.15), tw, 0.32,
            boxstyle="round,pad=0,rounding_size=0.13",
            linewidth=1.0, edgecolor=tcol, facecolor=tfill, zorder=3,
        )
    )
    ax.text(tx + tw / 2, cy + 0.005, tag, fontsize=13, color=tcol,
            ha="center", va="center", zorder=4)
    ax.plot([x + PAD_X * 0.6, x + w - PAD_X * 0.6], [y + HEAD_H, y + HEAD_H],
            color=GRID, linewidth=1.0, zorder=3)
    cy2 = y + HEAD_H + 0.08
    for kind, text in lines:
        fs, lh, fam, col, wt, st = LINE[kind]
        if text:
            ax.text(x + PAD_X, cy2 + lh / 2, text, fontsize=fs, family=fam,
                    color=col, fontweight=wt, fontstyle=st, ha="left",
                    va="center", zorder=4)
        cy2 += lh
    return y + h


def arrow_down(ax, x, y0, y1) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (x, y0 + 0.03), (x, y1 - 0.03),
            arrowstyle="-|>", mutation_scale=16, linewidth=1.8,
            color=CD["tuerkis"], zorder=1,
        )
    )


def elbow(ax, p0, p1, xg) -> None:
    """3-segment elbow p0 -> (xg, y0) -> (xg, y1) -> p1 with an arrow head."""
    (x0, y0), (x1, y1) = p0, p1
    ax.plot([x0, xg, xg], [y0, y0, y1], color=CD["tuerkis"], linewidth=1.8,
            solid_capstyle="round", zorder=1)
    ax.add_patch(
        FancyArrowPatch(
            (xg, y1), (x1, y1),
            arrowstyle="-|>", mutation_scale=16, linewidth=1.8,
            color=CD["tuerkis"], zorder=1,
        )
    )


def graph_legend(ax, x, y, w, counts) -> None:
    """Two-row class legend with counts, top-left corner at (x, y)."""
    cols = 3
    cw = w / cols
    k = 0
    for cls in CLS_ORDER:
        if cls not in counts:
            continue
        r, c = divmod(k, cols)
        lx, ly = x + c * cw, y + r * 0.32
        ax.add_patch(Circle((lx + 0.09, ly), 0.08, facecolor=CLS_COLOR[cls],
                            edgecolor=INK, linewidth=0.6, zorder=4))
        ax.text(lx + 0.25, ly, f"{cls} ({counts[cls]})", fontsize=13,
                color=INK, ha="left", va="center", zorder=4)
        k += 1


def _collapse_multi(g: nx.MultiDiGraph) -> nx.DiGraph:
    simple = nx.DiGraph()
    simple.add_nodes_from(g.nodes(data=True))
    for u, v, dat in g.edges(data=True):
        if simple.has_edge(u, v):
            simple[u][v]["n"] += 1
        else:
            simple.add_edge(u, v, n=1, **{k: dat.get(k) for k in ("type", "role")})
    return simple


def draw_schema_graph(fig, x, y, w, h, g, W, H, seed=7, node_size=90,
                      hand_pos=None, labels=None, labels_above=(),
                      edge_labels=None, margins=(0.05, 0.05)) -> None:
    """Embed the schema graph into the rect (x, y, w, h) in figure inches."""
    rect = [x / W, 1.0 - (y + h) / H, w / W, h / H]
    axg = fig.add_axes(rect)
    axg.set_facecolor("white")
    axg.axis("off")
    simple = _collapse_multi(g)
    if hand_pos is not None:
        pos = hand_pos
    else:
        # No scipy on this box: seeded spring layout. Isolated nodes (declared
        # in the sidecar but never referenced by a canonical row body) would
        # dominate the normalisation as a ring, so the connected core is laid
        # out alone and the isolated nodes are parked in a bottom row.
        und = simple.to_undirected()
        iso = sorted(n for n in und if und.degree(n) == 0)
        core = und.subgraph([n for n in und if n not in iso])
        pos = nx.spring_layout(core, seed=7, k=0.55, iterations=300)
        cols = max(1, (len(iso) + 1) // 2)
        for k_i, n in enumerate(iso):
            r, c = divmod(k_i, cols)
            step = 2.0 / max(1, cols - 1)
            pos[n] = (-1.0 + c * step, -1.45 - 0.30 * r)
    nx.draw_networkx_edges(simple, pos, ax=axg, edge_color="#aab4bc", width=1.2,
                           arrows=True, arrowsize=10, node_size=node_size)
    if edge_labels:
        for txt, (ex, ey), ha, rot in edge_labels:
            axg.text(ex, ey, txt, fontsize=13, ha=ha, va="center",
                     color=MUTED, family=MONO, rotation=rot, zorder=2,
                     rotation_mode="anchor", transform_rotates_text=True,
                     bbox=dict(facecolor="white", edgecolor="none", pad=1.2))
    node_colors = [CLS_COLOR.get(dat.get("cls"), MUTED) for _, dat in g.nodes(data=True)]
    nodes = nx.draw_networkx_nodes(g, pos, ax=axg, node_size=node_size,
                                   node_color=node_colors, edgecolors=INK,
                                   linewidths=0.7)
    nodes.set_zorder(3)
    if labels:
        for n, (px, py) in pos.items():
            if n in labels_above:
                axg.text(px, py + 0.11, labels.get(n, ""), fontsize=14,
                         fontweight="bold", ha="center", va="bottom", color=INK)
            else:
                axg.text(px, py - 0.11, labels.get(n, ""), fontsize=14,
                         fontweight="bold", ha="center", va="top", color=INK)
    axg.margins(x=margins[0], y=margins[1])


# ---------------------------------------------------------------------------
# Figure A data: read every artifact live from the corpus.
# ---------------------------------------------------------------------------


def real_data() -> dict:
    d = {}
    dossier = json.loads((CORPUS / "dossiers" / f"{KEY}.json").read_text())
    d["title"] = dossier["source"]["title"]
    d["year"] = dossier["source"]["year"]
    d["doi"] = dossier["source"]["doi"]
    d["authors"] = dossier["source"]["authors"]
    d["n_formulas"] = len(dossier["formulas"])
    raw = {f["id"]: f["latex"] for f in dossier["formulas"]}
    d["raw_eq2"] = raw["eq-0002"]
    d["raw_eq1"] = raw["eq-0001"]
    d["raw_eq3"] = raw["eq-0003"]

    dec = json.loads((CORPUS / "decisions" / f"assist_{KEY}.json").read_text())
    decisions = dec["formula_decisions"][0]["decisions"]
    counts: dict[str, int] = {}
    for row in decisions:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    d["verdicts"] = counts
    d["corr_eq3"] = next(r for r in decisions if r["id"] == "eq-0003")["parts"][0]
    d["symbols"] = dec["symbol_tables"][0]["symbols"]

    cache = json.loads((CORPUS / "assist" / "cache" / f"{KEY}.a.json").read_text())
    inner = json.loads(cache["records"][-1]["content"])
    rej = next(r for r in inner["decisions"] if r["status"] == "rejected")
    d["rej_id"], d["rej_reason"] = rej["id"], rej["reason"]

    decl_lines = (CORPUS / "declarations" / f"{KEY}.tex").read_text().splitlines()
    picks = ("%@ index T ", "%@ param p ", "%@ var x ", "%@ obj ")
    d["decls"] = [next(ln for ln in decl_lines if ln.startswith(p)) for p in picks]

    promoted = (CORPUS / "promoted" / f"{KEY}.tex").read_text().splitlines()
    d["can_obj"] = next(ln.strip() for ln in promoted if "\\max\\quad" in ln)
    d["can_con"] = next(ln.strip() for ln in promoted if "eq\\_0003" in ln)

    promo = json.loads((CORPUS / "promotion.json").read_text())
    entry = next(p for p in promo["papers"] if p["paper_key"] == KEY)
    d["rows"] = entry["rows"]
    d["rows_in"] = entry["partial"]["rows_included"]
    d["rows_ex"] = entry["partial"]["rows_excluded"]

    d["graph"] = schema_nx(load(CORPUS / "formulations" / f"{KEY}.json"))

    wl = json.loads((CORPUS / "wl" / "similarity.json").read_text())
    row = wl["matrix"][KEY]
    sim, nb = max((s, m) for m, s in row.items() if m != KEY)
    nbd = json.loads((CORPUS / "dossiers" / f"{nb}.json").read_text())["source"]
    d["wl_sim"], d["wl_title"], d["wl_year"] = sim, nbd["title"], nbd["year"]

    clusters = json.loads((CORPUS / "fingerprint" / "clusters.json").read_text())
    from corpusbuilder.fingerprint import cluster_label

    cl = next(c for c in clusters["clusters"] if KEY in c["papers"])
    d["family"] = cluster_label(cl.get("top_features") or [])
    d["family_id"], d["family_n"] = cl["id"], cl["size"]
    return d


# ---------------------------------------------------------------------------
# Figure A
# ---------------------------------------------------------------------------


def figure_real() -> Path:
    d = real_data()
    W, H = 13.6, 15.3
    colw, gut = 6.2, 0.6
    xl = 0.15
    xr = xl + colw + gut
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")

    ax.text(xl, 0.32, "One paper through the LP-mining pipeline",
            fontsize=19, fontweight="bold", color=INK, ha="left", va="center")
    ax.text(xl, 0.66, f"every excerpt below is the real artifact for {d['doi']}",
            fontsize=13.5, color=MUTED, ha="left", va="center", fontstyle="italic")

    y0 = 1.05
    gap = 0.38

    # ---- left column -----------------------------------------------------
    title_lines = [("bold", t) for t in _wrap(f"“{d['title']}”", BOLD_W)]
    b1 = draw_card(ax, xl, y0, colw, 1, "SOURCE", TAG_DET, [
        *title_lines,
        ("body", f"{', '.join(a.split()[-1] for a in d['authors'])} ({d['year']})"),
        ("body", f"DOI {d['doi']}"),
        ("body", f"Elsevier full-text XML → {d['n_formulas']} extracted formulas"),
        ("muted", "(MathML → LaTeX, deterministic converter)"),
    ])

    arrow_down(ax, xl + colw / 2, b1, b1 + gap)
    b2 = draw_card(ax, xl, b1 + gap, colw, 2, "RAW EXTRACTION", TAG_DET, [
        *_mwrap("mono", d["raw_eq2"], 2),
        ("muted", "eq-0002 verbatim: spaced identifiers “T_{n e w}” and"),
        ("muted", "\\underset big operators — typical MathML damage"),
    ])

    arrow_down(ax, xl + colw / 2, b2, b2 + gap)
    v = d["verdicts"]
    b3 = draw_card(ax, xl, b2 + gap, colw, 3, "TRIAGE", TAG_LLM, [
        ("body", f"{sum(v.values())} formulas → {v.get('accepted', 0)} accepted · "
                 f"{v.get('corrected', 0)} corrected · {v.get('rejected', 0)} rejected"),
        ("bad", _trunc(d["raw_eq1"], MONO_W)),
        ("body", f"✖ {d['rej_id']} rejected — reason: “{d['rej_reason']}”"),
        ("muted", "(a set definition, not a model statement)"),
    ])

    arrow_down(ax, xl + colw / 2, b3, b3 + gap)
    sym = d["symbols"]
    srows = [("T", sym["T"]), ("p", sym["p"]), ("x", sym["x"]),
             ("z", sym["z"]), ("Δ", sym["Δ"])]
    sline1 = "   ".join(f"{k} → {v}" for k, v in srows[:3])
    sline2 = "   ".join(f"{k} → {v}" for k, v in srows[3:])
    b4 = draw_card(ax, xl, b3 + gap, colw, 4, "SYMBOLS", TAG_LLM, [
        ("mono", sline1),
        ("mono", sline2),
        ("muted", f"5 of {len(sym)} rows of the per-paper symbol table;"),
        ("muted", "one verdict per (paper, symbol), reviewer overrides"),
    ])

    arrow_down(ax, xl + colw / 2, b4, b4 + gap)
    b5 = draw_card(ax, xl, b4 + gap, colw, 5, "DECLARATIONS", TAG_LLM, [
        ("mono", _trunc(d["decls"][0], MONO_W)),
        ("mono", _trunc(d["decls"][1], MONO_W)),
        ("mono", _trunc(d["decls"][2], MONO_W)),
        ("mono", _trunc(d["decls"][3], MONO_W)),
        ("muted", "sidecar corpus/declarations/….tex — states what the"),
        ("muted", "equations never do; marked non-deterministically sourced"),
    ])

    # ---- right column ----------------------------------------------------
    b6 = draw_card(ax, xr, y0, colw, 6, "REPAIR", TAG_LLM, [
        *_mwrap("mono", d["raw_eq3"], 2),
        ("body", "↓  LLM proposes the repaired form (eq-0003)"),
        *_mwrap("good", d["corr_eq3"], 2),
        ("muted", "accepted only because the deterministic parser passes it"),
    ])

    arrow_down(ax, xr + colw / 2, b6, b6 + gap)
    b7 = draw_card(ax, xr, b6 + gap, colw, 7, "CANONICAL MODEL", TAG_DET, [
        ("mono", "\\begin{align}   % corpus/promoted/….tex"),
        *_mwrap("mono", " " + d["can_obj"], 2),
        *_mwrap("mono", " " + d["can_con"], 2),
        ("mono", "\\end{align}"),
        ("body", f"{d['rows_in']}/{d['rows']} rows canonical "
                 f"({round(100 * d['rows_in'] / d['rows'])}% coverage); {d['rows_ex']} rows"),
        ("body", "excluded, each with its parser message recorded"),
    ])

    arrow_down(ax, xr + colw / 2, b7, b7 + gap)
    y8 = b7 + gap
    g = d["graph"]
    counts: dict[str, int] = {}
    for _, dat in g.nodes(data=True):
        counts[dat.get("cls")] = counts.get(dat.get("cls"), 0) + 1
    graph_h = 2.55
    cap = [
        ("body", f"schema graph: {g.number_of_nodes()} nodes · "
                 f"{g.number_of_edges()} edges — one node per entity"),
        ("muted", "bottom rows: entities declared but never referenced"),
    ]
    cap_h = sum(LINE[k][1] for k, _ in cap)
    b8 = draw_card(ax, xr, y8, colw, 8, "TYPED GRAPH", TAG_DET, cap,
                   extra=graph_h + 0.78)
    ins_y = y8 + HEAD_H + 0.08 + cap_h + 0.05
    draw_schema_graph(fig, xr + 0.4, ins_y, colw - 0.8, graph_h, g, W, H, seed=7)
    graph_legend(ax, xr + PAD_X, ins_y + graph_h + 0.26, colw - 0.7, counts)

    arrow_down(ax, xr + colw / 2, b8, b8 + gap)
    wl_lines = [("bold", t) for t in _wrap(f"“{d['wl_title']}” ({d['wl_year']})", BOLD_W)]
    fam_lines = [("body", t) for t in
                 _wrap(f"fingerprint family: {d['family']}", BODY_W, indent="   ")]
    b9 = draw_card(ax, xr, b8 + gap, colw, 9, "RELATIONS", TAG_DET, [
        ("body", f"WL structural similarity {d['wl_sim']:.2f} to"),
        *wl_lines,
        *fam_lines,
        ("muted", f"(cluster {d['family_id']}, n={d['family_n']} papers)"),
    ])

    # snake connector: card 5 -> card 6 through the column gutter
    elbow(ax, (xl + colw, (b4 + gap + b5) / 2), (xr, y0 + HEAD_H / 2),
          xl + colw + gut / 2)

    ax.text(xl, max(b5, b9) + 0.44,
            "LLM stages propose; the deterministic parser gates what enters the corpus.",
            fontsize=13, color=MUTED, ha="left", va="center", fontstyle="italic")
    return _save(fig, "fig_example_real")


# ---------------------------------------------------------------------------
# Figure B: the toy example (canonical doc verified by real ingest).
# ---------------------------------------------------------------------------

TOY_DOC = r"""%@ meta id=toy_two_trains family=lp schema=0.1.0
%@ name :: Toy: two trains, one single-track section
%@ desc :: Two departure times, one minimum-headway constraint.
%@ index T ordered=1 cyclic=0 :: trains {A, B}
%@ param h shape=- kind=scalar domain=- :: minimum headway
%@ var t shape=T domain=non_negative role=primary drole=- lo=- hi=- :: departure time
%@ obj sense=min name=objective combination=sum :: total departure time
%@ con headway kind=headway domain=- indicator=- :: B departs at least h after A
\begin{align}
  \min\quad & t_{A} + t_{B} \tag{objective} \\
  & t_{B} - t_{A} \ge h \tag{headway} \\
\end{align}
"""


def figure_toy() -> tuple[Path, bool]:
    res = ingest_latex(TOY_DOC, source="toy_two_trains.tex")
    if not res.ok:
        raise SystemExit(f"toy doc no longer ingests: {res.failures}")
    g = schema_nx(res.formulation)

    W, H = 13.6, 13.4
    colw, gut = 6.2, 0.6
    xl = 0.15
    xr = xl + colw + gut
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")

    ax.text(xl, 0.32, "The same pipeline on a toy example",
            fontsize=19, fontweight="bold", color=INK, ha="left", va="center")
    ax.text(xl, 0.66, "two trains, one single-track section — every artifact fully legible",
            fontsize=13.5, color=MUTED, ha="left", va="center", fontstyle="italic")

    y0 = 1.05
    gap = 0.38

    # ---- left column -----------------------------------------------------
    b1 = draw_card(ax, xl, y0, colw, 1, "SOURCE", TAG_DET, [
        ("bold", "toy paper: two trains, one single-track section"),
        ("body", "train B may enter the section only h minutes"),
        ("body", "after train A → 2 extracted formulas"),
    ])

    arrow_down(ax, xl + colw / 2, b1, b1 + gap)
    b2 = draw_card(ax, xl, b1 + gap, colw, 2, "RAW EXTRACTION", TAG_DET, [
        ("mono", r"min Z = t A + t B , s . t ."),
        ("mono", r"t B - t A \geq h \text{(headway)}"),
        ("muted", "typical damage: identifiers split into single"),
        ("muted", "letters, objective and constraint glued together"),
    ])

    arrow_down(ax, xl + colw / 2, b2, b2 + gap)
    b3 = draw_card(ax, xl, b2 + gap, colw, 3, "TRIAGE", TAG_LLM, [
        ("body", "2 formulas → both accepted"),
        ("mono", "row 1: objective     row 2: constraint"),
        ("muted", "(and the glued block is split into two rows)"),
    ])

    arrow_down(ax, xl + colw / 2, b3, b3 + gap)
    b4 = draw_card(ax, xl, b3 + gap, colw, 4, "SYMBOLS", TAG_LLM, [
        ("mono", "t → variable     h → parameter"),
        ("mono", "A, B → train labels (members of index T)"),
    ])

    arrow_down(ax, xl + colw / 2, b4, b4 + gap)
    b5 = draw_card(ax, xl, b4 + gap, colw, 5, "DECLARATIONS", TAG_LLM, [
        ("mono", "%@ index T ordered=1 cyclic=0 :: trains {A, B}"),
        ("mono", "%@ param h shape=- kind=scalar domain=-"),
        ("mono", "     :: minimum headway"),
        ("mono", "%@ var t shape=T domain=non_negative"),
        ("mono", "     role=primary :: departure time"),
        ("mono", "%@ obj sense=min name=objective combination=sum"),
    ])

    # ---- right column ----------------------------------------------------
    b6 = draw_card(ax, xr, y0, colw, 6, "REPAIR", TAG_LLM, [
        ("bad", r"t A          t B - t A \geq h"),
        ("body", "↓ spaced-identifier collapse, explicit \\cdot products"),
        ("good", r"t_{A}        t_{B} - t_{A} \ge h"),
        ("muted", "accepted only because the deterministic parser passes it"),
    ])

    arrow_down(ax, xr + colw / 2, b6, b6 + gap)
    b7 = draw_card(ax, xr, b6 + gap, colw, 7, "CANONICAL MODEL", TAG_DET, [
        ("mono", r"\begin{align}"),
        ("mono", r"  \min\quad & t_{A} + t_{B} \tag{objective} \\"),
        ("mono", r"  & t_{B} - t_{A} \ge h \tag{headway} \\"),
        ("mono", r"\end{align}"),
        ("good", f"ingest_latex(doc).ok = {res.ok}  % run just now"),
        ("body", "2/2 rows canonical (100% coverage)"),
    ])

    arrow_down(ax, xr + colw / 2, b7, b7 + gap)
    y8 = b7 + gap
    counts: dict[str, int] = {}
    for _, dat in g.nodes(data=True):
        counts[dat.get("cls")] = counts.get(dat.get("cls"), 0) + 1
    graph_h = 2.85
    cap = [
        ("body", f"real schema_nx output of the ingest: {g.number_of_nodes()} nodes"
                 f" · {g.number_of_edges()} edges"),
    ]
    cap_h = sum(LINE[k][1] for k, _ in cap)
    b8 = draw_card(ax, xr, y8, colw, 8, "TYPED GRAPH", TAG_DET, cap,
                   extra=graph_h + 0.78)
    hand_pos = {
        "objective:0": (0.06, 0.90),
        "constraint:headway": (0.88, 0.90),
        "var:t": (0.47, 0.48),
        "index:T": (0.06, 0.10),
        "param:h": (0.88, 0.10),
    }
    labels = {
        "objective:0": "objective",
        "constraint:headway": "headway",
        "var:t": "t",
        "index:T": "T",
        "param:h": "h",
    }
    import math

    def _ang(a, b):
        deg = math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
        return deg + 180 if deg < -90 or deg > 90 else deg

    a_obj = _ang(hand_pos["objective:0"], hand_pos["var:t"])
    a_con = _ang(hand_pos["constraint:headway"], hand_pos["var:t"])
    a_idx = _ang(hand_pos["var:t"], hand_pos["index:T"])
    edge_labels = [
        ("in objective ×2", (0.265, 0.69), "center", a_obj),
        ("in constraint ×2", (0.675, 0.69), "center", a_con),
        ("rhs", (0.915, 0.50), "left", 0),
        ("uses_index", (0.265, 0.29), "center", a_idx),
    ]
    ins_y = y8 + HEAD_H + 0.08 + cap_h + 0.05
    draw_schema_graph(fig, xr + 0.4, ins_y, colw - 0.8, graph_h,
                      g, W, H, node_size=640, hand_pos=hand_pos, labels=labels,
                      labels_above={"objective:0", "constraint:headway"},
                      edge_labels=edge_labels, margins=(0.16, 0.20))
    graph_legend(ax, xr + PAD_X, ins_y + graph_h + 0.26, colw - 0.7, counts)

    arrow_down(ax, xr + colw / 2, b8, b8 + gap)
    b9 = draw_card(ax, xr, b8 + gap, colw, 9, "RELATIONS", TAG_DET, [
        ("body", "WL similarity + structural fingerprint place the toy"),
        ("body", "next to other single-resource ordering models"),
        ("muted", "(computed corpus-wide, not per paper)"),
    ])

    elbow(ax, (xl + colw, (b4 + gap + b5) / 2), (xr, y0 + HEAD_H / 2),
          xl + colw + gut / 2)
    ax.text(xl, max(b5, b9) + 0.44,
            "the toy canonical document above is real: this script ingests it "
            "with lp2graph before drawing",
            fontsize=13, color=MUTED, ha="left", va="center", fontstyle="italic")
    return _save(fig, "fig_example_toy"), res.ok


def main() -> None:
    _style()
    p1 = figure_real()
    p2, ok = figure_toy()
    for p in (p1, p2):
        print(p, p.stat().st_size, "bytes")
    print("toy ingest ok:", ok)


if __name__ == "__main__":
    main()
