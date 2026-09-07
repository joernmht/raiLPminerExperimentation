"""Make the ``lp2graph`` library importable.

``lp2graph`` is the deterministic core this experiment harness orchestrates.
In most setups it is ``pip install``-ed and this module does nothing. During
local development it usually lives as a *sibling* checkout that is run with
``PYTHONPATH=src`` rather than installed (see the workspace ``CLAUDE.md``).

To keep the harness runnable out of the box in that layout, importing this
module adds a sibling ``lp2graph/src`` to ``sys.path`` *only* when the package
cannot already be imported. An installed ``lp2graph`` always wins; the fallback
never shadows it.

The search walks *up* from the repository root rather than probing two fixed
relative levels, because the checkout is not always a direct sibling of
``lp2graph``: a ``git worktree`` (the nightly quality loop uses
``~/.claude/loops/worktrees/<repo>``) or a CI layout that nests the checkout
puts several directories between the two. Ancestors are searched nearest-first
and the walk is bounded (:data:`_MAX_ANCESTORS`) so it cannot wander up to
``/`` and pick up an unrelated tree.

Set ``LP2GRAPH_SRC`` to point at a specific ``lp2graph/src`` directory to
override the search.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _already_importable() -> bool:
    return importlib.util.find_spec("lp2graph") is not None


#: How far above the repository root to look for a ``lp2graph`` checkout.
#: 5 covers a direct sibling (``../lp2graph``) through a worktree nested four
#: deep, without reaching into unrelated parts of the filesystem.
_MAX_ANCESTORS = 5


def _candidate_src_dirs() -> list[Path]:
    """Plausible ``lp2graph/src`` locations, most specific first."""
    candidates: list[Path] = []
    env = os.environ.get("LP2GRAPH_SRC")
    if env:
        candidates.append(Path(env))
    here = Path(__file__).resolve()
    # repo root is the parent of the ``railpminer`` package directory
    repo_root = here.parent.parent
    # Nearest ancestor first: a direct sibling wins over a distant namesake.
    for ancestor in list(repo_root.parents)[:_MAX_ANCESTORS]:
        candidates.append(ancestor / "lp2graph" / "src")
    return candidates


def ensure_importable() -> Path | None:
    """Ensure ``lp2graph`` can be imported; return the path used, if any.

    Returns the ``src`` directory added to ``sys.path`` when the sibling
    fallback was needed, ``None`` when ``lp2graph`` was already importable,
    and raises :class:`ModuleNotFoundError` when neither works.
    """
    if _already_importable():
        return None
    for src in _candidate_src_dirs():
        if (src / "lp2graph" / "__init__.py").exists():
            sys.path.insert(0, str(src))
            if _already_importable():
                return src
    raise ModuleNotFoundError(
        "Could not import 'lp2graph'. Install it (`pip install lp2graph`) or set "
        "LP2GRAPH_SRC to a checkout's 'src' directory. Looked in: "
        + ", ".join(str(p) for p in _candidate_src_dirs())
    )


# Run the side effect on import so `import railpminer` just works.
LP2GRAPH_SRC: Path | None = ensure_importable()
