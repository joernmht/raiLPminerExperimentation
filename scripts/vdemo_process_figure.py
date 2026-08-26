"""Slide figure: one scenario, two processes (Track B verifier demo).

One widescreen figure (``fig_vdemo_process``) contrasts the two Track B
processes on the SAME real run — 10.1016/j.jrtpm.2013.10.001, "A
demand-based weighted train delay approach for rescheduling railway
networks in real time" (2013):

* TOP lane: single-shot ablation WITHOUT the verifier. The open-weights
  LLM drafts a model, the draft fails the canonical grammar (constraint
  ``kind`` left ``-`` in all 3 samples) and the process dead-ends with an
  incomplete model and no citation. 0/30 valid across 3 samples x 10
  scenarios, for BOTH models (gpt-oss-120b and Llama-3.3-70B-Instruct).

* BOTTOM lane: the deterministic lp2graph verifier in the loop. Findings
  are fed back each round; the real run converges in 4 rounds to a valid
  canonical model, whose typed graph is structurally matched against the
  corpus and cites the LinTim vehicle-scheduling IP at similarity 0.93.

Everything on the figure is read live from the run artifacts under
``corpus/vdemo3`` / ``corpus/vdemo3_retry`` (transcripts, final.tex,
rematch.json, summary.json) plus the paper's prose/dossier records; the
script hard-fails if any number on the slide cannot be re-derived, and
every card line is measured (``TextPath``) against its card width so text
cannot silently overflow.

Style matches ``scripts/example_figures.py`` (chair CD palette, white
field, inch-coordinate axes, 300 dpi PNG + SVG with the date stripped).

Run:  PYTHONPATH=. python3 scripts/vdemo_process_figure.py
Out:  corpus/talkpack/figures/fig_vdemo_process.{png,svg}
"""

from __future__ import annotations

# ruff: noqa: I001 — the ``railpminer._lp2graph`` import below is a side
# effect (it puts the sibling lp2graph checkout on ``sys.path``) and must run
# before the ``lp2graph`` imports (house pattern of example_figures.py).

import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties
from matplotlib.patches import Circle, FancyArrowPatch, FancyBboxPatch
from matplotlib.textpath import TextPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

import example_figures as ef

from railpminer import _lp2graph  # noqa: F401  (must precede lp2graph imports)

from lp2graph.mining.ingest import ingest_latex
from lp2graph.mining.isomorphism.report import schema_nx

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "corpus"
KEY = "10.1016_j.jrtpm.2013.10.001"
VD3 = CORPUS / "vdemo3"
VD3R = CORPUS / "vdemo3_retry"
EXPECT_MATCH = "lintim__openlintim__vehicle-scheduling-ip"

CD, INK, MUTED, GRID, MONO = ef.CD, ef.INK, ef.MUTED, ef.GRID, ef.MONO
SANS = "DejaVu Sans"
DEAD = "#98a2ab"  # arrow colour of the dead top lane

#: kind -> (fontsize, line height in inches, family, colour, weight, style)
LN = {
    "body": (12.0, 0.265, SANS, INK, "normal", "normal"),
    "body-s": (11.0, 0.245, SANS, INK, "normal", "normal"),
    "bold": (12.5, 0.275, SANS, INK, "bold", "normal"),
    "mono": (11.0, 0.245, MONO, CD["dunkelblau"], "normal", "normal"),
    "bad": (11.0, 0.245, MONO, CD["rot"], "normal", "normal"),
    "good": (10.5, 0.235, MONO, CD["tuerkis"], "normal", "normal"),
    "muted": (11.0, 0.240, SANS, MUTED, "normal", "italic"),
}
HEAD_H = 0.44
PAD_X = 0.22
TAG_H = 0.40  # extra height when a tag pill sits at the card bottom


def fail(msg: str) -> None:
    raise SystemExit(f"vdemo_process_figure: {msg}")


def tw(text: str, fs: float, family: str = SANS, weight: str = "normal",
       style: str = "normal") -> float:
    """Exact rendered text width in inches (deterministic, renderer-free)."""
    fp = FontProperties(family=family, weight=weight, style=style)
    return TextPath((0, 0), text, size=fs, prop=fp).get_extents().width / 72.0


# ---------------------------------------------------------------------------
# Data: everything read + re-verified live from the run artifacts.
# ---------------------------------------------------------------------------


