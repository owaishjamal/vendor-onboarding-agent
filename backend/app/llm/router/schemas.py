"""Types crossing the router boundary.

The whole point of the router is that the application says what it needs and
never names a provider. These schemas are that contract: a request describes a
TASK, a response describes what happened. Nothing here mentions Groq, Cerebras
or Gemini, and nothing above the router should either.
"""

from __future__ import annotations

import enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Capabilities and task types
# ---------------------------------------------------------------------------

class Capability(str, enum.Enum):
    """What a model can do. Requirements are expressed in these terms only.

    Selection asks "which models can reason and call tools?", never "is this
    gpt-oss-120b?". That indirection is what lets a model be swapped in
    models.yaml without touching a line of application code.
    """

    REASONING = "reasoning"
    CODING = "coding"
    FAST = "fast"
    CLASSIFICATION = "classification"
    LONG_CONTEXT = "long_context"
    VISION = "vision"
    TOOL_CALLING = "tool_calling"
    AGENTIC = "agentic"
    JSON_MODE = "json_mode"
    STREAMING = "streaming"


class TaskType(str, enum.Enum):
    """What the caller is trying to do.

    A task type is a REQUEST FOR CAPABILITIES, not a model alias. The mapping
    from task to required capabilities lives in models.yaml, so "what should
    handle coding?" is answered by configuration and can change without a
    deploy.
    """

    GENERAL = "general"
    REASONING = "reasoning"
    CODING = "coding"
    FAST = "fast"
    CLASSIFICATION = "classification"
    LONG_CONTEXT = "long_context"
    VISION = "vision"
    AGENTIC = "agentic"
    TOOL_CALLING = "tool_calling"
    RESEARCH = "research"
    # Domain tasks for the vendor-onboarding app. They exist so the app can
    # express intent ("this is a compliance summary") rather than a size
    # ("give me something big"), and so their routing can be retuned centrally.
    VENDOR_EMAIL = "vendor_email"
    REVIEWER_SUMMARY = "reviewer_summary"
    OPS_CHAT = "ops_chat"
    DOC_EXTRACTION = "doc_extraction"


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthState(str, enum.Enum):
    HEALTHY = "healthy"
    RATE_LIMITED = "rate_limited"    # a 429 we were told about
    COOLDOWN = "cooldown"            # backing off after failures
    ERROR = "error"                  # failing for a non-rate-limit reason
    DISABLED = "disabled"            # switched off in configuration


class BreakerState(str, enum.Enum):
    CLOSED = "closed"        # normal
    OPEN = "open"            # refusing traffic
    HALF_OPEN = "half_open"  # letting one probe through


# ---------------------------------------------------------------------------
# Messages and tools
# ---------------------------------------------------------------------------

class Message(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    # Assistant turns that call tools, and the tool turns that answer them.
    tool_calls: Optional[list[dict[str, Any]]] = None
    tool_call_id: Optional[str] = None
    name: Optional[str] = None


class ToolSpec(BaseModel):
    """A tool in the OpenAI function-calling shape.

    Chosen as the internal representation because it is what most providers
    accept unchanged; the Gemini adapter translates it, which keeps exactly one
    translation in the codebase instead of one per caller.
    """

    name: str
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=lambda: {
        "type": "object", "properties": {}})

    def as_openai(self) -> dict[str, Any]:
        return {"type": "function",
                "function": {"name": self.name,
                             "description": self.description,
                             "parameters": self.parameters}}

    def as_gemini(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "parameters": _strip_unsupported(self.parameters)}


def _strip_unsupported(schema: Any) -> Any:
    """Gemini's function schema is a subset of JSON Schema.

    It rejects `additionalProperties`, `$schema` and friends with a 400 that
    does not say which key was at fault. Dropping them is cheaper than
    discovering it in production, and harmless for the providers that do
    accept them because this runs only on the Gemini path.
    """
    if isinstance(schema, dict):
        drop = {"additionalProperties", "$schema", "definitions", "$defs",
                "examples", "default", "title"}
        return {k: _strip_unsupported(v) for k, v in schema.items() if k not in drop}
    if isinstance(schema, list):
        return [_strip_unsupported(v) for v in schema]
    return schema


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------

class LLMRequest(BaseModel):
    messages: list[Message]
    task_type: TaskType = TaskType.GENERAL
    preferred_model: Optional[str] = None      # "provider:model", an override
    tools: Optional[list[ToolSpec]] = None
    stream: bool = False
    max_tokens: int = 1024
    temperature: float = 0.0
    # Extra capabilities beyond what the task type implies.
    require: list[Capability] = Field(default_factory=list)
    # Hard floor on the context window, when the caller knows its payload size.
    min_context: int = 0
    request_id: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens


class Attempt(BaseModel):
    """One try against one model. The audit trail of a routing decision.

    Kept on the response rather than only in logs: when a request is served by
    the third model in a chain, the caller can see the two that declined and
    why, without going to a log aggregator.
    """

    provider: str
    model: str
    ok: bool
    error_type: Optional[str] = None
    error: Optional[str] = None
    latency_ms: int = 0
    status_code: Optional[int] = None


class LLMResponse(BaseModel):
    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    provider: str = ""
    model: str = ""
    task_type: TaskType = TaskType.GENERAL
    usage: Usage = Field(default_factory=Usage)
    latency_ms: int = 0
    retry_count: int = 0
    fallback_count: int = 0
    estimated_cost_usd: float = 0.0
    request_id: str = ""
    attempts: list[Attempt] = Field(default_factory=list)
    cached: bool = False
    finish_reason: Optional[str] = None

    @property
    def routed_to(self) -> str:
        return f"{self.provider}:{self.model}"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class LLMError(Exception):
    """Base. Carries whether a retry could plausibly help."""

    retryable = False
    status_code: Optional[int] = None

    def __init__(self, message: str, *, status_code: Optional[int] = None,
                 provider: str = "", model: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.model = model


class RateLimitError(LLMError):
    """429 / RESOURCE_EXHAUSTED. Retryable, but never immediately."""

    retryable = True

    def __init__(self, message: str, *, retry_after: Optional[float] = None, **kw):
        super().__init__(message, **kw)
        self.retry_after = retry_after


class TransientError(LLMError):
    """408/500/502/503/504, timeouts, connection resets."""

    retryable = True


class PermanentError(LLMError):
    """400/401/403, unknown model, malformed request, content policy.

    Retrying these burns quota and latency to arrive at the same answer, so the
    router fails over to a DIFFERENT model rather than retrying this one — and
    if the cause is a malformed request, that fails too, quickly and loudly.
    """

    retryable = False


class NoCandidatesError(LLMError):
    """Nothing satisfies the request: everything is rate-limited, in cooldown,
    or no configured model has the capabilities asked for."""

    retryable = False
