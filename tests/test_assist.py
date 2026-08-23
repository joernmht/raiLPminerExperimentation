"""Assisted resolution — the rung-(c) annotator writes what promote reads.

The fixtures follow the house pattern of ``tests/test_promote.py``: a tiny
synthetic dossier whose formulas exercise every deterministic helper (an
objective, a binder family, a domain row, a rejectable metaheuristic update),
plus a prose digest in the ``prose-1`` schema. The LLM is a canned fake — every
test runs fully offline — and the round-trip tests do not re-model promote's
readers, they import and call them.
"""

from __future__ import annotations

import json

import pytest

import corpusbuilder.assist as assist
from corpusbuilder import promote
from corpusbuilder.assist import (
    Workspace,
    annotate_paper,
    validate_sidecar,
    validate_symbols,
    validate_triage,
)
from corpusbuilder.dossier import Dossier, ExtractionMethod, FormulaRecord, SourceInfo

TODAY = "2026-08-23"

#: id, the paper's own label, LaTeX. One objective, one constraint over a binder
#: family, one domain row, one PSO update (non-optimization content to reject).
FORMULAS = [
    ("eq-0000", "(1)", r"\min \sum_{i \in I} c_{i} x_{i}"),
    ("eq-0001", "(2)", r"\sum_{i \in I} a_{i} x_{i} \le b"),
    ("eq-0002", "(3)", r"x_{i} \in \{0,1\}"),
    ("eq-0003", "(4)", r"v_{k+1} = w \cdot v_{k} + r_{1}"),
]

PROSE = {
    "schema_version": "prose-1",
    "abstract": "We schedule trains under disruption.",
    "paras": [
        {"i": 0, "text": "Introduction far from any formula.", "formula_labels": []},
        {
            "i": 1,
            "text": "Here c_i denotes the unit cost coefficient of item i.",
            "formula_labels": ["(1)"],
        },
        {"i": 2, "text": "The budget b limits the selection.", "formula_labels": []},
        {
            "i": 3,
            "text": "Unrelated methodology paragraph about tabu search.",
            "formula_labels": [],
        },
    ],
    "deflists": [{"term": "I", "def": "set of candidate items"}],
}

GOOD_TRIAGE = {
    "cell": "P1",
    "decisions": [
        {"id": "eq-0000", "status": "accepted", "reason": "objective"},
        {"id": "eq-0001", "status": "accepted", "reason": "capacity"},
        {"id": "eq-0002", "status": "accepted", "reason": "domain row"},
        {"id": "eq-0003", "status": "rejected", "reason": "PSO update"},
    ],
}

GOOD_SYMBOLS = {
    "symbols": {
        "c": {"kind": "parameter", "desc": "unit cost", "shape": "I"},
        "x": {"kind": "variable", "desc": "selection", "domain": "binary", "shape": "I"},
        "I": {"kind": "index", "desc": "candidate items"},
        "a": {"kind": "parameter", "desc": "weight", "shape": "I"},
        "b": {"kind": "parameter", "desc": "budget"},
    }
}

GOOD_SIDECAR = "\n".join(
    [
        "%@ obj sense=min name=objective combination=sum :: total cost",
        "%@ index I ordered=0 cyclic=0 :: candidate items",
        "%@ param c shape=I kind=vector domain=- :: unit cost",
        "%@ param a shape=I kind=vector domain=- :: weight",
        "%@ param b shape=- kind=scalar domain=- :: budget",
        "%@ var x shape=I domain=binary role=primary drole=- lo=- hi=- :: selection",
        "%@ con eq_0001 kind=linear domain=- indicator=- :: capacity",
        "%@ con eq_0002 kind=linear domain=- indicator=- :: domain row",
    ]
)

GOOD = {"a": [GOOD_TRIAGE], "b": [GOOD_SYMBOLS], "c": [{"sidecar": GOOD_SIDECAR}]}


class FakeChat:
    """A canned ``_chat``: per-stage reply queues, every payload recorded."""

    def __init__(self, replies: dict):
        self.replies = {stage: list(queue) for stage, queue in replies.items()}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, payload: dict) -> tuple[str, dict]:
        system = payload["messages"][0]["content"]
        stage = (
            "a" if system.startswith("Stage A") else "b" if system.startswith("Stage B") else "c"
        )
        self.calls.append((stage, payload))
        queue = self.replies[stage]
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        return json.dumps(reply), {
            "prompt_tokens": 100,
            "prompt_cache_hit_tokens": 20,
            "prompt_cache_miss_tokens": 80,
            "completion_tokens": 50,
        }

    def user_content(self, stage: str) -> list[str]:
        return [p["messages"][1]["content"] for s, p in self.calls if s == stage]


