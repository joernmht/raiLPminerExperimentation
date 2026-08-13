"""Promotion — HITL decisions to canonical corpus entries.

The fixtures are built from a *real* seed formulation: it is rendered to
canonical LaTeX, then torn apart into the two halves the pipeline actually sees
(a declaration sidecar, and paper-style equation rows stripped of their canonical
scaffolding). A test that hand-wrote its own "canonical-looking" LaTeX would pass
while the real grammar rejected it.
"""

from __future__ import annotations

import json
import re

import pytest
from lp2graph import load
from lp2graph.codec import to_canonical_latex

from corpusbuilder import promote
from corpusbuilder.dossier import Dossier, ExtractionMethod, FormulaRecord, SourceInfo
from corpusbuilder.promote import PaperDecisions, load_decisions, promote_all, promote_paper

DECL_RECORDS = ("index", "param", "var", "obj", "con")


def _canonical(name: str = "mip_2_1_big_m") -> str:
    return to_canonical_latex(load(f"corpus/formulations/{name}.json"))


def _declarations(canonical: str) -> str:
    """The sidecar half of a canonical document: symbol/objective/constraint lines."""
    return "\n".join(
        line
        for line in canonical.splitlines()
        if line.startswith("%@") and line[2:].strip().split()[0] in DECL_RECORDS
    )


def _paper_rows(canonical: str) -> list[str]:
    """The algebra half, as a paper would print it: no tags, no align scaffolding."""
    body = re.search(r"\\begin\{align\}(.*?)\\end\{align\}", canonical, re.S).group(1)
    rows = []
    for raw in body.split("\\\\"):
        row = re.sub(r"\\tag\{.*?\}", "", raw).strip()
        if not row:
            continue
        row = re.sub(r"^\\min\\quad\s*&", r"\\min", row)
        rows.append(re.sub(r"^&", "", row).strip())
    return rows


@pytest.fixture
def workspace(tmp_path):
    """A corpus skeleton plus one paper whose formulas are a known-good model."""
    canonical = _canonical()
    rows = _paper_rows(canonical)
    dirs = {
        n: tmp_path / n
        for n in ("dossiers", "decisions", "declarations", "formulations", "provenance", "promoted")
    }
    for d in dirs.values():
        d.mkdir()

    dossier = Dossier(
        source=SourceInfo(
            title="A big-M ordering model",
            doi="10.1016/j.test.2020.01",
            venue="Transportation Research Part B",
            year=2020,
            authors=["A. Author"],
            cited_by_count=42,
            scopus_cited_by_count=51,
        ),
        formulas=[
            FormulaRecord(id=f"eq-{i:04d}", latex=row, method=ExtractionMethod.mathml)
            for i, row in enumerate(rows)
        ],
    )
    dossier.save(dirs["dossiers"])
    (dirs["declarations"] / f"{dossier.key}.tex").write_text(_declarations(canonical))
    return {
        "dirs": dirs,
        "dossier": dossier,
        "rows": rows,
        "declarations": _declarations(canonical),
    }


def _game_export(paper_key: str, decisions: list[dict], cell: str | None = "P3") -> dict:
    return {
        "schema_version": "game-decisions-1",
        "formula_decisions": [{"paper_key": paper_key, "doi": "10.1/x", "decisions": decisions}],
        "paper_cells": ([{"paper_key": paper_key, "cell": cell}] if cell else []),
    }


def _accept_all(workspace, cell: str | None = "P3") -> dict:
    return _game_export(
        workspace["dossier"].key,
        [{"id": f.id, "status": "accepted", "note": None} for f in workspace["dossier"].formulas],
        cell,
    )


def _write_decisions(workspace, payload: dict, name: str = "game_decisions_2026-08-13.json"):
    path = workspace["dirs"]["decisions"] / name
    path.write_text(json.dumps(payload))
    return path