def _jread(p: Path):
    if not p.is_file():
        fail(f"missing artifact {p}")
    return json.loads(p.read_text())


def _transcript(p: Path) -> list[dict]:
    if not p.is_file():
        fail(f"missing transcript {p}")
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


#: (pydantic model, field) -> 3-6 word finding class shown on a round chip.
FINDING_CLASS = {
    ("ConstraintTemplate", "kind"): "constraint kind = '-'",
    ("Term", "ref"): "term ref not an identifier",
    ("Parameter", "domain_class"): "param domain_class invalid",
}


def _round_label(ev: dict) -> tuple[str, bool]:
    """One chip label per transcript round event; True = valid (green)."""
    if ev["verdict"].startswith("valid"):
        warn = next(f for f in ev["findings"] if f["code"] == "unused-symbol")
        m = re.search(r":\s*(\w+)\.?\s*$", warn["message"])
        if not m:
            fail(f"cannot read unused symbol from {warn['message']!r}")
        return f"valid · unused param '{m.group(1)}'", True
    parse = next((f for f in ev["findings"] if f["code"] == "parse-failed"), None)
    if parse is None:
        fail(f"round {ev['round']} has no parse-failed finding")
    m = re.search(r"validation error for (\w+)\s+(\w+)", parse["detail"])
    if not m or (m.group(1), m.group(2)) not in FINDING_CLASS:
        fail(f"unmapped finding in round {ev['round']}: {parse['detail'][:80]!r}")
    return FINDING_CLASS[(m.group(1), m.group(2))], False


def _pretty_row(latex: str) -> str:
    """Compact display form of one real canonical align row (deterministic)."""
    s = latex.strip()
    s = re.sub(r"\\tag\{[^}]*\}", "", s)
    s = s.replace("&", "").replace("\\\\", "")
    s = re.sub(r"\\q?quad", " ", s)
    s = re.sub(r"\\mathcal\{(\w)\}", r"\1", s)
    s = s.replace("\\sum", "Σ").replace(" \\in ", "∈").replace("\\in ", "∈")
    s = s.replace("\\cdot", "·").replace("\\forall", "∀")
    s = s.replace("\\le ", "≤").replace("\\ge ", "≥").replace("\\min", "min")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.replace(" · ", "·").replace("_{t,a}", "_{ta}")
    s = s.replace(" = ", "=").replace("≤ ", "≤").replace("∀ ", "∀")
    return s


