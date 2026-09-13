"""corpusbuilder.vocab — the fill-in list between assisted resolution and the gate."""

from __future__ import annotations

import json
from pathlib import Path

from corpusbuilder import vocab

_HEADER = """%@ meta id=probe family=milp schema=0.1.0
%@ name :: Probe
%@ index I ordered=0 cyclic=0 :: items
%@ index K ordered=1 cyclic=0 :: periods
%@ param c shape=I kind=vector domain=- :: cost
%@ param M shape=- kind=big_m domain=- :: big M
%@ var x shape=I,K domain=binary role=primary drole=- lo=- hi=- :: choice
%@ var unused shape=- domain=continuous role=primary drole=- lo=- hi=- :: never used
%@ obj sense=min name=objective combination=sum :: cost
"""


def _doc(*rows: str) -> str:
    body = "\n".join(f"  & {r} \\tag{{eq\\_{i + 1:04d}}} \\\\" for i, r in enumerate(rows))
    return _HEADER + "\\begin{align}\n" + body + "\n\\end{align}\n"


def test_missing_names_are_the_folded_spellings_not_the_raw_scripts():
    a = vocab.scan_document(
        _doc(
            r"\min \sum_{i \in \mathcal{I}, k \in \mathcal{K}} c_{i} \cdot x_{i}^{k}",
            r"t_{i}^{arr} - t_{i}^{dep} \ge h_{min} \forall i \in \mathcal{I}",
            r"w \cdot x_{i, k} \le M \forall i \in \mathcal{I}, k \in \mathcal{K}",
        )
    )
    assert set(a["missing"]) == {"t_arr", "t_dep", "h_min", "w"}
    assert a["missing"]["w"]["kind"] == "param"  # always left of \cdot
    assert a["missing"]["t_arr"]["kind"] == "?"
    assert a["missing"]["t_arr"]["arities"] == {"1": 1}
    assert a["bound"] == ["i", "k"]
    assert a["declared_unused"] == ["unused"]
    assert a["shape_mismatch"] == []
    assert a["unresolved_script"] == []


def test_domain_rows_decide_variables_and_shapes_are_checked():
    a = vocab.scan_document(
        _doc(
            r"\min \sum_{i \in \mathcal{I}} c_{i} \cdot x_{i}",
            r"y_{i} \in \left\{0 , 1\right\} \forall i \in \mathcal{I}",
            r"z_{i} \ge 0 \forall i \in \mathcal{I}",
            r"a_{k}^{v \left(u\right)} \le M \forall k \in \mathcal{K}",
        )
    )
    assert a["missing"]["y"]["kind"] == "var"
    assert a["missing"]["y"]["domain"] == "binary"
    assert a["missing"]["z"]["kind"] == "var"
    # x is declared with 2 indices but written with 1
    assert a["shape_mismatch"] == [{"name": "x", "row": "eq_0001", "written": 1, "declared": 2}]
    assert a["unresolved_script"][0]["name"] == "a"


def test_undeclared_families_and_family_used_as_symbol():
    a = vocab.scan_document(
        _doc(r"\sum_{j \in \mathcal{J}} x_{i, j} \le K \forall i \in \mathcal{I}, j \in J")
    )
    assert a["missing_index"] == ["J"]
    assert a["family_as_symbol"] == {"K": 1}


def test_suggestion_block_lists_exactly_the_missing_names():
    a = vocab.scan_document(
        _doc(r"\min \sum_{i \in \mathcal{I}} w \cdot t_{i}^{arr} + y_{i}", r"y_{i} \ge 0")
    )
    block = vocab.suggestion_block("probe", a)
    assert "%@ param w shape=? kind=? ::" in block
    assert "%@ var y shape=? domain=non_negative" in block
    assert "%@ ? t_arr :: param or var?" in block
    assert "declared but never used after normalization: unused" in block
    assert block.count("%@ ") == 3


