"""Deterministic multi-formula detection & splitting for Tier-2 extractions.

One dossier formula record sometimes holds SEVERAL formulas glued together:
stacked constraint rows in a ``matrix``/``aligned`` environment, inline
``min ... s.t. ...`` model blocks, or comma-joined equation lists.  This
module decides — with pure string analysis, no RNG, no model calls — whether
a record should be split and where, so the review game can pre-fill its
multi-part fix sheet instead of making the reviewer cut by hand.

The hard part is NOT splitting look-alikes, all of which occur in the
corpus (see ``corpus/testsets/formula_split_labels.json``):

* chained relations               ``A <= B <= C``  (also across env rows)
* quantifier tails                ``..., \\forall i \\in I, k < s, j = 1,...,N``
* tail-context condition rows     index definitions after the main relation
* piecewise definitions           ``X = \\{ expr if c; 0 otherwise \\}``
* guarded rows                    trailing ``if``/condition cells
* real vectors/matrices           ``pmatrix``/``bmatrix`` content
* model refs                      ``min Z  s.t. constraints (1)-(35)``
* continuation rows               rows starting with ``+``/``=``/relations

Entry point: :func:`split_latex` -> :class:`SplitResult`.
``parts`` has length 1 when the record is a single formula.  ``confident``
is False when multiple statements were detected but could not be cleanly
separated (flat glued blobs) — the UI should flag those for manual cutting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# Detection-normalized copy with an index map back into the raw string.
# Removes text-wrapper commands and braces and collapses MathML
# spaced-single-letter noise ("s . t .", "o t h e r w i s e") so keyword
# regexes hit, while every normalized index still points at a raw index.
# --------------------------------------------------------------------------

_TEXT_CMD = re.compile(r"\\text(?:rm|sf|tt|normal|it|bf)?\s*\{|\\mathrm\s*\{|\\mathit\s*\{")


def _norm_map(s: str) -> tuple[str, list[int]]:
    out: list[str] = []
    idx: list[int] = []
    i = 0
    while i < len(s):
        m = _TEXT_CMD.match(s, i)
        if m:
            i = m.end()
            continue
        c = s[i]
        if c in "{}":
            c = " "  # keep a separator so tokens don't glue ("{max}" -> " max ")
        out.append(c)
        idx.append(i)
        i += 1
    norm = "".join(out)
    # collapse runs of single letters separated by single spaces (>=2 gaps)
    pat = re.compile(r"(?<![A-Za-z\\])([A-Za-z](?: [A-Za-z]){2,})(?![A-Za-z])")
    while True:
        m = pat.search(norm)
        if not m:
            return norm, idx
        a, b = m.span(1)
        keep = [j for j in range(a, b) if norm[j] != " "]
        norm = norm[:a] + "".join(norm[j] for j in keep) + norm[b:]
        idx = idx[:a] + [idx[j] for j in keep] + idx[b:]


_ST_MARKER = re.compile(
    r"\bs\s*\.\s*t\s*\.?|subject\s{0,3}to\b|Subject\s{0,3}[Tt]o\b", re.IGNORECASE
)
_MINMAX = re.compile(r"(?<![a-zA-Z\\])\\?(?:min|max|minimi[zs]e|maximi[zs]e|Min|Max)(?![a-zA-Z])")

# --------------------------------------------------------------------------
# Top-level tokenization: depth from braces, parens, \left/\right and
# environments.  Environments named here are *display* environments whose
# rows may be separate formulas; anything else (incl. pmatrix/bmatrix used
# as real matrices) is opaque.
# --------------------------------------------------------------------------

_SPLITTABLE_ENVS = frozenset(
    {"matrix", "aligned", "align", "align*", "gathered", "gather", "gather*",
     "split", "eqnarray", "eqnarray*", "array", "cases", "Bmatrix"}
)
_ENV_TOKEN = re.compile(r"\\(begin|end)\s*\{([a-zA-Z*]+)\}")
_LR = re.compile(
    r"\\(left|right)(?![a-zA-Z])\s*"
    r"(\\(?:l?[Vv]ert|[lr]angle|[lr]ceil|[lr]floor|[lr]brace|[lr]brack|backslash)"
    r"|\\[{}.|]|[^\sA-Za-z\\])?"
)


def _scan(s: str):
    """Yield ``(pos, kind, payload)`` events at nesting depth 0.

    kinds: ``char`` (payload=character), ``env`` (payload=(name, end_pos) —
    the whole environment span, reported as one opaque/structured token).
    """
    depth = 0
    i = 0
    n = len(s)
    while i < n:
        m = _ENV_TOKEN.match(s, i)
        if m and m.group(1) == "begin":
            # find matching \end (any env nesting counts)
            j = m.end()
            lvl = 1
            while j < n and lvl:
                m2 = _ENV_TOKEN.search(s, j)
                if not m2:
                    j = n
                    break
                lvl += 1 if m2.group(1) == "begin" else -1
                j = m2.end()
            if depth == 0:
                yield (i, "env", (m.group(2), j))
            i = j
            continue
        if m:  # stray \end
            i = m.end()
            continue
        m = _LR.match(s, i)
        if m:
            if m.group(1) == "left":
                depth += 1
            else:
                depth = max(0, depth - 1)
            i = m.end()
            continue
        c = s[i]
        if c == "\\" and i + 1 < n and s[i + 1] in "{}":
            depth += 1 if s[i + 1] == "{" else -1
            depth = max(0, depth)
            i += 2
            continue
        if c in "{([":
            depth += 1
        elif c in ")]}":
            depth = max(0, depth - 1)
        elif depth == 0:
            yield (i, "char", c)
        i += 1


def _top_spans(s: str) -> list[tuple[int, str]]:
    return [(i, c) for i, k, c in ((i, k, p) for i, k, p in _scan(s)) if k == "char"]


# relations at top level.  ``<-``/arrows count as assignment relations.
_REL_CMD = re.compile(
    r"\\(?:leq?|geq?|neq?|in|notin|ni|subseteq?|supseteq?|preceq|succeq|prec|succ"
    r"|leftarrow|gets|ll|gg)(?![a-zA-Z])"
)
_REL_CHARS = "=<>≤≥≠∈∉⊆⊇⪯⪰≺≻←∊"
_COMPARE_CHARS = "<>≤≥≠"


def _top_relations(s: str) -> list[tuple[int, str]]:
    """(position, relation-token) for every top-level relation in ``s``."""
    rels: list[tuple[int, str]] = []
    tops = dict(_top_spans(s))
    for m in _REL_CMD.finditer(s):
        if m.start() in tops and tops[m.start()] == "\\":
            rels.append((m.start(), m.group(0)))
    for i, c in tops.items():
        if c in _REL_CHARS:
            rels.append((i, c))
    return sorted(rels)


# --------------------------------------------------------------------------
# Segment / row classification
# --------------------------------------------------------------------------

_QUANT_LEAD = re.compile(
    r"^\s*(?:\\forall\b|∀|\\text\{?\s*(?:for|if|otherwise|where|and|else|s\.?t)|"
    r"for(?:all|any)?\b|iff?\b|if(?=[a-z]{0,2}\s*[=_^(<>≤≥∈\\{])|"
    r"otherwise\b|where\b|and\b|else\b|with\b|\\mid\b|\|)",
    re.IGNORECASE,
)
_REL_LEAD = re.compile(
    r"^\s*(?:[=<>≤≥≠∈←]|\\leq?\b|\\geq?\b|\\neq?\b|\\in\b|\\subseteq\b|\\sim\b)"
)
_BINOP_LEAD = re.compile(r"^\s*(?:[+\-*/·×]|\\times\b|\\cdot\b|\\pm\b|\\cup\b|\\cap\b)")
# a "bare" index LHS: 1-3 short symbols (letter or \command, optional numeric
# subscript / primes), comma-or-space separated — as in "t , p", "w_1", "i".
_ACCENT = r"\\(?:hat|bar|tilde|dot|ddot|check|breve|acute|grave|vec|overline|underline|widehat|widetilde)\s*\{\s*[A-Za-zα-ωΑ-Ω]\s*\}"
_BARE_SYM = (
    r"(?:" + _ACCENT + r"|\\[a-zA-Z]+|[A-Za-zα-ωΑ-Ω])"
    r"(?:\s*\^?['′]+|\s*\^\s*\{\s*['′]+\s*\})?(?:\s*_\s*(?:\d|\{\s*\d+\s*\}))?"
    r"(?:\s*[+\-]\s*(?:\d+|[A-Za-zα-ω]))?"  # index arithmetic: s+1, i+w, k-1
)
# looser index symbol for membership conditions: one short sub/sup group of
# letters/digits/primes is still an index ("s_k", "tr_{e'}", "s^1")
_IDX_ATOM = (
    r"(?:" + _ACCENT + r"|\\[a-zA-Z]+|[A-Za-z](?:\s?[A-Za-z]){0,2}|[α-ωΑ-Ω])"
    r"(?:\s*[_^]\s*(?:[A-Za-z0-9'′]|\{(?:[^{}]|\{[^{}]{0,8}\}){0,14}\})){0,2}"
    r"(?:\s*[+\-]\s*(?:\d+|[A-Za-zα-ω][a-z]?))?"
)
_IDX_SYM = _IDX_ATOM + r"(?:\s*(?:\\rightarrow|\\to(?![a-zA-Z])|→)\s*" + _IDX_ATOM + r")?"
_BARE_LHS = re.compile(
    r"^\s*(?:\(\s*)?" + _BARE_SYM + r"(?:\s*,\s*" + _BARE_SYM + r"){0,3}(?:\s*\))?\s*$"
)
_IDX_LHS = re.compile(
    r"^\s*(?:\\left\s*)?\(?\s*" + _IDX_SYM + r"(?:\s*,\s*" + _IDX_SYM + r"){0,3}"
    r"\s*(?:\\right\s*)?\)?\s*$"
)
# index arithmetic RHS: only symbols/numbers/basic ops, no structure
_IDX_EXPR = re.compile(
    r"^[\sA-Za-z0-9α-ωΑ-Ω_^'′{}+\-·×*/()\\,.]{1,40}$"
)
_STRUCTURE = re.compile(r"\\(?:sum|prod|frac|int|underset|overset|min|max|begin)\b")
_FUNC_LHS = re.compile(r"^\s*\\?[A-Za-zΔδ][A-Za-z]*\s*(?:\\left\s*)?\\?\(.*\)\s*$")
_NUMERIC = re.compile(r"^[\s\d.,…]*(?:\\[lh]?dots\b|\.\.\.)?[\s\d.,…]*$")
_TEXTY = re.compile(r"[A-Za-z]")
_REF_LIKE = re.compile(r"^[\s\(\)\[\]\d,;.\-–—~∼]*$")


def _strip_seps(x: str) -> str:
    return x.strip().strip(",;.").strip()


def _seg_relations(seg: str) -> list[tuple[int, str]]:
    return _top_relations(seg)


def _is_reference(seg: str) -> bool:
    """Text/number-only content: 'Constraints (1)-(35)', '(C_mpc)', 'ConS_2.'"""
    if _seg_relations(seg):
        return False
    body = re.sub(r"\\text[a-z]*\s*\{[^{}]*\}", " ", seg)
    body = re.sub(r"\\(?:sim|textrm|,|;|quad|qquad)", " ", body)
    if _REF_LIKE.match(body):
        return True
    # words but no math statement: "constraints", "Constraints of ..."
    toks = re.findall(r"[A-Za-z]{2,}", body)
    mathish = re.findall(r"[_^]|\\sum|\\frac|\\prod", seg)
    return bool(toks) and not mathish and not _seg_relations(seg)


_MEMBER_TOKS = ("\\in", "\\notin", "\\subseteq", "\\subset", "∈", "∉", "⊆", "∊")
# domain-literal RHS after ∈: {0,1}, number sets, numeric intervals — these
# mark DOMAIN CONSTRAINTS (own formula), not index conditions
_DOMAIN_RHS = re.compile(
    r"\\?\{\s*0\s*(?:,\s*1\s*)?\s*(?:\\right\s*)?\\?\}|\\mathbb|\\mathbf\{[ZRNB]\}"
    r"|[ℤℝℕ]|\(\s*0\s*,\s*1\s*(?:\\right\s*)?\)|(?:\\left\s*)?[\[(]\s*0\s*,"
)


def _base_of(lhs: str) -> str:
    """Leading letter-run of an LHS (spaced singles collapsed) — the symbol
    family, e.g. 'IP' from 'I P_{j}^{s}', 'x' from 'x_{e}'."""
    n = re.sub(r"\s+", "", _norm_of(lhs))
    n = re.sub(r"^(?:[^A-Za-z\\]|\\left)+", "", n)
    m = re.match(r"\\?([A-Za-z]+)", n)
    return m.group(1) if m else ""


def _is_condition_seg(
    seg: str, norm_seg: str, tail_engaged: bool = False, unit_base: str = ""
) -> bool:
    """Quantifier/condition-shaped: safe to keep in a tail."""
    seg = _strip_seps(seg)
    if not seg:
        return True
    if _QUANT_LEAD.match(norm_seg.strip() or seg):
        return True
    rels = _seg_relations(seg)
    if not rels:
        return True  # bare numbers / ellipsis / lone symbols continue a range
    lhs = re.sub(r"^[\s&]+", "", seg[: rels[0][0]])
    nlhs = re.sub(r"\s+", " ", _norm_of(lhs)).strip("& ")  # sheds \textrm{ } pad
    if _BARE_LHS.match(lhs) or _BARE_LHS.match(nlhs):
        return True
    tok = rels[0][1]
    if (
        tok in _MEMBER_TOKS
        and (_IDX_LHS.match(lhs) or _IDX_LHS.match(nlhs))
        and not _DOMAIN_RHS.search(seg[rels[0][0]:])
    ):
        return True  # "s_k \in S", "tr_{e'} \in TR": subscripted index member
    # numeric range chain "1 <= u < |S|": number, then a bare index symbol
    if (
        len(rels) >= 2
        and re.fullmatch(r"[\s\d.]+", lhs)
        and _IDX_LHS.match(seg[rels[0][0] + len(tok): rels[1][0]] or " ")
    ):
        return True
    if _FUNC_LHS.match(lhs) and not re.search(r"\\sum|\\prod|\\frac|\\int", seg):
        return True
    # index-binding equality once a tail is engaged: "t_l = t + k_l \tau";
    # a pure-number RHS ("= 0") is a boundary/fixing CONSTRAINT when its
    # symbol family matches the unit's main LHS ("IP_j^m = 0" after
    # "IP_j^s = ..."), an index guard otherwise ("r_{st_e} = 1")
    if tail_engaged and tok == "=" and (_IDX_LHS.match(lhs) or _IDX_LHS.match(nlhs)):
        rhs = _strip_seps(seg[rels[0][0] + len(tok):])
        if _IDX_EXPR.match(rhs) and not _STRUCTURE.search(seg):
            if not re.fullmatch(r"[\d\s.]+", rhs):
                return True
            if unit_base and _base_of(lhs) != unit_base:
                return True
    # idx-to-idx comparison inside an engaged tail: "t_e \neq t_f" —
    # only when it is the segment's sole relation (an "x >= 0 (i,j) \in N"
    # domain statement carries a second, membership relation)
    if (
        tail_engaged
        and len(rels) == 1
        and _is_compare_tok(tok)
        and (_IDX_LHS.match(lhs) or _IDX_LHS.match(nlhs))
        and _IDX_EXPR.match(_strip_seps(seg[rels[0][0] + len(tok):]) or "?")
        and not _STRUCTURE.search(seg)
    ):
        return True
    # set-emptiness conditions: "C_b \cap C_m \neq \emptyset"
    return bool(re.search(r"\\emptyset|∅", seg))


def _is_statement_seg(
    seg: str, norm_seg: str, tail_engaged: bool = False, unit_base: str = ""
) -> bool:
    """A compound-LHS relational statement — can start a new formula unit."""
    seg2 = _strip_seps(seg)
    if not seg2 or _QUANT_LEAD.match(norm_seg.strip() or seg2):
        return False
    if _REL_LEAD.match(seg2) or _BINOP_LEAD.match(seg2):
        return False
    rels = _seg_relations(seg2)
    if not rels:
        return _MINMAX.match(seg2.lstrip("\\ ")) is not None
    return not _is_condition_seg(seg2, norm_seg, tail_engaged, unit_base)


def _is_compare_tok(tok: str) -> bool:
    return (len(tok) == 1 and tok in _COMPARE_CHARS) or tok in (
        "\\leq", "\\le", "\\geq", "\\ge", "\\neq", "\\ne"
    )


def _has_compare_statement(seg: str) -> bool:
    """A comparison whose LOCAL LHS (since the last top-level separator or
    relation) is compound — bare-index conditions like "t_e \\neq t_f" or
    ": k > k'" don't count."""
    if re.search(r"\\emptyset|∅", seg):
        return False
    tops = _top_spans(seg)
    seps = sorted([i for i, c in tops if c in ",;:∧"])
    rels = _seg_relations(seg)
    for pos, tok in rels:
        if not _is_compare_tok(tok):
            continue
        prev = 0
        for i in seps:
            if i < pos:
                prev = max(prev, i + 1)
        for rpos, rtok in rels:
            if rpos < pos:
                prev = max(prev, rpos + len(rtok))
        lhs = _strip_seps(seg[prev:pos])
        nlhs = re.sub(r"\s+", " ", _norm_of(lhs))
        if (
            lhs
            and not _BARE_LHS.match(lhs)
            and not _BARE_LHS.match(nlhs)
            and not _IDX_LHS.match(lhs)
            and not _IDX_LHS.match(nlhs)
        ):
            return True
    return False


