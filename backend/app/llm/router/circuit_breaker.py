"""Circuit breaker per model.

    CLOSED ──(N consecutive failures)──► OPEN
      ▲                                   │
      │                             (cooldown elapses)
      │                                   ▼
      └────(probe succeeds)──────── HALF_OPEN ──(probe fails)──► OPEN

WHY A BREAKER AND NOT JUST RETRIES
    Retries handle a request that failed for a reason specific to that
    request. A breaker handles a model that is failing for everyone: once
    three requests in a row have failed, the fourth will almost certainly
    fail too, and trying costs the caller the full timeout before failover.
    Opening the circuit turns that timeout into an instant skip.

HALF_OPEN IS ONE REQUEST, NOT A TRICKLE
    When the cooldown elapses, exactly one caller is allowed through to test
    the water. Letting several through means a still-broken provider takes
    several more requests down with it; letting none through means the model
    never recovers. The flag is taken atomically so concurrent callers cannot
    all decide they are the probe.

RATE LIMITS DO NOT COUNT AS FAILURES
    A 429 means the model is healthy and busy. Feeding those to the breaker
    would open the circuit on a model that is working perfectly, and it would
    stay open long after the window cleared. Rate limiting is the limiter's
    job; the breaker only tracks things that look like breakage.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

from backend.app.llm.router.schemas import BreakerState

log = logging.getLogger("vo.llm.breaker")


@dataclass
class _Circuit:
    state: BreakerState = BreakerState.CLOSED
    consecutive_failures: int = 0
    opened_at: float = 0.0
    cooldown: float = 30.0
    probe_in_flight: bool = False
    total_failures: int = 0
    total_successes: int = 0
    last_error: str = ""


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 3, cooldown_seconds: float = 30.0,
                 max_cooldown_seconds: float = 300.0):
        self.threshold = failure_threshold
        self.base_cooldown = cooldown_seconds
        self.max_cooldown = max_cooldown_seconds
        self._c: dict[str, _Circuit] = {}
        self._lock = asyncio.Lock()

    def _circuit(self, key: str) -> _Circuit:
        if key not in self._c:
            self._c[key] = _Circuit(cooldown=self.base_cooldown)
        return self._c[key]

    async def allows(self, key: str) -> bool:
        """May a request go to this model right now?

        Also performs the OPEN → HALF_OPEN transition, because the only
        reliable moment to notice a cooldown has elapsed is when someone asks.
        A background sweeper would be a timer and a thread for no benefit.
        """
        async with self._lock:
            c = self._circuit(key)
            if c.state is BreakerState.CLOSED:
                return True

            if c.state is BreakerState.OPEN:
                if time.monotonic() - c.opened_at < c.cooldown:
                    return False
                c.state = BreakerState.HALF_OPEN
                c.probe_in_flight = True
                log.info("breaker %s: OPEN -> HALF_OPEN (probing)", key)
                return True

            # HALF_OPEN: exactly one probe at a time.
            if not c.probe_in_flight:
                c.probe_in_flight = True
                return True
            return False

    async def record_success(self, key: str) -> None:
        async with self._lock:
            c = self._circuit(key)
            c.total_successes += 1
            c.consecutive_failures = 0
            c.probe_in_flight = False
            if c.state is not BreakerState.CLOSED:
                log.info("breaker %s: %s -> CLOSED", key, c.state.value)
                c.state = BreakerState.CLOSED
                c.cooldown = self.base_cooldown      # reset the escalation
            c.last_error = ""

    async def record_failure(self, key: str, error: str = "") -> None:
        async with self._lock:
            c = self._circuit(key)
            c.total_failures += 1
            c.consecutive_failures += 1
            c.last_error = error[:200]

            if c.state is BreakerState.HALF_OPEN:
                # The probe failed: straight back to OPEN, and wait longer this
                # time. A model that fails its probe is more broken than one
                # that just started failing, so re-probing at the same interval
                # would hammer it.
                c.probe_in_flight = False
                c.cooldown = min(c.cooldown * 2, self.max_cooldown)
                c.state = BreakerState.OPEN
                c.opened_at = time.monotonic()
                log.warning("breaker %s: probe failed, OPEN for %.0fs", key, c.cooldown)
                return

            if (c.state is BreakerState.CLOSED
                    and c.consecutive_failures >= self.threshold):
                c.state = BreakerState.OPEN
                c.opened_at = time.monotonic()
                log.warning("breaker %s: %d consecutive failures, OPEN for %.0fs (%s)",
                            key, c.consecutive_failures, c.cooldown, c.last_error)

    async def force_open(self, key: str, seconds: float) -> None:
        """Open the circuit for a stated period.

        Used when a provider tells us how long to wait (Retry-After). Honouring
        that is strictly better than guessing, and ignoring it is how an
        integration gets its key throttled.
        """
        async with self._lock:
            c = self._circuit(key)
            c.state = BreakerState.OPEN
            c.opened_at = time.monotonic()
            c.cooldown = max(seconds, 0.1)
            c.probe_in_flight = False

    def state_of(self, key: str) -> BreakerState:
        return self._c[key].state if key in self._c else BreakerState.CLOSED

    def snapshot(self) -> dict[str, dict]:
        now = time.monotonic()
        return {
            k: {"state": c.state.value,
                "consecutive_failures": c.consecutive_failures,
                "total_failures": c.total_failures,
                "total_successes": c.total_successes,
                "cooldown_remaining": (
                    max(0.0, c.cooldown - (now - c.opened_at))
                    if c.state is BreakerState.OPEN else 0.0),
                "last_error": c.last_error}
            for k, c in self._c.items()
        }

    def reset(self) -> None:
        self._c.clear()
