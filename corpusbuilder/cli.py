"""Command-line entry for corpus construction.

    python -m corpusbuilder seeds  --query "railway rescheduling MILP" --min-citations 30
    python -m corpusbuilder dossier 10.1016/j.trc.2017.06.018 --arxiv 1706.05653
    python -m corpusbuilder fetch-arxiv 2103.04618

``seeds`` lists well-cited candidate papers; ``dossier`` builds the per-paper
documentation file (source + bidirectional citations, plus arXiv formulas when
an arXiv id is given); ``fetch-arxiv`` just extracts equations from source.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import sys
from pathlib import Path

from corpusbuilder import config
from corpusbuilder.arxiv import extract_equations, fetch_source
from corpusbuilder.dossier import Dossier, SourceInfo
from corpusbuilder.elsevier import ElsevierClient, ElsevierError, is_elsevier_doi
from corpusbuilder.openalex import OpenAlexClient

_DEFAULT_OUT = Path("corpus/dossiers")


def _today() -> str:
    # User-invoked materialization records the real retrieval date; this is NOT
    # in the deterministic pipeline forward path (that runs over frozen files).
    return datetime.date.today().isoformat()


def cmd_seeds(args: argparse.Namespace) -> int:
    client = OpenAlexClient()
    seeds = client.search_seeds(args.query, min_citations=args.min_citations, limit=args.limit)
    print(f"{len(seeds)} candidate(s) for {args.query!r} (>= {args.min_citations} cites):\n")
    for s in seeds:
        print(f"  {s.cited_by_count or 0:>6}  {s.year or '????'}  {s.doi or s.openalex_id}")
        print(f"          {s.title}")
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        import json

        out.write_text(
            json.dumps([s.model_dump(mode="json") for s in seeds], indent=2, ensure_ascii=False)
            + "\n",
            encoding="utf-8",
        )
        print(f"\nwrote {out}")
    return 0


def cmd_dossier(args: argparse.Namespace) -> int:
    client = OpenAlexClient()
    dossier = client.build_dossier(
        args.identifier,
        retrieved=_today(),
        ref_limit=args.ref_limit,
        cite_limit=args.cite_limit,
    )
    arxiv_id = args.arxiv or dossier.source.arxiv_id
    doi = dossier.source.doi

    # Tier 1 — arXiv LaTeX source (preferred: byte-exact).
    if arxiv_id and not args.no_formulas:
        try:
            src_dir, sha = fetch_source(arxiv_id, Path(args.out) / "_src")
            dossier.formulas = extract_equations(src_dir)
            dossier.source.arxiv_id = arxiv_id
            dossier.source.file_path = str(src_dir)
            dossier.source.file_sha256 = sha
            dossier.source.entitlement = "open-access"
            dossier.source.api = "openalex+arxiv"
            print(f"extracted {len(dossier.formulas)} formula(s) from arXiv:{arxiv_id} (Tier-1)")
        except Exception as e:  # report, never silently drop (honesty rule)
            print(f"WARNING: arXiv source extraction failed ({e}); trying other tiers")

    # Tier 2 — Elsevier ScienceDirect full-text MathML (needs entitlement).
    if not dossier.formulas and not args.no_formulas and is_elsevier_doi(doi):
        try:
            els = ElsevierClient(proxy=args.proxy)
            xml = els.full_text_xml(doi)
            if els.has_full_text(xml):
                dossier.formulas = els.extract_formulas(xml)
                dossier.source.file_sha256 = hashlib.sha256(xml.encode("utf-8")).hexdigest()
                dossier.source.api = "openalex+elsevier"
                dossier.source.entitlement = (
                    "tud-subscription" if (els.insttoken or els.proxies) else "open-access"
                )
                ok = sum(1 for f in dossier.formulas if not f.note)
                print(
                    f"extracted {len(dossier.formulas)} formula(s) from Elsevier full text "
                    f"(Tier-2, {ok} clean); proxy={'yes' if els.proxies else 'no'}"
                )
            else:
                dossier.source.entitlement = "metadata-only"
                print(
                    "Elsevier returned METADATA ONLY (not entitled): set ELSEVIER_INSTTOKEN or "
                    "ELSEVIER_PROXY (campus tunnel) for full text. Dossier has citations only."
                )
        except (ElsevierError, RuntimeError) as e:
            print(f"WARNING: Elsevier extraction failed ({e}); dossier has citations only")

    # Scopus cited-by cross-check (works without full-text entitlement).
    if not args.no_scopus and is_elsevier_doi(doi) and config.elsevier_api_key():
        try:
            count = ElsevierClient(proxy=args.proxy).scopus_cited_by_count(doi)
            dossier.source.scopus_cited_by_count = count
            if count is not None:
                print(f"Scopus cited-by: {count} (OpenAlex: {dossier.source.cited_by_count})")
        except (ElsevierError, RuntimeError) as e:
            print(f"WARNING: Scopus cross-check failed ({e})")

    json_path, md_path = dossier.save(args.out)
    print(f"wrote {json_path}\nwrote {md_path}")
    print(
        f"  {len(dossier.references)} references, {len(dossier.cited_by)} citers, "
        f"{len(dossier.formulas)} formulas"
    )
    return 0


def cmd_fetch_arxiv(args: argparse.Namespace) -> int:
    src_dir, sha = fetch_source(args.arxiv_id, Path(args.out) / "_src")
    formulas = extract_equations(src_dir)
    print(f"arXiv:{args.arxiv_id}  sha256={sha[:12]}…  {len(formulas)} formula(s)")
    for f in formulas[:10]:
        latex = f.latex.replace("\n", " ")
        print(f"  {f.id}: {(latex[:90] + '…') if len(latex) > 90 else latex}")
    if len(formulas) > 10:
        print(f"  … and {len(formulas) - 10} more")
    if args.save:
        src = SourceInfo(
            title=f"arXiv:{args.arxiv_id}",
            arxiv_id=args.arxiv_id,
            api="arxiv",
            retrieved=_today(),
            file_path=str(src_dir),
            file_sha256=sha,
            entitlement="open-access",
        )
        Dossier(source=src, formulas=formulas).save(args.out)
        print(f"wrote dossier to {args.out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="corpusbuilder", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("seeds", help="list well-cited candidate papers")
    s.add_argument("--query", required=True)
    s.add_argument("--min-citations", type=int, default=20)
    s.add_argument("--limit", type=int, default=25)
    s.add_argument("--out", default=None, help="write candidates to this JSON file")
    s.set_defaults(func=cmd_seeds)

    d = sub.add_parser("dossier", help="build a per-paper dossier (citations + arXiv formulas)")
    d.add_argument("identifier", help="DOI, arXiv id, or OpenAlex id")
    d.add_argument(
        "--arxiv", default=None, help="arXiv id for formula extraction (if not auto-found)"
    )
    d.add_argument("--ref-limit", type=int, default=200)
    d.add_argument("--cite-limit", type=int, default=100)
    d.add_argument(
        "--no-formulas", action="store_true", help="citations only, skip formula extraction"
    )
    d.add_argument("--no-scopus", action="store_true", help="skip the Scopus cited-by cross-check")
    d.add_argument(
        "--proxy",
        default=None,
        help="proxy for entitled Elsevier full text, e.g. socks5h://127.0.0.1:8080 "
        "(defaults to ELSEVIER_PROXY from the env/secrets)",
    )
    d.add_argument("--out", default=str(_DEFAULT_OUT))
    d.set_defaults(func=cmd_dossier)

    f = sub.add_parser("fetch-arxiv", help="extract equations from an arXiv source")
    f.add_argument("arxiv_id")
    f.add_argument("--save", action="store_true", help="also write a formulas-only dossier")
    f.add_argument("--out", default=str(_DEFAULT_OUT))
    f.set_defaults(func=cmd_fetch_arxiv)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
