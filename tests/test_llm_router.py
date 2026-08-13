"""Router tests: rate limits, fallback, breaker, tools, streaming, agents.

EVERY PROVIDER HERE IS FAKE. Nothing in this file touches a network. Rate
limits are simulated by raising RateLimitError rather than by actually
exhausting a free tier — hitting a real limit to test limit handling would be
slow, non-deterministic, and would burn the quota the demo needs.

The fakes implement the same BaseLLMProvider surface the real adapters do, so
what is exercised is the router's real logic: real scoring, real windows, real
breaker, real backoff.
"""

from __future__ import annotations

import asyncio
import os

import pytest

# Keys must exist before the registry loads, or every provider is skipped for
# want of a credential and the tests silently assert against an empty router.
os.environ.setdefault("GROQ_API_KEY", "test-groq")
os.environ.setdefault("CEREBRAS_API_KEY", "test-cerebras")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini")

from backend.app.llm.router import (                                # noqa: E402
    Capability, LLMRouter, Message, TaskType, Tool, ToolExecutor,
)
from backend.app.llm.router.model_registry import (                 # noqa: E402
    ModelRegistry, reset_registry,
)
from backend.app.llm.router.rate_limiter import InMemoryRateLimiter  # noqa: E402
from backend.app.llm.router.schemas import (                        # noqa: E402
    LLMResponse, NoCandidatesError, PermanentError, RateLimitError, ToolCall,
    TransientError, Usage,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeProvider:
    """Scripted provider. `script` is a list of outcomes, consumed in order.

    An outcome is an Exception (raised) or a string (returned as text). The
    last entry repeats once exhausted, so a provider that should always fail
    needs one entry rather than a guess at how many attempts will happen.
    """

    def __init__(self, name: str, script=None, *, tool_call=None, delay=0.0):
        self.name = name
        self.script = list(script or ["ok"])
        self.tool_call = tool_call
        self.delay = delay
        self.calls: list[dict] = []

    def _next(self):
        return self.script.pop(0) if len(self.script) > 1 else self.script[0]

    async def generate(self, *, spec, messages, tools=None, max_tokens=1024,
                       temperature=0.0, timeout=60.0):
        self.calls.append({"model": spec.name, "messages": len(messages),
                           "tools": len(tools or [])})
        if self.delay:
            await asyncio.sleep(self.delay)
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome

        calls = []
        if self.tool_call and not any(m.role == "tool" for m in messages):
            # Ask for the tool once; after a tool result comes back, answer.
            calls = [ToolCall(id="c1", name=self.tool_call[0],
                              arguments=self.tool_call[1])]
        return LLMResponse(
            text="" if calls else str(outcome), tool_calls=calls,
            provider=spec.provider, model=spec.name,
            usage=Usage(input_tokens=10, output_tokens=5))

    async def stream(self, *, spec, messages, tools=None, max_tokens=1024,
                     temperature=0.0, timeout=60.0):
        outcome = self._next()
        if isinstance(outcome, Exception):
            raise outcome
        for word in str(outcome).split():
            yield word + " "

    async def health_check(self, spec):
        return True


def build_router(*, groq=None, cerebras=None, gemini=None,
                 offline_fallback=False) -> LLMRouter:
    reset_registry()
    r = LLMRouter(registry=ModelRegistry(), redis_url=None,
                  allow_offline_fallback=offline_fallback)
    r.providers._providers["groq"] = groq or FakeProvider("groq")
    r.providers._providers["cerebras"] = cerebras or FakeProvider("cerebras")
    r.providers._providers["gemini"] = gemini or FakeProvider("gemini")
    return r


MSG = [{"role": "user", "content": "explain retrieval augmented generation"}]


# ---------------------------------------------------------------------------
# 1. Normal request
# ---------------------------------------------------------------------------

def test_a_normal_request_is_answered_and_names_no_provider_to_the_caller():
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.text == "ok"
    # The caller CAN see where it went, for debugging — but did not choose it.
    assert resp.provider in ("groq", "cerebras", "gemini")
    assert resp.usage.total == 15


def test_usage_and_cost_are_recorded():
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.estimated_cost_usd > 0
    assert r.cost.snapshot()["requests"] == 1


def test_the_highest_priority_capable_model_is_chosen_first():
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.routed_to == "groq:openai/gpt-oss-120b"


def test_an_explicit_preference_is_honoured():
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="reasoning",
                                  preferred_model="gemini:gemini-2.5-flash"))
    assert resp.routed_to == "gemini:gemini-2.5-flash"


