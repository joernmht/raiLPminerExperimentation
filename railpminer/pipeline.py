"""The end-to-end LP-mining pipeline.

Runs the six forward stages plus the a-posteriori validation loop and writes
the paper's artifacts to ``output_dir``:

    corpus  ->  taxonomy  ->  labels  ->  dataset + taxonomy tables  ->  validation

Each stage lives in its own module; this file is the deterministic glue. One
call to :func:`run` reproduces the whole experiment.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from lp2graph.mining import versions as _versions
from lp2graph.mining.cluster import Taxonomy

from . import (
    _lp2graph,  # noqa: F401
    clustering,
    dataset,
    labeling,
    taxonomy_export,
    validation,
)
from .clustering import StabilitySummary
from .config import PipelineConfig
from .corpus import LoadedCorpus, load_corpus
from .labeling import LabelingResult
from .validation import ValidationReport


@dataclass(frozen=True, slots=True)
class MiningResult:
    """Everything one run produces, in memory."""

    config: PipelineConfig
    corpus: LoadedCorpus
    taxonomy: Taxonomy
    silhouettes: dict[str, float]
    stability: StabilitySummary
    labels: LabelingResult
    validation: ValidationReport
    dataset: dict[str, Any]
    taxonomy_artifact: dict[str, Any]

    def summary(self) -> dict[str, Any]:
        """A compact, human-readable digest of the run."""
        return {
            "n_formulations": len(self.corpus),
            "load_failures": len(self.corpus.load_failures),
            "cluster_counts": self.taxonomy.summary(),
            "silhouettes": {k: round(v, 3) for k, v in self.silhouettes.items()},
            "stability_ari_min": {
                k: round(v, 3) for k, v in self.stability.ari_min_by_level.items()
            },
            "structural_fidelity_pass_rate": round(self.validation.structural_pass_rate, 3),
            "external_fidelity": [
                {
                    "formulation": e.formulation_id,
                    "cross_solver_agree": e.cross_solver_agree,
                    "matches_expected": e.matches_expected,
                }
                for e in self.validation.external
            ],
            "solvers_used": list(self.validation.solvers_used),
            "versions": _versioned_resources(),
        }


def _versioned_resources() -> dict[str, str]:
    return {
        "lexicon": _versions.LEXICON_VERSION,
        "thesaurus": _versions.THESAURUS_VERSION,
        "vocabulary": _versions.VOCABULARY_VERSION,
        "clustering": _versions.CLUSTERING_VERSION,
        "label_lexicon": _versions.LABEL_LEXICON_VERSION,
        "rewrite_rules": _versions.REWRITE_RULES_VERSION,
    }


def _write_json(path: Path, obj: Any) -> None:
    path.write_text(json.dumps(obj, indent=2, default=str) + "\n")


def run(config: PipelineConfig | None = None, *, write: bool = True) -> MiningResult:
    """Run the full pipeline and (optionally) write artifacts to ``output_dir``."""
    config = config or PipelineConfig()

    # 1. Corpus
    corpus = load_corpus(config)
    formulations = list(corpus.formulations)

    # 3-4. Feature construction + multi-level clustering
    tax = clustering.build_taxonomy(formulations, config)
    silh = clustering.silhouettes(tax)
    stab = clustering.stability(formulations, config, reference=tax)

    # 5. Labeling
    labels = labeling.run_labeling(formulations, config)

    # 7. Validation
    valid = validation.run_validation(corpus, tax, config)

    # 6. Outputs
    ds = dataset.build_dataset(corpus, tax, labels, valid)
    tax_artifact = taxonomy_export.build_taxonomy(tax, labels)

    result = MiningResult(
        config=config,
        corpus=corpus,
        taxonomy=tax,
        silhouettes=silh,
        stability=stab,
        labels=labels,
        validation=valid,
        dataset=ds,
        taxonomy_artifact=tax_artifact,
    )

    if write:
        out = config.output_dir
        out.mkdir(parents=True, exist_ok=True)
        _write_json(out / "dataset.json", ds)
        _write_json(out / "taxonomy.json", tax_artifact)
        (out / "taxonomy.csv").write_text(taxonomy_export.to_csv(tax_artifact["axes"]))
        (out / "taxonomy_axes.tex").write_text(taxonomy_export.to_latex(tax_artifact["axes"]))
        _write_json(
            out / "clustering_report.json",
            {
                "cluster_counts": tax.summary(),
                "silhouettes": silh,
                "stability": asdict(stab),
            },
        )
        _write_json(
            out / "validation_report.json",
            {
                "solvers_used": list(valid.solvers_used),
                "structural_pass_rate": valid.structural_pass_rate,
                "structural": [asdict(s) for s in valid.structural],
                "external": [asdict(e) for e in valid.external],
                "isomorphism": [asdict(i) for i in valid.isomorphism],
                "representatives": [asdict(r) for r in valid.representatives],
                "notes": list(valid.notes),
            },
        )
        _write_json(out / "run_summary.json", result.summary())

    return result


__all__ = ["MiningResult", "run"]
