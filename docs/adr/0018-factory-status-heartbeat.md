# ADR-0018: The factory heartbeat is an observability contract, never an input

- **Status:** accepted
- **Date:** 2026-09-10
- **Context:** `corpusbuilder.factory` renders the corpus pipeline as a factory floor
  and, when served locally, shows which station is running. That needs a signal from
  the long-running stages (`promote`, `assist`, `resolution`, `wlcluster`). Anything
  those stages write must not become a hidden input to a result, and a monitoring
  failure must never fail a run (ADR-0001's determinism boundary; ADR-0017's rule that
  artifacts record what produced them, not the other way round).

## Decision

1. **One status file, `corpus/factory_status.json`** (`factory-status-1`): the current
   stage, its state (`running` | `done` | `failed`), `done`/`total`, a free-text note
   (typically the paper key in progress), `started_at`/`updated_at` (UTC), and a
   history of the last 20 finished stages with durations. Written atomically
   (temp file + `os.replace`), UTF-8 + LF (ADR-0016).
2. **Written only at the CLI layer.** The library functions (`promote_all`,
   `resolution.compute`, `similarity_report`, `annotate_paper`) stay silent; they accept
   an optional `progress(done, total, key)` callback or nothing at all. Each module's
   `main()` wraps its loop in `factory.running(stage)` and forwards the callback.
   Importing `corpusbuilder.factory` therefore happens inside `main()`, never at module
   import time.
3. **Never read by a stage.** No pipeline code reads the status file; only the page
   polls it. It is gitignored and is not a corpus artifact.
4. **Never raises.** `beat()` swallows every exception; `running()` re-raises the
   stage's own exception untouched after recording `failed`. A missing or read-only
   status path costs nothing.
5. **Redirectable.** `RAILP_FACTORY_STATUS=<path>` moves the file; `off`/`0` disables
   heartbeats. The test suite sets it to a temp path for every test (`conftest.py`), so
   tests never touch the live file.

## Consequences

- The page's "while running" view is honest about scope: it shows liveness and
  progress of the stage that is running, not correctness. Counts on the floor come
  from the artifacts, which only change when a stage finishes and writes them.
- A stage's runtime behaviour, outputs and determinism are unchanged by the heartbeat;
  byte-identical artifacts remain byte-identical with or without it.
- The status schema is versioned; a consumer that needs more (per-row progress, ETA)
  extends the schema rather than parsing notes.
