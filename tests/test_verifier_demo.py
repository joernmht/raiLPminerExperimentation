"""Offline tests for :mod:`railpminer.verifier_demo` (the Track B talk demo).

No test here talks to a network: the LLM is a canned :func:`_scripted_chat`
double and the HTTP layer is exercised through the injectable ``post``/``sleep``
hooks of :class:`~railpminer.verifier_demo.LLMClient`. The structural facts the
citation tests assert (``mip_2_8_pesp`` and ``pesp_solvable`` are
schema-isomorphic in lp2graph's own tree but NOT in the lab corpus, whose
``mip_2_8_pesp.json`` is the stale pre-2026-07-13 copy still carrying the
unused index ``T``; ``assignment`` is isomorphic to neither) were *measured*
with ``are_isomorphic``/``schema_graph_hash`` before being written down here,
and the tests re-measure them so a codec, hash, or corpus change surfaces as a
failure, not as a stale comment.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from railpminer import verifier_demo as vd
from railpminer.verifier_demo import (
    FEW_SHOT_TEX,
    LLMClient,
    LLMError,
    LLMReply,
    Scenario,
    ScenarioError,
    cite,
    load_scenario,
    resolve_endpoint,
    run_batch,
    run_scenario,
)

CORPUS_FORMULATIONS = Path(__file__).resolve().parent.parent / "corpus" / "formulations"

#: A syntactically hopeless reply: no format parses it, so the verdict is
#: guaranteed ``invalid`` and the repair loop must fire.
GARBAGE = "I think you should use a genetic algorithm instead. Good luck!"

#: A valid reply: the embedded few-shot itself (verified valid by test below).
VALID = FEW_SHOT_TEX


# --------------------------------------------------------------------------
# Test doubles
# --------------------------------------------------------------------------


class _ScriptedChat:
    """A ChatFn double that replays canned replies and records every request."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[tuple[list[dict[str, str]], float]] = []

    def __call__(self, messages: list[dict[str, str]], temperature: float) -> LLMReply:
        self.calls.append(([dict(m) for m in messages], temperature))
        if not self.replies:
            raise AssertionError("scripted chat ran out of replies")
        return LLMReply(
            content=self.replies.pop(0),
            usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
        )


@pytest.fixture()
def match_dir(tmp_path: Path) -> Path:
    """A small match corpus: the isomorphic pair plus one non-isomorphic seed."""
    d = tmp_path / "match"
    d.mkdir()
    for name in ("mip_2_8_pesp", "pesp_solvable", "assignment"):
        shutil.copy(CORPUS_FORMULATIONS / f"{name}.json", d / f"{name}.json")
    return d


# --------------------------------------------------------------------------
# The embedded few-shot is load-bearing: pin its properties
# --------------------------------------------------------------------------


def test_few_shot_roundtrips_and_validates_clean():
    from lp2graph.mining.ingest import ingest_latex
    from lp2graph.validation import validate_text

    result = ingest_latex(FEW_SHOT_TEX, source="few-shot")
    assert result.formulation is not None, [f.message for f in result.failures]
    assert result.formulation.id == vd.FEW_SHOT_ID
    report = validate_text(FEW_SHOT_TEX)
    assert report.verdict == "valid"


