# 3. SOCKS tunnel (campus IP) for entitled Elsevier full text

- Status: accepted
- Date: 2026-06-22
- Deciders: Jörn Maurischat

## Context and problem statement

Tier-2 extraction (ADR-0002) needs Elsevier ScienceDirect **full-text** XML, not
just metadata. Elsevier grants full text by *institutional entitlement*, keyed to
the requesting IP (TU Dresden's range) and/or an `insttoken`. The corpus is built
on this sandbox box, which is not on the campus network. Without entitlement the
API silently returns metadata-only, so the build would quietly produce dossiers
with citations but no formulas. How do we obtain entitled full text from off-campus
without a heavyweight VPN, and *without* the non-entitled case failing silently?

## Decision

Route Elsevier HTTP through an **SSH SOCKS tunnel** terminating on a campus host,
so requests egress from an entitled IP. `ElsevierClient` accepts a `proxy`
(e.g. `socks5h://127.0.0.1:8080`), defaulting to `ELSEVIER_PROXY` from the env /
central secret store (`config.py`); `PySocks` enables `requests` to use it.
`socks5h` is chosen so DNS resolves *through* the tunnel (campus-side), not
locally. Entitlement is also satisfiable by `ELSEVIER_INSTTOKEN` when available.

Crucially, the non-entitled path is made **loud, not silent**: `has_full_text()`
distinguishes real full text from a metadata-only response, and `cmd_dossier`
prints an explicit `METADATA ONLY (not entitled)` warning and records
`entitlement="metadata-only"` on the source. This upholds the honesty invariant
(ADR-0001 / CLAUDE.md): a coverage gap is reported, never disguised as success.

## Consequences

- **Good:** entitled full text from anywhere, with a one-line SSH tunnel and no
  VPN client; DNS-through-tunnel avoids leaking campus-only hostnames.
- **Good:** the metadata-only fallback is explicit and stamped, so PRISMA
  (ADR-0004) can count "fetched but not entitled" honestly.
- **Bad:** setup friction — the operator must stand up the SSH tunnel and set
  `ELSEVIER_PROXY`/`ELSEVIER_INSTTOKEN`; an expired tunnel degrades silently to
  metadata-only (mitigated by the printed warning).
- **Bad:** credentials/proxy live in `.env` / the central secret store, which
  must stay out of git (`.gitignore` covers `.env*` except the example).
</content>
