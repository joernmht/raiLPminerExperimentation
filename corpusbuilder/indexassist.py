"""assist stage i — a small model is the *second annotator* for index letters.

The index discovery (:mod:`corpusbuilder.indices`) proposes, per paper, a
verdict for every letter that appears in a script position (index / alias /
candidate / juxtaposed / label) and names the rule that decided it. The human
decides on the discovery page (index / label / not / unsure, plus the family).
Measured on the first five labelled papers (2026-09-19, 84 letters) the
proposal is near-perfect where a binder names the family and weak in a few
small classes (binder without a family, notation-table letters, multi-letter
names, uppercase letters used as indices, position aliases). This stage asks a
small model for an *independent* second opinion on every letter and a
deterministic router decides which letters need the human at all:

* rule and model agree on "bind / don't bind" (and on the family where both
  name one): accepted, ``source: assist``;
* they disagree, the model is unsure, or an index has no family: the human's
  queue.

Independence is kept by construction: the model sees the evidence (formulas
carrying the letter, the paper's notation-table line, a prose window, the
binder sets, use counts) but **never the rule's verdict**. It learns the
house convention from the human's own decided papers, given as examples
(few-shot); a paper is never its own example.

Nothing the model writes bypasses the gate (:func:`validate_reply`): the
verdict must be one of the page's four, a family must be a set the paper
actually names (a discovered family, a declared index set or a binder set),
and everything else is recorded as a problem and treated as *unsure*.

Replies are cached like every assist stage (``corpus/assist/cache``, keyed by
the payload digest). Outputs: ``corpus/assist/indexassist/<key>.json``
(gitignored: quotes the paper's formulas) and, for ``--evaluate``, the
counts-only ``corpus/indexassist.{json,md}``.

The model's verdicts are **not** shown on the discovery page while the
reliability sample is being labelled: the human labels are the test set.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.indexassist --evaluate          # leave-one-paper-out over the labelled papers
    PYTHONPATH=. python3 -m corpusbuilder.indexassist KEY [KEY ...]       # second opinion + routing for these papers
    PYTHONPATH=. python3 -m corpusbuilder.indexassist --worklist 40       # top of corpus/review/discover.html
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Iterable
from datetime import date
from pathlib import Path

from corpusbuilder import assist, discovergame
from corpusbuilder.definitions import worklist_keys
from corpusbuilder.discover import DISCOVERY, load_record
from corpusbuilder.fulltext import DOSSIERS
from corpusbuilder.indices import INDICES
from corpusbuilder.promote import CORPUS

STAGE = "i"
SCHEMA = "indexassist-1"
OUT_DIR = CORPUS / "assist" / "indexassist"
REPORT_JSON = CORPUS / "indexassist.json"
REPORT_MD = CORPUS / "indexassist.md"
STATE_DIR = CORPUS / "review" / "state"
INBOX_DIR = CORPUS / "review" / "inbox"
DECISIONS_DIR = CORPUS / "decisions"

#: The page's letter verdicts (``discovergame.validate_export``).
VERDICTS = ("index", "label", "not", "unsure")
#: Deterministic verdicts that mean "bind this letter".
RULE_BINDS = ("index", "alias")

FORMULAS_PER_LETTER = 3
FORMULAS_PER_EXAMPLE = 2
LATEX_MAX = 220
CONTEXT_MAX = 200
DESC_MAX = 160
CARRIED_BY_MAX = 6
#: Soft cap on the examples block, in characters (the target paper is never cut).
EXAMPLE_CHAR_BUDGET = 60_000

SYSTEM = """You decide, for letters that appear as subscripts or superscripts in the formulas of a scientific paper on railway optimisation, whether each letter is an INDEX or not. You receive, per letter, evidence extracted deterministically from the paper: a few formulas that carry the letter, the paper's own notation-table line for it, a prose window, the sets the letter is bound to by sum/forall binders, and use counts. You also receive the paper's named sets (families) and, as examples, letters of other papers with the verdicts a human expert gave. Follow the expert's conventions.
Verdicts:
- "index": the letter is a dummy index that ranges over a set (bound by a sum or forall, declared "i ∈ I" in a table or in prose, or used like one). Give its "family": the set it ranges over, copied from the paper's list of families. Prefer the set the paper declares for the letter; if the paper names none and the letter is bound only through a tuple such as (i, j) ∈ E, give that tuple set. Decorated letters (kp = k', k_hat, r_tilde, npp = n'') are indices of the same family as their base letter when they are used like it.
- "label": the letter or word is part of a NAME (a mnemonic script such as arr, dep, max, min, st, tr, or a letter that distinguishes quantities such as B in t_B). Removing it would change WHICH quantity is meant, not WHICH element of a set.
- "not": not an index at all: the symbol is a set, a parameter, a variable, a constant or a function (e.g. S when it is the set of stations, lambda in lambda_s, N as a count), even when it sits in a script position.
- "unsure": the evidence does not decide, or one use is an index and another a label.
Rules: never invent a set name; the family must be one of the paper's families or empty. Copy letter names exactly as given. A capital letter is "index" only when the paper uses it as a running index; a set name is "not". Reply with one JSON object: {"<letter>": {"verdict": "index"|"label"|"not"|"unsure", "family": "<family or empty>", "why": "<at most twelve words>"}, ...} with every letter of the paper present."""


