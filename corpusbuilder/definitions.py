"""assist stage d — a small model refines *where a definition starts and ends*.

The roles stage proposes, by sentence rules, the verbatim sentence that defines
a symbol mention. That is citable but sometimes too coarse (a definition that
runs over two sentences, a sentence that defines two symbols, a clause after a
colon). This stage asks a small model, per paragraph, to return the **verbatim
substring** of the paragraph that defines each candidate, and whether the
defining words come before or after the element. Nothing the model writes is
kept unless it is an exact substring of the paragraph that contains the
element, so the result stays deterministically citable (paragraph index +
character offsets); an answer that fails that check keeps the rule's span.

Replies are cached like every other assist stage (``corpus/assist/cache``,
keyed by the payload digest), so reruns are free. Output:
``corpus/assist/definitions/<key>.json`` (gitignored: quotes the paper) which
``discovergame`` reads when it builds the paper's page (``source: model``).

Run::

    PYTHONPATH=. python3 -m corpusbuilder.definitions KEY [KEY ...]
    PYTHONPATH=. python3 -m corpusbuilder.definitions --worklist 10     # top of corpus/review/discover.html
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

from corpusbuilder import assist
from corpusbuilder.discover import DISCOVERY, load_record
from corpusbuilder.promote import CORPUS
from corpusbuilder.roles import propose_roles

STAGE = "d"
OUT_DIR = CORPUS / "assist" / "definitions"
REPORT = CORPUS / "assist" / "report.definitions.json"
SCHEMA = "definitions-1"

SYSTEM = """You mark, in a paragraph of a scientific paper, the exact words that DEFINE given mathematical symbols.
The paragraph is plain text; every mathematical element is written as ⟨LaTeX⟩. You receive a list of candidate elements
(their id and LaTeX) that a rule believes the paragraph defines. For each candidate answer whether the paragraph
really defines it (introduces what the symbol stands for) and, if so, copy the defining passage VERBATIM from the
paragraph: an exact, contiguous substring that includes the element itself, usually one sentence, sometimes a clause
or two sentences. Never paraphrase, never fix typos, never add words. Say whether the defining words come "before"
the element ("the set of trains is denoted by ⟨T⟩"), "after" it ("⟨T⟩ is the set of trains"), or "around" it
("Let ⟨T⟩ be the set of trains").
Reply with one JSON object: {"<id>": {"defines": true|false, "passage": "<verbatim substring>", "position": "before"|"after"|"around"}, ...}
with every candidate id present."""


def _user(plain: str, candidates: list[dict]) -> str:
    return json.dumps({"paragraph": plain, "candidates": candidates}, ensure_ascii=False)


def validate_reply(
    reply: dict, plain: str, candidates: list[dict]
) -> tuple[dict[str, dict], list[str]]:
    """Keep only verbatim passages that contain the element; return ``(spans, problems)``."""
    spans: dict[str, dict] = {}
    problems: list[str] = []
    for c in candidates:
        cid, marker = c["id"], f"⟨{c['latex']}⟩"
        ans = reply.get(cid)
        if not isinstance(ans, dict):
            problems.append(f"{cid}: missing")
            continue
        if not ans.get("defines"):
            spans[cid] = {"defines": False, "source": "model"}
            continue
        passage = str(ans.get("passage") or "").strip()
        if not passage or passage not in plain or marker not in passage:
            problems.append(f"{cid}: passage is not a verbatim substring containing the element")
            continue
        start = plain.index(passage)
        position = (
            ans.get("position")
            if ans.get("position") in ("before", "after", "around")
            else "around"
        )
        spans[cid] = {
            "start": start,
            "end": start + len(passage),
            "text": passage,
            "position": position,
            "source": "model",
            "defines": True,
        }
    return spans, problems


def refine_paper(
    ws: assist.Workspace,
    key: str,
    *,
    discovery_dir: Path = DISCOVERY,
    out_dir: Path = OUT_DIR,
    chat=None,
    force: bool = False,
) -> dict:
    """Refine every definition candidate of one paper; write the spans file; return its summary."""
    rec = load_record(key, discovery_dir)
    if rec is None:
        raise assist.AssistError(f"no discovery record for {key}")
    roles = propose_roles(rec)
    by_para: dict[int, list[dict]] = {}
    for mid, r in roles.items():
        if r["role"] == "definition" and r.get("span") and "para" in r["span"]:
            by_para.setdefault(r["span"]["para"], []).append(
                {
                    "id": mid,
                    "latex": next(m["latex"] for m in rec["maths"] if m["id"] == mid),
                    "rule_passage": r["span"]["text"],
                }
            )
    paras = {p["i"]: p for p in rec["paras"]}
    usage = assist.Usage()
    spans: dict[str, dict] = {}
    problems: list[str] = []
    calls = 0
    for pi in sorted(by_para):
        plain = paras[pi].get("plain") or ""
        candidates = by_para[pi]
        payload = assist._payload(SYSTEM, _user(plain, candidates), stage=STAGE)
        content = (
            chat(payload)
            if chat
            else assist._cached_chat(ws, key, f"{STAGE}{pi}", payload, usage, force=force)
        )
        calls += 1
        try:
            reply = assist._parse_json_reply(content)
        except (ValueError, json.JSONDecodeError) as e:
            problems.append(f"para {pi}: reply not JSON ({e})")
            continue
        got, bad = validate_reply(reply, plain, candidates)
        problems.extend(f"para {pi} {b}" for b in bad)
        for cid, sp in got.items():
            sp["para"] = pi
            rule = roles[cid]["span"]
            sp["differs_from_rule"] = bool(sp.get("defines")) and (sp["start"], sp["end"]) != (
                rule["start"],
                rule["end"],
            )
            spans[cid] = sp
    out = {
        "schema_version": SCHEMA,
        "paper_key": key,
        "model": assist.model_id(STAGE),
        "date": date.today().isoformat(),
        "candidates": sum(len(v) for v in by_para.values()),
        "paragraphs": len(by_para),
        "spans": spans,
        "problems": problems,
        "summary": {
            "refined": sum(1 for s in spans.values() if s.get("defines")),
            "not_a_definition": sum(1 for s in spans.values() if not s.get("defines")),
            "differs_from_rule": sum(1 for s in spans.values() if s.get("differs_from_rule")),
            "rejected": len(problems),
            "calls": calls,
            "cache_hits": usage.cache_hits,
            "api_calls": usage.calls,
        },
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{key}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    return out


def worklist_keys(n: int, index_page: Path = CORPUS / "review" / "discover.html") -> list[str]:
    html = index_page.read_text(encoding="utf-8")
    m = re.search(r'<script id="rows" type="application/json">(.*?)</script>', html, re.S)
    rows = json.loads(m.group(1))["papers"] if m else []
    return [r["key"] for r in rows[:n]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("keys", nargs="*")
    ap.add_argument(
        "--worklist", type=int, default=0, help="take the first N papers of the discovery worklist"
    )
    ap.add_argument("--force", action="store_true", help="ignore the reply cache")
    args = ap.parse_args(argv)
    keys = list(args.keys) + (worklist_keys(args.worklist) if args.worklist else [])
    if not keys:
        ap.error("give paper keys or --worklist N")
    ws = assist.Workspace()
    summaries: dict[str, dict] = {}
    for i, key in enumerate(keys, 1):
        try:
            out = refine_paper(ws, key, force=args.force)
        except assist.AssistError as exc:
            print(f"[{i}/{len(keys)}] {key} stopped: {exc}", file=sys.stderr)
            break
        summaries[key] = out["summary"]
        s = out["summary"]
        print(
            f"[{i}/{len(keys)}] {key}: {s['refined']} refined ({s['differs_from_rule']} differ from the rule), {s['not_a_definition']} not definitions, {s['rejected']} rejected, {s['api_calls']} calls",
            file=sys.stderr,
        )
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        json.dumps(
            {"schema_version": SCHEMA, "date": date.today().isoformat(), "papers": summaries},
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        ),
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