# --------------------------------------------------------------------------
# Result type
# --------------------------------------------------------------------------


@dataclass
class SplitResult:
    parts: list[str]
    kind: str
    confident: bool = True
    notes: list[str] = field(default_factory=list)

    @property
    def is_split(self) -> bool:
        return len(self.parts) > 1


def _clean_part(p: str) -> str:
    """Make a cut row/segment renderable: drop top-level alignment tabs and
    row breaks and unmatched env tokens, keep nested structure intact."""
    # drop unbalanced \begin/\end tokens (the split env's own remnants)
    for name in set(m.group(2) for m in _ENV_TOKEN.finditer(p)):
        pat_b = re.compile(r"\\begin\s*\{" + re.escape(name) + r"\}")
        pat_e = re.compile(r"\\end\s*\{" + re.escape(name) + r"\}")
        while len(pat_b.findall(p)) > len(pat_e.findall(p)):
            p = pat_b.sub(" ", p, count=1)
        while len(pat_e.findall(p)) > len(pat_b.findall(p)):
            # drop from the right: an \end without its \begin
            idx = [m.start() for m in pat_e.finditer(p)]
            p = p[: idx[-1]] + " " + p[idx[-1]:].replace("\\end{" + name + "}", " ", 1)
    # drop unpaired \left / \right tokens the cut left behind (FIRST — a
    # dangling \left\{ would hide the alignment chars at depth 1)
    events = []  # (start, end, which)
    i = 0
    while i < len(p):
        m = _LR.match(p, i)
        if m:
            events.append((m.start(), m.end(), m.group(1)))
            i = m.end()
        else:
            i += 1
    stack: list[int] = []
    unpaired: set[int] = set()
    for k, (_a, _b, which) in enumerate(events):
        if which == "left":
            stack.append(k)
        elif stack:
            stack.pop()
        else:
            unpaired.add(k)
    unpaired.update(stack)
    if unpaired:
        keep = []
        prev = 0
        for k, (a, b, _which) in enumerate(events):
            if k in unpaired:
                keep.append(p[prev:a])
                prev = b
        keep.append(p[prev:])
        p = " ".join(keep)
    # remove only TOP-LEVEL & and \\ (nested envs keep their alignment)
    drop = set()
    tops = _top_spans(p)
    for i, c in tops:
        if c == "&":
            drop.add(i)
        elif c == "\\" and p[i : i + 2] == "\\\\":
            drop.add(i)
            drop.add(i + 1)
    p = "".join(c for i, c in enumerate(p) if i not in drop)
    p = re.sub(r"\s+", " ", p).strip()
    return p.strip(",; ").strip()


