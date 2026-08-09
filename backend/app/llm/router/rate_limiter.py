"""Per-model RPM and TPM limiting, in-memory or Redis.

WHY LIMIT LOCALLY WHEN THE PROVIDER ALREADY DOES
    A 429 costs a round trip, and on a free tier it often costs more than
    that — some providers count rejected requests against the daily quota. If
    we know a model has issued 30 requests in the last minute and its limit is
    30, asking again is a guaranteed waste. Local limiting turns that into an
    instant failover to a model that CAN serve it.

    It is not a substitute for handling 429s. Our counters drift from the
    provider's (other processes, other keys, the provider's own window
    alignment), so both exist: predict locally, react to the truth.

SLIDING WINDOW, NOT TOKEN BUCKET
    A bucket permits a full burst the instant it refills, which is exactly the
    shape that trips a provider's per-minute cap. A sliding window answers the
    question the provider is actually asking — "how many in the last 60
    seconds?" — so it cannot admit a burst the provider will reject.

    The cost is memory proportional to requests-per-window. At tens of
    requests per minute that is nothing.

TOKENS ARE ESTIMATED BEFORE THE CALL
    TPM has to be checked before sending, when the output length is unknown.
    We reserve an estimate and reconcile with the true usage afterwards. The
    estimate is deliberately pessimistic: over-reserving costs a slightly early
    failover, under-reserving costs a 429.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, Protocol

log = logging.getLogger("vo.llm.ratelimit")

WINDOW = 60.0  # seconds; RPM and TPM are both per-minute


@dataclass
class Decision:
    allowed: bool
    reason: str = ""
    retry_after: float = 0.0


class RateLimiterBackend(Protocol):
    async def check_and_reserve(self, key: str, *, rpm: int, tpm: int,
                                est_tokens: int) -> Decision: ...
    async def reconcile(self, key: str, *, reserved: int, actual: int) -> None: ...
    async def penalise(self, key: str, seconds: float) -> None: ...
    async def snapshot(self, key: str) -> dict: ...


# ---------------------------------------------------------------------------
# In-memory — the default, and correct for a single-process deployment
# ---------------------------------------------------------------------------

@dataclass
class _Window:
    requests: list[float] = field(default_factory=list)          # timestamps
    tokens: list[tuple[float, int]] = field(default_factory=list)  # (ts, count)
    blocked_until: float = 0.0

    def prune(self, now: float) -> None:
        cutoff = now - WINDOW
        if self.requests and self.requests[0] < cutoff:
            self.requests = [t for t in self.requests if t >= cutoff]
        if self.tokens and self.tokens[0][0] < cutoff:
            self.tokens = [(t, n) for t, n in self.tokens if t >= cutoff]

    def token_total(self) -> int:
        return sum(n for _, n in self.tokens)


class InMemoryRateLimiter:
    """Process-local. Exactly right when there is one process, which is how
    this application deploys; see the router README on scaling out."""

    def __init__(self) -> None:
        self._w: dict[str, _Window] = {}
        self._lock = asyncio.Lock()

    def _win(self, key: str) -> _Window:
        if key not in self._w:
            self._w[key] = _Window()
        return self._w[key]

    async def check_and_reserve(self, key: str, *, rpm: int, tpm: int,
                                est_tokens: int) -> Decision:
        async with self._lock:
            now = time.monotonic()
            w = self._win(key)
            w.prune(now)

            if now < w.blocked_until:
                return Decision(False, "cooling down after a 429",
                                w.blocked_until - now)

            if rpm and len(w.requests) >= rpm:
                # Capacity returns when the oldest request leaves the window.
                return Decision(False, f"RPM {rpm} exhausted",
                                max(0.0, w.requests[0] + WINDOW - now))

            if tpm and w.token_total() + est_tokens > tpm:
                return Decision(False, f"TPM {tpm} would be exceeded",
                                max(0.0, (w.tokens[0][0] + WINDOW - now)
                                    if w.tokens else 1.0))

            w.requests.append(now)
            w.tokens.append((now, est_tokens))
            return Decision(True)

    async def reconcile(self, key: str, *, reserved: int, actual: int) -> None:
        """Replace the estimate with the truth, so the window stays honest."""
        if reserved == actual:
            return
        async with self._lock:
            w = self._win(key)
            for i in range(len(w.tokens) - 1, -1, -1):
                if w.tokens[i][1] == reserved:
                    w.tokens[i] = (w.tokens[i][0], actual)
                    return

    async def penalise(self, key: str, seconds: float) -> None:
        async with self._lock:
            self._win(key).blocked_until = time.monotonic() + max(0.0, seconds)

    async def snapshot(self, key: str) -> dict:
        async with self._lock:
            now = time.monotonic()
            w = self._win(key)
            w.prune(now)
            return {"requests_in_window": len(w.requests),
                    "tokens_in_window": w.token_total(),
                    "blocked_for": max(0.0, w.blocked_until - now)}


# ---------------------------------------------------------------------------
# Redis — for when more than one process shares a quota
# ---------------------------------------------------------------------------

_LUA = """
-- Sliding window over a sorted set, evaluated atomically.
-- Two processes checking the same limit concurrently would otherwise both
-- see room for one more request and both send it.
local rk, tk, bk = KEYS[1], KEYS[2], KEYS[3]
local now    = tonumber(ARGV[1])
local rpm    = tonumber(ARGV[2])
local tpm    = tonumber(ARGV[3])
local est    = tonumber(ARGV[4])
local window = tonumber(ARGV[5])
local member = ARGV[6]