def test_a_preference_for_an_incapable_model_is_ignored_not_obeyed():
    """Obeying it would produce a confusing downstream failure instead of a
    working answer from a model that can actually do the job."""
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="vision",
                                  preferred_model="groq:llama-3.1-8b-instant"))
    assert resp.provider == "gemini"        # the only vision provider


# ---------------------------------------------------------------------------
# 2. Provider failure and fallback
# ---------------------------------------------------------------------------

def test_a_failing_provider_falls_back_to_another():
    groq = FakeProvider("groq", [TransientError("503 upstream")])
    r = build_router(groq=groq)
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.provider == "cerebras"
    assert resp.fallback_count >= 1


def test_the_fallback_chain_is_recorded_on_the_response():
    groq = FakeProvider("groq", [TransientError("503")])
    r = build_router(groq=groq)
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    failed = [a for a in resp.attempts if not a.ok]
    assert failed and failed[0].provider == "groq"
    assert any(a.ok for a in resp.attempts)


def test_the_first_fallback_prefers_a_different_provider():
    """Provider-wide outages and shared per-org quotas are the common case, so
    falling back to a second model on the same key usually hits the same wall."""
    from backend.app.llm.router import scoring
    from backend.app.llm.router.schemas import LLMRequest
    r = build_router()
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     task_type=TaskType.REASONING)
    chain = scoring.diversify(scoring.rank(r.registry, req))
    assert chain[0].spec.provider != chain[1].spec.provider


def test_all_providers_unavailable_raises_rather_than_inventing_an_answer():
    err = TransientError("everything is down")
    r = build_router(groq=FakeProvider("groq", [err]),
                     cerebras=FakeProvider("cerebras", [err]),
                     gemini=FakeProvider("gemini", [err]))
    with pytest.raises(Exception) as exc:
        asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert "down" in str(exc.value).lower() or "unavailable" in str(exc.value).lower()


def test_offline_fallback_keeps_the_application_running():
    """The vendor pipeline must still produce a case when every provider is
    exhausted — the alternative is a submission that cannot be verified at all."""
    err = TransientError("down")
    r = build_router(groq=FakeProvider("groq", [err]),
                     cerebras=FakeProvider("cerebras", [err]),
                     gemini=FakeProvider("gemini", [err]),
                     offline_fallback=True)
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.provider == "offline"
    assert "offline" in resp.text.lower()


# ---------------------------------------------------------------------------
# 3. 429 handling
# ---------------------------------------------------------------------------

def test_a_429_moves_to_the_next_provider():
    groq = FakeProvider("groq", [RateLimitError("429 quota exceeded")])
    r = build_router(groq=groq)
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.provider == "cerebras"


def test_a_429_is_never_retried_on_the_same_model():
    """Retrying immediately after a 429 is the specific mistake to avoid: it
    cannot succeed and some providers count the rejection against the quota."""
    groq = FakeProvider("groq", [RateLimitError("429")])
    r = build_router(groq=groq)
    asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert len(groq.calls) == 1


def test_retry_after_is_honoured_for_the_stated_period():
    groq = FakeProvider("groq", [RateLimitError("429", retry_after=45)])
    r = build_router(groq=groq)
    asyncio.run(r.generate(MSG, task_type="reasoning"))
    snap = r.breaker.snapshot()["groq:openai/gpt-oss-120b"]
    assert 40 <= snap["cooldown_remaining"] <= 46


