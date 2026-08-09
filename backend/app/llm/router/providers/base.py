"""The provider contract.

Every adapter takes our schema types in and gives our schema types out. The
router never sees a provider-shaped dict, and no provider SDK type escapes the
adapter — which is what makes adding OpenAI, Anthropic, OpenRouter, Together or
Fireworks a new file rather than a change to the router.

Transport is httpx over each provider's HTTP API rather than its SDK. Three
SDKs means three dependency trees, three auth conventions and three sets of
exception types to normalise; the HTTP surfaces are small, stable, and already
documented. Where an SDK earns its place later it can live behind this same
interface.
"""

from __future__ import annotations

import abc
import logging
import os
from typing import Any, AsyncIterator, Optional

import httpx

from backend.app.llm.router.model_registry import ModelSpec
from backend.app.llm.router.schemas import (
    Capability, LLMResponse, Message, ToolSpec,
)

log = logging.getLogger("vo.llm.provider")

# One connection pool per process, shared by every adapter. A pool per request
# means a fresh TLS handshake per call — tens of milliseconds against
# providers whose whole selling point is latency.
_CLIENT: Optional[httpx.AsyncClient] = None


def http_client() -> httpx.AsyncClient:
    global _CLIENT
    if _CLIENT is None or _CLIENT.is_closed:
        _CLIENT = httpx.AsyncClient(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_connections=32, max_keepalive_connections=16),
        )
    return _CLIENT


async def close_http_client() -> None:
    global _CLIENT
    if _CLIENT is not None and not _CLIENT.is_closed:
        await _CLIENT.aclose()
    _CLIENT = None


class BaseLLMProvider(abc.ABC):
    """One provider. Instances are cheap and hold no per-request state."""

    name: str = "base"

    def __init__(self, *, base_url: str, api_key_env: str):
        self.base_url = base_url.rstrip("/")
        self.api_key_env = api_key_env

    @property
    def api_key(self) -> str:
        """Read at call time, never cached.

        Caching a key at construction means a rotated key needs a restart, and
        it puts the secret in an attribute that ends up in a repr. Reading from
        the environment each time costs a dict lookup.
        """
        return os.getenv(self.api_key_env, "")

    # -- required ----------------------------------------------------------

    @abc.abstractmethod
    async def generate(self, *, spec: ModelSpec, messages: list[Message],
                       tools: Optional[list[ToolSpec]] = None,
                       max_tokens: int = 1024, temperature: float = 0.0,
                       timeout: float = 60.0) -> LLMResponse: ...

    @abc.abstractmethod
    async def stream(self, *, spec: ModelSpec, messages: list[Message],
                     tools: Optional[list[ToolSpec]] = None,
                     max_tokens: int = 1024, temperature: float = 0.0,
                     timeout: float = 60.0) -> AsyncIterator[str]: ...

    # -- capability queries -------------------------------------------------
    #
    # Answered from the registry rather than hardcoded per adapter: whether a
    # given model does vision is a fact about the model, and models.yaml is
    # where model facts live. An adapter asserting "gemini supports vision"
    # would be wrong the moment a text-only Gemini model is added.

    def supports_tools(self, spec: ModelSpec) -> bool:
        return Capability.TOOL_CALLING in spec.capabilities

    def supports_vision(self, spec: ModelSpec) -> bool:
        return Capability.VISION in spec.capabilities

    def supports_streaming(self, spec: ModelSpec) -> bool:
        return Capability.STREAMING in spec.capabilities

    async def health_check(self, spec: ModelSpec) -> bool:
        """A minimal real call. Used by the /health endpoint and by tests.

        Deliberately a real generation rather than a HEAD or a models list: a
        provider whose catalogue endpoint answers but whose inference is
        failing is exactly the case a health check exists to catch.
        """
        try:
            r = await self.generate(
                spec=spec, messages=[Message(role="user", content="ping")],
                max_tokens=4, timeout=15.0)
            return bool(r.text or r.tool_calls) or r.finish_reason is not None
        except Exception as exc:
            log.debug("health check failed for %s: %s", spec.key, exc)
            return False

    def __repr__(self) -> str:            # never include the key
        return f"<{type(self).__name__} {self.name} {self.base_url}>"
