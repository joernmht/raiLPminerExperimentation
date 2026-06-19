"""Credential + endpoint loading for corpus acquisition.

Secrets live in a **gitignored ``<repo>/.env``** (``chmod 600``), mirroring the
other raiLPminer repos. This module loads that file into ``os.environ`` on
import (real environment variables win). A missing file or a missing
``python-dotenv`` is not fatal — keyless paths (OpenAlex, arXiv) still work.

    cp .env.example .env && chmod 600 .env && nano .env   # add the real keys

Never pass keys on the command line or print them; read them through these
accessors so they stay out of shell history and logs.
"""

from __future__ import annotations

import os
from pathlib import Path

#: Repo root (the directory that contains ``.env``).
PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_env() -> None:
    """Load ``<repo>/.env`` into ``os.environ`` if python-dotenv is available."""
    try:
        from dotenv import load_dotenv
    except ImportError:  # pragma: no cover - dotenv is a declared corpus extra
        return
    load_dotenv(PROJECT_ROOT / ".env")


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


def require(name: str) -> str:
    """Return a credential or raise a helpful error pointing at ``.env``."""
    value = _get(name)
    if not value:
        raise RuntimeError(
            f"{name} is not set. Add it to {PROJECT_ROOT / '.env'} "
            f"(copy .env.example to .env, chmod 600, fill in the key)."
        )
    return value
