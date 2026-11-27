"""Publish the public copy of this repository: code plus the example dataset.

The working repository is PRIVATE. It holds corpus material derived from
papers that are not openly licensed (their formulas, symbol tables, canonical
models, decisions). None of that may be public. The public repository
``joernmht/raiLPminerExperimentation`` is a *filtered export* of the private
history: the same commits with every such file removed, in every commit, on
every branch.

What stays public

* all code, tests and documentation;
* corpus-wide reports that hold counts and bibliographic identifiers only;
* per-paper files of the papers listed in ``corpus/public_keys.txt``: papers
  whose publisher version is CC BY according to two sources (OpenAlex and
  Crossref). They are the public example dataset (``corpus/PUBLIC.md``).

What is removed (:func:`rules`)

* per-paper files of every other paper, in every per-paper directory;
* corpus-wide reports that carry formulas or symbols of those papers;
* the 2025 pilot result tables, whose prompts hold input-paper text.

The export is deterministic: the same private history and the same key list
give the same public commit ids, so a publish after new private commits is a
fast-forward. Changing the key list or the rules rewrites the public history
from the first commit they touch, and the push must then be forced.

    python3 scripts/publish_public.py --check            # filter + verify, push nothing
    python3 scripts/publish_public.py --push             # fast-forward the public repo
    python3 scripts/publish_public.py --push --force     # after a change of rules or keys

Needs ``git-filter-repo`` (``git_filter_repo.py``; pass ``--filter-repo PATH``
or set ``GIT_FILTER_REPO``). Never push this repository to the public remote
by hand.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PRIVATE = "git@github.com:joernmht/raiLPminerExperimentation-private.git"
PUBLIC = "git@github.com:joernmht/raiLPminerExperimentation.git"
KEYS = REPO / "corpus" / "public_keys.txt"

#: Directories that hold one file (or folder) per paper, named by its key.
PER_PAPER = (
    "dossiers",
    "decisions",
    "declarations",
    "promoted",
    "formulations",
    "provenance",
    "vocab",
    "vdemo2",
    "vdemo3",
    "vdemo3_retry",
)

#: Corpus-wide files that carry formulas or symbols of papers outside the
#: public set, and the 2025 pilot tables (prompts with input-paper text).
WHOLE_FILES = (
    "corpus/promotion.json",
    "corpus/promotion.md",
    "corpus/promotion.baseline-2026-08-27.json",
    "corpus/objective_flags.json",
    "corpus/detdecl.json",
    "corpus/indices.json",
    "corpus/declare.json",
    "corpus/indexrules.json",
    "corpus/indexassist.json",
    "corpus/testsets/formula_split_labels.json",
    "experiment_results_metrics.csv",
    "experiment_results_metrics_corrected.csv",
    "experiment_results_metrics_corrected_selected.csv",
    "experiment_results_metrics_corrected_selected_fixed.csv",
    "experiment_results_with_classifications.csv",
    "filtered_output.csv",
)

#: Branches of the private repository that are published.
BRANCHES = (
    "main",
    "legacy/raiLPminer-2025",
    "loop/quality-style",
    "claude/corpus-extraction-pipeline-visual-lbk8rd",
    "claude/refactor-railway-validation-0SU6M",
)
TAGS = ("raiLPminer-v1-2025",)


def public_keys() -> list[str]:
    lines = KEYS.read_text(encoding="utf-8").splitlines()
    return sorted(ln.strip() for ln in lines if ln.strip() and not ln.startswith("#"))


def rules(keys: list[str]) -> list[str]:
    """The ``git filter-repo --invert-paths`` rules: what leaves the history."""
    allowed = "|".join(re.escape(k) for k in keys)
    per_paper = (
        r"regex:^corpus/(?:" + "|".join(PER_PAPER) + r")/(?:assist_)?"
        r"(?!(?:" + allowed + r")(?:\.|--|/|$))10\.\d"
    )
    return [per_paper, *WHOLE_FILES]


def run(*cmd: str, cwd: Path | None = None) -> str:
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def leftovers(repo: Path, keys: list[str]) -> list[str]:
    """Paths anywhere in the filtered history that the rules should have removed."""
    paths = set(run("git", "log", "--all", "--name-only", "--pretty=format:", cwd=repo).split("\n"))
    per_paper = re.compile(r"^corpus/(?:" + "|".join(PER_PAPER) + r")/(?:assist_)?(10\.\d[^/]*)")
    suffix = re.compile(r"(?:\.rewrites\.json|\.stub\.tex|\.json|\.md|\.tex|--.*)$")
    bad = []
    for path in sorted(p for p in paths if p):
        m = per_paper.match(path)
        if (m and suffix.sub("", m.group(1)) not in keys) or path in WHOLE_FILES:
            bad.append(path)
    return bad


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--check", action="store_true", help="filter and verify, push nothing")
    ap.add_argument(
        "--push", action="store_true", help="push the filtered history to the public repo"
    )
    ap.add_argument("--force", action="store_true", help="allow a non-fast-forward push")
    ap.add_argument("--filter-repo", default=os.environ.get("GIT_FILTER_REPO", ""))
    ap.add_argument("--keep", type=Path, help="keep the filtered bare repository here")
    args = ap.parse_args(argv)
    if not (args.check or args.push):
        ap.error("say --check or --push")
    tool = [sys.executable, args.filter_repo] if args.filter_repo else ["git", "filter-repo"]
    keys = public_keys()
    with tempfile.TemporaryDirectory(prefix="publish-public-") as tmp:
        work = Path(tmp) / "export.git"
        run("git", "clone", "--mirror", PRIVATE, str(work))
        wanted = {f"refs/heads/{b}" for b in BRANCHES} | {f"refs/tags/{t}" for t in TAGS}
        for ref in run("git", "for-each-ref", "--format=%(refname)", cwd=work).split():
            if ref not in wanted:
                run("git", "update-ref", "-d", ref, cwd=work)
        rules_file = Path(tmp) / "rules.txt"
        rules_file.write_text("\n".join(rules(keys)) + "\n", encoding="utf-8", newline="\n")
        run(*tool, "--invert-paths", "--paths-from-file", str(rules_file), "--force", cwd=work)
        bad = leftovers(work, set(keys))
        if bad:
            print(f"REFUSED: {len(bad)} path(s) of non-public papers survive, e.g. {bad[:5]}")
            return 1
        heads = run(
            "git", "for-each-ref", "--format=%(refname:short) %(objectname:short)", cwd=work
        )
        print(f"filtered history verified ({len(keys)} public papers):\n{heads}", end="")
        if args.keep:
            shutil.copytree(work, args.keep)
        if args.push:
            refs = [f"refs/heads/{b}:refs/heads/{b}" for b in BRANCHES]
            refs += [f"refs/tags/{t}:refs/tags/{t}" for t in TAGS]
            subprocess.run(
                ["git", "push", *(["--force"] if args.force else []), PUBLIC, *refs],
                cwd=work,
                check=True,
            )
            print("pushed to the public repository")
    return 0


if __name__ == "__main__":
    sys.exit(main())
