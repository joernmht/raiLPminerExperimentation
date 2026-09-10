"""Factory floor — the corpus pipeline drawn as a UML-flavoured shop floor.

``python3 -m corpusbuilder.factory`` renders ONE self-contained HTML page,
``corpus/factory.html`` (plus the JSON snapshot it embeds,
``corpus/factory_data.json``), showing every station of the Paper-1 corpus
pipeline as a UML component («module»), every artifact as a UML object node
sitting on the conveyor between stations, the swimlanes as the determinism
boundary the paper argues (deterministic / human in the loop / LLM-assisted /
validation & analysis), and the promotion step as a *gate* with the failure
bins underneath it.

Two things make it a factory *monitor* rather than a poster:

* **Snapshot** — every counter is read from the corpus artifacts on disk
  (``prisma.json``, ``resolution.json``, ``promotion.json``, the decision
  exports, the declaration sidecars, ...). Missing artifacts degrade to
  "no artifact yet"; the build is deterministic (same inputs -> byte-identical
  page; the only date on the page is the newest input's modification time).
* **Heartbeat** — :func:`beat` / :func:`running` write a tiny status file,
  ``corpus/factory_status.json`` (ADR-0018: observability only, never an
  input to any result). The CLI entry points of the long-running stages call
  them; when the page is served over HTTP (``--serve``) it polls the status
  file and the snapshot every two seconds and animates the active station.

Nothing on the page is publisher text: counts, paper keys, stage names,
cause classes and rule versions only (the Elsevier TDM rule of ``corpus/``).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
OUTPUTS = ROOT / "outputs"
PAPER_DIR = Path.home() / "67531d7506c81a8c34f5794e"

HTML_NAME = "factory.html"
DATA_NAME = "factory_data.json"
STATUS_NAME = "factory_status.json"
SCHEMA_VERSION = "factory-1"
STATUS_SCHEMA_VERSION = "factory-status-1"
HISTORY_LIMIT = 20

#: Swimlanes = the determinism boundary (top to bottom).
LANES: tuple[dict[str, str], ...] = (
    {"id": "det", "name": "Deterministic", "sub": "same input, same bytes"},
    {"id": "hitl", "name": "Human in the loop", "sub": "verdicts · corrections · symbol kinds"},
    {"id": "llm", "name": "LLM-assisted", "sub": "non-deterministically sourced, parser-gated"},
    {"id": "val", "name": "Validation & analysis", "sub": "round-trip · solvers · similarity"},
)

#: ``outside_grammar`` detail strings -> failure class. Order matters: the
#: superscript test must run before the generic "trailing" test. This is the
#: exact rule set the 2026-09-10 status pass used, so the bins match its
#: numbers.
_GRAMMAR_CLASSES: tuple[tuple[str, str], ...] = (
    ("trailing '^{", "superscript after subscript"),
    ("trailing '", "juxtaposed factor / residue"),
    ("chained relation with", "chain: 3+ comparators"),
    ("mixed-direction or equality chained", "chain: mixed / equality"),
    ("cannot serve as an index family", "label subscript"),
    # rewrite-2026.09.0: the codec refuses undeclared symbols by name; the
    # folded spellings (t_arr, v_c) the sidecars do not declare yet land here.
    ("not a declared variable or parameter", "undeclared symbol (vocabulary)"),
    ("not a declared parameter", "undeclared coefficient (vocabulary)"),
    ("string_pattern_mismatch", "parenthesised / text residue"),
    ("could not convert string to float", "non-numeric coefficient"),
    ("subscripted coefficient", "subscripted-coef shape mismatch"),
    ("unbalanced braces", "unbalanced braces"),
    ("no comparator", "no comparator"),
)


# ---------------------------------------------------------------------------
# Heartbeat protocol (ADR-0018)
# ---------------------------------------------------------------------------


def _now() -> str:
    """UTC timestamp; a module-level hook so tests can freeze it."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _write_atomic(path: Path, payload: dict) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    os.replace(tmp, path)


def status_path(path: Path | None = None) -> Path | None:
    """Where heartbeats go: an explicit ``path``, else ``$RAILP_FACTORY_STATUS``
    (``off``/``0`` disables heartbeats entirely), else ``corpus/factory_status.json``."""
    if path is not None:
        return path
    env = os.environ.get("RAILP_FACTORY_STATUS")
    if env is None:
        return CORPUS / STATUS_NAME
    if env.strip().lower() in ("", "0", "off", "none"):
        return None
    return Path(env)


def read_status(path: Path | None = None) -> dict | None:
    """The current status record, or ``None`` when there is none / it is unreadable."""
    p = status_path(path)
    if p is None:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def beat(
    stage: str,
    done: int | None = None,
    total: int | None = None,
    note: str = "",
    *,
    state: str = "running",
    path: Path | None = None,
) -> None:
    """Record progress for ``stage``. Never raises: a failed heartbeat is not a
    failed pipeline (it is observability, not a result)."""
    try:
        p = status_path(path)
        if p is None:
            return
        prev = read_status(p) or {}
        now = _now()
        same = prev.get("stage") == stage and prev.get("state") == "running"
        started = prev.get("started_at") if same else now
        history = list(prev.get("history") or [])
        if state in ("done", "failed"):
            try:
                t0 = datetime.fromisoformat(str(started))
                seconds = int((datetime.fromisoformat(now) - t0).total_seconds())
            except ValueError:
                seconds = None
            history.append(
                {
                    "stage": stage,
                    "state": state,
                    "started_at": started,
                    "ended_at": now,
                    "seconds": seconds,
                    "done": done if done is not None else prev.get("done"),
                    "total": total if total is not None else prev.get("total"),
                    "note": note,
                }
            )
            history = history[-HISTORY_LIMIT:]
        record = {
            "schema_version": STATUS_SCHEMA_VERSION,
            "stage": stage,
            "state": state,
            "done": done if done is not None else (prev.get("done") if same else None),
            "total": total if total is not None else (prev.get("total") if same else None),
            "note": note,
            "started_at": started,
            "updated_at": now,
            "history": history,
        }
        p.parent.mkdir(parents=True, exist_ok=True)
        _write_atomic(p, record)
    except Exception:  # a heartbeat must never take the stage down
        return


@contextmanager
def running(
    stage: str, total: int | None = None, *, path: Path | None = None
) -> Iterator[Callable[..., None]]:
    """``with running("promote", total=n) as tick: ... tick(i, note=key)``.

    Marks the stage running on entry, ``done`` on a clean exit and ``failed``
    (with the exception text as the note) when the body raises — the
    exception itself propagates untouched.
    """
    beat(stage, 0, total, "started", path=path)
    state = {"done": 0, "total": total}

    def tick(done: int | None = None, note: str = "", total: int | None = None) -> None:
        if done is not None:
            state["done"] = done
        if total is not None:
            state["total"] = total
        beat(stage, state["done"], state["total"], note, path=path)

    try:
        yield tick
    except BaseException as exc:
        beat(
            stage,
            state["done"],
            state["total"],
            f"{type(exc).__name__}: {exc}"[:200],
            state="failed",
            path=path,
        )
        raise
    beat(stage, state["done"], state["total"], "finished", state="done", path=path)


# ---------------------------------------------------------------------------
# Snapshot: read the artifacts
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _count(pattern: str, base: Path) -> int:
    return len([p for p in base.glob(pattern) if p.is_file()])


def _fmt(n: int | float | None) -> str:
    if n is None:
        return "—"
    if isinstance(n, float):
        return f"{n:,.2f}".rstrip("0").rstrip(".")
    return f"{n:,}"


def _pct(part: int | None, whole: int | None) -> str:
    if not part or not whole:
        return "—"
    return f"{100 * part / whole:.1f}%"


def classify_detail(detail: str | None) -> str:
    """Map an ``outside_grammar`` detail string to its failure class."""
    d = detail or ""
    for needle, label in _GRAMMAR_CLASSES:
        if needle in d:
            return label
    return "other"


def failure_bins(promotion: dict | None) -> list[dict]:
    """Failure classes of a promotion report, largest first (ties by label).

    ``outside_grammar`` papers are split by :func:`classify_detail`; every
    other cause is its own bin. The category (source finding vs pipeline gap)
    travels with each bin so the page can say which is which — as text, not
    colour: the bins are one series.
    """
    if not promotion:
        return []
    cats = {k: v.get("category", "") for k, v in (promotion.get("failures_by_cause") or {}).items()}
    counts: dict[tuple[str, str], int] = {}
    for paper in promotion.get("papers") or []:
        if paper.get("promoted"):
            continue
        cause = str(paper.get("cause") or "unknown")
        if cause == "outside_grammar":
            label = classify_detail(paper.get("detail"))
            key = (f"grammar: {label}", "outside_grammar")
        else:
            key = (cause.replace("_", " "), cats.get(cause, str(paper.get("category") or "")))
        counts[key] = counts.get(key, 0) + 1
    rows = [{"label": k[0], "category": k[1], "n": n} for k, n in counts.items()]
    rows.sort(key=lambda r: (-r["n"], r["label"]))
    return rows


