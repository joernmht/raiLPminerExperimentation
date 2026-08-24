"""Declared-symbol algebra normalization: juxtaposition, resolved deterministically.

Tier-2 MathML writes products by juxtaposition (``q \\pi_{ij} x_{ij}``) and
multi-letter identifiers as spaced letters (``t r_{e}`` meaning ``tr_e``); the
canonical grammar wants ``\\cdot`` products and single identifier tokens.
Deciding which is which needs to know what the paper's symbols ARE — exactly
what the declaration sidecar states. Given the declared names, both rewrites
are deterministic:

* **merge**: a run of spaced single letters that spells a declared multi-char
  name collapses to ``\\mathit{name}`` (longest declared match wins);
* **product**: two adjacent atoms whose base names are both declared (or a
  number followed by a declared name) get an explicit ``\\cdot``.

Adjacency inside script groups (``_{...}``/``^{...}``) is index context, not
multiplication, and is never touched. Undeclared adjacency is left alone: this
module only spends knowledge the sidecar actually states (assisted-resolution
rung (c) supplies the rest).
"""

from __future__ import annotations

import re

_SCRIPT_HEAD = re.compile(r"[_^]")
_PLACEHOLDER = "\x00{}\x00"

#: An atom: a number, a \mathit{name}, or a letter run — with masked scripts
#: (placeholders) allowed to trail it.
_ATOM = (
    r"(?:\d+(?:\.\d+)?|\\mathit\{[A-Za-z_\\]+\}|[A-Za-z]+)"
    r"(?:\x00\d+\x00|[_^][A-Za-z0-9])*"
)
# The lookbehind stops an atom from starting inside a macro (\cdot, \min:
# their letters match the atom pattern, and a failed match starting there
# CONSUMES the true adjacency to its right) or mid-identifier.
_ADJ = re.compile(rf"(?<![\\A-Za-z])(?P<a>{_ATOM})(?P<sp>[ \t]+)(?P<b>{_ATOM})")
_BASE = re.compile(r"^(?:\\mathit\{([A-Za-z_\\]+)\}|(\d+(?:\.\d+)?)|([A-Za-z]+))")


def _mask_scripts(latex: str) -> tuple[str, list[str]]:
    """Replace every ``_{...}``/``^{...}`` group with a placeholder."""
    out: list[str] = []
    stash: list[str] = []
    i = 0
    while i < len(latex):
        ch = latex[i]
        if ch in "_^" and i + 1 < len(latex) and latex[i + 1] == "{":
            depth = 0
            j = i + 1
            while j < len(latex):
                if latex[j] == "{":
                    depth += 1
                elif latex[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            stash.append(latex[i : j + 1])
            out.append(_PLACEHOLDER.format(len(stash) - 1))
            i = j + 1
        else:
            out.append(ch)
            i += 1
    return "".join(out), stash


def _unmask(latex: str, stash: list[str]) -> str:
    for k, group in enumerate(stash):
        latex = latex.replace(_PLACEHOLDER.format(k), group)
    return latex


def _base_name(atom: str) -> str | None:
    """The declared-name key of an atom, or None for a number."""
    m = _BASE.match(atom)
    if not m:
        return None
    if m.group(2):
        return None  # number
    name = m.group(1) or m.group(3)
    return name.replace("\\_", "_") if name else None


def _merge_spaced_names(masked: str, names: set[str]) -> str:
    """Collapse spaced single letters that spell a declared multi-char name."""
    multi = sorted((n for n in names if len(n) > 1 and n.isalpha()), key=len, reverse=True)
    for name in multi:
        spaced = r"(?<![A-Za-z\\])" + r"\s+".join(re.escape(c) for c in name) + r"(?![A-Za-z])"
        masked = re.sub(spaced, rf"\\mathit{{{name}}}", masked)
    return masked


def declared_products(latex: str, names: set[str]) -> str:
    """Resolve juxtaposition in one row, spending only declared knowledge."""
    if not names:
        return latex
    masked, stash = _mask_scripts(latex)
    masked = _merge_spaced_names(masked, names)

    def known(atom: str) -> bool:
        base = _base_name(atom)
        return base is None or base in names  # None = number

    def declared(atom: str) -> bool:
        base = _base_name(atom)
        return base is not None and base in names

    # Fixpoint: each pass may create new adjacencies (a b c -> a·b c -> a·b·c).
    while True:
        changed = False

        def _sub(m: re.Match[str]) -> str:
            nonlocal changed
            if known(m.group("a")) and declared(m.group("b")):
                changed = True
                return m.group("a") + r" \cdot " + m.group("b")
            return m.group(0)

        masked = _ADJ.sub(_sub, masked)
        if not changed:
            break
    return _unmask(masked, stash)


def declared_names(declarations: str) -> set[str]:
    """Every index/param/var identifier a sidecar declares."""
    names: set[str] = set()
    for raw in declarations.splitlines():
        line = raw.strip()
        if not line.startswith("%@"):
            continue
        head = line[2:].strip().split()
        if len(head) >= 2 and head[0] in ("index", "param", "var"):
            names.add(head[1])
    return names
