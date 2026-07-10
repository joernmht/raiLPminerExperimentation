"""Transient-fault handling for the acquisition boundary (OpenAlex / arXiv / Elsevier).

Every upstream this package talks to rate-limits or sheds load, and each signals
it differently: OpenAlex returns **429**, arXiv returns **503 + ``Retry-After``**,
Elsevier returns **429** on quota exhaustion, and the SSH SOCKS tunnel used for
entitled Elsevier full text (ADR-0003) dies as a ``requests.ProxyError``. Without
retries, a single blip anywhere aborts a multi-hour acquisition run.

Two ideas carry the module:

**Retry only what is worth retrying.** :data:`RETRYABLE_STATUS` and
:func:`is_transient_exception` draw the line. Connection resets, timeouts and
overload statuses are retried with jittered exponential backoff, honouring an
upstream ``Retry-After`` when it sends one. A 404 is returned immediately — it is
an answer, not a fault.

**A transient fault is not coverage information.** This is the load-bearing rule
for Paper 1. The PRISMA tally derives *permanent* exclusion reasons from what a
dossier ends up containing (``prisma.py``: an empty ``formulas`` list becomes
``not_entitled`` / ``no_machine_readable_formulas`` / ``awaiting_tier3_pdf``). If
a dropped tunnel is allowed to look like "this paper has no machine-readable
formulas", network weather silently enters the paper's PRISMA flow diagram. So
every error carries a :attr:`AcquisitionError.transient` flag, and callers that
would otherwise *record* the failure must abort instead (see ``cli.cmd_dossier``).

Jitter and wall-clock reads live here rather than in ``railpminer/``: ADR-0001
puts acquisition explicitly outside the determinism boundary. ``sleep``, ``rng``
and ``now`` are all injectable so the tests stay deterministic and instant.
"""

from __future__ import annotations

import email.utils
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

import requests

__all__ = [
    "DEFAULT_POLICY",
    "RETRYABLE_STATUS",
    "AcquisitionError",
    "RetryPolicy",
    "is_transient_exception",
    "is_transient_status",
    "parse_retry_after",
    "request_with_retry",
]

#: Statuses that mean "try again later", not "here is your answer".
#: 408 request-timeout, 425 too-early, 429 rate-limited, 5xx overload//upstream.
#: Deliberately excludes 4xx client errors (400/401/403/404): retrying a bad DOI
#: or a missing API key just burns quota and delays an inevitable failure.
RETRYABLE_STATUS = frozenset({408, 425, 429, 500, 502, 503, 504})

#: requests exceptions that indicate a transport-level blip rather than a bug.
#: ``ProxyError`` (a ``ConnectionError`` subclass) is the SOCKS-tunnel death case.
_TRANSIENT_EXC: tuple[type[Exception], ...] = (
    requests.exceptions.ConnectionError,  # incl. ProxyError, SSLError-on-reset
    requests.exceptions.Timeout,  # incl. ConnectTimeout, ReadTimeout
    requests.exceptions.ChunkedEncodingError,  # truncated response body
)