# --------------------------------------------------------------------------- #
# Evidence — deterministic, and without the rule's verdict
# --------------------------------------------------------------------------- #


def _clip(text: str, n: int) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


def dossier_latex(key: str, dossier_dir: Path = DOSSIERS) -> dict[str, str]:
    """``{eq-NNNN: latex}`` from the Tier-2 dossier; empty when there is none."""
    path = dossier_dir / f"{key}.json"
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {
        f["id"]: f.get("latex") or ""
        for f in (obj.get("formulas") or [])
        if isinstance(f, dict) and f.get("id") and f.get("latex")
    }


def row_latex(rec: dict, fallback: dict[str, str] | None = None) -> dict[str, str]:
    """``{row id: latex}``; display rows also under their dossier ``eq-NNNN`` id.

    ``fallback`` (the dossier's formulas) fills display rows the discovery record
    did not match to a ``<mml:math>`` element."""
    out: dict[str, str] = dict(fallback or {})
    for m in rec.get("maths") or []:
        out[m["id"]] = m.get("latex") or ""
        if m.get("where") == "display" and m.get("eq"):
            out[m["eq"]] = m.get("latex") or ""
    return out


def row_context(rec: dict) -> dict[str, str]:
    return {
        m["id"]: m.get("context") or ""
        for m in rec.get("maths") or []
        if m.get("where") == "inline" and m.get("context")
    }


def paper_families(idx: dict) -> dict[str, str]:
    """The sets a paper names, with the paper's own words where it has them."""
    fams: dict[str, str] = {}
    for name, f in sorted((idx.get("families") or {}).items()):
        letters = ", ".join(sorted((f.get("letters") or {}).keys()))
        desc = _clip(f.get("desc") or "", DESC_MAX)
        fams[name] = (desc + (f" [letters: {letters}]" if letters else "")).strip() or "set"
    dummies = set(idx.get("declared_index_are_letters") or [])
    for name in idx.get("declared_index") or []:
        if name not in dummies:
            fams.setdefault(name, "declared as an index set")
    for r in (idx.get("rows") or {}).values():
        for b in r.get("binders") or []:
            if b.get("family"):
                fams.setdefault(b["family"], "binder set")
    return fams


def allowed_families(idx: dict) -> set[str]:
    allowed = set(paper_families(idx))
    for e in (idx.get("letters") or {}).values():
        allowed.update((e.get("families") or {}).keys())
        if e.get("family"):
            allowed.add(e["family"])
    return allowed