def _balanced(p: str) -> bool:
    """Braces, \\left/\\right and environments all pair up."""
    depth = 0
    i = 0
    n = len(p)
    lr = 0
    while i < n:
        m = _LR.match(p, i)
        if m:
            lr += 1 if m.group(1) == "left" else -1
            if lr < 0:
                return False
            i = m.end()
            continue
        c = p[i]
        if c == "\\" and i + 1 < n and p[i + 1] in "{}":
            depth += 1 if p[i + 1] == "{" else -1
            i += 2
        else:
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        if depth < 0:
            return False
    if depth != 0 or lr != 0:
        return False
    begins = [m.group(2) for m in _ENV_TOKEN.finditer(p) if m.group(1) == "begin"]
    ends = [m.group(2) for m in _ENV_TOKEN.finditer(p) if m.group(1) == "end"]
    return sorted(begins) == sorted(ends)


def _norm_of(seg: str) -> str:
    return _norm_map(seg)[0]


def _finalize(original: str, parts: list[str], kind: str, confident: bool) -> SplitResult:
    """Emit a split only when every part survived the cut renderably."""
    parts = [p for p in parts if p.strip()]
    if len(parts) <= 1:
        return SplitResult([original], "single", confident)
    if not all(_balanced(p) for p in parts):
        return SplitResult(
            [original], "suspect_blob", False,
            ["split produced unbalanced parts — needs manual cut"],
        )
    return SplitResult(parts, kind, confident)


