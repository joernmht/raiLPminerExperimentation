"""Assisted resolution — rung (c) of the paper's staged symbol resolution.

The staged resolution ladder (paper §3.3.2, ``sec:resolution``) fills a paper's
symbol table bottom-up: (a) deterministic algebraic prefill
(:mod:`corpusbuilder.symbols`), (b) manual per-paper review (the game), and
(c) **LLM-assisted prose reading** for everything the first two rungs cannot
settle. This module is rung (c): an LLM-in-the-loop annotator that plays the
human reviewer's role and writes **exactly the artifact formats the existing
consumers already read**, so nothing downstream changes:

* ``corpus/decisions/assist_<paper_key>.json`` — a ``game-decisions-3`` export
  (formula verdicts, Shell-Sorter cell, symbol table), consumed by
  :func:`corpusbuilder.promote.load_decisions` / ``load_symbol_tables`` and by
  the PRISMA tally.
* ``corpus/declarations/<paper_key>.tex`` — the declaration sidecar (``%@``
  lines only), consumed by :func:`corpusbuilder.promote.promote_paper`.

Three principles keep the LLM on a leash:

**Deterministic evidence is given, not asked.** Binder families, bound letters
and domain rows (:func:`corpusbuilder.symbols.paper_evidence`) are computed
here and handed to the model as facts it must not contradict; a reply that
contradicts them is rejected mechanically, never trusted.

**The model fills, it does not free-write.** The sidecar prompt starts from
:func:`corpusbuilder.promote.declaration_stub` — the same fill-in-the-blank
artifact a human gets — and every reply is validated token by token
(:func:`validate_sidecar`) before a byte reaches ``corpus/declarations/``.
Validation errors are fed back for a bounded number of retries; a reply that
never validates is a recorded failure, not a silently-written sidecar.

**Everything is marked non-deterministic.** The sidecar header and the export's
``source`` field say the artifacts came from this module and which model, and
that they are *pending human confirmation* — the paper's determinism boundary
(ADR-0001) runs right through this module, and the marking is how the corpus
stays honest about it.

Caching: every request payload is hashed and its reply cached under
``corpus/assist/cache/`` (gitignored: prompts embed Elsevier TDM prose), so
reruns are free and a run interrupted halfway is resumable.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.assist --keys 10.1016_j.trb.2020.01.001
    PYTHONPATH=. python3 -m corpusbuilder.assist --all --limit 10 --promote-loop 2
"""

from __future__ import annotations

# ruff: noqa: I001 — the ``corpusbuilder.promote`` import below is also a *side
# effect* (its own railpminer._lp2graph import puts a sibling lp2graph checkout
# on ``sys.path``) and must precede the ``lp2graph`` import; sorting would put
# third-party ``lp2graph`` first and break the module where lp2graph is not
# installed. Same posture as promote.py.

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import requests

from corpusbuilder import promote
from corpusbuilder.dossier import Dossier
from corpusbuilder.game import extract_symbols, is_objective_latex
from corpusbuilder.promote import Decision, PaperDecisions, Row, declaration_stub, rows_for
from corpusbuilder.split import split_latex
from corpusbuilder.symbols import Evidence, binder_roles, paper_evidence

# ``promote`` above already ran the railpminer._lp2graph path shim as an import
# side effect, so lp2graph resolves here without repeating it.
from lp2graph.mining.ingest import ingest_latex

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"

EXPORT_SCHEMA = "game-decisions-3"
#: The game's own out-of-scope cell value (``S.cells[k]==="X"`` exports as this);
#: promote's ``CELL_TAXONOMY`` only accepts P1–P5, so a paper carrying it can
#: never promote — which is the point.
OUT_OF_SCOPE = "out_of_scope"
CELLS = ("P1", "P2", "P3", "P4", "P5")
SYMBOL_KINDS = ("index", "parameter", "variable")

#: Soft cap on one prompt, in characters. Prose paragraphs are trimmed
#: farthest-from-formula first until the prompt fits.
PROMPT_CHAR_BUDGET = 60_000
DESC_MAX = 100
STAGES = ("a", "b", "c", "r")

# --------------------------------------------------------------------------- #
# LLM client — plain requests against an OpenAI-compatible endpoint
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
TIMEOUT_S = 180
MAX_TRIES = 5
BACKOFF_BASE_S = 2.0

#: Pricing constants (USD per million tokens) used to *estimate* run cost in
#: the report. Input rates are the deepseek-v4-flash card values this task was
#: specified with; the output rate was not specified and is an assumption to
#: confirm against the official price card before quoting a number anywhere.
IN_MISS_USD_PER_MTOK = 0.44
IN_HIT_USD_PER_MTOK = 0.014
OUT_USD_PER_MTOK = 0.88  # ASSUMED (2x cache-miss input) — confirm before citing
#: DeepSeek's off-peak window discounts the standard rates by half. The report
#: states cost at both rates rather than guessing when a call ran.
OFF_PEAK_FACTOR = 0.5


class AssistError(RuntimeError):
    """A hard failure of the assist pipeline (network exhaustion, bad config)."""


class StageError(AssistError):
    """A stage's reply never validated within its retry budget."""


def model_id() -> str:
    return os.environ.get("ASSIST_MODEL") or DEFAULT_MODEL


def _endpoint() -> str:
    base = os.environ.get("ASSIST_BASE_URL") or DEFAULT_BASE_URL
    return base.rstrip("/") + "/chat/completions"


def _api_key() -> str:
    key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("ASSIST_API_KEY")
    if not key:
        raise AssistError(
            "no API key: set DEEPSEEK_API_KEY (or ASSIST_API_KEY) in the environment "
            "or ~/.config/raiLP/secrets.env"
        )
    return key


#: Injectable sleep so tests (and impatient callers) can neutralize backoff.
_sleep = time.sleep


def _chat(payload: dict) -> tuple[str, dict]:
    """One chat completion; returns ``(content, usage)``.

    Retries 429/5xx and transport errors with exponential backoff (base 2 s,
    at most :data:`MAX_TRIES` attempts). Any other HTTP status is an answer,
    not a fault, and raises immediately. The API key is sent in the header and
    never appears in logs, errors, or cache files.
    """
    headers = {"Authorization": f"Bearer {_api_key()}"}
    delay = BACKOFF_BASE_S
    last = "no attempt made"
    for attempt in range(MAX_TRIES):
        try:
            resp = requests.post(_endpoint(), json=payload, headers=headers, timeout=TIMEOUT_S)
        except requests.RequestException as exc:
            last = f"network error: {type(exc).__name__}"
        else:
            if resp.status_code == 200:
                body = resp.json()
                content = body["choices"][0]["message"]["content"]
                return str(content), dict(body.get("usage") or {})
            if resp.status_code == 429 or resp.status_code >= 500:
                last = f"HTTP {resp.status_code}"
            else:
                raise AssistError(f"chat completion refused: HTTP {resp.status_code}")
        if attempt < MAX_TRIES - 1:
            _sleep(delay)
            delay *= 2
    raise AssistError(f"chat completion failed after {MAX_TRIES} tries ({last})")


#: Per-stage reasoning switch. Triage (a) and symbol typing (b) are pattern
#: tasks where measured verdicts match with thinking off, and thinking-mode
#: reasoning dominates wall-clock (~3-10k reasoning tokens per call); the
#: declaration fill (c) is the grammar-heavy stage and keeps the default
#: thinking mode. ``ASSIST_THINKING=on|off`` overrides for every stage.
#: The switch is part of the request payload, so it is part of the cache key.
_NO_THINKING = {"thinking": {"type": "disabled"}}


def _stage_extras(stage: str) -> dict:
    override = os.environ.get("ASSIST_THINKING", "").strip().lower()
    if override == "on":
        return {}
    if override == "off":
        return dict(_NO_THINKING)
    return dict(_NO_THINKING) if stage in ("a", "b") else {}


def _payload(system: str, user: str, *, stage: str) -> dict:
    return {
        "model": model_id(),
        "temperature": 0,
        "max_tokens": 8192,
        "response_format": {"type": "json_object"},
        **_stage_extras(stage),
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }


_FENCE = re.compile(r"^\s*```[a-zA-Z]*\s*|\s*```\s*$")