def _dossier(
    doi="10.1016/j.test.2026.001", formulas=FORMULAS, title="A knapsack rescheduling model"
):
    return Dossier(
        source=SourceInfo(title=title, doi=doi, venue="Transportation Research Part B", year=2026),
        formulas=[
            FormulaRecord(id=fid, label=label, latex=latex, method=ExtractionMethod.mathml)
            for fid, label, latex in formulas
        ],
    )


@pytest.fixture
def workspace(tmp_path):
    ws = Workspace(
        dossiers=tmp_path / "dossiers",
        prose=tmp_path / "prose",
        decisions=tmp_path / "decisions",
        declarations=tmp_path / "declarations",
        assist=tmp_path / "assist",
    )
    for directory in (ws.dossiers, ws.prose, ws.decisions, ws.declarations):
        directory.mkdir()
    dossier = _dossier()
    dossier.save(ws.dossiers)
    (ws.prose / f"{dossier.key}.json").write_text(json.dumps(PROSE), encoding="utf-8")
    return {"ws": ws, "dossier": dossier}


def _patch_chat(monkeypatch, replies=GOOD) -> FakeChat:
    fake = FakeChat(replies)
    monkeypatch.setattr(assist, "_chat", fake)
    return fake


def _rows(dossier, triage=GOOD_TRIAGE):
    rows, _counts = promote.rows_for(dossier, assist._paper_decisions(dossier, triage))
    return rows


# --------------------------------------------------------------------------- #
# The export round-trips through promote's own readers
# --------------------------------------------------------------------------- #


def test_export_round_trips_through_promote_loaders(workspace, monkeypatch):
    _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    run = annotate_paper(ws, dossier.key, upto="b", today=TODAY)
    assert run.stages == {"a": "done", "b": "done"}

    path = ws.decisions / f"assist_{dossier.key}.json"
    payload = json.loads(path.read_text())
    assert payload["schema_version"] == "game-decisions-3"
    assert payload["source"].startswith("corpusbuilder.assist ")

    papers, unrecognised = promote.load_decisions([path])
    assert unrecognised == {}
    decisions = papers[dossier.key]
    assert decisions.cell == "P1"
    assert decisions.doi == dossier.source.doi
    assert {d.formula_id: d.status for d in decisions.decisions} == {
        "eq-0000": "accepted",
        "eq-0001": "accepted",
        "eq-0002": "accepted",
        "eq-0003": "rejected",
    }
    tables = promote.load_symbol_tables([path])
    assert tables[dossier.key] == {
        "I": "index",
        "a": "parameter",
        "b": "parameter",
        "c": "parameter",
        "x": "variable",
    }


def test_corrected_parts_follow_the_game_contract(workspace, monkeypatch):
    """``note`` must equal ``parts[0]`` — single-part readers take note alone."""
    parts = [r"\sum_{i \in I} a_{i} x_{i} \le b", r"x_{i} \ge 0"]
    triage = {
        "cell": "P1",
        "decisions": [
            {"id": "eq-0000", "status": "accepted"},
            {"id": "eq-0001", "status": "corrected", "parts": parts},
            {"id": "eq-0002", "status": "duplicate", "duplicate_of": "eq-0001"},
            {"id": "eq-0003", "status": "rejected"},
        ],
    }
    _patch_chat(monkeypatch, {"a": [triage]})
    ws, dossier = workspace["ws"], workspace["dossier"]
    annotate_paper(ws, dossier.key, upto="a", today=TODAY)

    path = ws.decisions / f"assist_{dossier.key}.json"
    exported = json.loads(path.read_text())["formula_decisions"][0]["decisions"]
    corrected = next(d for d in exported if d["id"] == "eq-0001")
    assert corrected["note"] == parts[0]
    assert corrected["parts"] == parts

    papers, _ = promote.load_decisions([path])
    by_id = papers[dossier.key].by_formula()
    assert by_id["eq-0001"].replacement == tuple(parts)
    assert by_id["eq-0002"].duplicate_of == "eq-0001"


