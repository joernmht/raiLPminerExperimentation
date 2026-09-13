"""Vocabulary check: the names a paper's formulas use vs the names its sidecar declares.

The promotion gate refuses a paper by name at the FIRST symbol the canonical
grammar does not know. After the M1b rewrite rules (lp2graph
``rewrite-2026.09.0``) a formula that wrote ``t_{i}^{arr}`` uses a symbol
spelled ``t_arr``, and a sidecar that declares ``t`` only leaves that name
undeclared. This module lists, per paper and deterministically, every symbol
name the assembled candidate document uses AFTER normalization next to every
name the ``%@`` header declares. The difference is the fill-in list: a person
or the assist stage (rung c) declares exactly those names and nothing else.

Inputs (read only): ``corpus/promoted/<key>.tex`` (the candidate document that
``corpusbuilder.promote`` assembles for every paper with decisions: sidecar
header + accepted rows) — so the check sees precisely what the gate sees.

Outputs: ``corpus/vocab/<key>.json`` per paper, ``corpus/vocab.{json,md}`` as
the corpus-wide report, and ``corpus/declarations/<key>.vocab.tex`` suggestion
blocks (gitignored like the stubs; regenerable). No LLM anywhere.

Run::

    PYTHONPATH=. python3 -m corpusbuilder.vocab            # every promoted doc
    PYTHONPATH=. python3 -m corpusbuilder.vocab --only KEY
"""

# ruff: noqa: I001 — the ``railpminer._lp2graph`` import below is a *side effect*
# (it puts a sibling lp2graph checkout on ``sys.path``) and must run before the
# ``lp2graph`` imports.

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from railpminer import _lp2graph  # noqa: F401
from lp2graph.mining.ingest import normalize_latex
from lp2graph.mining.ingest.latex_normalizer import DocContext
from lp2graph.mining.versions import REWRITE_RULES_VERSION

from corpusbuilder.promote import CORPUS, DECLARATIONS, PROMOTED, _rel
from corpusbuilder.symbols import domain_declaration

VOCAB_DIR = CORPUS / "vocab"

#: Symbol occurrence: a ``\mathit{name}`` or plain identifier, an optional
#: subscript (braced or single character) and an optional superscript.
_TOKEN_RE = re.compile(
    # base: a plain name may carry folded suffixes (t_arr, Y_1) but never an
    # underscore that opens a braced subscript
    r"(?<![\\A-Za-z0-9_])(\\mathit\{[^{}]*\}|[A-Za-z][A-Za-z0-9]*(?:_(?!\{)[A-Za-z0-9]+)*)"
    r"(?:_(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}))?"
    r"(?:\^(\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|[A-Za-z0-9*+-]))?"
)
_ENV_RE = re.compile(r"\\(?:begin|end)\s*\{[^{}]*\}")
#: Scripts hanging off a COMMAND base (``\wp_{s}^{min}``, ``\right)^{2}``) are
#: not symbol occurrences; their contents must not leak as names.
_CMD_SCRIPTS_RE = re.compile(
    r"(\\[A-Za-z]+|\\right[)\]|])(?:\s*(?:_|\^)(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|[A-Za-z0-9*+-]))+"
)
#: Operator words written without a backslash (``min T``, ``s . t``) are row
#: prefixes the assembly did not strip, never symbols.
_OPERATOR_WORDS = frozenset(
    {
        "min",
        "max",
        "minimize",
        "maximize",
        "minimise",
        "maximise",
        "subject",
        "to",
        "st",
        "such",
        "that",
        "where",
        "for",
        "all",
        "and",
        "or",
        "if",
        "otherwise",
        "else",
    }
)
_DECL_RE = re.compile(r"^\s*%@\s*(index|param|var)\s+([A-Za-z_]\w*)(.*)$")
_SET_AFTER_IN_RE = re.compile(
    r"\\in\s*(?:\\mathcal\{([A-Za-z]\w*)\}|([A-Za-z][A-Za-z0-9]*(?:_(?!\{)[A-Za-z0-9]+)*)"
    r"|\\mathit\{([A-Za-z]\w*)\})"
)
_BIGOP_GROUP_RE = re.compile(
    r"\\(?:sum|prod|max|min|bigcup|bigcap)_\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}(?:\^\{[^{}]*\})?"
)
_TEXTLIKE_RE = re.compile(r"\\(?:tag|text|textrm|mathrm|mathtt|mathsf|operatorname)\s*\{[^{}]*\}")
_MATHCAL_RE = re.compile(r"\\mathcal\{[^{}]*\}")
_BODY_RE = re.compile(r"\\begin\{align\}(.*?)\\end\{align\}", re.DOTALL)
_TAG_RE = re.compile(r"\\tag\{([^{}]*)\}")
_NUMBER_RE = re.compile(r"^\d+$")


