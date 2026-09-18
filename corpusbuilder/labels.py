"""Standard label words in subscripts and superscripts, and the scan that proposes additions.

``t_{end}``, ``x^{max}``, ``N_{st}``: the script is a *label* that belongs to the
symbol's name, not an index. The index discovery must know the common ones
before it looks for index letters, otherwise a glued lowercase pair such as
``st`` is read as two dummies. :data:`STANDARD_LABELS` is that list, curated
by hand. :func:`scan_all` counts every script token of the corpus that is not
an index letter, splits the counts into *standard* and *unlisted*, and writes
``corpus/labels.{json,md}`` so the unlisted frequent ones can be reviewed and,
together with the proposals people mark on the discovery pages
(``label_proposals`` in ``discover-decisions-2``), added to the list.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

from corpusbuilder.fulltext import DOSSIERS, included_dossiers
from corpusbuilder.promote import CORPUS

REPORT_JSON = CORPUS / "labels.json"
REPORT_MD = CORPUS / "labels.md"

#: Words that, inside a subscript or superscript, name a variant of the symbol
#: rather than an index: bounds, phases, directions, roles. Lower case; the
#: match is case-insensitive.
STANDARD_LABELS: frozenset[str] = frozenset(
    [
        "start",
        "end",
        "begin",
        "fin",
        "final",
        "init",
        "initial",
        "orig",
        "origin",
        "dest",
        "destination",
        "src",
        "source",
        "snk",
        "sink",
        "min",
        "max",
        "avg",
        "mean",
        "tot",
        "total",
        "sum",
        "cum",
        "cumulative",
        "arr",
        "arrival",
        "ar",
        "dep",
        "departure",
        "de",
        "pass",
        "passing",
        "stop",
        "dwell",
        "run",
        "running",
        "wait",
        "waiting",
        "turn",
        "head",
        "headway",
        "in",
        "out",
        "up",
        "down",
        "lo",
        "hi",
        "low",
        "high",
        "upper",
        "lower",
        "ub",
        "lb",
        "bound",
        "first",
        "last",
        "prev",
        "previous",
        "next",
        "cur",
        "curr",
        "current",
        "new",
        "old",
        "pre",
        "post",
        "before",
        "after",
        "early",
        "late",
        "nom",
        "nominal",
        "plan",
        "planned",
        "sched",
        "scheduled",
        "act",
        "actual",
        "real",
        "est",
        "estimated",
        "exp",
        "expected",
        "obs",
        "observed",
        "ref",
        "base",
        "fix",
        "fixed",
        "var",
        "variable",
        "opt",
        "optimal",
        "best",
        "worst",
        "static",
        "dyn",
        "dynamic",
        "safe",
        "safety",
        "buf",
        "buffer",
        "slack",
        "pen",
        "penalty",
        "cost",
        "time",
        "dist",
        "distance",
        "len",
        "length",
        "speed",
        "vel",
        "velocity",
        "acc",
        "accel",
        "dec",
        "decel",
        "brake",
        "cap",
        "capacity",
        "dem",
        "demand",
        "supply",
        "load",
        "flow",
        "sec",
        "section",
        "seg",
        "segment",
        "blk",
        "block",
        "track",
        "plat",
        "platform",
        "st",
        "sta",
        "stn",
        "station",
        "node",
        "arc",
        "edge",
        "link",
        "path",
        "route",
        "line",
        "tr",
        "train",
        "veh",
        "vehicle",
        "unit",
        "pax",
        "passenger",
        "crew",
        "driver",
        "on",
        "off",
        "open",
        "close",
        "closed",
        "occ",
        "occupied",
        "free",
        "idle",
        "temp",
        "tmp",
        "aux",
        "dual",
        "prime",
        "bar",
        "hat",
        "tilde",
        "star",
        "od",
        "dis",
        "disr",
        "disruption",
        "del",
        "delay",
        "res",
        "rev",
    ]
)

_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9]*")


def is_standard_label(token: str) -> bool:
    return token.lower() in STANDARD_LABELS


def script_tokens(norm: str) -> list[str]:
    """Every word (2+ letters) written inside a subscript or superscript of one normalised row."""
    from corpusbuilder.indices import _IDENT, _scripts  # local import: indices imports this module

    out: list[str] = []
    i = 0
    while True:
        m = _IDENT.search(norm, i)
        if not m:
            break
        sub, sup, j = _scripts(norm, m.end())
        i = j
        for g in sub + sup:
            g = re.sub(
                r"(?<![A-Za-z])([a-z])((?: [a-z])+)(?![A-Za-z])",
                lambda m: m.group(0).replace(" ", ""),
                g,
            )
            for tok in _TOKEN.findall(g):
                if len(tok) >= 2 and tok.isalpha():
                    out.append(tok)
    return out


def scan_all(dossier_dir: Path = DOSSIERS) -> dict:
    from corpusbuilder.indices import GREEK, base_letter, normalise

    counts: Counter[str] = Counter()
    papers: dict[str, set[str]] = {}
    for d in included_dossiers(dossier_dir):
        for f in d.formulas:
            if not f.latex:
                continue
            for tok in script_tokens(normalise(f.latex)):
                if tok in GREEK or re.fullmatch(r"[A-Za-z]p+", tok) or base_letter(tok) is not None:
                    continue  # primes (i' -> ip), decorated letters and Greek names are index letters, not labels
                counts[tok] += 1
                papers.setdefault(tok, set()).add(d.key)
    standard = {t: n for t, n in counts.items() if is_standard_label(t)}
    unlisted = {t: n for t, n in counts.items() if not is_standard_label(t)}
    return {
        "standard_list_size": len(STANDARD_LABELS),
        "tokens_seen": len(counts),
        "standard": dict(sorted(standard.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unlisted": dict(sorted(unlisted.items(), key=lambda kv: (-kv[1], kv[0]))),
        "unlisted_papers": {t: len(papers[t]) for t in unlisted},
    }


def render_report_md(report: dict, top: int = 60) -> str:
    lines = [
        "# Label words in subscripts and superscripts",
        "",
        f"Standard list: **{report['standard_list_size']}** words. Distinct script tokens seen: **{report['tokens_seen']}**.",
        "",
        "## Standard words seen (count)",
        "",
        ", ".join(f"{t} ({n})" for t, n in list(report["standard"].items())[:top]) or "none",
        "",
        "## Unlisted words, most frequent first (count · papers) — candidates for the list",
        "",
        "| word | count | papers |",
        "|---|---:|---:|",
    ]
    for t, n in list(report["unlisted"].items())[:top]:
        lines.append(f"| {t} | {n} | {report['unlisted_papers'][t]} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.parse_args(argv)
    report = scan_all()
    REPORT_JSON.write_text(
        json.dumps(report, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )
    REPORT_MD.write_text(render_report_md(report), encoding="utf-8", newline="\n")
    print(
        f"labels: {report['tokens_seen']} script tokens, {len(report['standard'])} standard, {len(report['unlisted'])} unlisted",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