def test_check_all_writes_per_paper_json_and_suggestions_deterministically(tmp_path: Path):
    promoted = tmp_path / "promoted"
    decl = tmp_path / "declarations"
    out = tmp_path / "vocab"
    promoted.mkdir()
    decl.mkdir()
    (promoted / "p1.tex").write_text(
        _doc(r"\min \sum_{i \in \mathcal{I}} c_{i} \cdot x_{i} + t_{i}^{arr}"), encoding="utf-8"
    )
    (promoted / "p2.tex").write_text(
        _doc(r"\min \sum_{i \in \mathcal{I}, k \in \mathcal{K}} c_{i} \cdot x_{i, k}"),
        encoding="utf-8",
    )
    # the sidecar is what the audit sees (not the header promote embedded)
    (decl / "p1.tex").write_text(
        "\n".join(ln for ln in _HEADER.splitlines() if ln.startswith("%@")) + "\n",
        encoding="utf-8",
    )

    r1 = vocab.check_all(promoted_dir=promoted, declarations_dir=decl, out_dir=out)
    first = {p.name: p.read_bytes() for p in list(out.iterdir()) + list(decl.glob("*.vocab.tex"))}
    r2 = vocab.check_all(promoted_dir=promoted, declarations_dir=decl, out_dir=out)
    second = {p.name: p.read_bytes() for p in list(out.iterdir()) + list(decl.glob("*.vocab.tex"))}
    assert first == second and r1 == r2
    assert r1["papers"] == 2
    assert r1["papers_complete"] == 1
    assert [p["paper_key"] for p in r1["per_paper"]] == ["p2", "p1"]  # least work first
    assert (decl / "p1.vocab.tex").exists()
    assert not (decl / "p2.vocab.tex").exists()
    audit = json.loads((out / "p1.json").read_text(encoding="utf-8"))
    assert audit["sidecar"] is True and list(audit["missing"]) == ["t_arr"]
    md = vocab.render_report_md(r1)
    assert "| `t_arr` | 1 |" in md


def test_dry_run_writes_nothing(tmp_path: Path):
    promoted = tmp_path / "promoted"
    promoted.mkdir()
    (promoted / "p1.tex").write_text(_doc(r"\min q"), encoding="utf-8")
    report = vocab.check_all(
        promoted_dir=promoted,
        declarations_dir=tmp_path / "decl",
        out_dir=tmp_path / "vocab",
        write=False,
    )
    assert report["missing_names_total"] == 1
    assert not (tmp_path / "vocab").exists()


def test_shapes_the_formulas_decide_are_repaired_with_a_note(tmp_path: Path):
    header = """%@ meta id=p family=milp schema=0.1.0
%@ index I ordered=0 cyclic=0 :: items
%@ param w shape=- kind=scalar domain=- :: written w_{i,k} everywhere
%@ param c shape=I kind=vector domain=- :: consistent
%@ var x shape=I domain=binary role=primary drole=- lo=- hi=- :: written x_{i} everywhere
%@ var y shape=I domain=binary role=primary drole=- lo=- hi=- :: mixed arities
%@ obj sense=min name=objective combination=sum :: cost
"""
    body = (
        "\\begin{align}\n"
        r"  \min\quad & \sum_{i \in \mathcal{I}, k \in K} w_{i, k} \cdot x_{i} \tag{eq\_0001} \\"
        + "\n"
        r"  & w_{i, k} \cdot x_{i} + c_{i} \le y_{i} + y_{i, k} \forall i \in \mathcal{I}, k \in K \tag{eq\_0002} \\"
        + "\n"
        "\\end{align}\n"
    )
    promoted = tmp_path / "promoted"
    decl = tmp_path / "declarations"
    promoted.mkdir()
    decl.mkdir()
    (promoted / "p.tex").write_text(header + body, encoding="utf-8")
    (decl / "p.tex").write_text(header, encoding="utf-8")
    report = vocab.check_all(
        promoted_dir=promoted, declarations_dir=decl, out_dir=tmp_path / "vocab", fix_shapes=True
    )
    assert report["shape_fix_candidates"] == 1  # w only: x is right, y is mixed, c is right
    assert report["shape_fixes_applied"] == 2  # the shape line + the new family K
    sidecar = (decl / "p.tex").read_text(encoding="utf-8")
    assert "%@ param w shape=I,K kind=scalar" in sidecar
    assert "% shape fixed by corpusbuilder.vocab" in sidecar
    assert "%@ index K ordered=0 cyclic=0 :: family bound in the formulas" in sidecar
    # the repaired sidecar is what the audit sees on the next run: no fix left
    again = vocab.check_all(
        promoted_dir=promoted, declarations_dir=decl, out_dir=tmp_path / "vocab", fix_shapes=True
    )
    assert again["shape_fix_candidates"] == 0