def test_measured_isomorphism_facts():
    """The structural facts the citation assertions rely on, re-measured.

    The few-shot (= ``pesp_solvable``) is schema-isomorphic to the lab corpus's
    ``pesp_solvable.json`` (identical schema graph, identical hash). The lab's
    ``mip_2_8_pesp.json`` is the stale copy that still declares the unused
    index ``T`` (lp2graph dropped it 2026-07-13), so the extra node makes it
    NOT isomorphic here even though the two differ "only by a bound" in
    lp2graph's own tree. That makes it the measured *near-relative* case.
    """
    from lp2graph import load
    from lp2graph.mining.corpusmgr.dedup import schema_graph_hash
    from lp2graph.mining.ingest import ingest_latex
    from lp2graph.mining.isomorphism.report import are_isomorphic

    pesp = load(CORPUS_FORMULATIONS / "mip_2_8_pesp.json")
    solvable = load(CORPUS_FORMULATIONS / "pesp_solvable.json")
    assignment = load(CORPUS_FORMULATIONS / "assignment.json")
    candidate = ingest_latex(FEW_SHOT_TEX, source="t").formulation
    assert candidate is not None
    assert are_isomorphic(candidate, solvable)
    assert schema_graph_hash(candidate) == schema_graph_hash(solvable)
    assert not are_isomorphic(candidate, pesp)  # stale unused index T = extra node
    assert not are_isomorphic(candidate, assignment)


# --------------------------------------------------------------------------
# Repair loop
# --------------------------------------------------------------------------


def test_invalid_then_valid_converges_in_two_rounds(tmp_path: Path, match_dir: Path):
    chat = _ScriptedChat([GARBAGE, VALID])
    result = run_scenario(
        Scenario(id="toy", text="Reschedule two trains on one track."),
        chat,
        rounds=5,
        match_dir=match_dir,
        dossier_dir=tmp_path / "no_dossiers",
        out_dir=tmp_path / "out",
        solve_check=False,
    )
    assert result.converged
    assert result.rounds_to_valid == 2
    assert result.final_verdict in ("valid", "valid_with_warnings")
    assert result.rounds_used == 2

    # The second request must carry the verifier's findings verbatim.
    second_messages = chat.calls[1][0]
    assert len(second_messages) == 4  # system, user, assistant, repair-user
    repair = second_messages[-1]["content"]
    assert "Verifier findings (verbatim):" in repair
    assert "INVALID" in repair  # first line of ValidationReport.summary()
    assert GARBAGE in repair  # previous output quoted back
    # Generation arm runs at temperature 0.
    assert all(t == vd.GENERATION_TEMPERATURE for _, t in chat.calls)