def _promote(workspace, *, write: bool = True):
    dirs = workspace["dirs"]
    return promote_all(
        decisions_dir=dirs["decisions"],
        dossiers_dir=dirs["dossiers"],
        declarations_dir=dirs["declarations"],
        out_dirs={k: dirs[k] for k in ("formulations", "provenance", "promoted")},
        write=write,
    )


# --------------------------------------------------------------------------- #
# Decision loading — both export schemas
# --------------------------------------------------------------------------- #


def test_reads_the_game_export_schema(tmp_path):
    path = tmp_path / "game_decisions_2026-08-13.json"
    path.write_text(json.dumps(_game_export("p1", [{"id": "eq-1", "status": "accepted"}])))
    papers, unrecognised = load_decisions([path])
    assert unrecognised == {}
    assert papers["p1"].cell == "P3"
    assert papers["p1"].decisions[0].status == "accepted"


def test_reads_the_review_view_export_schema(tmp_path):
    """The older single-paper export nests nothing; reading only ``formula_decisions``
    would see an empty file and promote nothing, silently."""
    path = tmp_path / "decisions_p1.json"
    path.write_text(
        json.dumps(
            {
                "paper_key": "p1",
                "doi": "10.1/x",
                "decisions": [{"id": "eq-1", "status": "accepted"}],
            }
        )
    )
    papers, _ = load_decisions([path])
    assert [(d.paper_key, d.status) for d in papers["p1"].decisions] == [("p1", "accepted")]


def test_a_later_export_supersedes_an_earlier_verdict(tmp_path):
    early = tmp_path / "game_decisions_2026-08-10.json"
    late = tmp_path / "game_decisions_2026-08-11.json"
    early.write_text(json.dumps(_game_export("p1", [{"id": "eq-1", "status": "accepted"}])))
    late.write_text(json.dumps(_game_export("p1", [{"id": "eq-1", "status": "rejected"}])))
    papers, _ = load_decisions([late, early])  # deliberately out of order
    assert len(papers["p1"].decisions) == 1
    assert papers["p1"].decisions[0].status == "rejected"


def test_unknown_statuses_are_reported_not_counted(tmp_path):
    path = tmp_path / "d.json"
    path.write_text(json.dumps(_game_export("p1", [{"id": "eq-1", "status": "maybe"}])))
    papers, unrecognised = load_decisions([path])
    assert unrecognised == {"maybe": 1}
    assert papers["p1"].decisions == ()


def test_corrected_parts_are_all_kept(tmp_path):
    """``parts`` is the multi-formula fix contract; ``note`` is only part 1."""
    path = tmp_path / "d.json"
    path.write_text(
        json.dumps(
            _game_export(
                "p1",
                [
                    {
                        "id": "eq-1",
                        "status": "corrected",
                        "note": "a \\le b",
                        "parts": ["a \\le b", "c \\le d"],
                    }
                ],
            )
        )
    )
    papers, _ = load_decisions([path])
    assert papers["p1"].decisions[0].replacement == ("a \\le b", "c \\le d")


# --------------------------------------------------------------------------- #
# The success path
# --------------------------------------------------------------------------- #


def test_accepted_formulas_become_a_validated_formulation(workspace):
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace)

    assert report["promoted"] == 1
    entry = report["papers"][0]["entry_id"]
    written = load(workspace["dirs"]["formulations"] / f"{entry}.json")
    assert written.family == "milp"
    assert written.objective is not None
    assert len(written.constraints) == 2
    assert {v.name for v in written.variables} == {"t", "y"}


def test_a_provenance_record_is_written_alongside(workspace):
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace)
    entry = report["papers"][0]["entry_id"]

    record = json.loads((workspace["dirs"]["provenance"] / f"{entry}.json").read_text())
    assert record["source_id"] == entry
    assert record["priority_cell"] == "P3"
    assert (record["domain_shell"], record["activity"]) == ("railway", "operations")
    # Scopus is the authoritative count and wins over the OpenAlex one.
    assert record["citation_count"] == 51
    assert record["quality_tier"] == "unranked"  # no venue-tier table on disk