def _parse_json_reply(content: str) -> dict:
    """Parse a model reply as a JSON object, tolerating a stray code fence."""
    parsed = json.loads(_FENCE.sub("", content.strip()))
    if not isinstance(parsed, dict):
        raise ValueError("reply is not a JSON object")
    return parsed


# --------------------------------------------------------------------------- #
# Usage + cost accounting
# --------------------------------------------------------------------------- #


@dataclass
class Usage:
    """Token counts of the *real* API calls (cache hits contribute nothing)."""

    calls: int = 0
    cache_hits: int = 0
    prompt_miss_tokens: int = 0
    prompt_hit_tokens: int = 0
    completion_tokens: int = 0

    def add(self, usage: dict, *, cached: bool) -> None:
        if cached:
            self.cache_hits += 1
            return
        self.calls += 1
        prompt = int(usage.get("prompt_tokens") or 0)
        hit = int(usage.get("prompt_cache_hit_tokens") or 0)
        miss = int(usage.get("prompt_cache_miss_tokens") or max(0, prompt - hit))
        self.prompt_hit_tokens += hit
        self.prompt_miss_tokens += miss
        self.completion_tokens += int(usage.get("completion_tokens") or 0)

    def merge(self, other: Usage) -> None:
        self.calls += other.calls
        self.cache_hits += other.cache_hits
        self.prompt_miss_tokens += other.prompt_miss_tokens
        self.prompt_hit_tokens += other.prompt_hit_tokens
        self.completion_tokens += other.completion_tokens

    def cost_usd(self) -> dict[str, float]:
        """Estimated cost at the standard and the off-peak rate, both.

        Which rate actually applied depends on when each call ran; recording
        tokens and stating both bounds is honest, guessing a wall-clock is not.
        """
        standard = (
            self.prompt_miss_tokens * IN_MISS_USD_PER_MTOK
            + self.prompt_hit_tokens * IN_HIT_USD_PER_MTOK
            + self.completion_tokens * OUT_USD_PER_MTOK
        ) / 1_000_000
        return {
            "standard": round(standard, 6),
            "off_peak": round(standard * OFF_PEAK_FACTOR, 6),
        }

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "cache_hits": self.cache_hits,
            "prompt_miss_tokens": self.prompt_miss_tokens,
            "prompt_hit_tokens": self.prompt_hit_tokens,
            "completion_tokens": self.completion_tokens,
            "cost_usd": self.cost_usd(),
        }


# --------------------------------------------------------------------------- #
# Workspace + response cache
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Workspace:
    """Where the pipeline reads and writes; injectable so tests stay offline."""

    dossiers: Path = CORPUS / "dossiers"
    prose: Path = CORPUS / "prose"
    decisions: Path = CORPUS / "decisions"
    declarations: Path = CORPUS / "declarations"
    assist: Path = CORPUS / "assist"
    #: Redirect targets for the promote feedback loop (tests promote into a
    #: staging tree; the CLI promotes into the live corpus).
    promote_out_dirs: dict[str, Path] | None = None

    @property
    def cache(self) -> Path:
        return self.assist / "cache"


_CACHE_SCHEMA = "assist-cache-1"
_CACHE_KEEP = 8  # retry variants of one stage worth remembering