def test_out_of_scope_paper_cannot_promote(workspace, monkeypatch):
    """The game's out-of-scope value is outside promote's P1–P5 taxonomy, and the
    later stages are skipped: a sidecar for an out-of-scope paper would be an
    invitation to promote it anyway."""
    triage = dict(GOOD_TRIAGE, cell="out_of_scope")
    fake = _patch_chat(monkeypatch, {"a": [triage]})
    ws, dossier = workspace["ws"], workspace["dossier"]
    run = annotate_paper(ws, dossier.key, upto="c", today=TODAY)

    assert run.stages["b"] == run.stages["c"] == "skipped: out_of_scope"
    assert [stage for stage, _p in fake.calls] == ["a"]
    assert not (ws.declarations / f"{dossier.key}.tex").exists()

    path = ws.decisions / f"assist_{dossier.key}.json"
    papers, _ = promote.load_decisions([path])
    cell = papers[dossier.key].cell
    assert cell == "out_of_scope"
    assert cell not in promote.CELL_TAXONOMY


# --------------------------------------------------------------------------- #
# Sidecar validation
# --------------------------------------------------------------------------- #


def test_sidecar_validation_passes_a_good_fill(workspace):
    assert validate_sidecar(GOOD_SIDECAR, _rows(workspace["dossier"])) == []


def test_sidecar_validation_catches_bad_tokens(workspace):
    bad = "\n".join(
        [
            "%@ obj sense=argmin name=objective combination=product :: bad",
            "%@ index I ordered=2 cyclic=0 :: bad flag",
            "%@ param c shape=J kind=fuzzy :: bad kind, undeclared shape",
            "%@ var x shape=- domain=positive role=chief :: bad var",
            "%@ var x shape=- domain=binary role=primary :: duplicate",
            "%@ con eq_9999 kind=linear :: unknown row",
        ]
    )
    joined = "\n".join(validate_sidecar(bad, _rows(workspace["dossier"])))
    assert "sense must be min or max" in joined
    assert "combination must be one of" in joined
    assert "ordered must be 0 or 1" in joined
    assert "kind must be one of" in joined
    assert "undeclared indices: J" in joined
    assert "domain must be one of" in joined
    assert "role must be one of" in joined
    assert "already declared" in joined
    assert "unknown constraint row 'eq_9999'" in joined


def test_sidecar_validation_rejects_generated_records_and_placeholders(workspace):
    rows = _rows(workspace["dossier"])
    bad = "\n".join(
        [
            "%@ meta family=milp",
            "%@ obj sense=? name=objective combination=sum :: unfilled",
            "stray non-comment line",
        ]
    )
    joined = "\n".join(validate_sidecar(bad, rows))
    assert "generated from the dossier" in joined
    assert "placeholder '?' left unfilled" in joined
    assert "not a %@ declaration or % comment" in joined
    # Exactly one objective line is required, never zero or two.
    assert "exactly one %@ obj line" in "\n".join(validate_sidecar("% only a comment", rows))


def test_annotated_sidecar_is_validated_and_marked_non_deterministic(workspace, monkeypatch):
    _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    run = annotate_paper(ws, dossier.key, upto="c", today=TODAY)
    assert run.stages["c"] == "done"

    text = (ws.declarations / f"{dossier.key}.tex").read_text()
    assert "Non-deterministically sourced; pending human confirmation." in text
    assert "rung (c)" in text
    assert assist.model_id() in text
    assert TODAY in text
    # The header is comments only; promote's parser keeps just the %@ lines.
    assert all(line.startswith("%") for line in text.splitlines() if line.strip())
    assert validate_sidecar(text, _rows(dossier)) == []


def test_stage_c_retries_with_the_validation_errors_fed_back(workspace, monkeypatch):
    bad = GOOD_SIDECAR.replace("kind=vector", "kind=fuzzy", 1)
    fake = _patch_chat(
        monkeypatch,
        {
            "a": [GOOD_TRIAGE],
            "b": [GOOD_SYMBOLS],
            "c": [{"sidecar": bad}, {"sidecar": GOOD_SIDECAR}],
        },
    )
    ws, dossier = workspace["ws"], workspace["dossier"]
    run = annotate_paper(ws, dossier.key, upto="c", today=TODAY)

    assert run.stages["c"] == "done"
    c_prompts = fake.user_content("c")
    assert len(c_prompts) == 2
    assert "Your previous reply was rejected" in c_prompts[1]
    assert "kind must be one of" in c_prompts[1]
    assert (
        validate_sidecar((ws.declarations / f"{dossier.key}.tex").read_text(), _rows(dossier)) == []
    )


# --------------------------------------------------------------------------- #
# Reply validation + re-ask
# --------------------------------------------------------------------------- #


def test_triage_is_re_asked_once_on_a_schema_violation(workspace, monkeypatch):
    incomplete = {"cell": "P1", "decisions": GOOD_TRIAGE["decisions"][:3]}
    fake = _patch_chat(monkeypatch, {"a": [incomplete, GOOD_TRIAGE]})
    ws, dossier = workspace["ws"], workspace["dossier"]
    run = annotate_paper(ws, dossier.key, upto="a", today=TODAY)

    assert run.stages["a"] == "done"
    prompts = fake.user_content("a")
    assert len(prompts) == 2
    assert "missing decisions for: eq-0003" in prompts[1]