def test_a_rate_limited_model_is_skipped_without_sending_a_request():
    groq = FakeProvider("groq", [RateLimitError("429", retry_after=30)])
    r = build_router(groq=groq)
    asyncio.run(r.generate(MSG, task_type="reasoning"))
    before = len(groq.calls)
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert len(groq.calls) == before      # not asked again
    assert resp.provider == "cerebras"


def test_a_rate_limit_does_not_count_as_a_breaker_failure():
    """A 429 means healthy-and-busy. Counting it as breakage would open the
    circuit on a working model and keep it shut long after the window cleared."""
    groq = FakeProvider("groq", [RateLimitError("429", retry_after=1)])
    r = build_router(groq=groq)
    asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert r.breaker.snapshot()["groq:openai/gpt-oss-120b"]["consecutive_failures"] == 0


# ---------------------------------------------------------------------------
# 4. Retry policy
# ---------------------------------------------------------------------------

def test_a_transient_error_is_retried_on_the_same_model():
    groq = FakeProvider("groq", [TransientError("500"), "recovered"])
    r = build_router(groq=groq)
    resp = asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert resp.text == "recovered"
    assert resp.provider == "groq"
    assert resp.retry_count >= 1


def test_a_permanent_error_is_not_retried():
    groq = FakeProvider("groq", [PermanentError("401 invalid api key")])
    r = build_router(groq=groq)
    asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert len(groq.calls) == 1


def test_a_permanent_error_still_fails_over():
    """A bad key or a retired model ID is provider-specific: another provider
    frequently works, and if the request itself is malformed everything fails
    quickly rather than hanging."""
    groq = FakeProvider("groq", [PermanentError("404 model not found")])
    r = build_router(groq=groq)
    assert asyncio.run(r.generate(MSG, task_type="reasoning")).provider == "cerebras"


@pytest.mark.parametrize("status,retryable", [
    (429, True), (500, True), (502, True), (503, True), (504, True), (408, True),
    (400, False), (401, False), (403, False), (404, False), (422, False),
])
def test_error_classification_matches_the_policy(status, retryable):
    from backend.app.llm.router.retry import classify
    err = classify(RuntimeError("x"), status_code=status)
    assert err.retryable is retryable


@pytest.mark.parametrize("body,expected", [
    ('{"error": {"retryDelay": "7s"}}', 7.0),
    ("Retry-After: 12", 12.0),
    ("please try again in 3.5s", 3.5),
    ("no hint here", None),
    ('{"retryDelay": "9999s"}', None),          # implausible, so ignored
])
def test_retry_after_is_parsed_from_the_shapes_providers_actually_use(body, expected):
    from backend.app.llm.router.retry import parse_retry_after
    assert parse_retry_after(body) == expected


def test_backoff_grows_and_is_jittered():
    from backend.app.llm.router.retry import backoff_delay
    assert all(0 <= backoff_delay(0, cap=8) <= 0.5 for _ in range(20))
    assert all(0 <= backoff_delay(5, cap=8) <= 8.0 for _ in range(20))
    # Jitter: identical inputs must not give identical delays, or a
    # synchronised herd retries in lockstep and recreates the burst.
    assert len({round(backoff_delay(3, cap=8), 6) for _ in range(20)}) > 1


# ---------------------------------------------------------------------------
# 5. Circuit breaker
# ---------------------------------------------------------------------------

def test_the_breaker_opens_after_repeated_failures():
    from backend.app.llm.router.circuit_breaker import CircuitBreaker
    from backend.app.llm.router.schemas import BreakerState

    async def go():
        b = CircuitBreaker(failure_threshold=3, cooldown_seconds=30)
        for _ in range(3):
            await b.record_failure("m", "boom")
        assert b.state_of("m") is BreakerState.OPEN
        assert await b.allows("m") is False
    asyncio.run(go())