def _payload_digest(payload: dict) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _cached_chat(
    ws: Workspace, paper_key: str, stage: str, payload: dict, usage: Usage, *, force: bool = False
) -> str:
    """A chat completion through the response cache.

    One file per ``(paper, stage)`` holding the last few ``(payload sha256,
    reply)`` records: a hit on the exact payload skips the API call entirely,
    which makes reruns free and an interrupted run resumable. Keeping several
    records (not one) means a run that needed a validation retry still replays
    from cache — both the first attempt and the retry are remembered.
    """
    path = ws.cache / f"{paper_key}.{stage}.json"
    digest = _payload_digest(payload)
    records: list[dict] = []
    if path.exists():
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
            records = [r for r in stored.get("records", []) if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError):
            records = []
    if not force:
        for record in records:
            if record.get("payload_sha256") == digest:
                usage.add(dict(record.get("usage") or {}), cached=True)
                return str(record.get("content") or "")

    content, call_usage = _chat(payload)
    records = [r for r in records if r.get("payload_sha256") != digest]
    records.append(
        {
            "payload_sha256": digest,
            "model": payload.get("model"),
            "usage": call_usage,
            "content": content,
        }
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": _CACHE_SCHEMA,
                "paper_key": paper_key,
                "stage": stage,
                "records": records[-_CACHE_KEEP:],
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    usage.add(call_usage, cached=False)
    return content


# --------------------------------------------------------------------------- #
# Prose context — linked paragraphs, budgeted
# --------------------------------------------------------------------------- #


def load_prose(ws: Workspace, key: str) -> dict | None:
    """The paper's ``prose-1`` digest, or ``None`` — formulas-only degrade."""
    path = ws.prose / f"{key}.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def linked_paragraphs(prose: dict | None, wanted_labels: set[str]) -> list[tuple[int, dict]]:
    """Paragraphs relevant to the formulas in play, nearest first.

    Relevant = a paragraph whose ``formula_labels`` name one of the wanted
    labels, plus one neighbouring paragraph on each side, deduped. The returned
    list is ordered by ``(distance to the nearest linked paragraph, position)``
    so a caller applying a budget keeps the nearest paragraphs and trims the
    farthest first — exactly the order the list is in.
    """
    if not prose:
        return []
    paras = [p for p in prose.get("paras") or [] if isinstance(p, dict) and p.get("text")]
    linked = {
        pos
        for pos, para in enumerate(paras)
        if wanted_labels & {str(lb) for lb in para.get("formula_labels") or []}
    }
    if not linked:
        return []
    out: list[tuple[int, dict]] = []
    for pos, para in enumerate(paras):
        distance = min(abs(pos - lp) for lp in linked)
        if distance <= 1:
            out.append((distance, para))
    out.sort(key=lambda item: (item[0], int(item[1].get("i") or 0)))
    return [(d, p) for d, p in out]


def prose_context(prose: dict | None, wanted_labels: set[str], *, fixed_len: int) -> dict:
    """Abstract + deflists + as many linked paragraphs as the budget allows.

    ``fixed_len`` is the length of everything else already in the prompt; the
    paragraph budget is what remains of :data:`PROMPT_CHAR_BUDGET` after it,
    the abstract and the definition lists. Paragraphs are consumed nearest
    first, so trimming under pressure drops the farthest-from-formula ones —
    and the survivors are re-sorted into reading order for the model.
    """
    if not prose:
        return {"abstract": "", "deflists": [], "paragraphs": []}
    abstract = str(prose.get("abstract") or "")
    deflists = [d for d in prose.get("deflists") or [] if isinstance(d, dict)]
    remaining = PROMPT_CHAR_BUDGET - fixed_len - len(abstract)
    remaining -= sum(len(str(d.get("term", ""))) + len(str(d.get("def", ""))) for d in deflists)
    picked: list[dict] = []
    for _distance, para in linked_paragraphs(prose, wanted_labels):
        cost = len(str(para.get("text", ""))) + 40
        if remaining - cost < 0:
            break
        remaining -= cost
        picked.append(para)
    picked.sort(key=lambda p: int(p.get("i") or 0))
    return {
        "abstract": abstract,
        "deflists": deflists,
        "paragraphs": [
            {"i": p.get("i"), "text": p.get("text"), "formula_labels": p.get("formula_labels")}
            for p in picked
        ],
    }


def _user_json(obj: dict) -> str:
    return json.dumps(obj, indent=1, ensure_ascii=False)


# --------------------------------------------------------------------------- #
# Stage A — triage
# --------------------------------------------------------------------------- #

SYSTEM_TRIAGE = """Stage A — triage. You are the human reviewer of formulas extracted \
from one optimization paper for a corpus of LP/MILP formulations. Judge every formula.

Reply with ONE JSON object:
{"cell": "P1"|"P2"|"P3"|"P4"|"P5"|"out_of_scope",
 "decisions": [{"id": "...", "status": "accepted"|"corrected"|"rejected"|"duplicate",
                "parts": ["..."], "duplicate_of": "...", "reason": "short"}]}

Cell taxonomy (pick ONE for the whole paper): P1 railway+rescheduling, P2 transport+\
rescheduling, P3 railway+operations, P4 transport+operations, P5 production+rescheduling; \
anything else is "out_of_scope".

Rules:
- Give a decision for EVERY formula id you were shown.
- REJECT non-optimization content: metaheuristic position updates (PSO/GWO), ML losses, \
numeric worked examples, prose fragments, pure quantifier tails.
- "duplicate" = the same statement re-extracted; set "duplicate_of" to the kept id.
- "corrected" + "parts" for glued multi-formula blobs: parts = the separated formulas, \
in order. A split suggestion, when given, is usually the right correction.
- Exactly ONE objective should survive per model; extra objective restatements are \
duplicates of the kept one.
- If the paper presents several distinct models, keep the main/complete one and reject \
the rows of the others (say which model in "reason").
- Do not contradict the given facts. Keep every "reason" under 100 characters.

Canonical-grammar notes. Accepted LaTeX must survive a strict parser:
- Set/notation DEFINITIONS are not model rows: reject rows of the shape \
S = \\{(e, e') \\in X \\times Y : condition\\} (set-builder), and any row with \
English words inside the math (", and t goes directly from station ..."). \
Their content belongs to the symbol table, not the model body. Reason: \
"set definition".
- Model-reference blobs ("min F, s.t., Constraints (2)-(5)" citing equations \
by number): if F (or Z) is DEFINED algebraically in another extracted formula, \
mark this row "corrected" with parts = ["\\min <that expression>"] so the model \
keeps its objective; reject it only when the objective expression appears \
nowhere. Reason: "model reference". A multi-objective block (min Z_1 ..., \
min Z_2 ...) keeps the FIRST objective; state the others in "reason".
- A row must state one relation (=, <=, >=) or the objective; quantifier \
tails (\\forall i \\in S, tuple pairs, side conditions) are fine as written.
- Trailing prose ("; and", "where Q is ...") must be cut: mark "corrected" \
with the cleaned formula in "parts"."""


def triage_input(dossier: Dossier, prose: dict | None, feedback: list[str]) -> dict:
    source = dossier.source
    formulas = []
    for f in dossier.formulas:
        entry: dict = {"id": f.id, "latex": f.latex, "is_objective": is_objective_latex(f.latex)}
        split = split_latex(f.latex)
        if split.is_split:
            entry["split_suggestion"] = {"parts": split.parts, "confident": split.confident}
        formulas.append(entry)
    payload = {
        "paper": {
            "key": dossier.key,
            "title": source.title,
            "venue": source.venue,
            "year": source.year,
            "doi": source.doi,
        },
        "abstract": (prose or {}).get("abstract") or "",
        "formulas": formulas,
    }
    if feedback:
        payload["feedback"] = feedback
    return payload


def validate_triage(reply: dict, formula_ids: list[str]) -> list[str]:
    """Mechanical schema check of a stage-A reply; returns human-readable errors."""
    errors: list[str] = []
    cell = reply.get("cell")
    if cell not in (*CELLS, OUT_OF_SCOPE):
        errors.append(f"cell must be one of {', '.join(CELLS)} or {OUT_OF_SCOPE}; got {cell!r}")
    decisions = reply.get("decisions")
    if not isinstance(decisions, list):
        return [*errors, "decisions must be a list"]
    known = set(formula_ids)
    seen: set[str] = set()
    for entry in decisions:
        if not isinstance(entry, dict):
            errors.append("every decision must be an object")
            continue
        fid = str(entry.get("id") or "")
        if fid not in known:
            errors.append(f"unknown formula id {fid!r}")
            continue
        if fid in seen:
            errors.append(f"duplicate decision for {fid}")
        seen.add(fid)
        status = entry.get("status")
        if status not in promote.TERMINAL_STATUSES:
            errors.append(
                f"{fid}: status must be one of {promote.TERMINAL_STATUSES}; got {status!r}"
            )
            continue
        if status == "corrected":
            parts = entry.get("parts")
            ok = (
                isinstance(parts, list)
                and parts
                and all(isinstance(p, str) and p.strip() for p in parts)
            )
            if not ok:
                errors.append(f"{fid}: corrected needs a non-empty 'parts' list of LaTeX strings")
        if status == "duplicate":
            target = entry.get("duplicate_of")
            if target not in known or target == fid:
                errors.append(f"{fid}: duplicate needs 'duplicate_of' naming another formula id")
    missing = [fid for fid in formula_ids if fid not in seen]
    if missing:
        errors.append("missing decisions for: " + ", ".join(missing))
    return errors


def validate_triage_objectives(reply: dict, dossier: Dossier) -> list[str]:
    """At most ONE accepted/corrected row may state an objective.

    The canonical model admits exactly one objective, and promotion refuses
    ``multiple_objectives`` outright — enforcing it here lets the re-ask carry
    the exact offending ids instead of a generic downstream cause. Zero
    objectives is NOT an error here: an extraction may genuinely carry none
    (that is the ``no_objective`` finding).
    """
    raw = {f.id: f.latex for f in dossier.formulas}
    winners = []
    for entry in reply.get("decisions") or []:
        if not isinstance(entry, dict):
            continue
        fid = str(entry.get("id") or "")
        status = entry.get("status")
        if status == "accepted" and is_objective_latex(raw.get(fid, "")):
            winners.append(fid)
        elif status == "corrected":
            parts = entry.get("parts") or []
            if any(is_objective_latex(str(p)) for p in parts):
                winners.append(fid)
    if len(winners) > 1:
        return [
            "more than one accepted row states an objective: "
            + ", ".join(winners)
            + " — keep exactly one (the main model's), mark restatements "
            "duplicate_of it, and reject stage/secondary objectives with a reason"
        ]
    return []


# --------------------------------------------------------------------------- #
# Stage B — symbols
# --------------------------------------------------------------------------- #

SYSTEM_SYMBOLS = """Stage B — symbols. You are the human reviewer building the symbol \
table of one optimization paper's model: for every symbol in the worklist decide whether \
it is an index (set the model ranges over), a parameter (given data), or a decision \
variable.

Reply with ONE JSON object:
{"symbols": {"<sym>": {"kind": "index"|"parameter"|"variable", "desc": "<=100 chars",
                       "domain": "continuous"|"non_negative"|"integer"|"binary",
                       "shape": "I,J"}}}
"domain" only for variables; "shape" (comma list of index symbols, or "-") for \
parameters and variables when the paper indexes them.

Rules:
- Cover EVERY symbol in the worklist; add nothing else.
- given_facts are deterministic reads of the algebra. Do NOT contradict them: a symbol \
listed under index_families is an index; one listed in variable_domains is a variable \
with that domain.
- Ground each "desc" in the paper's prose/definition lists where given; keep it under \
100 characters."""


def symbol_worklist(rows: list[Row]) -> tuple[list[str], Evidence]:
    """The symbols stage B must type, plus the deterministic evidence about them.

    Mirrors :func:`corpusbuilder.promote.declaration_stub`: body symbols in
    first-seen order (``extract_symbols`` with ``limit=None`` — a truncated set
    would leave symbols past the cut untyped), then binder families, minus the
    quantifier-bound letters that need no declaration of their own.
    """
    evidence = paper_evidence([row.latex for row in rows])
    names: list[str] = []
    for row in rows:
        for name, _count in extract_symbols(row.latex, limit=None)[0]:
            if name not in names and name not in evidence.bound:
                names.append(name)
        for name in sorted(binder_roles(row.latex).families):
            if name not in names and name not in evidence.bound:
                names.append(name)
    return names, evidence


def symbols_input(
    dossier: Dossier,
    rows: list[Row],
    worklist: list[str],
    evidence: Evidence,
    prose: dict | None,
    feedback: list[str],
) -> dict:
    labels = _labels_for(dossier, rows)
    fixed = {
        "paper": {"key": dossier.key, "title": dossier.source.title},
        "formulas": [{"name": row.name, "latex": row.latex} for row in rows],
        "worklist": worklist,
        "given_facts": {
            "index_families": sorted(evidence.families),
            "bound_letters": sorted(evidence.bound),
            "variable_domains": dict(sorted(evidence.domains.items())),
        },
    }
    if feedback:
        fixed["feedback"] = feedback
    fixed["prose"] = prose_context(prose, labels, fixed_len=len(_user_json(fixed)))
    return fixed


def validate_symbols(reply: dict, worklist: list[str], evidence: Evidence) -> list[str]:
    """Schema + given-facts check of a stage-B reply."""
    errors: list[str] = []
    symbols = reply.get("symbols")
    if not isinstance(symbols, dict):
        return ["symbols must be an object mapping symbol -> {kind, desc, ...}"]
    for name in worklist:
        entry = symbols.get(name)
        if not isinstance(entry, dict):
            errors.append(f"missing symbol {name!r}")
            continue
        kind = entry.get("kind")
        if kind not in SYMBOL_KINDS:
            errors.append(f"{name}: kind must be one of {SYMBOL_KINDS}; got {kind!r}")
            continue
        settled = evidence.kinds.get(name)
        if settled and kind != settled:
            errors.append(
                f"{name}: contradicts the given facts — the algebra settles it as {settled}"
            )
    return errors


def symbol_table(reply: dict, worklist: list[str]) -> dict[str, dict]:
    """The validated stage-B table, worklist symbols only, descs clipped.

    Extra symbols the model volunteered are dropped rather than trusted: the
    worklist is the contract, and an invented symbol has no formula to bind to.
    """
    symbols = reply.get("symbols") or {}
    out: dict[str, dict] = {}
    for name in worklist:
        entry = dict(symbols.get(name) or {})
        entry["desc"] = str(entry.get("desc") or "")[:DESC_MAX]
        out[name] = entry
    return out


# --------------------------------------------------------------------------- #
# Stage C — declarations (fill the stub, validate mechanically)
# --------------------------------------------------------------------------- #

SYSTEM_DECLARATIONS = """Stage C — declarations. You are the human reviewer completing \
the declaration sidecar of one optimization paper's model. You are given the exact \
fill-in-the-blank stub the pipeline wrote, the reviewed symbol table, and prose \
snippets. FILL the stub; do not free-write:
- Replace every ? placeholder with a real value.
- Where a symbol has three alternative lines (index/param/var), keep the one line the \
symbol table says and DELETE the other two.
- Keep only %@ lines and % comment lines; never add %@ meta, name, desc, tags or prov \
lines (those are generated elsewhere).
- Allowed values: index ordered=0|1 cyclic=0|1; param kind=scalar|vector|matrix|big_m|\
tolerance; var domain=continuous|non_negative|integer|binary role=primary|auxiliary|\
slack|indicator; obj sense=min|max combination=sum|weighted_sum|lexicographic; \
shape="-" or a comma list of DECLARED index symbols; con lines may only name the row \
names present in the stub.
- Descriptions go after the ":: " on each line, grounded in the prose.

Reply with ONE JSON object: {"sidecar": "<the completed sidecar as one string>"}"""


_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PARAM_KINDS = frozenset({"scalar", "vector", "matrix", "big_m", "tolerance"})
VAR_DOMAINS = frozenset({"continuous", "non_negative", "integer", "binary"})
VAR_ROLES = frozenset({"primary", "auxiliary", "slack", "indicator"})
OBJ_SENSES = frozenset({"min", "max"})
OBJ_COMBINATIONS = frozenset({"sum", "weighted_sum", "lexicographic"})
#: Records promote generates from the dossier; a sidecar must never carry them.
_GENERATED_RECORDS = frozenset({"meta", "name", "desc", "tags", "prov"})
_SIDECAR_RECORDS = ("index", "param", "var", "obj", "con")


def _parse_decl_line(line: str) -> tuple[str, str | None, dict[str, str], list[str]]:
    """Split one ``%@`` line into (record, name, key=value map, token errors)."""
    head = line[2:].partition("::")[0].strip()
    tokens = head.split()
    errors: list[str] = []
    record = tokens[0] if tokens else ""
    rest = tokens[1:]
    name: str | None = None
    if record in ("index", "param", "var", "con"):
        if rest and "=" not in rest[0]:
            name = rest[0]
            rest = rest[1:]
        else:
            errors.append("missing name")
    kv: dict[str, str] = {}
    for token in rest:
        key, sep, value = token.partition("=")
        if not sep or not key:
            errors.append(f"token {token!r} is not key=value")
        else:
            kv[key] = value
    return record, name, kv, errors


def _check_shape(kv: dict[str, str], index_names: set[str]) -> str | None:
    shape = kv.get("shape")
    if shape in (None, "-"):
        return None
    parts = [p for p in shape.split(",") if p]
    bad = [p for p in parts if p not in index_names]
    if not parts:
        return "shape must be '-' or a comma list of declared index symbols"
    if bad:
        return f"shape names undeclared indices: {', '.join(bad)}"
    return None


def validate_sidecar(text: str, rows: list[Row]) -> list[str]:
    """Mechanically validate a filled declaration sidecar; return errors.

    This is the gate between a non-deterministic reply and a corpus artifact:
    only what passes here is written. Checks are token-level (the same
    vocabulary the canonical grammar accepts) plus binding checks — a ``con``
    line must name a real row, a shape must name declared indices. Plain ``%``
    comments pass untouched: promote's ``_declaration_lines`` keeps only ``%@``
    lines, so comments are provenance, not payload.
    """
    con_names = {row.name for row in rows if not row.is_objective}
    lines = text.splitlines()
    index_names = {
        parsed[1]
        for parsed in (_parse_decl_line(ln.strip()) for ln in lines if ln.strip().startswith("%@"))
        if parsed[0] == "index" and parsed[1]
    }

    errors: list[str] = []
    declared: dict[str, int] = {}
    obj_lines = 0
    for n, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("%"):
            errors.append(f"line {n}: not a %@ declaration or % comment: {line[:60]!r}")
            continue
        if not line.startswith("%@"):
            continue  # plain comment — ignored by promote's parser
        record, name, kv, token_errors = _parse_decl_line(line)
        where = f"line {n} (%@ {record}"
        where += f" {name})" if name else ")"
        if record in _GENERATED_RECORDS:
            errors.append(f"{where}: {record} lines are generated from the dossier — remove")
            continue
        if record not in _SIDECAR_RECORDS:
            errors.append(f"{where}: unknown record {record!r}")
            continue
        if "?" in line.partition("::")[0]:
            errors.append(f"{where}: placeholder '?' left unfilled")
        errors.extend(f"{where}: {e}" for e in token_errors)
        if record in ("index", "param", "var"):
            if name and not _IDENT.match(name):
                errors.append(f"{where}: {name!r} is not an identifier")
            if name:
                if name in declared:
                    errors.append(f"{where}: {name} already declared on line {declared[name]}")
                declared[name] = n
        if record == "index":
            for key in ("ordered", "cyclic"):
                if kv.get(key) not in ("0", "1"):
                    errors.append(f"{where}: {key} must be 0 or 1; got {kv.get(key)!r}")
        elif record == "param":
            if kv.get("kind") not in PARAM_KINDS:
                errors.append(f"{where}: kind must be one of {sorted(PARAM_KINDS)}")
            shape_error = _check_shape(kv, index_names)
            if shape_error:
                errors.append(f"{where}: {shape_error}")
        elif record == "var":
            if kv.get("domain") not in VAR_DOMAINS:
                errors.append(f"{where}: domain must be one of {sorted(VAR_DOMAINS)}")
            if kv.get("role") not in VAR_ROLES:
                errors.append(f"{where}: role must be one of {sorted(VAR_ROLES)}")
            shape_error = _check_shape(kv, index_names)
            if shape_error:
                errors.append(f"{where}: {shape_error}")
        elif record == "obj":
            obj_lines += 1
            if kv.get("sense") not in OBJ_SENSES:
                errors.append(f"{where}: sense must be min or max")
            if kv.get("combination") not in OBJ_COMBINATIONS:
                errors.append(f"{where}: combination must be one of {sorted(OBJ_COMBINATIONS)}")
        elif record == "con" and name not in con_names:
            errors.append(
                f"{where}: unknown constraint row {name!r} — rows are: "
                + (", ".join(sorted(con_names)) or "(none)")
            )
    if obj_lines != 1:
        errors.append(f"exactly one %@ obj line required; found {obj_lines}")
    return errors


def declarations_input(
    dossier: Dossier,
    rows: list[Row],
    stub: str,
    table: dict[str, dict],
    prose: dict | None,
    feedback: list[str],
) -> dict:
    labels = _labels_for(dossier, rows)
    fixed = {
        "paper": {"key": dossier.key, "title": dossier.source.title},
        "stub": stub,
        "symbol_table": table,
        "row_names": sorted(row.name for row in rows if not row.is_objective),
    }
    if feedback:
        fixed["feedback"] = feedback
    fixed["prose"] = prose_context(prose, labels, fixed_len=len(_user_json(fixed)))
    return fixed


def sidecar_header(dossier: Dossier, today: str) -> str:
    """Provenance comment atop the written sidecar (ignored by promote's parser)."""
    return "\n".join(
        [
            f"% Declaration sidecar for {dossier.key}",
            "% ASSISTED RESOLUTION — rung (c) of staged symbol resolution (sec:resolution).",
            f"% Written by corpusbuilder.assist, model {model_id()}, {today}.",
            "% Non-deterministically sourced; pending human confirmation.",
            "%",
        ]
    )


def _labels_for(dossier: Dossier, rows: list[Row]) -> set[str]:
    in_play = {row.formula_id for row in rows}
    return {f.label for f in dossier.formulas if f.label and f.id in in_play}


# --------------------------------------------------------------------------- #
# Export — the game-decisions-3 file promote already reads
# --------------------------------------------------------------------------- #


def export_payload(
    dossier: Dossier, triage: dict, table: dict[str, dict] | None, *, today: str
) -> dict:
    """The decision export, shaped exactly like the review game's own.

    ``note`` mirrors ``parts[0]`` because single-part readers take ``note``
    alone; ``symbol_tables`` uses the spelled-out kinds the game exports and
    promote's ``load_symbol_tables`` filters on. Extra keys (``source``,
    ``generated``) are ignored by every reader and kept for provenance.
    """
    by_id = {str(d["id"]): d for d in triage["decisions"]}
    decisions = []
    for f in dossier.formulas:
        entry = by_id[f.id]
        record: dict = {"id": f.id, "status": entry["status"]}
        if entry["status"] == "corrected":
            parts = [str(p) for p in entry["parts"]]
            record["note"] = parts[0]
            record["parts"] = parts
        if entry["status"] == "duplicate":
            record["duplicate_of"] = entry["duplicate_of"]
        decisions.append(record)
    payload: dict = {
        "schema_version": EXPORT_SCHEMA,
        "source": f"corpusbuilder.assist {model_id()}",
        "generated": today,
        "formula_decisions": [
            {"paper_key": dossier.key, "doi": dossier.source.doi, "decisions": decisions}
        ],
        "paper_cells": [{"paper_key": dossier.key, "cell": triage["cell"]}],
    }
    if table:
        payload["symbol_tables"] = [
            {
                "paper_key": dossier.key,
                "symbols": {name: table[name]["kind"] for name in sorted(table)},
            }
        ]
    return payload


def _write_export(ws: Workspace, dossier: Dossier, payload: dict) -> Path:
    path = ws.decisions / f"assist_{dossier.key}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Per-paper pipeline
# --------------------------------------------------------------------------- #


@dataclass
class PaperRun:
    """What happened to one paper in one run."""

    key: str
    stages: dict[str, str] = field(default_factory=dict)  # stage -> done|skipped:...|failed:...
    cell: str | None = None
    statuses: dict[str, int] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "paper_key": self.key,
            "stages": dict(sorted(self.stages.items())),
            "cell": self.cell,
            "statuses": dict(sorted(self.statuses.items())),
            "tokens": self.usage.to_dict(),
            "errors": self.errors,
        }