# --------------------------------------------------------------------------
# Flat segmentation (commas / semicolons / "and" at top level)
# --------------------------------------------------------------------------

_AND_TOKEN = re.compile(r"\\text[a-z]*\s*\{\s*and\s*\}|(?<![A-Za-z\\])and(?![A-Za-z])")


def _flat_segments(s: str) -> list[tuple[str, str]]:
    """Split at top-level ',', ';', '∧'/'\\land' and textual 'and'.

    Returns (separator, segment) pairs; separator of the first is ''.
    """
    tops = _top_spans(s)
    cuts: list[tuple[int, int, str]] = []  # (start, end, sep-kind)
    for i, c in tops:
        if c in ",;∧":
            cuts.append((i, i + 1, ";" if c == ";" else ","))
    for m in _AND_TOKEN.finditer(s):
        a = m.start()
        if any(a == i for i, _ in tops) or s[a] == "\\":
            # command-form always top level only if its backslash char is top
            if s[a] == "\\" and not any(i == a for i, _ in tops):
                continue
            cuts.append((m.start(), m.end(), ","))
    for m in re.finditer(r"\\land(?![a-zA-Z])", s):
        if any(i == m.start() for i, _ in tops):
            cuts.append((m.start(), m.end(), ","))
    cuts.sort()
    segs: list[tuple[str, str]] = []
    prev = 0
    sep = ""
    for a, b, k in cuts:
        segs.append((sep, s[prev:a]))
        prev = b
        sep = k
    segs.append((sep, s[prev:]))
    return [(k, seg) for k, seg in segs if seg.strip()]