def _plain(name: str) -> str:
    m = re.fullmatch(r"\\mathit\{([^{}]*)\}", name)
    return (m.group(1) if m else name).replace("\\_", "_").replace(" ", "")


def _arity(sub: str | None) -> int:
    if not sub:
        return 0
    if not sub.startswith("{"):
        return 1
    inner = sub[1:-1]
    depth = 0
    n = 1
    for ch in inner:
        if ch in "{(":
            depth += 1
        elif ch in "})":
            depth -= 1
        elif ch == "," and depth == 0:
            n += 1
    return n if inner.strip() else 0


@dataclass
class Declared:
    indices: set[str] = field(default_factory=set)
    params: dict[str, int | None] = field(default_factory=dict)  # name -> shape arity
    variables: dict[str, int | None] = field(default_factory=dict)

    @classmethod
    def parse(cls, text: str) -> Declared:
        out = cls()
        for line in text.splitlines():
            m = _DECL_RE.match(line)
            if m is None:
                continue
            kind, name, rest = m.groups()
            if kind == "index":
                out.indices.add(name)
                continue
            sm = re.search(r"\bshape=(\S+)", rest)
            shape = sm.group(1) if sm else "-"
            arity = None if shape in ("-", "?") else len([s for s in shape.split(",") if s])
            (out.params if kind == "param" else out.variables)[name] = arity
        return out

    def names(self) -> set[str]:
        return self.indices | set(self.params) | set(self.variables)


@dataclass
class Use:
    name: str
    row: str
    arity: int
    role: str  # "coefficient" | "symbol"
    unresolved_script: str | None = None
    #: The subscript's index letters, offsets stripped (``i + 1`` -> ``i``).
    pieces: tuple[str, ...] = ()


_LETTER_FAMILY_RE = re.compile(
    r"(\\mathit\{[^{}]*\}|[A-Za-z]\w*)\s*\\in\s*"
    r"(?:\\mathcal\{([A-Za-z]\w*)\}|\\mathit\{([A-Za-z]\w*)\}|([A-Za-z][A-Za-z0-9]*(?:_(?!\{)[A-Za-z0-9]+)*))"
)


def _sub_pieces(sub: str | None) -> tuple[str, ...]:
    if not sub or not sub.startswith("{"):
        return (sub,) if sub else ()
    out: list[str] = []
    for piece in _split_top_commas_str(sub[1:-1]):
        q = piece.strip()
        m = re.fullmatch(r"(\\mathit\{[^{}]*\}|[A-Za-z]\w*)\s*[+-]\s*\d+", q)
        if m:
            q = m.group(1)
        if re.fullmatch(r"[A-Za-z](?:\s+[A-Za-z])+", q):
            out.extend(q.split())
        else:
            out.append(_plain(q))
    return tuple(out)


def _split_top_commas_str(inner: str) -> list[str]:
    out: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in inner:
        if ch in "{(":
            depth += 1
        elif ch in "})":
            depth -= 1
        if ch == "," and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return out


def doc_with_sidecar(promoted_text: str, sidecar_text: str) -> str:
    """The assembled document with its header replaced by the CURRENT sidecar
    (promote embeds the sidecar at assembly time; a later edit of the sidecar
    must be what the audit sees)."""
    body_at = promoted_text.find("\\begin{align}")
    head, body = (
        (promoted_text[:body_at], promoted_text[body_at:]) if body_at >= 0 else ("", promoted_text)
    )
    kept = [
        ln for ln in head.splitlines() if not re.match(r"\s*%@\s*(index|param|var|obj|con)\b", ln)
    ]
    decl = [ln for ln in sidecar_text.splitlines() if ln.strip().startswith("%@")]
    return "\n".join(kept + decl) + "\n" + body


def _rows(body: str) -> list[tuple[str, str]]:
    """Split an ``align`` body into (row name, row text) pairs."""
    rows: list[tuple[str, str]] = []
    for i, raw in enumerate(re.split(r"\\\\\s*\n", body)):
        text = raw.strip()
        if not text:
            continue
        tm = _TAG_RE.search(text)
        name = tm.group(1).replace("\\_", "_") if tm else f"row_{i + 1}"
        rows.append((name, text))
    return rows


