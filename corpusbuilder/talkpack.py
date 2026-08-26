"""Presentation-grade corpus figures — the deterministic "talk pack".

Slides rot faster than corpora grow: every deck so far has re-derived its
numbers by hand from ``corpus/*`` artifacts, and every re-derivation is a
chance to contradict the paper. This module makes the figure layer as
reproducible as the corpus layer: one command reads the frozen corpus
artifacts (dossiers, ``resolution.json``, ``objective_flags.json``,
``prisma.json``, and, once they exist, ``promotion.json``, promoted
``Formulation``s and verifier-demo summaries) and regenerates every talk
figure plus the exact numbers under them. If a figure and the paper disagree,
one of them was not regenerated; there is no third source of truth.

Outputs (all under ``corpus/talkpack/``, gitignored — regenerable, delivered
via Telegram/SendUserFile, never committed):

* ``figures/<name>.png``  — 300 dpi, white background (paste into slides)
* ``figures/<name>.svg``  — same figure, vector (scale-free, date-stripped)
* ``numbers.json``        — every figure's underlying numbers + a flat headline dict
* ``RESULTS.md``          — one page: headline block + one caption line per figure

Design rules (chair CD, from the ``tud-mobile`` skill):
The palette is the chair's: Tuerkis ``#0A777F`` is the accent and means
"deterministic yield", Orange ``#C85000`` means "pending / HITL queue",
Rot ``#D20F41`` means "loss / excluded", mid blue ``#2F57B2`` is neutral
volume, Violett ``#7369BE`` a secondary segment, Dunkelblau ``#001450``
emphasis ink. The categorical order ``tuerkis, orange, midblau, rot,
violett`` was checked with the dataviz palette validator (CVD-safe adjacent
pairs; Gelb is excluded from series — too light on white — and every bar is
direct-labeled so no reading depends on hue alone). White background, base
font >= 14 pt so the back of the room can read it, no dual axes (paired
measures become stacked panels sharing x).

Graceful-skip contract: several inputs may not exist yet (``promotion.json``
until the first promote run, promoted formulations beyond the 10 seeds,
``corpus/vdemo/**/summary.json``). A figure whose input is missing prints one
``skip fig_<name>: <reason>`` line, returns ``None``, and is recorded as
skipped in ``numbers.json`` — the pack never fails because the corpus has not
reached a stage yet.

Formulation -> dossier mapping (fig_architectures): ``corpusbuilder.promote``
writes a promoted paper to ``corpus/formulations/<entry_id>.json`` with
``Formulation.id == entry_id == promote.entry_id_for(paper_key)``, i.e. the
dossier key (the ``corpus/dossiers/*.json`` filename stem) lower-cased with
any character outside ``[a-z0-9_.-]`` folded to ``-`` (verified against
``promote_paper``'s write block; the fold is injective over the keys on disk,
collisions are refused as ``id_conflict``). We therefore invert it by
computing ``entry_id_for(stem)`` for every dossier stem. The 10 hand-written
seed formulations (``lp_1_1_fixed_sequence`` etc.) match no dossier and are
excluded by construction — exactly the "real Formulations only" gate.

Verifier-demo summary contract (fig_vdemo): a run directory
``corpus/vdemo/<scenario>--<mode>/`` may carry a ``summary.json`` with keys
``scenario``, ``mode`` (``feedback`` vs anything else = no feedback), ``runs``
(or ``n``), ``valid`` (or a direct ``valid_rate``), and ``mean_rounds_to_valid``
(or a ``rounds_to_valid`` list). Missing keys fall back to the directory name;
directories without a summary are ignored.

Paper-check reuse: the per-paper heuristic chips come from
``corpusbuilder.game._paper_check`` (called, not reimplemented). Its input is
the game's internal payload row (``f[5]``/``f[7]``/``f[9]`` = symbols /
relation / objective flag), and the game's own row builder (``game._payload``)
is awkward standalone: it reads the corpus path hard-coded, runs split
detection and display repair the checks do not need, and prints progress. So
this module builds the minimal ten-slot rows from the same public primitives
(``extract_symbols``, ``is_objective_latex``) and hands them to the real
``_paper_check`` — one heuristic, no fork.

Run:  PYTHONPATH=. python3 -m corpusbuilder.talkpack [--out corpus/talkpack]
                                                     [--only fig1,fig3,...]
"""

from __future__ import annotations

# ruff: noqa: I001 — inside ``_real_formulations`` the ``railpminer._lp2graph``
# import is a *side effect* (it puts a sibling lp2graph checkout on
# ``sys.path``) and must run before the ``lp2graph`` imports. Import sorting
# would place third-party ``lp2graph`` first and break the module on any
# machine where lp2graph is not installed (house pattern, same as promote.py).

import argparse
import json
import statistics
from collections import Counter
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless + deterministic; must precede pyplot

import matplotlib.pyplot as plt
from matplotlib import colors as mcolors
from matplotlib.patches import FancyArrowPatch, Patch, Rectangle

from corpusbuilder.fingerprint import cluster_label
from corpusbuilder.game import (
    _paper_check,
    extract_symbols,
    is_objective_latex,
)

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"
RESOLUTION = CORPUS / "resolution.json"
OBJECTIVE_FLAGS = CORPUS / "objective_flags.json"
PRISMA = CORPUS / "prisma.json"
PROMOTION = CORPUS / "promotion.json"
FORMULATIONS = CORPUS / "formulations"
VDEMO = CORPUS / "vdemo"
FINGERPRINT = CORPUS / "fingerprint"
OUT_DEFAULT = CORPUS / "talkpack"

#: "more than 20 real Formulations" gate for the architecture/taxonomy figures.
MIN_REAL_FORMULATIONS = 21

# ---------------------------------------------------------------------------
# Chair CD palette (tud-mobile skill). Categorical order validated CVD-safe.
# ---------------------------------------------------------------------------
CD = {
    "tuerkis": "#0A777F",  # main accent: positive / deterministic yield
    "orange": "#C85000",  # pending / attention / HITL queue
    "midblau": "#2F57B2",  # neutral volume
    "rot": "#D20F41",  # loss / excluded
    "violett": "#7369BE",  # secondary segment
    "gelb": "#FFC700",  # spare (diagrams only; never a series colour on white)
    "dunkelblau": "#001450",  # emphasis ink
    "brillantblau": "#00008C",  # logo blue (unused in plots)
}
SERIES = (CD["tuerkis"], CD["orange"], CD["midblau"], CD["rot"], CD["violett"])
INK = "#14202b"
MUTED = "#5f6b76"
GRID = "#d9dee3"

_CATEGORY_COLOR = {
    # promotion failure categories (promote.CAUSES): findings about the source
    # in warm/violet, findings about us in neutral blue, success in accent.
    "extraction_error": CD["rot"],
    "outside_grammar": CD["violett"],
    "under_specified": CD["orange"],
    "pipeline_incomplete": CD["midblau"],
}


def _style() -> None:
    """Apply the slide style: white field, big type, recessive chrome."""
    plt.rcParams.update(
        {
            "figure.facecolor": "white",
            "axes.facecolor": "white",
            "savefig.facecolor": "white",
            "font.family": "DejaVu Sans",
            "font.size": 15,
            "axes.titlesize": 18,
            "axes.titleweight": "bold",
            "axes.titlecolor": INK,
            "axes.labelsize": 15,
            "axes.labelcolor": INK,
            "xtick.labelsize": 14,
            "ytick.labelsize": 14,
            "xtick.color": MUTED,
            "ytick.color": MUTED,
            "legend.fontsize": 14,
            "legend.frameon": False,
            "text.color": INK,
            "axes.edgecolor": GRID,
            "axes.linewidth": 1.0,
            "svg.hashsalt": "talkpack",  # stable SVG ids across runs
            "svg.fonttype": "none",  # text stays text: small + deterministic
        }
    )


def _despine(ax, left: bool = True) -> None:
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if not left:
        ax.spines["left"].set_visible(False)


def _save(fig, outdir: Path, name: str, *, tight: bool = True) -> Path:
    """Write ``figures/<name>.png`` (300 dpi) and ``.svg``; return the PNG path.

    ``tight=False`` keeps the figure's declared canvas exactly as drawn — used by
    the full-bleed 16:9 flow figure, whose whole point is that it drops onto a
    widescreen slide without a crop changing its aspect ratio.
    """
    figdir = Path(outdir) / "figures"
    figdir.mkdir(parents=True, exist_ok=True)
    bbox = "tight" if tight else None
    png = figdir / f"{name}.png"
    fig.savefig(png, dpi=300, bbox_inches=bbox)
    # Strip the date so a rerun over unchanged inputs is byte-identical.
    fig.savefig(figdir / f"{name}.svg", bbox_inches=bbox, metadata={"Date": None})
    plt.close(fig)
    return png


def _skip(name: str, reason: str) -> None:
    print(f"skip {name}: {reason}")
    return None