def test_the_ingested_document_is_kept_for_audit(workspace):
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace)
    entry = report["papers"][0]["entry_id"]

    document = (workspace["dirs"]["promoted"] / f"{entry}.tex").read_text()
    assert f"%@ meta id={entry} family=milp" in document
    assert "%@ name :: A big-M ordering model" in document
    assert "%@ prov source :: 10.1016/j.test.2020.01" in document
    assert document.count(r"\tag{") == len(workspace["rows"])
    rewrites = json.loads((workspace["dirs"]["promoted"] / f"{entry}.rewrites.json").read_text())
    assert rewrites["rules_version"]


def test_promotion_is_deterministic(workspace):
    _write_decisions(workspace, _accept_all(workspace))
    first = _promote(workspace)
    entry = first["papers"][0]["entry_id"]
    formulation = (workspace["dirs"]["formulations"] / f"{entry}.json").read_text()
    document = (workspace["dirs"]["promoted"] / f"{entry}.tex").read_text()

    second = _promote(workspace)
    assert second == first
    assert (workspace["dirs"]["formulations"] / f"{entry}.json").read_text() == formulation
    assert (workspace["dirs"]["promoted"] / f"{entry}.tex").read_text() == document


def test_dry_run_writes_nothing(workspace):
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace, write=False)
    assert report["promoted"] == 1
    assert list(workspace["dirs"]["formulations"].iterdir()) == []
    assert list(workspace["dirs"]["promoted"].iterdir()) == []


def test_corrected_latex_replaces_the_extraction(workspace):
    """A corrected verdict must promote the reviewer's LaTeX, not the extractor's."""
    dossier = workspace["dossier"]
    broken = dossier.model_copy(deep=True)
    broken.formulas[1].latex = r"t_{j} - t_{i} + M \ge \text{garbled}"
    broken.save(workspace["dirs"]["dossiers"])

    decisions = [{"id": f.id, "status": "accepted", "note": None} for f in dossier.formulas]
    decisions[1] = {
        "id": dossier.formulas[1].id,
        "status": "corrected",
        "note": dossier.formulas[1].latex,
    }
    _write_decisions(workspace, _game_export(dossier.key, decisions))

    report = _promote(workspace)
    assert report["promoted"] == 1
    assert report["formula_decisions"] == {"accepted": 2, "corrected": 1}


# --------------------------------------------------------------------------- #
# Failure causes
# --------------------------------------------------------------------------- #


def _cause(report: dict) -> str:
    return report["papers"][0]["cause"]


def test_missing_declarations_fail_and_leave_a_fillable_stub(workspace):
    (workspace["dirs"]["declarations"] / f"{workspace['dossier'].key}.tex").unlink()
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace)

    assert _cause(report) == "missing_declarations"
    assert report["failures_by_category"] == {"under_specified": 1}
    stub = (workspace["dirs"]["declarations"] / f"{workspace['dossier'].key}.stub.tex").read_text()
    assert "%@ obj sense=? name=objective" in stub
    for symbol in ("t", "y", "M", "h"):
        assert f"%@ var {symbol} " in stub
    # Every constraint row is pre-named so the reviewer never invents a binding.
    assert "%@ con eq_0001 " in stub


def test_a_stub_left_unedited_does_not_promote_a_guessed_model(workspace):
    """The placeholders must fail loudly rather than resolve to defaults."""
    key = workspace["dossier"].key
    (workspace["dirs"]["declarations"] / f"{key}.tex").unlink()
    _write_decisions(workspace, _accept_all(workspace))
    _promote(workspace)

    stub = workspace["dirs"]["declarations"] / f"{key}.stub.tex"
    stub.rename(workspace["dirs"]["declarations"] / f"{key}.tex")
    report = _promote(workspace)
    assert report["promoted"] == 0
    assert report["papers"][0]["category"] in ("extraction_error", "outside_grammar")


