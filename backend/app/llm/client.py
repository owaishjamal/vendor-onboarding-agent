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
from backend.app.llm import offline, ops_copilot
from backend.app.llm.prompts import (
    REVIEWER_SUMMARY_PROMPT_VERSION, REVIEWER_SUMMARY_SYSTEM,
    VENDOR_EMAIL_PROMPT_VERSION, VENDOR_EMAIL_SYSTEM, OPS_CHAT_SYSTEM
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

    def ops_chat(self, payload: dict[str, Any],
                 messages: list[dict[str, str]]) -> dict[str, Any]:
        """Answer an ops question about ONE case, grounded in that case.

        Order of preference, and it matters:

          1. A deterministic lookup over the case record. Most of what a
             reviewer asks ("what's missing", "which checks failed") is a
             query, not a reasoning task — answering it from the data is both
             cheaper and impossible to hallucinate.
          2. A model, when configured, for anything that needs interpretation.
             It receives only the trimmed case context.
          3. An honest refusal listing what CAN be answered.

        Returns {"reply", "grounded_in", "source"} so the UI can show whether
        an answer came from the record or from a model.
        """
        question = ""
        for m in reversed(messages or []):
            if m.get("role") == "user":
                question = m.get("content", "")
                break

        grounded = ops_copilot.answer(payload, question)
        if grounded is not None:
            return {"reply": grounded, "source": "case-record",
                    "grounded_in": payload.get("case_id")}

        if self.provider == "offline":
            return {
                "reply": ops_copilot.NO_MODEL_FALLBACK.format(
                    status=str(payload.get("status", "flagged")).lower().replace("_", " ")),
                "source": "no-model",
                "grounded_in": payload.get("case_id"),
            }

        context = ops_copilot.context_for_model(payload)
        user_prompt = (f"CASE RECORD (the only source of truth):\n"
                       f"{json.dumps(context, default=str, indent=1)}\n\n"
                       f"CONVERSATION:\n")
        for m in messages:
            user_prompt += f"{m['role'].upper()}: {m['content']}\n\n"
        user_prompt += "ASSISTANT:"

        try:
            reply = self._complete(OPS_CHAT_SYSTEM, user_prompt, 4096).strip()
            return {"reply": reply, "source": self.provider,
                    "grounded_in": payload.get("case_id")}
        except Exception as exc:
            log.warning("ops chat failed (%s); declining rather than guessing", exc)
            # Do NOT reuse the offline text here: it claims no model is
            # configured, which contradicts the error above it and sends the
            # reader looking in the wrong place. Say what actually happened.
            return {
                "reply": (
                    f"The language model call failed, so I will not answer from "
                    f"memory.\n\n**{type(exc).__name__}:** {exc}\n\n"
                    + ops_copilot.grounded_menu(payload)),
                "source": "error",
                "grounded_in": payload.get("case_id"),
            }


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
    """Google Gemini via the v1beta REST API — no SDK needed (stdlib urllib).

    Matches the documented endpoint:
        POST .../models/{model}:generateContent
        header  X-goog-api-key: <key>
    Default model is gemini-flash-latest; override with LLM_MODEL.
    """

    provider = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self) -> None:
        if not config.GEMINI_API_KEY:
            raise RuntimeError("GEMINI_API_KEY is not set")
        self.api_key = config.GEMINI_API_KEY
        self.model = config.LLM_MODEL or "gemini-flash-latest"

    # Whether this model accepts generationConfig.thinkingConfig. Not every
    # model does, and the alias `gemini-flash-latest` can point at one that
    # doesn't — it answers 400 INVALID_ARGUMENT with no hint as to which
    # argument. Probed once per process, then remembered.
    _supports_thinking_config: bool = True

    def _complete(self, system: str, user: str, max_tokens: int) -> str:
        # Reasoning tokens, where a model has them, are billed against
        # maxOutputTokens — which is how an answer ends up truncated
        # mid-sentence, or replaced by the model's own plan for the answer.
        # Turning thinking off is the fix where it is supported; a generous
        # ceiling is the fallback where it is not.
        gen = {"temperature": 0, "maxOutputTokens": max(max_tokens, 1024)}

        if self._supports_thinking_config:
            try:
                return self._post({**gen, "thinkingConfig": {"thinkingBudget": 0}},
                                  system, user)
            except RuntimeError as exc:
                if "INVALID_ARGUMENT" not in str(exc):
                    raise
                # This model does not take thinkingConfig. Stop sending it —
                # otherwise every future call pays for two round trips.
                log.info("%s rejects thinkingConfig; retrying without it "
                         "(higher token ceiling covers the reasoning budget)",
                         self.model)
                type(self)._supports_thinking_config = False

        return self._post(gen, system, user)

    def _post(self, generation_config: dict, system: str, user: str) -> str:
        import urllib.error
        import urllib.request

        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": generation_config,
        }
        req = urllib.request.Request(
            f"{self.BASE}/{self.model}:generateContent",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "X-goog-api-key": self.api_key},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise RuntimeError(f"Gemini HTTP {e.code}: {detail}") from e

        return self._text_from(data)

    @staticmethod
    def _text_from(data: dict) -> str:
        """Pull the answer out of a response that may not contain one.

        The old one-liner indexed straight into parts[0].text, which raised a
        bare KeyError whenever the model returned no text — hitting the token
        ceiling, tripping a safety filter, or emitting only a thought part. A
        stack trace is a terrible answer to "why was this vendor flagged?", so
        every one of those cases now produces a sentence a reviewer can act on.
        """
        candidates = data.get("candidates") or []
        if not candidates:
            fb = (data.get("promptFeedback") or {}).get("blockReason")
            raise RuntimeError(f"Gemini returned no candidates"
                               + (f" (blocked: {fb})" if fb else ""))

        cand = candidates[0]
        parts = ((cand.get("content") or {}).get("parts")) or []
        # Skip thought parts; join everything else, so a multi-part answer is
        # not silently reduced to its first fragment.
        text = "\n".join(
            str(p["text"]) for p in parts
            if isinstance(p, dict) and p.get("text") and not p.get("thought")
        ).strip()

        if text:
            return text

        reason = cand.get("finishReason", "")
        if reason == "MAX_TOKENS":
            raise RuntimeError(
                "Gemini hit the output limit before writing an answer. "
                "Raise maxOutputTokens or shorten the case context.")
        if reason == "SAFETY":
            raise RuntimeError("Gemini blocked the response on safety grounds.")
        raise RuntimeError(f"Gemini returned an empty response (finishReason={reason or 'unknown'}).")


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