def letter_evidence(
    name: str,
    entry: dict,
    idx_rows: dict,
    latex_by_row: dict[str, str],
    context_by_row: dict[str, str],
    *,
    formulas: int = FORMULAS_PER_LETTER,
) -> dict:
    """What the model sees for one letter. No verdict, no rule name."""
    rows = list(entry.get("rows") or [])

    def binds(rid: str) -> bool:
        r = idx_rows.get(rid) or {}
        return any(name in (b.get("letters") or []) for b in r.get("binders") or [])

    display = sorted(
        (r for r in rows if r.startswith("eq-") and r in latex_by_row),
        key=lambda r: (not binds(r), r),
    )
    inline = sorted(
        (r for r in rows if r.startswith("m-") and r in latex_by_row),
        key=lambda r: (not binds(r), r),
    )
    picked = (display + inline)[:formulas]
    bases = entry.get("bases") or {}
    ev = {
        "letter": name,
        "formulas": [_clip(latex_by_row[r], LATEX_MAX) for r in picked],
        "paper_says": _clip(entry.get("desc") or "", DESC_MAX),
        "prose": [_clip(context_by_row[r], CONTEXT_MAX) for r in inline if r in context_by_row][:1],
        "bound_to": entry.get("families") or {},
        "carried_by": sorted(bases, key=lambda b: (-bases[b], b))[:CARRIED_BY_MAX],
        "uses": {
            "bound": int(entry.get("bound_rows") or 0),
            "subscript": int(entry.get("sub_rows") or 0),
            "superscript": int(entry.get("sup_rows") or 0),
            "prose": int(entry.get("prose_rows") or 0),
            "table": int(entry.get("table_rows") or 0),
        },
    }
    if entry.get("capped"):
        ev["ranges_to"] = entry["capped"]
    return ev


def load_paper(
    key: str,
    *,
    discovery_dir: Path = DISCOVERY,
    indices_dir: Path = INDICES,
    dossier_dir: Path = DOSSIERS,
) -> tuple[dict, dict]:
    rec = load_record(key, discovery_dir)
    if rec is None:
        raise assist.AssistError(f"no discovery record for {key}")
    path = indices_dir / f"{key}.json"
    if not path.exists():
        raise assist.AssistError(f"no index sidecar for {key}")
    rec = {**rec, "_dossier_latex": dossier_latex(key, dossier_dir)}
    return rec, json.loads(path.read_text(encoding="utf-8"))


def paper_block(key: str, rec: dict, idx: dict, *, formulas: int = FORMULAS_PER_LETTER) -> dict:
    latex, ctx = row_latex(rec, rec.get("_dossier_latex")), row_context(rec)
    rows = idx.get("rows") or {}
    return {
        "paper": key,
        "families": paper_families(idx),
        "letters": [
            letter_evidence(n, e, rows, latex, ctx, formulas=formulas)
            for n, e in sorted((idx.get("letters") or {}).items())
        ],
    }


def example_block(key: str, rec: dict, idx: dict, labels: dict[str, dict]) -> dict:
    """A labelled paper as a few-shot example: only the letters the human decided."""
    block = paper_block(key, rec, idx, formulas=FORMULAS_PER_EXAMPLE)
    out = []
    for ev in block["letters"]:
        h = labels.get(ev["letter"])
        if not h or h.get("verdict") not in VERDICTS or h["verdict"] == "unsure":
            continue
        out.append({**ev, "expert": {"verdict": h["verdict"], "family": h.get("family") or ""}})
    return {**block, "letters": out}