def scan_document(text: str) -> dict:
    """Deterministic vocabulary audit of one candidate document."""
    normalized, _prov = normalize_latex(text, source="corpusbuilder.vocab")
    bm = _BODY_RE.search(normalized)
    body = bm.group(1) if bm else normalized
    ctx = DocContext.build(normalized, body)
    declared = Declared.parse(normalized)

    families: Counter[str] = Counter()
    for m in _SET_AFTER_IN_RE.finditer(body):
        fam = m.group(1) or m.group(2) or m.group(3)
        if fam and fam not in ctx.bound and not _NUMBER_RE.match(fam):
            families[fam] += 1
    for m in _MATHCAL_RE.finditer(body):
        families[_plain(m.group(0)[9:-1])] += 0

    uses: list[Use] = []
    domains: dict[str, str] = {}
    for row_name, row in _rows(body):
        for sym, dom in domain_declaration(row).items():
            domains.setdefault(_plain(sym), dom)
        scrub = _TEXTLIKE_RE.sub(" ", row)
        scrub = _ENV_RE.sub(" ", scrub)
        scrub = _BIGOP_GROUP_RE.sub(r" \\sum ", scrub)
        scrub = _CMD_SCRIPTS_RE.sub(r"\1 ", scrub)
        scrub = _MATHCAL_RE.sub(" ", scrub)
        # quantifier tails bind letters and name families, never symbols
        scrub = re.split(r"\\forall", scrub, maxsplit=1)[0]
        for m in _TOKEN_RE.finditer(scrub):
            name = _plain(m.group(1))
            if _NUMBER_RE.match(name) or name in ctx.bound or name in families:
                continue
            if name in _OPERATOR_WORDS:
                continue
            after = scrub[m.end() : m.end() + 8]
            role = "coefficient" if re.match(r"\s*\\cdot", after) else "symbol"
            uses.append(
                Use(
                    name=name,
                    row=row_name,
                    arity=_arity(m.group(2)),
                    role=role,
                    unresolved_script=("^" + m.group(3)) if m.group(3) else None,
                    pieces=_sub_pieces(m.group(2)),
                )
            )

    missing: dict[str, dict] = {}
    mismatched: list[dict] = []
    unresolved: list[dict] = []
    family_as_symbol: Counter[str] = Counter()
    used_names: set[str] = set()
    for u in uses:
        used_names.add(u.name)
        if u.unresolved_script:
            unresolved.append({"name": u.name, "row": u.row, "script": u.unresolved_script})
        if u.name in declared.params or u.name in declared.variables:
            shape = (
                declared.params.get(u.name)
                if u.name in declared.params
                else declared.variables.get(u.name)
            )
            if shape is not None and u.arity and u.arity != shape:
                mismatched.append(
                    {"name": u.name, "row": u.row, "written": u.arity, "declared": shape}
                )
            continue
        if u.name in declared.indices:
            family_as_symbol[u.name] += 1
            continue
        rec = missing.setdefault(
            u.name, {"rows": [], "roles": Counter(), "arities": Counter(), "kind": "?"}
        )
        if u.row not in rec["rows"]:
            rec["rows"].append(u.row)
        rec["roles"][u.role] += 1
        rec["arities"][u.arity] += 1

    for name, rec in missing.items():
        roles = rec["roles"]
        dom = domains.get(name)
        if dom:
            rec["kind"] = "var"
            rec["domain"] = dom
        elif roles and set(roles) == {"coefficient"}:
            rec["kind"] = "param"
        rec["roles"] = dict(sorted(roles.items()))
        rec["arities"] = {str(k): v for k, v in sorted(rec["arities"].items())}
        rec["evidence"] = (
            "domain row"
            if dom
            else ("always a coefficient" if rec["kind"] == "param" else "position only")
        )

    unused = sorted((set(declared.params) | set(declared.variables)) - used_names)
    missing_index = sorted(f for f in families if f not in declared.indices)
    letter_families: dict[str, set[str]] = {}
    for m in _LETTER_FAMILY_RE.finditer(body):
        fam = m.group(2) or m.group(3) or m.group(4)
        letter_families.setdefault(_plain(m.group(1)), set()).add(fam)
    shape_fixes = shape_repairs(uses, declared, letter_families)
    return {
        "shape_fixes": shape_fixes,
        "rewrite_rules_version": REWRITE_RULES_VERSION,
        "declared": {
            "index": sorted(declared.indices),
            "param": sorted(declared.params),
            "var": sorted(declared.variables),
        },
        "used": sorted(used_names),
        "bound": sorted(ctx.bound),
        "families": sorted(families),
        "missing": dict(sorted(missing.items())),
        "missing_index": missing_index,
        "shape_mismatch": mismatched,
        "unresolved_script": unresolved,
        "declared_unused": unused,
        "family_as_symbol": dict(sorted(family_as_symbol.items())),
    }