def _ask(
    ws: Workspace,
    key: str,
    stage: str,
    system: str,
    build_input,
    validate,
    usage: Usage,
    *,
    retries: int,
    force: bool,
    feedback: list[str],
) -> dict:
    """Ask one stage, validating the reply and re-asking with the errors.

    ``build_input(feedback)`` assembles the user payload; ``validate(reply)``
    returns error strings. The first attempt carries the caller's feedback (a
    promote-loop cause, usually empty); each re-ask appends the validation
    errors so the model sees exactly what to fix. Runs out of retries ->
    :class:`StageError` — a recorded failure, never a guessed artifact.
    """
    notes = list(feedback)
    errors: list[str] = ["no attempt made"]
    for _attempt in range(retries + 1):
        payload = _payload(system, _user_json(build_input(notes)), stage=stage)
        content = _cached_chat(ws, key, stage, payload, usage, force=force)
        try:
            reply = _parse_json_reply(content)
        except ValueError as exc:
            errors = [f"reply is not valid JSON: {exc}"]
        else:
            errors = validate(reply)
            if not errors:
                return reply
        notes = list(feedback) + [f"Your previous reply was rejected: {e}" for e in errors]
    raise StageError(f"stage {stage} for {key}: reply never validated: " + "; ".join(errors))


