"""The ``railpminer`` command line: shared options reach the pipeline config.

``--corpus`` and ``--output`` are accepted before *and* after the subcommand.
Given before it, they used to be dropped without a word: the subparser re-applied
its own ``None`` default over the value the main parser had already parsed, so
``railpminer --output X run`` wrote into the repository's ``outputs/``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from railpminer import cli
from railpminer.config import REPO_ROOT


@pytest.mark.parametrize(
    "argv",
    [
        ["--corpus", "C", "--output", "O", "run"],
        ["run", "--corpus", "C", "--output", "O"],
        ["--output", "O", "run", "--corpus", "C"],
        ["--corpus", "C", "taxonomy", "--latex", "--output", "O"],
    ],
)
def test_shared_options_reach_the_config_in_any_position(argv: list[str]) -> None:
    cfg = cli._config(cli.build_parser().parse_args(argv))
    assert cfg.corpus_dir == Path("C")
    assert cfg.output_dir == Path("O")


def test_defaults_are_the_repository_directories() -> None:
    cfg = cli._config(cli.build_parser().parse_args(["run"]))
    assert cfg.corpus_dir == REPO_ROOT / "corpus"
    assert cfg.output_dir == REPO_ROOT / "outputs"


def test_main_runs_a_stage_against_the_given_corpus(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--corpus", str(REPO_ROOT / "corpus"), "corpus"]) == 0
    assert '"n_formulations"' in capsys.readouterr().out


def test_module_entry_point_prints_help() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "railpminer", "--help"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=REPO_ROOT,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "--output" in proc.stdout
