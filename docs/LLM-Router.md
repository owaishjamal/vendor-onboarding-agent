# LLM Router

**One internal interface over Groq, Cerebras and Gemini. The application asks for a *task*; the router picks the provider, handles rate limits, retries, and fails over — and the caller never learns which model answered.**

```python
from backend.app.llm.router import LLMRouter

router = LLMRouter()
response = await router.generate(
    messages=[{"role": "user", "content": "Explain RAG"}],
    task_type="reasoning",
)
print(response.text)
```

---

## Contents

1. [Why](#1-why)
2. [Architecture](#2-architecture)
3. [Installation and configuration](#3-installation-and-configuration)
4. [The model registry](#4-the-model-registry)
5. [Model selection](#5-model-selection)
6. [How rate limiting works](#6-how-rate-limiting-works)
7. [How fallback works](#7-how-fallback-works)
8. [Health and the circuit breaker](#8-health-and-the-circuit-breaker)
9. [Retry policy](#9-retry-policy)
10. [Tool calling](#10-tool-calling)
11. [Agentic workflows](#11-agentic-workflows)
12. [Observability and cost](#12-observability-and-cost)
13. [Adding a provider](#13-adding-a-provider)
14. [Adding a model](#14-adding-a-model)
15. [How the vendor app uses it](#15-how-the-vendor-app-uses-it)
16. [Testing](#16-testing)
17. [Design decisions](#17-design-decisions)

---

## 1. Why

The app previously called Gemini directly. That produced two failures worth fixing:

- **A free-tier 429 stopped the feature.** One provider, no alternative. The ops copilot returned an error mid-review.
- **Model choice was a process-wide constant.** `LLM_MODEL` meant a one-word classification and a compliance summary went to the same model — one wasteful, the other under-powered.

The router makes provider selection a **per-request decision from live state**. A 429 on Groq routes the next request to Cerebras without the caller noticing.

---

## 2. Architecture

```
                        APPLICATION
             (pipeline, ops copilot, agents)
                             │
                    router.generate(task_type=…)
                             │
┌────────────────────────────▼────────────────────────────────┐
│                        LLM ROUTER                           │
│                                                             │
│   scoring ──────► eligibility filter  (capabilities,        │
│      │                                 context, tools)      │
│      │            score               (priority, cost,      │
│      │                                 latency, headroom)   │
│      ▼                                                      │
│   diversify ────► interleave providers so the first         │
│      │            fallback is different infrastructure      │
│      ▼                                                      │
│   for each candidate:                                       │
│      health ────► breaker open?    ─► skip, no request sent │
│           └─────► RPM/TPM used up? ─► skip, no request sent │
│      │                                                      │
│      └─► attempt ─► retry (transient only, backoff+jitter)  │
│                  ─► 429: honour Retry-After, next candidate │
│                  ─► permanent: no retry, next candidate     │
└─────────────────────────────┬───────────────────────────────┘
                              │
        ┌─────────────┬───────┴───────┬─────────────┐
        ▼             ▼               ▼             ▼
      Groq        Cerebras         Gemini        offline
   (OpenAI-      (OpenAI-         (native       (templates,
    compatible)   compatible)      v1beta)       no network)
```

### Files

| File | Responsibility |
|---|---|
| `router.py` | The one public interface. Owns the dispatch loop. |
| `schemas.py` | Pydantic types crossing the boundary. Mentions no provider. |
| `models.yaml` | Registry: models, limits, capabilities, priorities, prices. |
| `model_registry.py` | Loads and validates the YAML. Only place that knows model names. |
| `provider_registry.py` | Maps a provider to its adapter. |
| `scoring.py` | Eligibility filter, then ranking. Builds the fallback chain. |
| `rate_limiter.py` | Sliding-window RPM/TPM. In-memory and Redis. |
| `circuit_breaker.py` | CLOSED / OPEN / HALF_OPEN per model. |
| `health.py` | Composes limiter + breaker into "can this take a request?" |
| `retry.py` | Transient vs permanent classification; backoff with jitter. |
| `observability.py` | Structured logs, Prometheus metrics, cost tracking. |
| `tools.py` | ToolExecutor and the model → tool → model loop. |
| `agents.py` | Supervisor / specialists / synthesizer over the router. |
| `providers/` | `base.py`, `openai_compatible.py`, `gemini.py`, `offline.py`. |

---

## 3. Installation and configuration

```bash
pip install -r requirements.txt        # httpx, pyyaml, pydantic
pip install redis                      # optional — distributed limiting
pip install prometheus-client          # optional — metrics
pip install langgraph                  # optional — real StateGraph
```

All three extras are genuinely optional: without Redis the limiter is in-memory, without `prometheus_client` metrics are no-ops with identical signatures, and without LangGraph the same agent nodes run on a built-in executor.

### `.env`

```bash
# At least one. Whatever is present is used; the rest are skipped by name.
GROQ_API_KEY=gsk_...
CEREBRAS_API_KEY=csk-...
GEMINI_API_KEY=AIza...

# Optional
LLM_ROUTER_REDIS_URL=redis://localhost:6379/0   # unset = in-memory
LLM_ROUTER_MODELS=/path/to/models.yaml          # unset = bundled registry
LLM_PROVIDER=offline                            # force templates, keys ignored
```

**Keys are never hardcoded, never logged, and never stored on an object.** Adapters read them from the environment at call time — so a rotated key needs no restart, and no key can reach a repr, a log line or a metric label. `test_no_api_key_is_ever_stored_on_a_provider_instance` enforces this.

A provider with no key is dropped at **startup**, with the reason recorded:

```json
{"skipped_providers": {"cerebras": "CEREBRAS_API_KEY not set"}}
```

Discovering a missing key on the third fallback mid-request produces a confusing error; discovering it at boot names the variable.

### Redis

```bash
docker run -d -p 6379:6379 redis:7-alpine
export LLM_ROUTER_REDIS_URL=redis://localhost:6379/0
```

Only needed when **more than one process shares a provider quota**. This app deploys as a single container, so the default in-memory limiter is exactly right; Redis is there for when it is not.

If Redis becomes unreachable the limiter logs and degrades to in-memory. A limiter is a guard, not a system of record — failing every LLM call because Redis is down would be a worse outcome than slightly permissive limiting across replicas.

---

## 4. The model registry

`models.yaml` is expected to go out of date, and nothing in it is load-bearing for correctness. A wrong number degrades routing quality; it does not break the app. Everything that must not drift lives in Python and is tested.

```yaml
providers:
  groq:
    api_key_env: GROQ_API_KEY
    base_url: https://api.groq.com/openai/v1
    adapter: openai_compatible
    models:
      openai/gpt-oss-120b:
        priority: 1
        context_window: 131072
        rpm: 30
        tpm: 8000
        capabilities: [reasoning, coding, tool_calling, agentic, json_mode]
        cost_per_1m_input: 0.15
        cost_per_1m_output: 0.75
        enabled: true
```

Limits are **free-tier figures set deliberately low**. Guessing low costs a slightly early failover; guessing high costs a 429 on the request that mattered.

A typo in one capability tag drops that tag rather than failing the registry — the model becomes ineligible for tasks needing what was misspelled, which is visible and recoverable.

---

## 5. Model selection

A task type is a **request for capabilities**, not a model alias. The mapping lives in YAML:

```yaml
task_capabilities:
  reasoning:        [reasoning]
  fast:             [fast]
  classification:   [classification]
  vision:           [vision]
  agentic:          [tool_calling, reasoning]
  ops_chat:         [reasoning, long_context]
```

**Two stages, deliberately separate.** Eligibility is a hard filter — capabilities, context window, output ceiling. Score is a soft ranking among survivors: priority dominates (an operator's stated preference must win), then cost, observed latency and headroom break ties.

One weighted formula would eventually conclude that something is "cheap enough to outweigh not supporting vision". Separating them also makes *"no model has vision"* distinguishable from *"the vision model is rate-limited"* — different problems, different responses.

Resolved chains with all three keys present:

| Task | Chain |
|---|---|
| `reasoning` | groq:gpt-oss-120b → cerebras:gpt-oss-120b → gemini-2.5-flash |
| `fast` | groq:llama-3.1-8b → gemini-2.5-flash-lite → gemini-2.5-flash |
| `classification` | groq:llama-3.1-8b → gemini-2.5-flash-lite |
| `vision` | gemini-2.5-flash-lite → gemini-2.5-flash → gemini-flash-latest |
| `ops_chat` | groq:gpt-oss-120b → gemini-2.5-flash → groq:qwen3-32b |

`ops_chat` skips Cerebras automatically: it requires `long_context`, and Cerebras's free tier caps at 8192 tokens. Nobody wrote that rule — it falls out of the capability filter.

### Overrides

```python
await router.generate(messages, preferred_model="groq:openai/gpt-oss-120b")
```

An override must still be **eligible**. Honouring a preference for a model that cannot do the job produces a confusing downstream error instead of a working answer.

---

## 6. How rate limiting works

**Sliding window, not token bucket.** A bucket permits a full burst the instant it refills — exactly the shape that trips a per-minute cap. A sliding window answers the question the provider is actually asking: *"how many in the last 60 seconds?"*

```
RPM:  [====|====|====|====|====]  30 requests in the last 60s?
TPM:  [==========|==============]  8000 tokens in the last 60s?
             now-60s          now
```

**Why limit locally when the provider already does.** A 429 costs a round trip, and on a free tier some providers count rejected requests against the daily quota. If we know a model has issued 30 requests in the last minute and its limit is 30, asking again is guaranteed waste — local limiting turns that into an instant failover.

It is *not* a substitute for handling 429s. Our counters drift from the provider's (other processes, other keys, window alignment), so both exist: **predict locally, react to the truth.**

**Tokens are estimated before the call**, since output length is unknown, then reconciled with real usage afterwards. The estimate is pessimistic by design.

Under Redis the check-and-reserve is a **Lua script**, so two processes cannot both see room for the last request.

### On a 429

1. Read `Retry-After` — header first, then the body (Gemini puts it in `retryDelay: "7s"`).
2. Stand the model down in **both** the limiter and the breaker for that period.
3. **Never retry the same model.** Move to the next candidate.
4. Subsequent requests skip it entirely until the cooldown expires.

---

## 7. How fallback works

The chain is **computed per request**, not written down:

```
task_type=reasoning
   │
   ├─ eligible: 6 models across 3 providers
   ├─ ranked:   by priority, cost, latency, headroom
   └─ diversified: interleaved so consecutive attempts differ by provider

   1. groq:openai/gpt-oss-120b     ← 429, Retry-After 30s
   2. cerebras:gpt-oss-120b        ← succeeds
```

**Provider interleaving matters.** The common failure is provider-wide — an outage, or a per-org quota shared by every model on that key. Falling back from Groq's first model to Groq's second hits the same wall.

Adding a provider to `models.yaml` puts it in the chain for every task it qualifies for, with no code change.

When every candidate declines, the router either raises `NoCandidatesError` or falls back to the offline provider, depending on `allow_offline_fallback`. The vendor app sets it **False** at the router and handles the exception one layer up — where it has better templates than a generic placeholder.

---

## 8. Health and the circuit breaker

```
    CLOSED ──(3 consecutive failures)──► OPEN
      ▲                                   │
      │                            (cooldown elapses)
      │                                   ▼
      └────(probe succeeds)──────── HALF_OPEN ──(probe fails)──► OPEN
                                                              (2× cooldown)
```

Retries handle a request that failed for its own reasons. A breaker handles a **model failing for everyone**: after three consecutive failures the fourth will almost certainly fail too, and trying costs the caller a full timeout before failover.

`HALF_OPEN` admits **exactly one** probe, taken atomically. Several would take more requests down with a still-broken provider; none would mean the model never recovers.

**A 429 is not a breaker failure.** It means healthy-and-busy. Counting it as breakage would open the circuit on a working model and hold it open long after the window cleared. Rate limiting is the limiter's job.

Health is a **view**, not a second source of truth — a separately-maintained flag drifts from what it summarises, and then the router avoids a model that recovered ten minutes ago.

```bash
curl -H "X-API-Key: $API_KEY" localhost:8000/v1/llm/health
```

---

## 9. Retry policy

| Retried (transient) | Not retried (permanent) |
|---|---|
| 408, 409, 425, 429, 500, 502, 503, 504, 529 | 400, 401, 403, 404, 405, 413, 415, 422 |
| timeouts, connection resets | content policy, invalid model, malformed request |

`max_retries = 2`, exponential backoff with **full jitter** — a uniform draw from `[0, backoff]`. Without jitter every request hitting the same 429 retries at the same moment and recreates the burst that caused it.

**A permanent error still fails over.** It will not succeed on retry, but it is frequently provider-specific — a retired model ID, an unfunded key — and another provider often works. If the request itself is malformed, every candidate fails fast and the caller gets a real error rather than a hang.

---

## 10. Tool calling

```
model ──► tool_call ──► ToolExecutor ──► result ──► model ──► answer
```

```python
from backend.app.llm.router import Tool, ToolExecutor, run_tool_loop

executor = ToolExecutor()
executor.register(Tool(
    "search_web", "Search the web",
    {"type": "object", "properties": {"q": {"type": "string"}}},
    my_search_function))

response = await run_tool_loop(router, messages, executor, task_type="agentic")
```

The loop lives outside the router because it is a different concern: the router routes one exchange, a tool conversation is several. **Each hop is independently routed** — if Groq rate-limits after the first tool call, the second hop continues on Cerebras.

Four things the executor refuses to assume, each returning a structured error **to the model** rather than raising:

| Assumption | Reality | Response |
|---|---|---|
| The tool exists | Models hallucinate names | `{"error": ..., "available_tools": [...]}` |
| Arguments match | Often they do not | `{"error": ..., "expected_schema": {...}}` |
| Tools do not raise | They call networks | `{"error": "ValueError: ..."}` |
| The loop terminates | Models repeat calls | Hard stop at `max_iterations`, tools withheld on a final call |

A model told *"that tool does not exist, here are the ones that do"* usually recovers; an exception ends the conversation.

Tool schemas translate per dialect — Gemini rejects `additionalProperties` with a 400 that does not say which key was at fault, so it is stripped on that path only.

---

## 11. Agentic workflows

```
                        USER
                          │
                     SUPERVISOR            classify (cheap fast model)
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
    RESEARCH           CODING            GENERAL
        │                 │                 │
    web tools         code tools         db tools
        └─────────────────┼─────────────────┘
                          ▼
                     SYNTHESIZER
                          │
                        USER
```

```python
from backend.app.llm.router import AgentGraph

graph = AgentGraph(router, tools={"research": research_executor})
state = await graph.run("What changed in our vendor policy?")
print(state["answer"], state["trace"])
```

**Every node calls the same router and none names a provider** — enforced by `test_agents_never_learn_which_provider_served_them`, which greps the module for provider names. The classifier asks for `classification` and gets a small cheap model; the research agent asks for `research` and gets a reasoning model with tools. Routing policy is one YAML file, not a decision scattered across four agents.

Nodes are plain `async (state) -> state` functions with **no framework types in their signatures**. That builds a real LangGraph `StateGraph` when langgraph is installed, runs the identical functions on a small built-in executor when it is not, and makes the agent logic unit-testable without standing up a graph at all.

---

## 12. Observability and cost

One structured log line per **application-level request**, however many attempts it took:

```
a3f9c21e task=ops_chat -> cerebras:gpt-oss-120b 812ms in=2104 out=387
         retries=0 fallbacks=1 cost=$0.001026
```

Prometheus at `/metrics`:

| Metric | Labels |
|---|---|
| `llm_requests_total` | provider, model, task_type, status |
| `llm_requests_failed_total` | provider, model, error_type |
| `llm_rate_limits_total` | provider, model, source (`provider` \| `local`) |
| `llm_tokens_total` | provider, model, direction |
| `llm_latency_seconds` | provider, model (histogram) |
| `llm_fallback_total` | from_provider, to_provider |
| `llm_cost_usd_total` | provider, model |
| `llm_provider_health` | provider, model (gauge) |

`rate_limits_total{source="local"}` vs `{source="provider"}` is the useful one: if `local` dominates, the configured limits are too conservative; if `provider` dominates, they are too loose.

Prices live in `models.yaml`, never in business logic. Costs are **estimated, not billed** — good enough to answer "which task type is expensive?", not an invoice.

---

## 13. Adding a provider

**OpenAI-compatible** (OpenAI, OpenRouter, Together, Fireworks, DeepInfra) — no code at all:

```yaml
  together:
    api_key_env: TOGETHER_API_KEY
    base_url: https://api.together.xyz/v1
    adapter: openai_compatible
    models:
      meta-llama/Llama-3.3-70B-Instruct-Turbo:
        priority: 3
        capabilities: [reasoning, coding, tool_calling]
        rpm: 60
        tpm: 100000
        cost_per_1m_input: 0.88
        cost_per_1m_output: 0.88
```

**A different wire format** (Anthropic) — one file plus one line:

```python
# providers/anthropic.py
class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"
    async def generate(self, *, spec, messages, tools=None, **kw) -> LLMResponse: ...
    async def stream(self, *, spec, messages, **kw) -> AsyncIterator[str]: ...

# provider_registry.py
ADAPTERS["anthropic"] = AnthropicProvider
```

Nothing in `router.py`, `scoring.py`, `health.py` or any caller changes.

---

## 14. Adding a model

Copy a block in `models.yaml`. Retire one with `enabled: false`. Retune a limit by editing `rpm`/`tpm`. No code change in any case — `test_the_registry_is_editable_without_touching_code` verifies this rather than asserting it in a comment.

---

## 15. How the vendor app uses it

Three methods, unchanged from before the router existed:

```python
get_llm().draft_vendor_email(payload)   # -> (text, cached)
get_llm().reviewer_summary(payload)     # -> (text, cached)
get_llm().ops_chat(payload, messages)   # -> {reply, source, grounded_in}
```

Domain task types let the app express **intent** rather than a size, and let routing be retuned centrally:

| Task | Requires | Why |
|---|---|---|
| `reviewer_summary` | `reasoning` | Read by a human deciding whether to pay someone |
| `vendor_email` | `fast` | Short, and the disclosure gate already decided the content |
| `ops_chat` | `reasoning`, `long_context` | A reviewer waiting, over a large case record |
| `doc_extraction` | `json_mode` | Structured output, not prose |

**What did not change:** caching, the offline templates, and the grounded-copilot ordering (case record → model → honest refusal). None are provider concerns — answering a reviewer from the record rather than a model is a correctness decision that would be wrong to delegate.

`LLM_PROVIDER=offline` still forces templates. Legacy values (`gemini`, `openai`) are accepted and mean "use the router": naming one provider no longer pins requests to it, because pinning gives up the failover that is the point.

The router is async; the pipeline is a synchronous SSE generator. One background event loop bridges them (`client.py::_run`) — keeping connections warm and rate-limit windows coherent, which `asyncio.run` per call would not.

---

## 16. Testing

```bash
pytest tests/test_llm_router.py -q      # 72 tests
```

**Every provider in the suite is fake and nothing touches a network.** Rate limits are simulated by raising `RateLimitError`, not by exhausting a real free tier — that would be slow, non-deterministic, and would burn the quota the demo needs. The fakes implement the same `BaseLLMProvider` surface, so what is exercised is real scoring, real windows, real breaker, real backoff.

| Group | Covers |
|---|---|
| Normal request | Routing, usage, cost, priority order, overrides |
| Failure & fallback | Failover, recorded chain, provider diversity, all-unavailable, offline |
| 429 handling | Failover, no same-model retry, Retry-After, skip-without-sending, not a breaker failure |
| Retry policy | Transient retried, permanent not, still fails over, classification table, jitter |
| Circuit breaker | Opens, probes once, reopens longer on probe failure, router skips |
| Rate limiting | RPM/TPM exhaustion, reconciliation, sliding window, skip before send |
| Capabilities | Vision routing, impossible requirements, context filtering, implied tool calling |
| Tool calling | Round trip, hallucinated names, bad arguments, raising tools, loop bound, parallelism |
| Streaming | Incremental, pre-token failover, `stream=True` rejected on `generate` |
| Concurrency | 12 parallel, atomic reservation, spread across providers |
| Agents | Supervisor routes, junk defaults to general, failure recovers, no provider names |
| Configuration | Missing key skipped by name, no key on an instance, YAML-only model addition |

---

## 17. Design decisions

| Decision | Why | Cost |
|---|---|---|
| Sliding window, not token bucket | A bucket permits the burst that trips the cap | Memory ∝ requests/window — negligible here |
| Local limiting *and* 429 handling | Counters drift; predict locally, react to truth | Two mechanisms to keep aligned |
| 429 never counts as a breaker failure | It means healthy-and-busy | Two failure notions to keep separate |
| Permanent errors fail over but never retry | Usually provider-specific; malformed requests fail fast | Malformed request tries every candidate once |
| Eligibility and scoring separate | Nothing is "cheap enough" to outweigh missing vision | Two passes |
| httpx, not three SDKs | Three dependency trees, three exception hierarchies | Wire formats maintained by hand |
| Capabilities in YAML, not adapters | Whether a model does vision is a fact about the model | Registry must stay honest |
| Keys read at call time | Rotation needs no restart; nothing to leak in a repr | A dict lookup per call |
| Redis optional, degrades to in-memory | A guard should not take down what it guards | Slightly permissive across replicas |
| LangGraph optional | One container, one URL is the deployment goal | Two runners for the same nodes |
| Offline provider is first-class | Every result reproducible with no key | Templates maintained alongside prompts |

### Known limits

- **In-memory limiting is per-process.** Correct for this single-container deployment; set `LLM_ROUTER_REDIS_URL` before scaling out.
- **Token estimates are character-based**, biased high. A real tokenizer means shipping a per-provider vocabulary and encoding every prompt twice.
- **No mid-stream failover.** Once bytes have reached the caller, switching model would splice two different answers together.
- **`models.yaml` will go stale.** That is expected and stated at the top of the file; wrong numbers degrade routing quality rather than breaking anything.