def annotate_paper(
    ws: Workspace,
    key: str,
    *,
    upto: str = "r",
    force: frozenset[str] | set[str] = frozenset(),
    feedback: dict[str, list[str]] | None = None,
    today: str | None = None,
) -> PaperRun:
    """Run the three-stage pipeline for one paper, writing the two artifacts.

    ``upto`` is the last stage to run ("a" | "b" | "c"); earlier stages always
    run first, through the cache, so asking for "c" alone replays a and b for
    free when they are cached. ``force`` recomputes the named stages even on a
    cache hit; ``feedback`` appends promote-loop findings to a stage's prompt.
    """
    feedback = feedback or {}
    today = today or date.today().isoformat()
    run = PaperRun(key=key)
    dossier_path = ws.dossiers / f"{key}.json"
    if not dossier_path.exists():
        run.errors.append(f"no dossier at {dossier_path}")
        run.stages["a"] = "failed: no_dossier"
        return run
    dossier = Dossier.load(dossier_path)
    prose = load_prose(ws, key)

    # ---- stage A: triage ---------------------------------------------------
    try:
        triage = _ask(
            ws,
            key,
            "a",
            SYSTEM_TRIAGE,
            lambda notes: triage_input(dossier, prose, notes),
            lambda reply: (
                validate_triage(reply, [f.id for f in dossier.formulas])
                + validate_triage_objectives(reply, dossier)
            ),
            run.usage,
            retries=1,
            force="a" in force,
            feedback=feedback.get("a", []),
        )
    except (AssistError, StageError) as exc:
        run.errors.append(str(exc))
        run.stages["a"] = "failed: invalid_reply"
        return run
    run.stages["a"] = "done"
    run.cell = triage["cell"]
    run.statuses = dict(Counter(str(d["status"]) for d in triage["decisions"]))
    _write_export(ws, dossier, export_payload(dossier, triage, None, today=today))
    if upto == "a":
        return run
    if run.cell == OUT_OF_SCOPE:
        # An out-of-scope paper must not promote; a symbol table for it would
        # be wasted tokens and a sidecar an invitation to promote it anyway.
        run.stages["b"] = run.stages["c"] = "skipped: out_of_scope"
        return run

    decisions = _paper_decisions(dossier, triage)
    rows, _counts = rows_for(dossier, decisions)
    if not rows:
        run.stages["b"] = run.stages["c"] = "skipped: no_rows"
        return run

    # ---- stage B: symbols --------------------------------------------------
    worklist, evidence = symbol_worklist(rows)
    if not worklist:
        table: dict[str, dict] = {}
        run.stages["b"] = "skipped: no_symbols"
    else:
        try:
            reply = _ask(
                ws,
                key,
                "b",
                SYSTEM_SYMBOLS,
                lambda notes: symbols_input(dossier, rows, worklist, evidence, prose, notes),
                lambda r: validate_symbols(r, worklist, evidence),
                run.usage,
                retries=1,
                force="b" in force,
                feedback=feedback.get("b", []),
            )
        except (AssistError, StageError) as exc:
            run.errors.append(str(exc))
            run.stages["b"] = "failed: invalid_reply"
            return run
        table = symbol_table(reply, worklist)
        run.stages["b"] = "done"
    _write_export(ws, dossier, export_payload(dossier, triage, table, today=today))
    if upto == "b":
        return run

    # ---- stage C: declarations --------------------------------------------
    kinds = {name: entry["kind"] for name, entry in table.items() if "kind" in entry}
    stub = declaration_stub(dossier, rows, kinds)
    try:
        reply = _ask(
            ws,
            key,
            "c",
            SYSTEM_DECLARATIONS,
            lambda notes: declarations_input(dossier, rows, stub, table, prose, notes),
            lambda r: validate_sidecar(str(r.get("sidecar") or ""), rows),
            run.usage,
            retries=2,
            force="c" in force,
            feedback=feedback.get("c", []),
        )
    except (AssistError, StageError) as exc:
        run.errors.append(str(exc))
        run.stages["c"] = "failed: invalid_sidecar"
        return run
    sidecar = sidecar_header(dossier, today) + "\n" + str(reply["sidecar"]).strip() + "\n"
    ws.declarations.mkdir(parents=True, exist_ok=True)
    (ws.declarations / f"{dossier.key}.tex").write_text(sidecar, encoding="utf-8")
    run.stages["c"] = "done"
    if upto == "c":
        return run

    # ---- stage R: row repair ----------------------------------------------
    # An inner loop, not one shot: each round probes, repairs only the rows
    # that still fail, and feeds the FRESH parser error for each back to the
    # model. The changing failure payload changes the cache key, so rounds
    # never replay a stale reply. The parser decides what is accepted.
    failures, objective_failed = probe_row_failures(dossier, triage, sidecar)
    initial = len(failures)
    if not failures:
        run.stages["r"] = "skipped: clean"
        return run
    total_fixed = 0
    for round_no in range(5):
        current = sidecar
        try:
            reply = _ask(
                ws,
                key,
                "r",
                SYSTEM_ROWFIX,
                lambda notes, _f=failures: rowfix_input(dossier, _f, table, notes),
                lambda r, _f=failures, _s=current: validate_rowfix(r, _f, _s),
                run.usage,
                retries=1,
                force=("r" in force and round_no == 0),
                feedback=feedback.get("r", []) + (
                    [f"repair round {round_no + 1}: the rows listed are the ones STILL failing"]
                    if round_no
                    else []
                ),
            )
        except (AssistError, StageError) as exc:
            run.errors.append(str(exc))
            run.stages["r"] = f"failed: invalid_reply (round {round_no + 1})"
            return run
        additions = [str(a) for a in reply.get("declarations_add") or []]
        if additions:
            sidecar = sidecar.rstrip() + "\n" + "\n".join(additions) + "\n"
        fixes = {str(k): [str(p) for p in v] for k, v in reply["rows"].items()}
        fixed, _still = apply_rowfixes(dossier, triage, sidecar, fixes)
        total_fixed += fixed
        if fixed and additions:
            (ws.declarations / f"{dossier.key}.tex").write_text(sidecar, encoding="utf-8")
        failures, objective_failed = probe_row_failures(dossier, triage, sidecar)
        if not failures:
            break
    run.statuses = dict(Counter(str(d["status"]) for d in triage["decisions"]))
    _write_export(ws, dossier, export_payload(dossier, triage, table, today=today))
    note = f"fixed {total_fixed}/{initial}"
    if objective_failed:
        note += " (objective)"
    run.stages["r"] = note if not failures else note + f", {len(failures)} still failing"
    return run