def _newest(paths: list[Path]) -> str | None:
    stamps = [p.stat().st_mtime for p in paths if p.exists()]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")


def _decision_stats(decisions_dir: Path) -> dict:
    files = sorted(decisions_dir.glob("*.json")) if decisions_dir.exists() else []
    sources: dict[str, int] = {}
    n_dec = 0
    n_multipart = 0
    n_symbol_papers = 0
    for f in files:
        d = _load_json(f)
        if not isinstance(d, dict):
            continue
        src = str(d.get("source") or "unknown")
        sources[src] = sources.get(src, 0) + 1
        for group in d.get("formula_decisions") or []:
            for entry in group.get("decisions") or []:
                n_dec += 1
                parts = entry.get("parts")
                if isinstance(parts, list) and len(parts) > 1:
                    n_multipart += 1
        n_symbol_papers += len(d.get("symbol_tables") or [])
    assist = sum(n for s, n in sources.items() if "assist" in s)
    return {
        "files": len(files),
        "decisions": n_dec,
        "multipart": n_multipart,
        "symbol_table_papers": n_symbol_papers,
        "assist_sourced_files": assist,
        "human_sourced_files": len(files) - assist,
    }


def _vdemo_stats(corpus: Path) -> dict:
    runs = converged = valid = 0
    batches = []
    match_set: int | None = None
    for d in sorted(corpus.glob("vdemo*")):
        if not d.is_dir():
            continue
        n = 0
        for rep in sorted(d.glob("*/report.json")):
            r = _load_json(rep)
            if not isinstance(r, dict):
                continue
            n += 1
            converged += bool(r.get("converged"))
            valid += r.get("final_verdict") in ("valid", "valid_with_warnings")
        if n:
            batches.append(d.name)
            runs += n
        rm = _load_json(d / "rematch.json")
        if isinstance(rm, dict) and rm.get("match_set_size"):
            match_set = int(rm["match_set_size"])
    return {
        "runs": runs,
        "converged": converged,
        "valid": valid,
        "batches": batches,
        "match_set_size": match_set,
    }


def _same_bytes(a: Path, b: Path) -> bool | None:
    try:
        return a.read_bytes() == b.read_bytes()
    except OSError:
        return None


