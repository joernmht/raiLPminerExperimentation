"""Credential + endpoint loading for corpus acquisition.

Secrets are read from two places, in order:

1. The **machine-wide store** ``~/.config/raiLP/secrets.env`` (``chmod 600``) —
   the single place to keep keys shared across the raiLP* tooling.
2. An optional **repo-local ``<repo>/.env``** which overrides the machine-wide
   store for this checkout only.

Real shell environment variables take precedence over both. A missing file or a
missing ``python-dotenv`` is not fatal — keyless paths (OpenAlex, arXiv) still
work. Read keys through the accessors below so they stay out of shell history
and logs.

    nano ~/.config/raiLP/secrets.env    # add ELSEVIER_API_KEY etc., once, machine-wide
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repo root (may hold an optional repo-local ``.env`` override).
PROJECT_ROOT = Path(__file__).resolve().parent.parent
#: Machine-wide secrets store shared across the raiLP* tooling.
CENTRAL_ENV = Path.home() / ".config" / "raiLP" / "secrets.env"


def load_env() -> None:
    """Populate ``os.environ`` from the secret files, ignoring empty values.

    Precedence (high to low): a genuinely-set (non-empty) shell env var, then the
    repo-local ``.env``, then the machine-wide store. **Empty** values never
    count — neither an ``X=`` placeholder in a file nor an injected ``X=""`` from
    the harness is allowed to shadow a real value from a lower layer.
    """
    try:
        from dotenv import dotenv_values
    except ImportError:  # pragma: no cover - dotenv is a declared corpus extra
        return
    merged: dict[str, str] = {}
    for path in (CENTRAL_ENV, PROJECT_ROOT / ".env"):  # low -> high precedence
        if path.exists():
            merged.update({k: v for k, v in dotenv_values(path).items() if v})
    for key, value in merged.items():
        if not os.environ.get(key):  # fill only if unset or empty in the shell
            os.environ[key] = value


load_env()


def _get(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() or None if value else None


def elsevier_api_key() -> str | None:
    return _get("ELSEVIER_API_KEY")


def elsevier_insttoken() -> str | None:
    return _get("ELSEVIER_INSTTOKEN")


def scopus_api_key() -> str | None:
    return _get("SCOPUS_API_KEY") or elsevier_api_key()


def deepseek_api_key() -> str | None:
    return _get("DEEPSEEK_API_KEY")


def openalex_mailto() -> str:
    return _get("OPENALEX_MAILTO") or "joern.maurischat@tu-dresden.de"


def corpus_proxy() -> str | None:
    """Proxy URL for entitled Elsevier full-text (e.g. an SSH SOCKS tunnel).

    Set ``ELSEVIER_PROXY=socks5h://127.0.0.1:8080`` after
    ``ssh -D 8080 -N -f <zih-login>@login1.zih.tu-dresden.de`` to route requests
    out through a TU Dresden campus IP.
    """
    return _get("ELSEVIER_PROXY") or _get("CORPUS_PROXY")


def proxies() -> dict[str, str] | None:
    """requests-style proxies dict, or None if no proxy is configured."""
    p = corpus_proxy()
    return {"http": p, "https": p} if p else None


def require(name: str) -> str:
    """Return a credential or raise a helpful error pointing at ``.env``."""
    value = _get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to {PROJECT_ROOT / '.env'} "
            f"(copy .env.example to .env, chmod 600, fill in the key)."
        )
    return value
