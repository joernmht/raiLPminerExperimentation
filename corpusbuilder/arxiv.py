"""arXiv Tier-1 formula extraction — equations straight from the LaTeX source.

This is the deterministic gold path: arXiv ships the author's ``.tex`` e-print,
so display equations are pulled **byte-exact** with no OCR. We download the
source tarball, concatenate its ``.tex`` files (comment-stripped, in sorted
order), and lift the standard display-math environments into
:class:`~corpusbuilder.dossier.FormulaRecord` objects.

Page spans are left ``None`` here — the ``.tex`` source has no page geometry;
the review app fills page numbers from the rendered PDF when needed.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import re
import tarfile
from pathlib import Path

import requests

from corpusbuilder.dossier import ExtractionMethod, FormulaRecord

_EPRINT = "https://arxiv.org/e-print/{id}"
_UA = "raiLPminer-corpusbuilder/1 (mailto:joern.maurischat@tu-dresden.de)"

# Display-math environments we lift verbatim. Order matters only for naming.
_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "flalign")
_ENV_RE = re.compile(
    r"\\begin\{(?P<env>(?:%s)\*?)\}(?P<body>.*?)\\end\{(?P=env)\}" % "|".join(_ENVS),
    re.DOTALL,
)
_BRACKET_RE = re.compile(r"(?<!\\)\\\[(?P<body>.*?)(?<!\\)\\\]", re.DOTALL)
_LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*?$", re.MULTILINE)


def normalize_arxiv_id(s: str) -> str:
    """Strip a URL/prefix down to a bare arXiv id (e.g. ``2103.04618``)."""
    s = s.strip()
    s = re.sub(r"^arxiv:", "", s, flags=re.I)
    m = re.search(r"((?:\d{4}\.\d{4,5})(?:v\d+)?|[a-z\-]+/\d{7}(?:v\d+)?)", s, re.I)
    return m.group(1) if m else s


def fetch_source(arxiv_id: str, dest_dir: str | Path, timeout: float = 60.0) -> tuple[Path, str]:
    """Download and unpack an arXiv e-print into ``dest_dir/<id>/``.

    Returns ``(extracted_dir, sha256_of_tarball)``. Raises if the e-print is
    PDF-only (no LaTeX source — fall back to a later extraction tier).
    """
    arxiv_id = normalize_arxiv_id(arxiv_id)
    r = requests.get(_EPRINT.format(id=arxiv_id), headers={"User-Agent": _UA}, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"arXiv e-print {arxiv_id}: HTTP {r.status_code}")
    raw = r.content
    sha = hashlib.sha256(raw).hexdigest()
    out = Path(dest_dir) / arxiv_id.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)

    if raw[:4] == b"%PDF":
        raise RuntimeError(f"arXiv {arxiv_id} is PDF-only (no LaTeX source)")
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            _safe_extract(tar, out)
    except tarfile.ReadError:
        # A single gzipped .tex file rather than a tarball.
        text = gzip.decompress(raw)
        (out / "main.tex").write_bytes(text)
    return out, sha


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball, refusing path-traversal members."""
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if not str(target).startswith(str(dest)):
            continue  # skip members escaping dest
        if m.isfile():
            tar.extract(m, dest)


def _strip_comments(tex: str) -> str:
    return _COMMENT_RE.sub("", tex)


def extract_equations_from_text(tex: str, source_file: str | None = None) -> list[FormulaRecord]:
    """Lift display-math environments and ``\\[ \\]`` blocks from one .tex string."""
    tex = _strip_comments(tex)
    found: list[tuple[int, str, str | None]] = []  # (position, body, label)
    for m in _ENV_RE.finditer(tex):
        body = m.group("body")
        label = _LABEL_RE.search(body)
        found.append((m.start(), body, label.group(1) if label else None))
    for m in _BRACKET_RE.finditer(tex):
        found.append((m.start(), m.group("body"), None))
    found.sort(key=lambda t: t[0])  # document order

    records: list[FormulaRecord] = []
    for i, (_pos, body, label) in enumerate(found, start=1):
        latex = _LABEL_RE.sub("", body).strip()
        if not latex:
            continue
        records.append(
            FormulaRecord(
                id=f"eq-{i:04d}",
                label=f"\\label{{{label}}}" if label else None,
                latex=latex,
                method=ExtractionMethod.arxiv_tex,
                source_file=source_file,
            )
        )
    return records


def extract_equations(tex_dir: str | Path) -> list[FormulaRecord]:
    """Extract equations from every ``.tex`` under ``tex_dir`` (sorted, re-id'd)."""
    files = sorted(Path(tex_dir).rglob("*.tex"))
    records: list[FormulaRecord] = []
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        records.extend(extract_equations_from_text(text, source_file=f.name))
    # Re-number across files so ids are unique and stable in document/file order.
    for i, rec in enumerate(records, start=1):
        rec.id = f"eq-{i:04d}"
    return records
