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

from corpusbuilder.promote import CORPUS, DECLARATIONS, DOSSIERS, PROMOTED, _rel
from corpusbuilder.symbols import domain_declaration

VOCAB_DIR = CORPUS / "vocab"
GOLD_PATH = CORPUS / "vocab_gold.json"
GOLD_LABELS_PATH = CORPUS / "vocab_gold_labels.json"
PROSE_DIR = CORPUS / "prose"

#: A human's "this name is not a symbol" verdict, kept in the sidecar so the
#: audit stops listing the name and the provenance shows who decided.
_NOT_SYMBOL_RE = re.compile(r"^\s*%\s*not a symbol \(human\):\s*([A-Za-z_]\w*)")
VAR_DOMAINS = ("binary", "integer", "non_negative", "continuous")
PARAM_KINDS = ("scalar", "vector", "matrix", "big_m", "tolerance")
VAR_ROLES = ("primary", "auxiliary", "slack", "indicator")

#: Provenance of a sidecar's ``%@`` lines, decided by the nearest preceding
#: marker comment. A sidecar written by the assist stage opens with the
#: ``ASSISTED RESOLUTION`` header (rung c); later blocks announce themselves.
SOURCE_MARKERS: tuple[tuple[str, str], ...] = (
    ("ASSISTED RESOLUTION", "assist-c"),
    ("vocabulary fill (stage v", "assist-v"),
    ("vocabulary (human", "human"),
    ("index families the formulas bind", "deterministic"),
)

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
    decl = [
        ln
        for ln in sidecar_text.splitlines()
        if ln.strip().startswith("%@") or _NOT_SYMBOL_RE.match(ln)
    ]
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
    not_symbols = sorted(
        {m.group(1) for m in (_NOT_SYMBOL_RE.match(ln) for ln in text.splitlines()) if m}
    )
    for name in not_symbols:
        missing.pop(name, None)
    return {
        "shape_fixes": shape_fixes,
        "not_symbols": not_symbols,
        "letter_families": {k: sorted(v) for k, v in sorted(letter_families.items())},
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


def classify_sidecar_lines(text: str) -> list[tuple[str, str, str]]:
    """``(record, name, source)`` for every index/param/var line of a sidecar.

    Sources: ``assist-c`` (the rung-c fill), ``assist-v`` (stage V), ``human``
    (a human block), ``deterministic`` (families added by this module),
    ``unknown`` (no marker before the line, e.g. a hand-written sidecar).
    A ``shape fixed by corpusbuilder.vocab`` note keeps the line's source; the
    shape repair is counted separately by :func:`sidecar_source_counts`.
    """
    source = "unknown"
    out: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("%") and not line.startswith("%@"):
            for needle, src in SOURCE_MARKERS:
                if needle in line:
                    source = src
                    break
            continue
        m = _DECL_RE.match(line)
        if m:
            out.append((m.group(1), m.group(2), source))
    return out


def sidecar_source_counts(text: str) -> dict[str, int]:
    counts: Counter[str] = Counter(src for _rec, _name, src in classify_sidecar_lines(text))
    counts["shape_fixed"] = sum(
        1 for ln in text.splitlines() if "shape fixed by corpusbuilder.vocab" in ln
    )
    return dict(sorted(counts.items()))


def proposals(sidecar_text: str) -> list[dict]:
    """The model-proposed declarations of a sidecar (assist-c and assist-v lines)
    as records a reviewer can confirm, fix or reject."""
    out: list[dict] = []
    for record, name, source in classify_sidecar_lines(sidecar_text):
        if not source.startswith("assist"):
            continue
        for raw in sidecar_text.splitlines():
            line = raw.strip()
            m = _DECL_RE.match(line)
            if m and m.group(1) == record and m.group(2) == name:
                kv = dict(
                    tok.split("=", 1) for tok in line.split("::", 1)[0].split()[2:] if "=" in tok
                )
                out.append(
                    {
                        "name": name,
                        "kind": record,
                        "shape": [f for f in kv.get("shape", "-").split(",") if f and f != "-"],
                        "domain": kv.get("domain", "-"),
                        "pkind": kv.get("kind", "-"),
                        "role": kv.get("role", "-"),
                        "desc": line.split("::", 1)[1].strip() if "::" in line else "",
                        "source": source,
                        "line": line,
                    }
                )
                break
    return out


def _rows_for_names(text: str) -> dict[str, str]:
    """Normalized row text by row name for one candidate document."""
    normalized, _prov = normalize_latex(text, source="corpusbuilder.vocab")
    bm = _BODY_RE.search(normalized)
    return {name: row_display_text(row) for name, row in _rows(bm.group(1) if bm else normalized)}


def row_display_text(row: str) -> str:
    """One align row as displayable TeX: no ``\\tag``, no alignment ``&``, no
    trailing ``\\\\``; the ``\\min\\quad &`` objective head becomes ``\\min``.
    (A blanket strip of ``\\`` used to eat the backslash of a leading
    ``\\left(`` and left the ``&`` in place: 27 of 247 gold rows failed to
    render.)"""
    text = _TAG_RE.sub("", row)
    text = re.sub(r"\\\\\s*$", "", text.strip())
    text = re.sub(r"\\quad\s*&", " ", text)
    text = text.replace("&", " ")
    return re.sub(r"\s{2,}", " ", text).strip()


def _paper_mentions(
    prose: dict | None, dossier_path: Path, row_names: list[str], name: str
) -> tuple[list[dict], list[dict]]:
    """The paper's own words about a symbol: notation-list entries that
    mention it, and the paragraphs around the formulas it occurs in (the
    formula's printed label links a row to its prose neighbourhood)."""
    if not prose:
        return [], []
    definitions: list[dict] = []
    base = name.split("_", 1)[0]
    for entry in prose.get("deflists") or []:
        if not isinstance(entry, dict):
            continue
        term = str(entry.get("term") or "")
        if re.search(rf"(?<![A-Za-z]){re.escape(base)}(?![A-Za-z])", term):
            definitions.append({"term": term[:120], "def": str(entry.get("def") or "")[:400]})
        if len(definitions) >= 4:
            break
    labels: set[str] = set()
    if dossier_path.exists():
        try:
            formulas = json.loads(dossier_path.read_text(encoding="utf-8")).get("formulas") or []
        except (OSError, json.JSONDecodeError):
            formulas = []
        wanted = {re.sub(r"_[a-z]$", "", r) for r in row_names}
        for f in formulas:
            fid = re.sub(r"[^A-Za-z0-9_]+", "_", str(f.get("id") or "")).strip("_")
            if fid in wanted and f.get("label"):
                labels.add(str(f["label"]))
    paras = [q for q in prose.get("paras") or [] if isinstance(q, dict) and q.get("text")]
    linked = [
        pos
        for pos, q in enumerate(paras)
        if labels & {str(lb) for lb in q.get("formula_labels") or []}
    ]
    mentions: list[dict] = []
    seen: set[int] = set()
    for pos in linked:
        for k in (pos, pos + 1, pos - 1):
            if 0 <= k < len(paras) and k not in seen:
                seen.add(k)
                q = paras[k]
                mentions.append(
                    {
                        "labels": [str(lb) for lb in q.get("formula_labels") or []][:4],
                        "text": str(q["text"])[:900],
                    }
                )
        if len(mentions) >= 5:
            break
    return definitions, mentions[:5]


def gold_sample(
    *,
    n: int = 100,
    seed: int = 20260913,
    vocab_dir: Path = VOCAB_DIR,
    promoted_dir: Path = PROMOTED,
    declarations_dir: Path = DECLARATIONS,
    prose_dir: Path = PROSE_DIR,
    dossiers_dir: Path = DOSSIERS,
) -> dict:
    """A fixed-seed random sample of missing names for BLIND human labelling.

    Every item carries what a labeller needs and nothing a proposal could
    anchor on: the rows the name occurs in (normalized), the paper's declared
    index families, and the abstract when a prose digest exists locally.
    """
    import random

    pool: list[tuple[str, str]] = []
    for path in sorted(vocab_dir.glob("*.json")):
        audit = json.loads(path.read_text(encoding="utf-8"))
        for name in sorted(audit.get("missing", {})):
            pool.append((audit["paper_key"], name))
    rng = random.Random(seed)
    chosen = sorted(rng.sample(pool, min(n, len(pool))))
    items: list[dict] = []
    rows_cache: dict[str, dict[str, str]] = {}
    for key, name in chosen:
        audit = json.loads((vocab_dir / f"{key}.json").read_text(encoding="utf-8"))
        rec = audit["missing"][name]
        if key not in rows_cache:
            doc = (promoted_dir / f"{key}.tex").read_text(encoding="utf-8")
            sidecar = declarations_dir / f"{key}.tex"
            if sidecar.exists():
                doc = doc_with_sidecar(doc, sidecar.read_text(encoding="utf-8"))
            rows_cache[key] = _rows_for_names(doc)
        prose_path = prose_dir / f"{key}.json"
        prose: dict | None = None
        if prose_path.exists():
            try:
                loaded = json.loads(prose_path.read_text(encoding="utf-8"))
                prose = loaded if isinstance(loaded, dict) else None
            except (OSError, json.JSONDecodeError):
                prose = None
        abstract = str((prose or {}).get("abstract") or "")
        definitions, mentions = _paper_mentions(
            prose, dossiers_dir / f"{key}.json", rec["rows"][:6], name
        )
        items.append(
            {
                "id": f"{key}::{name}",
                "paper_key": key,
                "name": name,
                "evidence": rec["evidence"],
                "kind_guess_hidden": rec["kind"],
                "arities": rec["arities"],
                "rows": [{"name": r, "latex": rows_cache[key].get(r, "")} for r in rec["rows"][:6]],
                "families": audit["declared"]["index"],
                "abstract": abstract[:2000],
                "definitions": definitions,
                "mentions": mentions,
            }
        )
    return {
        "schema_version": "vocab-gold-1",
        "seed": seed,
        "n": len(items),
        "pool": len(pool),
        "rewrite_rules_version": REWRITE_RULES_VERSION,
        "items": items,
    }


def decision_line(d: dict) -> str | None:
    """The ``%@`` line a human decision denotes (``None`` for non-declarations)."""
    if d.get("verdict") != "declare":
        return None
    name = d["name"]
    shape = ",".join(d.get("shape") or []) or "-"
    desc = (d.get("desc") or "").strip().replace("\n", " ")
    kind = d.get("kind")
    if kind == "index":
        return f"%@ index {name} ordered=0 cyclic=0 :: {desc}"
    if kind == "param":
        pkind = d.get("pkind") or "scalar"
        return f"%@ param {name} shape={shape} kind={pkind} domain=- :: {desc}"
    if kind == "var":
        domain = d.get("domain") or "continuous"
        role = d.get("role") or "primary"
        return (
            f"%@ var {name} shape={shape} domain={domain} role={role} drole=- lo=- hi=- :: {desc}"
        )
    return None


def validate_decision(d: dict) -> list[str]:
    errors: list[str] = []
    if d.get("verdict") not in ("declare", "not_a_symbol", "skip"):
        errors.append("verdict must be declare | not_a_symbol | skip")
    if not re.fullmatch(r"[A-Za-z_]\w*", str(d.get("name", ""))):
        errors.append("name is not an identifier")
    if d.get("verdict") == "declare":
        kind = d.get("kind")
        if kind not in ("index", "param", "var"):
            errors.append("kind must be index | param | var")
        if kind == "var" and d.get("domain") not in VAR_DOMAINS:
            errors.append(f"var domain must be one of {VAR_DOMAINS}")
        if kind == "param" and (d.get("pkind") or "scalar") not in PARAM_KINDS:
            errors.append(f"param kind must be one of {PARAM_KINDS}")
        if kind == "var" and (d.get("role") or "primary") not in VAR_ROLES:
            errors.append(f"var role must be one of {VAR_ROLES}")
        for fam in d.get("shape") or []:
            if not re.fullmatch(r"[A-Za-z_]\w*", str(fam)):
                errors.append(f"shape family {fam!r} is not an identifier")
    return errors


def apply_decisions(
    export: dict,
    *,
    declarations_dir: Path = DECLARATIONS,
    gold_labels_path: Path = GOLD_LABELS_PATH,
    today: str | None = None,
) -> dict:
    """Write a ``vocab-decisions-1`` export into the sidecars.

    Every declaration lands under a dated ``% --- vocabulary (human, MODE,
    DATE) ---`` block; a proposal the human superseded is commented out where
    it stood, so the sidecar shows both the model's line and the person's.
    ``not_a_symbol`` verdicts are kept as comments the audit honours. Blind
    decisions are additionally recorded in ``corpus/vocab_gold_labels.json``
    (merged by id) for the agreement measurement.
    """
    from datetime import date

    if export.get("schema_version") != "vocab-decisions-1":
        raise ValueError("expected a vocab-decisions-1 export")
    mode = str(export.get("mode") or "confirm")
    today = today or date.today().isoformat()
    report: dict = {
        "mode": mode,
        "papers": 0,
        "declared": 0,
        "not_a_symbol": 0,
        "skipped": 0,
        "superseded": 0,
        "families_added": 0,
        "invalid": [],
        "no_sidecar": [],
    }
    by_paper: dict[str, list[dict]] = {}
    for d in export.get("decisions", []):
        errors = validate_decision(d)
        if errors:
            report["invalid"].append({"id": d.get("id"), "errors": errors})
            continue
        by_paper.setdefault(str(d["paper_key"]), []).append(d)
    for key, decisions in sorted(by_paper.items()):
        sidecar_path = declarations_dir / f"{key}.tex"
        if not sidecar_path.exists():
            report["no_sidecar"].append(key)
            continue
        text = sidecar_path.read_text(encoding="utf-8")
        lines = text.splitlines()
        block: list[str] = [f"% --- vocabulary (human, {mode}, {today}) ---"]
        declared_now: set[str] = set()
        declared_families = {n for r, n, _s in classify_sidecar_lines(text) if r == "index"}
        for d in sorted(decisions, key=lambda x: str(x["name"])):
            name = str(d["name"])
            if d["verdict"] == "skip":
                report["skipped"] += 1
                continue
            # supersede any earlier declaration of the name (model or human)
            for i, ln in enumerate(lines):
                m = _DECL_RE.match(ln)
                if m and m.group(2) == name and m.group(1) in ("index", "param", "var"):
                    lines[i] = f"% superseded by human decision ({today}): {ln.strip()}"
                    report["superseded"] += 1
            if d["verdict"] == "not_a_symbol":
                block.append(f"% not a symbol (human): {name}")
                report["not_a_symbol"] += 1
                continue
            line = decision_line(d)
            if line is None:
                continue
            if d.get("proposal") and d.get("changed"):
                block.append(f"% changed from proposal: {d['proposal'].get('line', '')}")
            block.append(line)
            declared_now.add(name)
            report["declared"] += 1
            for fam in d.get("shape") or []:
                if fam not in declared_families and fam not in declared_now:
                    block.append(
                        f"%@ index {fam} ordered=0 cyclic=0 :: family used in a human-decided shape"
                    )
                    declared_families.add(fam)
                    report["families_added"] += 1
        if len(block) > 1:
            sidecar_path.write_text(
                "\n".join(lines).rstrip() + "\n" + "\n".join(block) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            report["papers"] += 1
    if mode == "blind":
        labels: dict = {"schema_version": "vocab-gold-labels-1", "labels": {}}
        if gold_labels_path.exists():
            labels = json.loads(gold_labels_path.read_text(encoding="utf-8"))
        for d in export.get("decisions", []):
            if not validate_decision(d):
                labels["labels"][str(d["id"])] = {
                    k: d.get(k)
                    for k in (
                        "paper_key",
                        "name",
                        "verdict",
                        "kind",
                        "shape",
                        "domain",
                        "pkind",
                        "role",
                    )
                }
        gold_labels_path.write_text(
            json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        report["gold_labels_total"] = len(labels["labels"])
    return report


def agreement(
    *, gold_labels_path: Path = GOLD_LABELS_PATH, declarations_dir: Path = DECLARATIONS
) -> dict:
    """Blind human labels vs the model's declarations for the same names.

    A name the model never declared counts as the model saying "not a
    symbol"; kind agreement is scored over index / param / var /
    not_a_symbol with Cohen's kappa, shape and domain agreement over the
    pairs where both declared the same kind.
    """
    labels = json.loads(gold_labels_path.read_text(encoding="utf-8"))["labels"]
    pairs: list[tuple[str, str]] = []
    shape_ok = shape_n = domain_ok = domain_n = 0
    no_proposal = 0
    props_cache: dict[str, dict[str, dict]] = {}
    for _id, lab in sorted(labels.items()):
        if lab["verdict"] == "skip":
            continue
        key = lab["paper_key"]
        if key not in props_cache:
            path = declarations_dir / f"{key}.tex"
            text = path.read_text(encoding="utf-8") if path.exists() else ""
            props_cache[key] = {p["name"]: p for p in proposals(text)}
        prop = props_cache[key].get(lab["name"])
        human = lab["kind"] if lab["verdict"] == "declare" else "not_a_symbol"
        model = prop["kind"] if prop else "not_a_symbol"
        if not prop:
            no_proposal += 1
        pairs.append((human, model))
        if prop and human == model and human in ("param", "var"):
            shape_n += 1
            shape_ok += int((lab.get("shape") or []) == prop["shape"])
            if human == "var":
                domain_n += 1
                domain_ok += int(lab.get("domain") == prop["domain"])
    n = len(pairs)
    agree = sum(1 for h, m in pairs if h == m)
    cats = ("index", "param", "var", "not_a_symbol")
    ph = {c: sum(1 for h, _ in pairs if h == c) / n for c in cats} if n else {}
    pm = {c: sum(1 for _, m in pairs if m == c) / n for c in cats} if n else {}
    pe = sum(ph[c] * pm[c] for c in cats) if n else 0.0
    po = agree / n if n else 0.0
    kappa = (po - pe) / (1 - pe) if n and pe < 1 else 0.0
    confusion = {
        h: {m: sum(1 for hh, mm in pairs if hh == h and mm == m) for m in cats} for h in cats
    }
    return {
        "schema_version": "vocab-agreement-1",
        "n": n,
        "no_proposal": no_proposal,
        "kind_agreement": round(po, 4),
        "kind_kappa": round(kappa, 4),
        "shape_agreement": round(shape_ok / shape_n, 4) if shape_n else None,
        "shape_n": shape_n,
        "domain_agreement": round(domain_ok / domain_n, 4) if domain_n else None,
        "domain_n": domain_n,
        "confusion_human_rows_model_cols": confusion,
    }


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
        audit["declared_by_source"] = (
            sidecar_source_counts(sidecar_path.read_text(encoding="utf-8"))
            if sidecar_path.exists()
            else {}
        )
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
        "declared_by_source": dict(
            sorted(
                sum(
                    (Counter(a.get("declared_by_source", {})) for a in papers.values()),
                    Counter(),
                ).items()
            )
        ),
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
        "- declarations by source: "
        + " · ".join(f"{k} {v}" for k, v in report["declared_by_source"].items()),
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
        "--gold",
        type=int,
        default=None,
        metavar="N",
        help="draw a fixed-seed sample of N missing names for BLIND human labelling "
        "into corpus/vocab_gold.json (rows + families + abstract; gitignored) and exit",
    )
    parser.add_argument("--seed", type=int, default=20260913, help="seed for --gold")
    parser.add_argument(
        "--apply-decisions",
        type=Path,
        default=None,
        metavar="FILE",
        help="write a vocab-decisions-1 export (the vocabulary round's output) into the "
        "sidecars under a dated human block and exit",
    )
    parser.add_argument(
        "--agreement",
        action="store_true",
        help="score the blind gold labels against the model's declarations and exit",
    )
    parser.add_argument(
        "--fix-shapes",
        action="store_true",
        help="rewrite sidecar shapes the formulas decide (every use writes the same bound "
        "letters); each change is announced by a comment line in the sidecar",
    )
    args = parser.parse_args(argv)

    from corpusbuilder import factory

    if args.apply_decisions is not None:
        report = apply_decisions(
            json.loads(args.apply_decisions.read_text(encoding="utf-8")),
            declarations_dir=args.declarations,
        )
        print(json.dumps(report, indent=1, ensure_ascii=False))
        return 0
    if args.agreement:
        result = agreement(declarations_dir=args.declarations)
        (CORPUS / "vocab_agreement.json").write_text(
            json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(json.dumps(result, indent=1))
        return 0
    if args.gold is not None:
        gold = gold_sample(n=args.gold, seed=args.seed)
        GOLD_PATH.write_text(
            json.dumps(gold, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
        )
        print(
            f"gold sample: {gold['n']} of {gold['pool']} missing names (seed {gold['seed']}) "
            f"-> {_rel(GOLD_PATH)}"
        )
        return 0

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
