"""Provider-agnostic LLM access with caching and an offline fallback.

The pipeline calls `get_llm().draft_vendor_email(...)` and
`get_llm().reviewer_summary(...)`. It never imports a vendor SDK, so switching
provider is an environment variable — and dropping to offline mode thirty
seconds before a demo is a one-line change that alters nothing else.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional

from backend.app import config
from backend.app.llm import offline
from backend.app.llm.prompts import (
    REVIEWER_SUMMARY_PROMPT_VERSION, REVIEWER_SUMMARY_SYSTEM,
    VENDOR_EMAIL_PROMPT_VERSION, VENDOR_EMAIL_SYSTEM,
)

log = logging.getLogger("vo.llm")


def _key(kind: str, version: str, payload: str) -> str:
    return hashlib.sha256(f"{kind}|{version}|{payload}".encode()).hexdigest()[:32]


def _cache_get(k: str) -> Optional[Any]:
    if not config.LLM_CACHE_ENABLED:
        return None
    p = config.CACHE_DIR / f"{k}.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return None
    return None


def _cache_put(k: str, v: Any) -> None:
    if config.LLM_CACHE_ENABLED:
        (config.CACHE_DIR / f"{k}.json").write_text(json.dumps(v, indent=2))


class LLMClient:
    provider = "offline"

    def _complete(self, system: str, user: str, max_tokens: int) -> str:
        raise NotImplementedError

    # -- public -------------------------------------------------------------

    def draft_vendor_email(self, payload: dict[str, Any]) -> tuple[str, bool]:
        if not payload.get("vendor_items"):
            return "", False
        blob = json.dumps(payload, sort_keys=True, default=str)
        k = _key(f"vemail:{self.provider}", VENDOR_EMAIL_PROMPT_VERSION, blob)
        hit = _cache_get(k)
        if hit is not None:
            return hit, True

        if self.provider == "offline":
            text = offline.draft_vendor_email(payload)
        else:
            try:
                text = self._complete(VENDOR_EMAIL_SYSTEM, blob, 600).strip()
            except Exception as exc:
                log.warning("vendor email generation failed (%s); using template", exc)
                text = offline.draft_vendor_email(payload)

        _cache_put(k, text)
        return text, False

    def reviewer_summary(self, payload: dict[str, Any]) -> tuple[str, bool]:
        blob = json.dumps(payload, sort_keys=True, default=str)
        k = _key(f"rsum:{self.provider}", REVIEWER_SUMMARY_PROMPT_VERSION, blob)
        hit = _cache_get(k)
        if hit is not None:
            return hit, True

        if self.provider == "offline":
            text = offline.reviewer_summary(payload)
        else:
            try:
                text = self._complete(REVIEWER_SUMMARY_SYSTEM, blob, 500).strip()
            except Exception as exc:
                log.warning("reviewer summary failed (%s); using template", exc)
                text = offline.reviewer_summary(payload)

        _cache_put(k, text)
        return text, False


class OfflineClient(LLMClient):
    provider = "offline"


class AnthropicClient(LLMClient):
    provider = "anthropic"

    def __init__(self) -> None:
        import anthropic
        self._c = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        self._model = config.LLM_MODEL or "claude-haiku-4-5-20251001"

    def _complete(self, system: str, user: str, max_tokens: int) -> str:
        r = self._c.messages.create(
            model=self._model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in r.content if getattr(b, "type", "") == "text")


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self) -> None:
        from openai import OpenAI
        self._c = OpenAI(api_key=config.OPENAI_API_KEY)
        self._model = config.LLM_MODEL or "gpt-4o-mini"

    def _complete(self, system: str, user: str, max_tokens: int) -> str:
        r = self._c.chat.completions.create(
            model=self._model, max_tokens=max_tokens,
            messages=[{"role": "system", "content": system},
                      {"role": "user", "content": user}],
        )
        return r.choices[0].message.content or ""


class GeminiClient(LLMClient):
    provider = "gemini"

    def __init__(self) -> None:
        import google.generativeai as genai
        genai.configure(api_key=config.GEMINI_API_KEY)
        self._genai = genai
        self._model_name = config.LLM_MODEL or "gemini-1.5-flash"

    def _complete(self, system: str, user: str, max_tokens: int) -> str:
        m = self._genai.GenerativeModel(self._model_name, system_instruction=system)
        r = m.generate_content(
            user, generation_config={"max_output_tokens": max_tokens, "temperature": 0})
        return r.text or ""


_REGISTRY = {"offline": OfflineClient, "anthropic": AnthropicClient,
             "openai": OpenAIClient, "gemini": GeminiClient}
_CLIENT: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    cls = _REGISTRY.get(config.LLM_PROVIDER)
    if cls is None:
        log.warning("unknown LLM_PROVIDER %r; using offline", config.LLM_PROVIDER)
        cls = OfflineClient
    try:
        _CLIENT = cls()
    except Exception as exc:
        log.warning("could not init %s (%s); using offline", config.LLM_PROVIDER, exc)
        _CLIENT = OfflineClient()
    return _CLIENT


def reset_llm() -> None:
    global _CLIENT
    _CLIENT = None
