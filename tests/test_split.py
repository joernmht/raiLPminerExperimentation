"""Tests for corpusbuilder.split — deterministic multi-formula splitting.

Two layers:

1. The hand-labeled test set ``corpus/testsets/formula_split_labels.json``
   (166 corpus records, 28 glued positives / 138 single negatives, sampled
   across 8 strata).  Precision and recall must both be perfect on it —
   every miss here is a regression on a case that was hand-verified.

2. Held-out spot checks: (doi, id, expected part count) triples verified by
   hand on two later validation samples, read live from corpus/dossiers.

Plus invariants: determinism, balanced parts, non-empty parts.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from corpusbuilder.split import SplitResult, _balanced, split_latex

ROOT = Path(__file__).resolve().parent.parent
LABELS = ROOT / "corpus" / "testsets" / "formula_split_labels.json"
DOSSIERS = ROOT / "corpus" / "dossiers"

needs_labels = pytest.mark.skipif(not LABELS.exists(), reason="label file missing")
needs_corpus = pytest.mark.skipif(not DOSSIERS.exists(), reason="corpus missing")


def _records():
    return json.loads(LABELS.read_text())["records"]


@needs_labels
def test_labeled_set_split_decision_perfect():
    """Every hand-labeled record must be classified exactly right."""
    wrong = []
    for r in _records():
        res = split_latex(r["latex"])
        if res.is_split != r["split"]:
            wrong.append((r["doi"], r["id"], r["kind"], res.kind))
    assert not wrong, f"{len(wrong)} misclassified: {wrong[:8]}"


@needs_labels
def test_labeled_set_part_counts():
    """Where the exact part count is labeled, it must match."""
    wrong = []
    for r in _records():
        if not r["split"]:
            continue
        res = split_latex(r["latex"])
        if r["parts"] is not None and len(res.parts) != r["parts"]:
            wrong.append((r["doi"], r["id"], r["parts"], len(res.parts)))
        if r["parts"] is None and res.is_split:
            assert len(res.parts) >= 2
    assert not wrong, f"part-count mismatches: {wrong}"


# (doi, formula id, expected part count) — hand-verified on two held-out
# validation samples AFTER the labeled set above was frozen
HOLDOUT = [
    ("10.1016_j.trb.2019.02.015", "eq-0019", 1),
    ("10.1016_j.tre.2016.07.015", "eq-0033", 1),
    ("10.1016_j.cie.2019.04.031", "eq-0009", 1),
    ("10.1016_j.trc.2021.103080", "eq-0030", 1),
    ("10.1016_j.omega.2020.102371", "eq-0025", 1),
    ("10.1016_j.eswa.2024.124173", "eq-0003", 1),
    ("10.1016_j.omega.2022.102796", "eq-0012", 1),
    ("10.1016_j.trb.2021.06.001", "eq-0019", 1),
    ("10.1016_j.trc.2020.102925", "eq-0057", 3),
    ("10.1016_j.trc.2021.102963", "eq-0033", 2),
    ("10.1016_j.trc.2022.103708", "eq-0050", 12),
    ("10.1016_j.tre.2023.103339", "eq-0038", 4),
    ("10.1016_j.tre.2025.104641", "eq-0047", 3),
    ("10.1016_j.omega.2018.04.003", "eq-0026", 3),
    ("10.1016_j.cor.2005.02.004", "eq-0056", 4),
    ("10.1016_j.trc.2016.05.020", "eq-0005", 2),
    ("10.1016_j.trc.2025.105441", "eq-0034", 4),
    ("10.1016_j.trc.2025.105441", "eq-0029", 4),
    ("10.1016_j.trb.2018.02.003", "eq-0013", 2),
    ("10.1016_j.trc.2021.103080", "eq-0048", 2),
    ("10.1016_j.trb.2018.09.001", "eq-0018", 6),
    ("10.1016_j.aej.2025.03.003", "eq-0010", 4),
    ("10.1016_j.omega.2017.08.018", "eq-0015", 2),
    ("10.1016_j.ress.2022.108515", "eq-0012", 2),
    ("10.1016_j.trc.2020.102925", "eq-0052", 3),
    ("10.1016_j.trc.2013.08.016", "eq-0028", 2),
    ("10.1016_j.trc.2016.05.020", "eq-0024", 5),
    ("10.1016_j.cie.2020.106374", "eq-0033", 3),
    ("10.1016_j.trb.2025.103233", "eq-0086", 3),
    ("10.1016_j.trb.2018.10.006", "eq-0018", 2),
]


def _formula(doi: str, fid: str) -> str:
    d = json.loads((DOSSIERS / f"{doi}.json").read_text())
    return next(f["latex"] for f in d["formulas"] if f["id"] == fid)


@needs_corpus
@pytest.mark.parametrize("doi,fid,want", HOLDOUT)
def test_holdout_part_counts(doi, fid, want):
    res = split_latex(_formula(doi, fid))
    assert len(res.parts) == want, f"kind={res.kind} parts={len(res.parts)}"


@needs_labels
def test_deterministic():
    for r in _records()[:40]:
        a = split_latex(r["latex"])
        b = split_latex(r["latex"])
        assert a.parts == b.parts and a.kind == b.kind


@needs_labels
def test_parts_are_balanced_and_nonempty():
    for r in _records():
        res = split_latex(r["latex"])
        if res.is_split:
            for p in res.parts:
                assert p.strip(), (r["doi"], r["id"])
                assert _balanced(p), (r["doi"], r["id"], p)


def test_result_shape():
    res = split_latex("x + y \\leq 1")
    assert isinstance(res, SplitResult)
    assert res.parts == ["x + y \\leq 1"] and not res.is_split
    assert split_latex("").kind == "empty"
