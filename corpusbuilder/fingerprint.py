"""Pre-canonical structural fingerprints — cross-paper architecture similarity.

The canonical route to "which papers share a model architecture" runs through
promotion: HITL-reviewed formulas become ``Formulation``s, lp2graph schema
graphs are compared, and identity is exact. That route is gated on human
declarations and therefore covers only promoted papers. This module answers a
weaker question *now*, over all included papers: which papers *look* like they
share an architecture, judged only from deterministic surface structure of the
raw extractions? Everything it emits is labeled **pre-canonical structural
fingerprints** — a similarity heuristic over unreviewed Tier-2 LaTeX, never a
statement about canonical model identity.

Per paper the fingerprint is a fixed-order feature vector, every entry derived
from artifacts that already exist (no network, no LLM at this stage):

* the review game's deterministic machinery (:func:`corpusbuilder.game.parse_tree`,
  :func:`~corpusbuilder.game.extract_symbols`,
  :func:`~corpusbuilder.game.is_objective_latex`) — formula counts, objective
  counts, big-operator / relation shares, expression-tree depth;
* the LLM-assisted symbol tables already on disk
  (``corpus/decisions/assist_<key>.json``, schema ``game-decisions-3``) —
  kind counts and the variable-to-parameter ratio. These are the one
  non-deterministically *sourced* input; reading them is still deterministic,
  and papers without a table fall back to zeros rather than being dropped;
* :func:`corpusbuilder.symbols.paper_evidence` — domain-row counts by domain
  (a binary row is the assignment/selection signal, non-negative rows the
  flow/LP signal);
* structural-motif shares via regex on the normalized LaTeX — big-M linking,
  flow balance (sum minus sum equals), headway/ordering differences, capacity
  (aggregate under a scalar bound), modulo/PESP arithmetic.

Clustering is agglomerative (average linkage) over cosine distance between
z-scored vectors, k chosen by silhouette over a scanned range with the
smallest k winning ties. scipy is not installed on this box and 238 points
need no library: the linkage is the textbook Lance-Williams update in pure
Python, with representative-index tie-breaks so reruns are byte-identical.

Outputs (``corpus/fingerprint/``, regenerable):

* ``features.json`` / ``features.csv`` — papers x features, sorted both ways
* ``clusters.json``  — chosen k, silhouette, k-scan, and per-cluster members
  plus the highest-mean-z features (what makes the cluster nameable)

Run:  PYTHONPATH=. python3 -m corpusbuilder.fingerprint [--out corpus/fingerprint]
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from corpusbuilder.game import (
    _collapse_words,
    _rewrite_ops,
    extract_symbols,
    is_objective_latex,
    parse_tree,
)
from corpusbuilder.symbols import paper_evidence

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"
DECISIONS = CORPUS / "decisions"
OUT_DEFAULT = CORPUS / "fingerprint"

SCHEMA_VERSION = "fingerprint-1"

#: Stamped into every artifact and every figure built from one: these vectors
#: describe raw extractions, not canonical (promoted) models.
LABEL = "pre-canonical structural fingerprints"

#: Silhouette scan range for the real corpus (inclusive). Tests pass their own.
K_RANGE_DEFAULT = (4, 12)

# --------------------------------------------------------------------------- #
# Structural motifs — regex on the normalized LaTeX
# --------------------------------------------------------------------------- #

#: Optional sub-/superscripts glued to a symbol (``M_{ij}``, ``t_i^k``).
_SCRIPTS = r"(?:\s*[_^]\s*(?:\{[^{}]*\}|\\?[a-zA-Z0-9]))*"

#: Big-M linking: ``M \cdot x`` / ``c \cdot M`` / ``M (1 - x)``. The letter M
#: alone is far too common (a set name, a machine index), so the pattern
#: demands the multiplicative context the modelling idiom actually uses.
_BIG_M = re.compile(
    rf"(?<![a-zA-Z\\])M{_SCRIPTS}\s*(?:\\cdot(?![a-zA-Z])|\\left\s*\(\s*1\s*-|\(\s*1\s*-)"
    rf"|\\cdot\s*M(?![a-zA-Z])"
)

#: Flow balance: an aggregation minus an aggregation pinned by an equality
#: (``sum in - sum out = b``). Everything up to the ``=`` must hold both sums.
_FLOW_BALANCE = re.compile(r"\\sum(?![a-zA-Z])[^=]*-[^=]*\\sum(?![a-zA-Z])[^=]*=")

#: Headway / ordering: the same letter, twice with (different) scripts, as a
#: difference bounded below — ``t_i - t_j \geq h``. Disjunctive scheduling in
#: one regex; the backreference keeps ``a_i - b_j`` from matching.
_HEADWAY = re.compile(rf"([a-zA-Z]){_SCRIPTS}\s*-\s*\1{_SCRIPTS}[^=<>]*(?:\\geq?(?![a-zA-Z])|≥|>)")

#: Modulo / PESP arithmetic in the spellings that survive MathML conversion.
_MODULO = re.compile(r"\\(?:p|b)?mod(?![a-zA-Z])|(?<![a-zA-Z\\])mod(?![a-zA-Z])|\\operatorname\s*\{\s*mod")

#: The ``\leq`` that may bound a capacity row.
_LEQ = re.compile(r"\\leq?(?![a-zA-Z])|≤")
_SUM = re.compile(r"\\sum(?![a-zA-Z])")
#: Where the right-hand side of a bound stops being the bound (quantifier tail).
_RHS_END = re.compile(r"\\forall|\\quad|\\qquad|,")


def _normalized(latex: str) -> str:
    """The same normalization every game-side reader applies first."""
    return _rewrite_ops(_collapse_words(latex))


def _is_capacity(s: str) -> bool:
    """Aggregate under a scalar bound: ``\\sum ... \\leq U``.

    "Scalar" is judged conservatively: the bound (up to any quantifier tail)
    holds no further aggregation, no additive structure, and at most one
    symbol — ``\\sum x_i \\leq C_j`` counts, ``\\sum x_i \\leq b_j + s_j``
    does not.
    """
    m = _LEQ.search(s)
    if not m or not _SUM.search(s[: m.start()]):
        return False
    rhs = _RHS_END.split(s[m.end() :])[0]
    if _SUM.search(rhs) or "+" in rhs or re.search(r"(?<![{(^_])-", rhs):
        return False
    syms, _ops, _rel = extract_symbols(rhs, limit=None)
    return len(syms) <= 1


#: Motif name -> matcher over the normalized LaTeX, in sorted-name order.
MOTIFS: tuple[tuple[str, object], ...] = (
    ("big_m", lambda s: bool(_BIG_M.search(s))),
    ("capacity", _is_capacity),
    ("flow_balance", lambda s: bool(_FLOW_BALANCE.search(s))),
    ("headway", lambda s: bool(_HEADWAY.search(s))),
    ("modulo", lambda s: bool(_MODULO.search(s))),
)


def motif_flags(latex: str) -> dict[str, bool]:
    """Which structural motifs one formula's LaTeX shows (normalized first)."""
    s = _normalized(latex)
    return {name: bool(match(s)) for name, match in MOTIFS}


