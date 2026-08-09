"""LLM router — one internal interface over Groq, Cerebras and Gemini.

    from backend.app.llm.router import LLMRouter, TaskType

    router = LLMRouter()
    r = await router.generate(
        messages=[{"role": "user", "content": "Explain RAG"}],
        task_type="reasoning")
    print(r.text)

The application never imports a provider, never names a model, and never
handles a 429. See README-ROUTER.md for the architecture and for how to add a
provider or a model.
"""

from backend.app.llm.router.agents import AgentGraph, AgentState
from backend.app.llm.router.model_registry import (
    ModelRegistry, ModelSpec, get_registry, reset_registry,
)
from backend.app.llm.router.router import LLMRouter, get_router, reset_router
from backend.app.llm.router.schemas import (
    Attempt, BreakerState, Capability, HealthState, LLMError, LLMRequest,
    LLMResponse, Message, NoCandidatesError, PermanentError, RateLimitError,
    TaskType, ToolCall, ToolSpec, TransientError, Usage,
)
from backend.app.llm.router.tools import Tool, ToolExecutor, run_tool_loop

__all__ = [
    "LLMRouter", "get_router", "reset_router",
    "ModelRegistry", "ModelSpec", "get_registry", "reset_registry",
    "TaskType", "Capability", "Message", "ToolSpec", "ToolCall",
    "LLMRequest", "LLMResponse", "Usage", "Attempt",
    "HealthState", "BreakerState",
    "LLMError", "RateLimitError", "TransientError", "PermanentError",
    "NoCandidatesError",
    "Tool", "ToolExecutor", "run_tool_loop",
    "AgentGraph", "AgentState",
]