def snapshot(
    corpus: Path = CORPUS,
    *,
    outputs: Path | None = OUTPUTS,
    paper_dir: Path | None = PAPER_DIR,
) -> dict:
    """Read every artifact the floor shows and return the page's data model."""
    inputs = [
        corpus / "candidates.json",
        corpus / "snowball_candidates.json",
        corpus / "prisma.json",
        corpus / "resolution.json",
        corpus / "promotion.json",
        corpus / "objective_flags.json",
        corpus / "wl" / "similarity.json",
        corpus / "fingerprint" / "clusters.json",
        corpus / "prisma_macros.tex",
        corpus / "resolution_macros.tex",
    ]
    cand = _load_json(corpus / "candidates.json") or {}
    snow = _load_json(corpus / "snowball_candidates.json") or {}
    prisma = (_load_json(corpus / "prisma.json") or {}).get("flow") or {}
    resolution = _load_json(corpus / "resolution.json") or {}
    promotion = _load_json(corpus / "promotion.json")
    flags = _load_json(corpus / "objective_flags.json") or {}
    wl = _load_json(corpus / "wl" / "similarity.json")
    fp = _load_json(corpus / "fingerprint" / "clusters.json")
    run_summary = _load_json(outputs / "run_summary.json") if outputs else None
    validation = _load_json(outputs / "validation_report.json") if outputs else None
    taxonomy = _load_json(outputs / "taxonomy.json") if outputs else None
    assist_report = _load_json(corpus / "assist" / "report.json")
    repo_report = _load_json(corpus / "repo_formulations" / "_report.json")

    dossiers_dir = corpus / "dossiers"
    n_dossiers = _count("*.json", dossiers_dir)
    decisions = _decision_stats(corpus / "decisions")
    decl_dir = corpus / "declarations"
    n_stubs = _count("*.stub.tex", decl_dir)
    n_sidecars = _count("*.tex", decl_dir) - n_stubs
    n_cache = _count("*", corpus / "assist" / "cache")
    form_dir = corpus / "formulations"
    form_files = sorted(p.name for p in form_dir.glob("*.json")) if form_dir.exists() else []
    n_mined = sum(1 for n in form_files if n.startswith("10."))
    n_seed = len(form_files) - n_mined
    repo_dir = corpus / "repo_formulations"
    n_repo = len(
        [
            p
            for p in repo_dir.glob("*.json")
            if not p.name.endswith(".meta.json") and not p.name.startswith("_")
        ]
    )
    n_manual = len(
        [p for p in (repo_dir / "manual").glob("*.json") if not p.name.endswith(".meta.json")]
    )
    n_instances = _count("*.json", corpus / "instances")
    figs = corpus / "talkpack" / "figures"
    n_png = _count("*.png", figs)
    n_pdf = _count("*.pdf", figs)
    n_svg = _count("*.svg", figs)
    vdemo = _vdemo_stats(corpus)

    ident = prisma.get("identification") or {}
    elig = prisma.get("retrieval_eligibility") or {}
    incl = prisma.get("included") or {}
    hitl = incl.get("hitl_review") or {}
    excluded = elig.get("reports_excluded") or {}
    formulas_total = incl.get("candidate_formulations")
    axes = resolution.get("structural_axes_pct") or {}

    papers_with_decisions = (promotion or {}).get("papers_with_decisions")
    promoted = (promotion or {}).get("promoted")
    failed = (promotion or {}).get("failed")
    rules = (promotion or {}).get("rewrite_rules_version")
    bins = failure_bins(promotion)
    top_pair = (wl or {}).get("top_pairs", [{}])[0] if wl else {}
    flag_counts = flags.get("counts") or {}
    assist_tokens = ((assist_report or {}).get("totals") or {}).get("tokens") or {}
    cost = (assist_tokens.get("cost_usd") or {}).get("standard")

    macros = {}
    for name in ("prisma_macros.tex", "resolution_macros.tex"):
        here = corpus / name
        there = paper_dir / name if paper_dir else None
        state = "no artifact yet"
        if here.exists():
            if there is not None and there.exists():
                same = _same_bytes(here, there)
                state = "paper copy in sync" if same else "paper copy differs"
            else:
                state = "not copied to the paper"
        macros[name] = state

    def art(name: str, count: str, present: bool = True) -> dict:
        return {"name": name, "count": count, "present": present}

    stations: list[dict] = [
        {
            "id": "discover",
            "lane": "det",
            "col": 0,
            "row": 0,
            "kind": "station",
            "stage": "discover",
            "stereo": "«corpusbuilder._discover»",
            "title": "Discover",
            "role": f"{_fmt(ident.get('database_queries') or (cand.get('queries') and len(cand['queries'])))} frozen queries · OpenAlex",
            "count": _fmt(cand.get("n_candidates") or ident.get("database_unique_records")),
            "count_label": "candidates",
            "artifact": art(
                "corpus/candidates.json",
                _fmt(cand.get("n_candidates")),
                bool(cand),
            ),
        },
        {
            "id": "snowball",
            "lane": "det",
            "col": 0,
            "row": 1,
            "kind": "station",
            "stage": "snowball",
            "stereo": "«corpusbuilder.snowball»",
            "title": "Snowball",
            "role": "backward + forward citations",
            "count": _fmt(snow.get("n_recommended") or ident.get("citation_search_recommended")),
            "count_label": f"of {_fmt(snow.get('n_candidates') or ident.get('citation_search_records_identified'))} recommended",
            "artifact": art(
                "corpus/snowball_candidates.json",
                _fmt(snow.get("n_candidates")),
                bool(snow),
            ),
        },
        {
            "id": "join",
            "lane": "det",
            "col": 1,
            "row": 0,
            "kind": "join",
            "title": "two identification arms",
            "count": _fmt(elig.get("reports_retrieved")),
            "count_label": "retrieved",
        },
        {
            "id": "harvest",
            "lane": "det",
            "col": 2,
            "row": 0,
            "kind": "station",
            "stage": "harvest",
            "stereo": "«corpusbuilder.elsevier · mathml»",
            "title": "Harvest",
            "role": "TDM full text · MathML → LaTeX",
            "count": _fmt(n_dossiers or elig.get("reports_retrieved")),
            "count_label": "dossiers",
            "artifact": art(
                "corpus/dossiers/*.json",
                f"{_fmt(incl.get('source_papers'))} with formulas",
                n_dossiers > 0,
            ),
        },
        {
            "id": "prisma",
            "lane": "det",
            "col": 3,
            "row": 0,
            "kind": "station",
            "stage": "prisma",
            "stereo": "«corpusbuilder.prisma»",
            "title": "PRISMA tally",
            "role": f"excluded {_fmt(elig.get('reports_excluded_total'))} · {len(excluded)} reasons",
            "count": _fmt(incl.get("source_papers")),
            "count_label": "papers included",
            "artifact": art(
                "corpus/prisma.json",
                f"{_fmt(formulas_total)} formulas",
                bool(prisma),
            ),
        },
        {
            "id": "split",
            "lane": "det",
            "col": 4,
            "row": 0,
            "kind": "station",
            "stage": "split",
            "stereo": "«corpusbuilder.split»",
            "title": "Split",
            "role": "glued formulas → units",
            "count": _fmt(decisions["multipart"]) if decisions["files"] else "—",
            "count_label": "multi-part corrections",
            "artifact": art(
                "corpus/review/game.html",
                "review game",
                (corpus / "review" / "game.html").exists(),
            ),
        },
        {
            "id": "review",
            "lane": "hitl",
            "col": 5,
            "row": 0,
            "kind": "station",
            "stage": "review",
            "stereo": "«corpusbuilder.game»",
            "title": "Formula Express",
            "role": "accept · correct · reject · duplicate",
            "count": _fmt(hitl.get("accepted")),
            "count_label": f"accepted · {_fmt(hitl.get('corrected'))} corrected · {_fmt(hitl.get('rejected'))} rejected",
            "artifact": art(
                "corpus/decisions/*.json",
                f"{_fmt(decisions['files'])} exports · {_fmt(decisions['human_sourced_files'])} human-sourced",
                decisions["files"] > 0,
            ),
        },
        {
            "id": "resolution",
            "lane": "det",
            "col": 6,
            "row": 0,
            "kind": "station",
            "stage": "resolution",
            "stereo": "«corpusbuilder.symbols · resolution»",
            "title": "Symbol resolution",
            "role": "breakdown coefficient β per formula",
            "count": _pct(resolution.get("resolved"), resolution.get("formulas")),
            "count_label": "formulas at β = 1",
            "artifact": art(
                "corpus/resolution.json",
                f"{_fmt(resolution.get('reviewed_pairs'))} typed pairs",
                bool(resolution),
            ),
        },
        {
            "id": "assist",
            "lane": "llm",
            "col": 7,
            "row": 0,
            "kind": "station",
            "stage": "assist",
            "stereo": "«corpusbuilder.assist»",
            "title": "Assisted resolution",
            "role": "rung (c): stages a · b · c · r",
            "count": _fmt(n_sidecars),
            "count_label": f"sidecars · {_fmt(n_stubs)} stubs open",
            "artifact": art(
                "corpus/declarations/*.tex",
                f"{_fmt(n_cache)} cached replies" if n_cache else "cache absent",
                n_sidecars > 0,
            ),
        },
        {
            "id": "vocab",
            "lane": "det",
            "col": 8,
            "row": 0,
            "kind": "planned",
            "stage": "vocab",
            "stereo": "«corpusbuilder.vocab»",
            "title": "Vocabulary check",
            "role": "used names vs declared names",
            "count": "planned",
            "count_label": "the fill-in list per paper",
            "artifact": art("corpus/vocab/*.json", "not built yet", False),
        },
        {
            "id": "algebra",
            "lane": "det",
            "col": 9,
            "row": 0,
            "kind": "station",
            "stage": "algebra",
            "stereo": "«corpusbuilder.algebra»",
            "title": "Algebra",
            "role": "declared names → products · ⋅",
            "count": _fmt(n_sidecars),
            "count_label": "papers with declared names",
            "artifact": art(
                "corpus/promoted/*.tex",
                f"{_fmt(_count('*.tex', corpus / 'promoted'))} assembled docs",
                _count("*.tex", corpus / "promoted") > 0,
            ),
        },
        {
            "id": "promote",
            "lane": "det",
            "col": 10,
            "row": 0,
            "kind": "gate",
            "stage": "promote",
            "stereo": "«corpusbuilder.promote»",
            "title": "Promotion gate",
            "role": "ingest · exact or refused by name",
            "count": f"{_fmt(promoted)} / {_fmt(papers_with_decisions)}",
            "count_label": f"papers promoted · {_fmt(failed)} in the bins ↓",
            "artifact": art(
                "corpus/promotion.json",
                f"{_fmt(len(bins))} failure classes",
                bool(promotion),
            ),
        },
        {
            "id": "codec",
            "lane": "det",
            "col": 11,
            "row": 0,
            "kind": "station",
            "stage": "codec",
            "stereo": "«lp2graph.mining.ingest»",
            "title": "M1b + codec",
            "role": "rewrite rules → canonical model",
            "count": rules or "—",
            "count_label": "rewrite-rule version",
            "artifact": art(
                "corpus/formulations/*.json",
                f"{_fmt(n_mined)} mined · {_fmt(n_seed)} seed · {_fmt(n_repo + n_manual)} repo",
                bool(form_files),
            ),
        },
        {
            "id": "verifier",
            "lane": "llm",
            "col": 12,
            "row": 0,
            "kind": "station",
            "stage": "verifier",
            "stereo": "«railpminer.verifier_demo»",
            "title": "Track B verifier",
            "role": "LLM draft → lp2graph validate → repair",
            "count": f"{_fmt(vdemo['valid'])} / {_fmt(vdemo['runs'])}",
            "count_label": "runs valid · "
            + (f"match set {vdemo['match_set_size']}" if vdemo["match_set_size"] else "no rematch"),
            "artifact": art(
                "corpus/vdemo*/",
                f"{len(vdemo['batches'])} batches" if vdemo["batches"] else "no runs",
                vdemo["runs"] > 0,
            ),
        },
        {
            "id": "validation",
            "lane": "val",
            "col": 12,
            "row": 0,
            "kind": "station",
            "stage": "validation",
            "stereo": "«railpminer.validation»",
            "title": "Fidelity validation",
            "role": "round-trip · "
            + (" / ".join((validation or {}).get("solvers_used") or []) or "solvers"),
            "count": _pct(round(((validation or {}).get("structural_pass_rate") or 0) * 100), 100)
            if validation
            else "—",
            "count_label": f"round-trip pass · {_fmt(n_instances)} planted instances",
            "artifact": art(
                "outputs/validation_report.json",
                f"{_fmt(len((validation or {}).get('external') or []))} external checks"
                if validation
                else "no artifact yet",
                bool(validation),
            ),
        },
        {
            "id": "wlcluster",
            "lane": "val",
            "col": 13,
            "row": 0,
            "kind": "station",
            "stage": "wlcluster",
            "stereo": "«corpusbuilder.wlcluster · fingerprint»",
            "title": "Similarity & families",
            "role": f"WL-{(wl or {}).get('iterations', 3)} on cores · k = {(fp or {}).get('k', '—')}",
            "count": _fmt(len((wl or {}).get("models") or [])) if wl else "—",
            "count_label": (
                f"models · top pair {top_pair.get('similarity', 0):.2f}"
                if wl
                else "no similarity matrix yet"
            ),
            "artifact": art(
                "corpus/wl/similarity.json",
                f"silhouette {(fp or {}).get('silhouette', 0):.2f}" if fp else "no fingerprint yet",
                bool(wl),
            ),
        },
        {
            "id": "talkpack",
            "lane": "val",
            "col": 14,
            "row": 0,
            "kind": "station",
            "stage": "talkpack",
            "stereo": "«corpusbuilder.talkpack»",
            "title": "Figures",
            "role": "CD-coloured PNG · SVG · PDF twins",
            "count": _fmt(n_png) if n_png else "—",
            "count_label": f"figures · {_fmt(n_pdf)} PDF twins for LaTeX",
            "artifact": art(
                "corpus/talkpack/figures/",
                f"{_fmt(n_png + n_svg + n_pdf)} files" if n_png else "not rendered here",
                n_png > 0,
            ),
        },
        {
            "id": "paper",
            "lane": "val",
            "col": 15,
            "row": 0,
            "kind": "exit",
            "stage": "paper",
            "stereo": "«Overleaf 67531d…»",
            "title": "Paper 1",
            "role": "LP Mining with LP2Graph",
            "count": "macros",
            "count_label": "prisma · resolution · prelim",
            "artifact": art(
                "*_macros.tex",
                " · ".join(f"{k.split('_')[0]}: {v}" for k, v in macros.items()),
                any(v != "no artifact yet" for v in macros.values()),
            ),
        },
    ]

    edges = [
        {"from": "discover", "to": "join"},
        {"from": "snowball", "to": "join"},
        {"from": "join", "to": "harvest"},
        {"from": "harvest", "to": "prisma"},
        {"from": "prisma", "to": "split"},
        {"from": "split", "to": "review"},
        {"from": "review", "to": "resolution"},
        {"from": "resolution", "to": "assist"},
        {"from": "assist", "to": "vocab", "dashed": True},
        {"from": "vocab", "to": "algebra", "dashed": True},
        {"from": "assist", "to": "algebra"},
        {"from": "algebra", "to": "promote"},
        {"from": "promote", "to": "codec"},
        {"from": "codec", "to": "validation"},
        {"from": "codec", "to": "verifier"},
        {"from": "verifier", "to": "wlcluster"},
        {"from": "validation", "to": "wlcluster"},
        {"from": "wlcluster", "to": "talkpack"},
        {"from": "talkpack", "to": "paper"},
    ]

    kpis = [
        {
            "v": _fmt(incl.get("source_papers")),
            "l": "papers with formulas",
            "tag": f"{_fmt(elig.get('reports_retrieved'))} retrieved",
        },
        {
            "v": _fmt(formulas_total),
            "l": "candidate formulas",
            "tag": f"{_pct(hitl.get('accepted'), formulas_total)} accepted",
        },
        {
            "v": _pct(resolution.get("resolved"), resolution.get("formulas")),
            "l": "formulas fully resolved (β = 1)",
            "tag": f"{_fmt(n_sidecars)} sidecars",
        },
        {
            "v": f"{_fmt(promoted)} / {_fmt(papers_with_decisions)}",
            "l": "papers through the gate",
            "tag": f"{_fmt(failed)} in the bins",
            "warn": bool(failed),
        },
        {
            "v": f"{_fmt(n_mined)} + {_fmt(n_seed)} + {_fmt(n_repo + n_manual)}",
            "l": "canonical models · mined + seed + repo",
            "tag": rules or "no promotion yet",
            "span": True,
        },
    ]

    notes = [
        f"Objective status: {_fmt(flag_counts.get('ok'))} ok · {_fmt(flag_counts.get('unmarked'))} unmarked · {_fmt(flag_counts.get('absent'))} absent."
        if flag_counts
        else "Objective flags: no artifact yet.",
        f"Structural axes: parses {axes.get('parses', '—')}% · single {axes.get('single', '—')}% · renders {axes.get('renders', '—')}% · statement {axes.get('statement', '—')}%."
        if axes
        else "Structural axes: no resolution artifact yet.",
        f"Assist: {_fmt(assist_tokens.get('calls'))} calls (+{_fmt(assist_tokens.get('cache_hits'))} cached), est. ${cost:.2f} at standard rate."
        if assist_tokens
        else "Assist: no report yet.",
        f"Repository conversions: {_fmt((repo_report or {}).get('converted'))} converted · {_fmt(len((repo_report or {}).get('failed') or []))} refused by the exact-or-refused converter · {_fmt(n_manual)} hand-canonicalised."
        if repo_report
        else "Repository conversions: no report yet.",
        f"Decision exports are {_fmt(decisions['assist_sourced_files'])} assist-sourced and {_fmt(decisions['human_sourced_files'])} human-sourced; the PRISMA HITL row cannot tell them apart yet.",
        f"Taxonomy run: {_fmt((run_summary or {}).get('n_formulations'))} formulations · rewrite rules {((run_summary or {}).get('versions') or {}).get('rewrite_rules', '—')} · {len((taxonomy or {}).get('axes') or [])} axes."
        if run_summary
        else "Taxonomy run (railpminer run): no outputs yet.",
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "derived_from": "corpus/* + outputs/*",
        "snapshot_of": _newest(inputs),
        "lanes": list(LANES),
        "stations": stations,
        "edges": edges,
        "kpis": kpis,
        "gate": {
            "papers": papers_with_decisions,
            "promoted": promoted,
            "failed": failed,
            "rules": rules,
            "bins": bins,
        },
        "notes": notes,
        "macros": macros,
    }


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

