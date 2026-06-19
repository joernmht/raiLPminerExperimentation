"""Deterministic MathML → LaTeX via the vendored node ``mathml-to-latex`` bridge.

Presentation MathML (e.g. Elsevier ``<ce:formula>`` bodies) has no LaTeX source,
so we convert it. The conversion is a pure function of the input — no model, no
randomness — so it preserves the determinism contract. Display equations convert
cleanly; the occasional inline text-identifier garbles and is flagged for the
human review step rather than silently trusted.

Setup: ``npm install`` inside ``corpusbuilder/_mathml2latex`` (committed
``package.json``; ``node_modules`` is gitignored).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_BRIDGE = Path(__file__).resolve().parent / "_mathml2latex"
_CONVERT = _BRIDGE / "convert.js"
_MATHML_NS = "http://www.w3.org/1998/Math/MathML"


class MathMLConversionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Converted:
    ok: bool
    latex: str
    error: str | None = None


def _normalize(mathml: str) -> str:
    """Drop the ``mml:`` prefix and ensure a default MathML namespace."""
    s = re.sub(r"\bmml:", "", mathml)
    s = re.sub(r'\sxmlns:mml="[^"]*"', "", s)
    if "xmlns" not in s:
        s = s.replace("<math", f'<math xmlns="{_MATHML_NS}"', 1)
    return s


def mathml_to_latex(mathml_list: list[str], timeout: float = 120.0) -> list[Converted]:
    """Convert a list of MathML strings to LaTeX, preserving order.

    Each result carries ``ok``; failed conversions keep ``latex=""`` and an
    ``error`` so the caller can flag them for review instead of dropping them.
    """
    if not mathml_list:
        return []
    if shutil.which("node") is None:
        raise MathMLConversionError("`node` not found on PATH; install Node.js")
    if not (_BRIDGE / "node_modules").exists():
        raise MathMLConversionError(
            f"mathml-to-latex not installed; run `npm install` in {_BRIDGE}"
        )
    payload = json.dumps([_normalize(m) for m in mathml_list])
    proc = subprocess.run(
        ["node", str(_CONVERT)],
        input=payload,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise MathMLConversionError(f"node bridge failed: {proc.stderr[:300]}")
    try:
        raw = json.loads(proc.stdout)
    except json.JSONDecodeError as e:  # pragma: no cover - defensive
        raise MathMLConversionError(f"node bridge returned non-JSON: {e}") from e
    return [Converted(ok=bool(r.get("ok")), latex=r.get("latex") or "", error=r.get("error")) for r in raw]