SYSTEM_ROWFIX = """Stage R — row repair. Each row below FAILED lp2graph's canonical \
LaTeX parser; the parser's message is attached. Rewrite each minimally so it \
parses, preserving the mathematical meaning exactly. You are given the paper's \
symbol table (kind per symbol).

Reply with ONE JSON object:
{"rows": {"<formula id>": ["<latex>", ...]},
 "declarations_add": ["%@ var z shape=- domain=continuous role=auxiliary drole=- lo=- hi=- :: Epigraph bound", ...]}
"declarations_add" is optional: %@ index / %@ param / %@ var lines for NEW \
symbols your rewrite introduces (an epigraph variable, a renamed set). Never \
redeclare an existing symbol.

Canonical style (from the corpus's own seed models):
  \\min\\quad \\sum_{w \\in \\mathcal{W}, j \\in \\mathcal{J}} c \\cdot x_{w,j}
  t_{i} \\ge \\mathit{earliest}_{i} \\qquad \\forall i \\in \\mathcal{I}

Rules (measured against this parser):
- THE COEFFICIENT RULE: in a product, the parameter is written BARE: \
w \\cdot c_{e}, NEVER w_{e} \\cdot c_{e} — the binder carries the index. A \
parameter standing alone (e.g. an RHS) keeps its subscripts.
- Binders are flexible: \\sum_{e \\in E}, \\mathcal/\\mathbb sets, subscripted \
or superscripted sets, set unions/differences, tuples (e,f) \\in A and double \
binders all parse. Do NOT restructure binders that are already of these forms.
- MathML writes multi-letter identifiers spaced: "t r_{e}" is ONE identifier \
tr_{e}, not a product. Collapse using the symbol table.
- Objectives: drop leading equation labels ("(F 1)", "L ="), write the \
operator as \\min or \\max. A min over decision variables with an inner max \
(minimax) must be reformulated as an epigraph: minimize a new auxiliary \
variable z (declare it via declarations_add) and add rows z >= <inner term> \
with the appropriate \\forall tail, as extra strings for the same formula id.
- Function application like D_{i}(x_{k}) is not linear algebra: if the row \
cannot be stated linearly, return the original unchanged (it will be recorded \
as outside the grammar).
- Cut trailing commas, periods and prose fragments; one relation per string; \
range tails ", 1 <= i <= n" become \\forall i \\in <a declared index set>.
- Do not change coefficients or drop terms; rename a symbol only to collapse \
spacing or replace a decoration (\\tilde, \\hat, prime) with a plain \
identifier, consistently within the affected rows, declaring it if new."""


def _effective_parts(triage: dict, formula_id: str, dossier: Dossier) -> list[str]:
    """The LaTeX a formula currently contributes: its correction, else its raw."""
    entry = {str(d["id"]): d for d in triage["decisions"]}[formula_id]
    if entry["status"] == "corrected":
        return [str(p) for p in entry["parts"]]
    raw = {f.id: f.latex for f in dossier.formulas}[formula_id]
    return [raw]


#: Failing rows offered to one repair call. More than this provokes reply
#: truncation; the inner loop reaches the rest in later rounds.
ROWFIX_BATCH = 12


@dataclass(frozen=True, slots=True)
class RowFailure:
    formula_id: str
    latex: list[str]
    error: str


def probe_row_failures(
    dossier: Dossier, triage: dict, declarations: str
) -> tuple[list[RowFailure], bool]:
    """Which promotable formulas break the canonical parser, deterministically.

    Assembles the objective alone first: a broken objective poisons every
    pairing, so in that case it is the ONLY reported failure (fix it, re-probe
    next round). Otherwise each constraint row is probed as objective+row.
    Returns ``(failures, objective_failed)``.
    """
    decisions = _paper_decisions(dossier, triage)
    rows, _ = rows_for(dossier, decisions)
    objective = [r for r in rows if r.is_objective]
    if not rows or not objective:
        return [], False

    def parse_ok(doc_rows: list[Row]) -> tuple[bool, str]:
        doc = promote.assemble(dossier, doc_rows, declarations, entry_id="probe")
        result = ingest_latex(doc, source="probe")
        if result.ok:
            return True, ""
        return False, "; ".join(f.message for f in result.failures)

    ok, err = parse_ok(objective)
    if not ok:
        fid = objective[0].formula_id
        return [RowFailure(fid, _effective_parts(triage, fid, dossier), err[:400])], True

    failures: list[RowFailure] = []
    seen: set[str] = set()
    for row in rows:
        if row.is_objective or row.formula_id in seen:
            continue
        group = [r for r in rows if r.formula_id == row.formula_id and not r.is_objective]
        ok, err = parse_ok(objective + group)
        if not ok:
            seen.add(row.formula_id)
            failures.append(
                RowFailure(
                    row.formula_id,
                    _effective_parts(triage, row.formula_id, dossier),
                    err[:400],
                )
            )
    return failures, False


