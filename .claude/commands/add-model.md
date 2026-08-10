---
description: Add or retune a model in the LLM router registry
argument-hint: <provider>:<model-id>
---

Add `$ARGUMENTS` to the router. This should be a **YAML edit and nothing else** —
if you find yourself changing `router.py`, `scoring.py` or `health.py`, stop and
explain why, because that is a design failure rather than a task.

## 1. Find the real numbers

Do not guess. Search for the provider's current model list, context window,
free-tier RPM/TPM and per-million pricing. Model IDs get renamed and limits get
retuned; a plausible-looking wrong ID 404s on first call.

## 2. Edit `backend/app/llm/router/models.yaml`

```yaml
  <provider>:
    api_key_env: <PROVIDER>_API_KEY
    base_url: https://...
    adapter: openai_compatible      # or a named adapter
    models:
      <model-id>:
        priority: 3
        context_window: 131072
        max_output_tokens: 8192
        rpm: 30
        tpm: 6000
        capabilities: [reasoning, coding, tool_calling]
        cost_per_1m_input: 0.15
        cost_per_1m_output: 0.60
        enabled: true
```

**Set limits conservatively.** Guessing low costs a slightly early failover;
guessing high costs a 429 on the request that mattered.

**Capabilities are a contract, not a description.** Only tag `reasoning` if you
would let it explain why a vendor was flagged. Only tag `fast` if it is
genuinely a fast lane. Priority is per-candidate-set, not a quality ranking — a
small model can be priority 1 for the `fast` set and never appear in `reasoning`
because its tags exclude it.

## 3. Show the effect

Print the resolved chain for each task type before and after, so the change is
visible rather than asserted:

```bash
python - <<'PY'
import sys; sys.path.insert(0, '.')
from backend.app.llm.router import LLMRouter, TaskType, scoring
from backend.app.llm.router.schemas import LLMRequest, Message
r = LLMRouter()
for t in ("reasoning", "fast", "classification", "vision", "ops_chat"):
    req = LLMRequest(messages=[Message(role="user", content="x")], task_type=TaskType(t))
    chain = scoring.diversify(scoring.rank(r.registry, req))
    print(f"{t:<16} " + " > ".join(c.spec.key for c in chain[:4]))
PY
```

Sanity-check the result. A model appearing in a chain it has no business in
means its capability tags are wrong.

## 4. Verify

`pytest tests/test_llm_router.py -q` — 72 tests. Nothing should need changing;
if a test breaks, the registry edit changed behaviour it was pinning.
