"""Elsevier ScienceDirect (Tier-2) + Scopus client.

Tier-2 of the formula-extraction ladder: the ScienceDirect Article Retrieval API
returns full-text XML in which each display equation is a ``<ce:formula>``
carrying Presentation MathML, which we convert deterministically to LaTeX
(:mod:`corpusbuilder.mathml`). Scopus supplies an authoritative cited-by count
to cross-check OpenAlex.

**Entitlement.** Full text needs institutional access — either an
``ELSEVIER_INSTTOKEN`` or requests routed from a campus IP. Off-campus without a
token you get metadata only (~a few KB, no ``<ce:formula>``); we detect that and
say so rather than emit empty formulas. Set ``ELSEVIER_PROXY`` to an SSH SOCKS
tunnel (``ssh -D 8080 -N -f <zih-login>@login1.zih.tu-dresden.de`` ->
``socks5h://127.0.0.1:8080``) to borrow a TU Dresden IP.
"""

from __future__ import annotations

import requests
from lxml import etree

from corpusbuilder import config
from corpusbuilder.dossier import ExtractionMethod, FormulaRecord, VerificationStatus
from corpusbuilder.mathml import mathml_to_latex

_BASE = "https://api.elsevier.com/content"
_NS = {
    "ce": "http://www.elsevier.com/xml/common/dtd",
    "mml": "http://www.w3.org/1998/Math/MathML",
}
# DOI registrant prefixes that route to ScienceDirect full text.
_ELSEVIER_PREFIXES = ("10.1016/", "10.1006/", "10.1053/", "10.1078/", "10.3182/")


def is_elsevier_doi(doi: str | None) -> bool:
    return bool(doi) and doi.startswith(_ELSEVIER_PREFIXES)


class ElsevierError(RuntimeError):
    pass


class ElsevierClient:
    """ScienceDirect + Scopus REST client (read-only)."""

    def __init__(
        self,
        api_key: str | None = None,
        insttoken: str | None = None,
        proxy: str | None = None,
        timeout: float = 90.0,
    ) -> None:
        self.api_key = api_key or config.require("ELSEVIER_API_KEY")
        self.insttoken = insttoken if insttoken is not None else config.elsevier_insttoken()
        self.proxies = {"http": proxy, "https": proxy} if proxy else config.proxies()
        self.timeout = timeout
        self._s = requests.Session()
        headers = {"X-ELS-APIKey": self.api_key, "User-Agent": "raiLPminer-corpusbuilder/1"}
        if self.insttoken:
            headers["X-ELS-Insttoken"] = self.insttoken
        self._s.headers.update(headers)

    def _get(self, path: str, accept: str, params: dict | None = None) -> requests.Response:
        return self._s.get(
            f"{_BASE}/{path}",
            params=params,
            headers={"Accept": accept},
            proxies=self.proxies,
            timeout=self.timeout,
        )

    # -- full text ----------------------------------------------------------

    def full_text_xml(self, doi: str) -> str:
        r = self._get(f"article/doi/{doi}", "text/xml")
        if r.status_code != 200:
            raise ElsevierError(f"Elsevier HTTP {r.status_code} for {doi}: {r.text[:200]}")
        return r.text

    @staticmethod
    def has_full_text(xml: str) -> bool:
        """True if the XML carries article body / formulas (not metadata-only)."""
        return "<ce:formula" in xml or "<ce:sections" in xml or "<ce:para" in xml

    def extract_formulas(self, xml: str) -> list[FormulaRecord]:
        """One :class:`FormulaRecord` per ``<ce:formula>`` (display equations).

        Inline single-symbol math elsewhere in the body is intentionally skipped
        — only the labeled formula objects are corpus-relevant.
        """
        root = etree.fromstring(xml.encode("utf-8"))
        formulas = root.findall(".//ce:formula", _NS)
        mml_strings: list[str] = []
        labels: list[str | None] = []
        for f in formulas:
            math = f.find(".//mml:math", _NS)
            if math is None:
                continue  # image-only / non-MathML formula; skip (review via PDF)
            mml_strings.append(etree.tostring(math, encoding="unicode"))
            labels.append(f.get("id"))
        converted = mathml_to_latex(mml_strings)
        records: list[FormulaRecord] = []
        for i, (conv, label, mml) in enumerate(zip(converted, labels, mml_strings, strict=True), 1):
            records.append(
                FormulaRecord(
                    id=f"eq-{i:04d}",
                    label=label,
                    latex=conv.latex,
                    mathml=mml,
                    method=ExtractionMethod.mathml,
                    status=VerificationStatus.unreviewed,
                    note=None if conv.ok else f"mathml->latex failed: {conv.error}",
                )
            )
        return records

    # -- Scopus -------------------------------------------------------------

    def scopus_cited_by_count(self, doi: str) -> int | None:
        """Authoritative Scopus citation count for a DOI (None if not indexed)."""
        r = self._get(
            "search/scopus",
            "application/json",
            params={"query": f"DOI({doi})", "field": "citedby-count"},
        )
        if r.status_code != 200:
            return None
        entries = r.json().get("search-results", {}).get("entry", [{}])
        count = entries[0].get("citedby-count") if entries else None
        return int(count) if count is not None and str(count).isdigit() else None
