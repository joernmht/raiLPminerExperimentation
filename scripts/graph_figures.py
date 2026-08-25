"""Standalone typed-graph figures: toy, real paper, and the WL twin pair.

Companion to ``example_figures.py`` (imports its style, colours and the
schema-graph renderer so the standalone graphs match the pipeline figures
exactly). Regenerate::

    PYTHONPATH=. python3 scripts/graph_figures.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import example_figures as ef
import matplotlib.pyplot as plt
from lp2graph import load
from lp2graph.mining.ingest import ingest_latex
from lp2graph.mining.isomorphism.report import schema_nx

from railpminer import _lp2graph  # noqa: F401


def _counts(g) -> dict:
    from collections import Counter

    c = Counter(dat.get("cls") for _, dat in g.nodes(data=True))
    return {k: c.get(k, 0) for k in ef.CLS_ORDER if c.get(k)}


def figure_graph(name: str, g, title: str, subtitle: str, W=9.0, H=8.2,
                 seed=7, node_size=110, hand_pos=None, labels=None,
                 labels_above=()) -> Path:
    ef._style()
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0.35, 0.42, title, fontsize=21, fontweight="bold",
            color=ef.INK, ha="left", va="top")
    ax.text(0.35, 0.90, subtitle, fontsize=14, style="italic",
            color=ef.MUTED, ha="left", va="top")
    ef.draw_schema_graph(fig, 0.5, 1.25, W - 1.0, H - 2.55, g, W, H, seed=seed,
                         node_size=node_size, hand_pos=hand_pos, labels=labels,
                         labels_above=labels_above)
    ef.graph_legend(ax, 0.5, H - 1.05, W - 1.0, _counts(g))
    return ef._save(fig, name)


def main() -> None:
    # 1) toy graph, standalone (same hand layout as the pipeline figure)
    res = ingest_latex(ef.TOY_DOC, source="toy_two_trains.tex")
    if not res.ok:
        raise SystemExit("toy doc stopped ingesting")
    g_toy = schema_nx(res.formulation)
    hand_pos = {
        "objective:0": (-0.75, 0.85),
        "constraint:headway": (0.75, 0.85),
        "var:t": (0.0, 0.05),
        "index:T": (-0.75, -0.75),
        "param:h": (0.75, -0.75),
    }
    labels = {
        "objective:0": "objective",
        "constraint:headway": "headway",
        "var:t": "t",
        "index:T": "T",
        "param:h": "h",
    }
    present = {n for n in g_toy.nodes}
    hand = {k: v for k, v in hand_pos.items() if k in present}
    lab = {k: v for k, v in labels.items() if k in present}
    figure_graph(
        "fig_graph_toy", g_toy,
        "Typed schema graph: toy model",
        "two trains, one single-track section · real lp2graph output "
        f"({g_toy.number_of_nodes()} nodes, {g_toy.number_of_edges()} edges)",
        W=8.0, H=7.0, node_size=900,
        hand_pos=hand or None, labels=lab,
        labels_above=("objective:0", "constraint:headway"),
    )

    # 2) real paper graph, standalone
    f_real = load("corpus/formulations/10.1016_j.trb.2017.06.018.json")
    g_real = schema_nx(f_real)
    figure_graph(
        "fig_graph_real", g_real,
        "Typed schema graph: skip-stop timetabling (2017)",
        "10.1016/j.trb.2017.06.018 · 11/14 rows canonical · "
        f"{g_real.number_of_nodes()} nodes, {g_real.number_of_edges()} edges "
        "(bottom rows: declared but never referenced)",
        W=10.0, H=9.0, node_size=130,
    )

    # 3) the WL twin pair, side by side, one figure.
    # Measured 2026-08-25: the once-promising cross-paper pair at full-graph
    # WL 0.90 collapses to 0.05 on connected cores (identical declared-only
    # index scaffolding, not structure). The one TRUE twin is the PESP pair:
    # core-WL 1.000, exact isomorphism refused only by a declared-but-unused
    # index in the lab copy.
    a = load("corpus/formulations/mip_2_8_pesp.json")
    b = load("corpus/formulations/pesp_solvable.json")
    ga, gb = schema_nx(a), schema_nx(b)
    ef._style()
    W, H = 12.5, 7.6
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0.35, 0.42,
            "One true structural twin: identical canonical cores (WL 1.00)",
            fontsize=21, fontweight="bold", color=ef.INK, ha="left", va="top")
    ax.text(0.35, 0.90,
            "Weisfeiler-Lehman subtree features on the connected cores of the "
            "typed schema graphs; exact isomorphism says \u201cdifferent\u201d "
            "because one model declares an index it never uses",
            fontsize=13.5, style="italic", color=ef.MUTED, ha="left", va="top")
    half = (W - 1.4) / 2

    import networkx as nx

    def packed_core(g):
        """Connected components laid out separately and packed on a row.

        A single spring layout overlaps disconnected components, and the
        isolated declared-only nodes would dominate the canvas; this figure
        shows the connected cores (disclosed in the subtitle) with each
        component given its own space.
        """
        und = g.to_undirected()
        comps = [c for c in nx.connected_components(und) if len(c) > 1]
        comps.sort(key=len, reverse=True)
        core = g.subgraph(set().union(*comps)) if comps else g
        pos = {}
        x_off = 0.0
        for comp in comps:
            sub = und.subgraph(comp)
            p = nx.spring_layout(sub, seed=7, k=0.9, iterations=400)
            xs = [v[0] for v in p.values()]
            ys = [v[1] for v in p.values()]
            w = (max(xs) - min(xs)) or 0.5
            h = (max(ys) - min(ys)) or 0.5
            scale = max(len(comp) ** 0.5 / 3.0, 0.45)
            for n, (px, py) in p.items():
                pos[n] = (x_off + (px - min(xs)) / w * scale,
                          (py - min(ys)) / h * scale - scale / 2)
            x_off += scale + 0.42
        return core, pos
    for gx, x0, head, sub in (
        (ga, 0.5, "mip_2_8_pesp (seed corpus)",
         "PESP cyclic timetabling · declares one unused index · "
         f"{ga.number_of_nodes()} nodes, {ga.number_of_edges()} edges"),
        (gb, 0.9 + half, "pesp_solvable (seed corpus)",
         "same model, bounded wrap counters · "
         f"{gb.number_of_nodes()} nodes, {gb.number_of_edges()} edges"),
    ):
        ax.text(x0 + half / 2, 1.30, head, fontsize=13,
                fontweight="bold", color=ef.INK, ha="center", va="top")
        ax.text(x0 + half / 2, 1.66, sub, fontsize=12,
                color=ef.MUTED, ha="center", va="top")
        core, pos = packed_core(gx)
        ef.draw_schema_graph(fig, x0, 1.95, half, H - 3.3, core, W, H,
                             node_size=170, hand_pos=pos)
    from collections import Counter

    both = Counter()
    for gx in (ga, gb):
        for _, dat in gx.nodes(data=True):
            both[dat.get("cls")] += 1
    ef.graph_legend(ax, 0.5, H - 1.0, W - 1.0,
                    {k: both.get(k, 0) for k in ef.CLS_ORDER if both.get(k)})
    ef._save(fig, "fig_graph_wl_twins")


if __name__ == "__main__":
    main()
