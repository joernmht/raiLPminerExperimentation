"""What the algebra states outright about a paper's symbols.

Most of a symbol table has to be read out of prose, but not all of it. Two
constructs declare a symbol's kind in the mathematics itself, and both are
common enough in published formulations to be worth mining before any human is
asked anything:

*Binders.* ``\\sum_{i \\in I}``, ``\\prod_{t = 1}^{T}`` and ``\\forall i \\in I``
name an index and the family it ranges over. The family is what the canonical
model must declare (``%@ index I``); the bound letter needs no declaration of
its own, so the two are kept apart rather than lumped together as "index-ish".

*Domain rows.* ``x_{ij} \\in \\{0,1\\}``, ``t_i \\ge 0``, ``k_a \\in \\mathbb{Z}``
declare decision variables and their domains. A paper states these once, in a
row of their own, and that row is extracted like any other formula. Recognizing
it types every occurrence of those symbols in the paper.

Everything here is deterministic and conservative: a construct that is only
probably a declaration is not one. ``t_e - \\bar{t}_e \\ge 0`` is a constraint,
not a domain row, and is rejected because its left side is an expression rather
than a list of symbols.

Used by :mod:`corpusbuilder.resolution` to measure the deterministic prefill and
by :mod:`corpusbuilder.promote` to pre-fill the declaration sidecar stub.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from corpusbuilder.game import _collapse_words, _group_end, _rewrite_ops, extract_symbols

# --------------------------------------------------------------------------- #
# Binders — indices and the families they range over
# --------------------------------------------------------------------------- #

#: Operators whose scripts bind an index, plus the universal quantifier.
_BINDER_HEAD = re.compile(r"\\(?:sum|prod|bigcup|bigcap|max|min|forall)(?![a-zA-Z])")

#: Where a ``\forall`` clause ends. Unlike a subscript group it has no braces to
#: delimit it, so it runs to the next row break or textual interruption. Commas
#: are *not* a terminator: ``\forall i \in I, j \in J`` is one clause naming two
#: families, and cutting at the comma would drop the second.
_FORALL_END = re.compile(r"\\\\|\\quad|\\qquad|\\text|\\mathit")

#: Set membership, in the spellings the MathML conversion actually emits.
_MEMBERSHIP = re.compile(r"\\in(?![a-zA-Z])|[\u2208\u220a]")


@dataclass(frozen=True, slots=True)
class BinderRoles:
    """Symbols a binder names, split by the role the binder gives them."""

    indices: frozenset[str]
    families: frozenset[str]

    @property
    def all(self) -> frozenset[str]:
        return self.indices | self.families


def _symbols(text: str) -> set[str]:
    return {name for name, _ in extract_symbols(text, limit=None)[0]}


def _split_membership(chunk: str) -> tuple[str, str]:
    """Split ``i \\in I, j \\in J`` into the index side and the family side.

    A middle segment carries a family and then the next index (``I, j``); the
    comma is the seam. Without a membership sign there is no family to find.
    """
    parts = _MEMBERSHIP.split(chunk)
    if len(parts) == 1:
        return chunk, ""
    index_side, family_side = [parts[0]], []
    for middle in parts[1:-1]:
        # The family ends at the first comma; whatever follows the last one
        # introduces the next index. Anything between is a restriction on the
        # quantifier ("j \prec i"), which names no family and binds no index.
        family_side.append(middle.partition(",")[0])
        _, seam, tail = middle.rpartition(",")
        if seam:
            index_side.append(tail)
    family_side.append(parts[-1].partition(",")[0])
    return " ".join(index_side), " ".join(family_side)


def _scripts(s: str, i: int) -> tuple[list[str], list[str], int]:
    """Consume the scripts attached at ``i``; return (subscripts, superscripts)."""
    sub: list[str] = []
    sup: list[str] = []
    while True:
        while i < len(s) and s[i] == " ":
            i += 1
        if i >= len(s) or s[i] not in "_^":
            return sub, sup, i
        target = sub if s[i] == "_" else sup
        i += 1
        while i < len(s) and s[i] == " ":
            i += 1
        if i < len(s) and s[i] == "{":
            end = _group_end(s, i)
            target.append(s[i + 1 : end - 1])
            i = end
        elif i < len(s):
            target.append(s[i])
            i += 1
        else:
            return sub, sup, i


def binder_roles(latex: str) -> BinderRoles:
    """Indices and index families named by the binders in one formula.

    Read off the normalized source rather than the expression tree: the tree
    flattens a binder to a display string, and that flattening leaks artifacts
    (``\\left`` contributes a spurious ``ft``) which would then type a body
    symbol of the same name.
    """
    s = _rewrite_ops(_collapse_words(latex))
    indices: set[str] = set()
    families: set[str] = set()
    for m in _BINDER_HEAD.finditer(s):
        if m.group(0) == "\\forall":
            end = _FORALL_END.search(s, m.end())
            index_text, family_text = _split_membership(s[m.end() : end.start() if end else len(s)])
        else:
            sub, sup, _ = _scripts(s, m.end())
            index_text, family_text = _split_membership(" ".join(sub))
            # An upper limit bounds the family: "\sum_{t = 1}^{T}" ranges over T.
            family_text = f"{family_text} {' '.join(sup)}"
        indices |= _symbols(index_text)
        families |= _symbols(family_text)
    return BinderRoles(frozenset(indices - families), frozenset(families))


def binder_symbols(latex: str) -> set[str]:
    """Every symbol a binder names, whichever role it gives them."""
    return set(binder_roles(latex).all)


# --------------------------------------------------------------------------- #
# Domain rows — decision variables and their domains
# --------------------------------------------------------------------------- #

#: Tokens that make a left-hand side an expression rather than a list of
#: symbols. Their presence means the row is a constraint: "t_e - \bar t_e \ge 0"
#: bounds a difference, it does not declare "t" to be a variable.
_LHS_OPERATOR = re.compile(
    r"[+/=<>|]|(?<![a-zA-Z])-|\\(?:frac|sum|prod|cdot|times|div|max|min|int|"
    r"left|right|sqrt|log|exp)(?![a-zA-Z])"
)

#: The relation that can introduce a domain, and where the domain text begins.
_DOMAIN_REL = re.compile(r"\\in(?![a-zA-Z])|[\u2208\u220a]|\\geq?(?![a-zA-Z])|[\u2265]")

#: Canonical domains, in the order they must be tested (binary before integer:
#: "{0,1}" is a subset of the integers but the canonical model names it binary).
_DOMAIN_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The MathML conversion writes set braces as "\left\{ ... \right\}", so the
    # delimiters are optional on both sides rather than a bare brace pair.
    (
        "binary",
        re.compile(r"^\s*(?:\\left)?\s*\\?\{\s*0\s*,\s*1\s*(?:\\right)?\s*\\?\}"),
    ),
    ("binary", re.compile(r"^\s*(?:\\mathbb|\\mathcal|\\mathbf)?\s*\{?\s*B(?![a-zA-Z])")),
    (
        "integer",
        re.compile(r"^\s*(?:\\mathbb|\\mathcal|\\mathbf)\s*\{?\s*[ZN](?![a-zA-Z])"),
    ),
    (
        "non_negative",
        re.compile(
            r"^\s*(?:\\mathbb|\\mathcal|\\mathbf)\s*\{?\s*R(?![a-zA-Z])\s*\}?"
            r"\s*(?:\^\s*\{?\s*\+|_\s*\{?\s*(?:\+|\\geq?\s*0))"
        ),
    ),
    ("continuous", re.compile(r"^\s*(?:\\mathbb|\\mathcal|\\mathbf)\s*\{?\s*R(?![a-zA-Z])")),
)

#: A non-negativity row states the bound directly rather than naming a set.
_GE_ZERO = re.compile(r"^\s*0(?![.0-9])")


def domain_declaration(latex: str) -> dict[str, str]:
    """Variables declared by a domain row, mapped to their canonical domain.

    Returns an empty map for anything that is not unambiguously such a row, so a
    caller may trust every entry it does return.
    """
    s = _rewrite_ops(_collapse_words(latex))
    m = _DOMAIN_REL.search(s)
    if not m:
        return {}
    lhs, rhs = s[: m.start()], s[m.end() :]
    if not lhs.strip() or _LHS_OPERATOR.search(lhs):
        return {}

    if _MEMBERSHIP.fullmatch(m.group(0)):
        domain = next((d for d, pat in _DOMAIN_PATTERNS if pat.match(rhs)), None)
    elif _GE_ZERO.match(rhs):
        domain = "non_negative"
    else:
        domain = None
    if domain is None:
        return {}

    symbols = _symbols(lhs)
    return {name: domain for name in sorted(symbols)}


# --------------------------------------------------------------------------- #
# Paper-level evidence
# --------------------------------------------------------------------------- #

#: Kinds in the canonical vocabulary, as the review game's classifier emits them.
INDEX, PARAMETER, VARIABLE = "index", "parameter", "variable"


@dataclass(frozen=True, slots=True)
class Evidence:
    """What the algebra of one paper settles about its symbols.

    ``kinds`` answers "does a reviewer still have to think about this symbol";
    ``families`` and ``bound`` answer the different question "does it need a
    declaration line of its own". A quantifier's bound letter is an index for
    the first purpose and nothing at all for the second: ``\\forall j \\in J``
    declares the family ``J``, while ``j`` is bound by the quantifier and
    declaring it would invent a family the model does not have.
    """

    kinds: dict[str, str]
    domains: dict[str, str]
    families: frozenset[str]
    bound: frozenset[str]

    def kind_of(self, symbol: str) -> str | None:
        return self.kinds.get(symbol)


def paper_evidence(latex_rows: list[str]) -> Evidence:
    """Accumulate deterministic symbol evidence over one paper's formulas.

    Order matters where the two constructs disagree. A symbol declared by a
    domain row is a variable, full stop: that row is an explicit statement about
    it, whereas appearing in a binder is circumstantial (an author may write
    ``\\sum_{t=1}^{T}`` where ``T`` is a horizon parameter, and may reuse a
    bound letter as a variable elsewhere). Variables therefore win over indices.
    """
    families: set[str] = set()
    bound: set[str] = set()
    for latex in latex_rows:
        roles = binder_roles(latex)
        families |= roles.families
        bound |= roles.indices
    bound -= families

    kinds: dict[str, str] = {name: INDEX for name in sorted(families | bound)}
    domains: dict[str, str] = {}
    for latex in latex_rows:
        for name, domain in domain_declaration(latex).items():
            kinds[name] = VARIABLE
            domains[name] = domain
    declared = set(domains)
    return Evidence(
        kinds=kinds,
        domains=domains,
        families=frozenset(families - declared),
        bound=frozenset(bound - declared),
    )