def _count_compound_compares(u: str) -> int:
    """Comparisons whose local LHS (since the last top-level separator or
    relation) is compound — bare-index conditions like ": k > k'" don't count."""
    tops = _top_spans(u)
    seps = sorted([i for i, c in tops if c in ",;:∧"])
    rels = _seg_relations(u)
    n = 0
    for pos, tok in rels:
        if not _is_compare_tok(tok):
            continue
        prev = 0
        for i in seps:
            if i < pos:
                prev = max(prev, i + 1)
        for rpos, rtok in rels:
            if rpos < pos:
                prev = max(prev, rpos + len(rtok))
        lhs = _strip_seps(u[prev:pos])
        if lhs and not _BARE_LHS.match(lhs) and not _IDX_LHS.match(lhs):
            n += 1
    return n


def _units_from_flat(s: str, *, in_model_tail: bool = False) -> tuple[list[str], bool]:
    """Group flat segments into formula units.  Returns (units, confident)."""
    segs = _flat_segments(s)
    units: list[list[str]] = []
    preamble: list[str] = []  # leading quantifier fragments before any unit
    locked = False  # inside a quantifier tail (∀ / for / if ...)
    engaged = False  # current unit already collected tail conditions
    unit_base = ""  # symbol family of the current unit's main LHS
    confident = True
    for sep, seg in segs:
        nseg = _norm_of(seg)
        if sep == ";":
            locked = False
        quant = bool(_QUANT_LEAD.match(nseg.strip() or seg.strip()))
        if not units and not _is_statement_seg(seg, nseg):
            preamble.append(seg)
            continue
        # mid-segment ∀ also locks what follows
        if units and not quant and locked:
            units[-1].append(seg)
            continue
        if units and (quant or not _is_statement_seg(seg, nseg, engaged, unit_base)):
            units[-1].append(seg)
            engaged = True
            if quant or re.search(r"\\forall|∀", seg):
                locked = True
            continue
        if units and in_model_tail and _is_reference(seg):
            units[-1].append(seg)
            continue
        if units and not _seg_relations(" ".join(units[-1])):
            # previous unit never got its relation — it was a dangling head,
            # not a formula of its own (e.g. a comma-separated LHS list)
            units[-1].append(seg)
        else:
            units.append([seg])
            engaged = False
        rels0 = _seg_relations(seg)
        unit_base = _base_of(seg[: rels0[0][0]]) if rels0 else ""
        if re.search(r"\\forall|∀", seg) or re.search(
            r"\\text[a-z]*\{\s*(?:for|if)", seg, re.I
        ) or re.search(r"(?<![a-zA-Z])(?:for\s?all|if)(?![a-zA-Z])", nseg, re.I):
            locked = True
    if preamble and units:
        units[0] = preamble + units[0]
    elif preamble and not units:
        units = [preamble]
    out = [" , ".join(u) for u in units]
    for u in out:
        # >=2 compound-LHS comparisons in one long unit = glued statements
        # we could not separate (chains share operands and stay short)
        if _count_compound_compares(u) >= 2 and len(u) > 220:
            confident = False
    return out, confident


# --------------------------------------------------------------------------
# Row handling for display environments
# --------------------------------------------------------------------------


def _rows_of(body: str) -> list[str]:
    tops = {i for i, c in _top_spans(body) if c == "\\"}
    rows: list[str] = []
    prev = 0
    i = 0
    while i < len(body) - 1:
        if body[i] == "\\" and body[i + 1] == "\\" and i in tops:
            rows.append(body[prev:i])
            prev = i + 2
            i += 2
            continue
        i += 1
    rows.append(body[prev:])
    return rows