# --------------------------------------------------------------------------- #
# Per-paper feature vector
# --------------------------------------------------------------------------- #

_INEQUALITIES = frozenset("≤≥<>")
_DOMAIN_NAMES = ("binary", "continuous", "integer", "non_negative")
_KIND_NAMES = ("index", "parameter", "variable")


def _tree_depth(tree) -> int:
    if isinstance(tree, str):
        return 1
    if not isinstance(tree, list) or len(tree) <= 1:
        return 1
    return 1 + max(_tree_depth(child) for child in tree[1:])


def paper_vector(latex_rows: list[str], symbol_kinds: dict[str, str]) -> dict[str, float]:
    """One paper's fingerprint, feature name -> value, complete and sorted.

    Every feature is present for every paper (missing evidence contributes 0),
    so the matrix downstream is dense and column order is the sorted feature
    name order everywhere — the whole point of a *fingerprint* is that two
    papers are compared position by position.
    """
    n = len(latex_rows)
    features: dict[str, float] = {"n_formulas": float(n)}

    n_objectives = 0
    bigop = eq = ineq = 0
    depths: list[int] = []
    motif_counts = dict.fromkeys((name for name, _ in MOTIFS), 0)
    for latex in latex_rows:
        if is_objective_latex(latex):
            n_objectives += 1
        _syms, ops, rel = extract_symbols(latex, limit=None)
        if any(op in ("∑", "∏") for op, _count in ops):
            bigop += 1
        if rel == "=":
            eq += 1
        elif rel in _INEQUALITIES:
            ineq += 1
        tree = parse_tree(latex)
        if tree is not None:
            depths.append(_tree_depth(tree))
        for name, hit in motif_flags(latex).items():
            motif_counts[name] += int(hit)

    features["n_objectives"] = float(n_objectives)
    features["share_bigop"] = bigop / n if n else 0.0
    features["share_equality"] = eq / n if n else 0.0
    features["share_inequality"] = ineq / n if n else 0.0
    features["depth_mean"] = sum(depths) / len(depths) if depths else 0.0
    features["depth_max"] = float(max(depths, default=0))
    for name, count in motif_counts.items():
        features[f"motif_{name}"] = count / n if n else 0.0

    evidence = paper_evidence(latex_rows)
    domain_counts = dict.fromkeys(_DOMAIN_NAMES, 0)
    for domain in evidence.domains.values():
        domain_counts[domain] += 1
    for name, count in domain_counts.items():
        features[f"domain_{name}"] = float(count)

    kind_counts = dict.fromkeys(_KIND_NAMES, 0)
    for kind in symbol_kinds.values():
        if kind in kind_counts:  # unknown kinds from foreign exports are dropped
            kind_counts[kind] += 1
    for name, count in kind_counts.items():
        features[f"sym_{name}"] = float(count)
    # max(.., 1) keeps table-less papers at a defined 0/1 = 0 instead of NaN;
    # z-scoring downstream needs every entry finite.
    features["sym_var_param_ratio"] = kind_counts["variable"] / max(kind_counts["parameter"], 1)

    return dict(sorted(features.items()))


