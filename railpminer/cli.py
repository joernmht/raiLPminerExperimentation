"""Command-line interface for the LP-mining experiment.

Mirrors lp2graph's CLI conventions (``lp2graph ingest|cluster|label|...``).
Subcommands let you run a single stage for inspection, or the whole pipeline::

    python -m railpminer run                 # full pipeline -> outputs/
    python -m railpminer corpus              # load + summarize the corpus
    python -m railpminer cluster             # induce + print the taxonomy
    python -m railpminer label               # run the labeling loop
    python -m railpminer validate            # fidelity checks
    python -m railpminer taxonomy            # print the taxonomy axes table

``--corpus DIR`` and ``--output DIR`` override the defaults.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import (
    _lp2graph,  # noqa: F401
    clustering,
    labeling,
    taxonomy_export,
    validation,
)
from .config import PipelineConfig
from .corpus import load_corpus
from .pipeline import run


def _config(args: argparse.Namespace) -> PipelineConfig:
    base = PipelineConfig()
    return PipelineConfig(
        corpus_dir=Path(args.corpus) if args.corpus else base.corpus_dir,
        output_dir=Path(args.output) if args.output else base.output_dir,
        cluster=base.cluster,
        labeling=base.labeling,
        optimum_tolerance=base.optimum_tolerance,
    )


def _print(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def cmd_corpus(args: argparse.Namespace) -> int:
    corpus = load_corpus(_config(args))
    _print(
        {
            "n_formulations": len(corpus),
            "formulations": [f.id for f in corpus.formulations],
            "load_failures": [{"path": x.path, "reason": x.reason} for x in corpus.load_failures],
            "manifest": corpus.manager.manifest.to_dict(),
        }
    )
    return 0


def cmd_cluster(args: argparse.Namespace) -> int:
    cfg = _config(args)
    corpus = load_corpus(cfg)
    fs = list(corpus.formulations)
    tax = clustering.build_taxonomy(fs, cfg)
    _print(
        {
            "cluster_counts": tax.summary(),
            "silhouettes": {k: round(v, 3) for k, v in clustering.silhouettes(tax).items()},
            "names": {
                "V": list(tax.level_v.clustering.names.values()),
                "C": list(tax.level_c.clustering.names.values()),
                "M": list(tax.level_m.clustering.names.values()),
                "domain": list(tax.domain.clustering.names.values()),
                "solution_approach": list(tax.solution_approach.clustering.names.values()),
            },
        }
    )
    return 0


def cmd_label(args: argparse.Namespace) -> int:
    cfg = _config(args)
    corpus = load_corpus(cfg)
    result = labeling.run_labeling(list(corpus.formulations), cfg)
    _print(
        {
            dim: {
                "vocabulary": list(d.vocabulary),
                "source_counts": d.source_counts,
                "labels": [
                    {"entity": o.entity_id, "value": o.value, "source": o.source}
                    for o in d.outcomes
                ],
            }
            for dim, d in result.dimensions.items()
        }
    )
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = _config(args)
    corpus = load_corpus(cfg)
    fs = list(corpus.formulations)
    tax = clustering.build_taxonomy(fs, cfg)
    report = validation.run_validation(corpus, tax, cfg)
    _print(
        {
            "solvers_used": list(report.solvers_used),
            "structural_pass_rate": report.structural_pass_rate,
            "external": [
                {
                    "formulation": e.formulation_id,
                    "instance": e.instance,
                    "expected_optimum": e.expected_optimum,
                    "cross_solver_agree": e.cross_solver_agree,
                    "matches_expected": e.matches_expected,
                    "solvers": [
                        {"solver": s.solver, "status": s.status, "objective": s.objective}
                        for s in e.solvers
                    ],
                }
                for e in report.external
            ],
            "isomorphism": [
                {
                    "cluster": i.cluster_name,
                    "size": i.size,
                    "whole_cluster_rate": i.whole_cluster_rate,
                }
                for i in report.isomorphism
            ],
            "notes": list(report.notes),
        }
    )
    return 0


def cmd_taxonomy(args: argparse.Namespace) -> int:
    cfg = _config(args)
    corpus = load_corpus(cfg)
    fs = list(corpus.formulations)
    tax = clustering.build_taxonomy(fs, cfg)
    labels = labeling.run_labeling(fs, cfg)
    axes = taxonomy_export.build_taxonomy_axes(tax, labels)
    if args.latex:
        print(taxonomy_export.to_latex(axes))
    else:
        _print(axes)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    result = run(_config(args), write=not args.no_write)
    _print(result.summary())
    if not args.no_write:
        print(f"\nArtifacts written to {result.config.output_dir}", file=sys.stderr)
    return 0


def build_parser() -> argparse.ArgumentParser:
    # Shared options usable either before or after the subcommand, so both
    # `railpminer --output X run` and `railpminer run --output X` work.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--corpus", help="corpus directory (default: ./corpus)")
    common.add_argument("--output", help="output directory (default: ./outputs)")

    parser = argparse.ArgumentParser(
        prog="railpminer", description=__doc__.splitlines()[0], parents=[common]
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("corpus", parents=[common], help="load and summarize the corpus").set_defaults(
        func=cmd_corpus
    )
    sub.add_parser(
        "cluster", parents=[common], help="induce and print the multi-level taxonomy"
    ).set_defaults(func=cmd_cluster)
    sub.add_parser(
        "label", parents=[common], help="run the closed-loop labeling and print labels"
    ).set_defaults(func=cmd_label)
    sub.add_parser(
        "validate", parents=[common], help="run representation-fidelity validation"
    ).set_defaults(func=cmd_validate)

    p_tax = sub.add_parser("taxonomy", parents=[common], help="print the taxonomy axes table")
    p_tax.add_argument("--latex", action="store_true", help="emit a LaTeX table instead of JSON")
    p_tax.set_defaults(func=cmd_taxonomy)

    p_run = sub.add_parser(
        "run", parents=[common], help="run the full pipeline and write all artifacts"
    )
    p_run.add_argument("--no-write", action="store_true", help="compute but do not write artifacts")
    p_run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