_LOGO = (
    '<svg viewBox="0 0 1014 321" xmlns="http://www.w3.org/2000/svg" role="img">'
    '<g transform="translate(-1485 -585)">'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="700" '
    'font-size="83" transform="matrix(1 0 0 1 1728.92 690)">TUD | Chair</text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1728.92 763)">o</text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1774.75 763)">f</text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1820.59 763)">Railway </text>'
    '<text fill="#00008C" font-family="Arial,Arial_MSFontService,sans-serif" font-weight="400" '
    'font-size="83" transform="matrix(1 0 0 1 1728.92 837)">Operations</text>'
    '<path d="M1600.51 630 1693 630 1693 722.49 1670.62 744.874 1670.62 674.888 1670.62 674.888 '
    "1670.62 651.685 1670.62 651.685 1647.41 651.685 1612.6 651.685 1589.4 674.876 1589.4 674.888 "
    "1647.41 674.888 1647.41 768.077 1577.49 838 1577.49 745.511 1485 745.511 1508.77 721.74 "
    "1601.01 721.74 1601.01 779.747 1601.02 779.747 1624.21 756.556 1624.21 721.74 1624.21 698.536 "
    '1624.21 698.536 1601.01 698.536 1601.01 698.536 1531.97 698.536Z" fill="#00008C" '
    'fill-rule="evenodd"/>'
    '<path d="M1485 628 1554 628 1530.99 651.345 1508.01 651.345 1508.01 674.655 1485 698Z" '
    'fill="#C00000" fill-rule="evenodd"/>'
    '<path d="M1485 768 1508.01 768 1508.01 813.988 1554 813.988 1554 837 1485 837Z" '
    'fill="#C00000" fill-rule="evenodd"/>'
    '<path d="M1694 768 1694 837 1624 837 1647.32 814.012 1670.65 814.012 1670.65 791.012Z" '
    'fill="#C00000" fill-rule="evenodd"/></g></svg>'
)

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="light dark">
<meta name="theme-color" content="#f3f7f8" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#00103a" media="(prefers-color-scheme: dark)">
<title>raiLPminer Factory Floor</title>
<style>
  :root{
    --page1:#f3f7f8; --page2:#e7f1f1;
    --ink:#0c1f3a; --muted:#566782;
    --card:#ffffff; --card2:#f1f8f8; --line:#d6e6e7; --track:#e6f0f0; --conv:#9fb6b8;
    --accent:#0A777F; --accent2:#2F57B2; --good:#0A777F; --warn:#C85000; --bad:#D20F41; --tier3:#7369BE;
    --kpi1:#e6f4f4; --kpi2:#eef0fb;
    --shadow:0 1px 3px rgba(12,40,50,.08),0 6px 18px rgba(12,40,50,.05);
  }
  /* Dark = TUD Dunkelblau field. Three viewer states: system (no stamp), or an explicit
     data-theme="light"|"dark" on the root; the tokens resolve as one set in each. */
  @media (prefers-color-scheme: dark){
    :root:not([data-theme="light"]){
      --page1:#00103a; --page2:#001a55;
      --ink:#eaf1ff; --muted:#a0b4d8;
      --card:#0c2766; --card2:#10307c; --line:#2a4a92; --track:#001a55; --conv:#4d6db8;
      --accent:#36b8bf; --accent2:#7aa2ff; --good:#36b8bf; --warn:#f0922e; --bad:#ff667e; --tier3:#a98bf0;
      --kpi1:#0c2a6e; --kpi2:#11306f;
      --shadow:0 1px 3px rgba(0,0,0,.4);
    }
    :root:not([data-theme="light"]) .brandlogo svg text, :root:not([data-theme="light"]) .brandlogo svg path{fill:#ffffff !important}
  }
  :root[data-theme="dark"]{
      --page1:#00103a; --page2:#001a55;
      --ink:#eaf1ff; --muted:#a0b4d8;
      --card:#0c2766; --card2:#10307c; --line:#2a4a92; --track:#001a55; --conv:#4d6db8;
      --accent:#36b8bf; --accent2:#7aa2ff; --good:#36b8bf; --warn:#f0922e; --bad:#ff667e; --tier3:#a98bf0;
      --kpi1:#0c2a6e; --kpi2:#11306f;
      --shadow:0 1px 3px rgba(0,0,0,.4);
  }
  :root[data-theme="dark"] .brandlogo svg text, :root[data-theme="dark"] .brandlogo svg path{fill:#ffffff !important}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  html,body{margin:0;padding:0}
  body{background:linear-gradient(180deg,var(--page1),var(--page2));background-attachment:fixed;color:var(--ink);
    font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
    padding:max(16px,env(safe-area-inset-top)) 16px calc(40px + env(safe-area-inset-bottom));
    max-width:1100px;margin:0 auto;-webkit-font-smoothing:antialiased}
  header{margin:6px 0 22px}
  .brandlogo{margin:2px 0 14px} .brandlogo svg{height:30px;width:auto;display:block}
  .eyebrow{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:800}
  h1{font-size:24px;line-height:1.2;margin:6px 0 4px;font-weight:800;letter-spacing:-.01em}
  .sub{color:var(--muted);font-size:14px}
  .pill{display:inline-flex;align-items:center;gap:6px;margin-top:12px;background:var(--card);border:1px solid var(--line);
    border-radius:999px;padding:6px 12px;font-size:12.5px;color:var(--muted);box-shadow:var(--shadow)}
  .pill b{color:var(--ink);font-weight:700}
  .dot{width:7px;height:7px;border-radius:50%;background:var(--muted);box-shadow:0 0 0 3px color-mix(in srgb,var(--muted) 24%,transparent)}
  .pill.live .dot{background:var(--good);box-shadow:0 0 0 3px color-mix(in srgb,var(--good) 24%,transparent);animation:blink 1.4s ease-in-out infinite}
  .pill.failed .dot{background:var(--bad)}
  @keyframes blink{50%{opacity:.35}}
  h2{font-size:13px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin:30px 0 12px;font-weight:700;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
  h2 .ctl{margin-left:auto;display:flex;gap:6px}
  button.tg{font:700 11.5px inherit;letter-spacing:0;text-transform:none;color:var(--accent);background:var(--card);border:1px solid var(--line);
    border-radius:999px;padding:4px 11px;cursor:pointer;box-shadow:var(--shadow)}
  button.tg[aria-pressed="true"]{background:color-mix(in srgb,var(--accent) 14%,transparent);border-color:color-mix(in srgb,var(--accent) 40%,transparent)}

  .kpis{display:grid;grid-template-columns:1fr 1fr;gap:12px}
  @media (min-width:720px){.kpis{grid-template-columns:repeat(4,1fr)}}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 14px 13px;box-shadow:var(--shadow)}
  .kpi .v{font-size:25px;font-weight:800;letter-spacing:-.02em;line-height:1}
  .kpi.warn .v{color:var(--warn)}
  .kpi .l{font-size:12.5px;color:var(--muted);margin-top:6px}
  .kpi .tag{font-size:11px;color:var(--accent);font-weight:700;margin-top:3px}
  .kpi.span{grid-column:1 / -1;background:linear-gradient(120deg,var(--kpi1),var(--kpi2))}
  .kpi.span .v{font-size:30px}

  /* ---- factory floor ---- */
  .floorwrap{overflow-x:auto;border:1px solid var(--line);border-radius:16px;background:var(--card);box-shadow:var(--shadow);position:relative}
  .floor{display:flex;align-items:flex-start;min-width:max-content}
  .lanes{position:sticky;left:0;z-index:3;background:var(--card);border-right:1px solid var(--line);flex:none}
  .lane{display:flex;flex-direction:column;justify-content:center;padding:0 10px;width:118px;border-bottom:1px solid var(--line)}
  .lane:last-child{border-bottom:0}
  .lane b{font-size:10.5px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);line-height:1.25}
  .lane span{font-size:9.5px;color:var(--muted);line-height:1.3;margin-top:3px}
  svg.fl{display:block;flex:none}
  .floorwrap.fit svg.fl{width:100%;height:auto}
  .floorwrap.fit .lanes{display:none}
  .band{fill:transparent} .band.alt{fill:color-mix(in srgb,var(--card2) 70%,transparent)}
  .bandline{stroke:var(--line);stroke-width:1}
  .lanetag{font:800 10px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--accent);letter-spacing:.08em;text-transform:uppercase;display:none}
  .floorwrap.fit .lanetag{display:block}
  .st .box{fill:var(--card);stroke:var(--line);stroke-width:1.3}
  .st.kind-gate .box{stroke:var(--warn);stroke-width:1.8}
  .st.kind-planned .box{stroke:var(--muted);stroke-dasharray:5 4;fill:transparent}
  .st.kind-exit .box{fill:var(--kpi1)}
  .st.active .box{stroke:var(--accent);stroke-width:2.6;animation:pulse 1.2s ease-in-out infinite}
  .st.failed .box{stroke:var(--bad);stroke-width:2.6}
  .st.done .box{stroke:var(--good);stroke-width:2}
  @keyframes pulse{50%{stroke-opacity:.35}}
  .cico{fill:var(--card);stroke:var(--muted);stroke-width:1}
  .st.kind-planned .cico{stroke-dasharray:2 2}
  .stereo{font:9.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:var(--muted)}
  .title{font:700 12.5px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--ink)}
  .role{font:10px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--muted)}
  .count{font:800 15px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--ink)}
  .st.kind-gate .count{fill:var(--warn)} .st.kind-planned .count,.st.kind-planned .title{fill:var(--muted)}
  .clabel{font:9.5px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--muted)}
  .badge{font:800 8.5px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--warn);letter-spacing:.06em}
  .st.kind-planned .badge{fill:var(--muted)}
  .gatepost{stroke:var(--warn);stroke-width:2.2;fill:none}
  .prog-bg{fill:var(--track);display:none} .prog{fill:var(--accent);display:none}
  .st.active .prog-bg,.st.active .prog{display:block}
  .st.active.indet .prog{animation:indet 1.1s linear infinite}
  @keyframes indet{from{transform:translateX(-40px)}to{transform:translateX(160px)}}
  .art rect{fill:var(--kpi1);stroke:var(--line);stroke-width:1}
  .art.absent rect{fill:transparent;stroke-dasharray:3 3}
  .an{font:9.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;fill:var(--ink);text-decoration:underline}
  .art.absent .an{fill:var(--muted)}
  .ac{font:10px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--muted)}
  .edge{fill:none;stroke:var(--conv);stroke-width:2;stroke-linejoin:round}
  .edge.dashed{stroke-dasharray:6 5;stroke:var(--muted);opacity:.7}
  .edge.flow{stroke:var(--accent);stroke-width:2.4;stroke-dasharray:8 6;animation:conv 1s linear infinite}
  @keyframes conv{to{stroke-dashoffset:-28}}
  .join{fill:var(--ink)}
  .jtxt{font:9.5px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--muted)}
  .jcount{font:800 12px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;fill:var(--ink)}
  @media (prefers-reduced-motion: reduce){.edge.flow,.st.active .box,.pill.live .dot,.st.active.indet .prog{animation:none}}

  /* ---- status strip ---- */
  .strip{margin-top:12px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow)}
  .strip .sh{display:flex;justify-content:space-between;gap:10px;align-items:baseline;flex-wrap:wrap}
  .strip .sn{font-weight:800} .strip .se{font-size:12px;color:var(--muted)}
  .strip .bar{height:8px;border-radius:6px;background:var(--track);overflow:hidden;margin-top:8px}
  .strip .bar > span{display:block;height:100%;width:0;background:var(--accent);transition:width .6s cubic-bezier(.22,1,.36,1)}
  .strip.failed .bar > span{background:var(--bad)}
  .strip .note{font-size:12px;color:var(--muted);margin-top:6px;font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;word-break:break-all}
  .hist{display:flex;flex-wrap:wrap;gap:6px;margin-top:10px}
  .hist span{font-size:11px;background:color-mix(in srgb,var(--good) 12%,transparent);border:1px solid color-mix(in srgb,var(--good) 35%,transparent);
    color:var(--ink);border-radius:999px;padding:3px 9px}
  .hist span.failed{background:color-mix(in srgb,var(--bad) 12%,transparent);border-color:color-mix(in srgb,var(--bad) 35%,transparent)}

  /* ---- gate bins (one series, one hue) ---- */
  .bins{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:14px 16px;box-shadow:var(--shadow)}
  .bins .bh{display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap;margin-bottom:8px}
  .bins .bt{font-weight:800} .bins .bs{font-size:12px;color:var(--muted)}
  .brow{display:grid;grid-template-columns:minmax(120px,42%) 1fr auto;align-items:center;gap:10px;padding:5px 0}
  .brow .bl{font-size:12.5px;line-height:1.2}
  .brow .bl small{display:block;font-size:10px;color:var(--muted);letter-spacing:.04em;text-transform:uppercase}
  .brow .bb{height:12px;background:var(--track);border-radius:0 4px 4px 0;overflow:hidden}
  .brow .bb > span{display:block;height:100%;width:0;background:var(--warn);border-radius:0 4px 4px 0;transition:width .9s cubic-bezier(.22,1,.36,1)}
  .brow .bv{font-weight:800;font-size:13px;min-width:2.4em;text-align:right}
  .binsnote{font-size:11.5px;color:var(--muted);margin-top:10px}

  /* ---- legend & notes ---- */
  .legendgrid{display:grid;grid-template-columns:1fr;gap:10px}
  @media (min-width:720px){.legendgrid{grid-template-columns:1fr 1fr}}
  .lcard{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:12px 14px;box-shadow:var(--shadow);font-size:13px}
  .lcard .h{font-size:12px;text-transform:uppercase;letter-spacing:.08em;color:var(--accent);font-weight:700;margin-bottom:6px}
  .lcard ul{margin:0;padding-left:18px} .lcard li{margin:3px 0}
  .lcard code{font-size:11.5px;background:var(--kpi1);padding:1px 5px;border-radius:5px;border:1px solid var(--line)}
  .lcard.vocab{border-left:4px solid var(--accent2)}
  footer{margin-top:34px;padding-top:16px;border-top:1px solid var(--line);font-size:12px;color:var(--muted)}
  footer code{background:var(--card);padding:2px 6px;border-radius:6px;color:var(--accent);font-size:11.5px;border:1px solid var(--line)}
  .modenote{font-size:11px;color:var(--muted);margin-top:6px;opacity:.85}
