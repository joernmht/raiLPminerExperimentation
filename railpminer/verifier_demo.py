"""Track B talk demo — a small open-weights LLM plus lp2graph's deterministic
verifier converges to a proper optimization model, and *cites* its output by
structure.

The claim on the slide is a division of labour: the LLM contributes fluency
(turning a prose rescheduling scenario into candidate canonical LaTeX) and the
deterministic side contributes *judgement* — ``lp2graph.validation.validate_text``
decides whether the candidate is a well-formed model, and its findings are fed
back verbatim as the repair prompt. Nothing the LLM says is trusted until the
verifier's verdict is not ``invalid``. The ablation (``--no-feedback``) is the
control arm: the same scenario, k independent single-shot samples, validated
once each with no repair. The A/B numbers (valid rate, rounds-to-valid) are the
experiment.

"Citation by structure" closes the loop with Paper 1's corpus: a converged model
is ingested to a canonical :class:`~lp2graph.core.model.Formulation` and compared
against every promoted corpus entry (plus the 10 seeds) three ways —

* exact schema-graph isomorphism (:func:`lp2graph.mining.isomorphism.report.are_isomorphic`),
* schema-graph hash equality (:func:`lp2graph.mining.corpusmgr.dedup.schema_graph_hash`),
* graded similarity via the M2 concept-vector machinery: one concept-count
  document per model (Level-M lexical bag pooled with the Level-C/V structural
  type-signature documents), TF-IDF via
  :class:`lp2graph.mining.homologize.ConceptVectorizer`, ranked by
  :func:`lp2graph.mining.cluster.distance.cosine_similarity`.

Matches resolve to bibliography metadata through ``corpus/dossiers/<key>.json``
when the corpus entry came from a paper, so the demo can literally say "the
generated model is structurally isomorphic to X (2021)".

Design notes (measured, not assumed):

* **Convergence accepts ``valid_with_warnings``.** The seed ``mip_2_8_pesp``
  itself validates as ``valid_with_warnings`` (the smoke solve on synthesized
  placeholder data reports *unbounded* because ``k`` has no lower bound there).
  Warnings on placeholder data are advisory; only ``invalid`` (a structural
  error or failed parse) triggers a repair round. The final verdict is always
  recorded verbatim in the report.
* **The few-shot example is embedded, not rendered at import.** It is the seed
  ``pesp_solvable`` rendered once by ``lp2graph.codec.latex.to_canonical_latex``
  and frozen as a module constant, so a run's prompt is byte-stable and does not
  depend on codec availability or version drift. Its round-trip through
  ``ingest_latex`` and a fully ``valid`` verdict are locked by tests.
* **The ablation samples at temperature 0.7.** The generation arm runs at
  temperature 0 for reproducibility; at temperature 0 the k independent
  ablation samples would be identical, so the ablation uses 0.7 and the report
  says so explicitly.

Endpoints: an OpenAI-compatible chat-completions API via plain ``requests``.
Default is the ScaDS.AI open-weights endpoint (``SCADS_API_KEY``); until that
key exists, the DeepSeek API (``DEEPSEEK_API_KEY``) is the drop-in fallback.
``VDEMO_BASE_URL`` / ``VDEMO_MODEL`` override either. The API key is read from
the environment and never written into any artifact.

Everything a run produces lands under ``corpus/vdemo/<run-id>/`` (gitignored:
model outputs are unreviewed LLM text, not corpus material): ``transcript.jsonl``
(every prompt/output/verdict/finding/token count, append-as-you-go so a crashed
run keeps its evidence), ``final.tex``, ``report.json`` and ``report.md``.

Run::

    PYTHONPATH=. python3 -m railpminer.verifier_demo --scenario-file toy.txt
    PYTHONPATH=. python3 -m railpminer.verifier_demo --paper-key <K> --rounds 5
    PYTHONPATH=. python3 -m railpminer.verifier_demo --paper-key <K> --no-feedback --samples 3
    PYTHONPATH=. python3 -m railpminer.verifier_demo --batch keys.txt
"""

# ruff: noqa: I001 — the ``railpminer._lp2graph`` import below is a *side effect*
# (it puts a sibling lp2graph checkout on ``sys.path``) and must run before the
# ``lp2graph`` imports. Import sorting would place third-party ``lp2graph``
# first and break the module on any machine where lp2graph is not installed.

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401

