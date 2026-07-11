# 7. Config faults are not coverage information either (abort, never degrade)

- Status: accepted
- Date: 2026-07-11
- Deciders: Jörn Maurischat
- Supersedes/extends: extends ADR-0006 (transient faults are not coverage
  information) one layer up the stack; builds on ADR-0002 (tiered ladder) and
  ADR-0004 (PRISMA tally as a versioned corpus artifact)

## Context and problem statement

ADR-0006 established the rule that a *network* fault must never be written into
the corpus, because `prisma.py` turns a dossier with an empty `formulas` list into
a **permanent exclusion reason** (`no_machine_readable_formulas` and friends),
which flows into a published "n =" number. It classified transport failures,
retried them, and made `cmd_dossier` refuse to persist anything on a transient
fault (exit 2).

CI then demonstrated, live, that the same hole was still open one layer up — with
a *missing credential* rather than a dropped packet as the trigger.

`ElsevierClient.__init__` resolves its key eagerly:

```python
self.api_key = api_key or config.require("ELSEVIER_API_KEY")
```

and `config.require` raised a bare `RuntimeError`. In `cmd_dossier`'s Tier-2 block
that constructor call sits *inside* the `try`, so with no key set:

1. `require` raises `RuntimeError` at **construction** — before any HTTP happens,
   and before the tier's real work is even attempted;
2. `RuntimeError` is in `_TIER_ERRORS`, so the handler catches it;
3. `is_transient_exception(e)` is `False` (it is not an `AcquisitionError`, and
   not a transport blip), so the ladder takes the **permanent** branch;
4. the permanent branch is *degrade and record*: "dossier has citations only";
5. a 0-formula dossier is written, and `prisma.py` later reads it as
   `no_machine_readable_formulas`.

So an unset key on the *operator's laptop* became a published claim about the
*literature*: a paper nobody ever fetched gets excluded on the grounds that it has
no machine-readable formulas. This is exactly the ADR-0006 failure mode, and
ADR-0006's own machinery — the transient/permanent dichotomy — is what routed it
there. A config fault is neither transient nor permanent *in the sense that
dichotomy means*: those words describe the paper and the upstream, and a missing
key describes **this machine**.

The bug reached CI because the test suite was not hermetic: the ladder tests
mocked `full_text_xml` but let the constructor run for real, so on Joern's box a
gitignored `.env` supplied a key and the mocks fired, while CI (no secret) never
got past step 1. The suite was green locally and red on PR #6 — and the red run is
what exposed the defect.

## Decision

Extend ADR-0006's rule to cover the machine as well as the network:

> **A config fault is not coverage information.** It says nothing about the paper.
> Abort; write nothing; never let it reach the degrade-and-record branch.

Concretely:

1. **Classify it distinctly.** `config.ConfigError(RuntimeError)` is raised by
   `config.require`. It is deliberately *not* an `AcquisitionError` — it must not
   acquire a `transient` flag, because the transient/permanent question does not
   apply to it.
2. **Abort before the transient check.** Every tier handler in `cmd_dossier` now
   catches `config.ConfigError` **first** and calls `_abort_config`, the sibling of
   `_abort_transient`: no dossier written, so PRISMA can learn nothing from it.
3. **A third exit code.** `EXIT_CONFIG = 3`, distinct from `EXIT_TRANSIENT = 2`,
   because re-running changes nothing until a human sets the key. A batch driver
   retries on 2, escalates on 1, and **stops** on 3 rather than retry-spinning.
4. **Hermetic ladder tests.** The `_no_openalex` fixture pins a dummy
   `ELSEVIER_API_KEY`. The machine's real credentials are part of the outside world
   the ladder tests isolate themselves from; a test whose meaning depends on a
   gitignored file is not a test. The missing-key case is now covered explicitly by
   a test that deletes the variable.

The Scopus cited-by cross-check is deliberately left alone: it is explicitly *not*
coverage information (a failure there leaves `scopus_cited_by_count` unset rather
than asserting a fact), and it is already guarded by `config.elsevier_api_key()`.

## Consequences

- **Good:** the class of bug ADR-0006 named is now closed at both layers. Neither
  the network's weather nor the operator's `.env` can enter the PRISMA tally.
- **Good:** the failure is now loud and actionable at the moment it happens ("set
  the key, then re-run") instead of silently producing a plausible dossier whose
  wrongness only becomes visible as a wrong "n =" in the paper months later.
- **Good:** the test suite no longer passes for the wrong reason. A green run on
  a laptop now means the same thing as a green run in CI.
- **Bad:** exit code 3 is a third state batch drivers must handle. Treating it as
  a generic failure is safe (it writes nothing); only retry-spinning is wrong.
- **Judgment call:** `ConfigError` still subclasses `RuntimeError` so that any
  handler elsewhere that catches `RuntimeError` keeps catching it. That is a
  hazard — such a handler would swallow it — but the alternative (a bare
  `Exception` subclass) risks it escaping as a traceback through code paths that
  legitimately degrade. The tier ladder, the only place that *records* coverage,
  now catches it explicitly and first.

## Follow-ups (not in this ADR)

- The other clients resolve credentials lazily or not at all; if any later grows an
  eager `config.require` in a code path that degrades, it needs the same treatment.
  A lint rule ("`require()` must not be called inside a `try` that degrades") would
  be better than vigilance, but there is no obvious way to express it.
- `prisma.py` trusts any dossier on disk. It cannot currently distinguish "we
  looked and found nothing" from "we never successfully looked" — the exclusion
  reasons are inferred from an *absence* (`formulas == []`) rather than from a
  recorded positive statement about what was attempted. Both ADR-0006 and this ADR
  are guards *upstream* of that weakness. A `tier_attempted` / `tier_outcome` field
  on `SourceInfo` would make the tally robust by construction rather than by
  discipline, at the cost of a `schema_version` bump to `dossier-2`.