</style>
</head>
<body>
<header>
  <div class="brandlogo" aria-label="TUD — Chair of Railway Operations">__LOGO__</div>
  <div class="eyebrow">raiLPminer · Paper 1 corpus pipeline</div>
  <h1>Factory floor</h1>
  <div class="sub">Every station is a module, every box on a conveyor an artifact on disk, every swimlane a promise about determinism. The promotion gate decides what becomes a canonical model.</div>
  <div class="pill" id="pill"><span class="dot"></span><span id="status">snapshot</span></div>
</header>

<section><div class="kpis" id="kpis"></div></section>

<h2>Floor plan <span class="ctl"><button class="tg" id="fitBtn" aria-pressed="false" type="button">fit to width</button></span></h2>
<div class="floorwrap" id="floorwrap"><div class="floor" id="floor"></div></div>
<div class="strip" id="strip" hidden>
  <div class="sh"><span class="sn" id="s-stage"></span><span class="se" id="s-elapsed"></span></div>
  <div class="bar"><span id="s-bar"></span></div>
  <div class="note" id="s-note"></div>
  <div class="hist" id="s-hist"></div>
</div>

<h2>Promotion gate · failure bins</h2>
<div class="bins" id="bins"></div>

<h2>How to read the floor</h2>
<div class="legendgrid" id="legend"></div>