def test_the_breaker_probes_once_after_the_cooldown_then_closes_on_success():
    from backend.app.llm.router.circuit_breaker import CircuitBreaker
    from backend.app.llm.router.schemas import BreakerState

    async def go():
        b = CircuitBreaker(failure_threshold=2, cooldown_seconds=0.05)
        await b.record_failure("m"); await b.record_failure("m")
        assert b.state_of("m") is BreakerState.OPEN
        await asyncio.sleep(0.06)
        assert await b.allows("m") is True            # the probe
        assert b.state_of("m") is BreakerState.HALF_OPEN
        assert await b.allows("m") is False           # only one at a time
        await b.record_success("m")
        assert b.state_of("m") is BreakerState.CLOSED
    asyncio.run(go())


def test_a_failed_probe_reopens_with_a_longer_cooldown():
    """A model that fails its probe is more broken than one that just started
    failing; re-probing at the same interval would hammer it."""
    from backend.app.llm.router.circuit_breaker import CircuitBreaker

    async def go():
        b = CircuitBreaker(failure_threshold=1, cooldown_seconds=0.05)
        await b.record_failure("m")
        first = b.snapshot()["m"]["cooldown_remaining"]
        await asyncio.sleep(0.06)
        await b.allows("m")
        await b.record_failure("m")
        assert b.snapshot()["m"]["cooldown_remaining"] > first
    asyncio.run(go())


def test_a_model_whose_circuit_is_open_is_skipped_by_the_router():
    groq = FakeProvider("groq", [TransientError("boom")])
    r = build_router(groq=groq)
    for _ in range(3):
        asyncio.run(r.generate(MSG, task_type="reasoning"))
    before = len(groq.calls)
    asyncio.run(r.generate(MSG, task_type="reasoning"))
    assert len(groq.calls) == before


# ---------------------------------------------------------------------------
# 6. Rate limiter: RPM and TPM exhaustion
# ---------------------------------------------------------------------------

def test_rpm_exhaustion_is_predicted_locally():
    async def go():
        lim = InMemoryRateLimiter()
        for _ in range(3):
            assert (await lim.check_and_reserve("m", rpm=3, tpm=0,
                                                est_tokens=1)).allowed
        d = await lim.check_and_reserve("m", rpm=3, tpm=0, est_tokens=1)
        assert not d.allowed and "RPM" in d.reason and d.retry_after > 0
    asyncio.run(go())


def test_tpm_exhaustion_is_predicted_locally():
    async def go():
        lim = InMemoryRateLimiter()
        assert (await lim.check_and_reserve("m", rpm=0, tpm=1000,
                                            est_tokens=900)).allowed
        d = await lim.check_and_reserve("m", rpm=0, tpm=1000, est_tokens=200)
        assert not d.allowed and "TPM" in d.reason
    asyncio.run(go())


def test_reconciling_the_estimate_frees_the_difference():
    """The estimate is pessimistic by design; keeping it would leave the window
    holding capacity the request never used."""
    async def go():
        lim = InMemoryRateLimiter()
        await lim.check_and_reserve("m", rpm=0, tpm=1000, est_tokens=900)
        await lim.reconcile("m", reserved=900, actual=100)
        assert (await lim.check_and_reserve("m", rpm=0, tpm=1000,
                                            est_tokens=800)).allowed
    asyncio.run(go())


def test_a_locally_rate_limited_model_is_skipped_before_any_request_is_sent():
    async def go():
        groq = FakeProvider("groq")
        r = build_router(groq=groq)
        await r.limiter.penalise("groq:openai/gpt-oss-120b", 30)
        resp = await r.generate(MSG, task_type="reasoning")
        assert resp.provider == "cerebras"
        assert groq.calls == []
    asyncio.run(go())


def test_the_window_slides_rather_than_resetting_in_bursts():
    async def go():
        lim = InMemoryRateLimiter()
        assert (await lim.check_and_reserve("m", rpm=1, tpm=0, est_tokens=1)).allowed
        d = await lim.check_and_reserve("m", rpm=1, tpm=0, est_tokens=1)
        # Capacity returns when the oldest request ages out, not on a tick.
        assert 0 < d.retry_after <= 60
    asyncio.run(go())


