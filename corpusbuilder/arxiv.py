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

from corpusbuilder._http import (
    DEFAULT_POLICY,
    AcquisitionError,
    RetryPolicy,
    is_transient_status,
    request_with_retry,
)
from corpusbuilder.dossier import ExtractionMethod, FormulaRecord

_EPRINT = "https://arxiv.org/e-print/{id}"
_UA = "raiLPminer-corpusbuilder/1 (mailto:joern.maurischat@tu-dresden.de)"

# Display-math environments we lift verbatim. Order matters only for naming.
_ENVS = ("equation", "align", "gather", "multline", "eqnarray", "displaymath", "flalign")
_ENV_RE = re.compile(
    r"\\begin\{(?P<env>(?:" + "|".join(_ENVS) + r")\*?)\}(?P<body>.*?)\\end\{(?P=env)\}",
    re.DOTALL,
)
_BRACKET_RE = re.compile(r"(?<!\\)\\\[(?P<body>.*?)(?<!\\)\\\]", re.DOTALL)
_LABEL_RE = re.compile(r"\\label\{([^}]*)\}")
_COMMENT_RE = re.compile(r"(?<!\\)%.*?$", re.MULTILINE)


class ArxivError(AcquisitionError):
    """An arXiv e-print could not be fetched or unpacked.

    ``transient=True`` means "retry later" (arXiv sheds load with 503 +
    ``Retry-After``, and a truncated tarball is a download artefact).
    ``transient=False`` is a fact about the paper — e.g. it is PDF-only, which
    permanently rules out Tier-1 and legitimately drops it down the ladder.
    """


def normalize_arxiv_id(s: str) -> str:
    """Strip a URL/prefix down to a bare arXiv id (e.g. ``2103.04618``)."""
    s = s.strip()
    s = re.sub(r"^arxiv:", "", s, flags=re.I)
    m = re.search(r"((?:\d{4}\.\d{4,5})(?:v\d+)?|[a-z\-]+/\d{7}(?:v\d+)?)", s, re.I)
    return m.group(1) if m else s


def fetch_source(
    arxiv_id: str,
    dest_dir: str | Path,
    timeout: float = 60.0,
    retry: RetryPolicy = DEFAULT_POLICY,
) -> tuple[Path, str]:
    """Download and unpack an arXiv e-print into ``dest_dir/<id>/``.

    Returns ``(extracted_dir, sha256_of_tarball)``. Raises :class:`ArxivError`;
    check ``.transient`` to tell "arXiv is rate-limiting us" (retry later) from
    "this e-print is PDF-only" (a permanent fact — fall to a later tier).
    """
    arxiv_id = normalize_arxiv_id(arxiv_id)
    url = _EPRINT.format(id=arxiv_id)
    try:
        r = request_with_retry(
            lambda: requests.get(url, headers={"User-Agent": _UA}, timeout=timeout),
            policy=retry,
            describe=f"arXiv e-print {arxiv_id}",
        )
    except requests.RequestException as e:
        raise ArxivError(f"arXiv e-print {arxiv_id}: {e}", transient=True) from e
    if r.status_code != 200:
        # arXiv sheds load with 503 + Retry-After; that is transient, a 404 is not.
        raise ArxivError(
            f"arXiv e-print {arxiv_id}: HTTP {r.status_code}",
            status=r.status_code,
            transient=is_transient_status(r.status_code),
        )
    raw = r.content
    sha = hashlib.sha256(raw).hexdigest()
    out = Path(dest_dir) / arxiv_id.replace("/", "_")
    out.mkdir(parents=True, exist_ok=True)

    if raw[:4] == b"%PDF":
        raise ArxivError(f"arXiv {arxiv_id} is PDF-only (no LaTeX source)", transient=False)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:*") as tar:
            _safe_extract(tar, out)
    except tarfile.ReadError:
        # A single gzipped .tex file rather than a tarball.
        try:
            text = gzip.decompress(raw)
        except (gzip.BadGzipFile, EOFError, OSError) as e:
            # Neither a tarball nor valid gzip: almost always a truncated body.
            raise ArxivError(
                f"arXiv {arxiv_id}: corrupt or truncated e-print archive ({e})", transient=True
            ) from e
        (out / "main.tex").write_bytes(text)
    return out, sha


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract a tarball, refusing path-traversal members.

    arXiv e-prints are author-controlled and therefore untrusted. We extract
    **regular files only** (symlink/hardlink/device members are ignored, closing
    link-based escapes) and require each resolved target to sit inside ``dest``.
    Containment is checked with :meth:`Path.is_relative_to`, *not* a string
    ``startswith`` — the latter falsely accepts sibling dirs that merely share
    the destination's name prefix (e.g. ``.../2103.04618`` vs
    ``.../2103.04618_evil/x``), which is an escape.
    """
    dest = dest.resolve()
    for m in tar.getmembers():
        if not m.isfile():
            continue  # skip symlinks/hardlinks/dirs/devices
        target = (dest / m.name).resolve()
        if not target.is_relative_to(dest):
            continue  # skip members escaping dest
        # filter="data" is a second, stdlib-native guard (rejects abs paths,
        # ``..``, links, special files) and the Python-3.14-ready default.
        tar.extract(m, dest, filter="data")


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