<h2>Side facts</h2>
<div class="lcard"><ul id="notes"></ul></div>

<footer>
  <span id="footer"></span>
  <div class="modenote">Build: <code>PYTHONPATH=. python3 -m corpusbuilder.factory</code> · live: <code>… --serve 8765</code> then open <code>http://127.0.0.1:8765/factory.html</code>. Türkis accent on light · TUD dark-blue field in dark mode.</div>
</footer>

<script type="application/json" id="data">__DATA__</script>
<script>
(function(){
  "use strict";
  var DATA = JSON.parse(document.getElementById("data").textContent);
  var G = {pitch:184, w:156, stH:84, cmpH:52, artH:40, gapArt:8, padTop:14, padBot:14, bus:10, left:16};
  var $ = function(id){ return document.getElementById(id); };
  var esc = function(s){ return String(s == null ? "" : s).replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;"); };
  var trunc = function(s, n){ s = String(s == null ? "" : s); return s.length > n ? s.slice(0, n - 1) + "…" : s; };

  function laneGeometry(data){
    var tops = {}, heights = {}, y = 0;
    data.lanes.forEach(function(l){
      var twoRows = data.stations.some(function(s){ return s.lane === l.id && s.row === 1; });
      var h = G.padTop + G.stH + G.gapArt + G.artH + G.bus + G.padBot + (twoRows ? 6 : 0);
      tops[l.id] = y; heights[l.id] = h; y += h;
    });
    return {tops:tops, heights:heights, total:y};
  }
  function place(data){
    var lg = laneGeometry(data), pos = {};
    var maxCol = 0;
    data.stations.forEach(function(s){
      maxCol = Math.max(maxCol, s.col);
      var x = G.left + s.col * G.pitch;
      var top = lg.tops[s.lane] + G.padTop;
      var h = G.stH;
      if (s.row === 1) { top = lg.tops[s.lane] + G.padTop + G.stH + G.gapArt; h = G.cmpH; }
      pos[s.id] = {x:x, y:top, w:G.w, h:h, cy: top + h/2, lane:s.lane, col:s.col, kind:s.kind, laneBottom: lg.tops[s.lane] + lg.heights[s.lane]};
    });
    var width = G.left + maxCol * G.pitch + G.w + G.left;
    return {pos:pos, lg:lg, width:width, height:lg.total};
  }
  function anchorOut(p){ return [p.x + p.w, p.cy]; }
  function anchorIn(p){ return [p.x, p.cy]; }
  function route(data, L, e){
    var a = L.pos[e.from], b = L.pos[e.to];
    if (!a || !b) return "";
    if (b.kind === "join") { var bx = b.x + G.w/2 - 4; return "M" + (a.x + a.w) + "," + a.cy + " H" + bx; }
    if (a.kind === "join") { var jx = a.x + G.w/2 + 4; return "M" + jx + "," + b.cy + " H" + b.x; }
    var sx = a.x + a.w, sy = a.cy, tx = b.x, ty = b.cy;
    var midX = tx - 13;
    var blocked = data.stations.some(function(s){ return s.lane === a.lane && s.col > a.col && s.col < b.col && s.row === 0; });
    if (blocked) {
      var busY = a.laneBottom - G.bus/2;
      return "M" + sx + "," + sy + " H" + (sx + 12) + " V" + busY + " H" + midX + " V" + ty + " H" + tx;
    }
    return "M" + sx + "," + sy + " H" + midX + " V" + ty + " H" + tx;
  }
  function stationSvg(s, p){
    var cls = "st kind-" + s.kind;
    var o = ['<g class="' + cls + '" data-id="' + esc(s.id) + '" data-stage="' + esc(s.stage || "") + '">'];
    var x = p.x, y = p.y, w = p.w, h = p.h;
    if (s.kind === "join") {
      return "";
    }
    if (s.kind === "exit") {
      o.push('<path class="box" d="M' + x + ',' + y + ' h' + (w - 16) + ' l16,16 v' + (h - 16) + ' h-' + w + ' z"/>');
      o.push('<path class="box" d="M' + (x + w - 16) + ',' + y + ' v16 h16" style="fill:var(--card2)"/>');
    } else {
      o.push('<rect class="box" x="' + x + '" y="' + y + '" width="' + w + '" height="' + h + '" rx="10"/>');
      o.push('<rect class="cico" x="' + (x - 4) + '" y="' + (y + 11) + '" width="8" height="5"/>');
      o.push('<rect class="cico" x="' + (x - 4) + '" y="' + (y + 19) + '" width="8" height="5"/>');
    }
    if (s.kind === "gate") {
      o.push('<path class="gatepost" d="M' + (x + w - 26) + ',' + (y + 6) + ' v14 M' + (x + w - 8) + ',' + (y + 6) + ' v14 M' + (x + w - 28) + ',' + (y + 9) + ' h22"/>');
    }
    if (s.kind === "planned") {
      o.push('<text class="badge" x="' + (x + w - 12) + '" y="' + (y + 14) + '" text-anchor="end">PLANNED</text>');
    }
    if (p.h === G.cmpH) {
      o.push('<text class="stereo" x="' + (x + 12) + '" y="' + (y + 14) + '">' + esc(trunc(s.stereo, 26)) + '</text>');
      o.push('<text class="title" x="' + (x + 12) + '" y="' + (y + 30) + '">' + esc(trunc(s.title, 19)) + '</text>');
      o.push('<text class="count" x="' + (x + 12) + '" y="' + (y + 46) + '" style="font-size:12px">' + esc(s.count) + '</text>');
      o.push('<text class="clabel" x="' + (x + 12 + 7 * String(s.count).length + 6) + '" y="' + (y + 46) + '">' + esc(trunc(s.count_label, 22)) + '</text>');
    } else {
      o.push('<text class="stereo" x="' + (x + 12) + '" y="' + (y + 15) + '">' + esc(trunc(s.stereo, 26)) + '</text>');
      o.push('<text class="title" x="' + (x + 12) + '" y="' + (y + 32) + '">' + esc(trunc(s.title, 19)) + '</text>');
      o.push('<text class="role" x="' + (x + 12) + '" y="' + (y + 46) + '">' + esc(trunc(s.role, 30)) + '</text>');
      o.push('<text class="count" x="' + (x + 12) + '" y="' + (y + 66) + '">' + esc(trunc(s.count, 18)) + '</text>');
      o.push('<text class="clabel" x="' + (x + 12) + '" y="' + (y + 78) + '">' + esc(trunc(s.count_label, 31)) + '</text>');
      o.push('<rect class="prog-bg" x="' + (x + 1) + '" y="' + (y + h - 4) + '" width="' + (w - 2) + '" height="3" rx="1.5"/>');
      o.push('<rect class="prog" x="' + (x + 1) + '" y="' + (y + h - 4) + '" width="0" height="3" rx="1.5"/>');
    }
    o.push('<title>' + esc(s.title + " — " + (s.role || "") + " · " + s.count + " " + (s.count_label || "")) + '</title>');
    o.push('</g>');
    if (s.artifact && p.h !== G.cmpH) {
      var ay = y + h + G.gapArt;
      var acls = "art" + (s.artifact.present ? "" : " absent");
      o.push('<g class="' + acls + '"><rect x="' + x + '" y="' + ay + '" width="' + w + '" height="' + G.artH + '" rx="6"/>');
      o.push('<text class="an" x="' + (x + 10) + '" y="' + (ay + 15) + '">' + esc(trunc(s.artifact.name, 25)) + '</text>');
      o.push('<text class="ac" x="' + (x + 10) + '" y="' + (ay + 30) + '">' + esc(trunc(s.artifact.count, 27)) + '</text>');
      o.push('<title>' + esc(s.artifact.name + " · " + s.artifact.count) + '</title></g>');
    }
    return o.join("");
  }
  function joinSvg(s, L){
    var p = L.pos[s.id]; if (!p) return "";
    var ys = L.pos.discover ? L.pos.discover.cy : p.cy - 40;
    var ye = L.pos.snowball ? L.pos.snowball.cy : p.cy + 40;
    var bx = p.x + G.w/2 - 4;
    var out = '<g class="st kind-join" data-id="join">';
    out += '<rect class="join" x="' + bx + '" y="' + (ys - 10) + '" width="8" height="' + (ye - ys + 20) + '" rx="2"/>';
    out += '<text class="jtxt" x="' + (bx + 4) + '" y="' + (ys - 16) + '" text-anchor="middle">' + esc(s.title) + '</text>';
    out += '<text class="jcount" x="' + (bx + 4) + '" y="' + (ye + 28) + '" text-anchor="middle">' + esc(s.count) + '</text>';
    out += '<text class="jtxt" x="' + (bx + 4) + '" y="' + (ye + 40) + '" text-anchor="middle">' + esc(s.count_label) + '</text>';
    return out + '</g>';
  }
  function renderFloor(data){
    var L = place(data);
    var svg = ['<svg class="fl" xmlns="http://www.w3.org/2000/svg" width="' + L.width + '" height="' + L.height + '" viewBox="0 0 ' + L.width + ' ' + L.height + '" role="img" aria-label="Pipeline factory floor">'];
    svg.push('<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill:var(--conv)"/></marker>' +
             '<marker id="arrA" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" style="fill:var(--accent)"/></marker></defs>');
    data.lanes.forEach(function(l, i){
      var t = L.lg.tops[l.id], h = L.lg.heights[l.id];
      svg.push('<rect class="band' + (i % 2 ? " alt" : "") + '" x="0" y="' + t + '" width="' + L.width + '" height="' + h + '"/>');
      if (i) svg.push('<line class="bandline" x1="0" x2="' + L.width + '" y1="' + t + '" y2="' + t + '"/>');
      svg.push('<text class="lanetag" x="8" y="' + (t + 12) + '">' + esc(l.name) + '</text>');
    });
    data.edges.forEach(function(e){
      var d = route(data, L, e); if (!d) return;
      svg.push('<path class="edge' + (e.dashed ? " dashed" : "") + '" data-to="' + esc(e.to) + '" d="' + d + '" marker-end="url(#arr)"/>');
    });
    data.stations.forEach(function(s){
      if (s.kind === "join") svg.push(joinSvg(s, L)); else svg.push(stationSvg(s, L.pos[s.id]));
    });
    svg.push('</svg>');
    var lanes = data.lanes.map(function(l){
      return '<div class="lane" style="height:' + L.lg.heights[l.id] + 'px"><b>' + esc(l.name) + '</b><span>' + esc(l.sub) + '</span></div>';
    }).join("");
    $("floor").innerHTML = '<div class="lanes">' + lanes + '</div>' + svg.join("");
  }
  function renderKpis(data){
    $("kpis").innerHTML = data.kpis.map(function(k){
      return '<div class="kpi' + (k.span ? " span" : "") + (k.warn ? " warn" : "") + '"><div class="v">' + esc(k.v) + '</div><div class="l">' + esc(k.l) + '</div>' + (k.tag ? '<div class="tag">' + esc(k.tag) + '</div>' : "") + '</div>';
    }).join("");
  }
  function renderBins(data){
    var g = data.gate || {}, bins = g.bins || [];
    var max = bins.reduce(function(m, b){ return Math.max(m, b.n); }, 0) || 1;
    var head = '<div class="bh"><span class="bt">' + esc((g.promoted == null ? "—" : g.promoted) + " of " + (g.papers == null ? "—" : g.papers) + " papers pass the gate") + '</span><span class="bs">' + esc(g.failed == null ? "" : g.failed + " papers land in " + bins.length + " bins · rules " + (g.rules || "—")) + '</span></div>';
    if (!bins.length) { $("bins").innerHTML = head + '<div class="binsnote">No promotion report yet — run <code>corpusbuilder.promote</code>.</div>'; return; }
    var rows = bins.map(function(b){
      var cat = b.category === "outside_grammar" ? "source: grammar" : (b.category === "pipeline_incomplete" ? "pipeline gap" : "source: " + String(b.category).replace(/_/g, " "));
      return '<div class="brow" title="' + esc(b.n + " papers · " + b.label + " · " + cat) + '"><div class="bl">' + esc(b.label) + '<small>' + esc(cat) + '</small></div><div class="bb"><span data-w="' + (100 * b.n / max).toFixed(1) + '"></span></div><div class="bv">' + esc(b.n) + '</div></div>';
    }).join("");
    $("bins").innerHTML = head + rows + '<div class="binsnote">One series, one hue: the bars compare counts only. "Source" bins are findings about the literature; "pipeline gap" bins are our own unfinished steps and are never reported as properties of the papers.</div>';
    requestAnimationFrame(function(){ document.querySelectorAll("#bins .bb > span").forEach(function(sp){ sp.style.width = sp.getAttribute("data-w") + "%"; }); });
  }
  function renderLegend(data){
    $("legend").innerHTML =
      '<div class="lcard"><div class="h">UML elements, factory reading</div><ul>' +
      '<li><b>Station</b> = a UML component «module» with its role and a live counter; the two small tabs on its left edge are the component icon.</li>' +
      '<li><b>Box below a station</b> = a UML object node: the artifact that station leaves on the conveyor (its file path underlined, with a count). Dashed = not produced yet.</li>' +
      '<li><b>Arrow</b> = conveyor. It animates into the station that is running. Dashed arrows are planned flows.</li>' +
      '<li><b>Thick bar</b> = UML join: the two identification arms (database queries, citation snowball) merge into one retrieval queue.</li>' +
      '<li><b>Gate</b> (orange posts) = promotion: a paper passes only if every accepted row is in the canonical grammar; everything else is sorted into the bins below, by cause.</li>' +
      '<li><b>Swimlanes</b> = the determinism boundary: only the top lane is byte-reproducible; human verdicts and LLM replies enter as logged, versioned inputs and are parser-gated.</li></ul></div>' +
      '<div class="lcard vocab"><div class="h">The planned station: vocabulary check</div>' +
      '<p style="margin:0 0 6px">It lists, per paper, the symbol names the formulas use after normalization next to the names the declaration sidecar declares; the difference is the fill-in list.</p>' +
      '<ul><li>A formula writes <code>t_{i}^{arr}</code>; after the rewrite rules that symbol is spelled <code>t_arr</code>.</li>' +
      '<li>The sidecar today declares <code>t</code> only, so the gate refuses the paper by name.</li>' +
      '<li>The check prints exactly the missing names, so a human or the assist stage declares <code>t_arr</code> and nothing else.</li></ul></div>';
  }
  function renderNotes(data){
    $("notes").innerHTML = (data.notes || []).map(function(n){ return "<li>" + esc(n) + "</li>"; }).join("");
    $("footer").textContent = "Snapshot of the newest input artifact: " + (data.snapshot_of || "no artifacts yet") + " · derived from " + data.derived_from + " · schema " + data.schema_version + ". Counts, keys, stage names and rule versions only — no publisher text on this page.";
  }
  function renderAll(data){ renderKpis(data); renderFloor(data); renderBins(data); renderLegend(data); renderNotes(data); }

  // ---- live status ----
  var lastStatusText = null, lastDataText = null, misses = 0, timer = null;
  function fmtElapsed(sec){ if (sec == null || isNaN(sec)) return ""; sec = Math.max(0, Math.round(sec)); var m = Math.floor(sec / 60), s = sec % 60; return (m ? m + "m " : "") + s + "s"; }
  function applyStatus(st){
    document.querySelectorAll(".st").forEach(function(g){ g.classList.remove("active", "failed", "done", "indet"); });
    document.querySelectorAll(".edge").forEach(function(p){ p.classList.remove("flow"); p.setAttribute("marker-end", "url(#arr)"); });
    var strip = $("strip"), pill = $("pill");
    if (!st || !st.stage) { strip.hidden = true; return; }
    strip.hidden = false;
    strip.classList.toggle("failed", st.state === "failed");
    pill.classList.toggle("failed", st.state === "failed");
    var g = document.querySelector('.st[data-stage="' + st.stage + '"]');
    var frac = (st.total && st.done != null) ? Math.min(1, st.done / st.total) : null;
    if (g) {
      g.classList.add(st.state === "running" ? "active" : (st.state === "failed" ? "failed" : "done"));
      if (st.state === "running" && frac == null) g.classList.add("indet");
      var pr = g.querySelector(".prog"), bg = g.querySelector(".prog-bg");
      if (pr && bg) pr.setAttribute("width", frac == null ? 40 : Math.max(2, (G.w - 2) * frac));
      if (st.state === "running") document.querySelectorAll('.edge[data-to="' + st.stage + '"]').forEach(function(p){ p.classList.add("flow"); p.setAttribute("marker-end", "url(#arrA)"); });
    }
    var started = st.started_at ? Date.parse(st.started_at) : NaN, updated = st.updated_at ? Date.parse(st.updated_at) : NaN;
    var ref = st.state === "running" ? Date.now() : updated;
    $("s-stage").textContent = st.stage + " · " + st.state + (st.total ? " · " + (st.done == null ? 0 : st.done) + " / " + st.total : (st.done != null ? " · " + st.done : ""));
    $("s-elapsed").textContent = isNaN(started) ? "" : "elapsed " + fmtElapsed((ref - started) / 1000) + (st.state !== "running" ? " · ended " + (st.updated_at || "") : "");
    $("s-bar").style.width = (frac == null ? (st.state === "done" ? 100 : 0) : Math.round(100 * frac)) + "%";
    $("s-note").textContent = st.note || "";
    $("s-hist").innerHTML = (st.history || []).slice(-8).reverse().map(function(h){
      return '<span class="' + (h.state === "failed" ? "failed" : "") + '">' + esc(h.stage) + " " + (h.state === "failed" ? "✕" : "✓") + (h.seconds != null ? " " + fmtElapsed(h.seconds) : "") + "</span>";
    }).join("");
    $("status").innerHTML = "<b>live</b> · " + esc(st.stage) + " " + esc(st.state);
  }
  function poll(){
    if (!/^https?:$/.test(location.protocol)) return;
    var hit = false;
    fetch("factory_status.json", {cache:"no-store"}).then(function(r){ if (!r.ok) throw 0; return r.text(); }).then(function(t){
      hit = true; misses = 0; $("pill").classList.add("live");
      if (t !== lastStatusText) { lastStatusText = t; try { applyStatus(JSON.parse(t)); } catch (e) {} }
      else if (lastStatusText) { try { var st = JSON.parse(lastStatusText); if (st.state === "running") applyStatus(st); } catch (e) {} }
    }).catch(function(){ }).finally(function(){
      fetch("factory_data.json", {cache:"no-store"}).then(function(r){ if (!r.ok) throw 0; return r.text(); }).then(function(t){
        hit = true;
        if (t !== lastDataText) { lastDataText = t; try { renderAll(JSON.parse(t)); if (lastStatusText) applyStatus(JSON.parse(lastStatusText)); } catch (e) {} }
      }).catch(function(){ }).finally(function(){
        if (!hit) { misses += 1; if (misses >= 3) { clearInterval(timer); $("status").textContent = "snapshot · serve with --serve for live mode"; $("pill").classList.remove("live"); } }
      });
    });
  }
  renderAll(DATA);
  $("fitBtn").addEventListener("click", function(){
    var on = this.getAttribute("aria-pressed") !== "true";
    this.setAttribute("aria-pressed", on ? "true" : "false");
    $("floorwrap").classList.toggle("fit", on);
  });
  $("status").textContent = "snapshot" + (DATA.snapshot_of ? " · inputs as of " + DATA.snapshot_of : "");
  if (/^https?:$/.test(location.protocol)) { poll(); timer = setInterval(poll, 2000); }
})();
</script>
</body>
</html>
"""


def build_html(data: dict) -> str:
    """Render the page for a snapshot. Pure: same data -> same bytes."""
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1).replace("</", "<\\/")
    return _TEMPLATE.replace("__LOGO__", _LOGO).replace("__DATA__", payload)


def build(
    corpus: Path = CORPUS,
    *,
    out: Path | None = None,
    outputs: Path | None = OUTPUTS,
    paper_dir: Path | None = PAPER_DIR,
) -> tuple[Path, Path]:
    """Write ``factory.html`` and ``factory_data.json`` (next to each other, so
    the page can poll the JSON when served). Returns both paths."""
    data = snapshot(corpus, outputs=outputs, paper_dir=paper_dir)
    html_path = out or (corpus / HTML_NAME)
    data_path = html_path.parent / DATA_NAME
    html_path.parent.mkdir(parents=True, exist_ok=True)
    data_path.write_text(
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=1) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    html_path.write_text(build_html(data), encoding="utf-8", newline="\n")
    return html_path, data_path


# ---------------------------------------------------------------------------
# Local live server
# ---------------------------------------------------------------------------


def _signature(corpus: Path, outputs: Path | None) -> tuple:
    """A cheap change detector: mtimes of the input files + artifact dirs."""
    probes = [
        corpus / "candidates.json",
        corpus / "snowball_candidates.json",
        corpus / "prisma.json",
        corpus / "resolution.json",
        corpus / "promotion.json",
        corpus / "objective_flags.json",
        corpus / "wl" / "similarity.json",
        corpus / "fingerprint" / "clusters.json",
        corpus / "decisions",
        corpus / "declarations",
        corpus / "formulations",
        corpus / "promoted",
        corpus / "talkpack" / "figures",
        corpus / "assist" / "cache",
    ]
    if outputs:
        probes += [outputs / "validation_report.json", outputs / "run_summary.json"]
    probes += [p for p in sorted(corpus.glob("vdemo*")) if p.is_dir()]
    sig = []
    for p in probes:
        try:
            sig.append((str(p), p.stat().st_mtime_ns))
        except OSError:
            sig.append((str(p), None))
    return tuple(sig)


class _Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def serve(
    corpus: Path = CORPUS,
    port: int = 8765,
    *,
    outputs: Path | None = OUTPUTS,
    paper_dir: Path | None = PAPER_DIR,
    once: bool = False,
    on_ready: Callable[[str], None] | None = None,
) -> str:
    """Rebuild the page, then serve ``corpus/`` on 127.0.0.1 and rebuild the
    snapshot whenever an input artifact changes (checked every 2 s)."""
    build(corpus, outputs=outputs, paper_dir=paper_dir)
    stop = threading.Event()

    def watcher() -> None:
        last = _signature(corpus, outputs)
        while not stop.wait(2.0):
            sig = _signature(corpus, outputs)
            if sig != last:
                last = sig
                try:
                    build(corpus, outputs=outputs, paper_dir=paper_dir)
                except Exception:  # the server keeps serving the last good page
                    continue

    def handler(*args: Any, **kwargs: Any) -> _Handler:
        return _Handler(*args, directory=str(corpus), **kwargs)

    httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{httpd.server_address[1]}/{HTML_NAME}"
    print(f"factory floor: {url}  (Ctrl-C stops; snapshot rebuilds when artifacts change)")
    if on_ready is not None:
        on_ready(url)
    t = threading.Thread(target=watcher, name="factory-watcher", daemon=True)
    t.start()
    try:
        if once:
            httpd.handle_request()
        else:
            httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        httpd.server_close()
    return url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusbuilder.factory",
        description="Render the corpus pipeline as a factory floor (and serve it live).",
    )
    parser.add_argument("--corpus", type=Path, default=CORPUS, help="corpus directory")
    parser.add_argument(
        "--out", type=Path, default=None, help="HTML path (default corpus/factory.html)"
    )
    parser.add_argument(
        "--outputs", type=Path, default=OUTPUTS, help="railpminer outputs dir (validation/taxonomy)"
    )
    parser.add_argument(
        "--no-paper", action="store_true", help="do not compare the macro files with the paper copy"
    )
    parser.add_argument(
        "--serve",
        nargs="?",
        const=8765,
        type=int,
        default=None,
        metavar="PORT",
        help="serve corpus/ on 127.0.0.1:PORT (default 8765) and rebuild on change",
    )
    args = parser.parse_args(argv)
    paper = None if args.no_paper else PAPER_DIR
    if args.serve is not None:
        serve(args.corpus, args.serve, outputs=args.outputs, paper_dir=paper)
        return 0
    html_path, data_path = build(args.corpus, out=args.out, outputs=args.outputs, paper_dir=paper)
    print(f"wrote {html_path} ({html_path.stat().st_size:,} bytes) + {data_path.name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