def test_all_rejected_is_an_extraction_error(workspace):
    dossier = workspace["dossier"]
    _write_decisions(
        workspace,
        _game_export(dossier.key, [{"id": f.id, "status": "rejected"} for f in dossier.formulas]),
    )
    report = _promote(workspace)
    assert _cause(report) == "all_rejected"
    assert report["failures_by_cause"]["all_rejected"]["category"] == "extraction_error"


def test_a_correction_without_replacement_text_is_reported(workspace):
    dossier = workspace["dossier"]
    decisions = [{"id": f.id, "status": "accepted"} for f in dossier.formulas]
    decisions[1] = {"id": dossier.formulas[1].id, "status": "corrected", "note": "   "}
    _write_decisions(workspace, _game_export(dossier.key, decisions))
    report = _promote(workspace)
    assert _cause(report) == "corrected_without_replacement"
    assert dossier.formulas[1].id in report["papers"][0]["detail"]


def test_a_model_without_an_objective_is_under_specified(workspace):
    dossier = workspace["dossier"]
    decisions = [{"id": f.id, "status": "accepted"} for f in dossier.formulas]
    decisions[0] = {"id": dossier.formulas[0].id, "status": "rejected"}  # the objective row
    _write_decisions(workspace, _game_export(dossier.key, decisions))
    report = _promote(workspace)
    assert _cause(report) == "no_objective"


def test_two_objective_rows_are_reported_not_silently_merged(workspace):
    """``from_canonical_latex`` keeps the last objective row it sees; a second one
    would vanish without a word."""
    dossier = workspace["dossier"]
    extra = dossier.model_copy(deep=True)
    extra.formulas.append(
        FormulaRecord(
            id="eq-0099",
            latex=r"\max \sum_{i \in \mathcal{I}} t_{i}",
            method=ExtractionMethod.mathml,
        )
    )
    extra.save(workspace["dirs"]["dossiers"])
    _write_decisions(
        workspace,
        _game_export(dossier.key, [{"id": f.id, "status": "accepted"} for f in extra.formulas]),
    )
    report = _promote(workspace)
    assert _cause(report) == "multiple_objectives"
    assert report["failures_by_cause"]["multiple_objectives"]["category"] == "outside_grammar"


def test_ungrammatical_latex_is_reported_as_outside_the_grammar(workspace):
    dossier = workspace["dossier"]
    garbled = dossier.model_copy(deep=True)
    garbled.formulas[
        1
    ].latex = r"A_{run} = \left\{\left(e , e^{'}\right) \in E_{de} : t r_{e} = t r_{e^{'}}\right\}"
    garbled.save(workspace["dirs"]["dossiers"])
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace)
    assert _cause(report) == "outside_grammar"
    assert report["papers"][0]["detail"]


def test_an_unsorted_paper_is_a_pipeline_gap_not_a_source_finding(workspace):
    _write_decisions(workspace, _accept_all(workspace, cell=None))
    report = _promote(workspace)
    assert _cause(report) == "not_sorted"
    assert report["failures_by_category"] == {"pipeline_incomplete": 1}
    assert list(workspace["dirs"]["provenance"].iterdir()) == []


def test_decisions_for_a_paper_with_no_dossier_are_reported(workspace):
    _write_decisions(
        workspace, _game_export("10.1/never-fetched", [{"id": "eq-1", "status": "accepted"}])
    )
    report = _promote(workspace)
    assert _cause(report) == "no_dossier"
    assert report["failures_by_cause"]["no_dossier"]["category"] == "pipeline_incomplete"


