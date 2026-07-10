# 6. Transient faults are not coverage information (retry, classify, refuse to record)

- Status: accepted
- Date: 2026-07-10
- Deciders: Jörn Maurischat
- Supersedes/extends: builds on ADR-0001 (determinism boundary), ADR-0002 (tiered
  ladder), ADR-0003 (SOCKS tunnel for entitled Elsevier full text)

## Context and problem statement

Corpus acquisition talks to three flaky upstreams and one flaky transport:

| Upstream | How it sheds load |
|---|---|
| OpenAlex | HTTP **429** (polite pool ≈ 10 req/s) |
| arXiv | HTTP **503** + `Retry-After` |
| Elsevier / Scopus | HTTP **429** on quota exhaustion |
| SSH SOCKS tunnel (ADR-0003) | `requests.ProxyError` when it dies |

Before this ADR, none of the three clients retried anything. Worse, the failures
were *recorded*. `cli.cmd_dossier` implements ADR-0002's ladder as "stop at the
first tier that yields formulas", and `prisma.py` later turns a dossier with an
empty `formulas` list into a **permanent exclusion reason**:

```python
# prisma.py
if nf > 0:            incl_papers += 1
elif entitlement == "metadata-only":  excl["not_entitled"] += 1
elif is_elsevier_doi(doi):            excl["no_machine_readable_formulas"] += 1
else:                                 excl["awaiting_tier3_pdf"] += 1
```

So a dropped tunnel or a 30-second arXiv 503 would make a paper look like it has
*no machine-readable formulas* — a claim about the paper — and that claim flows
straight into the PRISMA flow diagram, i.e. into a published "n =" number. The
network's weather would silently become part of the paper's methodology.

Three concrete defects made this reachable:

1. **`cmd_dossier` did not even catch the tunnel failure.** It caught
   `(ElsevierError, RuntimeError)`, but every `requests` exception derives from
   `OSError`, *not* `RuntimeError`. A `ProxyError` escaped the handler and
   crashed the command with a traceback — the documented "dossier has citations
   only" degradation never ran. (Reproduced; now regression-tested.)
2. **`OpenAlexClient.get_work` treated any error as "not found."** Its arXiv path
   did `except OpenAlexError: pass` and fell through to a fuzzy title search. On a
   429/500 that search could bind a **different paper** to the arXiv id.
3. **`scopus_cited_by_count` returned `None` on any non-200**, conflating "not
   indexed in Scopus" with "rate-limited" — recording a fabricated fact in the
   dossier, which CLAUDE.md's honesty rule forbids.

## Decision

Introduce `corpusbuilder/_http.py` and adopt one rule across acquisition:

> **A transient fault is not coverage information.** Retry it; if it survives the
> retries, refuse to write the corpus record it would have produced.

Concretely:

1. **Classify every failure.** `AcquisitionError` (base of `OpenAlexError`,
   `ElsevierError`, `ArxivError`) carries `status` and a `transient` flag.
   `RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}`; transport blips
   (`ConnectionError`/`ProxyError`, `Timeout`, `ChunkedEncodingError`) are
   transient. 4xx client errors are **not** retried — a 404 is an answer.
2. **Retry the transient ones.** `request_with_retry` wraps every client GET:
   jittered exponential backoff (full jitter, capped), honouring an upstream
   `Retry-After` (both delta-seconds and HTTP-date forms) over the computed
   delay, capped at `max_retry_after` so a buggy upstream cannot park the run.
3. **Never record a transient failure.**
   - `cli.cmd_dossier` exits `EXIT_TRANSIENT` (2) and writes **no dossier** when a
     tier fails transiently. It does *not* descend the ladder — a rate-limited
     arXiv must never demote a Tier-1 paper to Tier-2.
   - `_discover` checkpoints each query to `candidates.partial.json` and writes
     the corpus artifact `candidates.json` **only when every query succeeded**,
     because `prisma.py` derives `database_queries` / `database_search_records`
     from it. `--resume` re-runs only the missing queries.
   - `scopus_cited_by_count` returns `None` **only** on a 404 (Scopus answered:
     not indexed); everything else raises.
4. **Permanent failures still degrade and record**, exactly as ADR-0002 intends:
   a PDF-only arXiv e-print (`ArxivError(transient=False)`) or a 403 "not
   entitled" is a real fact about the paper and legitimately drops it down the
   ladder.

Exit code **2** is reserved for "transient, nothing written, re-run"; **1** stays
"permanent error", so a batch driver can retry on 2 and escalate on 1.

## Consequences

- **Good:** the PRISMA tally can no longer be polluted by network weather. Every
  "n =" in Paper 1 is a claim about the literature, not about the tunnel.
- **Good:** long acquisition sweeps survive rate limits; `_discover` no longer
  discards ~45 min of API calls when the last query 429s.
- **Good:** the corpus-corruption path (fuzzy search binding the wrong paper to an
  arXiv id after a 5xx) is closed.
- **Good:** determinism is untouched — `_http` lives entirely on the acquisition
  side of ADR-0001's boundary; `railpminer/` gains no clock and no RNG. `sleep`,
  `rng` and `now` are injectable, so the 41 new tests are offline and instant.
  `_discover`'s merge was additionally made order-independent, so a `--resume`
  sweep reproduces an uninterrupted one byte-for-byte.
- **Bad:** an operator now sees hard failures (exit 2) where the tool previously
  produced a plausible-looking dossier. This is the point, but it means batch
  drivers must handle exit 2 and re-run rather than assuming success.
- **Bad:** a transient fault that persists across retries blocks that paper until
  a human re-runs. Preferred over silently mislabelling it.
- **Judgment call:** malformed publisher XML (`LxmlError`) is treated as
  **permanent**. It survives the HTTP-layer retries, so it is a content problem,
  not a blip — recorded as a Tier-2 miss rather than blocking the dossier.

## Follow-ups (not in this ADR)

- `SourceInfo` has no field to distinguish "Scopus says not indexed" from "we
  never asked / lookup failed"; both serialize as `scopus_cited_by_count: null`.
  A `scopus_lookup: ok | not-indexed | unavailable` field would need a
  `schema_version` bump to `dossier-2`.
- No client-side pacing for OpenAlex's ~10 req/s polite pool; retries absorb the
  429s reactively rather than avoiding them.