def rowfix_input(
    dossier: Dossier,
    failures: list[RowFailure],
    table: dict[str, dict],
    feedback: list[str],
) -> dict:
    payload: dict = {
        "paper": {"key": dossier.key, "title": dossier.source.title},
        "symbol_table": {
            name: entry.get("kind", "?") for name, entry in sorted(table.items())
        },
        "failing_rows": [
            {"id": f.formula_id, "latex": f.latex, "parser_error": f.error}
            for f in failures[:ROWFIX_BATCH]
        ],
    }
    if len(failures) > ROWFIX_BATCH:
        payload["note"] = (
            f"{len(failures) - ROWFIX_BATCH} more failing rows follow in later rounds"
        )
    if feedback:
        payload["feedback"] = feedback
    return payload


_ADDABLE_RECORDS = ("index", "param", "var")


def validate_decl_additions(lines: list, existing: str) -> list[str]:
    """Token-validate ``declarations_add`` lines; refuse redeclarations."""
    errors: list[str] = []
    have = {
        parsed[1]
        for parsed in (
            _parse_decl_line(ln.strip())
            for ln in existing.splitlines()
            if ln.strip().startswith("%@")
        )
        if parsed[1]
    }
    for i, raw in enumerate(lines, 1):
        if not isinstance(raw, str) or not raw.strip().startswith("%@"):
            errors.append(f"declarations_add {i}: must be a %@ line")
            continue
        record, name, _kv, token_errors = _parse_decl_line(raw.strip())
        if record not in _ADDABLE_RECORDS:
            errors.append(f"declarations_add {i}: only index/param/var may be added")
            continue
        if not name or not _IDENT.match(name):
            errors.append(f"declarations_add {i}: {name!r} is not an identifier")
            continue
        if name in have:
            errors.append(f"declarations_add {i}: {name!r} is already declared")
        errors.extend(f"declarations_add {i}: {e}" for e in token_errors)
    return errors


def validate_rowfix(reply: dict, failures: list[RowFailure], declarations: str = "") -> list[str]:
    errors: list[str] = []
    additions = reply.get("declarations_add")
    if additions is not None:
        if not isinstance(additions, list):
            errors.append("declarations_add must be a list of %@ lines")
        else:
            errors.extend(validate_decl_additions(additions, declarations))
    rows = reply.get("rows")
    if not isinstance(rows, dict):
        return ["rows must be an object mapping formula ids to LaTeX lists"]
    wanted = {f.formula_id for f in failures}
    for fid, parts in rows.items():
        if fid not in wanted:
            errors.append(f"unknown failing id {fid!r}")
            continue
        ok = isinstance(parts, list) and parts and all(
            isinstance(p, str) and p.strip() for p in parts
        )
        if not ok:
            errors.append(f"{fid}: needs a non-empty list of LaTeX strings")
    # Partial coverage is acceptable: rows the reply omits simply stay in the
    # failing set for the next inner round (a 30-row demand in one reply just
    # provokes truncation).
    if not rows:
        errors.append("rows fixed nothing")
    return errors


def apply_rowfixes(
    dossier: Dossier, triage: dict, declarations: str, fixes: dict[str, list[str]]
) -> tuple[int, int]:
    """Adopt each fix ONLY if its row now parses; returns ``(fixed, kept)``.

    A fix is trialled by rebuilding the paper's rows with that one formula
    corrected and re-probing it. A fix that still fails leaves the original
    verdict standing — the parser, not the model, decides.
    """
    by_id = {str(d["id"]): d for d in triage["decisions"]}
    fixed = kept = 0
    for fid, parts in sorted(fixes.items()):
        entry = by_id[fid]
        before = dict(entry)
        entry["status"] = "corrected"
        entry["parts"] = [p.strip() for p in parts if p.strip()]
        entry.setdefault("reason", "")
        entry["reason"] = (entry["reason"] + " | rowfix").strip(" |")[:100]
        failures, _ = probe_row_failures(dossier, triage, declarations)
        if any(f.formula_id == fid for f in failures):
            entry.clear()
            entry.update(before)
            kept += 1
        else:
            fixed += 1
    return fixed, kept


def _paper_decisions(dossier: Dossier, triage: dict) -> PaperDecisions:
    """Stage-A verdicts as the promote-side records, so ``rows_for`` is reused."""
    by_id = {str(d["id"]): d for d in triage["decisions"]}
    decisions = []
    for f in dossier.formulas:
        entry = by_id[f.id]
        status = str(entry["status"])
        decisions.append(
            Decision(
                paper_key=dossier.key,
                formula_id=f.id,
                status=status,
                replacement=(
                    tuple(str(p) for p in entry["parts"]) if status == "corrected" else ()
                ),
                duplicate_of=(str(entry["duplicate_of"]) if entry.get("duplicate_of") else None),
            )
        )
    return PaperDecisions(
        paper_key=dossier.key,
        doi=dossier.source.doi,
        cell=str(triage["cell"]),
        decisions=tuple(decisions),
    )


# --------------------------------------------------------------------------- #
# Ordering — fewest symbols first, resolution-style
# --------------------------------------------------------------------------- #


def paper_symbol_count(dossier: Dossier) -> int:
    """Distinct symbols across all of a paper's formulas (bodies + families)."""
    names: set[str] = set()
    for f in dossier.formulas:
        names.update(name for name, _count in extract_symbols(f.latex, limit=None)[0])
        names.update(binder_roles(f.latex).families)
    return len(names)


def order_keys(ws: Workspace, keys: list[str] | None = None) -> list[str]:
    """Paper keys ordered by ascending symbol-table size (ties by key).

    The resolution measurement showed the unit of work is the paper's symbol
    table, so annotating the smallest tables first means a partially completed
    run still yields a coherent, finishable subset rather than a scattering of
    half-done large papers. Formula-less dossiers have nothing to annotate and
    are skipped.
    """
    ranked: list[tuple[int, str]] = []
    wanted = set(keys) if keys is not None else None
    for path in sorted(ws.dossiers.glob("*.json")):
        dossier = Dossier.load(path)
        if wanted is not None and dossier.key not in wanted:
            continue
        if not dossier.formulas:
            continue
        ranked.append((paper_symbol_count(dossier), dossier.key))
    return [key for _n, key in sorted(ranked)]


# --------------------------------------------------------------------------- #
# Promote feedback loop
# --------------------------------------------------------------------------- #

#: Promotion cause -> the stage whose reply should be re-asked with the cause.
#: Objective-count and correction problems are triage calls (stage a); grammar,
#: semantics and declaration problems live in the sidecar (stage c). Causes not
#: listed (all_rejected, not_sorted, not_reviewed, no_dossier, id_conflict) are
#: terminal findings — re-asking would only launder them into different ones.
#: ``no_objective`` is deliberately TERMINAL: a missing objective in the
#: extracted set is an ``under_specified`` finding about the source, and
#: measured behaviour showed a re-triage flips previously accepted rows to
#: rejected, relabelling the failure ``extraction_error/all_rejected`` — the
#: loop must never launder one taxonomy category into another. (Recovering an
#: unmarked objective from prose is a possible future rung-(c) extension; it is
#: not wholesale re-triage.)
#: ``outside_grammar`` and ``normalize_failed`` are BODY failures (measured:
#: word-form objectives, MathML-spaced identifiers, implicit products, glued
#: prose), so they go to stage R, which probes each row against the parser and
#: repairs exactly the failing ones; a sidecar re-fill (stage c) cannot touch
#: the body and would loop uselessly. ``semantic_invalid`` (parses, fails validation, e.g.
#: an undeclared symbol) and ``missing_declarations`` are sidecar failures.
RETRY_CAUSE_STAGE: dict[str, str] = {
    "multiple_objectives": "a",
    "corrected_without_replacement": "a",
    "outside_grammar": "r",
    "normalize_failed": "r",
    "semantic_invalid": "c",
    "missing_declarations": "c",
}


