"""Stage 1 — corpus construction (M5).

Loads the on-disk corpus into a :class:`LoadedCorpus`: validated canonical
:class:`~lp2graph.core.model.Formulation` objects, their aligned
:class:`~lp2graph.mining.corpusmgr.ProvenanceRecord` metadata, the
regeneration :class:`~lp2graph.mining.corpusmgr.CorpusManifest`, and a
:class:`~lp2graph.mining.corpusmgr.CorpusManager` tying them together.

The on-disk layout (see ``corpus/``)::

    corpus/
      manifest.json            queries + frozen search date (regeneration record)
      formulations/<id>.json   one validated canonical LP2Graph model per entry
      provenance/<id>.json     one ProvenanceRecord per formulation, matched by id
      instances/*.json         validation instances (consumed by the validation stage)

Every formulation is loaded through lp2graph's two-phase validator, so a model
that does not validate is *reported* (in :attr:`LoadedCorpus.load_failures`),
never silently dropped — the M1/M5 honesty invariant.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from lp2graph import load
from lp2graph.core.model import Formulation
from lp2graph.mining.corpusmgr import (
    CorpusManager,
    CorpusManifest,
    ProvenanceRecord,
)

from . import _lp2graph  # noqa: F401
from .config import PipelineConfig

#: The fields lp2graph's ProvenanceRecord accepts; anything else in the JSON
#: (documentation keys, etc.) is ignored rather than crashing the loader.
_PROV_FIELDS = (
    "source_id",
    "venue",
    "quality_tier",
    "year",
    "citation_count",
    "domain_shell",
    "activity",
    "priority_cell",
)


@dataclass(frozen=True, slots=True)
class LoadFailure:
    """A corpus entry that could not be loaded, with the reason why."""

    path: str
    reason: str


@dataclass(frozen=True, slots=True)
class LoadedCorpus:
    """A validated corpus ready for the rest of the pipeline."""

    manager: CorpusManager
    load_failures: tuple[LoadFailure, ...]

    @property
    def formulations(self) -> tuple[Formulation, ...]:
        return tuple(f for f, _ in self.manager.entries)

    @property
    def records(self) -> tuple[ProvenanceRecord, ...]:
        return self.manager.records

    @property
    def manifest(self) -> CorpusManifest:
        return self.manager.manifest

    def __len__(self) -> int:
        return len(self.manager.entries)


def _load_manifest(path: Path) -> CorpusManifest:
    data = json.loads(path.read_text())
    # Construct directly from the regeneration fields; we deliberately do not
    # couple this repo's manifest file to lp2graph's internal schema version.
    return CorpusManifest(
        frozen_search_date=str(data["frozen_search_date"]),
        queries=tuple(str(q) for q in data.get("queries", ())),
        notes=str(data.get("notes", "")),
    )


def _load_provenance(path: Path) -> ProvenanceRecord:
    data = json.loads(path.read_text())
    kwargs = {k: data[k] for k in _PROV_FIELDS if k in data}
    return ProvenanceRecord(**kwargs)


def load_corpus(config: PipelineConfig | None = None) -> LoadedCorpus:
    """Load the corpus described by ``config`` (defaults to ``corpus/``).

    Entries are sorted by formulation id for a deterministic order. A
    formulation without a matching ``provenance/<id>.json`` is reported as a
    load failure rather than guessed at.
    """
    config = config or PipelineConfig()
    manifest = _load_manifest(config.manifest_path)

    entries: list[tuple[Formulation, ProvenanceRecord]] = []
    failures: list[LoadFailure] = []

    for fpath in sorted(config.formulations_dir.glob("*.json")):
        try:
            formulation = load(fpath)
        except Exception as exc:  # report, never drop
            failures.append(LoadFailure(str(fpath), f"validation: {exc}"))
            continue
        ppath = config.provenance_dir / f"{formulation.id}.json"
        if not ppath.exists():
            failures.append(LoadFailure(str(fpath), f"missing provenance/{formulation.id}.json"))
            continue
        try:
            record = _load_provenance(ppath)
        except Exception as exc:  # report, never drop
            failures.append(LoadFailure(str(ppath), f"provenance: {exc}"))
            continue
        entries.append((formulation, record))

    entries.sort(key=lambda e: e[0].id)
    manager = CorpusManager.build(manifest, entries)
    return LoadedCorpus(manager=manager, load_failures=tuple(failures))


__all__ = ["LoadFailure", "LoadedCorpus", "load_corpus"]