def test_transcript_and_report_structure(tmp_path: Path, match_dir: Path):
    chat = _ScriptedChat([GARBAGE, VALID])
    result = run_scenario(
        Scenario(id="toy", text="Reschedule two trains."),
        chat,
        match_dir=match_dir,
        dossier_dir=tmp_path / "no_dossiers",
        out_dir=tmp_path / "out",
        solve_check=False,
    )
    run_dir = result.run_dir
    assert (run_dir / "final.tex").read_text(encoding="utf-8") == VALID
    rows = [
        json.loads(line)
        for line in (run_dir / "transcript.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    events = [r["event"] for r in rows]
    assert events == ["start", "round", "round", "end"]
    assert rows[1]["verdict"] == "invalid"
    assert rows[1]["output"] == GARBAGE
    assert rows[1]["findings"], "invalid round must carry verifier findings"
    assert rows[1]["usage"]["total_tokens"] == 30
    assert rows[-1]["converged"] is True

    report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["schema_version"] == "vdemo-1"
    assert report["mode"] == "feedback"
    assert report["converged"] is True
    assert report["rounds_to_valid"] == 2
    assert report["tokens"]["total_tokens"] == 60
    assert len(report["rounds"]) == 2
    assert report["citations"], "a converged run against a match corpus must cite"
    md = (run_dir / "report.md").read_text(encoding="utf-8")
    assert "structurally isomorphic" in md


def test_always_invalid_hits_round_cap(tmp_path: Path, match_dir: Path):
    chat = _ScriptedChat([GARBAGE] * 3)
    result = run_scenario(
        Scenario(id="toy", text="Reschedule two trains."),
        chat,
        rounds=3,
        match_dir=match_dir,
        dossier_dir=tmp_path / "no_dossiers",
        out_dir=tmp_path / "out",
        solve_check=False,
    )
    assert not result.converged
    assert result.rounds_to_valid is None
    assert result.rounds_used == 3
    assert result.final_verdict == "invalid"
    assert result.citations == []  # never cite an invalid model
    report = json.loads((result.run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["citations"] == []
    assert len(report["rounds"]) == 3


# --------------------------------------------------------------------------
# Ablation arm
# --------------------------------------------------------------------------


def test_no_feedback_samples_independently(tmp_path: Path, match_dir: Path):
    chat = _ScriptedChat([GARBAGE, VALID, GARBAGE])
    result = run_scenario(
        Scenario(id="toy", text="Reschedule two trains."),
        chat,
        feedback=False,
        samples=3,
        match_dir=match_dir,
        dossier_dir=tmp_path / "no_dossiers",
        out_dir=tmp_path / "out",
        solve_check=False,
    )
    assert result.valid_rate == pytest.approx(1 / 3)
    assert result.converged  # the one valid sample is citable
    assert result.citations
    # Independent single shots: every request is fresh (system + user only),
    # and the ablation runs at the elevated temperature, as noted in the report.
    assert all(len(msgs) == 2 for msgs, _ in chat.calls)
    assert all(t == vd.ABLATION_TEMPERATURE for _, t in chat.calls)
    report = json.loads((result.run_dir / "report.json").read_text(encoding="utf-8"))
    assert report["mode"] == "no_feedback"
    assert "temperature 0" in report["note"]
    assert report["valid_rate"] == pytest.approx(1 / 3)


# --------------------------------------------------------------------------
# Citation by structure
# --------------------------------------------------------------------------


def test_citation_ranking_isomorphic_first(tmp_path: Path, match_dir: Path):
    from lp2graph.mining.ingest import ingest_latex

    candidate = ingest_latex(VALID, source="test").formulation
    assert candidate is not None
    citations = cite(candidate, match_dir=match_dir, dossier_dir=tmp_path / "no_dossiers")
    by_id = {c["id"]: c for c in citations}
    # Measured (test_measured_isomorphism_facts): the lab pesp_solvable is
    # isomorphic to the candidate; the lab mip_2_8_pesp (stale unused index T)
    # and assignment are not — mip_2_8_pesp is the high-similarity relative.
    assert by_id["pesp_solvable"]["relation"] == "isomorphic"
    assert by_id["mip_2_8_pesp"]["relation"] == "similar"
    assert by_id["assignment"]["relation"] == "similar"
    assert by_id["pesp_solvable"]["same_schema_hash"] is True
    assert by_id["mip_2_8_pesp"]["same_schema_hash"] is False
    # Isomorphic entries rank before similar ones; similarity is a sane cosine.
    relations = [c["relation"] for c in citations]
    assert relations == sorted(relations, key=lambda r: r != "isomorphic")
    for c in citations:
        assert -1.0001 <= float(c["similarity"]) <= 1.0001
    assert float(by_id["pesp_solvable"]["similarity"]) > float(by_id["assignment"]["similarity"])
    # graded similarity: the near-relative PESP beats the unrelated assignment
    assert float(by_id["mip_2_8_pesp"]["similarity"]) > float(by_id["assignment"]["similarity"])
    # No dossier dir -> seeds cite as themselves by name.
    assert by_id["mip_2_8_pesp"]["title"] == "2.8 PESP cyclic timetabling"
    assert by_id["mip_2_8_pesp"]["doi"] is None


def test_citation_resolves_dossier_metadata(tmp_path: Path, match_dir: Path):
    from lp2graph.mining.ingest import ingest_latex

    dossiers = tmp_path / "dossiers"
    dossiers.mkdir()
    (dossiers / "mip_2_8_pesp.json").write_text(
        json.dumps(
            {"source": {"title": "A PESP paper", "year": 2021, "doi": "10.1/xyz"}}
        ),
        encoding="utf-8",
    )
    candidate = ingest_latex(VALID, source="test").formulation
    assert candidate is not None
    by_id = {c["id"]: c for c in cite(candidate, match_dir=match_dir, dossier_dir=dossiers)}
    assert by_id["mip_2_8_pesp"]["title"] == "A PESP paper"
    assert by_id["mip_2_8_pesp"]["year"] == 2021
    assert by_id["mip_2_8_pesp"]["doi"] == "10.1/xyz"


# --------------------------------------------------------------------------
# Scenario loading
# --------------------------------------------------------------------------


def test_load_scenario_from_prose(tmp_path: Path):
    prose = tmp_path / "prose"
    prose.mkdir()
    (prose / "k1.json").write_text(
        json.dumps({"schema_version": "prose-1", "abstract": "Trains are delayed."}),
        encoding="utf-8",
    )
    s = load_scenario(paper_key="k1", prose_dir=prose)
    assert s.text == "Trains are delayed."
    assert s.paper_key == "k1"


def test_load_scenario_errors(tmp_path: Path):
    with pytest.raises(ScenarioError, match="exactly one"):
        load_scenario()
    with pytest.raises(ScenarioError, match="does not exist"):
        load_scenario(paper_key="nope", prose_dir=tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema_version": "prose-9", "abstract": "x"}), encoding="utf-8")
    with pytest.raises(ScenarioError, match="schema"):
        load_scenario(paper_key="bad", prose_dir=tmp_path)
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"schema_version": "prose-1", "abstract": " "}), encoding="utf-8")
    with pytest.raises(ScenarioError, match="empty abstract"):
        load_scenario(paper_key="empty", prose_dir=tmp_path)
    with pytest.raises(ScenarioError, match="not found"):
        load_scenario(scenario_file=tmp_path / "missing.txt")


# --------------------------------------------------------------------------
# Endpoint resolution + retry policy (offline via injected post/sleep)
# --------------------------------------------------------------------------


def test_resolve_endpoint_prefers_scads_then_deepseek():
    e = resolve_endpoint({"SCADS_API_KEY": "s3cret"})
    assert e.provider == "scads"
    assert e.base_url == vd.SCADS_BASE_URL
    assert e.model == vd.SCADS_MODEL
    e = resolve_endpoint({"DEEPSEEK_API_KEY": "d33p"})
    assert e.provider == "deepseek"
    assert e.model == vd.DEEPSEEK_MODEL
    e = resolve_endpoint(
        {"DEEPSEEK_API_KEY": "d33p", "VDEMO_BASE_URL": "http://x/v1", "VDEMO_MODEL": "m"}
    )
    assert (e.base_url, e.model) == ("http://x/v1", "m")
    with pytest.raises(vd.EndpointError, match="no API key"):
        resolve_endpoint({})
    # The key never leaks into report-able info.
    assert "d33p" not in json.dumps(e.public_dict())


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, headers=None):
        self.status_code = status_code
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def test_llm_client_retries_429_then_succeeds():
    ok = _FakeResponse(
        200,
        {
            "choices": [{"message": {"content": "hello"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        },
    )
    responses = [_FakeResponse(429, headers={"Retry-After": "0"}), _FakeResponse(503), ok]
    slept: list[float] = []
    posts: list[dict] = []

    def fake_post(url, json=None, headers=None, timeout=None):
        posts.append({"url": url, "json": json, "timeout": timeout})
        return responses.pop(0)

    client = LLMClient(
        endpoint=vd.Endpoint("http://fake/v1", "m", "k", "test"),
        post=fake_post,
        sleep=slept.append,
    )
    reply = client.chat([{"role": "user", "content": "hi"}], 0.0)
    assert reply.content == "hello"
    assert reply.usage["total_tokens"] == 3
    assert len(posts) == 3
    assert posts[0]["url"] == "http://fake/v1/chat/completions"
    assert posts[0]["timeout"] == 240.0
    assert slept == [0.0, 2.0]  # Retry-After honoured, then exponential backoff


def test_llm_client_gives_up_after_max_attempts():
    def always_503(url, json=None, headers=None, timeout=None):
        return _FakeResponse(503)

    client = LLMClient(
        endpoint=vd.Endpoint("http://fake/v1", "m", "k", "test"),
        post=always_503,
        sleep=lambda _s: None,
    )
    with pytest.raises(LLMError, match="after 4 attempts"):
        client.chat([{"role": "user", "content": "hi"}], 0.0)


def test_llm_client_does_not_retry_hard_4xx():
    calls = []

    def post_401(url, json=None, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(401, {"error": "bad key"})

    client = LLMClient(
        endpoint=vd.Endpoint("http://fake/v1", "m", "k", "test"),
        post=post_401,
        sleep=lambda _s: None,
    )
    with pytest.raises(LLMError, match="HTTP 401"):
        client.chat([{"role": "user", "content": "hi"}], 0.0)
    assert len(calls) == 1  # an answer, not a fault: no retry


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------


def test_batch_aggregates_and_citation_hit_rate(tmp_path: Path, match_dir: Path):
    prose = tmp_path / "prose"
    prose.mkdir()
    for key in ("mip_2_8_pesp", "otherpaper"):
        (prose / f"{key}.json").write_text(
            json.dumps({"schema_version": "prose-1", "abstract": f"Abstract of {key}."}),
            encoding="utf-8",
        )
    # Per key: 1 feedback call + 3 ablation calls, everything valid. The
    # generated model is the PESP few-shot, so the scenario named after
    # mip_2_8_pesp (which exists in match_dir) must be a top-3 citation hit.
    chat = _ScriptedChat([VALID] * 8)
    summary = run_batch(
        ["mip_2_8_pesp", "otherpaper", "missingkey"],
        chat,
        rounds=2,
        samples=3,
        match_dir=match_dir,
        dossier_dir=tmp_path / "no_dossiers",
        prose_dir=prose,
        out_dir=tmp_path / "out",
        solve_check=False,
    )
    assert summary["n_keys"] == 3
    assert summary["n_run"] == 2
    assert summary["n_errors"] == 1
    assert summary["feedback"]["valid_rate"] == 1.0
    assert summary["feedback"]["mean_rounds_to_valid"] == 1.0
    assert summary["no_feedback"]["mean_valid_rate"] == 1.0
    # Only mip_2_8_pesp exists in the match corpus -> denominator 1, and the
    # isomorphic match puts it in the top 3.
    assert summary["citation_hit_denominator"] == 1
    assert summary["citation_hit_rate"] == 1.0
    errors = [r for r in summary["runs"] if "error" in r]
    assert len(errors) == 1 and errors[0]["paper_key"] == "missingkey"
    on_disk = json.loads((tmp_path / "out" / "summary.json").read_text(encoding="utf-8"))
    assert on_disk["kind"] == "batch_summary"
    # Both arms wrote their run dirs.
    assert (tmp_path / "out" / "mip_2_8_pesp--feedback" / "report.json").is_file()
    assert (tmp_path / "out" / "mip_2_8_pesp--no_feedback" / "report.json").is_file()


# --------------------------------------------------------------------------
# CLI plumbing
# --------------------------------------------------------------------------


def test_read_batch_file(tmp_path: Path):
    p = tmp_path / "keys.txt"
    p.write_text("# comment\nkey1\n\nkey2\n", encoding="utf-8")
    assert vd._read_batch_file(p) == ["key1", "key2"]
    with pytest.raises(ScenarioError, match="not found"):
        vd._read_batch_file(tmp_path / "nope.txt")


def test_main_reports_missing_key_cleanly(tmp_path: Path, monkeypatch):
    for var in ("SCADS_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    scenario = tmp_path / "s.txt"
    scenario.write_text("Two trains, one track.", encoding="utf-8")
    rc = vd.main(["--scenario-file", str(scenario), "--out", str(tmp_path / "out")])
    assert rc == 2  # clean operator error, not a traceback
