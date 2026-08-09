"""Which model should serve this request?

Two stages, deliberately separate:

  ELIGIBILITY  a hard filter. Capabilities, context window, tool support.
               A model that fails this cannot serve the request at all, and
               no amount of being fast or cheap changes that.

  SCORE        a soft ranking among the eligible. Priority dominates; cost,
               observed latency and headroom break ties.

Why not one weighted formula? Because "cheap enough to outweigh not
supporting vision" is nonsense, and any single scoring pass eventually
produces exactly that. Separating them also gives a better error: "no model
has vision" and "the vision model is rate-limited" are different problems and
the caller can tell which one they have.

Live availability is NOT part of the score. It is checked at dispatch, in
order, because checking it here would reserve capacity on every candidate
just to rank them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from backend.app.llm.router.model_registry import ModelRegistry, ModelSpec
from backend.app.llm.router.schemas import Capability, LLMRequest, TaskType


@dataclass
class Candidate:
    spec: ModelSpec
    score: float
    why: str


def required_capabilities(registry: ModelRegistry,
                          req: LLMRequest) -> set[Capability]:
    """Everything this request needs, from three sources.

    The task type is the main one; `require` lets a caller add a constraint
    the task type does not imply; and passing tools implies tool calling
    whether or not the caller thought to say so — that last one prevents a
    silent failure where a model without tool support quietly ignores them and
    answers in prose.
    """
    caps = registry.capabilities_for(req.task_type)
    caps |= set(req.require)
    if req.tools:
        caps.add(Capability.TOOL_CALLING)
    if req.stream:
        caps.add(Capability.STREAMING)
    return caps


def eligible(registry: ModelRegistry, req: LLMRequest) -> list[ModelSpec]:
    caps = required_capabilities(registry, req)
    need_ctx = max(req.min_context, 0)
    out = []
    for spec in registry.all():
        if not spec.enabled:
            continue
        if not spec.supports(caps):
            continue
        if spec.context_window < need_ctx:
            continue
        # A model whose output ceiling is below what was asked for would
        # truncate mid-answer. Better to route elsewhere than to return a
        # sentence that stops halfway.
        if req.max_tokens > spec.max_output_tokens:
            continue
        out.append(spec)
    return out


def score(spec: ModelSpec, req: LLMRequest, *,
          avg_latency_ms: float = 0.0) -> tuple[float, str]:
    """Higher is better. Returns (score, human-readable reason)."""
    reasons = []

    # Priority is the operator's stated preference and dominates everything
    # else: priority 1 must beat priority 2 no matter how cheap or fast the
    # latter is, or the config file stops meaning what it says.
    s = (10 - min(spec.priority, 9)) * 100.0
    reasons.append(f"priority {spec.priority}")

    # Cost, normalised against a rough ceiling of $5/1M output. Worth at most
    # 30 points — a tiebreak, never a reason to take a worse model.
    if spec.cost_per_1m_output > 0:
        s += max(0.0, 30.0 * (1 - min(spec.cost_per_1m_output / 5.0, 1.0)))
        reasons.append(f"${spec.cost_per_1m_output}/1M out")

    # Observed latency, worth at most 20. Only counts once we have data, so a
    # cold model is not penalised for being unmeasured.
    if avg_latency_ms > 0:
        s += max(0.0, 20.0 * (1 - min(avg_latency_ms / 10_000.0, 1.0)))
        reasons.append(f"~{avg_latency_ms:.0f}ms")

    # A little credit for headroom, so a 60k-TPM model is preferred over a
    # 6k-TPM one at equal priority. Capped at 10: headroom is a nice-to-have,
    # not a reason to override the operator.
    s += min(10.0, spec.tpm / 10_000.0)

    # Interactive tasks prefer models tagged fast; batch tasks do not care.
    if req.task_type in (TaskType.FAST, TaskType.CLASSIFICATION):
        if Capability.FAST in spec.capabilities:
            s += 40.0
            reasons.append("tagged fast")

    # Don't burn a large reasoning model on a trivial ask when a cheap one
    # qualifies. Saves money and, on a free tier, saves the scarce quota for
    # the requests that need it.
    if req.task_type is TaskType.CLASSIFICATION and spec.cost_per_1m_output > 1.0:
        s -= 25.0
        reasons.append("expensive for classification")

    return s, ", ".join(reasons)


def rank(registry: ModelRegistry, req: LLMRequest,
         *, latency_lookup=None) -> list[Candidate]:
    """The full fallback chain for this request, best first.

    This IS the fallback chain — it is computed per request from live
    configuration rather than written down anywhere. Add a provider to
    models.yaml and it appears in the chain for every task it qualifies for,
    with no code change.
    """
    specs = eligible(registry, req)

    # An explicit override still has to be eligible. Honouring a preference
    # for a model that cannot do the job produces a confusing downstream error
    # instead of a clear "that model lacks vision".
    if req.preferred_model:
        pref = [s for s in specs if s.key == req.preferred_model]
        if pref:
            rest = [s for s in specs if s.key != req.preferred_model]
            head = Candidate(pref[0], 1e9, "explicitly requested")
            return [head] + _ranked(rest, req, latency_lookup)

    return _ranked(specs, req, latency_lookup)


def _ranked(specs: Iterable[ModelSpec], req: LLMRequest,
            latency_lookup) -> list[Candidate]:
    out = []
    for spec in specs:
        lat = latency_lookup(spec.key) if latency_lookup else 0.0
        s, why = score(spec, req, avg_latency_ms=lat)
        out.append(Candidate(spec, s, why))
    out.sort(key=lambda c: c.score, reverse=True)
    return out


def diversify(chain: list[Candidate]) -> list[Candidate]:
    """Reorder so consecutive attempts prefer a DIFFERENT provider.

    The common failure is provider-wide: an outage, or a per-org quota shared
    by every model on that key. Falling back from Groq's first model to Groq's
    second then hits the same wall. Interleaving providers means the first
    fallback is usually on different infrastructure, while still respecting
    score order within each provider.
    """
    by_provider: dict[str, list[Candidate]] = {}
    for c in chain:
        by_provider.setdefault(c.spec.provider, []).append(c)

    # Provider order follows their best candidate, so the top pick stays top.
    order = sorted(by_provider, key=lambda p: -by_provider[p][0].score)
    out: list[Candidate] = []
    i = 0
    while len(out) < len(chain):
        for p in order:
            bucket = by_provider[p]
            if i < len(bucket):
                out.append(bucket[i])
        i += 1
    return out