class AcquisitionError(RuntimeError):
    """Base for the acquisition clients' domain errors.

    Subclasses ``RuntimeError`` so existing ``except (…, RuntimeError)`` handlers
    keep working. Carries the upstream ``status`` when there was one and, more
    importantly, whether the failure is :attr:`transient` — i.e. whether a later
    re-run could plausibly succeed. Callers must never persist a corpus record
    derived from a transient failure.
    """

    def __init__(self, message: str, *, status: int | None = None, transient: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.transient = transient


@dataclass(frozen=True)
class RetryPolicy:
    """How hard to try before giving up on a transient fault."""

    attempts: int = 4  # total tries, so 3 retries after the first call
    base_delay: float = 1.0  # seconds; doubled each attempt
    max_delay: float = 30.0  # cap on a single computed backoff
    max_retry_after: float = 300.0  # never honour an absurd upstream Retry-After
    jitter: bool = True  # full jitter: uniform(0, delay), avoids thundering herds


DEFAULT_POLICY = RetryPolicy()


def is_transient_status(status: int | None) -> bool:
    """True if an HTTP status means "try again later"."""
    return status in RETRYABLE_STATUS


def is_transient_exception(exc: BaseException) -> bool:
    """True if ``exc`` is a transport blip or an :class:`AcquisitionError` flagged transient.

    Used by the tier ladder to decide between *degrade and record* (permanent:
    the paper really has no arXiv source) and *abort and re-run later*
    (transient: the network broke, and recording that would corrupt PRISMA).
    """
    if isinstance(exc, AcquisitionError):
        return exc.transient
    return isinstance(exc, _TRANSIENT_EXC)


def parse_retry_after(
    value: str | None, *, now: float | None = None, cap: float = 300.0
) -> float | None:
    """Parse a ``Retry-After`` header into a non-negative delay in seconds.

    Supports both RFC 9110 forms: delta-seconds (``"120"``) and an HTTP-date
    (``"Wed, 21 Oct 2015 07:28:00 GMT"``). Returns ``None`` if absent or
    unparseable, and clamps to ``[0, cap]`` so a hostile or buggy upstream cannot
    park the run for a day.
    """
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    try:
        seconds = float(int(value))
    except ValueError:
        try:
            when = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if when is None:
            return None
        reference = time.time() if now is None else now
        seconds = when.timestamp() - reference
    return max(0.0, min(seconds, cap))


def _backoff(attempt: int, policy: RetryPolicy, rng: random.Random) -> float:
    """Exponential backoff for a 0-indexed ``attempt``, with optional full jitter."""
    delay = min(policy.base_delay * (2**attempt), policy.max_delay)
    return rng.uniform(0.0, delay) if policy.jitter else delay


def request_with_retry(
    send: Callable[[], requests.Response],
    *,
    policy: RetryPolicy = DEFAULT_POLICY,
    sleep: Callable[[float], None] = time.sleep,
    rng: random.Random | None = None,
    now: Callable[[], float] = time.time,
    describe: str = "request",
    on_retry: Callable[[int, float, str], None] | None = None,
) -> requests.Response:
    """Call ``send()`` with retries on transient faults; return the final response.

    ``send`` is a zero-argument thunk so callers keep full control of the session,
    URL, params, proxies and timeout. Retries cover both transport exceptions and
    :data:`RETRYABLE_STATUS` responses; an upstream ``Retry-After`` wins over the
    computed backoff.

    Returns the last :class:`requests.Response` even when it carries a retryable
    status and the attempts ran out — the caller owns the status→domain-error
    mapping and can see that the status was transient via
    :func:`is_transient_status`. Non-retryable transport exceptions and the final
    transport exception after exhaustion propagate unchanged.
    """
    rng = rng or random.Random()
    last_exc: Exception | None = None

    for attempt in range(policy.attempts):
        final = attempt == policy.attempts - 1
        try:
            response = send()
        except Exception as exc:
            if not is_transient_exception(exc) or final:
                raise
            last_exc = exc
            delay = _backoff(attempt, policy, rng)
            if on_retry:
                on_retry(attempt + 1, delay, f"{describe}: {type(exc).__name__}: {exc}")
            sleep(delay)
            continue

        if not is_transient_status(response.status_code) or final:
            return response

        retry_after = parse_retry_after(
            response.headers.get("Retry-After"), now=now(), cap=policy.max_retry_after
        )
        delay = retry_after if retry_after is not None else _backoff(attempt, policy, rng)
        if on_retry:
            on_retry(attempt + 1, delay, f"{describe}: HTTP {response.status_code}")
        sleep(delay)

    # Unreachable: the final attempt always returns or raises.
    raise last_exc or RuntimeError(f"{describe}: retry loop exhausted")
