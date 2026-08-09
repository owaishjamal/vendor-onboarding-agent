"""A provider that never leaves the process.

Not a mock — it is the reason the whole test suite runs without a network, the
reason a demo survives an exhausted quota, and the reason every verdict in the
README is reproducible with no API key. It is registered like any other
provider and reached through the same interface, so the router's behaviour on
this path is the behaviour it has on every path.

It deliberately does NOT try to sound like a model. Its job is to be
recognisably deterministic so nobody mistakes a fallback for a real answer;
the vendor-onboarding prose templates in `backend/app/llm/offline.py` are what
produce publishable text.
"""

from __future__ import annotations

import json
from typing import AsyncIterator, Optional

from backend.app.llm.router.model_registry import ModelSpec
from backend.app.llm.router.providers.base import BaseLLMProvider
from backend.app.llm.router.rate_limiter import estimate_tokens
from backend.app.llm.router.schemas import (
    LLMResponse, Message, ToolSpec, Usage,
)


class OfflineProvider(BaseLLMProvider):
    name = "offline"

    def __init__(self, **_: object):
        super().__init__(base_url="", api_key_env="")

    @property
    def api_key(self) -> str:
        return "not-required"

    async def generate(self, *, spec: ModelSpec, messages: list[Message],
                       tools: Optional[list[ToolSpec]] = None,
                       max_tokens: int = 1024, temperature: float = 0.0,
                       timeout: float = 60.0) -> LLMResponse:
        last = next((m.content for m in reversed(messages)
                     if m.role == "user"), "")
        text = (
            "[offline] No language model is configured, so this is a "
            "deterministic placeholder rather than a generated answer.\n\n"
            f"The request was {len(last)} characters"
            + (f", and {len(tools)} tool(s) were offered." if tools else ".")
        )
        prompt_chars = sum(len(m.content or "") for m in messages)
        return LLMResponse(
            text=text, provider=self.name, model=spec.name,
            usage=Usage(input_tokens=estimate_tokens("x" * prompt_chars),
                        output_tokens=estimate_tokens(text)),
            finish_reason="stop")

    async def stream(self, *, spec: ModelSpec, messages: list[Message],
                     tools: Optional[list[ToolSpec]] = None,
                     max_tokens: int = 1024, temperature: float = 0.0,
                     timeout: float = 60.0) -> AsyncIterator[str]:
        r = await self.generate(spec=spec, messages=messages, tools=tools,
                                max_tokens=max_tokens, temperature=temperature)
        for word in r.text.split(" "):
            yield word + " "

    async def health_check(self, spec: ModelSpec) -> bool:
        return True
