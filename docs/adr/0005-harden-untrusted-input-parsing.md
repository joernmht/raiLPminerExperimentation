# 5. Harden parsing of untrusted acquisition inputs (XML + tarballs)

Date: 2026-07-04

## Status

Accepted

## Context

`corpusbuilder/` acquires formulations from the open web, so it parses two
classes of **attacker-influenceable** input:

- **arXiv e-prints** (`arxiv.fetch_source`) — a `.tar.gz` uploaded by *any*
  author. Extracted to `dest_dir/<id>/` so the `.tex` files can be scanned.
- **Elsevier ScienceDirect full-text XML** (`elsevier.ElsevierClient.extract_formulas`)
  — parsed with `lxml`. Nominally from `api.elsevier.com` over TLS, but it is
  routed through a user-run SOCKS/SSH tunnel (ADR-0003) and is third-party
  publisher content, so it must be treated as untrusted.

Two concrete weaknesses were found in the 2026-07-04 nightly security pass:

1. **Tar path-traversal (`_safe_extract`).** Containment was checked with
   `str(target).startswith(str(dest))`. That string test *falsely accepts*
   sibling directories that merely share the destination's name prefix — e.g.
   with `dest=…/2103.04618`, a member named `../2103.04618_evil/pwn.tex`
   resolves to `…/2103.04618_evil/pwn.tex`, which `startswith` accepts, so the
   file lands **outside** the intended directory. An author-controlled e-print
   thus had a (bounded) arbitrary-file-write primitive.

2. **XML entity handling (`etree.fromstring(xml.encode())`).** The call used
   lxml's **implicit default parser**. Empirically (lxml 6.1.1): the default
   parser *does* expand internal entities (billion-laughs DoS surface); and when
   entity resolution is enabled, a `file://` SYSTEM entity is resolved from the
   local disk — `no_network=True` blocks the network but **not** local files, so
   it is not a sufficient control on its own. The security property we need is
   simply: never resolve entities in untrusted XML.

## Decision

**Parse every untrusted acquisition input through an explicit, hardened path;
never rely on a library's implicit default.**

- **Tarballs** — `_safe_extract` extracts **regular files only** (symlink /
  hardlink / dir / device members are skipped, closing link-based escapes) and
  requires `target.resolve().is_relative_to(dest.resolve())` for proper
  path-component containment (not a string prefix). Each `tar.extract` also
  passes `filter="data"` as a second, stdlib-native guard and the
  Python-3.14-ready default.

- **XML** — a module-level hardened `etree.XMLParser(resolve_entities=False,
  no_network=True, load_dtd=False, huge_tree=False)` is used for all
  publisher XML. `resolve_entities=False` is the load-bearing control (kills
  both local-file XXE and entity-expansion DoS); the others are defense in
  depth. Elsevier `<ce:formula>` bodies use numeric character references /
  Unicode rather than custom named entities, so extraction is unaffected; any
  residual entity reference is preserved verbatim and flagged for the human
  review step rather than resolved.

Both are covered by regression tests in `tests/test_corpusbuilder.py`
(`test_safe_extract_blocks_path_traversal`,
`test_safe_extract_skips_symlink_members`,
`test_elsevier_parser_does_not_disclose_local_files`).

## Consequences

- Path-traversal and XXE/entity-expansion vectors on the acquisition boundary
  are closed and pinned by tests, independent of future lxml/CPython default
  changes.
- Named-entity references inside publisher XML are no longer auto-resolved. This
  is an accepted, low-impact trade-off: such entities are effectively absent in
  Elsevier formula bodies, and the honest fallback (preserve + flag for review)
  matches the repo's "honesty about coverage" convention.
- The MathML→LaTeX node bridge (`mathml.py`) already uses `subprocess.run` with
  an argument **list** (no `shell=True`) and passes content via stdin/JSON, so
  it has no command-injection surface and needs no change under this ADR.

## Related

- ADR-0002 (tiered extraction ladder — the sources being hardened here).
- ADR-0003 (SOCKS tunnel — why Elsevier XML is treated as untrusted transport).