def test_triage_validation_names_the_violations():
    ids = ["eq-0000", "eq-0001"]
    errors = validate_triage(
        {
            "cell": "P9",
            "decisions": [
                {"id": "eq-0000", "status": "maybe"},
                {"id": "eq-0001", "status": "corrected"},
                {"id": "eq-0404", "status": "accepted"},
            ],
        },
        ids,
    )
    joined = "\n".join(errors)
    assert "cell must be one of" in joined
    assert "status must be one of" in joined
    assert "corrected needs a non-empty 'parts'" in joined
    assert "unknown formula id 'eq-0404'" in joined
    assert validate_triage(GOOD_TRIAGE, [f[0] for f in FORMULAS]) == []


def test_symbol_replies_may_not_contradict_the_deterministic_evidence(workspace):
    rows = _rows(workspace["dossier"])
    worklist, evidence = assist.symbol_worklist(rows)
    assert worklist == ["c", "x", "I", "a", "b"]  # bound letter i excluded

    assert validate_symbols(GOOD_SYMBOLS, worklist, evidence) == []
    contradicting = {
        "symbols": {**GOOD_SYMBOLS["symbols"], "I": {"kind": "parameter", "desc": "horizon"}}
    }
    joined = "\n".join(validate_symbols(contradicting, worklist, evidence))
    assert "contradicts the given facts" in joined
    missing = {"symbols": {k: v for k, v in GOOD_SYMBOLS["symbols"].items() if k != "b"}}
    assert any("missing symbol 'b'" in e for e in validate_symbols(missing, worklist, evidence))


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #


def test_a_second_run_is_served_entirely_from_cache(workspace, monkeypatch):
    fake = _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    first = annotate_paper(ws, dossier.key, upto="c", today=TODAY)
    assert first.usage.calls == 3
    calls_after_first = len(fake.calls)
    assert (ws.cache / f"{dossier.key}.a.json").exists()

    second = annotate_paper(ws, dossier.key, upto="c", today=TODAY)
    assert len(fake.calls) == calls_after_first  # zero new API calls
    assert second.usage.calls == 0
    assert second.usage.cache_hits == 3
    assert second.stages == first.stages


def test_force_stage_recomputes_only_that_stage(workspace, monkeypatch):
    fake = _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    annotate_paper(ws, dossier.key, upto="c", today=TODAY)
    before = len(fake.calls)

    annotate_paper(ws, dossier.key, upto="c", force={"b"}, today=TODAY)
    assert [stage for stage, _p in fake.calls[before:]] == ["b"]


# --------------------------------------------------------------------------- #
# Prompt assembly — linked prose and given facts
# --------------------------------------------------------------------------- #


def test_prompts_carry_linked_paragraphs_and_given_facts(workspace, monkeypatch):
    fake = _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    annotate_paper(ws, dossier.key, upto="b", today=TODAY)

    prompt = fake.user_content("b")[0]
    # The linked paragraph and its neighbours travel; a paragraph two away does not.
    assert "unit cost coefficient" in prompt
    assert "budget b limits the selection" in prompt
    assert "tabu search" not in prompt
    assert "set of candidate items" in prompt  # deflists always ride along
    # Deterministic evidence is handed over as given facts.
    assert '"index_families"' in prompt and '"I"' in prompt
    assert '"variable_domains"' in prompt and '"binary"' in prompt
    # Stage A saw the abstract.
    assert "schedule trains under disruption" in fake.user_content("a")[0]


def test_prompt_budget_trims_farthest_paragraphs_first(monkeypatch):
    prose = {
        "abstract": "A",
        "paras": [
            {"i": 0, "text": "n" * 50, "formula_labels": []},
            {"i": 1, "text": "L" * 50, "formula_labels": ["(1)"]},
            {"i": 2, "text": "m" * 50, "formula_labels": []},
        ],
        "deflists": [],
    }
    monkeypatch.setattr(assist, "PROMPT_CHAR_BUDGET", 1 + (50 + 40) + 10)
    tight = assist.prose_context(prose, {"(1)"}, fixed_len=0)
    assert [p["i"] for p in tight["paragraphs"]] == [1]  # linked survives, neighbours trimmed

    monkeypatch.setattr(assist, "PROMPT_CHAR_BUDGET", 100_000)
    roomy = assist.prose_context(prose, {"(1)"}, fixed_len=0)
    assert [p["i"] for p in roomy["paragraphs"]] == [0, 1, 2]  # reading order once picked


