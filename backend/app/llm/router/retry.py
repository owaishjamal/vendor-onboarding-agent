"""What is worth retrying, and how long to wait.

TWO KINDS OF FAILURE
    Transient — 408, 429, 500, 502, 503, 504, timeouts, dropped connections.
        The same request might work in a moment. Retrying is reasonable.

    Permanent — 400, 401, 403, 404, 422, content policy, unknown model.
        The request is wrong, or we are not allowed. Retrying produces the
        same answer more slowly and, on a 401, can get a key locked. The
        router fails over to a different model instead: if the cause was
        provider-specific (a bad key, a retired model ID) another provider
        succeeds; if the request itself is malformed, everything fails fast
        and the caller gets a real error rather than a hung request.

JITTER IS NOT OPTIONAL
    Without it, every request that hits the same 429 retries at the same
    moment and reproduces the burst that caused it. Full jitter — a uniform
    draw from [0, backoff] — spreads a synchronised herd out.
"""

from __future__ import annotations

import random
from typing import Optional

from backend.app.llm.router.schemas import (
    LLMError, PermanentError, RateLimitError, TransientError,
)

RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 529}
PERMANENT_STATUS = {400, 401, 403, 404, 405, 413, 415, 422}

# Substrings that identify a rate limit when the status code does not.
# Gemini answers 429 with RESOURCE_EXHAUSTED, but some gateways surface the
# same condition as a 503 with the phrase in the body.
_RATE_LIMIT_MARKERS = (
    "rate limit", "rate_limit", "resource_exhausted", "quota", "too many requests",
    "429",
)

_PERMANENT_MARKERS = (
    "invalid api key", "unauthorized", "permission denied", "invalid_argument",
    "model not found", "does not exist", "content policy", "safety",
    "invalid request",
)


def classify(exc: BaseException, *, status_code: Optional[int] = None,
             body: str = "", provider: str = "", model: str = "") -> LLMError:
    """Turn any provider failure into one of our three error types.

    Status code first, because it is unambiguous when present. Body text is a
    fallback for providers that return 200 with an error payload, or a
    non-standard code for a standard condition.
    """
    if isinstance(exc, LLMError):
        return exc

    text = (body or str(exc)).lower()

    if status_code in PERMANENT_STATUS:
        # A 429 is never in PERMANENT_STATUS, so this cannot swallow one.
        return PermanentError(_msg(exc, body), status_code=status_code,
                              provider=provider, model=model)

    if status_code == 429 or any(m in text for m in _RATE_LIMIT_MARKERS):
        return RateLimitError(_msg(exc, body), status_code=status_code or 429,
                              retry_after=parse_retry_after(body),
                              provider=provider, model=model)

    if status_code in RETRYABLE_STATUS:
        return TransientError(_msg(exc, body), status_code=status_code,
                              provider=provider, model=model)

    if any(m in text for m in _PERMANENT_MARKERS):
        return PermanentError(_msg(exc, body), status_code=status_code,
                              provider=provider, model=model)

    if isinstance(exc, (TimeoutError, ConnectionError, OSError)):
        return TransientError(_msg(exc, body), provider=provider, model=model)

    # Unknown. Treated as transient so one odd failure fails over rather than
    # aborting the caller — the retry budget is small (2) and the breaker
    # catches anything that keeps happening.
    return TransientError(_msg(exc, body), status_code=status_code,
                          provider=provider, model=model)


def _msg(exc: BaseException, body: str) -> str:
    base = str(exc) or type(exc).__name__
    return f"{base}: {body[:300]}" if body and body[:300] not in base else base


def parse_retry_after(text: str) -> Optional[float]:
    """Pull a Retry-After value out of a header or an error body.

    Providers express it inconsistently: a bare header, `"retryDelay": "7s"`
    in a Gemini error, or prose like "try again in 2.5s". Reading it is worth
    the parsing — the provider knows exactly how long the window has left, and
    our estimate does not.
    """
    if not text:
        return None
    import re
    for pattern in (r'retry[-_]?after["\s:]+([0-9.]+)',
                    r'retrydelay["\s:]+"?([0-9.]+)s?',
                    r'try again in ([0-9.]+)\s*s',
                    r'in ([0-9.]+) seconds'):
        m = re.search(pattern, text, re.I)
        if m:
            try:
                v = float(m.group(1))
                # Sanity bound: a malformed parse yielding 86400 would take a
                # model out for a day.
                return v if 0 < v <= 300 else None
            except ValueError:
                pass
    return None


def backoff_delay(attempt: int, *, base: float = 0.5, cap: float = 8.0) -> float:
    """Exponential backoff with full jitter. `attempt` is 0-based."""
    ceiling = min(cap, base * (2 ** attempt))
    return random.uniform(0, ceiling)


def should_retry(err: LLMError, attempt: int, max_retries: int) -> bool:
    return err.retryable and attempt < max_retries