def _fmt(n: int | float) -> str:
    return f"{n:,}" if isinstance(n, int) else f"{n:,.1f}"


# ---------------------------------------------------------------------------
# Data layer: plain dicts from the corpus artifacts, sorted and stable.
# ---------------------------------------------------------------------------


def load_papers(dossier_dir: Path = DOSSIERS) -> list[dict]:
    """One flat record per dossier, in sorted-filename order.

    Reads the raw JSON rather than :class:`corpusbuilder.dossier.Dossier` —
    the figures need five scalar fields and the formula LaTeX strings, and a
    286-file pydantic validation pass buys nothing here.
    """
    papers: list[dict] = []
    for path in sorted(Path(dossier_dir).glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        src = raw.get("source") or {}
        formulas = raw.get("formulas") or []
        papers.append(
            {
                "key": path.stem,
                "year": src.get("year"),
                "venue": src.get("venue"),
                "publisher": src.get("publisher"),
                "cited_by": src.get("cited_by_count"),
                "n_formulas": len(formulas),
                "latex": [str(f.get("latex") or "") for f in formulas],
            }
        )
    return papers


def _included(papers: list[dict]) -> list[dict]:
    """A paper is included iff extraction yielded at least one formula."""
    return [p for p in papers if p["n_formulas"] > 0]


def data_timeline(papers: list[dict]) -> dict | None:
    inc = _included(papers)
    if not inc:
        return None
    dated = sorted(p["year"] for p in inc if p["year"])
    per_year = Counter(dated)
    return {
        "papers_included": len(inc),
        "papers_undated": len(inc) - len(dated),
        "span": [dated[0], dated[-1]] if dated else None,
        "per_year": {str(y): per_year[y] for y in sorted(per_year)},
    }


def data_formulas_by_year(papers: list[dict]) -> dict | None:
    inc = [p for p in _included(papers) if p["year"]]
    if not inc:
        return None
    per_year: Counter[int] = Counter()
    counts_by_year: dict[int, list[int]] = {}
    for p in inc:
        per_year[p["year"]] += p["n_formulas"]
        counts_by_year.setdefault(p["year"], []).append(p["n_formulas"])
    return {
        "formulas_total": sum(per_year.values()),
        "per_year": {str(y): per_year[y] for y in sorted(per_year)},
        "median_per_paper_by_year": {
            str(y): float(statistics.median(sorted(v))) for y, v in sorted(counts_by_year.items())
        },
    }


def data_structural_yield(resolution_path: Path = RESOLUTION) -> dict | None:
    path = Path(resolution_path)
    if not path.exists():
        return None
    res = json.loads(path.read_text(encoding="utf-8"))
    axes = res.get("structural_axes") or {}
    rows = [
        # (label, value, class) — class picks the colour and the legend entry.
        ("extracted", res.get("formulas"), "volume"),
        ("expression tree parses", axes.get("parses"), "axis"),
        ("single statement", axes.get("single"), "axis"),
        ("renders without repair", axes.get("renders"), "axis"),
        ("relation or min/max head", axes.get("statement"), "axis"),
        ("clean on all four axes", res.get("structurally_clean"), "clean"),
        ("fully symbol-resolved (beta = 1)", res.get("resolved"), "resolved"),
        ("ready (clean + beta = 1)", res.get("ready"), "resolved"),
    ]
    if any(v is None for _, v, _c in rows):
        return None
    return {
        "rows": [{"label": label, "value": int(v), "class": c} for label, v, c in rows],
        "formulas": int(res["formulas"]),
        "papers": int(res.get("papers", 0)),
        "prefilled_pairs": res.get("prefilled_pairs"),
        "symbol_pairs": res.get("symbol_pairs"),
    }


def data_objective_status(flags_path: Path = OBJECTIVE_FLAGS) -> dict | None:
    path = Path(flags_path)
    if not path.exists():
        return None
    counts = (json.loads(path.read_text(encoding="utf-8")).get("counts")) or {}
    if not counts:
        return None
    order = ["ok", "unmarked", "absent"]
    return {
        "counts": {k: int(counts.get(k, 0)) for k in order},
        "total": sum(int(counts.get(k, 0)) for k in order),
    }


def data_promotion(promotion_path: Path = PROMOTION) -> dict | None:
    path = Path(promotion_path)
    if not path.exists():
        return None
    rep = json.loads(path.read_text(encoding="utf-8"))
    causes = rep.get("failures_by_cause") or {}
    return {
        "papers_with_decisions": rep.get("papers_with_decisions", 0),
        "promoted": rep.get("promoted", 0),
        "failed": rep.get("failed", 0),
        "by_cause": {
            cause: {"papers": int(info.get("papers", 0)), "category": str(info.get("category", ""))}
            for cause, info in sorted(causes.items())
        },
        "by_category": dict(sorted((rep.get("failures_by_category") or {}).items())),
    }


def data_prisma_flow(prisma_path: Path = PRISMA) -> dict | None:
    """Flatten ``prisma.json`` into exactly the boxes the flow figure draws."""
    path = Path(prisma_path)
    if not path.exists():
        return None
    flow = (json.loads(path.read_text(encoding="utf-8")) or {}).get("flow") or {}
    ident = flow.get("identification") or {}
    elig = flow.get("retrieval_eligibility") or {}
    inc = flow.get("included") or {}
    hitl = inc.get("hitl_review") or {}
    excluded = elig.get("reports_excluded") or {}
    verdicts = [
        {"label": name, "count": int(hitl.get(name, 0) or 0)}
        for name in ("accepted", "corrected", "duplicate", "rejected", "unreviewed")
    ]
    return {
        "freeze_date": flow.get("freeze_date"),
        "db_records": int(ident.get("database_search_records", 0) or 0),
        "db_queries": int(ident.get("database_queries", 0) or 0),
        "db_duplicates": int(ident.get("duplicates_removed", 0) or 0),
        "db_unique": int(ident.get("database_unique_records", 0) or 0),
        "citation_records": int(ident.get("citation_search_records_identified", 0) or 0),
        "citation_recommended": int(ident.get("citation_search_recommended", 0) or 0),
        "citation_retrieved": int(elig.get("from_citation_arm", 0) or 0),
        "retrieved": int(elig.get("reports_retrieved", 0) or 0),
        "from_database_arm": int(elig.get("from_database_arm", 0) or 0),
        "excluded_total": int(elig.get("reports_excluded_total", 0) or 0),
        "excluded": {k: int(v or 0) for k, v in sorted(excluded.items())},
        "papers_included": int(inc.get("source_papers", 0) or 0),
        "formulas_total": int(inc.get("candidate_formulations", 0) or 0),
        "verdicts": verdicts,
    }


def _real_formulations(
    formulations_dir: Path, dossier_dir: Path
) -> tuple[list, dict[str, str], dict[str, int]] | None:
    """Load promoted Formulations and map them back to their dossiers.

    Returns ``(formulations, key_by_id, year_by_key)`` or ``None`` when the
    gate (> 20 real, dossier-backed Formulations) is not met. Import of
    lp2graph is deferred to here: the first four figures must not require it.
    """
    paths = sorted(Path(formulations_dir).glob("*.json"))
    if not paths:
        return None
    from railpminer import _lp2graph  # noqa: F401  (sibling-checkout shim)

    from lp2graph import load

    from corpusbuilder.promote import entry_id_for

    key_by_id: dict[str, str] = {}
    year_by_key: dict[str, int] = {}
    for dpath in sorted(Path(dossier_dir).glob("*.json")):
        key_by_id[entry_id_for(dpath.stem)] = dpath.stem
        raw = json.loads(dpath.read_text(encoding="utf-8"))
        year = (raw.get("source") or {}).get("year")
        if year:
            year_by_key[dpath.stem] = int(year)

    forms = []
    for path in paths:
        try:
            f = load(path)
        except Exception as exc:  # a broken file must not sink the talk pack
            print(f"note fig_architectures: could not load {path.name}: {exc!r}")
            continue
        if f.id in key_by_id:  # seeds match no dossier and drop out here
            forms.append(f)
    if len(forms) < MIN_REAL_FORMULATIONS:
        return None
    return forms, key_by_id, year_by_key


def data_architectures(
    formulations_dir: Path = FORMULATIONS, dossier_dir: Path = DOSSIERS
) -> dict | None:
    loaded = _real_formulations(formulations_dir, dossier_dir)
    if loaded is None:
        return None
    forms, key_by_id, year_by_key = loaded
    from lp2graph.mining.corpusmgr.dedup import schema_graph_hash

    groups: dict[str, list] = {}
    for f in forms:
        groups.setdefault(schema_graph_hash(f), []).append(f)

    families = []
    for h in sorted(groups):
        members = groups[h]
        papers = sorted({key_by_id[f.id] for f in members})
        if len(papers) < 2:
            continue
        years = sorted(year_by_key[k] for k in papers if k in year_by_key)
        families.append(
            {
                "hash": h,
                "n_formulations": len(members),
                "papers": papers,
                "years": years,
                "iso_verified": None,
            }
        )
    if not families:
        return {"n_real": len(forms), "families": []}

    # Hash collision says "same schema multiset"; the isomorphism report says
    # "actually the same graph". Verify where networkx is available.
    try:
        from lp2graph.mining.isomorphism.report import isomorphism_report

        report = isomorphism_report({fam["hash"]: groups[fam["hash"]] for fam in families})
        for fam in families:
            iso = report[fam["hash"]]
            classes = getattr(iso, "classes", None)
            n_classes = len(classes) if classes is not None else None
            fam["iso_verified"] = (n_classes == 1) if n_classes is not None else None
    except Exception as exc:  # optional dep (networkx) absent: report unverified
        print(f"note fig_architectures: isomorphism verification unavailable ({exc!r})")

    families.sort(key=lambda fam: (-len(fam["papers"]), fam["hash"]))
    return {"n_real": len(forms), "families": families}


def data_taxonomy(
    formulations_dir: Path = FORMULATIONS, dossier_dir: Path = DOSSIERS
) -> dict | None:
    loaded = _real_formulations(formulations_dir, dossier_dir)
    if loaded is None:
        return None
    forms, _key_by_id, _years = loaded
    try:
        from lp2graph.mining.cluster.taxonomy import induce

        tax = induce(forms)  # default ClusterConfig: agglomerative (no hdbscan here)
    except Exception as exc:  # clustering extras missing: skip, never crash the pack
        print(f"note fig_taxonomy: taxonomy induction unavailable ({exc!r})")
        return None

    def sizes(level) -> dict[str, int]:
        part = level.named_partition()
        return {name: len(part[name]) for name in sorted(part)}

    return {
        "n_real": len(forms),
        "summary": tax.summary(),
        "level_m": sizes(tax.level_m),
        "domain": sizes(tax.domain),
    }


def data_vdemo(vdemo_dir: Path = VDEMO) -> dict | None:
    paths = sorted(Path(vdemo_dir).rglob("summary.json")) if Path(vdemo_dir).exists() else []
    if not paths:
        return None
    scenarios: dict[str, dict[str, dict]] = {}
    for path in paths:
        raw = json.loads(path.read_text(encoding="utf-8"))
        dirname = path.parent.name
        scenario = str(raw.get("scenario") or dirname.split("--")[0])
        mode_raw = str(raw.get("mode") or (dirname.split("--")[1] if "--" in dirname else ""))
        arm = "feedback" if "feedback" in mode_raw.lower() else "single"
        runs = int(raw.get("runs") or raw.get("n") or 0)
        if "valid_rate" in raw:
            valid_rate = float(raw["valid_rate"])
        elif runs:
            valid_rate = float(raw.get("valid", 0)) / runs
        else:
            continue
        rounds = raw.get("mean_rounds_to_valid")
        if (
            rounds is None
            and isinstance(raw.get("rounds_to_valid"), list)
            and raw["rounds_to_valid"]
        ):
            rounds = statistics.mean(raw["rounds_to_valid"])
        scenarios.setdefault(scenario, {})[arm] = {
            "runs": runs,
            "valid_rate": round(valid_rate, 3),
            "mean_rounds_to_valid": round(float(rounds), 2) if rounds is not None else None,
        }
    if not scenarios:
        return None
    return {"scenarios": {k: scenarios[k] for k in sorted(scenarios)}}


def _fingerprint_inputs(fingerprint_dir: Path, dossier_dir: Path) -> tuple[dict, dict, dict] | None:
    """clusters.json + features.json + a key -> year map, or None if unbuilt.

    The fingerprint layer is its own artifact (``python3 -m
    corpusbuilder.fingerprint``); the talk pack only *reads* it, so a missing
    directory is the normal graceful-skip case, not an error.
    """
    clusters_path = Path(fingerprint_dir) / "clusters.json"
    features_path = Path(fingerprint_dir) / "features.json"
    if not clusters_path.exists() or not features_path.exists():
        return None
    clusters = json.loads(clusters_path.read_text(encoding="utf-8"))
    features = json.loads(features_path.read_text(encoding="utf-8"))
    years: dict[str, int | None] = {}
    for path in sorted(Path(dossier_dir).glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        years[path.stem] = (raw.get("source") or {}).get("year")
    return clusters, features, years


def data_fingerprint_families(
    fingerprint_dir: Path = FINGERPRINT, dossier_dir: Path = DOSSIERS
) -> dict | None:
    loaded = _fingerprint_inputs(fingerprint_dir, dossier_dir)
    if loaded is None:
        return None
    clusters, features, years = loaded
    fams = []
    undated = 0
    for c in clusters.get("clusters") or []:
        members = []
        for key in c["papers"]:
            year = years.get(key)
            if year is None:
                undated += 1
            members.append(
                {
                    "key": key,
                    "year": year,
                    "n_formulas": int((features["papers"].get(key) or {}).get("n_formulas") or 0),
                }
            )
        fams.append(
            {
                "id": c["id"],
                "label": cluster_label(c.get("top_features") or []),
                "size": c["size"],
                "top_features": c.get("top_features") or [],
                "papers": members,
            }
        )
    if not fams:
        return None
    return {
        "note": clusters.get("label", "pre-canonical structural fingerprints"),
        "k": clusters.get("k"),
        "silhouette": clusters.get("silhouette"),
        "n_papers": clusters.get("n_papers"),
        "undated": undated,
        "clusters": fams,
    }


#: Centered moving-sum window for the timeline shares. Raw per-year shares over
#: ~10 papers/year flicker too hard to read from the back of a room; a 3-year
#: window is still fully deterministic and is disclosed in the axis label.
_FP_WINDOW = 3


def data_fingerprint_timeline(
    fingerprint_dir: Path = FINGERPRINT, dossier_dir: Path = DOSSIERS
) -> dict | None:
    fams = data_fingerprint_families(fingerprint_dir, dossier_dir)
    if fams is None:
        return None
    dated_years = sorted(
        m["year"] for c in fams["clusters"] for m in c["papers"] if m["year"] is not None
    )
    if not dated_years:
        return None
    lo, hi = dated_years[0], dated_years[-1]
    span = list(range(lo, hi + 1))
    counts = {c["id"]: dict.fromkeys(span, 0) for c in fams["clusters"]}
    for c in fams["clusters"]:
        for m in c["papers"]:
            if m["year"] is not None:
                counts[c["id"]][m["year"]] += 1
    half = _FP_WINDOW // 2
    shares: dict[int, list[float]] = {cid: [] for cid in counts}
    for y in span:
        window = [w for w in range(y - half, y + half + 1) if lo <= w <= hi]
        totals = {cid: sum(counts[cid][w] for w in window) for cid in counts}
        grand = sum(totals.values())
        for cid in counts:
            shares[cid].append(round(totals[cid] / grand, 4) if grand else 0.0)
    return {
        "note": fams["note"],
        "k": fams["k"],
        "silhouette": fams["silhouette"],
        "window_years": _FP_WINDOW,
        "years": span,
        "labels": {c["id"]: c["label"] for c in fams["clusters"]},
        "sizes": {c["id"]: c["size"] for c in fams["clusters"]},
        "shares": shares,
    }


def paper_check_summary(papers: list[dict]) -> dict:
    """Corpus-level digest of ``game._paper_check`` over all included papers.

    Builds the minimal ten-slot payload rows the check reads (indices 2, 5, 7,
    9 = latex, symbols, relation, objective flag) from the game's own public
    primitives, then calls the real ``_paper_check`` — see the module
    docstring for why the game's full row builder is not used.
    """
    checks = []
    for p in _included(papers):
        rows = []
        for i, latex in enumerate(p["latex"]):
            syms, ops, rel = extract_symbols(latex)
            obj = 1 if is_objective_latex(latex) else 0
            rows.append([f"f{i:04d}", "", latex, "", 0, syms, ops, rel, 0, obj])
        checks.append(_paper_check(rows))
    if not checks:
        return {"papers_checked": 0}
    return {
        "papers_checked": len(checks),
        "complete": sum(c["comp"] for c in checks),
        "with_objective": sum(1 for c in checks if c["obj"] >= 1),
        "median_coherence": round(statistics.median(sorted(c["coh"] for c in checks)), 3),
    }


# ---------------------------------------------------------------------------
# Figures. Each: fig_<name>(outdir, ...) -> Path | None (None == skipped).
# ---------------------------------------------------------------------------


def _year_axis(ax, years: list[int]) -> None:
    lo, hi = min(years), max(years)
    step = 5 if hi - lo > 12 else 1
    first = lo - (lo % step)
    ax.set_xticks([y for y in range(first, hi + 1) if y % step == 0 and y >= lo])
    ax.set_xlim(lo - 0.8, hi + 0.8)


def fig_timeline(outdir: Path, dossier_dir: Path = DOSSIERS, data: dict | None = None):
    """Included papers per publication year, with cumulative growth below."""
    data = data or data_timeline(load_papers(dossier_dir))
    if not data or not data["per_year"]:
        return _skip("fig_timeline", "no included papers with a publication year")
    _style()
    years = sorted(int(y) for y in data["per_year"])
    counts = [data["per_year"][str(y)] for y in years]
    full = list(range(years[0], years[-1] + 1))
    per = {int(y): c for y, c in zip(years, counts, strict=True)}
    series = [per.get(y, 0) for y in full]
    cumulative = list(_running(series))

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 6.4), sharex=True, height_ratios=[3, 2], constrained_layout=True
    )
    ax1.bar(full, series, color=CD["midblau"], width=0.8)
    ax1.set_ylabel("papers / year")
    span = data["span"]
    ax1.set_title(
        f"Optimization papers in the corpus: N = {data['papers_included']}, {span[0]}–{span[1]}"
    )
    peak = max(series)
    peak_year = full[series.index(peak)]
    ax1.annotate(
        str(peak),
        (peak_year, peak),
        textcoords="offset points",
        xytext=(0, 4),
        ha="center",
        fontsize=14,
        color=INK,
    )
    ax2.plot(full, cumulative, color=CD["tuerkis"], linewidth=2.5)
    ax2.fill_between(full, cumulative, color=CD["tuerkis"], alpha=0.12, linewidth=0)
    ax2.set_ylabel("cumulative")
    ax2.annotate(
        f"{cumulative[-1]}",
        (full[-1], cumulative[-1]),
        textcoords="offset points",
        xytext=(-4, 6),
        ha="right",
        fontsize=14,
        fontweight="bold",
        color=CD["tuerkis"],
    )
    for ax in (ax1, ax2):
        _despine(ax)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    _year_axis(ax2, full)
    if data["papers_undated"]:
        ax2.set_xlabel(f"publication year ({data['papers_undated']} undated papers not shown)")
    else:
        ax2.set_xlabel("publication year")
    return _save(fig, outdir, "fig_timeline")