_NOISE_ROW = re.compile(r"^[\s&]*$")
# spacing-only matrix blocks the converter emits for horizontal alignment
_SPACING_ENV = re.compile(r"\\begin\{[a-zA-Z*]+\}[\s&\\]*\\end\{[a-zA-Z*]+\}")


def _strip_row_noise(row: str) -> str:
    prev = None
    while prev != row:
        prev = row
        row = _SPACING_ENV.sub(" ", row)
    # converter indentation artifact: content wrapped in a leading matrix
    # opener — strip openers/&/space so lead classification sees the content
    return re.sub(r"^(?:\s|&|\\begin\{[a-zA-Z*]+\})+", "", row)


def _row_cells(row: str) -> list[str]:
    tops = {i for i, c in _top_spans(row) if c == "&"}
    cells: list[str] = []
    prev = 0
    for i in sorted(tops):
        cells.append(row[prev:i])
        prev = i + 1
    cells.append(row[prev:])
    return cells


def _guard_cell(cell: str) -> bool:
    """Trailing cell that is an if/otherwise/condition guard."""
    ncell = _norm_of(cell).strip()
    if not ncell:
        return False
    if re.match(r"^(?:iff?\b|otherwise\b|for\b|else\b)", ncell, re.I):
        return True
    if re.match(r"^\\?text", cell.strip()) and re.search(r"\b(?:if|otherwise|for|else)\b",
                                                         ncell, re.I):
        return True
    rels = _seg_relations(cell)
    if rels and _is_condition_seg(cell, ncell) and not _QUANT_LEAD.match(ncell):
        # bare condition like "i \in S" as its own cell
        return not _is_statement_seg(cell, ncell)
    # short lone comparison cell ("F_{ik} > 0") = a case guard
    return (
        len(rels) == 1
        and _is_compare_tok(rels[0][1])
        and len(_strip_seps(cell)) <= 45
        and not re.search(r"\\sum|\\prod|\\frac|\\int", cell)
    )


def _row_tail_context(row: str) -> bool:
    """Row ends inside a quantifier tail (its trailing top-level comma
    segments are conditions), so condition-shaped next rows continue it."""
    segs = _flat_segments(row)
    if len(segs) < 2:
        return bool(re.search(r"\\forall|∀", row))
    tail = False
    for _sep, seg in segs[1:]:
        nseg = _norm_of(seg)
        if _QUANT_LEAD.match(nseg.strip() or seg.strip()) or (
            _seg_relations(seg) and _is_condition_seg(seg, nseg)
        ):
            tail = True
        elif _is_statement_seg(seg, nseg):
            tail = False
    return tail or bool(re.search(r"\\forall|∀", row))


def _condition_row(row: str) -> bool:
    """Row containing only conditions/orderings — mergeable in tail context."""
    if _has_compare_statement(row):
        return False
    for _sep, seg in _flat_segments(row):
        nseg = _norm_of(seg)
        if _seg_relations(seg) and not _is_condition_seg(seg, nseg, True):
            return False
    return True


def _split_env_rows(rows: list[str], *, in_model_tail: bool = False) -> tuple[list[str], bool]:
    """Merge env rows into formula units.  Returns (units, confident)."""
    units: list[list[str]] = []
    confident = True
    tailctx = False
    preamble: list[str] = []  # leading quantifier/fragment rows
    for row in rows:
        if _NOISE_ROW.match(re.sub(r"\\begin\{[a-z]*\}|\\end\{[a-z]*\}", "", row)):
            continue
        stripped = _strip_row_noise(row)
        nrow = _norm_of(stripped).strip()
        rels = _seg_relations(row)
        has_main_rel = any(
            _is_compare_tok(t) or t in ("=", "←", "\\leftarrow", "\\gets")
            for _, t in rels
        )
        # leading "-" is a sign, not glue: only a continuation when the row
        # carries no main relation of its own (wrapped RHS overflow lines)
        minus_lead = bool(re.match(r"^\s*[-−]", stripped))
        is_cont = (
            (not rels and not _MINMAX.match(stripped.lstrip("\\ ")))
            or _REL_LEAD.match(stripped)
            or (_BINOP_LEAD.match(stripped) and not (minus_lead and has_main_rel))
        )
        is_quant = bool(_QUANT_LEAD.match(nrow or stripped))
        if not units and (is_quant or is_cont):
            preamble.append(row)
            continue
        if units and (is_cont or is_quant):
            units[-1].append(row)
            if is_quant:
                tailctx = True
            continue
        if units and tailctx and _condition_row(row):
            units[-1].append(row)
            continue
        # a pure named-set condition row ("s, s+1 \in S_{i,m}") cannot stand
        # alone as a formula — it is the previous statement's tail even
        # without explicit tail context.  Requires a membership and tolerates
        # index equalities, but any comparison, domain literal ("{0,1}") or
        # model-tail position (stacked constraints expected) keeps it apart.
        if (
            units
            and not in_model_tail
            and not _DOMAIN_RHS.search(row)
            and any(t in _MEMBER_TOKS for _, t in _seg_relations(row))
            and not any(_is_compare_tok(t) for _, t in _seg_relations(row))
            and _condition_row(row)
            and _seg_relations(" ".join(units[-1]))
        ):
            units[-1].append(row)
            continue
        if in_model_tail and _is_reference(row):
            continue  # drop pure reference rows in model tails
        if units and not _seg_relations(" ".join(units[-1])) and not _MINMAX.match(
            _strip_row_noise(" ".join(units[-1])).lstrip("\\ ")
        ):
            # previous "unit" never got a relation: it was a dangling head
            # (mid-expression line break), not a formula of its own
            units[-1].append(row)
            tailctx = _row_tail_context(row)
            continue
        units.append([row])
        tailctx = _row_tail_context(row)
    if preamble and units:
        units[0] = preamble + units[0]
    elif preamble and not units:
        units = [preamble]
    return [" ".join(u) for u in units], confident


