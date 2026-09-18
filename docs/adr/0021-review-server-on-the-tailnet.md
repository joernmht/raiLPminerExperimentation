# ADR-0021: The discovery pages run as a web app on a private network only

**Status:** accepted (2026-09-18)

## Context

The discovery pages (ADR-0019/0020) quote Elsevier full text, so they can
never sit on a public address, and the machine that builds them holds API
keys and repository tokens. Reviewing on the phone from downloaded files works
but loses state (a file opened from Android's file manager gets no browser
storage) and moves decisions by hand.

## Decision

1. **Private network, not a public host.** The box joins the owner's Tailscale
   network in *userspace networking* mode (no root, static binaries under
   `~/.local/tailscale`, state under `~/.local/state/tailscale`), and
   `tailscale serve` fronts the pages for the owner's own logged-in devices.
   No inbound port, no DNS record, no access policy to keep right.
2. **A dumb server** (`corpusbuilder.reviewserver`, stdlib only, bound to
   127.0.0.1): serves `corpus/review/` read-only, accepts a validated
   `discover-decisions` export into `corpus/review/inbox/` (append-only, never
   into `corpus/decisions/`; that move stays a reviewed step), and keeps the
   page state per paper under `corpus/review/state/` so a paper continues on
   another device. Bodies are capped, paths are confined, nothing executes.
3. **The pages stay files.** Served, they sync state and post exports; opened
   from a file they behave as before (download, share sheet). One code path,
   one export format.
4. **Operation** is the house pattern: `~/.claude/loops/review_server_start.sh`
   (idempotent) from cron at reboot and every ten minutes: tailscaled, the
   server, the serve mapping once the node is logged in.

## Consequences

- `corpus/review/` (pages, inbox, state) stays gitignored; exports reach the
  repository only after the inbox is reviewed and merged with
  `discovergame.load_decisions`.
- Losing the tailnet login means losing access, not exposure.
- A Play-Store app would need a backend with logins; nothing here moves
  towards that.