from lp2graph import load as load_formulation
from lp2graph.core.model import Formulation
from lp2graph.mining.cluster.distance import cosine_similarity
from lp2graph.mining.corpusmgr.dedup import schema_graph_hash
from lp2graph.mining.homologize import (
    ConceptVectorizer,
    concept_bag,
    entities,
    level_m_entity,
    signature_documents,
)
from lp2graph.mining.ingest import ingest_latex
from lp2graph.mining.isomorphism.report import are_isomorphic
from lp2graph.validation import validate_text
from lp2graph.validation.report import ValidationReport

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "corpus"
DEFAULT_MATCH_DIR = CORPUS / "formulations"
DEFAULT_DOSSIER_DIR = CORPUS / "dossiers"
DEFAULT_PROSE_DIR = CORPUS / "prose"
DEFAULT_OUT_DIR = CORPUS / "vdemo"

REPORT_SCHEMA = "vdemo-1"
PROSE_SCHEMA = "prose-1"  # written by corpusbuilder.fulltext

#: Primary endpoint: ScaDS.AI's hosted open-weights models (OpenAI-compatible).
SCADS_BASE_URL = "https://llm.scads.ai/v1"
SCADS_MODEL = "meta-llama/Llama-3.3-70B-Instruct"
#: Fallback while the ScaDS key does not exist yet: DeepSeek's OpenAI-compatible API.
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-v4-flash"

GENERATION_TEMPERATURE = 0.0
ABLATION_TEMPERATURE = 0.7
ABLATION_NOTE = (
    "ablation samples were drawn at temperature "
    f"{ABLATION_TEMPERATURE} because at temperature 0 the k independent "
    "single-shot samples would be identical"
)

#: HTTP statuses worth retrying (rate limit / upstream overload). A 4xx other
#: than 429 is an answer, not a fault.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4

# --------------------------------------------------------------------------
# Few-shot example
# --------------------------------------------------------------------------

#: Seed ``pesp_solvable`` (corpus/formulations/pesp_solvable.json) rendered by
#: ``lp2graph.codec.latex.to_canonical_latex`` (canonical schema 0.1.0),
#: frozen 2026-08-23. Chosen over ``mip_2_8_pesp`` because it validates fully
#: ``valid`` (bounded objective -> clean smoke solve). Round-trip through
#: ``ingest_latex`` and the clean verdict are enforced by tests, so drift in
#: the codec surfaces as a test failure, never as a silently degraded prompt.
FEW_SHOT_ID = "pesp_solvable"
FEW_SHOT_TEX = r"""% lp2graph canonical LaTeX
% Reversible with lp2graph.codec.from_canonical_latex (schema 0.1.0).
%@ meta id=pesp_solvable family=milp schema=0.1.0
%@ name :: PESP cyclic timetabling (solvable instance form)
%@ desc :: Periodic Event Scheduling Problem in the timtab/MIPLIB family. For each ordered event pair (i,j) the cyclic difference (t_j - t_i) wrapped by period T into the window [l_{i,j}, l_{i,j}+w_{i,j}] via a non-negative integer wrap counter k_{i,j}. Minimizing the total number of wraps is a proxy for timetable tension. Same structure as the IBM/CPLEX timtab1 cyclic-timetabling MILP, with non-negative wrap counters so the objective is bounded.
%@ tags :: milp | pesp | cyclic | modulo | timetabling
%@ index E ordered=0 cyclic=0 :: Periodic events.
%@ param l shape=E,E kind=matrix domain=time_duration :: Lower offset target for the pair (i,j).
%@ param w shape=E,E kind=matrix domain=time_duration :: Tolerance window width for the pair (i,j).
%@ param T_period shape=- kind=scalar domain=time_duration :: Period length.
%@ var t shape=E domain=non_negative role=primary drole=timing lo=- hi=- :: Event time within the period.
%@ var k shape=E,E domain=integer role=auxiliary drole=auxiliary_linearization lo=0 hi=- :: Non-negative period wrap counter.
%@ obj sense=min name=total_wraps combination=sum :: Minimize the total number of period wraps (timetable tension proxy).
%@ con pesp_lower kind=modulo domain=periodic_modulo_pesp indicator=- :: Lower bound on the cyclic offset.
%@ con pesp_upper kind=modulo domain=periodic_modulo_pesp indicator=- :: Upper bound on the cyclic offset (lower target plus window).
\begin{align}
  \min\quad & \sum_{i \in \mathcal{E}, j \in \mathcal{E}} k_{i,j} \tag{total\_wraps} \\
  & t_{j} - t_{i} + \mathit{T\_period} \cdot k_{i,j} \ge l_{i,j} \qquad \forall i \in \mathcal{E},\; \forall j \in \mathcal{E},\; j \neq i \tag{pesp\_lower} \\
  & t_{j} - t_{i} + \mathit{T\_period} \cdot k_{i,j} \le l_{i,j} + w_{i,j} \qquad \forall i \in \mathcal{E},\; \forall j \in \mathcal{E},\; j \neq i \tag{pesp\_upper} \\
\end{align}
"""

