"""The one interface the application talks to.

    response = await router.generate(messages=[...], task_type="reasoning")
    response.text

The caller never learns whether that went to Groq, Cerebras or Gemini — and
must not, because the whole point is that the answer to "which provider?"
changes per request based on live conditions.

THE REQUEST LIFECYCLE

    rank candidates for the task            scoring.py — capability filter,
        │                                   then priority/cost/latency score
        ▼
    interleave providers                    so the first fallback is usually
        │                                   different infrastructure
        ▼
    for each candidate:
        │
        ├─ availability?  ── no ──► next candidate      (breaker or limiter)
        │                                                no request is sent
        ▼
        attempt (up to max_retries)
        │
        ├─ 429        ─► record, penalise BOTH limiter and breaker,
        │                honour Retry-After, next candidate
        ├─ transient  ─► backoff with jitter, retry SAME model
        ├─ permanent  ─► do not retry, next candidate
        └─ success    ─► reconcile tokens, record cost, return
        │
        ▼
    every candidate exhausted ──► offline fallback, or NoCandidatesError

WHY FAILOVER RATHER THAN RETRY ON A PERMANENT ERROR
    A 400 or 401 will produce the same answer however many times it is sent.
    But it is frequently provider-specific — a retired model ID, a key that
    was never funded — and a different provider often succeeds. If the request
    itself is malformed, every candidate fails quickly and the caller gets a
    real error instead of a hang.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Any, AsyncIterator, Optional, Sequence

from backend.app.llm.router import observability as obs
from backend.app.llm.router import retry as retry_policy
from backend.app.llm.router import scoring
from backend.app.llm.router.circuit_breaker import CircuitBreaker
from backend.app.llm.router.health import HealthTracker
from backend.app.llm.router.model_registry import ModelRegistry, get_registry
from backend.app.llm.router.provider_registry import ProviderRegistry
from backend.app.llm.router.rate_limiter import build_limiter, estimate_tokens
from backend.app.llm.router.schemas import (
    Attempt, Capability, LLMError, LLMRequest, LLMResponse, Message,
    NoCandidatesError, PermanentError, RateLimitError, TaskType, ToolSpec,
    Usage,
)

log = logging.getLogger("vo.llm.router")


class LLMRouter:
    """Thread-safe, async, and cheap to construct. One per process is plenty."""

    def __init__(self, *, registry: Optional[ModelRegistry] = None,
                 redis_url: Optional[str] = None,
                 allow_offline_fallback: bool = True):
        self.registry = registry or get_registry()
        self.providers = ProviderRegistry(self.registry)

        d = self.registry.defaults
        self.limiter = build_limiter(
            redis_url if redis_url is not None else os.getenv("LLM_ROUTER_REDIS_URL"))
        self.breaker = CircuitBreaker(failure_threshold=d.failure_threshold,
                                      cooldown_seconds=d.cooldown_seconds)
        self.health = HealthTracker(self.limiter, self.breaker)
        self.cost = obs.CostTracker()
        self.allow_offline_fallback = allow_offline_fallback

    # -- public -------------------------------------------------------------

    async def generate(self, messages: Sequence[dict[str, Any] | Message],
                       *, task_type: TaskType | str = TaskType.GENERAL,
                       preferred_model: Optional[str] = None,
                       tools: Optional[Sequence[ToolSpec | dict]] = None,
                       stream: bool = False,
                       max_tokens: int = 1024,
                       temperature: float = 0.0,
                       require: Sequence[Capability | str] = (),
                       min_context: int = 0,
                       request_id: Optional[str] = None,
                       **metadata: Any) -> LLMResponse:
        """Route one request. The only method the application needs."""
        req = self._build_request(
            messages, task_type, preferred_model, tools, stream, max_tokens,
            temperature, require, min_context, request_id, metadata)
        if stream:
            # Streaming has its own method because it returns an iterator, not
            # a response. Silently ignoring the flag would be worse.
            raise ValueError("use generate_stream() for stream=True")
        return await self._dispatch(req)

    async def generate_stream(self, messages, **kw) -> AsyncIterator[str]:
        """Same routing, incremental output.

        Selection is identical, but there is no mid-stream failover: once
        bytes have reached the caller, switching model would splice two
        different answers together. A failure before the first token still
        falls through to the next candidate.
        """
        kw.pop("stream", None)
        req = self._build_request(
            messages, kw.pop("task_type", TaskType.GENERAL),
            kw.pop("preferred_model", None), kw.pop("tools", None), True,
            kw.pop("max_tokens", 1024), kw.pop("temperature", 0.0),
            kw.pop("require", ()), kw.pop("min_context", 0),
            kw.pop("request_id", None), kw)

        chain = self._chain(req)
        est = self._estimate(req)
        last: Optional[Exception] = None

        for cand in chain:
            spec = cand.spec
            avail = await self.health.availability(spec, est)
            if not avail.available:
                continue
            provider = self.providers.get(spec)
            if provider is None:
                continue
            started = time.monotonic()
            try:
                produced = False
                async for chunk in provider.stream(
                        spec=spec, messages=req.messages, tools=req.tools,
                        max_tokens=req.max_tokens, temperature=req.temperature,
                        timeout=self.registry.defaults.request_timeout_seconds):
                    produced = True
                    yield chunk
                await self.health.record_success(
                    spec, latency_ms=int((time.monotonic() - started) * 1000),
                    reserved_tokens=est, actual_tokens=est)
                if produced:
                    return
            except Exception as exc:
                err = retry_policy.classify(exc, provider=spec.provider,
                                            model=spec.name)
                last = err
                if isinstance(err, RateLimitError):
                    obs.note_rate_limit(spec.provider, spec.name, "provider")
                    await self.health.record_rate_limit(spec, err.retry_after)
                else:
                    await self.health.record_failure(spec, error=str(err))
                continue

        raise last or NoCandidatesError("no model could serve the stream")

    async def health_report(self) -> dict[str, Any]:
        return {
            "providers": self.providers.names(),
            "models": await self.health.report(self.registry.all()),
            "skipped_providers": self.registry.skipped,
            "cost": self.cost.snapshot(),
        }

    # -- internals ----------------------------------------------------------

    def _build_request(self, messages, task_type, preferred_model, tools,
                       stream, max_tokens, temperature, require, min_context,
                       request_id, metadata) -> LLMRequest:
        msgs = [m if isinstance(m, Message) else Message(**m) for m in messages]
        tool_specs = None
        if tools:
            tool_specs = [t if isinstance(t, ToolSpec) else ToolSpec(**t)
                          for t in tools]
        caps = []
        for c in require:
            caps.append(c if isinstance(c, Capability) else Capability(str(c)))
        return LLMRequest(
            messages=msgs,
            task_type=(task_type if isinstance(task_type, TaskType)
                       else TaskType(str(task_type))),
            preferred_model=preferred_model, tools=tool_specs, stream=stream,
            max_tokens=max_tokens, temperature=temperature, require=caps,
            min_context=min_context,
            request_id=request_id or uuid.uuid4().hex[:12],
            metadata=metadata or {})

    def _chain(self, req: LLMRequest) -> list[scoring.Candidate]:
        ranked = scoring.rank(self.registry, req,
                              latency_lookup=self.health.avg_latency_ms)
        if req.preferred_model:
            # An explicit choice must stay first; interleaving would demote it.
            return ranked
        return scoring.diversify(ranked)

    def _estimate(self, req: LLMRequest) -> int:
        prompt = sum(estimate_tokens(m.content or "") for m in req.messages)
        if req.tools:
            prompt += sum(estimate_tokens(str(t.model_dump())) for t in req.tools)
        return prompt + req.max_tokens

    async def _dispatch(self, req: LLMRequest) -> LLMResponse:
        record = obs.RequestRecord(request_id=req.request_id or "",
                                   task_type=req.task_type.value)
        chain = self._chain(req)
        est = self._estimate(req)
        attempts: list[Attempt] = []

        if not chain:
            # No model has the capabilities — a configuration problem, not a
            # transient one. Say which capability so it is actionable.
            need = sorted(c.value for c in
                          scoring.required_capabilities(self.registry, req))
            record.finish_error("no_capable_model")
            raise NoCandidatesError(
                f"no configured model satisfies {need or ['(none)']} "
                f"with context >= {req.min_context}. "
                f"Providers loaded: {self.providers.names() or 'none'}; "
                f"skipped: {self.registry.skipped or 'none'}.")

        last_error: Optional[LLMError] = None
        previous_provider = ""

        for cand in chain:
            spec = cand.spec

            avail = await self.health.availability(spec, est)
            if not avail.available:
                log.debug("skip %s: %s", spec.key, avail.reason)
                attempts.append(Attempt(provider=spec.provider, model=spec.name,
                                        ok=False, error_type=avail.state.value,
                                        error=avail.reason))
                if avail.state.value == "rate_limited":
                    obs.note_rate_limit(spec.provider, spec.name, "local")
                continue

            provider = self.providers.get(spec)
            if provider is None:
                continue

            if previous_provider:
                record.fallback_count += 1
                obs.note_fallback(previous_provider, spec.provider)
            previous_provider = spec.provider

            result = await self._attempt_model(req, cand, provider, est,
                                               record, attempts)
            if result is not None:
                result.attempts = attempts
                result.fallback_count = record.fallback_count
                result.retry_count = record.retry_count
                return result
            last_error = self._last_error or last_error

        # Everything declined. Offline is a deliberate last resort so the
        # application degrades to templates rather than raising into a user's
        # face — the vendor pipeline in particular must still produce a case.
        if self.allow_offline_fallback:
            log.warning("all %d candidate(s) unavailable; using offline provider",
                        len(chain))
            spec = self._offline_spec(req)
            r = await self.providers.offline.generate(
                spec=spec, messages=req.messages, tools=req.tools,
                max_tokens=req.max_tokens, temperature=req.temperature)
            r.task_type = req.task_type
            r.request_id = req.request_id or ""
            r.attempts = attempts
            r.fallback_count = record.fallback_count
            record.finish_ok(provider="offline", model="offline",
                             input_tokens=r.usage.input_tokens,
                             output_tokens=r.usage.output_tokens, cost=0.0)
            return r

        record.finish_error(type(last_error).__name__ if last_error else "no_candidates")
        raise last_error or NoCandidatesError(
            f"all {len(chain)} candidate model(s) were unavailable")

    _last_error: Optional[LLMError] = None

    async def _attempt_model(self, req: LLMRequest, cand: scoring.Candidate,
                             provider, est: int, record: obs.RequestRecord,
                             attempts: list[Attempt]) -> Optional[LLMResponse]:
        """Try one model, with retries. None means "move to the next model"."""
        spec = cand.spec
        d = self.registry.defaults
        self._last_error = None

        for attempt in range(d.max_retries + 1):
            started = time.monotonic()
            try:
                r = await provider.generate(
                    spec=spec, messages=req.messages, tools=req.tools,
                    max_tokens=req.max_tokens, temperature=req.temperature,
                    timeout=d.request_timeout_seconds)
            except Exception as exc:
                err = retry_policy.classify(exc, provider=spec.provider,
                                            model=spec.name)
                self._last_error = err
                latency = int((time.monotonic() - started) * 1000)
                record.note_attempt(provider=spec.provider, model=spec.name,
                                    ok=False, latency_ms=latency,
                                    error_type=type(err).__name__, error=str(err))
                attempts.append(Attempt(
                    provider=spec.provider, model=spec.name, ok=False,
                    error_type=type(err).__name__, error=str(err)[:200],
                    latency_ms=latency, status_code=err.status_code))

                if isinstance(err, RateLimitError):
                    # Never retry the same model on a 429 — that is the
                    # behaviour the brief calls out, and it is how an
                    # integration gets throttled harder. Stand the model down
                    # for as long as it asked, and move on.
                    obs.note_rate_limit(spec.provider, spec.name, "provider")
                    await self.health.record_rate_limit(spec, err.retry_after)
                    return None

                if isinstance(err, PermanentError):
                    await self.health.record_failure(spec, error=str(err))
                    return None

                await self.health.record_failure(spec, error=str(err))
                if attempt < d.max_retries:
                    record.retry_count += 1
                    await asyncio.sleep(retry_policy.backoff_delay(
                        attempt, cap=d.max_backoff_seconds))
                    continue
                return None

            # Success.
            latency = int((time.monotonic() - started) * 1000)
            usage = r.usage or Usage()
            actual = usage.total or est
            await self.health.record_success(
                spec, latency_ms=latency, reserved_tokens=est, actual_tokens=actual)
            record.note_attempt(provider=spec.provider, model=spec.name,
                                ok=True, latency_ms=latency)
            attempts.append(Attempt(provider=spec.provider, model=spec.name,
                                    ok=True, latency_ms=latency))

            cost = spec.cost_for(usage.input_tokens, usage.output_tokens)
            self.cost.record(model_key=spec.key, task_type=req.task_type.value,
                             cost=cost)
            record.finish_ok(provider=spec.provider, model=spec.name,
                             input_tokens=usage.input_tokens,
                             output_tokens=usage.output_tokens, cost=cost)

            r.task_type = req.task_type
            r.latency_ms = latency
            r.estimated_cost_usd = cost
            r.request_id = req.request_id or ""
            return r

        return None

    def _offline_spec(self, req: LLMRequest):
        from backend.app.llm.router.model_registry import ModelSpec
        return ModelSpec(provider="offline", name="offline",
                         context_window=1_000_000,
                         max_output_tokens=max(req.max_tokens, 4096))


_ROUTER: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    global _ROUTER
    if _ROUTER is None:
        _ROUTER = LLMRouter()
    return _ROUTER


def reset_router() -> None:
    global _ROUTER
    _ROUTER = None