def _running(values: list[int]):
    total = 0
    for v in values:
        total += v
        yield total


def fig_formulas_by_year(outdir: Path, dossier_dir: Path = DOSSIERS, data: dict | None = None):
    """Extracted formulas per publication year + median formulas per paper."""
    data = data or data_formulas_by_year(load_papers(dossier_dir))
    if not data or not data["per_year"]:
        return _skip("fig_formulas_by_year", "no dated papers with formulas")
    _style()
    years = sorted(int(y) for y in data["per_year"])
    full = list(range(years[0], years[-1] + 1))
    per = {int(y): v for y, v in data["per_year"].items()}
    med = {int(y): v for y, v in data["median_per_paper_by_year"].items()}

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(11, 6.4), sharex=True, height_ratios=[3, 2], constrained_layout=True
    )
    ax1.bar(full, [per.get(y, 0) for y in full], color=CD["tuerkis"], width=0.8)
    ax1.set_ylabel("formulas / year")
    ax1.set_title(f"Modelling volume: {_fmt(data['formulas_total'])} extracted formulas")
    peak_year = max(full, key=lambda y: per.get(y, 0))
    ax1.annotate(
        _fmt(per[peak_year]),
        (peak_year, per[peak_year]),
        textcoords="offset points",
        xytext=(0, 4),
        ha="center",
        fontsize=14,
        color=INK,
    )
    med_years = sorted(med)
    ax2.plot(
        med_years,
        [med[y] for y in med_years],
        color=CD["orange"],
        linewidth=2,
        marker="o",
        markersize=5,
    )
    ax2.set_ylabel("median / paper")
    ax2.set_xlabel("publication year")
    ax2.set_ylim(bottom=0)
    for ax in (ax1, ax2):
        _despine(ax)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    _year_axis(ax2, full)
    return _save(fig, outdir, "fig_formulas_by_year")