def shape_repairs(
    uses: list[Use], declared: Declared, letter_families: dict[str, set[str]]
) -> list[dict]:
    """Shapes the formulas decide: a declared param/var whose EVERY written
    use carries the same index letters, each bound to exactly one family in
    the document, gets that family tuple as its shape. Mixed arities, unbound
    letters or a letter with two families leave the declaration alone."""
    by_name: dict[str, set[tuple[str, ...]]] = {}
    for u in uses:
        if u.name in declared.params or u.name in declared.variables:
            by_name.setdefault(u.name, set()).add(u.pieces)
    fixes: list[dict] = []
    for name, spellings in sorted(by_name.items()):
        if len(spellings) != 1:
            continue
        (pieces,) = spellings
        declared_arity = (
            declared.params.get(name) if name in declared.params else declared.variables.get(name)
        )
        if len(pieces) == (declared_arity or 0):
            continue
        fams: list[str] = []
        for letter in pieces:
            options = letter_families.get(letter, set())
            if len(options) != 1:
                break
            fams.append(next(iter(options)))
        else:
            fixes.append(
                {
                    "name": name,
                    "kind": "param" if name in declared.params else "var",
                    "declared": declared_arity,
                    "written": list(pieces),
                    "shape": fams,
                    "new_index": [f for f in fams if f not in declared.indices],
                }
            )
    return fixes


def apply_shape_fixes(sidecar_path: Path, fixes: list[dict]) -> int:
    """Rewrite the sidecar's ``shape=`` values in place, each change announced
    by a comment line; families the formulas bind but the sidecar never
    declared are added. Returns the number of lines changed."""
    if not fixes:
        return 0
    text = sidecar_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    out: list[str] = []
    changed = 0
    by_name = {f["name"]: f for f in fixes}
    for ln in lines:
        m = re.match(r"^(\s*%@\s*(?:param|var)\s+)([A-Za-z_]\w*)(\s.*)$", ln)
        if m and m.group(2) in by_name and re.search(r"\bshape=\S+", m.group(3)):
            fix = by_name[m.group(2)]
            new_shape = ",".join(fix["shape"]) if fix["shape"] else "-"
            old = re.search(r"\bshape=(\S+)", m.group(3)).group(1)
            out.append(
                f"% shape fixed by corpusbuilder.vocab (deterministic: every use writes "
                f"{','.join(fix['written']) or 'no index'}): {fix['name']} shape={old} -> {new_shape}"
            )
            out.append(
                m.group(1)
                + m.group(2)
                + re.sub(r"\bshape=\S+", f"shape={new_shape}", m.group(3), count=1)
            )
            changed += 1
        else:
            out.append(ln)
    added: list[str] = []
    seen: set[str] = set()
    for fix in fixes:
        for fam in fix["new_index"]:
            if fam not in seen:
                seen.add(fam)
                added.append(
                    f"%@ index {fam} ordered=0 cyclic=0 :: family bound in the formulas (added by corpusbuilder.vocab)"
                )
    if added:
        out.append(
            "% --- index families the formulas bind (corpusbuilder.vocab, deterministic) ---"
        )
        out.extend(added)
    if changed or added:
        sidecar_path.write_text("\n".join(out) + "\n", encoding="utf-8", newline="\n")
    return changed + len(added)


