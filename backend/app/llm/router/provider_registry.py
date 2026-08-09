"""Adapter instances, one per provider, built from the model registry.

Adding a provider is two steps and no router change:

  1. Write an adapter subclassing BaseLLMProvider (or reuse
     OpenAICompatibleProvider if the provider speaks that dialect).
  2. Register it in ADAPTERS below and reference the key as `adapter:` in
     models.yaml.

OpenAI, Anthropic, OpenRouter, Together and Fireworks all fit this shape —
the first, fourth and fifth need no new adapter at all, only a models.yaml
block pointing at `openai_compatible` with their base URL.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from backend.app.llm.router.model_registry import ModelRegistry, ModelSpec
from backend.app.llm.router.providers.base import BaseLLMProvider
from backend.app.llm.router.providers.gemini import GeminiProvider
from backend.app.llm.router.providers.offline import OfflineProvider
from backend.app.llm.router.providers.openai_compatible import (
    CerebrasProvider, GroqProvider, OpenAICompatibleProvider,
)

log = logging.getLogger("vo.llm.providers")

# provider name (as in models.yaml) -> adapter class
ADAPTERS: dict[str, Callable[..., BaseLLMProvider]] = {
    "groq": GroqProvider,
    "cerebras": CerebrasProvider,
    "gemini": GeminiProvider,
    "offline": OfflineProvider,
}

# adapter dialect -> class, for providers not named individually above.
# A new OpenAI-compatible host needs only `adapter: openai_compatible`.
DIALECTS: dict[str, Callable[..., BaseLLMProvider]] = {
    "openai_compatible": OpenAICompatibleProvider,
    "gemini": GeminiProvider,
    "offline": OfflineProvider,
}


class ProviderRegistry:
    def __init__(self, registry: ModelRegistry):
        self._providers: dict[str, BaseLLMProvider] = {}
        for name, conf in registry.providers.items():
            cls = ADAPTERS.get(name) or DIALECTS.get(
                conf.get("adapter", "openai_compatible"))
            if cls is None:
                log.warning("no adapter for provider %r; skipping", name)
                continue
            try:
                inst = cls(base_url=conf.get("base_url", ""),
                           api_key_env=conf.get("api_key_env", ""))
                # The dialect classes are generic; name them after the
                # provider so logs, metrics and responses say "groq" rather
                # than "openai_compatible".
                inst.name = name
                self._providers[name] = inst
            except Exception as exc:
                log.warning("could not construct provider %r: %s", name, exc)

        # Always available, and not in models.yaml — it needs no key and must
        # never be selected by scoring, only reached by explicit fallback.
        self._offline = OfflineProvider()

    def get(self, spec: ModelSpec) -> Optional[BaseLLMProvider]:
        return self._providers.get(spec.provider)

    @property
    def offline(self) -> OfflineProvider:
        return self._offline

    def names(self) -> list[str]:
        return sorted(self._providers)
