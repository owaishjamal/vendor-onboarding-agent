"""Structured logs, Prometheus metrics, cost accounting.

prometheus_client is optional. If it is not installed the metric objects
become no-ops with the same method signatures, so instrumentation call sites
never need a conditional and the router runs identically either way. An
observability dependency that can break the thing it observes is a bad trade.

NOTHING HERE EVER SEES AN API KEY. Keys are read from the environment at call
time inside the adapters, never passed through a request object, never logged,
and never included in a repr — so there is no path by which one reaches a log
line or a metric label.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

log = logging.getLogger("vo.llm.router")

# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

try:                                              # pragma: no cover
    from prometheus_client import Counter, Gauge, Histogram
    _PROM = True
except ImportError:                               # pragma: no cover
    _PROM = False

    class _Noop:
        def labels(self, *_a, **_kw): return self
        def inc(self, *_a, **_kw): return None
        def observe(self, *_a, **_kw): return None
        def set(self, *_a, **_kw): return None

    def Counter(*_a, **_kw): return _Noop()       # type: ignore
    def Gauge(*_a, **_kw): return _Noop()         # type: ignore
    def Histogram(*_a, **_kw): return _Noop()     # type: ignore


def _counter(name, doc, labels):
    try:
        return Counter(name, doc, labels)
    except ValueError:
        # Already registered — happens when the module is re-imported under a
        # test runner. The existing collector is fine.
        return _Noop() if not _PROM else Counter(name + "_dup", doc, labels)


llm_requests_total = _counter(
    "llm_requests_total", "LLM requests attempted",
    ["provider", "model", "task_type", "status"])
llm_requests_failed_total = _counter(
    "llm_requests_failed_total", "LLM requests that failed",
    ["provider", "model", "error_type"])
llm_rate_limits_total = _counter(
    "llm_rate_limits_total", "Rate limits encountered",
    ["provider", "model", "source"])
llm_tokens_total = _counter(
    "llm_tokens_total", "Tokens consumed", ["provider", "model", "direction"])
llm_fallback_total = _counter(
    "llm_fallback_total", "Times a request fell back to another model",
    ["from_provider", "to_provider"])
llm_cost_usd_total = _counter(
    "llm_cost_usd_total", "Estimated spend in USD", ["provider", "model"])

try:
    llm_latency_seconds = Histogram(
        "llm_latency_seconds", "End-to-end latency per attempt",
        ["provider", "model"],
        buckets=(0.1, 0.25, 0.5, 1, 2, 4, 8, 16, 32, 60))
    llm_provider_health = Gauge(
        "llm_provider_health", "1 healthy, 0 unavailable", ["provider", "model"])
except ValueError:                                # pragma: no cover
    llm_latency_seconds = _Noop()                 # type: ignore
    llm_provider_health = _Noop()                 # type: ignore


# ---------------------------------------------------------------------------
# Per-request record
# ---------------------------------------------------------------------------

@dataclass
class RequestRecord:
    """One application-level request, however many attempts it took.

    Emitted as a single structured log line at the end rather than a line per
    attempt: "this request took 3 attempts across 2 providers and cost
    $0.0004" is the useful unit, and it is what a reviewer or an on-call
    engineer actually needs to read.
    """

    request_id: str
    task_type: str
    started: float = field(default_factory=time.monotonic)
    provider: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    status: str = "pending"
    retry_count: int = 0
    fallback_count: int = 0
    error_type: Optional[str] = None
    estimated_cost_usd: float = 0.0
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def latency_ms(self) -> int:
        return int((time.monotonic() - self.started) * 1000)

    def note_attempt(self, *, provider: str, model: str, ok: bool,
                     latency_ms: int, error_type: Optional[str] = None,
                     error: Optional[str] = None) -> None:
        self.attempts.append({
            "provider": provider, "model": model, "ok": ok,
            "latency_ms": latency_ms, "error_type": error_type,
            "error": (error or "")[:200] or None})
        llm_latency_seconds.labels(provider, model).observe(latency_ms / 1000.0)
        llm_requests_total.labels(provider, model, self.task_type,
                                  "ok" if ok else "error").inc()
        if not ok:
            llm_requests_failed_total.labels(provider, model,
                                             error_type or "unknown").inc()
        llm_provider_health.labels(provider, model).set(1 if ok else 0)

    def finish_ok(self, *, provider: str, model: str, input_tokens: int,
                  output_tokens: int, cost: float) -> None:
        self.provider, self.model = provider, model
        self.input_tokens, self.output_tokens = input_tokens, output_tokens
        self.estimated_cost_usd = cost
        self.status = "ok"
        llm_tokens_total.labels(provider, model, "input").inc(input_tokens)
        llm_tokens_total.labels(provider, model, "output").inc(output_tokens)
        llm_cost_usd_total.labels(provider, model).inc(cost)
        self._emit(logging.INFO)

    def finish_error(self, error_type: str) -> None:
        self.status = "error"
        self.error_type = error_type
        self._emit(logging.WARNING)

    def _emit(self, level: int) -> None:
        log.log(level, "llm request", extra={"llm": self.as_dict()})
        # Also as a readable line: most deployments here read plain logs
        # rather than running a JSON pipeline.
        log.log(level,
                "  %s task=%s -> %s:%s %dms in=%d out=%d retries=%d "
                "fallbacks=%d cost=$%.6f%s",
                self.request_id, self.task_type, self.provider or "-",
                self.model or "-", self.latency_ms, self.input_tokens,
                self.output_tokens, self.retry_count, self.fallback_count,
                self.estimated_cost_usd,
                f" error={self.error_type}" if self.error_type else "")

    def as_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id, "provider": self.provider,
            "model": self.model, "task_type": self.task_type,
            "input_tokens": self.input_tokens, "output_tokens": self.output_tokens,
            "latency_ms": self.latency_ms, "status": self.status,
            "retry_count": self.retry_count, "fallback_count": self.fallback_count,
            "error_type": self.error_type,
            "estimated_cost_usd": round(self.estimated_cost_usd, 8),
            "attempts": self.attempts,
        }


def note_rate_limit(provider: str, model: str, source: str) -> None:
    """source: 'provider' for a real 429, 'local' for our own limiter."""
    llm_rate_limits_total.labels(provider, model, source).inc()


def note_fallback(from_provider: str, to_provider: str) -> None:
    llm_fallback_total.labels(from_provider or "none", to_provider).inc()


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

class CostTracker:
    """Running spend, in process. Prices live in models.yaml, never in code.

    Estimated, not billed: token counts come from the provider's own usage
    block where given and from our estimator where not, and prices drift. Good
    enough to answer "which task type is expensive?", not an invoice.
    """

    def __init__(self) -> None:
        self.total_usd = 0.0
        self.by_model: dict[str, float] = {}
        self.by_task: dict[str, float] = {}
        self.requests = 0

    def record(self, *, model_key: str, task_type: str, cost: float) -> None:
        self.total_usd += cost
        self.by_model[model_key] = self.by_model.get(model_key, 0.0) + cost
        self.by_task[task_type] = self.by_task.get(task_type, 0.0) + cost
        self.requests += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "total_usd": round(self.total_usd, 6),
            "requests": self.requests,
            "by_model": {k: round(v, 6) for k, v in
                         sorted(self.by_model.items(), key=lambda kv: -kv[1])},
            "by_task": {k: round(v, 6) for k, v in
                        sorted(self.by_task.items(), key=lambda kv: -kv[1])},
        }