_YIELD_COLOR = {
    "volume": CD["midblau"],
    "axis": CD["midblau"],
    "clean": CD["dunkelblau"],
    "resolved": CD["tuerkis"],
}
_YIELD_LEGEND = {
    "axis": "deterministic structural axis",
    "clean": "clean on all four axes",
    "resolved": "symbol-resolved (deterministic share)",
}


def fig_structural_yield(
    outdir: Path, resolution_path: Path = RESOLUTION, data: dict | None = None
):
    """Horizontal funnel over the deterministic axes down to beta = 1."""
    data = data or data_structural_yield(resolution_path)
    if not data:
        return _skip("fig_structural_yield", f"{resolution_path} missing or incomplete")
    _style()
    rows = data["rows"]
    total = data["formulas"]
    fig, ax = plt.subplots(figsize=(11, 5.6), constrained_layout=True)
    ypos = list(range(len(rows)))
    ax.barh(
        ypos,
        [r["value"] for r in rows],
        color=[_YIELD_COLOR[r["class"]] for r in rows],
        height=0.62,
    )
    ax.set_yticks(ypos, [r["label"] for r in rows])
    ax.invert_yaxis()
    for y, r in zip(ypos, rows, strict=True):
        pct = 100.0 * r["value"] / total if total else 0.0
        label = _fmt(r["value"]) if r["class"] == "volume" else f"{_fmt(r['value'])}  ({pct:.1f}%)"
        ax.annotate(
            label,
            (r["value"], y),
            textcoords="offset points",
            xytext=(6, 0),
            va="center",
            fontsize=14,
            color=INK,
        )
    ax.set_xlim(0, total * 1.22)
    ax.set_title(f"Deterministic structural yield over {_fmt(total)} extracted formulas")
    ax.set_xlabel("formulas")
    _despine(ax, left=False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    ax.legend(
        handles=[Patch(color=_YIELD_COLOR[c], label=lab) for c, lab in _YIELD_LEGEND.items()],
        loc="lower right",
    )
    return _save(fig, outdir, "fig_structural_yield")


_OBJ_META = {
    "ok": ("objective detected", CD["tuerkis"]),
    "unmarked": ("needs prose (HITL queue)", CD["orange"]),
    "absent": ("no min/max at all (screening)", CD["rot"]),
}


def fig_objective_status(
    outdir: Path, flags_path: Path = OBJECTIVE_FLAGS, data: dict | None = None
):
    """One stacked bar: where the deterministic objective detector stands."""
    data = data or data_objective_status(flags_path)
    if not data:
        return _skip("fig_objective_status", f"{flags_path} missing or empty")
    _style()
    counts, total = data["counts"], data["total"]
    fig, ax = plt.subplots(figsize=(11, 2.9), constrained_layout=True)
    left = 0
    n_outside = 0  # narrow segments get their label above/below alternately,
    for key in ("ok", "unmarked", "absent"):  # so adjacent labels never collide
        _label, color = _OBJ_META[key]
        v = counts.get(key, 0)
        ax.barh([0], [v], left=left, color=color, height=0.5, edgecolor="white", linewidth=2)
        pct = 100.0 * v / total if total else 0.0
        inside = v / total > 0.12 if total else False
        if inside:
            pos, va = (left + v / 2, 0), "center"
        else:
            above = n_outside % 2 == 0
            pos, va = (left + v / 2, 0.36 if above else -0.36), ("bottom" if above else "top")
            n_outside += 1
        ax.annotate(
            f"{v}  ({pct:.0f}%)",
            pos,
            ha="center",
            va=va,
            fontsize=14,
            fontweight="bold",
            color="white" if inside else INK,
        )
        left += v
    ax.set_xlim(0, total)
    ax.set_ylim(-0.75, 0.95)
    ax.set_yticks([])
    ax.set_title(f"Objective status across {total} included papers")
    ax.legend(
        handles=[Patch(color=c, label=lab) for lab, c in _OBJ_META.values()],
        loc="lower center",
        bbox_to_anchor=(0.5, -0.42),
        ncols=3,
    )
    _despine(ax, left=False)
    ax.set_xticks([])
    for side in ("bottom",):
        ax.spines[side].set_visible(False)
    return _save(fig, outdir, "fig_objective_status")


_VERDICT_COLOR = {
    "accepted": CD["tuerkis"],
    "corrected": CD["midblau"],
    "duplicate": CD["violett"],
    "rejected": CD["rot"],
    # Not a verdict but a remainder: neutral grey so it never reads as an outcome.
    "unreviewed": "#c2c9d1",
}
# Verdict labels alternate between two rows so the narrow segments never collide.
_VERDICT_ROW = {"accepted": 0, "corrected": 1, "duplicate": 0, "rejected": 1, "unreviewed": 0}

# Flow-figure geometry, in canvas units (1920 x 1080 = the 16:9 slide, y grows down).
_FW, _FH = 1920.0, 1080.0
_FMARGIN = 50.0
_FCOL_W = 413.0
_FCOL_X = (50.0, 519.0, 988.0, 1457.0)
_FBOX_H = 205.0
_FROW_Y = (205.0, 440.0)
_FARROW = "#9aa4af"


def _flow_box(ax, x, y, w, h, *, stage, number, details, color, fill="white", edge=GRID):
    """One flow box: a muted stage caption, the count, then at most three details."""
    ax.add_patch(Rectangle((x, y), w, h, facecolor=fill, edgecolor=edge, lw=1.4, zorder=2))
    ax.text(x + 26, y + 38, stage.upper(), fontsize=13, fontweight="bold", color=MUTED, zorder=3)
    ax.text(x + 26, y + 102, number, fontsize=40, fontweight="bold", color=color, zorder=3)
    for i, line in enumerate(details):
        ax.text(x + 26, y + 138 + 27 * i, line, fontsize=14, color=INK, zorder=3)


def _flow_arrow(ax, pts, *, color=_FARROW, dashed=False):
    """An orthogonal connector through ``pts`` with a single head at the end."""
    ax.plot(
        [p[0] for p in pts],
        [p[1] for p in pts],
        color=color,
        lw=2.0,
        solid_capstyle="round",
        linestyle=(0, (5, 4)) if dashed else "-",
        zorder=1,
    )
    ax.add_patch(
        FancyArrowPatch(
            pts[-2],
            pts[-1],
            arrowstyle="-|>",
            mutation_scale=20,
            color=color,
            lw=0.1,
            shrinkA=0,
            shrinkB=0,
            zorder=1,
        )
    )


def fig_prisma_flow(outdir: Path, prisma_path: Path = PRISMA, data: dict | None = None):
    """The whole corpus pipeline on one widescreen slide: PRISMA arms across the
    top, the review verdicts on the extracted formulas as one proportional bar
    below. Full-bleed 16:9 on white, every count read from ``prisma.json``."""
    d = data or data_prisma_flow(prisma_path)
    if not d:
        return _skip("fig_prisma_flow", f"{Path(prisma_path).name} not found")
    _style()
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.set_xlim(0, _FW)
    ax.set_ylim(_FH, 0)  # y grows downward: the figure is laid out like a page
    ax.set_axis_off()
    # A drawn white ground keeps the saved canvas at exactly 16:9.
    ax.add_patch(Rectangle((0, 0), _FW, _FH, facecolor="white", edgecolor="none", zorder=0))

    right = _FW - _FMARGIN
    ax.text(
        _FMARGIN,
        88,
        "Corpus construction: the PRISMA flow",
        fontsize=30,
        fontweight="bold",
        color=CD["dunkelblau"],
    )
    ax.text(
        _FMARGIN,
        124,
        "Published railway rescheduling optimization models, two identification arms. "
        f"Database arm frozen {d.get('freeze_date') or 'n/a'}.",
        fontsize=16,
        color=MUTED,
    )

    stages = ("1 · Identification", "2 · Retrieval & eligibility", "3 · Included", "4 · Extraction")
    for x, stage in zip(_FCOL_X, stages, strict=True):
        ax.text(x, 172, stage.upper(), fontsize=14, fontweight="bold", color=CD["dunkelblau"])
        ax.plot([x, x + _FCOL_W], [186, 186], color=GRID, lw=1.6, zorder=1)

    top, bottom = _FROW_Y
    mid = top + _FBOX_H / 2
    c1, c2, c3, c4 = _FCOL_X

    _flow_box(
        ax,
        c1,
        top,
        _FCOL_W,
        _FBOX_H,
        stage="Database search",
        number=_fmt(d["db_records"]),
        details=[
            f"{d['db_queries']} frozen keyword queries",
            f"−{d['db_duplicates']} duplicates, {d['db_unique']} unique",
            "all screened on abstract",
        ],
        color=CD["midblau"],
    )
    _flow_box(
        ax,
        c1,
        bottom,
        _FCOL_W,
        _FBOX_H,
        stage="Citation search",
        number=_fmt(d["citation_records"]),
        details=[
            "backward + forward sweep",
            f"{_fmt(d['citation_recommended'])} recommended",
            f"{d['citation_retrieved']} retrieved for mining",
        ],
        color=CD["midblau"],
    )
    _flow_box(
        ax,
        c2,
        top,
        _FCOL_W,
        _FBOX_H,
        stage="Reports retrieved",
        number=_fmt(d["retrieved"]),
        details=[
            "dossiers with full text",
            f"{d['from_database_arm']} database + {d['citation_retrieved']} cited",
        ],
        color=CD["tuerkis"],
    )
    _EXCL_LABEL = {
        "not_entitled": "no full-text access",
        "no_machine_readable_formulas": "no MathML formulas",
        "awaiting_tier3_pdf": "await Tier-3 PDF pass",
    }
    excl = d["excluded"]
    _flow_box(
        ax,
        c2,
        bottom,
        _FCOL_W,
        _FBOX_H,
        stage="Excluded at eligibility",
        number=_fmt(d["excluded_total"]),
        details=[f"{excl[k]} {_EXCL_LABEL[k]}" for k in _EXCL_LABEL if k in excl],
        color=CD["rot"],
        fill="#fdf3f5",
        edge="#f0ced8",
    )
    _flow_box(
        ax,
        c3,
        top,
        _FCOL_W,
        _FBOX_H,
        stage="Source papers included",
        number=_fmt(d["papers_included"]),
        details=[
            "at least one recoverable",
            "optimization formulation",
        ],
        color=CD["tuerkis"],
    )
    _flow_box(
        ax,
        c4,
        top,
        _FCOL_W,
        _FBOX_H,
        stage="Candidate formulations",
        number=_fmt(d["formulas_total"]),
        details=[
            "MathML → LaTeX records",
            "one per displayed equation",
        ],
        color=CD["tuerkis"],
    )

    junction = c2 - 28
    _flow_arrow(ax, [(c1 + _FCOL_W, mid), (c2, mid)])
    _flow_arrow(
        ax,
        [
            (c1 + _FCOL_W, bottom + _FBOX_H / 2),
            (junction, bottom + _FBOX_H / 2),
            (junction, mid),
            (c2, mid),
        ],
    )
    _flow_arrow(ax, [(c2 + _FCOL_W, mid), (c3, mid)])
    _flow_arrow(ax, [(c3 + _FCOL_W, mid), (c4, mid)])
    _flow_arrow(ax, [(c2 + _FCOL_W / 2, top + _FBOX_H), (c2 + _FCOL_W / 2, bottom)])

    # ---- the review band -------------------------------------------------
    band_y, bar_y, bar_h = 720.0, 762.0, 70.0
    total = d["formulas_total"] or 1
    ax.text(
        _FMARGIN,
        band_y - 12,
        f"5 · HUMAN REVIEW — ONE VERDICT PER EXTRACTED FORMULATION ({_fmt(total)})",
        fontsize=14,
        fontweight="bold",
        color=CD["dunkelblau"],
    )
    ax.plot([_FMARGIN, right], [band_y, band_y], color=GRID, lw=1.6, zorder=1)
    _flow_arrow(
        ax,
        [(c4 + _FCOL_W / 2, top + _FBOX_H), (c4 + _FCOL_W / 2, band_y - 44)],
        dashed=True,
    )

    span = right - _FMARGIN
    cursor = _FMARGIN
    rows = (866.0, 912.0)
    for v in d["verdicts"]:
        name, count = v["label"], v["count"]
        w = span * count / total
        colour = _VERDICT_COLOR[name]
        ax.add_patch(
            Rectangle(
                (cursor, bar_y), w, bar_h, facecolor=colour, edgecolor="white", lw=1.0, zorder=2
            )
        )
        centre = cursor + w / 2
        if w > 44:
            ax.text(
                centre,
                bar_y + bar_h / 2 + 8,
                _fmt(count),
                fontsize=20,
                fontweight="bold",
                ha="center",
                color="white" if name != "unreviewed" else INK,
                zorder=3,
            )
        row = rows[_VERDICT_ROW[name]]
        last = name == d["verdicts"][-1]["label"]
        tick_x = min(centre, right - 6) if not last else cursor + w / 2
        ax.plot([tick_x, tick_x], [bar_y + bar_h, row - 18], color="#c7ced6", lw=1.2, zorder=1)
        ax.text(
            right if last else centre,
            row,
            f"{name}  {100 * count / total:.1f}%",
            fontsize=17,
            color=INK,
            ha="right" if last else "center",
            zorder=3,
        )
        cursor += w

    ax.text(
        _FMARGIN,
        972,
        "“Corrected” formulations enter the corpus with the reviewer's LaTeX; "
        "“duplicate” marks a re-print of a formulation already held.",
        fontsize=14,
        color=MUTED,
    )
    ax.text(
        _FMARGIN,
        1024,
        "Derived from corpus/prisma.json (corpusbuilder.prisma) — the single source of "
        "truth for every count in the paper's PRISMA diagram.",
        fontsize=13,
        color=MUTED,
    )
    return _save(fig, outdir, "fig_prisma_flow", tight=False)


def fig_promotion(outdir: Path, promotion_path: Path = PROMOTION, data: dict | None = None):
    """Promotion outcomes: promoted papers vs failures by cause and category."""
    data = data or data_promotion(promotion_path)
    if not data:
        return _skip("fig_promotion", f"{promotion_path} missing (no promote run yet)")
    _style()
    causes = sorted(
        data["by_cause"].items(), key=lambda kv: (kv[1]["category"], -kv[1]["papers"], kv[0])
    )
    labels = ["promoted"] + [c for c, _ in causes]
    values = [data["promoted"]] + [info["papers"] for _, info in causes]
    colors = [CD["tuerkis"]] + [
        _CATEGORY_COLOR.get(info["category"], CD["violett"]) for _, info in causes
    ]
    fig, ax = plt.subplots(figsize=(11, 1.4 + 0.55 * len(labels)), constrained_layout=True)
    ypos = list(range(len(labels)))
    ax.barh(ypos, values, color=colors, height=0.62)
    ax.set_yticks(ypos, labels)
    ax.invert_yaxis()
    for y, v in zip(ypos, values, strict=True):
        ax.annotate(
            str(v),
            (v, y),
            textcoords="offset points",
            xytext=(6, 0),
            va="center",
            fontsize=14,
            color=INK,
        )
    seen = sorted({info["category"] for _, info in causes})
    handles = [Patch(color=CD["tuerkis"], label="promoted")] + [
        Patch(color=_CATEGORY_COLOR.get(cat, CD["violett"]), label=cat.replace("_", " "))
        for cat in seen
    ]
    ax.legend(handles=handles, loc="lower right")
    ax.set_title(
        f"Promotion outcomes: {data['promoted']} promoted, "
        f"{data['failed']} failed of {data['papers_with_decisions']} papers with decisions"
    )
    ax.set_xlabel("papers")
    _despine(ax, left=False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return _save(fig, outdir, "fig_promotion")


def fig_architectures(
    outdir: Path,
    formulations_dir: Path = FORMULATIONS,
    dossier_dir: Path = DOSSIERS,
    data: dict | None = None,
):
    """Architecture families: identical schema graphs across papers over time."""
    data = data or data_architectures(formulations_dir, dossier_dir)
    if data is None:
        return _skip(
            "fig_architectures",
            f"fewer than {MIN_REAL_FORMULATIONS} real (dossier-backed) formulations "
            f"in {formulations_dir}",
        )
    families = [fam for fam in data["families"] if fam["years"]]
    if not families:
        return _skip("fig_architectures", "no cross-paper architecture families yet")
    _style()
    fig, ax = plt.subplots(figsize=(11, 1.6 + 0.6 * len(families)), constrained_layout=True)
    labels = []
    for row, fam in enumerate(families):
        years = fam["years"]
        ax.plot([min(years), max(years)], [row, row], color=GRID, linewidth=1.5, zorder=1)
        ax.scatter(
            years,
            [row] * len(years),
            s=110,
            color=CD["tuerkis"],
            zorder=2,
            edgecolors="white",
            linewidths=1.5,
        )
        tick = " (iso verified)" if fam["iso_verified"] else ""
        labels.append(f"{fam['hash'][:8]} · {len(fam['papers'])} papers{tick}")
    ax.set_yticks(range(len(families)), labels)
    ax.invert_yaxis()
    ax.set_xlabel("publication year of the member paper")
    ax.set_title(
        f"Architecture families: same schema graph across papers (n = {data['n_real']} models)"
    )
    _despine(ax, left=False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return _save(fig, outdir, "fig_architectures")


def _treemap_strip(ax, sizes: dict[str, int], row_y: float, ramp_to: str) -> None:
    """One horizontal strip of proportional rectangles, sequential ramp by size."""
    total = sum(sizes.values()) or 1
    ordered = sorted(sizes.items(), key=lambda kv: (-kv[1], kv[0]))
    biggest = ordered[0][1]
    x = 0.0
    base = mcolors.to_rgb(ramp_to)
    for name, size in ordered:
        width = size / total
        share = size / biggest
        color = tuple(1 - (1 - c) * (0.30 + 0.70 * share) for c in base)
        ax.add_patch(
            Rectangle((x, row_y), width, 0.34, facecolor=color, edgecolor="white", linewidth=2)
        )
        if width > 0.07:
            lum = 0.299 * color[0] + 0.587 * color[1] + 0.114 * color[2]
            ax.annotate(
                f"{name}\n{size}",
                (x + width / 2, row_y + 0.17),
                ha="center",
                va="center",
                fontsize=13,
                color="white" if lum < 0.55 else INK,
            )
        x += width


def fig_taxonomy(
    outdir: Path,
    formulations_dir: Path = FORMULATIONS,
    dossier_dir: Path = DOSSIERS,
    data: dict | None = None,
):
    """Induced taxonomy, two levels: model clusters over domain clusters."""
    data = data or data_taxonomy(formulations_dir, dossier_dir)
    if data is None:
        return _skip(
            "fig_taxonomy",
            f"fewer than {MIN_REAL_FORMULATIONS} real formulations, or clustering unavailable",
        )
    _style()
    fig, ax = plt.subplots(figsize=(11, 4.2), constrained_layout=True)
    _treemap_strip(ax, data["level_m"], 0.56, CD["tuerkis"])
    _treemap_strip(ax, data["domain"], 0.06, CD["midblau"])
    ax.annotate("model level (M)", (0, 0.94), fontsize=14, color=MUTED)
    ax.annotate("domain level", (0, 0.44), fontsize=14, color=MUTED)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1.02)
    ax.axis("off")
    s = data["summary"]
    ax.set_title(
        f"Induced taxonomy over {data['n_real']} models: "
        f"{s.get('M', '?')} model clusters, {s.get('domain', '?')} domain clusters"
    )
    return _save(fig, outdir, "fig_taxonomy")


def fig_vdemo(outdir: Path, vdemo_dir: Path = VDEMO, data: dict | None = None):
    """Verifier demo A/B: valid rate and rounds-to-valid, with vs without feedback."""
    data = data or data_vdemo(vdemo_dir)
    if not data:
        return _skip("fig_vdemo", f"no summary.json under {vdemo_dir}")
    _style()
    scenarios = sorted(data["scenarios"])
    arms = [
        ("feedback", "with verifier feedback", CD["tuerkis"]),
        ("single", "without", CD["midblau"]),
    ]
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(11, 4.6), constrained_layout=True)
    width = 0.38
    xs = list(range(len(scenarios)))
    for k, (arm, label, color) in enumerate(arms):
        offs = [x + (k - 0.5) * width for x in xs]
        rates = [data["scenarios"][s].get(arm, {}).get("valid_rate") for s in scenarios]
        ax1.bar(
            offs,
            [r if r is not None else 0 for r in rates],
            width=width * 0.94,
            color=color,
            label=label,
        )
        for x, r in zip(offs, rates, strict=True):
            if r is not None:
                ax1.annotate(
                    f"{100 * r:.0f}%",
                    (x, r),
                    textcoords="offset points",
                    xytext=(0, 3),
                    ha="center",
                    fontsize=13,
                    color=INK,
                )
        rounds = [data["scenarios"][s].get(arm, {}).get("mean_rounds_to_valid") for s in scenarios]
        ax2.bar(
            offs,
            [r if r is not None else 0 for r in rounds],
            width=width * 0.94,
            color=color,
        )
        for x, r in zip(offs, rounds, strict=True):
            if r is not None:
                ax2.annotate(
                    f"{r:.1f}",
                    (x, r),
                    textcoords="offset points",
                    xytext=(0, 3),
                    ha="center",
                    fontsize=13,
                    color=INK,
                )
    ax1.set_ylim(0, 1.12)
    ax1.set_ylabel("valid rate")
    ax1.set_title("Valid rate")
    ax2.set_ylabel("mean rounds to valid")
    ax2.set_title("Rounds to valid")
    for ax in (ax1, ax2):
        ax.set_xticks(xs, scenarios)
        _despine(ax)
        ax.grid(axis="y", color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
    ax1.legend(loc="upper right")
    return _save(fig, outdir, "fig_vdemo")


def _fp_colors(k: int) -> list:
    """A deterministic colour per cluster: the CD series, lightened per cycle.

    The lightening factor compounds (0.55 ** cycle), otherwise the third
    cycle would repeat the second one's colours exactly and C5/C10 would be
    indistinguishable in a k = 11 plot.
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


def fig_fingerprint_families(
    outdir: Path,
    fingerprint_dir: Path = FINGERPRINT,
    dossier_dir: Path = DOSSIERS,
    data: dict | None = None,
):
    """Fingerprint clusters as rows, one dot per paper at its publication year.

    The corpus-scale version of the architecture-families slide: it does not
    wait for canonical promotion, so it is explicitly labeled pre-canonical.
    """
    data = data or data_fingerprint_families(fingerprint_dir, dossier_dir)
    if data is None:
        return _skip(
            "fig_fingerprint_families",
            f"no fingerprint artifacts under {fingerprint_dir} "
            "(run python3 -m corpusbuilder.fingerprint first)",
        )
    _style()
    fams = data["clusters"]
    colors = _fp_colors(len(fams))
    fig, ax = plt.subplots(figsize=(11, 1.9 + 0.62 * len(fams)), constrained_layout=True)
    labels = []
    all_years: list[int] = []
    for row, (fam, color) in enumerate(zip(fams, colors, strict=True)):
        pts = [m for m in fam["papers"] if m["year"] is not None]
        years = [m["year"] for m in pts]
        all_years += years
        if years:
            ax.plot([min(years), max(years)], [row, row], color=GRID, linewidth=1.5, zorder=1)
        ax.scatter(
            years,
            [row] * len(pts),
            s=[26 + 3.2 * m["n_formulas"] for m in pts],
            color=[color],
            alpha=0.75,
            zorder=2,
            edgecolors="white",
            linewidths=1.0,
        )
        labels.append(f"C{fam['id']} · {fam['label']} · {fam['size']}")
    ax.set_yticks(range(len(fams)), labels)
    ax.invert_yaxis()
    undated = data.get("undated") or 0
    ax.set_xlabel(
        "publication year (dot area ~ formulas per paper"
        + (f"; {undated} undated papers not shown)" if undated else ")")
    )
    ax.set_title(
        f"Architecture families over {data['n_papers']} papers: pre-canonical "
        f"structural fingerprints (k = {data['k']}, silhouette = {data['silhouette']:.2f})"
    )
    _despine(ax, left=False)
    ax.tick_params(axis="y", length=0)
    ax.grid(axis="x", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    if all_years:
        _year_axis(ax, all_years)
    return _save(fig, outdir, "fig_fingerprint_families")


def fig_fingerprint_timeline(
    outdir: Path,
    fingerprint_dir: Path = FINGERPRINT,
    dossier_dir: Path = DOSSIERS,
    data: dict | None = None,
):
    """Stacked shares of the fingerprint families per publication year."""
    data = data or data_fingerprint_timeline(fingerprint_dir, dossier_dir)
    if data is None:
        return _skip(
            "fig_fingerprint_timeline",
            f"no fingerprint artifacts under {fingerprint_dir} "
            "(run python3 -m corpusbuilder.fingerprint first)",
        )
    _style()
    years = data["years"]
    cids = sorted(data["shares"], key=int)
    colors = _fp_colors(len(cids))
    fig, ax = plt.subplots(figsize=(12.5, 5.6), constrained_layout=True)
    ax.stackplot(
        years,
        [data["shares"][cid] for cid in cids],
        colors=colors,
        alpha=0.85,
        linewidth=0.6,
        edgecolor="white",
    )
    handles = [
        Patch(
            facecolor=color,
            label=f"C{cid} · {data['labels'][cid]} ({data['sizes'][cid]})",
        )
        for cid, color in zip(cids, colors, strict=True)
    ]
    ax.legend(handles=handles, loc="center left", bbox_to_anchor=(1.01, 0.5), fontsize=12)
    ax.set_ylim(0, 1)
    ax.set_ylabel("share of papers")
    ax.set_xlabel(f"publication year ({data['window_years']}-year centered window)")
    ax.set_title(
        f"Evolution of architecture families: pre-canonical structural "
        f"fingerprints (k = {data['k']})"
    )
    _despine(ax)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    _year_axis(ax, years)
    return _save(fig, outdir, "fig_fingerprint_timeline")


# ---------------------------------------------------------------------------
# The pack: registry, numbers.json, RESULTS.md, CLI.
# ---------------------------------------------------------------------------

_ALIASES = {
    "fig0": ("prisma_flow",),
    "fig1": ("timeline",),
    "fig2": ("formulas_by_year",),
    "fig3": ("structural_yield",),
    "fig4": ("objective_status",),
    "fig5": ("promotion",),
    "fig6": ("architectures", "taxonomy"),
    "fig7": ("vdemo",),
    "fig8": ("fingerprint_families", "fingerprint_timeline"),
}
_ORDER = [
    "prisma_flow",
    "timeline",
    "formulas_by_year",
    "structural_yield",
    "objective_status",
    "promotion",
    "architectures",
    "taxonomy",
    "vdemo",
    "fingerprint_families",
    "fingerprint_timeline",
]


def _select(only: str | None) -> list[str]:
    if not only:
        return list(_ORDER)
    picked: set[str] = set()
    for token in only.split(","):
        token = token.strip().lower()
        if not token:
            continue
        if token in _ALIASES:
            picked.update(_ALIASES[token])
        elif token in _ORDER:
            picked.add(token)
        elif token.removeprefix("fig_") in _ORDER:
            picked.add(token.removeprefix("fig_"))
        else:
            raise SystemExit(f"unknown figure selector: {token!r} (use fig0..fig8 or names)")
    return [name for name in _ORDER if name in picked]


def _caption(name: str, numbers: dict) -> str:
    """A paste-ready slide caption per figure, from the figure's own numbers."""
    n = numbers.get(name) or {}
    if name == "prisma_flow":
        verdicts = {v["label"]: v["count"] for v in n.get("verdicts", [])}
        return (
            f"PRISMA flow of the corpus: {_fmt(n.get('db_records', 0))} database records and "
            f"{_fmt(n.get('citation_records', 0))} citation-search neighbours yield "
            f"{n.get('retrieved', 0)} retrieved reports, {n.get('excluded_total', 0)} excluded at "
            f"eligibility, {n.get('papers_included', 0)} papers included and "
            f"{_fmt(n.get('formulas_total', 0))} candidate formulations, of which "
            f"{_fmt(verdicts.get('accepted', 0))} are accepted and "
            f"{_fmt(verdicts.get('corrected', 0))} corrected in review."
        )
    if name == "timeline":
        span = n.get("span") or ["?", "?"]
        return (
            f"Included papers per publication year with cumulative growth: "
            f"{n.get('papers_included', '?')} papers, {span[0]} to {span[1]}."
        )
    if name == "formulas_by_year":
        return (
            f"Extracted candidate formulas per publication year "
            f"({_fmt(n.get('formulas_total', 0))} in total); the lower panel gives the "
            f"median formulas per paper."
        )
    if name == "structural_yield":
        rows = {r["label"]: r["value"] for r in n.get("rows", [])}
        return (
            f"Deterministic structural yield: of {_fmt(n.get('formulas', 0))} extracted "
            f"formulas, {_fmt(rows.get('clean on all four axes', 0))} are clean on all four "
            f"deterministic axes; {_fmt(rows.get('fully symbol-resolved (beta = 1)', 0))} are "
            f"fully symbol-resolved and {_fmt(rows.get('ready (clean + beta = 1)', 0))} ready "
            f"for promotion."
        )
    if name == "objective_status":
        c = n.get("counts", {})
        return (
            f"Objective detection over {n.get('total', 0)} included papers: "
            f"{c.get('ok', 0)} detected deterministically, {c.get('unmarked', 0)} need the "
            f"paper's prose (HITL queue), {c.get('absent', 0)} carry no optimization head "
            f"(screening candidates)."
        )
    if name == "promotion":
        return (
            f"Promotion outcomes: {n.get('promoted', 0)} papers promoted to canonical "
            f"formulations, {n.get('failed', 0)} routed by failure cause."
        )
    if name == "architectures":
        return (
            f"Architecture families over {n.get('n_real', 0)} promoted models: "
            f"{len(n.get('families', []))} schema graphs recur across papers over time."
        )
    if name == "taxonomy":
        s = n.get("summary", {})
        return (
            f"Induced taxonomy over {n.get('n_real', 0)} models: {s.get('M', '?')} model-level "
            f"clusters and {s.get('domain', '?')} domain clusters."
        )
    if name == "vdemo":
        return (
            "Verifier demo A/B: valid rate and mean rounds to valid, with and without "
            "structural verifier feedback."
        )
    if name == "fingerprint_families":
        return (
            f"Pre-canonical structural fingerprints over {n.get('n_papers', '?')} papers: "
            f"k = {n.get('k', '?')} architecture families (silhouette "
            f"{n.get('silhouette', '?')}), one dot per paper at its publication year."
        )
    if name == "fingerprint_timeline":
        return (
            f"Evolution of the k = {n.get('k', '?')} pre-canonical fingerprint families: "
            f"stacked share of papers per publication year "
            f"({n.get('window_years', '?')}-year centered window)."
        )
    return ""


def run(
    out: Path = OUT_DEFAULT,
    only: str | None = None,
    *,
    dossier_dir: Path = DOSSIERS,
    resolution_path: Path = RESOLUTION,
    flags_path: Path = OBJECTIVE_FLAGS,
    prisma_path: Path = PRISMA,
    promotion_path: Path = PROMOTION,
    formulations_dir: Path = FORMULATIONS,
    vdemo_dir: Path = VDEMO,
    fingerprint_dir: Path = FINGERPRINT,
) -> dict:
    """Build the pack; return {"rendered": [...], "skipped": {...}, "out": str}."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    selected = _select(only)
    papers = load_papers(dossier_dir)

    numbers: dict[str, dict | None] = {}
    if "prisma_flow" in selected:
        numbers["prisma_flow"] = data_prisma_flow(prisma_path)
    if "timeline" in selected:
        numbers["timeline"] = data_timeline(papers)
    if "formulas_by_year" in selected:
        numbers["formulas_by_year"] = data_formulas_by_year(papers)
    if "structural_yield" in selected:
        numbers["structural_yield"] = data_structural_yield(resolution_path)
    if "objective_status" in selected:
        numbers["objective_status"] = data_objective_status(flags_path)
    if "promotion" in selected:
        numbers["promotion"] = data_promotion(promotion_path)
    if "architectures" in selected:
        numbers["architectures"] = data_architectures(formulations_dir, dossier_dir)
    if "taxonomy" in selected:
        numbers["taxonomy"] = data_taxonomy(formulations_dir, dossier_dir)
    if "vdemo" in selected:
        numbers["vdemo"] = data_vdemo(vdemo_dir)
    if "fingerprint_families" in selected:
        numbers["fingerprint_families"] = data_fingerprint_families(fingerprint_dir, dossier_dir)
    if "fingerprint_timeline" in selected:
        numbers["fingerprint_timeline"] = data_fingerprint_timeline(fingerprint_dir, dossier_dir)

    figures = {
        "prisma_flow": lambda: fig_prisma_flow(out, prisma_path, data=numbers.get("prisma_flow")),
        "timeline": lambda: fig_timeline(out, dossier_dir, data=numbers.get("timeline")),
        "formulas_by_year": lambda: fig_formulas_by_year(
            out, dossier_dir, data=numbers.get("formulas_by_year")
        ),
        "structural_yield": lambda: fig_structural_yield(
            out, resolution_path, data=numbers.get("structural_yield")
        ),
        "objective_status": lambda: fig_objective_status(
            out, flags_path, data=numbers.get("objective_status")
        ),
        "promotion": lambda: fig_promotion(out, promotion_path, data=numbers.get("promotion")),
        "architectures": lambda: fig_architectures(
            out, formulations_dir, dossier_dir, data=numbers.get("architectures")
        ),
        "taxonomy": lambda: fig_taxonomy(
            out, formulations_dir, dossier_dir, data=numbers.get("taxonomy")
        ),
        "vdemo": lambda: fig_vdemo(out, vdemo_dir, data=numbers.get("vdemo")),
        "fingerprint_families": lambda: fig_fingerprint_families(
            out, fingerprint_dir, dossier_dir, data=numbers.get("fingerprint_families")
        ),
        "fingerprint_timeline": lambda: fig_fingerprint_timeline(
            out, fingerprint_dir, dossier_dir, data=numbers.get("fingerprint_timeline")
        ),
    }

    rendered: list[str] = []
    skipped: dict[str, str] = {}
    for name in selected:
        path = figures[name]()
        if path is None:
            skipped[name] = "input missing or below gate (see the skip line above)"
        else:
            rendered.append(name)
            print(f"wrote {path}")

    checks = paper_check_summary(papers)
    headline = _headline(papers, checks, resolution_path, flags_path, prisma_path)

    payload = {
        "generated_by": "corpusbuilder.talkpack",
        "headline": headline,
        "figures": {
            name: (numbers.get(name) if name in rendered else {"skipped": skipped[name]})
            for name in selected
        },
    }
    (out / "numbers.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (out / "RESULTS.md").write_text(
        _results_md(headline, selected, rendered, skipped, numbers), encoding="utf-8"
    )
    print(f"wrote {out / 'numbers.json'}")
    print(f"wrote {out / 'RESULTS.md'}")
    return {"rendered": rendered, "skipped": skipped, "out": str(out)}


def _headline(
    papers: list[dict],
    checks: dict,
    resolution_path: Path,
    flags_path: Path,
    prisma_path: Path,
) -> dict:
    """The flat headline dict: every number a slide is allowed to quote."""
    inc = _included(papers)
    years = sorted(p["year"] for p in inc if p["year"])
    head: dict = {
        "dossiers": len(papers),
        "papers_included": len(inc),
        "formulas_extracted": sum(p["n_formulas"] for p in inc),
        "year_min": years[0] if years else None,
        "year_max": years[-1] if years else None,
        "papers_complete_heuristic": checks.get("complete"),
        "papers_with_objective_heuristic": checks.get("with_objective"),
        "median_paper_coherence": checks.get("median_coherence"),
    }
    if Path(resolution_path).exists():
        res = json.loads(Path(resolution_path).read_text(encoding="utf-8"))
        head.update(
            {
                "structurally_clean": res.get("structurally_clean"),
                "structurally_clean_pct": res.get("structurally_clean_pct"),
                "beta1_formulas": res.get("resolved"),
                "beta1_pct": res.get("resolved_pct"),
                "ready_formulas": res.get("ready"),
                "prefilled_pairs": res.get("prefilled_pairs"),
                "symbol_pairs": res.get("symbol_pairs"),
            }
        )
    if Path(flags_path).exists():
        counts = json.loads(Path(flags_path).read_text(encoding="utf-8")).get("counts") or {}
        head.update({f"objective_{k}": v for k, v in sorted(counts.items())})
    if Path(prisma_path).exists():
        flow = json.loads(Path(prisma_path).read_text(encoding="utf-8")).get("flow") or {}
        ident = flow.get("identification") or {}
        elig = flow.get("retrieval_eligibility") or {}
        head.update(
            {
                "prisma_citation_records": ident.get("citation_search_records_identified"),
                "prisma_reports_retrieved": elig.get("reports_retrieved"),
                "prisma_reports_excluded": elig.get("reports_excluded_total"),
            }
        )
    return head


def _results_md(
    headline: dict,
    selected: list[str],
    rendered: list[str],
    skipped: dict[str, str],
    numbers: dict,
) -> str:
    """One page for the speaker: headline block, then a caption line per figure."""
    lines = [
        "# Talk pack",
        "",
        "Regenerate with `PYTHONPATH=. python3 -m corpusbuilder.talkpack`.",
        "All numbers below derive deterministically from the frozen corpus artifacts.",
        "",
        "## Headline numbers",
        "",
    ]
    label = {
        "dossiers": "Paper dossiers (retrieved)",
        "papers_included": "Included papers (with formulas)",
        "formulas_extracted": "Extracted candidate formulas",
        "year_min": "Earliest publication year",
        "year_max": "Latest publication year",
        "structurally_clean": "Clean on all four structural axes",
        "structurally_clean_pct": "Clean share (%)",
        "beta1_formulas": "Fully symbol-resolved (beta = 1)",
        "beta1_pct": "Symbol-resolved share (%)",
        "ready_formulas": "Ready (clean and beta = 1)",
        "prefilled_pairs": "Symbol pairs prefilled by the algebra",
        "symbol_pairs": "Symbol pairs total",
        "objective_ok": "Papers with a detected objective",
        "objective_unmarked": "Papers whose objective needs prose (HITL)",
        "objective_absent": "Papers with no optimization head",
        "papers_complete_heuristic": "Papers complete (objective, constraint, symbols)",
        "papers_with_objective_heuristic": "Papers with an objective (heuristic check)",
        "median_paper_coherence": "Median per-paper symbol coherence",
        "prisma_citation_records": "Citation-search records identified (PRISMA)",
        "prisma_reports_retrieved": "Reports retrieved (PRISMA)",
        "prisma_reports_excluded": "Reports excluded (PRISMA)",
    }
    for key, name in label.items():
        if headline.get(key) is not None:
            value = headline[key]
            # Years are identifiers, not quantities: no thousands separator.
            plain = key.startswith("year_") or not isinstance(value, int)
            lines.append(f"- {name}: {value if plain else _fmt(value)}")
    lines += ["", "## Figures", ""]
    for name in selected:
        if name in rendered:
            lines.append(f"- `figures/fig_{name}.png` (and `.svg`): {_caption(name, numbers)}")
        else:
            lines.append(f"- fig_{name}: not rendered, {skipped[name]}.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpusbuilder.talkpack",
        description="Deterministic presentation figures + numbers from the corpus artifacts.",
    )
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT, help="output directory")
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated figure selectors (fig1..fig8 or names like structural_yield)",
    )
    args = parser.parse_args(argv)
    summary = run(out=args.out, only=args.only)
    print(
        f"rendered: {', '.join(summary['rendered']) or 'none'}"
        + (f" | skipped: {', '.join(sorted(summary['skipped']))}" if summary["skipped"] else "")
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