def test_a_foreign_entry_id_is_never_overwritten(workspace):
    """Promotion must not clobber another source's corpus entry (or a seed template)."""
    _write_decisions(workspace, _accept_all(workspace))
    entry = promote.entry_id_for(workspace["dossier"].key)
    (workspace["dirs"]["provenance"] / f"{entry}.json").write_text(
        json.dumps({"source_id": "someone_else"})
    )
    report = _promote(workspace)
    assert _cause(report) == "id_conflict"
    assert json.loads((workspace["dirs"]["provenance"] / f"{entry}.json").read_text()) == {
        "source_id": "someone_else"
    }


def test_papers_reviewed_but_undecided_are_not_a_source_finding(workspace):
    _write_decisions(
        workspace,
        _game_export(
            workspace["dossier"].key,
            [{"id": f.id, "status": "unreviewed"} for f in workspace["dossier"].formulas],
        ),
    )
    report = _promote(workspace)
    assert _cause(report) == "not_reviewed"


# --------------------------------------------------------------------------- #
# Assembly rules and the report
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (r"\min Z = \sum_{i} c_{i}", r"\min \sum_{i} c_{i}"),
        (r"\max Z_{1} = \sum_{i} c_{i}", r"\max \sum_{i} c_{i}"),
        (r"\min \sum_{i} c_{i}", r"\min \sum_{i} c_{i}"),
        # A comparison is not a label: nothing may be stripped here.
        (r"\min \sum_{i} c_{i} = 0", r"\min \sum_{i} c_{i} = 0"),
    ],
)
def test_objective_labels_are_stripped_but_algebra_is_not(raw, expected):
    assert promote.strip_objective_label(raw).strip() == expected


def test_row_names_are_identifiers_derived_from_the_formula_id():
    assert promote.row_name("eq-0007", 0, 1) == "eq_0007"
    assert promote.row_name("eq-0007", 1, 3) == "eq_0007_b"
    assert promote.row_name("0007", 0, 1) == "c_0007"


def test_family_follows_the_declared_variable_domains():
    assert promote.declared_family("%@ var x shape=- domain=binary role=primary") == "milp"
    assert promote.declared_family("%@ var x shape=- domain=non_negative role=primary") == "lp"
    assert promote.declared_family("%@ meta family=mip\n%@ var x domain=binary") == "mip"


def test_the_report_names_the_causes_it_cannot_decide(workspace):
    _write_decisions(workspace, _accept_all(workspace))
    report = _promote(workspace, write=False)
    assert set(report["not_assessed_here"]) == {
        "missing_instance_data",
        "cross_solver_disagreement",
    }
    assert promote.render_report_md(report).startswith("# Promotion report")


def test_every_cause_the_code_can_emit_is_in_the_taxonomy():
    """A cause with no category would render an empty column in the paper's table."""
    emitted = set(
        re.findall(r'fail\(\s*"([a-z_]+)"', promote.__loader__.get_source(promote.__name__))
    )
    emitted |= {"no_dossier"}
    assert emitted <= set(promote.CAUSES)
    assert all(
        cat in {"extraction_error", "outside_grammar", "under_specified", "pipeline_incomplete"}
        for cat, _ in promote.CAUSES.values()
    )


def test_paper_decisions_index_by_formula_id():
    decisions = PaperDecisions(
        paper_key="p", decisions=(promote.Decision("p", "eq-1", "accepted"),)
    )
    assert decisions.by_formula()["eq-1"].status == "accepted"


def test_promote_paper_accepts_an_explicit_output_layout(workspace, tmp_path):
    """The write targets are injectable, so a caller can promote into a staging tree."""
    _write_decisions(workspace, _accept_all(workspace))
    papers, _ = load_decisions(sorted(workspace["dirs"]["decisions"].glob("*.json")))
    staging = tmp_path / "staging"
    outcome = promote_paper(
        workspace["dossier"],
        papers[workspace["dossier"].key],
        declarations_dir=workspace["dirs"]["declarations"],
        out_dirs={n: staging / n for n in ("formulations", "provenance", "promoted")},
    )
    assert outcome.promoted
    assert (staging / "formulations" / f"{outcome.entry_id}.json").exists()
