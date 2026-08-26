"""Two pies: deterministic parsing vs canonical translation, numbers read live.

Left: the deterministic structural layer over every extracted formula
(resolution.json). Right: how far the review-kept rows actually get toward a
canonical formulation (promotion.json) — the honest work-in-progress chart.

Regenerate::

    PYTHONPATH=. python3 scripts/pie_figures.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import example_figures as ef
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent


def data() -> tuple[list, list, int, int]:
    r = json.loads((ROOT / "corpus/resolution.json").read_text())
    total = r["formulas"]
    parses = r["structural_axes"]["parses"]
    clean = r["structurally_clean"]
    pie1 = [
        ("clean on all four checks", clean, ef.CD["tuerkis"]),
        ("parses, fails another check", parses - clean, ef.CD["midblau"]),
        ("fails to parse", total - parses, ef.CD["rot"]),
    ]
    if sum(n for _, n, _ in pie1) != total:
        raise SystemExit("pie 1 does not sum to the formula total")

    p = json.loads((ROOT / "corpus/promotion.json").read_text())
    canon = excl = grammar = other = 0
    for e in p["papers"]:
        rows = e.get("rows") or 0
        if e.get("promoted"):
            inc = e["partial"]["rows_included"] if e.get("partial") else rows
            canon += inc
            excl += rows - inc
        elif e.get("cause") in ("outside_grammar", "normalize_failed", "semantic_invalid"):
            grammar += rows
        else:
            other += rows
    pie2 = [
        ("paper blocked by grammar causes", grammar, ef.CD["orange"]),
        ("paper blocked by other causes", other, "#8a949e"),
        ("excluded row in a partial model", excl, ef.CD["violett"]),
        ("canonical, in a promoted model", canon, ef.CD["tuerkis"]),
    ]
    total2 = sum(n for _, n, _ in pie2)
    return pie1, pie2, total, total2


def draw() -> None:
    pie1, pie2, total1, total2 = data()
    ef._style()
    W, H = 14.0, 6.8
    fig = plt.figure(figsize=(W, H))
    fig.patch.set_facecolor("white")
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.text(0.4, 0.45, "Deterministic parsing vs canonical translation",
            fontsize=21, fontweight="bold", color=ef.INK, ha="left", va="top")
    ax.text(0.4, 0.92,
            "the deterministic layer covers nearly everything; the canonical "
            "grammar is the measured frontier (work in progress)",
            fontsize=13.5, style="italic", color=ef.MUTED, ha="left", va="top")

    canon_share = pie2[3][1] / total2
    specs = (
        (0.35, f"all {total1:,} extracted formulas",
         "deterministic structural checks", pie1,
         (f"{pie1[0][1] / total1:.0%}", "structurally clean", ef.CD["tuerkis"])),
        (7.35, f"all {total2:,} review-kept candidate rows",
         "distance to the canonical formulation", pie2,
         (f"{canon_share:.1%}", "canonical so far", ef.CD["tuerkis"])),
    )
    for x0, head, sub, slices, centre in specs:
        axp = fig.add_axes([(x0 + 0.30) / W, 0.10, 3.9 / W, 3.9 / H])
        axp.axis("off")
        vals = [n for _, n, _ in slices]
        cols = [c for _, _, c in slices]
        axp.pie(
            vals, colors=cols, startangle=90, counterclock=False,
            wedgeprops=dict(width=0.42, edgecolor="white", linewidth=2),
        )
        axp.set_aspect("equal")
        c_val, c_label, c_color = centre
        axp.text(0, 0.10, c_val, fontsize=27, fontweight="bold",
                 color=c_color, ha="center", va="center")
        axp.text(0, -0.24, c_label, fontsize=11, color=ef.MUTED,
                 ha="center", va="center")
        ax.text(x0 + 2.25, 1.72, head, fontsize=14.5, fontweight="bold",
                color=ef.INK, ha="center", va="top")
        ax.text(x0 + 2.25, 2.08, sub, fontsize=11.5, color=ef.MUTED,
                ha="center", va="top")
        # label column to the right of the donut
        ly = 2.95
        for name, n, c in slices:
            ax.add_patch(plt.Rectangle((x0 + 4.45, ly - 0.09), 0.18, 0.18,
                                       facecolor=c, edgecolor="none"))
            ax.text(x0 + 4.72, ly, f"{name}", fontsize=12, color=ef.INK,
                    ha="left", va="center")
            ax.text(x0 + 4.72, ly + 0.30,
                    f"{n:,}  ·  {n / sum(vals):.1%}", fontsize=11.5,
                    color=ef.MUTED, ha="left", va="center", family=ef.MONO)
            ly += 0.86
    ax.text(0.4, H - 0.30,
            "Sources: corpus/resolution.json (deterministic axes) and corpus/promotion.json "
            "(partial promotion, ADR-0013); candidate rows = accepted or corrected in review.",
            fontsize=10.5, color=ef.MUTED, ha="left", va="center", style="italic")
    ef._save(fig, "fig_pies_parse_vs_canonical")


if __name__ == "__main__":
    draw()
    from PIL import Image

    p = ROOT / "corpus/talkpack/figures/fig_pies_parse_vs_canonical.png"
    im = Image.open(p)
    im.thumbnail((1900, 1900))
    im.save(p.with_name("fig_pies_parse_vs_canonical_preview.png"))
    print("done", Image.open(p).size)