def _user(target: dict, examples: list[dict]) -> str:
    budget = EXAMPLE_CHAR_BUDGET
    kept: list[dict] = []
    for ex in examples:
        size = len(json.dumps(ex, ensure_ascii=False))
        if kept and size > budget:
            break
        kept.append(ex)
        budget -= size
    return json.dumps({"examples": kept, "paper": target}, ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------- #
# Gate + router
# --------------------------------------------------------------------------- #


def validate_reply(
    reply: dict, letters: Iterable[str], allowed: set[str]
) -> tuple[dict[str, dict], list[str]]:
    """One gated verdict per letter; anything outside the contract becomes *unsure* and a problem."""
    verdicts: dict[str, dict] = {}
    problems: list[str] = []
    for name in letters:
        ans = reply.get(name)
        if not isinstance(ans, dict):
            problems.append(f"{name}: missing")
            verdicts[name] = {
                "verdict": "unsure",
                "family": "",
                "why": "",
                "source": "model",
                "gated": True,
            }
            continue
        verdict = ans.get("verdict")
        family = str(ans.get("family") or "").strip()
        why = _clip(str(ans.get("why") or ""), 120)
        gated = False
        if verdict not in VERDICTS:
            problems.append(f"{name}: verdict {verdict!r}")
            verdict, gated = "unsure", True
        if verdict != "index":
            family = ""
        elif family and family not in allowed:
            problems.append(f"{name}: family {family!r} is not a set the paper names")
            family, gated = "", True
        verdicts[name] = {
            "verdict": verdict,
            "family": family,
            "why": why,
            "source": "model",
            "gated": gated,
        }
    return verdicts, problems


def route(entry: dict, model: dict) -> tuple[str, str, dict | None]:
    """``("auto", reason, accepted)`` or ``("human", reason, None)`` for one letter."""
    rule_binds = entry.get("verdict") in RULE_BINDS
    mv = model.get("verdict")
    if mv == "unsure":
        return "human", "model unsure", None
    if rule_binds != (mv == "index"):
        return "human", "bind disagreement", None
    if mv != "index":
        return "auto", "both do not bind", {"verdict": mv, "family": "", "source": "assist"}
    rf, mf = entry.get("family") or "", model.get("family") or ""
    if not rf:
        return "human", "rule has no family", None
    if mf and mf != rf:
        return "human", "family disagreement", None
    reason = "both bind, same family" if mf else "both bind, model names no family"
    return "auto", reason, {"verdict": "index", "family": rf, "source": "assist"}


# --------------------------------------------------------------------------- #
# Human labels — exports and page state
# --------------------------------------------------------------------------- #


def load_labels(paths: Iterable[Path]) -> dict[str, dict[str, dict]]:
    """``{paper key: {letter: {verdict, family}}}`` from ``discover-decisions`` exports and
    page-state files, in the order given (later files win)."""
    out: dict[str, dict[str, dict]] = {}
    for p in paths:
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(obj, dict):
            continue
        if obj.get("schema_version") in discovergame.EXPORT_SCHEMAS:
            for key, m in discovergame.load_decisions([p]).items():
                out.setdefault(key, {}).update(m["indices"])
        elif isinstance(obj.get("indices"), dict):
            key = str(obj.get("paper_key") or p.stem)
            for n, v in obj["indices"].items():
                if isinstance(v, dict) and v.get("verdict") in VERDICTS:
                    out.setdefault(key, {})[n] = {
                        "verdict": v["verdict"],
                        "family": v.get("family") or "",
                    }
    return {k: v for k, v in out.items() if v}


def default_label_paths(
    *, decisions: Path = DECISIONS_DIR, inbox: Path = INBOX_DIR, state: Path = STATE_DIR
) -> list[Path]:
    paths: list[Path] = []
    for d, pattern in ((decisions, "discover*.json"), (inbox, "*.json"), (state, "*.json")):
        if d.exists():
            paths.extend(sorted(d.glob(pattern)))
    return paths


# --------------------------------------------------------------------------- #
# One paper
# --------------------------------------------------------------------------- #


def run_paper(
    ws: assist.Workspace,
    key: str,
    examples: list[dict],
    *,
    discovery_dir: Path = DISCOVERY,
    indices_dir: Path = INDICES,
    out_dir: Path = OUT_DIR,
    chat=None,
    force: bool = False,
) -> dict:
    """Second opinion + routing for every letter of one paper; writes the sidecar; returns it."""
    rec, idx = load_paper(key, discovery_dir=discovery_dir, indices_dir=indices_dir)
    letters = idx.get("letters") or {}
    examples = [ex for ex in examples if ex.get("paper") != key]
    usage = assist.Usage()
    problems: list[str] = []
    verdicts: dict[str, dict] = {}
    calls = 0
    if letters:
        payload = assist._payload(SYSTEM, _user(paper_block(key, rec, idx), examples), stage=STAGE)
        content = (
            chat(payload)
            if chat
            else assist._cached_chat(ws, key, STAGE, payload, usage, force=force)
        )
        calls = 1
        try:
            reply = assist._parse_json_reply(content)
        except (ValueError, json.JSONDecodeError) as e:
            problems.append(f"reply not JSON ({e})")
            reply = {}
        verdicts, bad = validate_reply(reply, sorted(letters), allowed_families(idx))
        problems.extend(bad)
    out_letters: dict[str, dict] = {}
    for name in sorted(letters):
        e = letters[name]
        m = verdicts.get(name) or {
            "verdict": "unsure",
            "family": "",
            "why": "",
            "source": "model",
            "gated": True,
        }
        where, reason, accepted = route(e, m)
        out_letters[name] = {
            "rule": e.get("rule"),
            "proposed": e.get("verdict"),
            "proposed_family": e.get("family") or "",
            "model": m,
            "route": where,
            "reason": reason,
            "accepted": accepted,
        }
    reasons = Counter(v["reason"] for v in out_letters.values())
    out = {
        "schema_version": SCHEMA,
        "paper_key": key,
        "model": assist.model_id(STAGE),
        "date": date.today().isoformat(),
        "examples": [ex["paper"] for ex in examples],
        "letters": out_letters,
        "problems": problems,
        "summary": {
            "letters": len(out_letters),
            "auto": sum(1 for v in out_letters.values() if v["route"] == "auto"),
            "human": sum(1 for v in out_letters.values() if v["route"] == "human"),
            "reasons": dict(sorted(reasons.items())),
            "gated": sum(1 for v in out_letters.values() if v["model"].get("gated")),
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


def build_examples(
    labels: dict[str, dict[str, dict]],
    *,
    discovery_dir: Path = DISCOVERY,
    indices_dir: Path = INDICES,
) -> list[dict]:
    examples = []
    for key in sorted(labels):
        try:
            rec, idx = load_paper(key, discovery_dir=discovery_dir, indices_dir=indices_dir)
        except assist.AssistError:
            continue
        block = example_block(key, rec, idx, labels[key])
        if block["letters"]:
            examples.append(block)
    return examples


# --------------------------------------------------------------------------- #
# Evaluation — leave one paper out
# --------------------------------------------------------------------------- #


def rule_class(name: str, entry: dict) -> str:
    """The class a letter's proposal falls in; the unit of the reliability table."""
    v, rule, fam = entry.get("verdict"), entry.get("rule") or "?", entry.get("family") or ""
    if v in RULE_BINDS:
        cls = rule + ("+family" if fam else "-family")
        if name[:1].isupper():
            cls += "/UPPER"
        return cls
    return f"{v}/{rule}"


TALLY_KEYS = (
    "n",
    "rule_bind_ok",
    "model_bind_ok",
    "model_exact_ok",
    "model_unsure",
    "family_n",
    "rule_family_ok",
    "model_family_ok",
    "auto",
    "auto_ok",
    "human",
    "rule_wrong",
    "rule_wrong_caught",
)


def _bind_h(v: str) -> bool:
    return v == "index"


def _bind_r(v: str) -> bool:
    return v in RULE_BINDS


def evaluate(
    ws: assist.Workspace,
    labels: dict[str, dict[str, dict]],
    *,
    discovery_dir: Path = DISCOVERY,
    indices_dir: Path = INDICES,
    out_dir: Path = OUT_DIR / "loo",
    chat=None,
    force: bool = False,
) -> dict:
    """Leave-one-paper-out: every labelled paper is judged with the others as examples."""
    examples = build_examples(labels, discovery_dir=discovery_dir, indices_dir=indices_dir)
    per_letter: list[dict] = []
    per_paper: dict[str, dict] = {}
    model = None
    for key in sorted(labels):
        try:
            out = run_paper(
                ws,
                key,
                [ex for ex in examples if ex["paper"] != key],
                discovery_dir=discovery_dir,
                indices_dir=indices_dir,
                out_dir=out_dir,
                chat=chat,
                force=force,
            )
        except assist.AssistError as exc:
            per_paper[key] = {"error": str(exc)}
            continue
        model = out["model"]
        _, idx = load_paper(key, discovery_dir=discovery_dir, indices_dir=indices_dir)
        n = 0
        for name, h in sorted(labels[key].items()):
            e = (idx.get("letters") or {}).get(name)
            if e is None or h.get("verdict") == "unsure":
                continue
            r = out["letters"][name]
            per_letter.append(
                {
                    "paper": key,
                    "letter": name,
                    "class": rule_class(name, e),
                    "rule": e.get("verdict"),
                    "rule_family": e.get("family") or "",
                    "model": r["model"]["verdict"],
                    "model_family": r["model"].get("family") or "",
                    "human": h["verdict"],
                    "human_family": h.get("family") or "",
                    "route": r["route"],
                    "reason": r["reason"],
                }
            )
            n += 1
        per_paper[key] = {**out["summary"], "labelled": n, "examples": len(out["examples"])}

    def tally(items: list[dict]) -> dict:
        t: Counter = Counter({k: 0 for k in TALLY_KEYS})
        for x in items:
            hb, rb, mb = _bind_h(x["human"]), _bind_r(x["rule"]), _bind_h(x["model"])
            t["n"] += 1
            t["rule_bind_ok"] += hb == rb
            t["model_bind_ok"] += hb == mb
            t["model_exact_ok"] += x["human"] == x["model"]
            t["model_unsure"] += x["model"] == "unsure"
            if hb and x["human_family"]:
                t["family_n"] += 1
                t["rule_family_ok"] += x["rule_family"] == x["human_family"]
                t["model_family_ok"] += x["model_family"] == x["human_family"]
            if x["route"] == "auto":
                t["auto"] += 1
                ok = hb == rb
                if ok and hb and x["human_family"] and x["rule_family"]:
                    ok = x["rule_family"] == x["human_family"]
                t["auto_ok"] += ok
            else:
                t["human"] += 1
            if hb != rb:
                t["rule_wrong"] += 1
                t["rule_wrong_caught"] += x["route"] == "human"
        return dict(sorted(t.items()))

    classes = sorted({x["class"] for x in per_letter})
    return {
        "schema_version": SCHEMA,
        "date": date.today().isoformat(),
        "model": model,
        "papers": sorted(labels),
        "totals": tally(per_letter),
        "per_class": {c: tally([x for x in per_letter if x["class"] == c]) for c in classes},
        "per_paper": per_paper,
        "per_letter": per_letter,
    }


def _pct(a: int, b: int) -> str:
    return f"{a}/{b}" + (f" ({100 * a / b:.0f}%)" if b else "")


def report_markdown(report: dict) -> str:
    t = report["totals"]
    lines = [
        "# Index letters: the model as second annotator, leave-one-paper-out",
        "",
        f"Papers labelled: **{len(report['papers'])}**, letters with a human verdict (unsure excluded): "
        f"**{t.get('n', 0)}**. Model: `{report.get('model')}`. Date: {report['date']}.",
        "Every paper is judged with the other labelled papers as examples; the model never sees the rule's verdict.",
        "",
        "| | agrees with the human |",
        "|---|---:|",
        f"| rule, bind vs don't bind | {_pct(t.get('rule_bind_ok', 0), t.get('n', 0))} |",
        f"| model, bind vs don't bind | {_pct(t.get('model_bind_ok', 0), t.get('n', 0))} |",
        f"| model, exact verdict (index/label/not) | {_pct(t.get('model_exact_ok', 0), t.get('n', 0))} |",
        f"| rule, family (human said index and named one) | {_pct(t.get('rule_family_ok', 0), t.get('family_n', 0))} |",
        f"| model, family | {_pct(t.get('model_family_ok', 0), t.get('family_n', 0))} |",
        "",
        "## Routing (rule and model agree → accepted; else the human)",
        "",
        "| | count |",
        "|---|---:|",
        f"| accepted without the human | {_pct(t.get('auto', 0), t.get('n', 0))} |",
        f"| … of which right (bind, and family where both name one) | {_pct(t.get('auto_ok', 0), t.get('auto', 0))} |",
        f"| sent to the human | {_pct(t.get('human', 0), t.get('n', 0))} |",
        f"| rule errors caught by the routing | {_pct(t.get('rule_wrong_caught', 0), t.get('rule_wrong', 0))} |",
        f"| model unsure | {t.get('model_unsure', 0)} |",
        "",
        "## Per class",
        "",
        "| class | n | rule bind ok | model bind ok | model exact | family n | rule fam ok | model fam ok | auto | auto ok | human | rule errors caught |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for c, x in sorted(report["per_class"].items(), key=lambda kv: -kv[1].get("n", 0)):
        lines.append(
            f"| {c} | {x.get('n', 0)} | {x.get('rule_bind_ok', 0)} | {x.get('model_bind_ok', 0)} | {x.get('model_exact_ok', 0)} "
            f"| {x.get('family_n', 0)} | {x.get('rule_family_ok', 0)} | {x.get('model_family_ok', 0)} "
            f"| {x.get('auto', 0)} | {x.get('auto_ok', 0)} | {x.get('human', 0)} | {x.get('rule_wrong_caught', 0)}/{x.get('rule_wrong', 0)} |"
        )
    lines += [
        "",
        "## Per paper",
        "",
        "| paper | labelled | auto | human | gated | examples | api calls |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for key, s in sorted(report["per_paper"].items()):
        if "error" in s:
            lines.append(f"| {key} | | | | | | {s['error']} |")
            continue
        lines.append(
            f"| {key} | {s.get('labelled', 0)} | {s.get('auto', 0)} | {s.get('human', 0)} | {s.get('gated', 0)} | {s.get('examples', 0)} | {s.get('api_calls', 0)} |"
        )
    lines += [
        "",
        "## Every letter",
        "",
        "| paper | letter | class | rule → family | model → family | human → family | route |",
        "|---|---|---|---|---|---|---|",
    ]
    for x in report["per_letter"]:
        mark = "" if _bind_h(x["human"]) == _bind_h(x["model"]) else " ✗"
        lines.append(
            f"| {x['paper']} | `{x['letter']}` | {x['class']} | {x['rule']} → {x['rule_family'] or '–'} "
            f"| {x['model']} → {x['model_family'] or '–'}{mark} | {x['human']} → {x['human_family'] or '–'} | {x['route']} ({x['reason']}) |"
        )
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("keys", nargs="*")
    ap.add_argument(
        "--worklist", type=int, default=0, help="take the first N papers of the discovery worklist"
    )
    ap.add_argument(
        "--evaluate", action="store_true", help="leave-one-paper-out over the labelled papers"
    )
    ap.add_argument(
        "--labels",
        nargs="*",
        type=Path,
        help="exports / page-state files (default: decisions, inbox, state)",
    )
    ap.add_argument("--force", action="store_true", help="ignore the reply cache")
    args = ap.parse_args(argv)
    labels = load_labels(args.labels if args.labels else default_label_paths())
    ws = assist.Workspace()
    if args.evaluate:
        if not labels:
            ap.error("no labelled papers found")
        report = evaluate(ws, labels, force=args.force)
        REPORT_JSON.write_text(
            json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
            encoding="utf-8",
            newline="\n",
        )
        REPORT_MD.write_text(report_markdown(report), encoding="utf-8", newline="\n")
        t = report["totals"]
        print(
            f"{len(report['papers'])} papers, {t.get('n', 0)} letters: rule bind {t.get('rule_bind_ok', 0)}, "
            f"model bind {t.get('model_bind_ok', 0)}, auto {t.get('auto', 0)} (ok {t.get('auto_ok', 0)}), "
            f"human {t.get('human', 0)}, rule errors caught {t.get('rule_wrong_caught', 0)}/{t.get('rule_wrong', 0)} "
            f"-> {REPORT_MD}",
            file=sys.stderr,
        )
        return 0
    keys = list(args.keys) + (worklist_keys(args.worklist) if args.worklist else [])
    if not keys:
        ap.error("give paper keys, --worklist N or --evaluate")
    examples = build_examples(labels)
    for i, key in enumerate(keys, 1):
        try:
            out = run_paper(ws, key, examples, force=args.force)
        except assist.AssistError as exc:
            print(f"[{i}/{len(keys)}] {key} stopped: {exc}", file=sys.stderr)
            break
        s = out["summary"]
        print(
            f"[{i}/{len(keys)}] {key}: {s['letters']} letters, {s['auto']} accepted, {s['human']} for the human "
            f"({', '.join(f'{k} {v}' for k, v in s['reasons'].items())}), {s['gated']} gated, {s['api_calls']} calls",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