local blocked = redis.call('GET', bk)
if blocked and tonumber(blocked) > now then
  return {0, 'cooldown', tostring(tonumber(blocked) - now)}
end

local cutoff = now - window
redis.call('ZREMRANGEBYSCORE', rk, '-inf', cutoff)
redis.call('ZREMRANGEBYSCORE', tk, '-inf', cutoff)

if rpm > 0 then
  local n = redis.call('ZCARD', rk)
  if n >= rpm then
    local oldest = redis.call('ZRANGE', rk, 0, 0, 'WITHSCORES')
    local wait = window
    if oldest[2] then wait = tonumber(oldest[2]) + window - now end
    return {0, 'rpm', tostring(wait)}
  end
end

if tpm > 0 then
  local used = 0
  for _, v in ipairs(redis.call('ZRANGE', tk, 0, -1)) do
    local sep = string.find(v, ':')
    used = used + tonumber(string.sub(v, sep + 1))
  end
  if used + est > tpm then
    local oldest = redis.call('ZRANGE', tk, 0, 0, 'WITHSCORES')
    local wait = window
    if oldest[2] then wait = tonumber(oldest[2]) + window - now end
    return {0, 'tpm', tostring(wait)}
  end
end

redis.call('ZADD', rk, now, member)
redis.call('ZADD', tk, now, member .. ':' .. est)
redis.call('EXPIRE', rk, math.ceil(window * 2))
redis.call('EXPIRE', tk, math.ceil(window * 2))
return {1, 'ok', '0'}
"""


class RedisRateLimiter:
    """Distributed sliding window. Falls back to in-memory if Redis is down.

    That fallback is deliberate. A limiter is a guard, not the system of
    record — if Redis is unreachable, degrading to process-local limiting
    (slightly too permissive across replicas) is better than failing every LLM
    request in the application.
    """

    def __init__(self, url: str, namespace: str = "llmrouter"):
        import redis.asyncio as aioredis           # imported lazily on purpose
        self._r = aioredis.from_url(url, encoding="utf-8", decode_responses=True)
        self._ns = namespace
        self._script = self._r.register_script(_LUA)
        self._local = InMemoryRateLimiter()        # the degraded path
        self._seq = 0

    def _keys(self, key: str) -> tuple[str, str, str]:
        return (f"{self._ns}:{key}:req", f"{self._ns}:{key}:tok",
                f"{self._ns}:{key}:block")

    async def check_and_reserve(self, key: str, *, rpm: int, tpm: int,
                                est_tokens: int) -> Decision:
        self._seq += 1
        member = f"{time.time():.6f}-{self._seq}"
        rk, tk, bk = self._keys(key)
        try:
            allowed, reason, wait = await self._script(
                keys=[rk, tk, bk],
                args=[time.time(), rpm, tpm, est_tokens, WINDOW, member])
        except Exception as exc:
            log.warning("redis limiter unavailable (%s); using local window", exc)
            return await self._local.check_and_reserve(
                key, rpm=rpm, tpm=tpm, est_tokens=est_tokens)
        if int(allowed) == 1:
            return Decision(True)
        return Decision(False, str(reason), float(wait))

    async def reconcile(self, key: str, *, reserved: int, actual: int) -> None:
        # The recorded estimate expires with the window; correcting it would
        # need a read-modify-write for a value that is about to vanish. Not
        # worth the round trip.
        return None

    async def penalise(self, key: str, seconds: float) -> None:
        _, _, bk = self._keys(key)
        try:
            await self._r.set(bk, time.time() + seconds, ex=int(seconds) + 1)
        except Exception as exc:
            log.warning("redis penalise failed (%s)", exc)
            await self._local.penalise(key, seconds)

    async def snapshot(self, key: str) -> dict:
        rk, tk, bk = self._keys(key)
        try:
            now = time.time()
            await self._r.zremrangebyscore(rk, "-inf", now - WINDOW)
            reqs = await self._r.zcard(rk)
            toks = sum(int(v.rsplit(":", 1)[1])
                       for v in await self._r.zrange(tk, 0, -1))
            blocked = await self._r.get(bk)
            return {"requests_in_window": reqs, "tokens_in_window": toks,
                    "blocked_for": max(0.0, float(blocked or 0) - now)}
        except Exception:
            return await self._local.snapshot(key)


def build_limiter(redis_url: Optional[str] = None) -> RateLimiterBackend:
    """Redis when a URL is configured and importable, in-memory otherwise."""
    if redis_url:
        try:
            return RedisRateLimiter(redis_url)
        except Exception as exc:
            log.warning("could not initialise Redis limiter (%s); "
                        "using in-memory", exc)
    return InMemoryRateLimiter()


def estimate_tokens(text: str) -> int:
    """Cheap character-based estimate, biased high.

    A real tokenizer would be more accurate and would mean shipping a
    per-provider vocabulary and paying to encode every prompt twice. ~3.6
    chars/token under-counts English slightly, which is the safe direction:
    reserving too much fails over early, reserving too little gets a 429.
    """
    return max(1, int(len(text) / 3.6) + 16)
