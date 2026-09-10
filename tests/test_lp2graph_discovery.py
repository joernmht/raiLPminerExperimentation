"""The sibling-``lp2graph`` fallback must survive a relocated checkout.

Co-existence (ISO/IEC 25010 -> compatibility). ``railpminer`` runs alongside
other checkouts of the same repositories: the paper sessions use
``~/<repo>``, the nightly loops use ``git worktree`` trees under
``~/.claude/loops/worktrees/<repo>``, and CI nests the checkout under
``main/``. Probing a fixed number of relative levels for ``../lp2graph/src``
worked only for the direct-sibling layout — in a worktree the import of
``railpminer`` itself raised ``ModuleNotFoundError``.
"""

from __future__ import annotations

from pathlib import Path

from railpminer import _lp2graph


def test_env_override_is_searched_first(monkeypatch) -> None:
    monkeypatch.setenv("LP2GRAPH_SRC", "/somewhere/lp2graph/src")
    assert _lp2graph._candidate_src_dirs()[0] == Path("/somewhere/lp2graph/src")


def test_candidates_walk_up_from_the_repo_root(monkeypatch) -> None:
    monkeypatch.delenv("LP2GRAPH_SRC", raising=False)
    candidates = _lp2graph._candidate_src_dirs()
    repo_root = Path(_lp2graph.__file__).resolve().parent.parent

    # Every candidate is `<some ancestor of the repo>/lp2graph/src` ...
    ancestors = list(repo_root.parents)
    for candidate in candidates:
        assert candidate.name == "src"
        assert candidate.parent.name == "lp2graph"
        assert candidate.parent.parent in ancestors

    # ... the direct sibling comes first (nearest ancestor wins) ...
    assert candidates[0] == repo_root.parent / "lp2graph" / "src"

    # ... and the walk is bounded, so it cannot reach an unrelated tree at `/`.
    assert len(candidates) == min(_lp2graph._MAX_ANCESTORS, len(ancestors))


def test_the_search_reaches_far_enough_for_the_worktree_layout() -> None:
    """``~/.claude/loops/worktrees/<repo>`` sits four levels below ``~/lp2graph``.

    That is the layout the nightly loops actually use, so anything less than
    four ancestors reintroduces the ``ModuleNotFoundError`` this fixes.
    """
    assert _lp2graph._MAX_ANCESTORS >= 4
