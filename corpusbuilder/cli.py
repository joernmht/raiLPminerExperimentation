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
import sys
from pathlib import Path

from corpusbuilder.arxiv import extract_equations, fetch_source
from corpusbuilder.dossier import Dossier, SourceInfo
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
    if arxiv_id and not args.no_formulas:
        try:
            src_dir, sha = fetch_source(arxiv_id, Path(args.out) / "_src")
            dossier.formulas = extract_equations(src_dir)
            dossier.source.arxiv_id = arxiv_id
            dossier.source.file_path = str(src_dir)
            dossier.source.file_sha256 = sha
            dossier.source.entitlement = "open-access"
            dossier.source.api = "openalex+arxiv"
            print(f"extracted {len(dossier.formulas)} formula(s) from arXiv:{arxiv_id}")
        except Exception as e:  # noqa: BLE001 — report, never silently drop (honesty rule)
            print(f"WARNING: arXiv source extraction failed ({e}); dossier has citations only")
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
        src = SourceInfo(title=f"arXiv:{args.arxiv_id}", arxiv_id=args.arxiv_id, api="arxiv",
                         retrieved=_today(), file_path=str(src_dir), file_sha256=sha,
                         entitlement="open-access")
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
    d.add_argument("--arxiv", default=None, help="arXiv id for formula extraction (if not auto-found)")
    d.add_argument("--ref-limit", type=int, default=200)
    d.add_argument("--cite-limit", type=int, default=100)
    d.add_argument("--no-formulas", action="store_true", help="citations only, skip arXiv source")
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
