"""Full-text XML cache: the prose that Tier-2 extraction threw away.

The 2026-06 harvest kept only what the formula ladder needed — ``<ce:formula>``
MathML converted to LaTeX — and discarded the article body. Rung (c) of staged
symbol resolution ("assisted resolution", sec:resolution) needs exactly that
body back: symbol *definitions* live in prose ("where :math:`x_{ij}` denotes
...") and nomenclature lists, not in displayed equations. This module re-fetches
the full-text XML for every included paper once, caches it, and derives a
per-paper prose digest that the assisted-resolution step can quote from.

Both output directories are **gitignored on purpose**: Elsevier TDM material
must never be committed or published (same rule as ``corpus/review/``).

* ``corpus/fulltext/<key>.xml``  — raw Article Retrieval XML, fetched once
* ``corpus/prose/<key>.json``    — abstract, paragraphs (with adjacency to the
  dossier's formula labels), and definition/nomenclature lists

Run::

    ELSEVIER_PROXY=socks5h://127.0.0.1:8080 PYTHONPATH=. \
        python3 -m corpusbuilder.fulltext --fetch     # needs the ZIH tunnel
    PYTHONPATH=. python3 -m corpusbuilder.fulltext --extract   # offline
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from lxml import etree

from corpusbuilder.dossier import Dossier
from corpusbuilder.elsevier import _NS, _XML_PARSER, ElsevierClient, ElsevierError, is_elsevier_doi

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "corpus"
DOSSIERS = CORPUS / "dossiers"
FULLTEXT = CORPUS / "fulltext"
PROSE = CORPUS / "prose"

PROSE_SCHEMA = "prose-1"


def included_dossiers(dossier_dir: Path = DOSSIERS) -> list[Dossier]:
    """Included papers = dossiers that carry formulas (the 238 of prisma.json)."""
    out = []
    for p in sorted(dossier_dir.glob("*.json")):
        d = Dossier.load(p)
        if d.formulas:
            out.append(d)
    return out


# -- fetch ------------------------------------------------------------------


def fetch_all(sleep_s: float = 0.7, refetch: bool = False) -> dict:
    """Fetch + cache full-text XML for every included Elsevier paper.

    Idempotent: an existing cache file with article body is never re-fetched
    unless ``refetch``. Returns the run log (also written into the cache dir).
    """
    FULLTEXT.mkdir(parents=True, exist_ok=True)
    client = ElsevierClient()
    log: dict[str, list] = {"fetched": [], "cached": [], "no_body": [], "failed": [], "skipped": []}
    dossiers = included_dossiers()
    for i, d in enumerate(dossiers, 1):
        key, doi = d.key, d.source.doi
        target = FULLTEXT / f"{key}.xml"
        if not is_elsevier_doi(doi):
            log["skipped"].append(key)
            continue
        if target.exists() and not refetch and ElsevierClient.has_full_text(
            target.read_text(encoding="utf-8", errors="replace")[:200_000]
        ):
            log["cached"].append(key)
            continue
        try:
            xml = client.full_text_xml(doi)
        except ElsevierError as e:
            print(f"[{i}/{len(dossiers)}] FAIL {key}: {e}", file=sys.stderr)
            log["failed"].append({"key": key, "error": str(e)[:200]})
            time.sleep(sleep_s)
            continue
        if not ElsevierClient.has_full_text(xml):
            log["no_body"].append(key)  # metadata-only: not entitled via this route
        else:
            target.write_text(xml, encoding="utf-8")
            log["fetched"].append(key)
        print(f"[{i}/{len(dossiers)}] {'ok  ' if key in log['fetched'] else 'meta'} {key}")
        time.sleep(sleep_s)
    (FULLTEXT / "_fetch_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


# -- extract ----------------------------------------------------------------


def _text_of(el: etree._Element) -> str:
    """Flatten an element to readable text; inline MathML becomes ``⟨…⟩`` runs."""
    parts: list[str] = []

    def emit(node: etree._Element) -> None:
        tag = etree.QName(node).localname if isinstance(node.tag, str) else ""
        if tag == "math":
            frag = " ".join("".join(node.itertext()).split())
            if frag:
                parts.append(f"⟨{frag}⟩")
            return  # subtree consumed; tail is appended by the caller
        if node.text:
            parts.append(node.text)
        for child in node:
            emit(child)
            if child.tail:
                parts.append(child.tail)

    emit(el)
    return " ".join(" ".join(parts).split())


def extract_prose(xml: str, formula_labels: list[str | None]) -> dict:
    """Derive the prose digest of one article.

    ``formula_labels`` are the dossier's ``FormulaRecord.label`` values (the
    ``<ce:formula id=…>`` attributes), so paragraphs can be linked back to the
    formulas they surround — the "surrounding prose" that rung (c) reads.
    """
    root = etree.fromstring(xml.encode("utf-8"), parser=_XML_PARSER)
    wanted = {lb for lb in formula_labels if lb}

    # Abstract: any <ce:abstract> paragraphs (skip graphical/highlights variants).
    abstract_parts = []
    for ab in root.findall(".//ce:abstract", _NS):
        cls = (ab.get("class") or "").lower()
        if cls and cls not in ("author", "abstract"):
            continue
        for p in ab.findall(".//ce:simple-para", _NS) + ab.findall(".//ce:para", _NS):
            abstract_parts.append(_text_of(p))
    abstract = " ".join(abstract_parts)

    # Paragraphs in document order; note which formula ids each one contains.
    paras = []
    for i, p in enumerate(root.findall(".//ce:sections//ce:para", _NS)):
        labels = [
            f.get("id")
            for f in p.findall(".//ce:formula", _NS)
            if f.get("id") and f.get("id") in wanted
        ]
        paras.append({"i": i, "text": _text_of(p)[:4000], "formula_labels": labels})

    # Definition / nomenclature lists: the closest thing to a symbol table.
    deflists = []
    for dl in root.findall(".//ce:def-list", _NS):
        for item in dl.findall(".//ce:def-term", _NS):
            desc = item.getnext()
            term = _text_of(item)
            definition = _text_of(desc) if desc is not None else ""
            if term:
                deflists.append({"term": term[:200], "def": definition[:500]})

    return {
        "schema_version": PROSE_SCHEMA,
        "abstract": abstract[:6000],
        "paras": paras,
        "deflists": deflists,
    }


def extract_all() -> dict:
    """Offline pass: prose digests for every cached XML."""
    PROSE.mkdir(parents=True, exist_ok=True)
    log: dict[str, list] = {"ok": [], "empty": [], "failed": []}
    for d in included_dossiers():
        src = FULLTEXT / f"{d.key}.xml"
        if not src.exists():
            continue
        try:
            digest = extract_prose(
                src.read_text(encoding="utf-8", errors="replace"),
                [f.label for f in d.formulas],
            )
        except etree.XMLSyntaxError as e:
            log["failed"].append({"key": d.key, "error": str(e)[:200]})
            continue
        (PROSE / f"{d.key}.json").write_text(
            json.dumps(digest, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (log["ok"] if digest["paras"] else log["empty"]).append(d.key)
    (PROSE / "_extract_log.json").write_text(json.dumps(log, indent=1), encoding="utf-8")
    return log


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true", help="fetch+cache XML (needs tunnel)")
    ap.add_argument("--extract", action="store_true", help="derive prose digests (offline)")
    ap.add_argument("--refetch", action="store_true")
    args = ap.parse_args(argv)
    if not (args.fetch or args.extract):
        ap.error("pick --fetch and/or --extract")
    if args.fetch:
        log = fetch_all(refetch=args.refetch)
        print(
            f"fetch: {len(log['fetched'])} new, {len(log['cached'])} cached, "
            f"{len(log['no_body'])} metadata-only, {len(log['failed'])} failed"
        )
    if args.extract:
        log = extract_all()
        print(f"extract: {len(log['ok'])} ok, {len(log['empty'])} empty, {len(log['failed'])} failed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