def load_symbol_kinds(decisions_dir: Path, paper_key: str) -> dict[str, str]:
    """The LLM symbol table for one paper, or ``{}`` when none exists yet.

    ``assist_<key>.json`` follows the ``game-decisions-3`` export schema:
    ``symbol_tables`` is a list of ``{paper_key, symbols}`` entries. Entries
    for other papers are ignored; multiple entries for the same paper merge in
    list order with the last one winning — the same rule (ADR-0008) every
    other decisions consumer applies.
    """
    path = Path(decisions_dir) / f"assist_{paper_key}.json"
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    kinds: dict[str, str] = {}
    for table in raw.get("symbol_tables") or []:
        if isinstance(table, dict) and table.get("paper_key") == paper_key:
            symbols = table.get("symbols")
            if isinstance(symbols, dict):
                kinds.update({str(k): str(v) for k, v in symbols.items()})
    return kinds


def build_features(dossier_dir: Path = DOSSIERS, decisions_dir: Path = DECISIONS) -> dict:
    """Fingerprint every included paper (>= 1 extracted formula)."""
    papers: dict[str, dict[str, float]] = {}
    feature_names: list[str] = []
    for path in sorted(Path(dossier_dir).glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        rows = [str(f.get("latex") or "") for f in raw.get("formulas") or []]
        if not rows:
            continue  # metadata-only dossier: not an included paper
        vector = paper_vector(rows, load_symbol_kinds(decisions_dir, path.stem))
        papers[path.stem] = vector
        feature_names = list(vector)  # identical for every paper by construction
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "corpusbuilder.fingerprint",
        "label": LABEL,
        "features": feature_names,
        "papers": papers,
    }


def _round(value: float) -> float | int:
    """6-decimal rounding, ints kept integral — stable bytes across reruns."""
    value = round(float(value), 6)
    return int(value) if value.is_integer() else value


