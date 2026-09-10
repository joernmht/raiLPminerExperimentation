# 17. Artifacts record the code that produced them, not just its inputs

- Status: accepted
- Date: 2026-09-07
- Deciders: nightly quality pass (Compatibility), Claude

## Context and problem statement

`run_summary.json` carried a `versions` block with lp2graph's six frozen
*resource* versions — lexicon, thesaurus, vocabulary, clustering, label
lexicon, rewrite rules (`railpminer/pipeline.py:_versioned_resources`). Those
pin the method's **inputs**. They say nothing about the **implementation**
that consumed them.

The dependency is declared as `lp2graph[mining,solver]>=0.3`, unbounded, and
in this workspace lp2graph is not installed at all: `railpminer/_lp2graph.py`
puts the sibling source checkout `../lp2graph/src` on `sys.path`, so the code
in play is whatever that working tree happens to contain — a moving target,
since lp2graph is under active development in the same session.

The result was a false sense of provenance. Two artifact sets could carry
identical `versions` blocks and byte-different numbers, with nothing in the
artifact to distinguish them. For a paper whose central claim is
reproducibility, "which code produced this" is not optional metadata.

## Decision

`MiningResult.summary()` gains a sibling `software` block recording the
harness version, the lp2graph version and the interpreter version:

```json
"software": {"railpminer": "1.0.0", "lp2graph": "0.3.0", "python": "3.12.3"}
```

`lp2graph`'s version is read from distribution metadata, falling back to the
package's `__version__` attribute when it is a source checkout with no
installed distribution (the normal local layout).

The existing `versions` block is left untouched, so consumers that read
`versions.clustering` keep working; `software` is additive.

`run_summary.json` is provenance, not a determinism-compared artifact — the
determinism test (`tests/test_pipeline.py::test_run_is_deterministic`)
compares the mined `dataset`, which stays environment-free. Recording the
interpreter version here is therefore honest rather than contradictory.

## Consequences

- An artifact set is traceable to the code that emitted it, on any machine.
- The unbounded `>=0.3` requirement is now visible in the output rather than
  only in `pyproject.toml`. A future lp2graph that changes the mining
  algorithms will produce differently-stamped artifacts instead of silently
  differently-valued ones.
- The paper can cite the exact library version behind each reported number.