SYSTEM_PROMPT = f"""You are an operations-research modeling assistant. You write mixed-integer
linear programs (MILPs) as *lp2graph canonical LaTeX* documents.

A canonical document has exactly two parts and nothing else:

1. A declaration block of comment lines starting with %@ :
   - one `%@ meta id=<lowercase_id> family=milp schema=0.1.0` line,
   - `%@ name :: ...` and `%@ desc :: ...` lines,
   - one `%@ index <S> ordered=0 cyclic=0 :: ...` line per index family,
   - one `%@ param <p> shape=<S,...|-> kind=<scalar|vector|matrix> domain=- :: ...` line per parameter,
   - one `%@ var <x> shape=<S,...> domain=<binary|integer|non_negative|continuous> role=primary drole=- lo=- hi=- :: ...` line per variable,
   - one `%@ obj sense=<min|max> name=<obj_name> combination=sum :: ...` line,
   - one `%@ con <con_name> kind=- domain=- indicator=- :: ...` line per constraint.
2. A single \\begin{{align}}...\\end{{align}} body: the first row is the
   objective (\\min or \\max of a linear expression, tagged \\tag{{<obj_name>}}),
   then one row per constraint: a linear (in)equality, its quantifiers
   (\\qquad \\forall i \\in ...), and its \\tag{{<con_name>}}.

Every symbol used in the align body must be declared in the %@ block, and
every declared index / parameter / variable should be used. The model must be
linear: products of two decision variables are not allowed.

Two strict grammar rules (the verifier rejects violations):
- The fields domain=, drole=, kind= take values from closed vocabularies.
  When you are not certain a value is allowed, write `-` (always accepted).
- Expressions are flat signed sums of terms; parenthesized grouping with
  \\left( ... \\right) is not part of the grammar. Distribute instead.

Here is a complete valid example document:

{FEW_SHOT_TEX}
Reply with ONE canonical document only: no prose before or after, no markdown fences.
"""


# --------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------


class VDemoError(RuntimeError):
    """Any demo-level failure with a message meant for the operator."""


class ScenarioError(VDemoError):
    """The scenario input could not be loaded."""


class EndpointError(VDemoError):
    """No usable LLM endpoint/key configuration was found."""


class LLMError(VDemoError):
    """The chat-completions call failed after retries."""


# --------------------------------------------------------------------------
# Endpoint + LLM client
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Endpoint:
    """A resolved OpenAI-compatible chat endpoint (the key stays out of reports)."""

    base_url: str
    model: str
    api_key: str
    provider: str  # "scads" | "deepseek"

    def public_dict(self) -> dict[str, str]:
        """What may be written into artifacts: everything except the key."""
        return {"base_url": self.base_url, "model": self.model, "provider": self.provider}


def resolve_endpoint(env: Mapping[str, str] | None = None) -> Endpoint:
    """Pick the endpoint from the environment.

    ``SCADS_API_KEY`` wins (the intended open-weights endpoint); otherwise
    ``DEEPSEEK_API_KEY`` selects the DeepSeek fallback so the demo can be
    rehearsed before the ScaDS key exists. ``VDEMO_BASE_URL`` / ``VDEMO_MODEL``
    override the chosen provider's defaults either way.
    """
    e = os.environ if env is None else env
    if e.get("SCADS_API_KEY"):
        base, model, key, provider = SCADS_BASE_URL, SCADS_MODEL, e["SCADS_API_KEY"], "scads"
    elif e.get("DEEPSEEK_API_KEY"):
        base, model, key, provider = (
            DEEPSEEK_BASE_URL,
            DEEPSEEK_MODEL,
            e["DEEPSEEK_API_KEY"],
            "deepseek",
        )
    else:
        raise EndpointError(
            "no API key found: set SCADS_API_KEY (ScaDS.AI endpoint) or "
            "DEEPSEEK_API_KEY (fallback), optionally with VDEMO_BASE_URL / VDEMO_MODEL."
        )
    return Endpoint(
        base_url=e.get("VDEMO_BASE_URL", base),
        model=e.get("VDEMO_MODEL", model),
        api_key=key,
        provider=provider,
    )


@dataclass(frozen=True, slots=True)
class LLMReply:
    """One assistant message plus the provider's token accounting."""

    content: str
    usage: dict[str, int] = field(default_factory=dict)


#: The pluggable generation interface: (messages, temperature) -> LLMReply.
#: Tests inject canned functions here; the live path is :meth:`LLMClient.chat`.
ChatFn = Callable[[list[dict[str, str]], float], LLMReply]


