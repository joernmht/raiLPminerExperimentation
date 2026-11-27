r"""The head of an objective row, read by rule.

Papers state an objective in many spellings: ``\min \sum ...``, ``Z = min \sum
...``, ``(P1) \underset{x}{min} f(x) = ...``, ``\mathit{Minimize} ...``,
``[MILP] : min z = ...``, a ``cases`` block that carries "s.t. constraints
(1)-(7)" after the objective. The review game's detector
(:func:`corpusbuilder.game.is_objective_latex`) knows the common ones; the
assembly then took the row's first whitespace token as the operator, which is
wrong for every spelling that does not *start* with it.

:func:`objective_split` reads the head as a small grammar instead::

    PREFIX*  OPERATOR  [decision variables under the operator]  EXPRESSION  [s.t. ...]

``PREFIX`` is label material only (a problem label in brackets, words before a
colon, a scalar label ``Z =``); if anything else precedes the operator the row
is not an objective statement and the function returns ``None``. The operator
followed by a braced or parenthesised *list* (``max \{a, b\}``) is the
function, not an objective, and is refused as well, as is a label that carries
an index letter (``D_{j} = max ...`` defines a quantity per ``j``).

Nothing here interprets the expression: it is handed on to M1b untouched.
"""

from __future__ import annotations

import re

_GROUP = r"[^{}]*(?:\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}[^{}]*)*"
_OPWORD = r"(?:minimi[sz]e|maximi[sz]e|min|max)"
_WRAPPERS = re.compile(
    r"\\(?:begin|end)\{(?:matrix|aligned|gathered|split|array|cases|pmatrix)\}(?:\{[^{}]*\})?|&|\\\\"
)
_OP = re.compile(
    r"\\underset\s*\{" + _GROUP + r"\}\s*\{\s*(?:\\(?:text|mathrm|mathit|mathbf|operatorname)\s*"
    r"\{\s*)?(?P<w1>" + _OPWORD + r")\s*\}?\s*\}"
    r"|\\(?P<w2>min|max)(?![A-Za-z])\s*(?:\\limits)?\s*(?:_\{" + _GROUP + r"\})?"
    r"|\\(?:text|mathrm|mathit|mathbf|operatorname|textbf)\s*\{\s*(?P<w3>"
    + _OPWORD
    + r")\s*:?\s*\}"
    r"\s*:?\s*(?:_\{" + _GROUP + r"\})?"
    r"|\\mathit\s*\{\s*(?P<w4>min|max)(?P<glued>[A-Z]\w*)\s*\}"
    r"|(?<![A-Za-z\\_])(?P<w5>" + _OPWORD + r")(?![A-Za-z])\s*:?\s*(?:_\{" + _GROUP + r"\})?"
    r"|(?<![A-Za-z\\_])(?P<w6>m\s+i\s+n|m\s+a\s+x)(?![A-Za-z])",
    re.IGNORECASE,
)
_SUBJECT_TO = re.compile(
    r"(?:\\(?:text|mathrm|mathbf|textrm)\s*\{\s*)?(?<![A-Za-z])s\s*\}?\s*\.\s*"
    r"(?:\\(?:text|mathrm|mathbf|textrm)\s*\{\s*)?t\s*\}?\s*\.?"
    r"|(?:\\(?:text|mathrm|mathbf)\s*\{\s*)?subject\s*\}?\s*(?:\\(?:text|mathrm|mathbf)\s*\{\s*)?to\b",
    re.IGNORECASE,
)
_SCRIPT = r"(?:\s*[_^]\s*(?:\{" + _GROUP + r"\}|[A-Za-z0-9*']))"
_SYMBOL = (
    r"(?:\\(?:mathit|mathcal|mathbf|mathrm|text)\s*\{\s*(?:\\mathcal\{)?[A-Za-z][A-Za-z0-9 ]*\}?\s*\}"
    r"|\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]*(?:\s[a-z])*)"
)
_LABEL_EQ = re.compile(
    r"^(?P<sym>" + _SYMBOL + r")(?P<scripts>" + _SCRIPT + r"*)"
    r"(?:\s*(?:\\left)?\(\s*[^()=]*\s*(?:\\right)?\))?\s*=\s*"
)
_PREFIX_ITEMS = (
    re.compile(r"^(?:\\left)?\(\s*[^()=+]{0,40}\s*(?:\\right)?\)" + _SCRIPT + r"*\s*:?\s*"),
    re.compile(r"^(?:\\left)?\[\s*[^\[\]=+]{0,40}\s*(?:\\right)?\]" + _SCRIPT + r"*\s*:?\s*"),
    re.compile(r"^\\(?:text|mathbf|mathrm|textbf|mathit)\s*\{[^{}=+]{0,40}\}\s*:?\s*"),
    re.compile(r"^(?:\\left\s*\\\{\s*(?:\\right\s*\.)?|\\\{|\\left\s*\.|\\rightarrow)\s*"),
    re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ]{0,30}:\s*"),
)
_LIST_ARG = re.compile(r"^(?:\\left\s*)?(\\\{|\(|\[|\{)")