def _has_guarded_rows(rows: list[str]) -> bool:
    """True when every statement row carries an if/condition guard cell or
    an inline if/otherwise — the piecewise-definition pattern."""
    n_stmt = 0
    n_guard = 0
    for row in rows:
        rels = _seg_relations(row)
        if not rels:
            continue
        n_stmt += 1
        cells = _row_cells(row)
        guarded = len(cells) >= 2 and any(_guard_cell(c) for c in cells[1:]) and any(
            _seg_relations(c) for c in cells[:1]
        )
        nrow = _norm_of(row)
        if re.search(r"(?<![a-zA-Z])(?:if|otherwise)(?![a-zA-Z])", nrow, re.I):
            guarded = True
        if guarded:
            n_guard += 1
    return n_stmt > 0 and n_guard == n_stmt


# --------------------------------------------------------------------------
# Environment structure
# --------------------------------------------------------------------------


# degenerate immediately-closed pair the converter emits as decoration
_DEGENERATE_LR = re.compile(r"\\left\s*(?:\\\{|\{|\(|\[|\.)\s*\\right\s*\.?")
_LEFT_OPEN = re.compile(r"^\s*(?::\s*)?\\left\s*(?:\\\{|\{|\(|\[)")


def _unwrap_envelope(s: str) -> tuple[str, str]:
    """Strip a leading ``\\left\\{ ... \\right`` envelope wrapping the whole
    string.  Returns ``(core, post)`` — ``post`` is trailing content after
    the closing ``\\right`` (usually a shared quantifier tail)."""
    s = _DEGENERATE_LR.sub(" ", s)
    m = _LEFT_OPEN.match(s)
    if not m:
        return s, ""
    depth = 0
    i = m.start()
    n = len(s)
    while i < n:
        lm = _LR.match(s, i)
        if lm:
            if lm.group(1) == "left":
                depth += 1
            else:
                depth -= 1
                if depth == 0:
                    inner = s[m.end():lm.start()]
                    return inner, s[lm.end():]
            i = lm.end()
            continue
        i += 1
    # no matching \right (truncated) — drop the opener
    return s[m.end():], ""


def _outer_env(s: str):
    """First top-level display environment: (pre, name, body, post) or None."""
    for i, kind, payload in _scan(s):
        if kind == "env":
            name, end = payload
            m = _ENV_TOKEN.match(s, i)
            body_start = m.end()
            m_end = re.compile(r"\\end\s*\{" + re.escape(name) + r"\}")
            # matching \end is just before ``end``
            inner = s[body_start:end]
            em = None
            for em2 in m_end.finditer(s, body_start, end):
                em = em2
            body = s[body_start:em.start()] if em else inner
            return s[:i], name, body, s[end:]
    return None


# --------------------------------------------------------------------------
# Public entry point
# --------------------------------------------------------------------------


# the converter writes literal set braces as immediately-closed pairs:
# "\left\{\right. content \left.\right\}".  Rewrite them to \{ ... \} so
# their content nests properly — EXCEPT when the pair wraps a display
# environment (there it is a block decoration, handled by _unwrap_envelope).
_DEG_OPEN = re.compile(r"\\left\s*\\\{\s*\\right(?![a-zA-Z])(?![\s.]*\\begin)\s*\.?")
_DEG_CLOSE = re.compile(r"\\left(?![a-zA-Z])\s*\.?\s*\\right(?![a-zA-Z])\s*\\\}")
_DEG_OPEN_P = re.compile(r"\\left\s*\(\s*\\right(?![a-zA-Z])(?![\s.]*\\begin)\s*\.?")
_DEG_CLOSE_P = re.compile(r"\\left(?![a-zA-Z])\s*\.?\s*\\right(?![a-zA-Z])\s*\)")


def _fix_degenerate(s: str) -> str:
    s = _DEG_CLOSE.sub(r" \\} ", s)
    s = _DEG_OPEN.sub(r" \\{ ", s)
    s = _DEG_CLOSE_P.sub(" ) ", s)
    s = _DEG_OPEN_P.sub(" ( ", s)
    return s