@dataclass(slots=True)
class LLMClient:
    """Minimal OpenAI-compatible chat client over plain ``requests``.

    ``post`` and ``sleep`` are injectable so the retry policy is testable
    offline and instantly. Retries only rate-limit/overload statuses
    (:data:`RETRYABLE_STATUS`) and transport faults, with exponential backoff,
    honouring a numeric ``Retry-After`` when the upstream sends one.
    """

    endpoint: Endpoint
    timeout: float = 240.0
    post: Callable[..., object] | None = None
    sleep: Callable[[float], None] = time.sleep

    def chat(self, messages: list[dict[str, str]], temperature: float) -> LLMReply:
        import requests  # local import: tests and offline paths never need it

        post = self.post if self.post is not None else requests.post
        url = self.endpoint.base_url.rstrip("/") + "/chat/completions"
        payload = {
            "model": self.endpoint.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        headers = {
            "Authorization": f"Bearer {self.endpoint.api_key}",
            "Content-Type": "application/json",
        }
        last_error = "no attempt made"
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = post(url, json=payload, headers=headers, timeout=self.timeout)
            except requests.RequestException as exc:  # connection reset, timeout, ...
                last_error = f"{type(exc).__name__}: {exc}"
                self.sleep(2.0**attempt)
                continue
            status = getattr(resp, "status_code", 0)
            if status in RETRYABLE_STATUS:
                last_error = f"HTTP {status}"
                retry_after = getattr(resp, "headers", {}).get("Retry-After")
                try:
                    delay = float(retry_after) if retry_after else 2.0**attempt
                except ValueError:
                    delay = 2.0**attempt
                self.sleep(delay)
                continue
            if status != 200:
                body = getattr(resp, "text", "")[:300]
                raise LLMError(f"chat completion failed: HTTP {status}: {body}")
            data = resp.json()
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LLMError(f"malformed chat response: {exc}") from exc
            usage = data.get("usage") or {}
            return LLMReply(
                content=str(content),
                usage={k: int(v) for k, v in usage.items() if isinstance(v, (int, float))},
            )
        raise LLMError(f"chat completion failed after {MAX_ATTEMPTS} attempts ({last_error})")


# --------------------------------------------------------------------------
# Scenario input
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Scenario:
    """One problem description to model.

    ``paper_key`` is set when the scenario came from a corpus paper's abstract,
    which is what makes the batch citation-hit-rate measurable (did the model's
    structural citations recover the scenario's own source paper?).
    """

    id: str
    text: str
    paper_key: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {"id": self.id, "paper_key": self.paper_key, "chars": len(self.text)}


def load_scenario(
    *,
    paper_key: str | None = None,
    scenario_file: str | Path | None = None,
    prose_dir: Path = DEFAULT_PROSE_DIR,
) -> Scenario:
    """Load the scenario from a prose abstract or a plain-text file.

    Exactly one source must be given. Failures raise :class:`ScenarioError`
    with the offending path in the message, because "file not found" without a
    path is the least debuggable error a batch run can emit.
    """
    if (paper_key is None) == (scenario_file is None):
        raise ScenarioError("give exactly one of --paper-key or --scenario-file")
    if scenario_file is not None:
        p = Path(scenario_file)
        if not p.is_file():
            raise ScenarioError(f"scenario file not found: {p}")
        text = p.read_text(encoding="utf-8").strip()
        if not text:
            raise ScenarioError(f"scenario file is empty: {p}")
        return Scenario(id=p.stem, text=text)
    assert paper_key is not None
    p = prose_dir / f"{paper_key}.json"
    if not p.is_file():
        raise ScenarioError(
            f"no prose record for paper key {paper_key!r}: {p} does not exist "
            "(run `python3 -m corpusbuilder.fulltext` to extract prose first)."
        )
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ScenarioError(f"prose record {p} is not valid JSON: {exc}") from exc
    schema = data.get("schema_version")
    if schema != PROSE_SCHEMA:
        raise ScenarioError(f"prose record {p} has schema {schema!r}, expected {PROSE_SCHEMA!r}")
    abstract = str(data.get("abstract") or "").strip()
    if not abstract:
        raise ScenarioError(f"prose record {p} has an empty abstract")
    return Scenario(id=paper_key, text=abstract, paper_key=paper_key)


# --------------------------------------------------------------------------
# Prompts
# --------------------------------------------------------------------------


def generation_prompt(scenario: Scenario) -> str:
    return (
        "Write a MILP formulation for the following railway/transport problem "
        "as one lp2graph canonical LaTeX document.\n\nProblem description:\n"
        f"{scenario.text}\n"
    )


def repair_prompt(previous_output: str, report: ValidationReport) -> str:
    """The verifier speaks for itself: findings are quoted verbatim.

    The whole point of the demo is that the repair signal is the deterministic
    report, not our paraphrase of it, so this quotes ``report.summary()``
    (verdict + every non-ok check with its detail) unedited.
    """
    return (
        "Your previous document failed deterministic verification "
        "(lp2graph.validation).\n\nVerifier findings (verbatim):\n"
        f"{report.summary()}\n\nYour previous document:\n{previous_output}\n\n"
        "Fix every finding and reply with the full corrected canonical "
        "document only: no prose, no markdown fences."
    )


# --------------------------------------------------------------------------
# Citation by structure
# --------------------------------------------------------------------------

#: How many non-isomorphic relatives a citation block lists.
MAX_SIMILAR = 5


def _concept_doc(f: Formulation) -> Counter[str]:
    """One concept-count document per model for the M2 vector space.

    Pools the lexical Level-M bag (name/description/tags through the
    homologizer's concept normalization) with the *structural* type-signature
    documents of every Level-C and Level-V entity (namespaced tokens like
    ``cmp:le`` / ``role:auxiliary``), so two models can be close either
    because they talk about the same things or because they are built the
    same way. Deterministic given the formulation.
    """
    doc: Counter[str] = Counter(concept_bag(level_m_entity(f).text))
    for level in ("C", "V"):
        for sig_doc in signature_documents(entities(f, level)):
            doc.update(sig_doc)
    return doc


def _resolve_metadata(entry_key: str, f: Formulation, dossier_dir: Path) -> dict[str, object]:
    """Bibliographic identity of one match-corpus entry.

    A promoted corpus entry is stored as ``<paper_key>.json`` next to a dossier
    of the same key; a seed has no dossier and cites as itself by name.
    """
    p = dossier_dir / f"{entry_key}.json"
    if p.is_file():
        try:
            src = json.loads(p.read_text(encoding="utf-8")).get("source", {})
            return {
                "title": src.get("title") or f.name,
                "year": src.get("year"),
                "doi": src.get("doi"),
            }
        except (json.JSONDecodeError, OSError):
            pass  # fall through: a broken dossier must not sink the citation block
    return {"title": f.name, "year": None, "doi": None}


def cite(
    candidate: Formulation,
    *,
    match_dir: Path = DEFAULT_MATCH_DIR,
    dossier_dir: Path = DEFAULT_DOSSIER_DIR,
) -> list[dict[str, object]]:
    """Rank the match corpus against ``candidate`` by structure.

    Every isomorphic entry is reported (relation ``"isomorphic"``), followed by
    the :data:`MAX_SIMILAR` most similar non-isomorphic entries (relation
    ``"similar"``). Ordering is deterministic: isomorphic first, then similarity
    descending, entry key ascending as the tie-break. ``same_schema_hash``
    records the (cheaper) hash test alongside the exact isomorphism check so
    the two can be compared corpus-wide later.
    """
    entries: list[tuple[str, Formulation]] = []
    skipped: list[str] = []
    for p in sorted(match_dir.glob("*.json")):
        try:
            entries.append((p.stem, load_formulation(p)))
        except Exception:  # not a Formulation (or schema drift): skip, never crash
            skipped.append(p.stem)
    if not entries:
        return []

    docs = [_concept_doc(candidate)] + [_concept_doc(f) for _, f in entries]
    _, vectors = ConceptVectorizer.fit_transform(docs)
    cand_vec = vectors[0]
    cand_hash = schema_graph_hash(candidate)

    rows: list[dict[str, object]] = []
    for (key, f), vec in zip(entries, vectors[1:], strict=True):
        iso = are_isomorphic(candidate, f)
        rows.append(
            {
                "id": key,
                **_resolve_metadata(key, f, dossier_dir),
                "relation": "isomorphic" if iso else "similar",
                "similarity": round(cosine_similarity(cand_vec, vec), 4),
                "same_schema_hash": schema_graph_hash(f) == cand_hash,
            }
        )
    rows.sort(key=lambda r: (r["relation"] != "isomorphic", -float(r["similarity"]), r["id"]))
    n_iso = sum(1 for r in rows if r["relation"] == "isomorphic")
    return rows[: n_iso + MAX_SIMILAR]


# --------------------------------------------------------------------------
# The runs
# --------------------------------------------------------------------------


@dataclass(slots=True)
class RunResult:
    """Everything one run produced; ``report`` is the JSON-ready record."""

    run_id: str
    run_dir: Path
    converged: bool
    final_verdict: str
    rounds_used: int
    rounds_to_valid: int | None
    valid_rate: float | None
    citations: list[dict[str, object]]
    report: dict[str, object]


def _accumulate_tokens(total: dict[str, int], usage: Mapping[str, int]) -> None:
    for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
        total[k] = total.get(k, 0) + int(usage.get(k, 0))


def _transcript_row(kind: str, **payload: object) -> dict[str, object]:
    return {"event": kind, **payload}


def _report_dicts(report: ValidationReport) -> list[dict[str, str]]:
    """The non-ok checks, JSON-ready (the ok checks would only bloat the log)."""
    return [c.to_dict() for c in report.checks if c.level != "ok"]


def run_scenario(
    scenario: Scenario,
    chat: ChatFn,
    *,
    endpoint_info: Mapping[str, str] | None = None,
    feedback: bool = True,
    rounds: int = 5,
    samples: int = 3,
    match_dir: Path = DEFAULT_MATCH_DIR,
    dossier_dir: Path = DEFAULT_DOSSIER_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    solve_check: bool = True,
) -> RunResult:
    """One scenario, one arm: verifier-feedback loop or single-shot ablation.

    The transcript is written line by line *as events happen*, so an aborted
    live run still leaves its evidence on disk. ``chat`` is any
    :data:`ChatFn` — the live client's bound method or a canned test double.
    """
    mode = "feedback" if feedback else "no_feedback"
    run_id = f"{scenario.id}--{mode}"
    run_dir = out_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    transcript_path = run_dir / "transcript.jsonl"
    tokens: dict[str, int] = {}
    round_records: list[dict[str, object]] = []
    final_text = ""
    final_verdict = "invalid"
    converged = False
    rounds_to_valid: int | None = None

    def emit(row: dict[str, object]) -> None:
        with transcript_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    transcript_path.write_text("", encoding="utf-8")  # a rerun starts a fresh log
    emit(
        _transcript_row(
            "start",
            schema_version=REPORT_SCHEMA,
            run_id=run_id,
            mode=mode,
            scenario=scenario.to_dict(),
            endpoint=dict(endpoint_info or {}),
            rounds=rounds,
            samples=samples,
            temperature=GENERATION_TEMPERATURE if feedback else ABLATION_TEMPERATURE,
        )
    )

    if feedback:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": generation_prompt(scenario)},
        ]
        for rnd in range(1, rounds + 1):
            reply = chat(messages, GENERATION_TEMPERATURE)
            report = validate_text(
                reply.content, source=f"{run_id}#r{rnd}", solve_check=solve_check
            )
            _accumulate_tokens(tokens, reply.usage)
            record = {
                "round": rnd,
                "verdict": report.verdict,
                "findings": _report_dicts(report),
                "usage": dict(reply.usage),
            }
            round_records.append(record)
            emit(_transcript_row("round", output=reply.content, **record))
            final_text, final_verdict = reply.content, report.verdict
            if report.verdict != "invalid":
                # valid_with_warnings converges too: see the module docstring
                # (a canonical seed still warns on the synthesized smoke data).
                converged, rounds_to_valid = True, rnd
                break
            messages = [
                *messages,
                {"role": "assistant", "content": reply.content},
                {"role": "user", "content": repair_prompt(reply.content, report)},
            ]
        valid_rate = None
    else:
        n_valid = 0
        for smp in range(1, samples + 1):
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": generation_prompt(scenario)},
            ]
            reply = chat(messages, ABLATION_TEMPERATURE)
            report = validate_text(
                reply.content, source=f"{run_id}#s{smp}", solve_check=solve_check
            )
            _accumulate_tokens(tokens, reply.usage)
            record = {
                "sample": smp,
                "verdict": report.verdict,
                "findings": _report_dicts(report),
                "usage": dict(reply.usage),
            }
            round_records.append(record)
            emit(_transcript_row("sample", output=reply.content, **record))
            if report.verdict != "invalid":
                n_valid += 1
                if not converged:  # first valid sample becomes the citable model
                    converged = True
                    final_text, final_verdict = reply.content, report.verdict
            elif not converged:
                final_text, final_verdict = reply.content, report.verdict
        valid_rate = n_valid / samples if samples else 0.0

    citations: list[dict[str, object]] = []
    if converged and final_text:
        ing = ingest_latex(final_text, source=run_id)
        if ing.formulation is not None:
            citations = cite(ing.formulation, match_dir=match_dir, dossier_dir=dossier_dir)
        else:
            # validate_text accepted it but strict canonical ingestion did not
            # (e.g. it validated under a non-LaTeX parser): report, don't hide.
            emit(
                _transcript_row(
                    "ingest_failure",
                    failures=[f"[{f.stage}] {f.message}" for f in ing.failures],
                )
            )

    if final_text:
        (run_dir / "final.tex").write_text(final_text, encoding="utf-8")

    report_doc: dict[str, object] = {
        "schema_version": REPORT_SCHEMA,
        "run_id": run_id,
        "mode": mode,
        "scenario": scenario.to_dict(),
        "endpoint": dict(endpoint_info or {}),
        "temperature": GENERATION_TEMPERATURE if feedback else ABLATION_TEMPERATURE,
        "rounds_cap": rounds if feedback else None,
        "samples": None if feedback else samples,
        "converged": converged,
        "rounds_to_valid": rounds_to_valid,
        "valid_rate": valid_rate,
        "final_verdict": final_verdict,
        "rounds": round_records,
        "tokens": tokens,
        "citations": citations,
    }
    if not feedback:
        report_doc["note"] = ABLATION_NOTE
    (run_dir / "report.json").write_text(
        json.dumps(report_doc, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (run_dir / "report.md").write_text(_report_markdown(report_doc), encoding="utf-8")
    emit(
        _transcript_row(
            "end",
            converged=converged,
            final_verdict=final_verdict,
            rounds_to_valid=rounds_to_valid,
            valid_rate=valid_rate,
            n_citations=len(citations),
        )
    )
    return RunResult(
        run_id=run_id,
        run_dir=run_dir,
        converged=converged,
        final_verdict=final_verdict,
        rounds_used=len(round_records),
        rounds_to_valid=rounds_to_valid,
        valid_rate=valid_rate,
        citations=citations,
        report=report_doc,
    )


def _cite_name(c: Mapping[str, object]) -> str:
    year = f" ({c['year']})" if c.get("year") else ""
    return f"{c.get('title')}{year}"


def _report_markdown(doc: Mapping[str, object]) -> str:
    """The human-readable half of the report (what goes on the slide)."""
    lines = [f"# Verifier demo run `{doc['run_id']}`", ""]
    lines.append(f"- mode: **{doc['mode']}** (temperature {doc['temperature']})")
    scenario = doc.get("scenario") or {}
    if isinstance(scenario, Mapping) and scenario.get("paper_key"):
        lines.append(f"- scenario: abstract of `{scenario['paper_key']}`")
    endpoint = doc.get("endpoint") or {}
    if isinstance(endpoint, Mapping) and endpoint.get("model"):
        lines.append(f"- model: `{endpoint.get('model')}` @ {endpoint.get('base_url')}")
    if doc["mode"] == "feedback":
        outcome = (
            f"converged in **{doc['rounds_to_valid']}** round(s)"
            if doc["converged"]
            else f"did **not** converge within {doc['rounds_cap']} rounds"
        )
        lines.append(f"- outcome: {outcome} (final verdict: {doc['final_verdict']})")
    else:
        lines.append(
            f"- outcome: valid rate **{doc['valid_rate']}** over {doc['samples']} "
            f"single-shot samples ({doc.get('note', '')})"
        )
    tokens = doc.get("tokens") or {}
    if isinstance(tokens, Mapping) and tokens:
        lines.append(f"- tokens: {tokens.get('total_tokens', 0)} total")
    lines.append("")
    citations = doc.get("citations") or []
    if isinstance(citations, Sequence) and citations:
        iso = [c for c in citations if c.get("relation") == "isomorphic"]
        sim = [c for c in citations if c.get("relation") == "similar"]
        lines.append("## Citation by structure")
        lines.append("")
        if iso:
            names = "; ".join(_cite_name(c) for c in iso)
            lines.append(f"The generated model is **structurally isomorphic** to {names}.")
        else:
            lines.append("The generated model is not isomorphic to any corpus entry.")
        if sim:
            rel = ", ".join(f"{_cite_name(c)} [{c['similarity']}]" for c in sim)
            lines.append(f"Closest relatives: {rel}.")
        lines.append("")
    elif doc.get("converged"):
        lines.append("No citations: the match corpus was empty or unreadable.")
        lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Batch
# --------------------------------------------------------------------------


def run_batch(
    paper_keys: Sequence[str],
    chat: ChatFn,
    *,
    endpoint_info: Mapping[str, str] | None = None,
    rounds: int = 5,
    samples: int = 3,
    match_dir: Path = DEFAULT_MATCH_DIR,
    dossier_dir: Path = DEFAULT_DOSSIER_DIR,
    prose_dir: Path = DEFAULT_PROSE_DIR,
    out_dir: Path = DEFAULT_OUT_DIR,
    solve_check: bool = True,
) -> dict[str, object]:
    """Both arms over a list of paper keys, plus the aggregate ``summary.json``.

    Each key gets one feedback run *and* one no-feedback ablation run — the
    comparison is the experiment, so a batch always runs both arms. Scenarios
    whose prose record is missing are recorded as errors, never silently
    skipped (the denominator on the slide must be honest).

    Citation hit rate: over the scenarios whose own paper exists in the match
    corpus, the fraction whose feedback run cites that paper in its top-3.
    """
    per_key: list[dict[str, object]] = []
    fb_valid = fb_rounds = ab_total = 0
    ab_rate_sum = 0.0
    hits = hit_denominator = 0
    for key in paper_keys:
        row: dict[str, object] = {"paper_key": key}
        try:
            scenario = load_scenario(paper_key=key, prose_dir=prose_dir)
        except ScenarioError as exc:
            row["error"] = str(exc)
            per_key.append(row)
            continue
        fb = run_scenario(
            scenario,
            chat,
            endpoint_info=endpoint_info,
            feedback=True,
            rounds=rounds,
            match_dir=match_dir,
            dossier_dir=dossier_dir,
            out_dir=out_dir,
            solve_check=solve_check,
        )
        ab = run_scenario(
            scenario,
            chat,
            endpoint_info=endpoint_info,
            feedback=False,
            samples=samples,
            match_dir=match_dir,
            dossier_dir=dossier_dir,
            out_dir=out_dir,
            solve_check=solve_check,
        )
        row["feedback"] = {
            "converged": fb.converged,
            "rounds_to_valid": fb.rounds_to_valid,
            "final_verdict": fb.final_verdict,
        }
        row["no_feedback"] = {"valid_rate": ab.valid_rate}
        fb_valid += 1 if fb.converged else 0
        if fb.rounds_to_valid is not None:
            fb_rounds += fb.rounds_to_valid
        ab_total += 1
        ab_rate_sum += ab.valid_rate or 0.0
        if (match_dir / f"{key}.json").is_file():
            hit_denominator += 1
            top3 = [c["id"] for c in fb.citations[:3]]
            hit = key in top3
            hits += 1 if hit else 0
            row["citation_hit"] = hit
        per_key.append(row)

    n_run = ab_total
    summary: dict[str, object] = {
        "schema_version": REPORT_SCHEMA,
        "kind": "batch_summary",
        "n_keys": len(paper_keys),
        "n_run": n_run,
        "n_errors": len(paper_keys) - n_run,
        "feedback": {
            "valid_rate": (fb_valid / n_run) if n_run else None,
            "mean_rounds_to_valid": (fb_rounds / fb_valid) if fb_valid else None,
            "rounds_cap": rounds,
        },
        "no_feedback": {
            "mean_valid_rate": (ab_rate_sum / n_run) if n_run else None,
            "samples": samples,
            "note": ABLATION_NOTE,
        },
        "citation_hit_rate": (hits / hit_denominator) if hit_denominator else None,
        "citation_hit_denominator": hit_denominator,
        "runs": per_key,
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return summary


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def _read_batch_file(path: Path) -> list[str]:
    if not path.is_file():
        raise ScenarioError(f"batch file not found: {path}")
    keys = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            keys.append(line)
    if not keys:
        raise ScenarioError(f"batch file has no paper keys: {path}")
    return keys


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python3 -m railpminer.verifier_demo",
        description="LLM + deterministic verifier convergence demo with citation by structure.",
    )
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--paper-key", help="corpus paper key; reads corpus/prose/<key>.json")
    src.add_argument("--scenario-file", help="plain-text problem description")
    src.add_argument("--batch", help="file with one paper key per line (# = comment)")
    ap.add_argument("--rounds", type=int, default=5, help="max repair rounds (default 5)")
    ap.add_argument(
        "--no-feedback",
        action="store_true",
        help=f"ablation arm: independent single-shot samples at T={ABLATION_TEMPERATURE}",
    )
    ap.add_argument("--samples", type=int, default=3, help="ablation sample count (default 3)")
    ap.add_argument("--match-dir", type=Path, default=DEFAULT_MATCH_DIR)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT_DIR)
    args = ap.parse_args(argv)

    try:
        endpoint = resolve_endpoint()
        client = LLMClient(endpoint=endpoint)
        info = endpoint.public_dict()
        if args.batch:
            summary = run_batch(
                _read_batch_file(Path(args.batch)),
                client.chat,
                endpoint_info=info,
                rounds=args.rounds,
                samples=args.samples,
                match_dir=args.match_dir,
                out_dir=args.out,
            )
            fb = summary["feedback"]
            nf = summary["no_feedback"]
            print(
                f"batch: {summary['n_run']}/{summary['n_keys']} run | "
                f"feedback valid rate {fb['valid_rate']} "  # type: ignore[index]
                f"(mean rounds {fb['mean_rounds_to_valid']}) | "  # type: ignore[index]
                f"ablation mean valid rate {nf['mean_valid_rate']} | "  # type: ignore[index]
                f"citation hit rate {summary['citation_hit_rate']}"
            )
            print(f"summary: {args.out / 'summary.json'}")
        else:
            scenario = load_scenario(
                paper_key=args.paper_key, scenario_file=args.scenario_file
            )
            result = run_scenario(
                scenario,
                client.chat,
                endpoint_info=info,
                feedback=not args.no_feedback,
                rounds=args.rounds,
                samples=args.samples,
                match_dir=args.match_dir,
                out_dir=args.out,
            )
            print((result.run_dir / "report.md").read_text(encoding="utf-8"))
            print(f"artifacts: {result.run_dir}")
            return 0 if result.converged else 1
    except VDemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