def _is_list(rest: str) -> bool:
    """``rest`` opens with a bracketed group holding a top-level comma: the
    operator is the function ``max \\{a, b\\}``, not an objective."""
    m = _LIST_ARG.match(rest)
    if not m:
        return False
    depth = 0
    i = m.start(1)
    while i < len(rest):
        if rest.startswith(("\\{", "\\}"), i):
            depth += 1 if rest[i + 1] == "{" else -1
            i += 2
            if depth == 0:
                return False
            continue
        ch = rest[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return False
        elif ch == "," and depth == 1:
            return True
        i += 1
    return False


def _has_index_script(scripts: str) -> bool:
    """A script made of single lower-case letters marks an indexed quantity
    (``h_{i j s}``), not a scalar objective label (``Z_{1}``, ``J^{low}``)."""
    for group in re.findall(r"[_^]\s*(?:\{(" + _GROUP + r")\}|([A-Za-z0-9]))", scripts):
        inner = group[0] or group[1]
        toks = re.findall(r"[A-Za-z]+|\d+", re.sub(r"\\[A-Za-z]+", " ", inner))
        if any(len(t) == 1 and t.islower() for t in toks):
            return True
    return False


def objective_split(latex: str) -> tuple[str, str] | None:
    """``(sense, expression)`` when ``latex`` states an objective, else ``None``."""
    s = " ".join(_WRAPPERS.sub(" ", latex).split())
    m = _OP.search(s)
    if m is None:
        return None
    word = next(m.group(g) for g in ("w1", "w2", "w3", "w4", "w5", "w6") if m.group(g))
    sense = "min" if word.replace(" ", "").lower().startswith("min") else "max"
    prefix, rest = s[: m.start()].strip(), s[m.end() :].strip()
    if m.group("glued"):
        rest = m.group("glued") + " " + rest
    labelled = False
    # the prefix must be label material only
    scalar_label = True
    while prefix:
        lm = _LABEL_EQ.match(prefix)
        if lm is not None:
            labelled = True
            if _has_index_script(lm.group("scripts") or ""):
                scalar_label = False
            prefix = prefix[lm.end() :].strip()
            continue
        for rx in _PREFIX_ITEMS:
            pm = rx.match(prefix)
            if pm is not None and pm.end() > 0:
                prefix = prefix[pm.end() :].strip()
                break
        else:
            return None
    if not scalar_label or not rest:
        return None
    if labelled and r"\in" in m.group(0):
        return None  # LABEL = max_{i \in I} ...: an aggregation that defines the label
    cut = _SUBJECT_TO.search(rest)
    if cut is not None:
        rest = rest[: cut.start()].strip()
    if r"\forall" in rest:
        return None  # a quantified row defines a quantity per index, it is no objective
    rest = re.sub(r"(?:\\right\s*\.|\\left\s*\.|[\s.,;:])+$", "", rest)
    if not rest or _is_list(rest):
        return None
    # "min T = min (T_1 + T_2)": a repeated operator after a label says nothing new
    rest = re.sub(
        r"(=\s*)(?:\\(?:min|max)(?![A-Za-z])|(?<![A-Za-z\\_])(?:"
        + _OPWORD
        + r"|m\s+i\s+n|m\s+a\s+x)(?![A-Za-z]))\s*",
        r"\1",
        rest,
        flags=re.IGNORECASE,
    )
    return sense, rest


def _squeeze(text: str) -> str:
    text = re.sub(r"\\(?:text|mathrm|mathit|mathbf|mathcal)\s*\{([^{}]*)\}", r"\1", text)
    return re.sub(r"[\s{}]|\\left|\\right", "", text)


def resolve_objectives(texts: list[str]) -> list[tuple[bool, str]]:
    r"""Which of several objective statements is the model's objective.

    ``texts`` are the rows the detector calls objectives. Returned per row:
    ``(is_objective, latex)``. Two rules, both read off the rows themselves:

    * a later row that states the same expression as an earlier one is a
      restatement (``(M0) min Z`` after ``min Z``): it stops being an
      objective, its text is kept;
    * when exactly one row's expression names the label of every other row
      (``min Z = \alpha_1 Z_1 + \alpha_2 Z_2`` next to ``min Z_1 = ...`` and
      ``min Z_2 = ...``, or ``min Z`` next to ``min Z = ...``), the others
      define those labels: each becomes the equation ``label = expression``.

    Rows the head grammar cannot read, and papers with several unrelated
    objectives, come back unchanged.
    """
    out = [(True, t) for t in texts]
    heads = [objective_split(t) for t in texts]
    seen: dict[str, int] = {}
    live: list[int] = []
    for k, head in enumerate(heads):
        if head is None:
            live.append(k)
            continue
        key = head[0] + ":" + _squeeze(head[1])  # min f and max f are two objectives
        if key in seen:
            out[k] = (False, texts[k])
            continue
        seen[key] = k
        live.append(k)
    if len(live) < 2 or any(heads[k] is None for k in live):
        return out
    labels: dict[int, str] = {}
    exprs: dict[int, str] = {}
    for k in live:
        rest = heads[k][1]  # type: ignore[index]
        lm = _LABEL_EQ.match(rest)
        labels[k] = _squeeze(rest[: lm.end()].rstrip().rstrip("=")) if lm else ""
        exprs[k] = rest[lm.end() :] if lm else rest

    def tokens(text: str) -> set[str]:
        # symbols with their scripts, compared in the squeezed spelling
        return {
            _squeeze(t)
            for t in re.findall(
                r"(?:\\[A-Za-z]+\s*\{[^{}]*\}|\\[A-Za-z]+|[A-Za-z][A-Za-z0-9]*)"
                r"(?:\s*[_^]\s*(?:\{[^{}]*\}|[A-Za-z0-9]))*",
                text,
            )
        }

    toks = {k: tokens(exprs[k]) for k in live}

    def names(k: int, other: int) -> bool:
        return bool(labels[other]) and labels[other] in toks[k]

    masters = [k for k in live if all(names(k, o) for o in live if o != k)]
    if len(masters) != 1:
        return out
    master = masters[0]
    for k in live:
        if k != master:
            out[k] = (False, heads[k][1])  # type: ignore[index]
    return out