# ---------------------------------------------------------------------------
# 7. Capability matching
# ---------------------------------------------------------------------------

def test_vision_only_routes_to_a_model_that_has_it():
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="vision"))
    spec = r.registry.get(resp.routed_to)
    assert Capability.VISION in spec.capabilities


def test_an_impossible_capability_fails_loudly_and_says_what_was_missing():
    r = build_router()
    with pytest.raises(NoCandidatesError) as exc:
        asyncio.run(r.generate(MSG, task_type="vision",
                               require=["classification"], min_context=9_000_000))
    assert "no configured model satisfies" in str(exc.value)


def test_a_long_payload_avoids_short_context_models():
    """Cerebras's free tier caps context at 8192; a large case record must not
    be routed there and silently truncated."""
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="reasoning", min_context=100_000))
    assert r.registry.get(resp.routed_to).context_window >= 100_000


def test_passing_tools_implies_tool_calling_even_if_unstated():
    """Otherwise a model without tool support quietly ignores them and answers
    in prose — a silent failure that looks like a bad model."""
    from backend.app.llm.router import scoring
    from backend.app.llm.router.schemas import LLMRequest, ToolSpec
    r = build_router()
    req = LLMRequest(messages=[Message(role="user", content="x")],
                     task_type=TaskType.GENERAL,
                     tools=[ToolSpec(name="t", description="d")])
    assert Capability.TOOL_CALLING in scoring.required_capabilities(r.registry, req)
    assert all(Capability.TOOL_CALLING in s.capabilities
               for s in scoring.eligible(r.registry, req))


def test_classification_prefers_a_cheap_fast_model():
    r = build_router()
    resp = asyncio.run(r.generate(MSG, task_type="classification"))
    assert r.registry.get(resp.routed_to).cost_per_1m_output <= 0.5


def test_an_unknown_capability_in_yaml_is_dropped_not_fatal():
    """A typo in one tag should cost that tag, not the whole registry."""
    from backend.app.llm.router.model_registry import ModelSpec
    spec = ModelSpec(provider="p", name="m",
                     capabilities=["reasoning", "teleportation"])
    assert Capability.REASONING in spec.capabilities
    assert len(spec.capabilities) == 1


# ---------------------------------------------------------------------------
# 8. Tool calling
# ---------------------------------------------------------------------------

def _executor():
    ex = ToolExecutor()
    ex.register(Tool("search_web", "Search the web",
                     {"type": "object", "properties": {"q": {"type": "string"}}},
                     lambda q: f"results for {q}"))
    return ex


def test_a_tool_is_called_and_its_result_reaches_the_model():
    from backend.app.llm.router.tools import run_tool_loop
    groq = FakeProvider("groq", ["final answer"],
                        tool_call=("search_web", {"q": "zamp"}))
    r = build_router(groq=groq)
    resp = asyncio.run(run_tool_loop(
        r, [Message(role="user", content="search for zamp")], _executor()))
    assert resp.text == "final answer"
    assert "search_web" in (resp.finish_reason or "")


def test_a_hallucinated_tool_name_returns_a_correction_not_a_crash():
    async def go():
        res = await _executor().execute(ToolCall(id="1", name="no_such_tool"))
        assert not res.ok
        assert "search_web" in res.content        # tells the model what exists
    asyncio.run(go())


def test_bad_tool_arguments_return_the_schema_rather_than_raising():
    async def go():
        res = await _executor().execute(
            ToolCall(id="1", name="search_web", arguments={"wrong": 1}))
        assert not res.ok and "expected_schema" in res.content
    asyncio.run(go())


def test_a_raising_tool_is_reported_to_the_model_not_to_the_caller():
    async def go():
        ex = ToolExecutor()
        ex.register(Tool("boom", "always fails", {"type": "object", "properties": {}},
                         lambda: (_ for _ in ()).throw(ValueError("nope"))))
        res = await ex.execute(ToolCall(id="1", name="boom"))
        assert not res.ok and "ValueError" in res.content
    asyncio.run(go())


