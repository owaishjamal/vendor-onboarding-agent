"""Google Gemini — native v1beta REST, not OpenAI-shaped.

Gemini differs from the other two in every part of the request: the system
prompt is a sibling field rather than a message, roles are `user`/`model`,
tools are `functionDeclarations`, and generation settings live under
`generationConfig`. That is why it gets its own adapter instead of a flag on
the OpenAI one.

Three behaviours here were learned the hard way against the live API and are
preserved from the client this replaces:

  1. THINKING TOKENS BILL AGAINST maxOutputTokens. On a thinking model, the
     reasoning consumes the budget and the answer arrives truncated — or the
     response contains only a thought part and no answer at all. Setting
     thinkingBudget to 0 fixes it where supported.

  2. NOT EVERY MODEL ACCEPTS thinkingConfig. Those that don't answer 400
     INVALID_ARGUMENT without naming the offending field, and moving aliases
     like `gemini-flash-latest` can point at either kind. Probed once, then
     remembered per model, so the cost is one extra round trip per process.

  3. A 200 DOES NOT GUARANTEE TEXT. Safety blocks, token exhaustion and
     thought-only responses all return success with no answer. Indexing
     straight into parts[0].text raises a bare KeyError; a reviewer asking
     "why was this vendor flagged?" deserves a sentence, not a stack trace.
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
    LLMResponse, Message, PermanentError, RateLimitError, ToolCall, ToolSpec,
    TransientError, Usage,
)

log = logging.getLogger("vo.llm.provider.gemini")


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    # Per-model, per-process. Class-level so every instance shares what one
    # of them learned.
    _thinking_ok: dict[str, bool] = {}

    def _url(self, spec: ModelSpec, method: str) -> str:
        return f"{self.base_url}/models/{spec.name}:{method}"

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "X-goog-api-key": self.api_key}

    # -- translation --------------------------------------------------------

    @staticmethod
    def _split(messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """Gemini takes the system prompt separately and calls assistant 'model'."""
        system_parts: list[str] = []
        contents: list[dict[str, Any]] = []

        for m in messages:
            if m.role == "system":
                system_parts.append(m.content)
                continue

            if m.role == "tool":
                contents.append({"role": "user", "parts": [{
                    "functionResponse": {
                        "name": m.name or "tool",
                        "response": _as_object(m.content)}}]})
                continue

            if m.role == "assistant" and m.tool_calls:
                parts = [{"functionCall": {
                    "name": (c.get("function") or {}).get("name", ""),
                    "args": _as_object((c.get("function") or {}).get("arguments"))}}
                    for c in m.tool_calls]
                if m.content:
                    parts.insert(0, {"text": m.content})
                contents.append({"role": "model", "parts": parts})
                continue

            contents.append({
                "role": "model" if m.role == "assistant" else "user",
                "parts": [{"text": m.content}]})

        return "\n\n".join(system_parts), contents

    def _body(self, spec: ModelSpec, messages: list[Message],
              tools: Optional[list[ToolSpec]], max_tokens: int,
              temperature: float, *, thinking: bool) -> dict[str, Any]:
        system, contents = self._split(messages)

        # Floor of 1024 even when the caller asked for less: on a thinking
        # model that cannot take thinkingConfig, a tight ceiling is consumed
        # by reasoning and the answer never gets written.
        gen: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": min(max(max_tokens, 1024), spec.max_output_tokens),
        }
        if thinking:
            gen["thinkingConfig"] = {"thinkingBudget": 0}

        body: dict[str, Any] = {"contents": contents, "generationConfig": gen}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [{"function_declarations": [t.as_gemini() for t in tools]}]
        return body

    # -- generate -----------------------------------------------------------

    async def generate(self, *, spec: ModelSpec, messages: list[Message],
                       tools: Optional[list[ToolSpec]] = None,
                       max_tokens: int = 1024, temperature: float = 0.0,
                       timeout: float = 60.0) -> LLMResponse:
        use_thinking = self._thinking_ok.get(spec.name, True)

        try:
            return await self._post(spec, messages, tools, max_tokens,
                                    temperature, timeout, thinking=use_thinking)
        except PermanentError as exc:
            # Only one recoverable case: this model does not take
            # thinkingConfig. Anything else is a real 400 and must surface.
            if not use_thinking or "INVALID_ARGUMENT" not in str(exc).upper():
                raise
            log.info("%s rejects thinkingConfig; retrying without it", spec.name)
            self._thinking_ok[spec.name] = False
            return await self._post(spec, messages, tools, max_tokens,
                                    temperature, timeout, thinking=False)

    async def _post(self, spec: ModelSpec, messages, tools, max_tokens,
                    temperature, timeout, *, thinking: bool) -> LLMResponse:
        body = self._body(spec, messages, tools, max_tokens, temperature,
                          thinking=thinking)
        try:
            resp = await http_client().post(
                self._url(spec, "generateContent"),
                headers=self._headers(), json=body, timeout=timeout)
        except httpx.HTTPError as exc:
            raise classify(exc, provider=self.name, model=spec.name) from exc

        if resp.status_code >= 400:
            text = resp.text[:600]
            if resp.status_code == 429 or "RESOURCE_EXHAUSTED" in text:
                raise RateLimitError(
                    f"gemini rate limited: {text}", status_code=429,
                    # Gemini puts the wait in the body as `retryDelay: "7s"`,
                    # not in a header.
                    retry_after=parse_retry_after(text),
                    provider=self.name, model=spec.name)
            raise classify(RuntimeError(f"HTTP {resp.status_code}"),
                           status_code=resp.status_code, body=text,
                           provider=self.name, model=spec.name)

        return self._parse(resp.json(), spec)

    def _parse(self, data: dict, spec: ModelSpec) -> LLMResponse:
        candidates = data.get("candidates") or []
        if not candidates:
            block = (data.get("promptFeedback") or {}).get("blockReason")
            if block:
                raise PermanentError(
                    f"Gemini blocked the prompt ({block}).",
                    provider=self.name, model=spec.name)
            raise TransientError("Gemini returned no candidates.",
                                 provider=self.name, model=spec.name)

        cand = candidates[0]
        parts = ((cand.get("content") or {}).get("parts")) or []

        calls: list[ToolCall] = []
        chunks: list[str] = []
        for i, p in enumerate(parts):
            if not isinstance(p, dict):
                continue
            if fc := p.get("functionCall"):
                calls.append(ToolCall(id=f"call_{i}", name=fc.get("name", ""),
                                      arguments=fc.get("args") or {}))
            # Skip thought parts: they are the model's scratchpad, and
            # returning them as the answer is how a reviewer ends up reading a
            # plan for a reply instead of the reply.
            elif p.get("text") and not p.get("thought"):
                chunks.append(str(p["text"]))

        text = "\n".join(chunks).strip()
        finish = cand.get("finishReason", "")

        if not text and not calls:
            if finish == "MAX_TOKENS":
                raise TransientError(
                    "Gemini hit the output limit before writing an answer.",
                    provider=self.name, model=spec.name)
            if finish == "SAFETY":
                raise PermanentError("Gemini blocked the response on safety grounds.",
                                     provider=self.name, model=spec.name)
            raise TransientError(
                f"Gemini returned an empty response (finishReason={finish or 'unknown'}).",
                provider=self.name, model=spec.name)

        u = data.get("usageMetadata") or {}
        return LLMResponse(
            text=text, tool_calls=calls, provider=self.name, model=spec.name,
            usage=Usage(input_tokens=int(u.get("promptTokenCount") or 0),
                        output_tokens=int(u.get("candidatesTokenCount") or 0)),
            finish_reason=finish or None)

    # -- stream -------------------------------------------------------------

    async def stream(self, *, spec: ModelSpec, messages: list[Message],
                     tools: Optional[list[ToolSpec]] = None,
                     max_tokens: int = 1024, temperature: float = 0.0,
                     timeout: float = 60.0) -> AsyncIterator[str]:
        body = self._body(spec, messages, tools, max_tokens, temperature,
                          thinking=self._thinking_ok.get(spec.name, True))
        url = self._url(spec, "streamGenerateContent") + "?alt=sse"
        async with http_client().stream(
                "POST", url, headers=self._headers(), json=body,
                timeout=timeout) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise classify(RuntimeError(f"HTTP {resp.status_code}"),
                               status_code=resp.status_code, body=resp.text[:400],
                               provider=self.name, model=spec.name)
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    obj = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                for cand in obj.get("candidates") or []:
                    for p in ((cand.get("content") or {}).get("parts")) or []:
                        if isinstance(p, dict) and p.get("text") and not p.get("thought"):
                            yield str(p["text"])


def _as_object(raw: Any) -> dict:
    """Gemini requires functionResponse payloads to be objects, not scalars."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {"result": parsed}
        except json.JSONDecodeError:
            return {"result": raw}
    return {"result": raw}