def data() -> dict:
    d: dict = {}

    # -- scenario: prose abstract + dossier metadata -------------------------
    prose = _jread(CORPUS / "prose" / f"{KEY}.json")
    if not prose.get("abstract"):
        fail("prose abstract empty")
    d["abstract"] = prose["abstract"]
    src = _jread(CORPUS / "dossiers" / f"{KEY}.json")["source"]
    if src["year"] != 2013 or "weighted train delay" not in src["title"]:
        fail(f"dossier metadata unexpected: {src['year']} {src['title']!r}")
    d["title"], d["year"] = src["title"], src["year"]
    d["doi"] = src["doi"]

    # -- aggregate stats: vdemo3 + vdemo3_retry (merged), vdemo2 (2nd model) -
    s3 = _jread(VD3 / "summary.json")
    sr = _jread(VD3R / "summary.json")
    s2 = _jread(CORPUS / "vdemo2" / "summary.json")
    for name, s in (("vdemo3", s3), ("vdemo2", s2)):
        nf = s["no_feedback"]
        if s["n_keys"] != 10 or nf["samples"] != 3 or nf["mean_valid_rate"] != 0.0:
            fail(f"{name} ablation is not 0/30 (n_keys={s['n_keys']}, {nf})")
    merged = {r["paper_key"]: r["feedback"] for r in s3["runs"]}
    for r in sr["runs"]:  # the retry re-ran the non-converged keys; it wins
        if r["paper_key"] not in merged:
            fail(f"retry key {r['paper_key']} not in vdemo3")
        merged[r["paper_key"]] = r["feedback"]
    rounds = sorted(fb["rounds_to_valid"] for fb in merged.values() if fb["converged"])
    if len(merged) != 10 or len(rounds) != 5:
        fail(f"merged feedback is not 5/10 (rounds={rounds})")
    d["mean_rounds"] = sum(rounds) / len(rounds)
    if abs(d["mean_rounds"] - 4.2) > 1e-9:
        fail(f"mean rounds != 4.2 (got {d['mean_rounds']})")
    d["n_valid"], d["n_scen"] = len(rounds), len(merged)

    def model_of(vdir: Path) -> str:
        first = sorted(vdir.glob("*--feedback/transcript.jsonl"))[0]
        return _transcript(first)[0]["endpoint"]["model"]

    d["model"], d["model2"] = model_of(VD3), model_of(CORPUS / "vdemo2")
    if d["model"] != "openai/gpt-oss-120b":
        fail(f"unexpected vdemo3 model {d['model']}")
    d["model_short"] = d["model"].split("/")[-1]
    d["model2_short"] = d["model2"].split("/")[-1]

    # -- bottom lane: the chosen feedback run --------------------------------
    fdir = VD3 / f"{KEY}--feedback"
    tr = _transcript(fdir / "transcript.jsonl")
    rounds_ev = [ev for ev in tr if ev.get("event") == "round"]
    end = next(ev for ev in tr if ev.get("event") == "end")
    if len(rounds_ev) != 4 or not end["converged"] or end["rounds_to_valid"] != 4:
        fail(f"feedback run did not converge in 4 rounds ({end})")
    d["round_chips"] = [_round_label(ev) for ev in rounds_ev]
    if [ok for _, ok in d["round_chips"]] != [False, False, False, True]:
        fail("round verdicts are not invalid,invalid,invalid,valid")
    d["temp_fb"] = tr[0]["temperature"]

    final_tex = (fdir / "final.tex").read_text()
    align = [
        ln.strip()
        for ln in final_tex.splitlines()
        if ln.strip().startswith(("\\min", "&"))
    ]
    if len(align) != 3:
        fail(f"expected 3 align rows in final.tex, got {len(align)}")
    d["rows"] = [_pretty_row(ln) for ln in align]
    if "min Σ" not in d["rows"][0] or "cap_a" not in d["rows"][2]:
        fail(f"canonical rows look wrong: {d['rows']}")
    res = ingest_latex(final_tex, source=f"{KEY}--feedback/final.tex")
    if not res.ok:
        fail("final.tex stopped ingesting — the 'valid' claim would be false")
    d["graph"] = schema_nx(res.formulation)
    d["gn"], d["ge"] = d["graph"].number_of_nodes(), d["graph"].number_of_edges()

    # -- citation by structure ----------------------------------------------
    rem = _jread(VD3 / "rematch.json")["runs"][KEY]
    if rem["isomorphic"]:
        fail(f"expected no isomorphic corpus twin, got {rem['isomorphic']}")
    top = rem["similar"][0]
    if top["id"] != EXPECT_MATCH or not 0.92 <= top["similarity"] < 0.94:
        fail(f"top structural match changed: {top}")
    d["cite_sim"] = top["similarity"]

    # -- top lane: the ablation run on the same scenario ---------------------
    ndir = VD3 / f"{KEY}--no_feedback"
    ntr = _transcript(ndir / "transcript.jsonl")
    samples = [ev for ev in ntr if ev.get("event") == "sample"]
    nend = next(ev for ev in ntr if ev.get("event") == "end")
    if len(samples) != 3 or nend["valid_rate"] != 0.0:
        fail(f"no_feedback run is not 0/3 ({nend})")
    codes: set = set()
    for ev in samples:
        if ev["verdict"] != "invalid":
            fail("a single-shot sample was not invalid")
        parse = next(f for f in ev["findings"] if f["code"] == "parse-failed")
        m = re.search(r"validation error for (\w+)\s+(\w+)", parse["detail"])
        codes.add((m.group(1), m.group(2)) if m else None)
        codes.update(f["code"] for f in ev["findings"] if f["level"] == "error")
    if ("ConstraintTemplate", "kind") not in codes:
        fail(f"expected the constraint-kind failure in all samples, got {codes}")
    if "all-parsers-failed" not in codes:
        fail("expected all-parsers-failed in the ablation findings")
    d["temp_nf"] = ntr[0]["temperature"]
    d["bad_line"] = next(
        ln
        for ln in samples[0]["output"].splitlines()
        if ln.startswith("%@ con") and "kind=-" in ln
    )
    return d


# ---------------------------------------------------------------------------
# Drawing helpers (visual language of example_figures, compact variant).
# ---------------------------------------------------------------------------