def test_the_tool_loop_cannot_run_forever():
    """A model that keeps calling the same tool would otherwise burn the quota."""
    from backend.app.llm.router.tools import run_tool_loop

    class Looper(FakeProvider):
        async def generate(self, *, spec, messages, tools=None, **kw):
            self.calls.append({})
            if tools:
                return LLMResponse(
                    tool_calls=[ToolCall(id="c", name="search_web",
                                         arguments={"q": "again"})],
                    provider=spec.provider, model=spec.name, usage=Usage())
            return LLMResponse(text="forced", provider=spec.provider,
                               model=spec.name, usage=Usage())

    # Every provider loops, so the forced final answer cannot come from a
    # fallback that simply happens to reply.
    r = build_router(groq=Looper("groq"), cerebras=Looper("cerebras"),
                     gemini=Looper("gemini"))
    resp = asyncio.run(run_tool_loop(
        r, [Message(role="user", content="go")], _executor(), max_iterations=3))
    assert resp.finish_reason == "tool_iteration_limit"
    # Tools are withheld on the last call, which is what forces an answer.
    assert resp.text == "forced"


def test_tools_are_executed_in_parallel():
    async def go():
        ex = ToolExecutor()
        ex.register(Tool("slow", "", {"type": "object", "properties": {}},
                         _sleep_tool))
        import time
        started = time.monotonic()
        res = await ex.execute_all([ToolCall(id=str(i), name="slow")
                                    for i in range(4)])
        assert len(res) == 4
        assert time.monotonic() - started < 0.3      # not 4 x 0.1
    asyncio.run(go())


async def _sleep_tool():
    await asyncio.sleep(0.1)
    return "done"


def test_a_tool_schema_is_translated_for_both_dialects():
    from backend.app.llm.router.schemas import ToolSpec
    t = ToolSpec(name="t", description="d",
                 parameters={"type": "object", "properties": {},
                             "additionalProperties": False})
    assert t.as_openai()["function"]["name"] == "t"
    # Gemini rejects additionalProperties with an unhelpful 400.
    assert "additionalProperties" not in t.as_gemini()["parameters"]


# ---------------------------------------------------------------------------
# 9. Streaming
# ---------------------------------------------------------------------------

def test_streaming_yields_incrementally():
    async def go():
        r = build_router(groq=FakeProvider("groq", ["one two three"]))
        chunks = [c async for c in r.generate_stream(MSG, task_type="reasoning")]
        assert len(chunks) >= 3
        assert "".join(chunks).strip() == "one two three"
    asyncio.run(go())


def test_a_stream_that_fails_before_any_token_falls_over_to_another_provider():
    async def go():
        r = build_router(groq=FakeProvider("groq", [TransientError("boom")]),
                         cerebras=FakeProvider("cerebras", ["from cerebras"]))
        out = "".join([c async for c in r.generate_stream(MSG, task_type="reasoning")])
        assert "cerebras" in out
    asyncio.run(go())


def test_stream_true_on_generate_is_an_error_not_a_silent_no_op():
    r = build_router()
    with pytest.raises(ValueError, match="generate_stream"):
        asyncio.run(r.generate(MSG, task_type="reasoning", stream=True))


# ---------------------------------------------------------------------------
# 10. Concurrency
# ---------------------------------------------------------------------------

def test_concurrent_requests_all_succeed():
    async def go():
        r = build_router()
        results = await asyncio.gather(
            *(r.generate(MSG, task_type="reasoning") for _ in range(12)))
        assert all(x.text == "ok" for x in results)
    asyncio.run(go())


def test_concurrent_requests_cannot_exceed_the_window_together():
    """Two coroutines checking the same limit must not both see room for one
    more — the check and the reservation have to be atomic."""
    async def go():
        lim = InMemoryRateLimiter()
        decisions = await asyncio.gather(
            *(lim.check_and_reserve("m", rpm=5, tpm=0, est_tokens=1)
              for _ in range(20)))
        assert sum(d.allowed for d in decisions) == 5
    asyncio.run(go())


