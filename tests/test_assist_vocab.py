"""Stage V (vocabulary fill): the fill-in list in, parser-gated %@ lines out."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import corpusbuilder.assist as assist
from corpusbuilder.assist import Workspace, fill_vocab_paper, validate_vocab

_PROMOTED = """% lp2graph canonical LaTeX — assembled by corpusbuilder.promote
%@ meta id=p1 family=milp schema=0.1.0
%@ name :: Probe
%@ index I ordered=0 cyclic=0 :: items
%@ var x shape=I domain=binary role=primary drole=- lo=- hi=- :: choice
%@ obj sense=min name=objective combination=sum :: cost
\\begin{align}
  \\min\\quad & \\sum_{i \\in \\mathcal{I}} \\mathit{beta} \\cdot x_{i} + t_{i}^{arr} \\tag{eq\\_0001} \\\\
  & t_{i}^{arr} \\le M \\forall i \\in \\mathcal{I} \\tag{eq\\_0002} \\\\
\\end{align}
"""

_SIDECAR = """% Declaration sidecar for p1
%@ index I ordered=0 cyclic=0 :: items
%@ var x shape=I domain=binary role=primary drole=- lo=- hi=- :: choice
%@ obj sense=min name=objective combination=sum :: cost
"""

GOOD = {
    "declarations_add": [
        "%@ param beta shape=- kind=scalar domain=- :: weight",
        "%@ var t_arr shape=I domain=continuous role=primary drole=- lo=- hi=- :: arrival time",
        "%@ param M shape=- kind=big_m domain=- :: big M",
    ],
    "unsure": [],
}


class FakeChat:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def __call__(self, payload):
        self.calls.append(payload)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return json.dumps(reply), {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.fixture
def ws(tmp_path: Path) -> Workspace:
    w = Workspace(
        dossiers=tmp_path / "dossiers",
        prose=tmp_path / "prose",
        decisions=tmp_path / "decisions",
        declarations=tmp_path / "declarations",
        assist=tmp_path / "assist",
        promoted=tmp_path / "promoted",
    )
    for d in (w.dossiers, w.prose, w.decisions, w.declarations, w.promoted):
        d.mkdir()
    (w.promoted / "p1.tex").write_text(_PROMOTED, encoding="utf-8")
    (w.declarations / "p1.tex").write_text(_SIDECAR, encoding="utf-8")
    return w


def test_validate_vocab_gates_names_shapes_and_vocabularies():
    allowed = {"beta", "t_arr", "M"}
    assert validate_vocab(GOOD, allowed, {"I"}) == []
    bad = {
        "declarations_add": [
            "%@ param gamma shape=- kind=scalar domain=- :: not listed",
            "%@ var t_arr shape=K domain=continuous role=primary :: undeclared family",
            "%@ var beta shape=- domain=real role=primary :: bad domain",
            "%@ param M shape=- kind=huge domain=- :: bad kind",
        ],
        "unsure": ["zzz"],
    }
    errors = validate_vocab(bad, allowed, {"I"})
    assert any("not on the fill-in list" in e for e in errors)
    assert any("undeclared indices: K" in e for e in errors)
    assert any("domain must be" in e for e in errors)
    assert any("kind must be" in e for e in errors)
    assert any("unsure: 'zzz'" in e for e in errors)
    # silence is not an answer: every listed name is declared or unsure
    errors = validate_vocab({"declarations_add": [], "unsure": ["beta"]}, allowed, {"I"})
    assert errors == [
        "every name on the fill-in list needs a %@ line or an 'unsure' entry; missing: M, t_arr"
    ]
    # an index added in the same reply may be used by a shape
    ok = {
        "declarations_add": [
            "%@ index K ordered=0 cyclic=0 :: periods",
            "%@ var t_arr shape=K domain=continuous role=primary drole=- lo=- hi=- :: t",
        ],
        "unsure": [],
    }
    assert validate_vocab(ok, {"t_arr", "K"}, {"I"}) == []


def test_fill_appends_a_marked_block_and_a_rerun_is_a_noop(ws: Workspace, monkeypatch):
    fake = FakeChat([GOOD])
    monkeypatch.setattr(assist, "_chat", fake)
    run = fill_vocab_paper(ws, "p1", today="2026-09-10", probe=False)
    assert run.stages["v"].startswith("done: +3 declared, 0 unsure of 3 listed")
    sidecar = (ws.declarations / "p1.tex").read_text(encoding="utf-8")
    assert sidecar.startswith(_SIDECAR.rstrip())
    assert assist.VOCAB_MARK in sidecar
    assert "%@ var t_arr shape=I" in sidecar and "%@ param beta" in sidecar
    # the fill-in list the model saw is exactly the deterministic list
    user = json.loads(fake.calls[0]["messages"][1]["content"])
    assert {e["name"] for e in user["fill_in"]} == {"beta", "t_arr", "M"}
    assert user["already_declared_names"] == ["I", "x"]
    # second run: the audit reads the CURRENT sidecar, nothing is missing
    run2 = fill_vocab_paper(ws, "p1", today="2026-09-10", probe=False)
    assert run2.stages["v"] == "skipped: vocabulary complete"
    assert len(fake.calls) == 1
    assert sidecar == (ws.declarations / "p1.tex").read_text(encoding="utf-8")


def test_fill_reasks_with_validation_errors_then_fails_honestly(ws: Workspace, monkeypatch):
    bad = {"declarations_add": ["%@ param gamma shape=- kind=scalar domain=- :: no"], "unsure": []}
    fake = FakeChat([bad, bad, bad, bad])
    monkeypatch.setattr(assist, "_chat", fake)
    run = fill_vocab_paper(ws, "p1", today="2026-09-10", probe=False, retries=1)
    assert run.stages["v"] == "failed: reply never validated"
    assert len(fake.calls) == 2
    feedback = json.loads(fake.calls[1]["messages"][1]["content"])["feedback"]
    assert any("not on the fill-in list" in f for f in feedback)
    assert assist.VOCAB_MARK not in (ws.declarations / "p1.tex").read_text(encoding="utf-8")


def test_fill_skips_without_document_or_sidecar(ws: Workspace):
    assert (
        fill_vocab_paper(ws, "nope", today="x", probe=False)
        .stages["v"]
        .startswith("skipped: no assembled")
    )
    (ws.declarations / "p1.tex").unlink()
    assert (
        fill_vocab_paper(ws, "p1", today="x", probe=False)
        .stages["v"]
        .startswith("skipped: no sidecar")
    )


def test_foreign_endpoint_gets_its_own_key_and_no_deepseek_fields(monkeypatch):
    monkeypatch.setenv("ASSIST_BASE_URL", "https://llm.scads.ai/v1")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-secret")
    monkeypatch.delenv("ASSIST_API_KEY", raising=False)
    with pytest.raises(assist.AssistError):
        assist._api_key()
    monkeypatch.setenv("ASSIST_API_KEY", "scads-key")
    assert assist._api_key() == "scads-key"
    assert assist._stage_extras("v") == {}
    monkeypatch.delenv("ASSIST_BASE_URL")
    assert assist._api_key() == "ds-secret"
    assert assist._stage_extras("v") == {"thinking": {"type": "disabled"}}


def test_shape_letters_are_repaired_to_families_before_validation():
    from corpusbuilder.assist import repair_vocab_shapes

    reply = {
        "declarations_add": [
            "%@ var t_arr shape=i,k domain=continuous role=primary :: t",
            "%@ param w shape=i,q kind=vector domain=- :: w",
        ],
        "unsure": [],
    }
    out = repair_vocab_shapes(reply, {"i": ["I"], "k": ["K"], "q": ["Q", "R"]})
    assert out["declarations_add"][0].startswith("%@ var t_arr shape=I,K ")
    # q is bound to two families: left for validation to refuse
    assert out["declarations_add"][1].startswith("%@ param w shape=I,q ")
