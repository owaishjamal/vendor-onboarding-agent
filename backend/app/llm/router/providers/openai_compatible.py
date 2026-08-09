"""Groq and Cerebras: one adapter, two subclasses.

Both expose `/chat/completions` in OpenAI's shape, so the wire format is
shared. What is NOT shared is the small print, and the brief was right to warn
about it — each subclass below overrides only where its provider actually
differs, so the divergences are visible instead of buried in conditionals.
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncIterator, Optional

import httpx

from backend.app.llm.router.model_registry import ModelSpec
from backend.app.llm.router.providers.base import BaseLLMProvider, http_client
from backend.app.llm.router.retry import classify, parse_retry_after
from backend.app.llm.router.schemas import (
    LLMResponse, Message, RateLimitError, ToolCall, ToolSpec, Usage,
)

log = logging.getLogger("vo.llm.provider.openai")


class OpenAICompatibleProvider(BaseLLMProvider):
    name = "openai_compatible"

    # -- request ------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _payload(self, spec: ModelSpec, messages: list[Message],
                 tools: Optional[list[ToolSpec]], max_tokens: int,
                 temperature: float, stream: bool) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": spec.name,
            "messages": [self._message(m) for m in messages],
            "max_tokens": min(max_tokens, spec.max_output_tokens),
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            body["tools"] = [t.as_openai() for t in tools]
            body["tool_choice"] = "auto"
        return body

    @staticmethod
    def _message(m: Message) -> dict[str, Any]:
        if m.role == "tool":
            return {"role": "tool", "tool_call_id": m.tool_call_id,
                    "content": m.content}
        out: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_calls:
            out["tool_calls"] = m.tool_calls
            # An assistant turn that only calls tools has no prose. Some
            # providers reject `"content": ""` on such a turn, others reject
            # null — None is the documented shape and the more widely accepted.
            if not m.content:
                out["content"] = None
        return out

    # -- generate -----------------------------------------------------------

    async def generate(self, *, spec: ModelSpec, messages: list[Message],
                       tools: Optional[list[ToolSpec]] = None,
                       max_tokens: int = 1024, temperature: float = 0.0,
                       timeout: float = 60.0) -> LLMResponse:
        body = self._payload(spec, messages, tools, max_tokens, temperature, False)
        try:
            resp = await http_client().post(
                f"{self.base_url}/chat/completions",
                headers=self._headers(), json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise classify(exc, provider=self.name, model=spec.name) from exc

        if resp.status_code >= 400:
            raise self._error(resp, spec)

        return self._parse(resp.json(), spec)

    def _error(self, resp: httpx.Response, spec: ModelSpec) -> Exception:
        text = resp.text[:600]
        if resp.status_code == 429:
            # The header is authoritative and cheap; the body is the fallback
            # for gateways that describe the wait in prose instead.
            hdr = resp.headers.get("retry-after")
            wait = None
            if hdr:
                try:
                    wait = float(hdr)
                except ValueError:
                    wait = None
            return RateLimitError(
                f"{self.name} rate limited: {text}", status_code=429,
                retry_after=wait if wait is not None else parse_retry_after(text),
                provider=self.name, model=spec.name)
        return classify(RuntimeError(f"HTTP {resp.status_code}"),
                        status_code=resp.status_code, body=text,
                        provider=self.name, model=spec.name)

    def _parse(self, data: dict, spec: ModelSpec) -> LLMResponse:
        choices = data.get("choices") or []
        if not choices:
            # 200 with no choices happens on content filtering at some
            # gateways. Raising beats returning an empty string that the
            # caller then renders as a blank answer.
            raise classify(RuntimeError("no choices in response"),
                           body=json.dumps(data)[:300],
                           provider=self.name, model=spec.name)

        choice = choices[0]
        msg = choice.get("message") or {}
        calls = [
            ToolCall(id=c.get("id") or f"call_{i}",
                     name=(c.get("function") or {}).get("name", ""),
                     arguments=_json_or_empty((c.get("function") or {}).get("arguments")))
            for i, c in enumerate(msg.get("tool_calls") or [])
        ]
        u = data.get("usage") or {}
        return LLMResponse(
            text=(msg.get("content") or "").strip(),
            tool_calls=calls,
            provider=self.name, model=spec.name,
            usage=Usage(input_tokens=int(u.get("prompt_tokens") or 0),
                        output_tokens=int(u.get("completion_tokens") or 0)),
            finish_reason=choice.get("finish_reason"),
        )

    # -- stream -------------------------------------------------------------

    async def stream(self, *, spec: ModelSpec, messages: list[Message],
                     tools: Optional[list[ToolSpec]] = None,
                     max_tokens: int = 1024, temperature: float = 0.0,
                     timeout: float = 60.0) -> AsyncIterator[str]:
        body = self._payload(spec, messages, tools, max_tokens, temperature, True)
        async with http_client().stream(
                "POST", f"{self.base_url}/chat/completions",
                headers=self._headers(), json=body, timeout=timeout) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise self._error(resp, spec)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    delta = ((json.loads(chunk).get("choices") or [{}])[0]
                             .get("delta") or {})
                except json.JSONDecodeError:
                    continue          # keep-alive or partial frame
                if piece := delta.get("content"):
                    yield piece


def _json_or_empty(raw: Any) -> dict:
    """Tool arguments arrive as a JSON *string*, and models do emit invalid ones.

    A malformed argument blob should surface as a tool that fails validation,
    not as a JSONDecodeError from inside the router.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {"value": parsed}
    except (json.JSONDecodeError, TypeError):
        log.warning("model emitted unparseable tool arguments: %r", str(raw)[:200])
        return {}


class GroqProvider(OpenAICompatibleProvider):
    """Groq. The closest of the three to plain OpenAI."""

    name = "groq"

    def _payload(self, spec, messages, tools, max_tokens, temperature, stream):
        body = super()._payload(spec, messages, tools, max_tokens, temperature, stream)
        # Groq documents `max_completion_tokens` and treats `max_tokens` as
        # deprecated. Sending both is accepted and keeps this working across
        # the deprecation.
        body["max_completion_tokens"] = body["max_tokens"]
        return body


class CerebrasProvider(OpenAICompatibleProvider):
    """Cerebras. OpenAI-shaped with two differences that matter."""

    name = "cerebras"

    def _payload(self, spec, messages, tools, max_tokens, temperature, stream):
        body = super()._payload(spec, messages, tools, max_tokens, temperature, stream)
        # Cerebras rejects `tool_choice: "auto"` on some models with a 400
        # rather than ignoring it. Tools alone imply auto selection, so the
        # hint is redundant — dropping it costs nothing and avoids the 400.
        body.pop("tool_choice", None)
        # The free tier caps context at 8192 across models, and an over-large
        # max_tokens is refused outright instead of being clamped.
        body["max_tokens"] = min(body["max_tokens"], spec.max_output_tokens)
        return body