def test_concurrent_load_spreads_across_providers_as_models_are_exhausted():
    async def go():
        r = build_router()
        # Groq's first model allows 30 RPM; 40 concurrent requests must not
        # all pile onto it.
        results = await asyncio.gather(
            *(r.generate(MSG, task_type="reasoning") for _ in range(40)))
        used = {x.provider for x in results}
        assert len(used) > 1, f"everything went to {used}"
    asyncio.run(go())


# ---------------------------------------------------------------------------
# 11. Agents
# ---------------------------------------------------------------------------

def test_the_supervisor_routes_and_the_synthesizer_answers():
    from backend.app.llm.router import AgentGraph
    r = build_router(groq=FakeProvider("groq", ["research"]),
                     gemini=FakeProvider("gemini", ["research"]),
                     cerebras=FakeProvider("cerebras", ["research"]))
    state = asyncio.run(AgentGraph(r).run("what is RAG?"))
    assert state["route"] == "research"
    assert state["answer"]
    assert any("supervisor" in t for t in state["trace"])


def test_an_unparseable_classification_defaults_to_general():
    """A classifier returning junk must not stop the request."""
    from backend.app.llm.router import AgentGraph
    r = build_router(groq=FakeProvider("groq", ["banana"]),
                     gemini=FakeProvider("gemini", ["banana"]),
                     cerebras=FakeProvider("cerebras", ["banana"]))
    state = asyncio.run(AgentGraph(r).run("hello"))
    assert state["route"] == "general"


def test_a_failing_specialist_does_not_lose_the_request():
    from backend.app.llm.router import AgentGraph
    err = TransientError("down")
    r = build_router(groq=FakeProvider("groq", [err]),
                     cerebras=FakeProvider("cerebras", [err]),
                     gemini=FakeProvider("gemini", [err]),
                     offline_fallback=True)
    state = asyncio.run(AgentGraph(r).run("anything"))
    assert state.get("answer")


def test_agents_never_learn_which_provider_served_them():
    """The agent code contains no provider name; only the trace records one,
    and only for debugging."""
    import inspect
    from backend.app.llm.router import agents
    src = inspect.getsource(agents)
    for provider in ("groq", "cerebras", "gemini", "llama", "qwen", "gpt-oss"):
        assert provider not in src.lower(), f"agents.py mentions {provider}"


# ---------------------------------------------------------------------------
# 12. Configuration and safety
# ---------------------------------------------------------------------------

def test_a_provider_without_its_key_is_skipped_with_a_stated_reason():
    reset_registry()
    saved = os.environ.pop("GROQ_API_KEY")
    try:
        reg = ModelRegistry()
        assert "groq" in reg.skipped and "GROQ_API_KEY" in reg.skipped["groq"]
        assert not any(m.provider == "groq" for m in reg.all())
    finally:
        os.environ["GROQ_API_KEY"] = saved
        reset_registry()


def test_no_api_key_is_ever_stored_on_a_provider_instance():
    """Keys are read from the environment at call time so a rotated key needs
    no restart — and so no key can end up in a repr or a log line."""
    r = build_router()
    from backend.app.llm.router.providers.gemini import GeminiProvider
    p = GeminiProvider(base_url="https://x", api_key_env="GEMINI_API_KEY")
    assert "test-gemini" not in repr(p)
    assert not any("test-gemini" in str(v) for v in vars(p).values())


