"""Guard: text I/O declares ``encoding="utf-8"``, and text writes declare ``newline="\\n"``.

Compatibility (ISO/IEC 25010 -> interoperability). ``open()``,
``Path.read_text()`` and ``Path.write_text()`` default to the *locale*
encoding. On Linux that is UTF-8 in practice (PEP 538/540 coerce the C
locale), so the omission is invisible here — but the corpus is an
interchange artifact: it ships on GitHub/Zenodo and is read by third
parties. Under a cp1252 default (the Windows norm) a read silently
*mojibakes* every non-ASCII string and a write raises ``UnicodeEncodeError``
for characters cp1252 cannot represent. See ADR-0016.

The same argument applies to ``newline``. A text write with the default
``newline=None`` translates every ``"\\n"`` to ``os.linesep``, so on Windows
the emitted artifacts are CRLF — which silently breaks this repo's central
determinism claim ("same corpus + same versioned config => byte-identical
artifacts", CLAUDE.md). Writes in the producing packages therefore pin
``newline="\\n"``; tests are exempt (they compare in-process, not on the wire).

This is an AST check rather than a lint rule because ruff's preview
``PLW1514``/``FURB`` encoding rules are not part of the shared toolchain
(``~/.claude/quality/STYLE.md`` §1 pins ruff's stable E/F/W/I/UP/B/SIM/RUF set).
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Packages and script trees whose text I/O must be explicit.
CHECKED_TREES = ("railpminer", "corpusbuilder", "scripts", "tests")

#: Trees that emit on-disk artifacts, so their writes must also pin the newline.
PRODUCER_TREES = ("railpminer", "corpusbuilder", "scripts")

#: ``pathlib`` text helpers; both take an ``encoding`` keyword.
_TEXT_METHODS = frozenset({"read_text", "write_text"})


def _mode_of(call: ast.Call) -> str:
    """The literal ``mode`` argument of an ``open()`` call, if it is a constant."""
    if len(call.args) >= 2 and isinstance(call.args[1], ast.Constant):
        return str(call.args[1].value)
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return ""


def offenders(source: str, label: str) -> list[str]:
    """Text-I/O calls in ``source`` that do not pass ``encoding=``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=label)):
        if not isinstance(node, ast.Call):
            continue
        if any(kw.arg == "encoding" for kw in node.keywords):
            continue
        # `Path.read_text()` / `Path.write_text()` — attribute form only.
        if isinstance(node.func, ast.Attribute) and node.func.attr in _TEXT_METHODS:
            found.append(f"{label}:{node.lineno} {node.func.attr}()")
        # Builtin `open()` — the bare name only, so `tarfile.open` / `Image.open`
        # (different signatures, no `encoding`) are not swept up.
        elif (
            isinstance(node.func, ast.Name) and node.func.id == "open" and "b" not in _mode_of(node)
        ):
            found.append(f"{label}:{node.lineno} open()")
    return found


def test_every_text_io_call_declares_utf8() -> None:
    found: list[str] = []
    for tree in CHECKED_TREES:
        for path in sorted((REPO_ROOT / tree).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            found += offenders(path.read_text(encoding="utf-8"), str(rel))
    assert found == [], (
        "text I/O without an explicit encoding (see ADR-0016); "
        'add encoding="utf-8":\n  ' + "\n  ".join(found)
    )


def test_the_guard_sees_planted_violations() -> None:
    """A green run means something only if the check can still fail."""
    src = "from pathlib import Path\nPath('a').read_text()\nPath('b').write_text('x')\nopen('c')\n"
    assert len(offenders(src, "planted.py")) == 3


def newline_offenders(source: str, label: str) -> list[str]:
    """Text *writes* in ``source`` that do not pin ``newline="\\n"``."""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=label)):
        if not isinstance(node, ast.Call):
            continue
        if any(kw.arg == "newline" for kw in node.keywords):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr == "write_text":
            found.append(f"{label}:{node.lineno} write_text()")
        elif func.attr == "open":
            mode = (
                node.args[0].value if node.args and isinstance(node.args[0], ast.Constant) else ""
            )
            if isinstance(mode, str) and "b" not in mode and any(c in mode for c in "wax"):
                found.append(f"{label}:{node.lineno} open({mode!r})")
    return found


def test_every_artifact_write_pins_lf() -> None:
    found: list[str] = []
    for tree in PRODUCER_TREES:
        for path in sorted((REPO_ROOT / tree).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT)
            found += newline_offenders(path.read_text(encoding="utf-8"), str(rel))
    assert found == [], (
        "text write without an explicit newline (see ADR-0016); artifacts would be "
        'CRLF on Windows. Add newline="\\n":\n  ' + "\n  ".join(found)
    )


def test_the_newline_guard_sees_planted_violations() -> None:
    src = (
        "from pathlib import Path\n"
        "Path('a').write_text('x')\n"
        "Path('b').open('w')\n"
        "Path('c').write_text('x', newline='\\n')\n"
        "Path('d').open('rb')\n"
        "Path('e').read_text()\n"
    )
    assert len(newline_offenders(src, "planted.py")) == 2


def test_the_guard_accepts_declared_and_binary_calls() -> None:
    src = (
        "import tarfile\n"
        "from pathlib import Path\n"
        "Path('a').read_text(encoding='utf-8')\n"
        "open('b', 'rb')\n"
        "open('c', mode='wb')\n"
        "tarfile.open('d')\n"
    )
    assert offenders(src, "planted.py") == []