def suggestion_block(key: str, audit: dict) -> str:
    """The fill-in list as ``%@`` lines a reviewer copies into the sidecar."""
    lines = [
        f"% Vocabulary suggestions for {key} — written by corpusbuilder.vocab",
        f"% (deterministic, regenerable; rewrite rules {audit['rewrite_rules_version']}).",
        "% Every name below is used by the normalized formulas but not declared in",
        f"% corpus/declarations/{key}.tex. Copy the lines you confirm into the sidecar;",
        "% a ? is for you (or the assist stage) to decide. Nothing else is missing.",
    ]
    for fam in audit["missing_index"]:
        lines.append(f"%@ index {fam} ordered=0 cyclic=0 :: set used in binders or quantifiers")
    for name, rec in audit["missing"].items():
        arities = ", ".join(f"{a} index" + ("" if a == "1" else "es") for a in rec["arities"])
        where = ", ".join(rec["rows"][:6]) + (" ..." if len(rec["rows"]) > 6 else "")
        note = f"{rec['evidence']}; written with {arities}; rows {where}"
        if rec["kind"] == "param":
            lines.append(f"%@ param {name} shape=? kind=? :: {note}")
        elif rec["kind"] == "var":
            lines.append(
                f"%@ var {name} shape=? domain={rec.get('domain', '?')} role=primary "
                f"drole=- lo=- hi=- :: {note}"
            )
        else:
            lines.append(f"%@ ? {name} :: param or var? {note}")
    for m in audit["shape_mismatch"]:
        lines.append(
            f"% shape: {m['name']} is declared with {m['declared']} index(es) but row "
            f"{m['row']} writes {m['written']}"
        )
    for u in audit["unresolved_script"]:
        lines.append(
            f"% unresolved script: {u['name']}{u['script']} in row {u['row']} "
            "(no rule could tell index from label; fix the row or declare the folded name)"
        )
    for name in audit["declared_unused"]:
        lines.append(f"% declared but never used after normalization: {name}")
    return "\n".join(lines) + "\n"