def _run_promote(ws: Workspace, keys: list[str]) -> dict:
    return promote.promote_all(
        decisions_dir=ws.decisions,
        dossiers_dir=ws.dossiers,
        declarations_dir=ws.declarations,
        out_dirs=ws.promote_out_dirs,
        write=True,
        only=set(keys),
    )


def promote_loop(
    ws: Workspace,
    keys: list[str],
    rounds: int,
    runs: dict[str, PaperRun],
    *,
    run_promote=None,
    today: str | None = None,
) -> dict:
    """Annotate -> promote -> re-ask the stage each failure implicates.

    Promotion is the ground truth this module answers to: its report (the same
    ``promote_all`` report the CLI writes) names a cause per failed paper, and
    each retryable cause maps to the stage that can fix it. The failing stage
    is re-asked with the cause and detail appended (a changed prompt misses the
    cache by construction, and ``force`` guards the degenerate case), then the
    downstream stages rebuild because their inputs changed. Terminal causes are
    kept as findings. Returns the last promotion report.
    """
    run_promote = run_promote or _run_promote
    report = run_promote(ws, keys)
    for _round in range(max(0, rounds)):
        retry: dict[str, tuple[str, str]] = {}
        for paper in report.get("papers", []):
            if paper.get("promoted"):
                continue
            stage = RETRY_CAUSE_STAGE.get(paper.get("cause") or "")
            if stage and paper["paper_key"] in set(keys):
                message = (
                    f"promotion failed with cause {paper['cause']}"
                    f" ({paper.get('detail') or promote.CAUSES[paper['cause']][1]})"
                    " — revise your reply so the assembled model avoids this."
                )
                retry[paper["paper_key"]] = (stage, message)
        if not retry:
            break
        for key, (stage, message) in sorted(retry.items()):
            run = annotate_paper(
                ws,
                key,
                upto="r",
                force={stage},
                feedback={stage: [message]},
                today=today,
            )
            if key in runs:
                runs[key].usage.merge(run.usage)
                runs[key].stages.update({s: v for s, v in run.stages.items()})
                runs[key].errors.extend(run.errors)
                runs[key].cell = run.cell or runs[key].cell
                runs[key].statuses = run.statuses or runs[key].statuses
            else:
                runs[key] = run
        report = run_promote(ws, sorted(retry))
    return report


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

REPORT_SCHEMA = "assist-report-1"


def build_report(runs: list[PaperRun], *, today: str) -> dict:
    totals = Usage()
    statuses: Counter[str] = Counter()
    for run in runs:
        totals.merge(run.usage)
        statuses.update(run.statuses)
    return {
        "schema_version": REPORT_SCHEMA,
        "generated": today,
        "model": model_id(),
        "pricing_usd_per_mtok": {
            "input_cache_miss": IN_MISS_USD_PER_MTOK,
            "input_cache_hit": IN_HIT_USD_PER_MTOK,
            "output": OUT_USD_PER_MTOK,
            "off_peak_factor": OFF_PEAK_FACTOR,
        },
        "papers": [run.to_dict() for run in sorted(runs, key=lambda r: r.key)],
        "totals": {
            "papers": len(runs),
            "statuses": dict(sorted(statuses.items())),
            "tokens": totals.to_dict(),
        },
    }


def render_report_md(report: dict) -> str:
    tokens = report["totals"]["tokens"]
    cost = tokens["cost_usd"]
    lines = [
        "# Assisted resolution report",
        "",
        f"Rung (c) annotator run, model `{report['model']}`, {report['generated']}.",
        "All outputs are non-deterministically sourced and pending human confirmation.",
        "",
        f"- papers: **{report['totals']['papers']}**",
        f"- API calls: {tokens['calls']} (+{tokens['cache_hits']} cache hits)",
        f"- tokens: {tokens['prompt_miss_tokens']} prompt-miss / "
        f"{tokens['prompt_hit_tokens']} prompt-hit / {tokens['completion_tokens']} completion",
        f"- est. cost: ${cost['standard']:.4f} standard / ${cost['off_peak']:.4f} off-peak",
        "",
        "| paper | cell | a | b | c | statuses |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for paper in report["papers"]:
        status_text = ", ".join(f"{k} {v}" for k, v in paper["statuses"].items())
        lines.append(
            f"| {paper['paper_key']} | {paper['cell'] or ''} "
            f"| {paper['stages'].get('a', '')} | {paper['stages'].get('b', '')} "
            f"| {paper['stages'].get('c', '')} | {status_text} |"
        )
    return "\n".join(lines) + "\n"


def write_report(ws: Workspace, report: dict, *, suffix: str | None = None) -> None:
    ws.assist.mkdir(parents=True, exist_ok=True)
    stem = f"report.{suffix}" if suffix else "report"
    (ws.assist / f"{stem}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (ws.assist / f"{stem}.md").write_text(render_report_md(report), encoding="utf-8")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusbuilder.assist",
        description="LLM-assisted resolution (rung c): annotate papers into the "
        "decision-export + declaration-sidecar formats corpusbuilder.promote reads.",
    )
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--keys", nargs="+", metavar="PAPER_KEY", help="annotate these papers")
    which.add_argument(
        "--all", action="store_true", help="annotate every dossier, fewest symbols first"
    )
    parser.add_argument("--limit", type=int, default=None, help="with --all: at most N papers")
    parser.add_argument(
        "--stage",
        choices=[*STAGES, "all"],
        default="all",
        help="last stage to run (earlier stages replay through the cache)",
    )
    parser.add_argument(
        "--force-stage",
        action="append",
        choices=list(STAGES),
        default=[],
        metavar="X",
        help="recompute stage X even on a cache hit (repeatable)",
    )
    parser.add_argument(
        "--promote-loop",
        type=int,
        default=0,
        metavar="N",
        help="after annotating, promote and re-ask failing stages, up to N rounds",
    )
    parser.add_argument("--report", action="store_true", help="print the markdown report")
    parser.add_argument(
        "--shard",
        metavar="I/N",
        default=None,
        help="with --all: take every N-th paper starting at I (1-based) and write "
        "report.shard-I-of-N.{json,md}; lets N processes run the corpus in parallel "
        "(all other artifacts are per-paper files, so shards never collide)",
    )
    args = parser.parse_args(argv)
    shard = None
    if args.shard:
        try:
            i, n = (int(x) for x in args.shard.split("/", 1))
        except ValueError:
            parser.error("--shard expects I/N, e.g. 2/6")
        if not (args.all and 1 <= i <= n):
            parser.error("--shard needs --all and 1 <= I <= N")
        shard = (i, n)

    ws = Workspace()
    today = date.today().isoformat()
    if args.all:
        keys = order_keys(ws)
        if shard is not None:
            i, n = shard
            keys = keys[i - 1 :: n]
        if args.limit is not None:
            keys = keys[: args.limit]
    else:
        keys = list(args.keys)
    upto = "r" if args.stage == "all" else args.stage
    force = frozenset(args.force_stage)

    runs: dict[str, PaperRun] = {}
    for key in keys:
        run = annotate_paper(ws, key, upto=upto, force=force, today=today)
        runs[key] = run
        stages_text = " ".join(f"{s}:{v}" for s, v in sorted(run.stages.items()))
        print(f"{key}  {stages_text}" + (f"  [{run.cell}]" if run.cell else ""))

    if args.promote_loop > 0:
        promotion = promote_loop(ws, keys, args.promote_loop, runs, today=today)
        print(
            f"promotion after loop: {promotion['promoted']} promoted, {promotion['failed']} failed"
        )

    report = build_report(list(runs.values()), today=today)
    suffix = f"shard-{shard[0]}-of-{shard[1]}" if shard is not None else None
    write_report(ws, report, suffix=suffix)
    tokens = report["totals"]["tokens"]
    print(
        f"papers: {report['totals']['papers']} · calls: {tokens['calls']} "
        f"(+{tokens['cache_hits']} cached) · est ${tokens['cost_usd']['standard']:.4f} std / "
        f"${tokens['cost_usd']['off_peak']:.4f} off-peak · wrote corpus/assist/report.{{json,md}}"
    )
    if args.report:
        print()
        print(render_report_md(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