def test_the_registry_is_editable_without_touching_code():
    """The claim that adding a model is a YAML edit, verified rather than
    asserted in a comment."""
    import tempfile, pathlib, yaml
    reg = ModelRegistry()
    raw = yaml.safe_load(pathlib.Path(reg.path).read_text())
    raw["providers"]["groq"]["models"]["a-brand-new-model"] = {
        "priority": 1, "context_window": 128000, "max_output_tokens": 4096,
        "rpm": 10, "tpm": 5000, "capabilities": ["reasoning"],
        "cost_per_1m_input": 0.1, "cost_per_1m_output": 0.1}
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        yaml.safe_dump(raw, f)
        path = f.name
    os.environ["LLM_ROUTER_MODELS"] = path
    try:
        reset_registry()
        assert ModelRegistry().get("groq:a-brand-new-model") is not None
    finally:
        os.environ.pop("LLM_ROUTER_MODELS", None)
        reset_registry()


def test_costs_come_from_configuration_not_from_business_logic():
    from backend.app.llm.router.model_registry import ModelSpec
    spec = ModelSpec(provider="p", name="m", cost_per_1m_input=1.0,
                     cost_per_1m_output=2.0)
    assert spec.cost_for(1_000_000, 1_000_000) == 3.0


def test_the_health_report_exposes_state_without_exposing_secrets():
    async def go():
        r = build_router()
        await r.generate(MSG, task_type="reasoning")
        rep = await r.health_report()
        assert rep["models"] and "cost" in rep
        blob = str(rep)
        for key in ("test-groq", "test-cerebras", "test-gemini"):
            assert key not in blob
    asyncio.run(go())


# ---------------------------------------------------------------------------
# 13. Deployment safety
#
# Two real incidents, both found by tracing where a key actually travels
# rather than by reading the code that consumes it.
# ---------------------------------------------------------------------------

import pathlib                                                     # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_env_files_cannot_enter_the_docker_image():
    """The Dockerfile does `COPY . .`.

    Without an ignore rule that copies a developer's backend/.env — live
    provider keys — into every layer of the image, where it travels to anyone
    who pulls it. Keys reach a container through the platform's environment
    settings, never through the build context.
    """
    ignore = (REPO / ".dockerignore").read_text()
    assert "COPY . ." in (REPO / "Dockerfile").read_text(), \
        "Dockerfile no longer bulk-copies; re-check whether this rule is still needed"
    patterns = {l.strip() for l in ignore.splitlines() if l.strip()}
    assert ".env" in patterns, ".dockerignore must exclude .env"
    assert "**/.env" in patterns, ".dockerignore must exclude nested .env files"


def test_render_declares_every_provider_key_and_stores_none_of_them():
    """render.yaml is committed. `sync: false` makes Render prompt for the
    value and hold it encrypted; a literal here would be a key in git."""
    text = (REPO / "render.yaml").read_text()
    for var in ("GROQ_API_KEY", "CEREBRAS_API_KEY", "GEMINI_API_KEY"):
        assert var in text, f"{var} is not declared for deployment"
        after = text.split(var, 1)[1][:80]
        assert "sync: false" in after, f"{var} must be sync: false, not a value"


def test_the_committed_env_example_holds_no_values():
    """A live Gemini key was committed here once and reached the history."""
    for line in (REPO / ".env.example").read_text().splitlines():
        line = line.strip()
        if line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if "KEY" in key.upper() or "SECRET" in key.upper():
            assert value.strip().strip('"').strip("'") == "", \
                f"{key} has a value in a committed file"


def test_provider_key_variable_names_match_what_the_registry_looks_up():
    """The registry skips a provider whose env var is absent, by design and
    silently. A key saved under the wrong name (`grok=` rather than
    `GROQ_API_KEY=`) therefore disables that provider with no error at all —
    which is exactly how this deployment ran on one provider, with no
    failover, while appearing configured."""
    import yaml
    registry = yaml.safe_load(
        (REPO / "backend/app/llm/router/models.yaml").read_text())
    declared = {p["api_key_env"] for p in registry["providers"].values()}
    documented = {l.split("=")[0].strip()
                  for l in (REPO / ".env.example").read_text().splitlines()
                  if "_API_KEY=" in l and not l.strip().startswith("#")}
    missing = declared - documented
    assert not missing, (
        f"{missing} are read by the router but never mentioned in "
        f".env.example, so nobody knows to set them")