def check_all(
    *,
    promoted_dir: Path = PROMOTED,
    declarations_dir: Path = DECLARATIONS,
    out_dir: Path = VOCAB_DIR,
    only: set[str] | None = None,
    write: bool = True,
    fix_shapes: bool = False,
    progress=None,
) -> dict:
    """Audit every assembled candidate document; return the corpus report.

    ``fix_shapes`` applies :func:`shape_repairs` to the sidecars in place.
    """
    docs = sorted(p for p in promoted_dir.glob("*.tex") if not p.name.endswith(".stub.tex"))
    keys = [p.stem for p in docs if not only or p.stem in only]
    papers: dict[str, dict] = {}
    for i, key in enumerate(keys):
        if progress is not None:
            progress(i, len(keys), key)
        text = (promoted_dir / f"{key}.tex").read_text(encoding="utf-8")
        sidecar_path = declarations_dir / f"{key}.tex"
        if sidecar_path.exists():
            text = doc_with_sidecar(text, sidecar_path.read_text(encoding="utf-8"))
        audit = scan_document(text)
        audit["paper_key"] = key
        audit["sidecar"] = sidecar_path.exists()
        if fix_shapes and write and audit["sidecar"] and audit["shape_fixes"]:
            audit["shape_fixes_applied"] = apply_shape_fixes(sidecar_path, audit["shape_fixes"])
        papers[key] = audit
        if write:
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / f"{key}.json").write_text(
                json.dumps(audit, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            if audit["missing"] or audit["missing_index"] or audit["shape_mismatch"]:
                (declarations_dir / f"{key}.vocab.tex").write_text(
                    suggestion_block(key, audit), encoding="utf-8", newline="\n"
                )
    return build_report(papers)


def build_report(papers: dict[str, dict]) -> dict:
    n_missing = {k: len(a["missing"]) for k, a in papers.items()}
    kinds = Counter(rec["kind"] for a in papers.values() for rec in a["missing"].values())
    names = Counter(name for a in papers.values() for name in a["missing"])
    buckets = Counter(
        "0" if n == 0 else ("1-3" if n <= 3 else ("4-9" if n <= 9 else "10+"))
        for n in n_missing.values()
    )
    return {
        "schema_version": "vocab-1",
        "rewrite_rules_version": REWRITE_RULES_VERSION,
        "papers": len(papers),
        "papers_complete": sum(1 for n in n_missing.values() if n == 0),
        "missing_names_total": sum(n_missing.values()),
        "missing_by_bucket": {k: buckets.get(k, 0) for k in ("0", "1-3", "4-9", "10+")},
        "missing_kind_guess": {k: kinds.get(k, 0) for k in ("param", "var", "?")},
        "shape_mismatch_papers": sum(1 for a in papers.values() if a["shape_mismatch"]),
        "unresolved_script_papers": sum(1 for a in papers.values() if a["unresolved_script"]),
        "missing_index_papers": sum(1 for a in papers.values() if a["missing_index"]),
        "shape_fix_candidates": sum(len(a.get("shape_fixes", [])) for a in papers.values()),
        "shape_fixes_applied": sum(a.get("shape_fixes_applied", 0) for a in papers.values()),
        "most_common_missing": names.most_common(20),
        "per_paper": [
            {
                "paper_key": k,
                "missing": n_missing[k],
                "missing_index": len(a["missing_index"]),
                "shape_mismatch": len(a["shape_mismatch"]),
                "unresolved_script": len(a["unresolved_script"]),
                "declared_unused": len(a["declared_unused"]),
            }
            for k, a in sorted(papers.items(), key=lambda kv: (n_missing[kv[0]], kv[0]))
        ],
    }


def render_report_md(report: dict) -> str:
    b = report["missing_by_bucket"]
    g = report["missing_kind_guess"]
    lines = [
        "# Vocabulary report",
        "",
        "Names the formulas use after normalization vs names the sidecars declare;",
        "generated by `corpusbuilder.vocab`. Regenerate with",
        "`PYTHONPATH=. python3 -m corpusbuilder.vocab`; do not edit by hand.",
        "",
        f"- papers checked: **{report['papers']}** (rewrite rules `{report['rewrite_rules_version']}`)",
        f"- papers whose vocabulary is complete: **{report['papers_complete']}**",
        f"- missing names in total: **{report['missing_names_total']}**",
        f"- papers by missing names: 0: {b['0']} · 1-3: {b['1-3']} · 4-9: {b['4-9']} · 10+: {b['10+']}",
        f"- kind decidable from position: param {g['param']} · var {g['var']} · undecided {g['?']}",
        f"- papers with a shape mismatch: {report['shape_mismatch_papers']}"
        f" · with an unresolved script: {report['unresolved_script_papers']}"
        f" · with an undeclared index family: {report['missing_index_papers']}",
        f"- shapes the formulas decide: {report['shape_fix_candidates']} candidates"
        f" · applied to sidecars this run: {report['shape_fixes_applied']}",
        "",
        "## Most common missing names",
        "",
        "| name | papers |",
        "| --- | ---: |",
    ]
    lines += [f"| `{n}` | {c} |" for n, c in report["most_common_missing"]]
    lines += [
        "",
        "## Papers (least work first)",
        "",
        "| paper | missing | undeclared families | shape mismatches | unresolved scripts | declared unused |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for p in report["per_paper"]:
        lines.append(
            f"| {p['paper_key']} | {p['missing']} | {p['missing_index']} | {p['shape_mismatch']} "
            f"| {p['unresolved_script']} | {p['declared_unused']} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m corpusbuilder.vocab",
        description="List, per paper, the symbol names the formulas use vs the names declared.",
    )
    parser.add_argument("--promoted", type=Path, default=PROMOTED, help="assembled docs dir")
    parser.add_argument("--declarations", type=Path, default=DECLARATIONS, help="sidecar dir")
    parser.add_argument("--out", type=Path, default=VOCAB_DIR, help="per-paper output dir")
    parser.add_argument("--only", action="append", default=[], metavar="PAPER_KEY")
    parser.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    parser.add_argument(
        "--fix-shapes",
        action="store_true",
        help="rewrite sidecar shapes the formulas decide (every use writes the same bound "
        "letters); each change is announced by a comment line in the sidecar",
    )
    args = parser.parse_args(argv)

    from corpusbuilder import factory

    with factory.running("vocab") as beat:
        report = check_all(
            promoted_dir=args.promoted,
            declarations_dir=args.declarations,
            out_dir=args.out,
            only=set(args.only) or None,
            write=not args.dry_run,
            fix_shapes=args.fix_shapes,
            progress=lambda done, total, key: beat(done=done, total=total, note=key),
        )
    if not args.dry_run:
        (CORPUS / "vocab.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        (CORPUS / "vocab.md").write_text(render_report_md(report), encoding="utf-8", newline="\n")
    b = report["missing_by_bucket"]
    print(
        f"papers {report['papers']} · complete {report['papers_complete']} · missing names "
        f"{report['missing_names_total']} (0: {b['0']}, 1-3: {b['1-3']}, 4-9: {b['4-9']}, "
        f"10+: {b['10+']})"
    )
    if not args.dry_run:
        print(
            f"wrote {_rel(CORPUS / 'vocab.json')}, {_rel(CORPUS / 'vocab.md')}, {_rel(args.out)}/"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
