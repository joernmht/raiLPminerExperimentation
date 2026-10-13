"""Shape checks for identifiers that are interpolated into request URLs.

The OpenAlex, Elsevier and arXiv clients put a DOI or an arXiv id into the URL
*path*, and ``requests`` normalises dot-segments before sending. A "DOI" such as
``10.1016/x/../../authors/A1?x=`` therefore became a request for a different
endpoint (the Elsevier one still carrying the API key), and an arXiv string that
matched no id pattern went verbatim into both the e-print URL and the extraction
directory name. Identifiers come from OpenAlex records or from the operator, so
the realistic case is a malformed value rather than an attack; either way it has
to fail before any request is made.

Dependency-free on purpose, so offline modules can import it.
"""

from __future__ import annotations

import re

__all__ = ["InvalidIdentifier", "check_arxiv_id", "check_doi"]

#: ``10.<registrant>/<suffix>``. A suffix may hold almost anything (Elsevier's
#: older DOIs carry parentheses), but not what ends or re-routes a URL path:
#: whitespace, ``?``, ``#`` or a backslash.
_DOI = re.compile(r"10\.\d{4,9}/[^\s?#\\]+")

#: New-style (``2103.04618``) or old-style (``hep-th/9901001``) id, optional
#: version: the same shapes ``arxiv.normalize_arxiv_id`` extracts.
_ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z\-]+/\d{7})(?:v\d+)?", re.IGNORECASE)


class InvalidIdentifier(ValueError):
    """An identifier does not have the shape it claims to have."""


def check_doi(doi: str) -> str:
    """Return ``doi`` unchanged if it is shaped like a DOI, else raise.

    ``.`` and ``..`` path segments are rejected too: no real DOI uses them, and
    they are exactly what re-routes a URL path.
    """
    if _DOI.fullmatch(doi) is None or any(seg in (".", "..") for seg in doi.split("/")):
        raise InvalidIdentifier(f"not a DOI: {doi!r}")
    return doi


def check_arxiv_id(arxiv_id: str) -> str:
    """Return ``arxiv_id`` unchanged if it is a bare arXiv id, else raise."""
    if _ARXIV.fullmatch(arxiv_id) is None:
        raise InvalidIdentifier(f"not an arXiv id: {arxiv_id!r}")
    return arxiv_id
