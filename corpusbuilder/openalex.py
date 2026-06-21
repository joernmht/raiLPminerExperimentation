"""OpenAlex acquisition — seed selection and bidirectional citations.

OpenAlex is free, CC0 and keyless: it gives both directions of the citation
graph (``referenced_works`` = what a paper cites, and a ``cites:`` filter = what
cites a paper). We use it as the citation spine; Scopus cross-check (Elsevier
key) is added later in :mod:`corpusbuilder.scopus`.

All list results are sorted by stable keys so a dossier built twice from the
same OpenAlex snapshot is identical. Network responses still change over time —
that is why the dossier records the retrieval date.
"""

from __future__ import annotations

import re

import requests

from corpusbuilder import config
from corpusbuilder.dossier import CitationRef, Dossier, SourceInfo

_BASE = "https://api.openalex.org"
_ARXIV_RE = re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5}|[a-z\-]+/[0-9]{7})", re.I)


class OpenAlexError(RuntimeError):
    pass


class OpenAlexClient:
    """Thin OpenAlex REST client (read-only)."""

    def __init__(self, mailto: str | None = None, timeout: float = 30.0) -> None:
        self.timeout = timeout
        # OpenAlex "polite pool": identify yourself for stabler rate limits.
        self.mailto = mailto or config.openalex_mailto()
        self._s = requests.Session()
        ua = f"raiLPminer-corpusbuilder/1 (mailto:{self.mailto})"
        self._s.headers.update({"User-Agent": ua, "Accept": "application/json"})

    def _get(self, path: str, params: dict | None = None) -> dict:
        params = dict(params or {})
        params.setdefault("mailto", self.mailto)
        r = self._s.get(f"{_BASE}/{path.lstrip('/')}", params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise OpenAlexError(f"OpenAlex {r.status_code} for {r.url}: {r.text[:200]}")
        return r.json()

    # -- raw work lookup ----------------------------------------------------

    def get_work(self, identifier: str) -> dict:
        """Fetch one work by DOI, arXiv id, or OpenAlex id.

        ``identifier`` may be a bare DOI (``10.x/..``), a DOI/OpenAlex URL, a
        ``W...`` id, or ``arXiv:1234.5678``. arXiv ids resolve via search.
        """
        ident = identifier.strip()
        _bare_arxiv = re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?", ident, re.I)
        if ident.lower().startswith("arxiv:") or _ARXIV_RE.search(ident) or _bare_arxiv:
            arxiv_id = ident.split(":", 1)[-1] if ":" in ident else ident
            m = _ARXIV_RE.search(ident)
            if m:
                arxiv_id = m.group(1)
            arxiv_id = re.sub(r"v\d+$", "", arxiv_id)  # OpenAlex indexes the versionless id
            # arXiv mints a DataCite DOI (10.48550/arXiv.<id>); that is the reliable key.
            try:
                return self._get(f"works/doi:10.48550/arXiv.{arxiv_id}")
            except OpenAlexError:
                pass
            # Fallback: search, but only accept a hit that actually has an arXiv location.
            hits = self._get("works", {"search": arxiv_id, "per-page": 5})
            for w in hits.get("results", []):
                if any(_ARXIV_RE.search(loc.get("landing_page_url") or "") for loc in w.get("locations", [])):
                    return w
            raise OpenAlexError(f"no OpenAlex work for arXiv id {arxiv_id!r}")
        if ident.startswith("W") and ident[1:].isdigit():
            return self._get(f"works/{ident}")
        if "openalex.org/W" in ident:
            return self._get(f"works/{ident.rstrip('/').rsplit('/', 1)[-1]}")
        # treat as DOI
        doi = ident.replace("https://doi.org/", "").replace("doi:", "")
        return self._get(f"works/doi:{doi}")

    # -- mapping ------------------------------------------------------------

    @staticmethod
    def _arxiv_of(work: dict) -> str | None:
        for loc in work.get("locations", []) or []:
            m = _ARXIV_RE.search(loc.get("landing_page_url") or "") or _ARXIV_RE.search(
                loc.get("pdf_url") or ""
            )
            if m:
                return m.group(1)
        return None

    @classmethod
    def to_source(cls, work: dict) -> SourceInfo:
        ids = work.get("ids", {}) or {}
        primary = work.get("primary_location") or {}
        src = primary.get("source") or {}
        oa_url = ids.get("openalex") or work.get("id") or ""
        doi = (work.get("doi") or "").replace("https://doi.org/", "") or None
        arxiv_id = cls._arxiv_of(work)
        if arxiv_id is None and doi and doi.lower().startswith("10.48550/arxiv."):
            arxiv_id = doi.split(".", 2)[-1]  # 10.48550/arXiv.2502.15544 -> 2502.15544
        return SourceInfo(
            title=work.get("display_name") or work.get("title") or "(untitled)",
            doi=doi,
            arxiv_id=arxiv_id,
            openalex_id=oa_url.rstrip("/").rsplit("/", 1)[-1] if oa_url else None,
            authors=[
                a.get("author", {}).get("display_name", "")
                for a in (work.get("authorships") or [])
                if a.get("author")
            ],
            year=work.get("publication_year"),
            venue=src.get("display_name"),
            publisher=src.get("host_organization_name"),
            cited_by_count=work.get("cited_by_count"),
            landing_url=(work.get("doi") or (primary.get("landing_page_url"))),
            api="openalex",
        )

    @classmethod
    def _ref(cls, work: dict) -> CitationRef:
        ids = work.get("ids", {}) or {}
        oa = ids.get("openalex") or work.get("id") or ""
        return CitationRef(
            title=work.get("display_name"),
            doi=(work.get("doi") or "").replace("https://doi.org/", "") or None,
            openalex_id=oa.rstrip("/").rsplit("/", 1)[-1] if oa else None,
            year=work.get("publication_year"),
            cited_by_count=work.get("cited_by_count"),
            source="openalex",
        )

    # -- citations (both directions) ---------------------------------------

    def references(self, work: dict, limit: int = 200) -> list[CitationRef]:
        """Works this paper cites, hydrated to titles (batched), sorted by citations."""
        ref_ids = [r.rstrip("/").rsplit("/", 1)[-1] for r in (work.get("referenced_works") or [])]
        ref_ids = ref_ids[:limit]
        out: list[CitationRef] = []
        for i in range(0, len(ref_ids), 50):  # OpenAlex OR-filter caps ~50/req
            batch = ref_ids[i : i + 50]
            data = self._get(
                "works",
                {"filter": f"openalex_id:{'|'.join(batch)}", "per-page": 50},
            )
            out.extend(self._ref(w) for w in data.get("results", []))
        out.sort(key=lambda r: (-(r.cited_by_count or 0), r.openalex_id or ""))
        return out

    def cited_by(self, work: dict, limit: int = 100) -> list[CitationRef]:
        """Works that cite this paper, most-cited first."""
        oa = (work.get("ids", {}) or {}).get("openalex") or work.get("id") or ""
        wid = oa.rstrip("/").rsplit("/", 1)[-1]
        data = self._get(
            "works",
            {"filter": f"cites:{wid}", "sort": "cited_by_count:desc", "per-page": min(limit, 200)},
        )
        return [self._ref(w) for w in data.get("results", [])]

    # -- high-level builders ------------------------------------------------

    def search_seeds(
        self, query: str, min_citations: int = 20, limit: int = 25
    ) -> list[SourceInfo]:
        """Candidate seed papers for the corpus, most-cited first.

        Filters to journal/conference articles with at least ``min_citations``
        citations matching ``query`` in title+abstract.
        """
        # ``title_and_abstract.search`` (a filter) restricts matching to the
        # title+abstract, unlike the broad ``search`` param which also matches
        # full text and lets off-topic mega-cited papers dominate the ranking.
        data = self._get(
            "works",
            {
                "filter": (
                    f"title_and_abstract.search:{query},"
                    f"cited_by_count:>{max(min_citations - 1, 0)},type:article"
                ),
                "sort": "cited_by_count:desc",
                "per-page": min(limit, 50),
            },
        )
        return [self.to_source(w) for w in data.get("results", [])]

    def build_dossier(
        self,
        identifier: str,
        retrieved: str,
        ref_limit: int = 200,
        cite_limit: int = 100,
    ) -> Dossier:
        """Build a citations-populated dossier for one paper (no formulas yet).

        ``retrieved`` is an explicit ISO date string — acquisition never reads
        the wall clock itself, so a caller can pin/replay it.
        """
        work = self.get_work(identifier)
        source = self.to_source(work)
        source.retrieved = retrieved
        refs = self.references(work, limit=ref_limit)
        citers = self.cited_by(work, limit=cite_limit)
        return Dossier(
            source=source,
            references=refs,
            cited_by=citers,
            references_count=len(work.get("referenced_works") or []),
            cited_by_count=work.get("cited_by_count"),
        )
