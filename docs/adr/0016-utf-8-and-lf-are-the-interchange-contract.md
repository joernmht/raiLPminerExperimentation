# 16. UTF-8 and LF are the interchange contract, declared at every call site

- Status: accepted
- Date: 2026-09-07
- Deciders: nightly quality pass (Compatibility), Claude

## Context and problem statement

The corpus is not a private scratch directory — it is the paper's dataset. It
ships on a public GitHub repo, is destined for Zenodo, and is meant to be read
by third parties on their own machines. 487 of its JSON files carry non-ASCII
text (author names, en-dashes in station pairs `S1–S2`, the minus sign in
`max(0, arrival − due)`, review prose).

Python's text I/O defaults are *locale*-dependent. `open()`,
`Path.read_text()` and `Path.write_text()` with no `encoding=` use
`locale.getpreferredencoding(False)`, and a text write with no `newline=`
translates every `"\n"` to `os.linesep`. On Linux this is invisible: PEP 538
locale coercion and PEP 540 UTF-8 mode make the default UTF-8 even under
`LC_ALL=C`, and `os.linesep` is already `"\n"`. On Windows — the platform
this code has never been run on — neither holds:

- **Reads corrupt silently.** cp1252 has a mapping for almost every byte, so
  a UTF-8 corpus file decodes without raising and yields mojibake:
  `Bešinović` → `BeÅ¡inoviÄ‡`, `—` → `â€"`. No exception, no signal, wrong data.
- **Writes crash.** 472 corpus files contain at least one character cp1252
  cannot represent; re-emitting them raises `UnicodeEncodeError`.
- **The determinism claim breaks.** CLAUDE.md promises "same corpus + same
  versioned config ⇒ byte-identical artifacts". Under `newline=None` the same
  run on Windows emits CRLF, so the artifacts differ by every line.

Before this decision, 78 text-I/O call sites across `railpminer/`,
`corpusbuilder/`, `scripts/` and `tests/` relied on the locale default, and 55
text writes relied on `os.linesep`.

## Decision

UTF-8 and LF are part of the artifact contract, not of the environment.

1. Every text-mode `open()`, `read_text()` and `write_text()` passes
   `encoding="utf-8"` explicitly. Binary-mode calls are exempt.
2. Every text write in the *producing* packages (`railpminer`,
   `corpusbuilder`, `scripts`) also passes `newline="\n"`. Tests are exempt:
   they compare in-process values, not bytes on the wire.
3. `tests/test_text_io_encoding.py` enforces both by walking the AST of every
   checked file, and carries planted-violation tests so a green run means the
   guard can still fail.
4. CI adds a `c-locale` job that runs the suite with `PYTHONUTF8=0`,
   `PYTHONCOERCECLOCALE=0`, `LC_ALL=C` — which drives Python's preferred
   encoding to ASCII on ubuntu — so the failure mode is exercised on a Linux
   runner rather than assumed away.

An AST test rather than a lint rule because ruff's encoding rules
(`PLW1514`, the `FURB` set) are preview-only and the shared style convention
(`~/.claude/quality/STYLE.md` §1) pins ruff's stable `E/F/W/I/UP/B/SIM/RUF`
selection across all four repos.

## Consequences

- The corpus round-trips byte-identically on any platform, and the
  determinism claim becomes platform-independent rather than Linux-only.
- New code pays a small, mechanical cost: two keywords per write. The guard
  makes forgetting them a test failure, not a bug report from a reader.
- The guard is repo-local. It is a candidate to ripple to the other three
  repos (`~/.claude/quality/STYLE.md` §3) — `lp2graph` in particular emits the
  canonical LaTeX/JSON these artifacts are made of.
- Reads keep universal-newline translation (`newline` unset on read), so a
  CRLF file authored elsewhere still parses.