def card_height(lines, tag: bool, extra: float = 0.0) -> float:
    h = HEAD_H + 0.06 + extra + sum(LN[k][1] for k in (kind for kind, _ in lines))
    return h + (TAG_H if tag else 0.14)


def tag_pill(ax, x, y, tag: str, llm: bool, fs: float = 10.0) -> float:
    col = CD["orange"] if llm else CD["tuerkis"]
    fill = "#FAEDE4" if llm else "#E4F1F0"
    w = tw(tag, fs) + 0.26
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, 0.30,
            boxstyle="round,pad=0,rounding_size=0.12",
            linewidth=1.0, edgecolor=col, facecolor=fill, zorder=3,
        )
    )
    ax.text(x + w / 2, y + 0.155, tag, fontsize=fs, color=col,
            ha="center", va="center", zorder=4)
    return w


def card(ax, x, y, w, title, lines, tag=None, llm=False, extra=0.0,
         edge=GRID, face="white", tcolor=INK, dashed=False) -> float:
    """Compact stage card, top-left at (x, y); returns its bottom y.

    Every body line and the title are measured against the card width and
    the script refuses to draw an overflowing line (QC by construction).
    """
    inner = w - 2 * PAD_X
    if tw(title, 13.5, weight="bold") > inner + 0.04:
        fail(f"card title {title!r} overflows width {w}")
    for kind, text in lines:
        fs, _lh, fam, _c, wt, st = LN[kind]
        if text and tw(text, fs, fam, wt, st) > inner + 0.04:
            fail(f"line {text!r} ({kind}) overflows card {title!r} (w={w})")
    h = card_height(lines, tag is not None, extra)
    ax.add_patch(
        FancyBboxPatch(
            (x, y), w, h,
            boxstyle="round,pad=0,rounding_size=0.10",
            linewidth=1.6 if edge != GRID else 1.4,
            edgecolor=edge, facecolor=face,
            linestyle=(0, (4, 3)) if dashed else "solid", zorder=2,
        )
    )
    ax.text(x + PAD_X, y + HEAD_H / 2 + 0.02, title, fontsize=13.5,
            fontweight="bold", color=tcolor, ha="left", va="center", zorder=4)
    ax.plot([x + PAD_X * 0.7, x + w - PAD_X * 0.7], [y + HEAD_H, y + HEAD_H],
            color=edge if edge != GRID else GRID, linewidth=1.0, zorder=3)
    cy = y + HEAD_H + 0.06 + extra
    for kind, text in lines:
        fs, lh, fam, col, wt, st = LN[kind]
        if text:
            ax.text(x + PAD_X, cy + lh / 2, text, fontsize=fs, family=fam,
                    color=col, fontweight=wt, fontstyle=st,
                    ha="left", va="center", zorder=4)
        cy += lh
    if tag:
        tag_pill(ax, x + PAD_X, y + h - 0.38, tag, llm)
    return y + h


def harrow(ax, x0, x1, y, color, lw=1.8) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (x0 + 0.03, y), (x1 - 0.03, y),
            arrowstyle="-|>", mutation_scale=15, linewidth=lw,
            color=color, zorder=1,
        )
    )


def varrow(ax, x, y0, y1, color, lw=1.8) -> None:
    ax.add_patch(
        FancyArrowPatch(
            (x, y0 + 0.03), (x, y1 - 0.03),
            arrowstyle="-|>", mutation_scale=15, linewidth=lw,
            color=color, zorder=1,
        )
    )


def stat_pill(ax, xr, y, plain: str, strong: str, color, fill) -> None:
    """Right-anchored stat chip: plain text, then a pill with the number."""
    pw = tw(strong, 12.5, weight="bold") + 0.44
    px = xr - pw
    ax.add_patch(
        FancyBboxPatch(
            (px, y - 0.19), pw, 0.38,
            boxstyle="round,pad=0,rounding_size=0.16",
            linewidth=1.4, edgecolor=color, facecolor=fill, zorder=3,
        )
    )
    ax.text(px + pw / 2, y, strong, fontsize=12.5, fontweight="bold",
            color=color, ha="center", va="center", zorder=4)
    ax.text(px - 0.16, y, plain, fontsize=11.5, color=MUTED,
            ha="right", va="center", zorder=4)


# ---------------------------------------------------------------------------
# The figure.
# ---------------------------------------------------------------------------