def write_features(payload: dict, out: Path) -> tuple[Path, Path]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    rounded = {
        **payload,
        "papers": {
            key: {name: _round(v) for name, v in vec.items()}
            for key, vec in sorted(payload["papers"].items())
        },
    }
    json_path = out / "features.json"
    json_path.write_text(
        json.dumps(rounded, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    names = payload["features"]
    lines = ["paper_key," + ",".join(names)]
    for key, vec in sorted(payload["papers"].items()):
        lines.append(key + "," + ",".join(f"{_round(vec[n]):g}" for n in names))
    csv_path = out / "features.csv"
    csv_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return json_path, csv_path


# --------------------------------------------------------------------------- #
# Clustering — pure Python; 238 points make a dependency unjustifiable
# --------------------------------------------------------------------------- #


def zscore(matrix: list[list[float]]) -> list[list[float]]:
    """Column-wise z-score (population std); constant columns become zeros."""
    if not matrix:
        return []
    n, m = len(matrix), len(matrix[0])
    out = [[0.0] * m for _ in range(n)]
    for j in range(m):
        col = [row[j] for row in matrix]
        mean = sum(col) / n
        var = sum((x - mean) ** 2 for x in col) / n
        std = var**0.5
        if std > 0:
            for i in range(n):
                out[i][j] = (col[i] - mean) / std
    return out


def cosine_distance_matrix(rows: list[list[float]]) -> list[list[float]]:
    """1 - cosine similarity; a zero vector is maximally far from everything."""
    n = len(rows)
    norms = [sum(x * x for x in row) ** 0.5 for row in rows]
    d = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1, n):
            if norms[i] == 0 or norms[j] == 0:
                dist = 1.0
            else:
                dot = sum(a * b for a, b in zip(rows[i], rows[j], strict=True))
                dist = 1.0 - dot / (norms[i] * norms[j])
            d[i][j] = d[j][i] = max(0.0, min(2.0, dist))
    return d


def agglomerate(dist: list[list[float]], ks: set[int]) -> dict[int, list[int]]:
    """Average-linkage merges; return ``{k: labels}`` snapshots for each k.

    Cluster identity is the smallest member index, the merge pick is
    ``(distance, rep_i, rep_j)``-minimal, and the Lance-Williams average
    update is exact for average linkage — three choices that together make
    the whole dendrogram (and therefore every downstream artifact)
    reproducible byte for byte.
    """
    n = len(dist)
    members: dict[int, list[int]] = {i: [i] for i in range(n)}
    pair: dict[tuple[int, int], float] = {
        (i, j): dist[i][j] for i in range(n) for j in range(i + 1, n)
    }
    snapshots: dict[int, list[int]] = {}

    def snapshot() -> list[int]:
        labels = [0] * n
        for rep, pts in members.items():
            for p in pts:
                labels[p] = rep
        return labels

    if n in ks:
        snapshots[n] = snapshot()
    while len(members) > 1 and min(ks, default=1) < len(members):
        (a, b), _d = min(pair.items(), key=lambda kv: (kv[1], kv[0]))
        na, nb = len(members[a]), len(members[b])
        members[a] = sorted(members[a] + members.pop(b))
        for other in members:
            if other == a:
                continue
            ka = (min(a, other), max(a, other))
            kb = (min(b, other), max(b, other))
            pair[ka] = (na * pair[ka] + nb * pair.pop(kb)) / (na + nb)
        del pair[(a, b)]
        if len(members) in ks:
            snapshots[len(members)] = snapshot()
    return snapshots


def silhouette(dist: list[list[float]], labels: list[int]) -> float:
    """Mean silhouette; singleton clusters contribute 0 by convention."""
    clusters: dict[int, list[int]] = {}
    for i, lab in enumerate(labels):
        clusters.setdefault(lab, []).append(i)
    if len(clusters) < 2:
        return 0.0
    total = 0.0
    for i, lab in enumerate(labels):
        own = clusters[lab]
        if len(own) == 1:
            continue
        a = sum(dist[i][j] for j in own if j != i) / (len(own) - 1)
        b = min(
            sum(dist[i][j] for j in pts) / len(pts)
            for other, pts in clusters.items()
            if other != lab
        )
        denom = max(a, b)
        total += (b - a) / denom if denom > 0 else 0.0
    return total / len(labels)


def cluster_features(payload: dict, k_range: tuple[int, int] = K_RANGE_DEFAULT) -> dict:
    """Cluster the fingerprint matrix; return the ``clusters.json`` payload."""
    keys = sorted(payload["papers"])
    names = payload["features"]
    matrix = [[float(payload["papers"][k][name]) for name in names] for k in keys]
    z = zscore(matrix)
    dist = cosine_distance_matrix(z)

    n = len(keys)
    ks = {k for k in range(k_range[0], k_range[1] + 1) if 2 <= k <= n - 1}
    base = {
        "schema_version": SCHEMA_VERSION,
        "generated_by": "corpusbuilder.fingerprint",
        "label": LABEL,
        "n_papers": n,
        "n_features": len(names),
        "distance": "cosine over z-scored features, average linkage",
    }
    if not ks:
        return {
            **base,
            "k": 1,
            "silhouette": None,
            "k_scan": {},
            "clusters": [
                {
                    "id": 0,
                    "size": n,
                    "papers": keys,
                    "top_features": [],
                }
            ],
        }

    snapshots = agglomerate(dist, ks)
    scan = {k: silhouette(dist, snapshots[k]) for k in sorted(ks)}
    # Strict ">" while scanning ascending k = the smallest k wins exact ties.
    best_k = min(scan)
    for k, score in sorted(scan.items()):
        if score > scan[best_k]:
            best_k = k

    groups: dict[int, list[int]] = {}
    for i, rep in enumerate(snapshots[best_k]):
        groups.setdefault(rep, []).append(i)
    ordered = sorted(groups.values(), key=lambda pts: (-len(pts), keys[pts[0]]))
    clusters = []
    for cid, pts in enumerate(ordered):
        mean_z = [
            (names[j], sum(z[i][j] for i in pts) / len(pts)) for j in range(len(names))
        ]
        top = sorted(mean_z, key=lambda kv: (-kv[1], kv[0]))[:4]
        clusters.append(
            {
                "id": cid,
                "size": len(pts),
                "papers": [keys[i] for i in pts],
                "top_features": [
                    {"feature": name, "mean_z": _round(value)} for name, value in top
                ],
            }
        )
    return {
        **base,
        "k": best_k,
        "silhouette": _round(scan[best_k]),
        "k_scan": {str(k): _round(s) for k, s in sorted(scan.items())},
        "clusters": clusters,
    }


# --------------------------------------------------------------------------- #
# Naming — shared with the talkpack figures so labels never diverge
# --------------------------------------------------------------------------- #

#: Feature -> short display term for composing a cluster label.
FEATURE_SHORT = {
    "depth_max": "deep expressions",
    "depth_mean": "deep expressions",
    "domain_binary": "binary domains",
    "domain_continuous": "continuous domains",
    "domain_integer": "integer domains",
    "domain_non_negative": "non-negative domains",
    "motif_big_m": "big-M linking",
    "motif_capacity": "capacity bounds",
    "motif_flow_balance": "flow balance",
    "motif_headway": "headway/ordering",
    "motif_modulo": "modulo/PESP",
    "n_formulas": "large models",
    "n_objectives": "many objectives",
    "share_bigop": "aggregation-heavy",
    "share_equality": "equality-heavy",
    "share_inequality": "inequality-heavy",
    "sym_index": "index-rich",
    "sym_parameter": "parameter-rich",
    "sym_variable": "variable-rich",
    "sym_var_param_ratio": "variable-dominant",
}


def cluster_label(top_features: list[dict], limit: int = 3) -> str:
    """A nameable label from the highest-mean-z features, duplicates folded."""
    terms: list[str] = []
    for entry in top_features:
        if entry.get("mean_z", 0) <= 0:
            continue
        term = FEATURE_SHORT.get(entry.get("feature", ""), entry.get("feature", ""))
        if term and term not in terms:
            terms.append(term)
        if len(terms) >= limit:
            break
    return " · ".join(terms) if terms else "mixed"


# --------------------------------------------------------------------------- #
# Entry points
# --------------------------------------------------------------------------- #


def run(
    dossier_dir: Path = DOSSIERS,
    decisions_dir: Path = DECISIONS,
    out: Path = OUT_DEFAULT,
    k_range: tuple[int, int] = K_RANGE_DEFAULT,
) -> dict:
    """Build features + clusters, write all artifacts, return the summary."""
    payload = build_features(dossier_dir, decisions_dir)
    json_path, csv_path = write_features(payload, out)
    clusters = cluster_features(payload, k_range)
    clusters_path = Path(out) / "clusters.json"
    clusters_path.write_text(
        json.dumps(clusters, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "papers": len(payload["papers"]),
        "features": len(payload["features"]),
        "k": clusters["k"],
        "silhouette": clusters["silhouette"],
        "paths": [str(json_path), str(csv_path), str(clusters_path)],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpusbuilder.fingerprint",
        description="Deterministic pre-canonical structural fingerprints + clustering.",
    )
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT, help="output directory")
    parser.add_argument("--dossiers", type=Path, default=DOSSIERS)
    parser.add_argument("--decisions", type=Path, default=DECISIONS)
    args = parser.parse_args(argv)
    summary = run(dossier_dir=args.dossiers, decisions_dir=args.decisions, out=args.out)
    print(
        f"fingerprinted {summary['papers']} papers x {summary['features']} features; "
        f"k = {summary['k']}, silhouette = {summary['silhouette']}"
    )
    for path in summary["paths"]:
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
