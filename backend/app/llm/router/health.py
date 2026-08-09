"""Live health state per model, assembled from the limiter and the breaker.

This is a VIEW, not a second source of truth. The limiter knows about rate
limits; the breaker knows about failures. Health composes them into the single
question scoring needs answered — "can this model take a request right now,
and if not, when?" — plus the rolling latency the scorer uses to prefer fast
models when everything else is equal.

Keeping it derived matters: a separately-maintained health flag drifts from
the things it claims to summarise, and then the router starts avoiding a model
that recovered ten minutes ago.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from backend.app.llm.router.circuit_breaker import CircuitBreaker
from backend.app.llm.router.model_registry import ModelSpec
from backend.app.llm.router.rate_limiter import RateLimiterBackend
from backend.app.llm.router.schemas import BreakerState, HealthState


@dataclass
class Availability:
    available: bool
    state: HealthState
    reason: str = ""
    retry_after: float = 0.0


@dataclass
class _Stats:
    # Bounded: this is a rolling picture, not a metrics store. Prometheus
    # holds the history; this holds just enough to make a routing decision.
    latencies_ms: deque = field(default_factory=lambda: deque(maxlen=32))
    last_success: float = 0.0
    last_failure: float = 0.0
    rate_limit_hits: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return (sum(self.latencies_ms) / len(self.latencies_ms)
                if self.latencies_ms else 0.0)


class HealthTracker:
    def __init__(self, limiter: RateLimiterBackend, breaker: CircuitBreaker):
        self._limiter = limiter
        self._breaker = breaker
        self._stats: dict[str, _Stats] = {}
        self._disabled: set[str] = set()

    def _s(self, key: str) -> _Stats:
        if key not in self._stats:
            self._stats[key] = _Stats()
        return self._stats[key]

    # -- reads --------------------------------------------------------------

    async def availability(self, spec: ModelSpec, est_tokens: int) -> Availability:
        """Can this model take a request now?

        Order matters and is cheapest-first: an operator switch, then an
        in-memory breaker check, then the limiter (which may be a Redis round
        trip). No point paying for Redis to learn about a model that is
        switched off.

        NOTE this RESERVES capacity when it returns available. Callers must
        actually make the request or the reservation ages out of the window
        holding capacity it never used.
        """
        if spec.key in self._disabled or not spec.enabled:
            return Availability(False, HealthState.DISABLED, "disabled")

        if not await self._breaker.allows(spec.key):
            snap = self._breaker.snapshot().get(spec.key, {})
            return Availability(
                False, HealthState.COOLDOWN,
                f"circuit open ({snap.get('last_error') or 'repeated failures'})",
                snap.get("cooldown_remaining", 0.0))

        decision = await self._limiter.check_and_reserve(
            spec.key, rpm=spec.rpm, tpm=spec.tpm, est_tokens=est_tokens)
        if not decision.allowed:
            return Availability(False, HealthState.RATE_LIMITED,
                                decision.reason, decision.retry_after)

        return Availability(True, HealthState.HEALTHY)

    def state_of(self, spec: ModelSpec) -> HealthState:
        """Best-effort state for reporting. Does not reserve capacity."""
        if spec.key in self._disabled or not spec.enabled:
            return HealthState.DISABLED
        bs = self._breaker.state_of(spec.key)
        if bs is BreakerState.OPEN:
            return HealthState.COOLDOWN
        if bs is BreakerState.HALF_OPEN:
            return HealthState.ERROR
        return HealthState.HEALTHY

    def avg_latency_ms(self, key: str) -> float:
        return self._s(key).avg_latency_ms

    # -- writes -------------------------------------------------------------

    async def record_success(self, spec: ModelSpec, *, latency_ms: int,
                             reserved_tokens: int, actual_tokens: int) -> None:
        s = self._s(spec.key)
        s.latencies_ms.append(latency_ms)
        s.last_success = time.time()
        await self._breaker.record_success(spec.key)
        await self._limiter.reconcile(spec.key, reserved=reserved_tokens,
                                      actual=actual_tokens)

    async def record_failure(self, spec: ModelSpec, *, error: str) -> None:
        self._s(spec.key).last_failure = time.time()
        await self._breaker.record_failure(spec.key, error)

    async def record_rate_limit(self, spec: ModelSpec,
                                retry_after: Optional[float]) -> None:
        """A 429 from the provider.

        Explicitly NOT a breaker failure — see the note in circuit_breaker.py.
        The model is fine; we asked too often. Both the limiter and the breaker
        are told to stand off for the same period so neither routes here again
        before the window clears, and Retry-After is honoured when given
        because the provider knows better than our estimate.
        """
        s = self._s(spec.key)
        s.rate_limit_hits += 1
        s.last_failure = time.time()
        wait = retry_after if retry_after and retry_after > 0 else 15.0
        await self._limiter.penalise(spec.key, wait)
        await self._breaker.force_open(spec.key, wait)

    def disable(self, key: str) -> None:
        self._disabled.add(key)

    def enable(self, key: str) -> None:
        self._disabled.discard(key)

    # -- reporting ----------------------------------------------------------

    async def report(self, specs: list[ModelSpec]) -> list[dict]:
        breakers = self._breaker.snapshot()
        out = []
        for spec in specs:
            s = self._s(spec.key)
            b = breakers.get(spec.key, {})
            out.append({
                "model": spec.key,
                "provider": spec.provider,
                "state": self.state_of(spec).value,
                "priority": spec.priority,
                "capabilities": sorted(c.value for c in spec.capabilities),
                "breaker": b.get("state", BreakerState.CLOSED.value),
                "consecutive_failures": b.get("consecutive_failures", 0),
                "cooldown_remaining": round(b.get("cooldown_remaining", 0.0), 1),
                "avg_latency_ms": round(s.avg_latency_ms),
                "rate_limit_hits": s.rate_limit_hits,
                "window": await self._limiter.snapshot(spec.key),
            })
        return out
