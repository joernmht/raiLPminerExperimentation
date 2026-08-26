#!/usr/bin/env python3
"""Widescreen "wallpaper" mosaics of per-paper structural graphs.

One tile per corpus paper: a tiny bipartite formula-symbol graph built
deterministically from the paper's dossier + HITL/assist decisions, tinted by
the paper's fingerprint family (``corpus/fingerprint/clusters.json``). Tiles
are grouped by family (largest first, row-major) so the mosaic reads as
colour bands. No legend by design: it is a wallpaper, not a chart.

Outputs (into ``corpus/talkpack/figures/``):

- ``fig_wallpaper_all``  — every clustered paper (~238 tiles), 16:9,
  5120x2880 px. PNG only.
- ``fig_wallpaper_50``   — exactly 50 tiles in a 10x5 grid, stratified
  proportionally to family size (>=1 per family, largest-remainder), the
  most formula-rich papers per family. PNG + SVG.

Run: ``PYTHONPATH=. python3 scripts/wallpaper_figures.py [--preview-dir DIR]``
Deterministic across reruns (per-paper seeded spring layouts).
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.colors as mcolors
import matplotlib.pyplot as plt
import networkx as nx
from matplotlib.patches import FancyBboxPatch, Rectangle
from PIL import Image

from corpusbuilder.game import extract_symbols, is_objective_latex

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"
DECISIONS = CORPUS / "decisions"
CLUSTERS = CORPUS / "fingerprint" / "clusters.json"
OUTDIR = CORPUS / "talkpack" / "figures"

# ---------------------------------------------------------------------------
# Chair CD palette (mirrors corpusbuilder.talkpack / scripts.example_figures).
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
SERIES = (CD["tuerkis"], CD["orange"], CD["midblau"], CD["rot"], CD["violett"])
MUTED = "#5f6b76"
EDGE = "#c9ced4"
GREY = "#8a949e"

KIND_COLOR = {
    "variable": CD["tuerkis"],
    "parameter": CD["violett"],
    "index": CD["gelb"],
}

MAX_FORMULAS = 24
MAX_SYMBOLS = 16

# Canvas: 25.6 x 14.4 in at 200 dpi = 5120 x 2880 px (16:9).
FIG_W_IN, FIG_H_IN, DPI = 25.6, 14.4, 200


def _fp_colors(k: int) -> list:
    """One colour per family: the CD series, compounding lightening per cycle.

    Identical rule to ``corpusbuilder.talkpack._fp_colors`` so the wallpaper's
    family tints match the fingerprint-families talk figure.
    """
    cols = []
    for i in range(k):
        cycle, idx = divmod(i, len(SERIES))
        base = mcolors.to_rgb(SERIES[idx])
        if cycle:
            factor = 0.55**cycle
            base = tuple(1 - (1 - c) * factor for c in base)
        cols.append(base)
    return cols


def _seed(key: str) -> int:
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest(), 16) % (2**31)


# ---------------------------------------------------------------------------
# Per-paper tile graph from existing artifacts.
# ---------------------------------------------------------------------------


def _verdicts(key: str) -> dict[str, str] | None:
    """formula id -> status from the assist decisions file, or None if absent."""
    path = DECISIONS / f"assist_{key}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text())
    out: dict[str, str] = {}
    for group in data.get("formula_decisions") or []:
        for dec in group.get("decisions") or []:
            if dec.get("id"):
                out[dec["id"]] = dec.get("status", "")
    return out


def _symbol_kinds(key: str) -> dict[str, str]:
    """symbol -> index|parameter|variable from the assist decisions file."""
    path = DECISIONS / f"assist_{key}.json"
    if not path.is_file():
        return {}
    data = json.loads(path.read_text())
    kinds: dict[str, str] = {}
    for tab in data.get("symbol_tables") or []:
        kinds.update(tab.get("symbols") or {})
    return kinds


def tile_graph(key: str) -> tuple[nx.Graph, int | None]:
    """Build the capped bipartite formula-symbol graph for one paper.

    Returns ``(graph, year)``. Nodes carry ``color`` and ``size`` attributes;
    isolated nodes are dropped.
    """
    dossier = json.loads((DOSSIERS / f"{key}.json").read_text())
    year = (dossier.get("source") or {}).get("year")
    verdicts = _verdicts(key)
    kinds = _symbol_kinds(key)

    rows = []
    for row in dossier.get("formulas") or []:
        if verdicts is not None and verdicts.get(row["id"]) not in ("accepted", "corrected"):
            continue
        rows.append(row)
    if not rows:
        # every formula rejected in HITL: show the raw extraction anyway —
        # an empty tinted tile is a hole in a wallpaper (picture, not metric).
        rows = list(dossier.get("formulas") or [])
    rows = rows[:MAX_FORMULAS]

    uses: list[tuple[str, list[str]]] = []
    counts: dict[str, int] = {}
    for row in rows:
        syms = [s for s, _n in extract_symbols(row.get("latex") or "")[0]]
        uses.append((row["id"], syms))
        for s in syms:
            counts[s] = counts.get(s, 0) + 1
    keep = {s for s, _c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:MAX_SYMBOLS]}

    g = nx.Graph()
    for row in rows:
        color = CD["orange"] if is_objective_latex(row.get("latex") or "") else CD["midblau"]
        g.add_node(("f", row["id"]), color=color, size=8.0)
    for s in sorted(keep):
        g.add_node(("s", s), color=KIND_COLOR.get(kinds.get(s, ""), GREY), size=16.0)
    for fid, syms in uses:
        for s in syms:
            if s in keep:
                g.add_edge(("f", fid), ("s", s))
    g.remove_nodes_from([n for n in list(g) if g.degree(n) == 0])
    return g, year


# ---------------------------------------------------------------------------
# Selection: families, orders, the stratified 50.
# ---------------------------------------------------------------------------


def load_families() -> list[dict]:
    """Clusters in file order (size-descending), each with its member papers
    restricted to those that actually have a dossier on disk."""
    data = json.loads(CLUSTERS.read_text())
    fams = []
    for c in data["clusters"]:
        papers = [k for k in c["papers"] if (DOSSIERS / f"{k}.json").is_file()]
        fams.append({"id": c["id"], "papers": papers})
    return fams


def _kept_formula_count(key: str) -> int:
    dossier = json.loads((DOSSIERS / f"{key}.json").read_text())
    verdicts = _verdicts(key)
    n = 0
    for row in dossier.get("formulas") or []:
        if verdicts is not None and verdicts.get(row["id"]) not in ("accepted", "corrected"):
            continue
        n += 1
    return n


def stratified_50(fams: list[dict], total: int = 50) -> list[dict]:
    """Largest-remainder allocation proportional to family size (>=1 each),
    then the most formula-rich papers within each family."""
    sizes = [len(f["papers"]) for f in fams]
    grand = sum(sizes)
    quotas = [s * total / grand for s in sizes]
    alloc = [max(1, int(q)) for q in quotas]
    remainders = sorted(
        range(len(fams)), key=lambda i: (-(quotas[i] - int(quotas[i])), i)
    )
    i = 0
    while sum(alloc) < total:
        alloc[remainders[i % len(fams)]] += 1
        i += 1
    while sum(alloc) > total:  # defensive; min-1 bumps could overshoot
        j = max(range(len(fams)), key=lambda x: alloc[x])
        alloc[j] -= 1
    picked = []
    for fam, n in zip(fams, alloc, strict=True):
        ranked = sorted(fam["papers"], key=lambda k: (-_kept_formula_count(k), k))
        picked.append({"id": fam["id"], "papers": ranked[:n]})
    return picked


# ---------------------------------------------------------------------------
# Rendering.
# ---------------------------------------------------------------------------


def _grid_for(n: int) -> tuple[int, int]:
    """Columns/rows on the 16:9 canvas: near-square cells, few blank cells."""
    best = None
    for cols in range(16, 28):
        rows = -(-n // cols)
        blanks = cols * rows - n
        cell_aspect = (FIG_W_IN / cols) / (FIG_H_IN / rows)
        score = 0.02 * blanks + abs(cell_aspect - 1.0)
        if best is None or score < best[0]:
            best = (score, cols, rows)
    return best[1], best[2]


def _draw_tile(ax, key: str, g: nx.Graph, year: int | None, tint) -> None:
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.set_axis_off()
    r, gg, b = tint
    ax.add_patch(
        FancyBboxPatch(
            (0.035, 0.035),
            0.93,
            0.93,
            boxstyle="round,pad=0,rounding_size=0.06",
            facecolor=(r, gg, b, 0.13),
            edgecolor="none",
            zorder=0,
        )
    )
    ax.add_patch(
        Rectangle(
            (0.10, 0.045),
            0.80,
            0.028,
            facecolor=(r, gg, b, 0.55),
            edgecolor="none",
            zorder=1,
        )
    )
    if g.number_of_nodes():
        pos = nx.spring_layout(g, seed=_seed(key), k=0.7)
        xs = [p[0] for p in pos.values()]
        ys = [p[1] for p in pos.values()]
        span_x = max(max(xs) - min(xs), 1e-9)
        span_y = max(max(ys) - min(ys), 1e-9)
        span = max(span_x, span_y)  # uniform scale keeps the layout's shape
        cx, cy = (max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2
        lo, hi = 0.14, 0.90
        mid, half = (lo + hi) / 2, (hi - lo) / 2
        # a 2-node graph stretched across the full tile reads as a stray
        # line; scale the extent with graph size instead.
        half *= min(1.0, 0.30 + 0.70 * (g.number_of_nodes() / 20.0) ** 0.5)
        sx = {n: mid + (p[0] - cx) / span * 2 * half for n, p in pos.items()}
        sy = {n: mid + (p[1] - cy) / span * 2 * half for n, p in pos.items()}
        for u, v in g.edges():
            ax.plot(
                [sx[u], sx[v]],
                [sy[u], sy[v]],
                color=EDGE,
                linewidth=0.4,
                alpha=0.85,
                zorder=2,
                solid_capstyle="round",
            )
        order = sorted(g.nodes(), key=str)
        ax.scatter(
            [sx[n] for n in order],
            [sy[n] for n in order],
            s=[g.nodes[n]["size"] for n in order],
            c=[g.nodes[n]["color"] for n in order],
            linewidths=0,
            zorder=3,
        )
    if year:
        ax.text(
            0.915,
            0.085,
            str(year),
            ha="right",
            va="bottom",
            fontsize=4.6,
            color=MUTED,
            alpha=0.85,
            zorder=4,
        )


def render_wallpaper(
    fams: list[dict],
    name: str,
    cols: int,
    rows: int,
    caption: str | None = None,
    svg: bool = False,
) -> tuple[Path, dict[int, int]]:
    colors = _fp_colors(len(fams))
    fig = plt.figure(figsize=(FIG_W_IN, FIG_H_IN), facecolor="white")
    plt.rcParams["svg.hashsalt"] = "wallpaper-figures"

    bottom = 0.028 if caption else 0.0
    cell_w = 1.0 / cols
    cell_h = (1.0 - bottom) / rows

    counts: dict[int, int] = {}
    idx = 0
    for fam, tint in zip(fams, colors, strict=True):
        for key in fam["papers"]:
            g, year = tile_graph(key)
            row, col = divmod(idx, cols)
            ax = fig.add_axes(
                [col * cell_w, 1.0 - (row + 1) * cell_h, cell_w, cell_h]
            )
            _draw_tile(ax, key, g, year, tint)
            counts[fam["id"]] = counts.get(fam["id"], 0) + 1
            idx += 1
    if caption:
        fig.text(0.5, 0.009, caption, ha="center", va="bottom", fontsize=7, color=MUTED)

    OUTDIR.mkdir(parents=True, exist_ok=True)
    png = OUTDIR / f"{name}.png"
    fig.savefig(png, dpi=DPI, facecolor="white")
    if svg:
        fig.savefig(OUTDIR / f"{name}.svg", facecolor="white", metadata={"Date": None})
    plt.close(fig)
    return png, counts


def write_preview(png: Path, preview_dir: Path, max_w: int = 1900) -> Path:
    preview_dir.mkdir(parents=True, exist_ok=True)
    img = Image.open(png)
    if img.width > max_w:
        img = img.resize((max_w, round(img.height * max_w / img.width)), Image.LANCZOS)
    out = preview_dir / f"{png.stem}_preview.png"
    img.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--preview-dir",
        type=Path,
        default=OUTDIR,
        help="where to write downscaled *_preview.png QC copies (default: next to figures)",
    )
    args = ap.parse_args()

    fams = load_families()
    n_all = sum(len(f["papers"]) for f in fams)
    cols, rows = _grid_for(n_all)
    png_all, counts_all = render_wallpaper(fams, "fig_wallpaper_all", cols, rows)

    fams50 = stratified_50(fams)
    png_50, counts_50 = render_wallpaper(
        fams50,
        "fig_wallpaper_50",
        cols=10,
        rows=5,
        caption="50 of 238 mined model structures, tinted by architecture family",
        svg=True,
    )

    for png, counts in ((png_all, counts_all), (png_50, counts_50)):
        prev = write_preview(png, args.preview_dir)
        with Image.open(png) as img:
            size = img.size
        print(f"{png}  {size[0]}x{size[1]} px  tiles/family={counts}  preview={prev}")


if __name__ == "__main__":
    main()