def figure(d: dict) -> Path:
    W, H = 15.0, 8.3
    ef._style()
    fig = plt.figure(figsize=(W, H))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(H, 0)
    ax.set_aspect("equal")
    ax.axis("off")

    ax.text(0.4, 0.32, "One scenario, two processes: the verifier makes the difference",
            fontsize=19, fontweight="bold", color=INK, ha="left", va="center")

    # -- scenario strip (shared input of both lanes) ------------------------
    sx, sy, sw = 0.4, 0.60, 14.2
    ax.add_patch(
        FancyBboxPatch(
            (sx, sy), sw, 1.06,
            boxstyle="round,pad=0,rounding_size=0.10",
            linewidth=1.4, edgecolor=GRID, facecolor="white", zorder=2,
        )
    )
    ax.text(sx + PAD_X, sy + 0.26, "Scenario", fontsize=12.5, fontweight="bold",
            color=CD["dunkelblau"], ha="left", va="center", zorder=4)
    ax.text(sx + 1.30, sy + 0.26,
            f"“{d['title']}” ({d['year']}) — the paper's abstract, verbatim:",
            fontsize=12, color=INK, ha="left", va="center", zorder=4)
    for k, line in enumerate(ef._wrap(d["abstract"], 168, maxlines=2, indent="")):
        ax.text(sx + PAD_X, sy + 0.55 + 0.26 * k, line, fontsize=11.5,
                color=MUTED, style="italic", ha="left", va="center", zorder=4)

    # -- lane bands ---------------------------------------------------------
    bx, bw = 0.6, 14.0
    t0, t1 = 2.00, 4.10   # top band (without verifier)
    b0, b1 = 4.52, 7.88   # bottom band (with verifier)
    for y0, y1, fill in ((t0, t1, "#FBF4F1"), (b0, b1, "#EFF6F5")):
        ax.add_patch(
            FancyBboxPatch(
                (bx, y0), bw, y1 - y0,
                boxstyle="round,pad=0,rounding_size=0.14",
                linewidth=0, facecolor=fill, zorder=0,
            )
        )
    ax.text(bx + 0.25, t0 + 0.27, "WITHOUT the verifier — one shot",
            fontsize=13.5, fontweight="bold", color=CD["rot"],
            ha="left", va="center", zorder=4)
    stat_pill(ax, bx + bw - 0.18, t0 + 0.27,
              "3 samples × 10 scenarios · both models:",
              "single shot  0 / 30 valid", CD["rot"], "#FDF0F3")
    ax.text(bx + 0.25, b0 + 0.27, "WITH the deterministic verifier in the loop",
            fontsize=13.5, fontweight="bold", color=CD["tuerkis"],
            ha="left", va="center", zorder=4)
    stat_pill(ax, bx + bw - 0.18, b0 + 0.27,
              f"{d['model_short']} @ ScaDS · mean {d['mean_rounds']:.1f} rounds to valid:",
              f"{d['n_valid']} / {d['n_scen']} valid", CD["tuerkis"], "#E4F1F0")

    # contrast between the lanes
    ax.text(W / 2, (t1 + b0) / 2,
            "same scenario, same LLM — the only change: a deterministic verifier in the loop  ▼",
            fontsize=13, fontweight="bold", color=CD["dunkelblau"],
            ha="center", va="center", zorder=4)

    llm_tag = f"LLM ({d['model_short']})"

    # -- TOP lane: LLM -> draft -> invalid -> dead end ----------------------
    tm = (t0 + 0.52 + t1) / 2  # content mid
    x = 0.82
    lines = [("body", "one draft, no repair"), ("body", f"T = {d['temp_nf']}, 3 samples")]
    h = card_height(lines, True)
    top_llm_mid = tm  # scenario feed target
    card(ax, x, tm - h / 2, 2.35, "open-weights LLM", lines, tag=llm_tag, llm=True)
    harrow(ax, x + 2.35, x + 2.35 + 0.30, tm, DEAD)

    x = x + 2.35 + 0.30
    dw = 3.50
    lines = [
        ("mono", ef._trunc(d["bad_line"].split("::")[0].rstrip(), 33)),
        ("muted", "declaration element left '-'"),
    ]
    h = card_height(lines, False)
    card(ax, x, tm - h / 2, dw, "model draft", lines)
    harrow(ax, x + dw, x + dw + 0.30, tm, DEAD)

    x = x + dw + 0.30
    vw = 3.75
    lines = [
        ("bad", "✗ parse-failed: constraint kind '-'"),
        ("bad", "✗ all-parsers-failed  (3/3 samples)"),
    ]
    h = card_height(lines, True)
    card(ax, x, tm - h / 2, vw, "verdict: invalid", lines,
         tag="deterministic", edge="#E8B9C6")
    harrow(ax, x + vw, x + vw + 0.30, tm, DEAD)

    x = x + vw + 0.30
    lines = [("bold", "incomplete model"), ("body", "citation unknown")]
    h = card_height(lines, False)
    card(ax, x, tm - h / 2, 2.45, "dead end", lines, edge=CD["rot"],
         face="#FDF2F4", tcolor=CD["rot"], dashed=True)

    # -- BOTTOM lane --------------------------------------------------------
    ly0 = b0 + 0.50  # content top
    # loop block: LLM (top), verifier (below), rounds panel to the right;
    # findings flow back up through the channel between them.
    lx, lw_ = 0.82, 2.55
    lines_llm = [("body-s", f"drafts the model, T = {d['temp_fb']}")]
    h_llm = card_height(lines_llm, True)
    y_llm = ly0 + 0.08
    card(ax, lx, y_llm, lw_, "open-weights LLM", lines_llm, tag=llm_tag, llm=True)
    lines_ver = [("body-s", "parse → grammar → checks")]
    y_ver = y_llm + h_llm + 0.34
    card(ax, lx, y_ver, lw_, "lp2graph verifier", lines_ver, tag="deterministic")
    varrow(ax, lx + 0.55, y_llm + h_llm, y_ver, CD["tuerkis"])
    ax.text(lx + 0.66, y_llm + h_llm + 0.17, "draft", fontsize=10.5,
            color=MUTED, style="italic", ha="left", va="center", zorder=4)
    bot_llm_mid = y_llm + h_llm / 2  # scenario feed target

    # channel: findings fed back (verifier -> LLM), drawn orange
    chx = lx + lw_ + 0.21
    y_or = y_ver + 0.16
    ax.plot([lx + lw_, chx, chx], [y_or, y_or, y_llm + h_llm - 0.25],
            color=CD["orange"], linewidth=1.6, zorder=1)
    ax.add_patch(
        FancyArrowPatch(
            (chx, y_llm + h_llm - 0.25), (lx + lw_ + 0.03, y_llm + h_llm - 0.25),
            arrowstyle="-|>", mutation_scale=13, linewidth=1.6,
            color=CD["orange"], zorder=1,
        )
    )

    # rounds panel
    rx, rw = lx + lw_ + 0.38, 2.97
    chip_h = 0.36
    r_h = HEAD_H + 0.10 + 4 * chip_h + 0.14
    ry = ly0 + 0.24
    ax.add_patch(
        FancyBboxPatch(
            (rx, ry), rw, r_h,
            boxstyle="round,pad=0,rounding_size=0.10",
            linewidth=1.4, edgecolor=GRID, facecolor="white", zorder=2,
        )
    )
    ax.text(rx + PAD_X, ry + HEAD_H / 2 + 0.02, "repair rounds", fontsize=13.5,
            fontweight="bold", color=INK, ha="left", va="center", zorder=4)
    ax.plot([rx + PAD_X * 0.7, rx + rw - PAD_X * 0.7],
            [ry + HEAD_H, ry + HEAD_H], color=GRID, linewidth=1.0, zorder=3)
    for k, (label, ok) in enumerate(d["round_chips"]):
        if tw(label, 10.5, MONO) > rw - 0.58 - 0.10:
            fail(f"round chip label too wide: {label!r}")
        cy = ry + HEAD_H + 0.10 + k * chip_h + chip_h / 2
        col = CD["tuerkis"] if ok else CD["rot"]
        ax.add_patch(Circle((rx + PAD_X + 0.12, cy), 0.12, facecolor=col,
                            edgecolor="none", zorder=3))
        ax.text(rx + PAD_X + 0.12, cy, str(k + 1), color="white", fontsize=10,
                fontweight="bold", ha="center", va="center", zorder=4)
        ax.text(rx + PAD_X + 0.36, cy, label, fontsize=10.5, family=MONO,
                color=col, ha="left", va="center", zorder=4)
    # verifier -> rounds (the loop's ticker), rounds -> canonical model
    harrow(ax, lx + lw_, rx, ry + r_h - 0.30, CD["tuerkis"])
    ax.text(rx + rw / 2, ry + r_h + 0.17, "↺ findings fed back each round",
            fontsize=10.5, color=CD["orange"], style="italic",
            ha="center", va="center", zorder=4)

    bm = ry + r_h / 2  # station mid line of the rest of the lane
    harrow(ax, rx + rw, rx + rw + 0.30, bm, CD["tuerkis"])

    # canonical model (the 3 real align rows of final.tex)
    cx, cw = rx + rw + 0.28, 3.00
    lines_can = [("good", r) for r in d["rows"]] + [
        ("muted", "+ full %@ declaration block")
    ]
    h_can = card_height(lines_can, True)
    card(ax, cx, bm - h_can / 2, cw, "canonical model", lines_can,
         tag="valid (round 4)", edge=CD["tuerkis"])
    harrow(ax, cx + cw, cx + cw + 0.20, bm, CD["tuerkis"])

    # typed graph (real ingest of final.tex)
    gx, gw = cx + cw + 0.20, 1.70
    g_extra = 1.22
    lines_g = [("muted", f"{d['gn']} n / {d['ge']} e")]
    h_g = card_height(lines_g, False, extra=g_extra)
    gy = bm - h_g / 2
    card(ax, gx, gy, gw, "typed graph", lines_g, extra=g_extra)
    ef.draw_schema_graph(fig, gx + 0.10, gy + HEAD_H + 0.05, gw - 0.20,
                         g_extra - 0.02, d["graph"], W, H, node_size=40)
    harrow(ax, gx + gw, gx + gw + 0.34, bm, CD["tuerkis"])

    # citation box
    zx, zw = gx + gw + 0.34, 2.35
    lines_cite = [
        ("bold", "cites (LinTim):"),
        ("good", "vehicle-scheduling IP"),
        ("body-s", f"structural similarity {d['cite_sim']:.2f}"),
        ("muted", "isomorphic: none"),
    ]
    h_cite = card_height(lines_cite, True)
    card(ax, zx, bm - h_cite / 2, zw, "citation", lines_cite,
         tag="deterministic", edge=CD["tuerkis"], face="#F4FAF9")
    ax.text((gx + gw + zx) / 2, bm + (h_cite / 2) + 0.20,
            "structural matching (Level-M features)", fontsize=10, color=MUTED, style="italic",
            ha="center", va="center", zorder=4)

    # scenario feeds both lanes down the left margin (grey fork)
    mx = 0.42
    ax.plot([mx, mx], [sy + 1.08, bot_llm_mid], color=DEAD,
            linewidth=1.8, solid_capstyle="round", zorder=1)
    harrow(ax, mx, 0.82, top_llm_mid, DEAD)
    harrow(ax, mx, lx, bot_llm_mid, DEAD)

    # caption (wrapped and width-checked so it cannot stretch the bbox)
    caption = (
        f"Run {d['doi']} read live from corpus/vdemo3 (transcript, final.tex, rematch.json). "
        f"Ablation: 0/30 valid per model ({d['model_short']} and {d['model2_short']}); "
        "feedback stats merged from vdemo3 + vdemo3_retry; similarity = Level-M concept-feature cosine (rematch vs the canonical corpus)."
    )
    cap_lines = ef._wrap(caption, 190, maxlines=2, indent="")
    for k, line in enumerate(cap_lines):
        if tw(line, 10.5) > W - 0.8:
            fail(f"caption line too wide: {line[:60]!r}")
        ax.text(0.4, H - 0.34 + 0.18 * k, line, fontsize=10.5, color=MUTED,
                ha="left", va="center")

    ef.OUTDIR.mkdir(parents=True, exist_ok=True)
    png = ef.OUTDIR / "fig_vdemo_process.png"
    fig.savefig(png, dpi=300, bbox_inches="tight")
    fig.savefig(ef.OUTDIR / "fig_vdemo_process.svg", bbox_inches="tight",
                metadata={"Date": None})
    plt.close(fig)
    return png


def main() -> None:
    d = data()
    png = figure(d)
    print(f"wrote {png} and {png.with_suffix('.svg')}")
    for k, (label, ok) in enumerate(d["round_chips"], 1):
        print(f"  round {k}: {'valid' if ok else 'invalid'} — {label}")
    print(f"  citation: {EXPECT_MATCH} @ {d['cite_sim']:.4f}, isomorphic: none")


if __name__ == "__main__":
    main()