def test_missing_prose_degrades_to_formulas_only(workspace, monkeypatch):
    fake = _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    (ws.prose / f"{dossier.key}.json").unlink()
    run = annotate_paper(ws, dossier.key, upto="b", today=TODAY)
    assert run.stages == {"a": "done", "b": "done"}
    assert '"paragraphs": []' in fake.user_content("b")[0]


# --------------------------------------------------------------------------- #
# Ordering, feedback loop, report
# --------------------------------------------------------------------------- #


def test_all_ordering_is_ascending_symbol_table_size(workspace):
    ws = workspace["ws"]
    small = _dossier(
        doi="10.1016/j.small.2026.002", formulas=[("eq-0000", "(1)", r"\min x")], title="Small"
    )
    small.save(ws.dossiers)
    assert assist.order_keys(ws) == [small.key, workspace["dossier"].key]


def test_retry_causes_map_to_stages_and_terminal_causes_do_not():
    assert assist.RETRY_CAUSE_STAGE == {
        "multiple_objectives": "a",
        "corrected_without_replacement": "a",
        "outside_grammar": "r",
        "normalize_failed": "r",
        "semantic_invalid": "c",
        "missing_declarations": "c",
    }
    for terminal in (
        "all_rejected",
        "no_objective",
        "not_sorted",
        "not_reviewed",
        "no_dossier",
        "id_conflict",
    ):
        assert terminal not in assist.RETRY_CAUSE_STAGE
    # Every retryable cause is a real promotion cause, never an invented one.
    assert set(assist.RETRY_CAUSE_STAGE) <= set(promote.CAUSES)


def test_promote_loop_re_asks_the_implicated_stage_with_the_cause(workspace, monkeypatch):
    fake = _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    key = dossier.key
    runs = {key: annotate_paper(ws, key, upto="c", today=TODAY)}

    promote_calls = []

    def fake_promote(_ws, keys):
        promote_calls.append(sorted(keys))
        if len(promote_calls) == 1:
            return {
                "promoted": 0,
                "failed": 1,
                "papers": [{"paper_key": key, "promoted": False, "cause": "multiple_objectives"}],
            }
        return {"promoted": 1, "failed": 0, "papers": [{"paper_key": key, "promoted": True}]}

    report = assist.promote_loop(ws, [key], 2, runs, run_promote=fake_promote, today=TODAY)
    assert report["promoted"] == 1
    assert promote_calls == [[key], [key]]
    feedback_prompts = [
        p for p in fake.user_content("a") if "promotion failed with cause multiple_objectives" in p
    ]
    assert feedback_prompts, "the failing stage must be re-asked with the promotion cause"


def test_report_records_tokens_and_cost_at_both_rates(workspace, monkeypatch):
    _patch_chat(monkeypatch)
    ws, dossier = workspace["ws"], workspace["dossier"]
    run = annotate_paper(ws, dossier.key, upto="c", today=TODAY)
    report = assist.build_report([run], today=TODAY)
    assist.write_report(ws, report)

    data = json.loads((ws.assist / "report.json").read_text())
    tokens = data["totals"]["tokens"]
    assert tokens["calls"] == 3
    assert tokens["prompt_miss_tokens"] == 240 and tokens["prompt_hit_tokens"] == 60
    assert set(tokens["cost_usd"]) == {"standard", "off_peak"}
    assert tokens["cost_usd"]["off_peak"] == pytest.approx(
        tokens["cost_usd"]["standard"] * assist.OFF_PEAK_FACTOR
    )
    assert (ws.assist / "report.md").read_text().startswith("# Assisted resolution report")


def test_cost_model_applies_the_published_rates():
    usage = assist.Usage()
    usage.add(
        {"prompt_tokens": 1_000_000, "prompt_cache_miss_tokens": 1_000_000, "completion_tokens": 0},
        cached=False,
    )
    assert usage.cost_usd()["standard"] == pytest.approx(assist.IN_MISS_USD_PER_MTOK)
    hit_only = assist.Usage()
    hit_only.add(
        {"prompt_tokens": 1_000_000, "prompt_cache_hit_tokens": 1_000_000, "completion_tokens": 0},
        cached=False,
    )
    assert hit_only.cost_usd()["standard"] == pytest.approx(assist.IN_HIT_USD_PER_MTOK)
    assert hit_only.cost_usd()["off_peak"] == pytest.approx(
        assist.IN_HIT_USD_PER_MTOK * assist.OFF_PEAK_FACTOR
    )
