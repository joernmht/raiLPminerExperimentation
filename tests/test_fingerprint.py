"""Tests for :mod:`corpusbuilder.fingerprint` — pre-canonical fingerprints.

Everything runs offline against a synthetic mini-corpus written into tmp
directories: four included papers with structurally distinct formulas (an
assignment paper, a flow paper, a scheduling paper, a mixed one), one
metadata-only dossier, and LLM symbol tables for two of the papers so the
zero-fallback path is exercised too. The real ``corpus/`` is never read —
tests must stay green on a checkout without the (gitignored) Elsevier corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpusbuilder import fingerprint, talkpack

# ---------------------------------------------------------------------------
# Synthetic mini-corpus
# ---------------------------------------------------------------------------

_ASSIGN = [
    r"\min \sum_{i \in I} \sum_{j \in J} c_{ij} x_{ij}",
    r"\sum_{j \in J} x_{ij} = 1 \quad \forall i \in I",
    r"x_{ij} \in \{0,1\}",
    r"y_{ij} \leq M \cdot x_{ij}",
]
_FLOW = [
    r"\min \sum_{a \in A} c_a f_a",
    r"\sum_{a \in \delta^+(v)} f_a - \sum_{a \in \delta^-(v)} f_a = b_v",
    r"\sum_{a \in A} u_a f_a \leq U",
    r"f_a \geq 0",
]
_SCHED = [
    r"\min z",
    r"t_i - t_j \geq h_{ij}",
    r"(t_j - t_i) \bmod T \geq h",
    r"t_i \geq 0",
]
_MIXED = [
    r"\max \sum_{k \in K} p_k y_k",
    r"y_k \in \{0,1\}",
]


def _dossier(key: str, year: int | None, rows: list[str]) -> dict:
    return {
        "schema_version": "dossier-1",
        "source": {"title": f"Paper {key}", "doi": f"10.9999/{key}", "year": year},
        "formulas": [
            {"id": f"eq-{i:04d}", "latex": latex, "status": "candidate"}
            for i, latex in enumerate(rows)
        ],
    }


@pytest.fixture()
def mini_corpus(tmp_path: Path) -> dict:
    dossiers = tmp_path / "dossiers"
    dossiers.mkdir()
    records = {
        "10.9999_assign": _dossier("assign", 2015, _ASSIGN),
        "10.9999_flow": _dossier("flow", 2018, _FLOW),
        "10.9999_sched": _dossier("sched", 2021, _SCHED),
        "10.9999_mixed": _dossier("mixed", None, _MIXED),  # undated on purpose
        "10.9999_empty": _dossier("empty", 2020, []),  # metadata-only: excluded
    }
    for key, rec in records.items():
        (dossiers / f"{key}.json").write_text(json.dumps(rec), encoding="utf-8")

    decisions = tmp_path / "decisions"
    decisions.mkdir()
    tables = {
        "10.9999_assign": {"x": "variable", "y": "variable", "c": "parameter", "i": "index"},
        "10.9999_flow": {"f": "variable", "u": "parameter", "b": "parameter", "a": "index"},
    }
    for key, symbols in tables.items():
        (decisions / f"assist_{key}.json").write_text(
            json.dumps(
                {
                    "schema_version": "game-decisions-3",
                    "symbol_tables": [{"paper_key": key, "symbols": symbols}],
                }
            ),
            encoding="utf-8",
        )
    return {"dossiers": dossiers, "decisions": decisions, "out": tmp_path / "fingerprint"}


# ---------------------------------------------------------------------------
# Motifs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("latex", "motif", "expected"),
    [
        (r"y_{ij} \leq M \cdot x_{ij}", "big_m", True),
        (r"y \leq d + M \left(1 - x\right)", "big_m", True),
        (r"y \leq M x", "big_m", False),  # bare juxtaposition: too ambiguous
        (r"\sum_{a} f_a - \sum_{b} f_b = 0", "flow_balance", True),
        (r"\sum_{i} x_i = 1", "flow_balance", False),
        (r"t_i - t_j \geq h_{ij}", "headway", True),
        (r"a_i - b_j \geq h", "headway", False),  # different letters: no ordering
        (r"(t_j - t_i) \bmod T \geq h", "modulo", True),
        (r"x \pmod{T}", "modulo", True),
        (r"the model x", "modulo", False),  # "mod" inside a word must not fire
        (r"\sum_{i \in I} u_i x_i \leq U", "capacity", True),
        (r"\sum_i x_i \leq b_j + s_j", "capacity", False),  # additive bound
        (r"x_i \leq U", "capacity", False),  # no aggregation
    ],
)
def test_motif_flags(latex: str, motif: str, expected: bool) -> None:
    assert fingerprint.motif_flags(latex)[motif] is expected


# ---------------------------------------------------------------------------
# Feature vector
# ---------------------------------------------------------------------------


def test_paper_vector_assignment_paper() -> None:
    vec = fingerprint.paper_vector(
        _ASSIGN, {"x": "variable", "c": "parameter", "i": "index", "M": "parameter"}
    )
    assert vec["n_formulas"] == 4
    assert vec["n_objectives"] == 1
    assert vec["domain_binary"] == 1  # x declared by its {0,1} row
    assert vec["motif_big_m"] == pytest.approx(0.25)
    assert vec["share_bigop"] == pytest.approx(0.5)
    assert vec["share_equality"] == pytest.approx(0.25)
    assert vec["share_inequality"] == pytest.approx(0.25)
    assert vec["sym_variable"] == 1
    assert vec["sym_parameter"] == 2
    assert vec["sym_var_param_ratio"] == pytest.approx(0.5)
    assert vec["depth_max"] >= vec["depth_mean"] > 0


def test_paper_vector_is_complete_and_sorted() -> None:
    vec = fingerprint.paper_vector(_SCHED, {})
    assert list(vec) == sorted(vec)
    # Every feature must exist even with no symbol table and no domain rows...
    assert vec["sym_variable"] == 0
    assert vec["sym_var_param_ratio"] == 0  # 0 variables / max(0 params, 1)
    # ...and identically-named features across papers (dense matrix contract).
    assert list(vec) == list(fingerprint.paper_vector(_ASSIGN, {"x": "variable"}))


def test_paper_vector_domain_rows() -> None:
    vec = fingerprint.paper_vector(_FLOW, {})
    assert vec["domain_non_negative"] == 1  # f_a >= 0
    assert vec["motif_flow_balance"] == pytest.approx(0.25)
    assert vec["motif_capacity"] == pytest.approx(0.25)


def test_load_symbol_kinds(mini_corpus: dict) -> None:
    kinds = fingerprint.load_symbol_kinds(mini_corpus["decisions"], "10.9999_assign")
    assert kinds == {"c": "parameter", "i": "index", "x": "variable", "y": "variable"}
    # Missing file: zeros downstream, never an exception.
    assert fingerprint.load_symbol_kinds(mini_corpus["decisions"], "10.9999_sched") == {}


def test_load_symbol_kinds_last_table_wins(tmp_path: Path) -> None:
    (tmp_path / "assist_k.json").write_text(
        json.dumps(
            {
                "symbol_tables": [
                    {"paper_key": "k", "symbols": {"x": "parameter"}},
                    {"paper_key": "other", "symbols": {"x": "index"}},  # foreign: ignored
                    {"paper_key": "k", "symbols": {"x": "variable"}},
                ]
            }
        ),
        encoding="utf-8",
    )
    assert fingerprint.load_symbol_kinds(tmp_path, "k") == {"x": "variable"}


# ---------------------------------------------------------------------------
# Feature artifacts
# ---------------------------------------------------------------------------


def test_build_features_includes_only_papers_with_formulas(mini_corpus: dict) -> None:
    payload = fingerprint.build_features(mini_corpus["dossiers"], mini_corpus["decisions"])
    assert sorted(payload["papers"]) == [
        "10.9999_assign",
        "10.9999_flow",
        "10.9999_mixed",
        "10.9999_sched",
    ]
    assert payload["label"] == "pre-canonical structural fingerprints"
    assert payload["features"] == sorted(payload["features"])


def test_write_features_deterministic_bytes(mini_corpus: dict, tmp_path: Path) -> None:
    payload = fingerprint.build_features(mini_corpus["dossiers"], mini_corpus["decisions"])
    a1, c1 = fingerprint.write_features(payload, tmp_path / "one")
    a2, c2 = fingerprint.write_features(payload, tmp_path / "two")
    assert a1.read_bytes() == a2.read_bytes()
    assert c1.read_bytes() == c2.read_bytes()
    header = c1.read_text(encoding="utf-8").splitlines()[0]
    assert header == "paper_key," + ",".join(payload["features"])


# ---------------------------------------------------------------------------
# Clustering
# ---------------------------------------------------------------------------


def _blobs() -> dict:
    papers = {}
    for g, base in enumerate(([10.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 10.0])):
        for i in range(4):
            papers[f"p{g}{i}"] = {
                "fa": base[0] + i * 0.1,
                "fb": base[1] + i * 0.1,
                "fc": base[2] + i * 0.1,
            }
    return {"features": ["fa", "fb", "fc"], "papers": papers}


def test_cluster_features_recovers_blobs() -> None:
    out = fingerprint.cluster_features(_blobs(), k_range=(2, 6))
    assert out["k"] == 3
    assert out["silhouette"] > 0.9
    assert [c["size"] for c in out["clusters"]] == [4, 4, 4]
    covered = sorted(p for c in out["clusters"] for p in c["papers"])
    assert covered == sorted(_blobs()["papers"])
    # Nameable: the blob loaded on fa must surface fa as its top feature.
    by_first_paper = {c["papers"][0]: c for c in out["clusters"]}
    assert by_first_paper["p00"]["top_features"][0]["feature"] == "fa"


def test_cluster_features_deterministic() -> None:
    assert fingerprint.cluster_features(_blobs(), k_range=(2, 6)) == fingerprint.cluster_features(
        _blobs(), k_range=(2, 6)
    )


def test_cluster_features_identical_points_pick_smallest_k() -> None:
    papers = {f"p{i}": {"fa": 1.0, "fb": 2.0} for i in range(6)}
    out = fingerprint.cluster_features(
        {"features": ["fa", "fb"], "papers": papers}, k_range=(2, 4)
    )
    # Constant columns z-score to zero vectors: every distance ties at 1.0
    # (zero-norm convention), every silhouette is 0, the smallest k must win.
    assert out["k"] == 2


def test_cluster_features_too_few_papers_degrades_to_one_cluster() -> None:
    papers = {"a": {"f": 1.0}, "b": {"f": 2.0}}
    out = fingerprint.cluster_features({"features": ["f"], "papers": papers}, k_range=(4, 12))
    assert out["k"] == 1
    assert out["silhouette"] is None
    assert out["clusters"][0]["papers"] == ["a", "b"]


def test_silhouette_bounds() -> None:
    dist = [[0.0, 0.1, 1.0], [0.1, 0.0, 1.0], [1.0, 1.0, 0.0]]
    score = fingerprint.silhouette(dist, [0, 0, 2])
    assert 0.0 < score <= 1.0


# ---------------------------------------------------------------------------
# End to end + talkpack figures
# ---------------------------------------------------------------------------


def test_run_end_to_end(mini_corpus: dict) -> None:
    summary = fingerprint.run(
        dossier_dir=mini_corpus["dossiers"],
        decisions_dir=mini_corpus["decisions"],
        out=mini_corpus["out"],
        k_range=(2, 3),
    )
    assert summary["papers"] == 4
    clusters = json.loads((mini_corpus["out"] / "clusters.json").read_text(encoding="utf-8"))
    assert clusters["label"] == "pre-canonical structural fingerprints"
    assert 2 <= clusters["k"] <= 3
    covered = sorted(p for c in clusters["clusters"] for p in c["papers"])
    assert covered == sorted(
        json.loads((mini_corpus["out"] / "features.json").read_text(encoding="utf-8"))["papers"]
    )
    assert "10.9999_empty" not in covered
    for c in clusters["clusters"]:
        assert c["top_features"], "clusters must be nameable"


def test_talkpack_fingerprint_figures(mini_corpus: dict, tmp_path: Path) -> None:
    fingerprint.run(
        dossier_dir=mini_corpus["dossiers"],
        decisions_dir=mini_corpus["decisions"],
        out=mini_corpus["out"],
        k_range=(2, 3),
    )
    out = tmp_path / "talkpack"
    fam = talkpack.fig_fingerprint_families(
        out, fingerprint_dir=mini_corpus["out"], dossier_dir=mini_corpus["dossiers"]
    )
    assert fam is not None and fam.exists() and fam.name == "fig_fingerprint_families.png"
    tl = talkpack.fig_fingerprint_timeline(
        out, fingerprint_dir=mini_corpus["out"], dossier_dir=mini_corpus["dossiers"]
    )
    assert tl is not None and tl.exists() and tl.name == "fig_fingerprint_timeline.png"
    assert (out / "figures" / "fig_fingerprint_families.svg").exists()


def test_talkpack_fingerprint_figures_skip_when_unbuilt(tmp_path: Path) -> None:
    dossiers = tmp_path / "dossiers"
    dossiers.mkdir()
    assert (
        talkpack.fig_fingerprint_families(
            tmp_path / "out", fingerprint_dir=tmp_path / "missing", dossier_dir=dossiers
        )
        is None
    )
    assert (
        talkpack.fig_fingerprint_timeline(
            tmp_path / "out", fingerprint_dir=tmp_path / "missing", dossier_dir=dossiers
        )
        is None
    )


def test_talkpack_timeline_data_shares_sum_to_one(mini_corpus: dict) -> None:
    fingerprint.run(
        dossier_dir=mini_corpus["dossiers"],
        decisions_dir=mini_corpus["decisions"],
        out=mini_corpus["out"],
        k_range=(2, 3),
    )
    data = talkpack.data_fingerprint_timeline(
        fingerprint_dir=mini_corpus["out"], dossier_dir=mini_corpus["dossiers"]
    )
    assert data is not None
    assert data["years"][0] == 2015 and data["years"][-1] == 2021
    for idx in range(len(data["years"])):
        total = sum(shares[idx] for shares in data["shares"].values())
        assert total == pytest.approx(1.0, abs=0.01) or total == 0.0