def split_latex(latex: str) -> SplitResult:
    """Deterministically split a Tier-2 extraction into formula units."""
    s = latex.strip()
    if not s:
        return SplitResult([latex], "empty")
    s = _fix_degenerate(s)
    norm, idxmap = _norm_map(s)

    # ---- model block: an s.t. marker at top level -------------------------
    mm = _ST_MARKER.search(norm)
    if mm:
        raw_cut = idxmap[mm.start()] if mm.start() < len(idxmap) else len(s)
        raw_end = idxmap[mm.end() - 1] + 1 if mm.end() - 1 < len(idxmap) else len(s)
        head, tail = s[:raw_cut], s[raw_end:]
        return _split_model(s, head, tail)

    # ---- environment rows -------------------------------------------------
    core, env_post = _unwrap_envelope(s)
    env = _outer_env(core)
    if env:
        pre, name, body, post = env
        post = (post + " " + env_post).strip() if env_post else post
        if name in _SPLITTABLE_ENVS:
            if _top_relations(pre):
                return SplitResult([latex], "piecewise")  # X = {env}: RHS block
            rows = _rows_of(body)
            if _has_guarded_rows(rows):
                return SplitResult([latex], "piecewise")
            # converter sometimes emits the guards as a SIBLING matrix
            # ("{expr \\ expr} {if c \\ otherwise}") — that is one piecewise
            if re.search(
                r"(?<![a-zA-Z])(?:if|otherwise)(?![a-zA-Z])", _norm_of(post), re.I
            ):
                return SplitResult([latex], "piecewise")
            units, conf = _split_env_rows(rows)
            n_real_rows = len([r for r in rows if not _NOISE_ROW.match(
                re.sub(r"\\begin\{[a-z]*\}|\\end\{[a-z]*\}", "", r))])
            # a lone row may still hide structure — recurse into it once;
            # multi-row envs merged to one unit stay merged (the row logic
            # already decided those rows belong together)
            if len(units) == 1 and n_real_rows == 1 and units[0].strip() != s:
                if _outer_env(units[0]):
                    inner = split_latex(units[0])
                    if inner.is_split:
                        return inner
                flat_units, fconf = _units_from_flat(units[0])
                if len(flat_units) > 1:
                    return _finalize(
                        latex, [_clean_part(u) for u in flat_units], "comma_units", fconf
                    )
            if len(units) <= 1:
                return SplitResult([latex], "single")
            post_tail = _strip_seps(_clean_part(post))
            parts = []
            for u in units:
                p = _clean_part(u)
                if post_tail and _is_condition_seg(post_tail, _norm_of(post_tail)):
                    p = p.rstrip(" ,;") + " , " + post_tail
                parts.append(p)
            if post_tail and not _is_condition_seg(post_tail, _norm_of(post_tail)):
                parts.append(post_tail)
            return _finalize(latex, parts, "stacked_rows", conf)
        # opaque env (pmatrix/bmatrix/…): fall through to flat handling
    # ---- flat text --------------------------------------------------------
    # top-level \\ outside any env acts like a row separator
    if any(True for _ in re.finditer(r"\\\\", s)):
        tops = {i for i, c in _top_spans(s) if c == "\\"}
        if any(i in tops and s[i : i + 2] == "\\\\" for i in tops):
            rows = _rows_of(s)
            if len(rows) > 1:
                units, conf = _split_env_rows(rows)
                if len(units) > 1:
                    return _finalize(latex, [_clean_part(u) for u in units], "stacked_rows", conf)
                return SplitResult([latex], "single")
    units, conf = _units_from_flat(s)
    if len(units) > 1:
        return _finalize(latex, [_clean_part(u) for u in units], "comma_units", conf)
    if not conf:
        return SplitResult([latex], "suspect_blob", False)
    return SplitResult([latex], "single")


def _split_model(full: str, head: str, tail: str) -> SplitResult:
    """min/max head + s.t. tail -> objective + real constraint units."""
    # marker text often sits inside \text{...}; the cut leaves orphan
    # closers/colons at the tail head and a dangling \text{ opener at the
    # head's end — junk, never content
    tail = re.sub(r"^[\s})\]:.,;]+", " ", tail)
    head = re.sub(r"(?:\\text[a-z]*\s*\{?\s*)+$", " ", head)
    head_clean = _clean_part(head)
    has_obj = bool(_MINMAX.search(_norm_of(head_clean))) and bool(head_clean)
    # tail: environment rows or flat segments
    tail_units: list[str] = []
    confident = True
    t, t_post = _unwrap_envelope(tail)
    if t_post.strip():
        t = t + " " + t_post
    env = _outer_env(t)
    guard_piecewise = False
    if env and env[1] in _SPLITTABLE_ENVS and not _top_relations(env[0]):
        rows = _rows_of(env[2])
        if _has_guarded_rows(rows) and env[1] == "cases" and not any(
            _MINMAX.search(_norm_of(r) or r) for r in rows
        ):
            guard_piecewise = True
        else:
            tail_units, confident = _split_env_rows(rows, in_model_tail=True)
            post = _strip_seps(_clean_part(env[3]))
            if post and not _is_reference(post):
                tail_units.append(post)
    if not tail_units and not guard_piecewise:
        # flat or env-less tail; env rows may still hide behind \left\{
        body = t
        if "\\\\" in body:
            rows = _rows_of(body)
            tail_units, confident = _split_env_rows(rows, in_model_tail=True)
        else:
            tail_units, confident = _units_from_flat(body, in_model_tail=True)
            tail_units = [u for u in tail_units if not _is_reference(u)]
    real = [u for u in tail_units if not _is_reference(u) and _seg_relations(u)]
    if not real:
        kind = "model_ref" if has_obj or _is_reference(tail) else "st_prefix"
        return SplitResult([full], kind)
    parts = ([head_clean] if has_obj else []) + [_clean_part(u) for u in real]
    if len(parts) <= 1:
        return SplitResult([full], "st_prefix")
    # a single giant unsplit constraint blob after s.t. -> low confidence
    for u in real:
        n_comp = len([1 for p, tok in _seg_relations(u) if _is_compare_tok(tok)])
        if n_comp >= 2 and len(u) > 200:
            confident = False
    return _finalize(full, parts, "model_block", confident)
