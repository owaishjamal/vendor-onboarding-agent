"""The application's view of the LLM: three methods, no providers.

    get_llm().draft_vendor_email(payload)   -> (text, cached)
    get_llm().reviewer_summary(payload)     -> (text, cached)
    get_llm().ops_chat(payload, messages)   -> {reply, source, grounded_in}

Everything below the surface is the router (`backend/app/llm/router/`), which
picks a provider and model per request from live rate-limit and health state
and fails over when one declines. The pipeline calls these three methods and
learns nothing about Groq, Cerebras or Gemini — which is deliberate, because
the answer to "which provider?" now changes between two consecutive calls.

WHAT SURVIVED THE REWRITE, AND WHY
    Caching, the offline path and the grounded-copilot ordering are unchanged.
    They are not provider concerns: caching is keyed on the prompt, the offline
    templates are what make the demo reproducible without a key, and answering
    a reviewer from the case record rather than a model is a correctness
    decision that would be wrong to delegate to any provider.

WHY THIS LAYER IS SYNCHRONOUS
    The router is async; the pipeline that calls this is a synchronous
    generator streaming SSE. Rather than make the whole pipeline async for two
    calls per case, the bridge lives here in one place — see `_run`.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import hashlib
import json
import logging
import threading
from typing import Any, Optional

from backend.app import config
from backend.app.llm import offline, ops_copilot
from backend.app.llm.prompts import (
    OPS_CHAT_SYSTEM, REVIEWER_SUMMARY_PROMPT_VERSION, REVIEWER_SUMMARY_SYSTEM,
    VENDOR_EMAIL_PROMPT_VERSION, VENDOR_EMAIL_SYSTEM,
)
from backend.app.llm.router import LLMRouter, TaskType

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


# ---------------------------------------------------------------------------
# Sync/async bridge
# ---------------------------------------------------------------------------

_LOOP: Optional[asyncio.AbstractEventLoop] = None
_LOOP_LOCK = threading.Lock()


def _background_loop() -> asyncio.AbstractEventLoop:
    """One event loop on one daemon thread, for the whole process.

    `asyncio.run` per call would build and tear down a loop — and, worse, a new
    httpx connection pool — for every LLM call, paying a TLS handshake each
    time against providers whose selling point is latency. One long-lived loop
    keeps connections warm and keeps the router's in-memory rate-limit windows
    coherent, which they would not be across throwaway loops.
    """
    global _LOOP
    with _LOOP_LOCK:
        if _LOOP is None or _LOOP.is_closed():
            _LOOP = asyncio.new_event_loop()
            threading.Thread(target=_LOOP.run_forever, daemon=True,
                             name="llm-router-loop").start()
        return _LOOP


def _run(coro, timeout: float = 120.0):
    """Run a coroutine from synchronous code, wherever that code is called from.

    Two cases, and getting them confused is a deadlock:

      * No running loop (the pipeline thread, a script, a test) — submit to
        the background loop and block.
      * Inside a running loop (a FastAPI async endpoint) — we must NOT block
        the loop we are on, so hand the work to the background loop and wait
        on the other side.

    Both paths land on the same background loop, so there is exactly one
    connection pool and one set of rate-limit windows.
    """
    loop = _background_loop()
    fut = asyncio.run_coroutine_threadsafe(coro, loop)
    try:
        return fut.result(timeout=timeout)
    except concurrent.futures.TimeoutError:
        fut.cancel()
        raise TimeoutError(f"LLM call exceeded {timeout}s")


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------

class LLMClient:
    """Offline base. Every method here is the deterministic path."""

    provider = "offline"

    @property
    def routes_via(self) -> str:
        return "offline templates"

    def _complete(self, system: str, user: str, max_tokens: int,
                  task_type: TaskType) -> str:
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
                text = self._complete(VENDOR_EMAIL_SYSTEM, blob, 600,
                                      TaskType.VENDOR_EMAIL).strip()
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
                text = self._complete(REVIEWER_SUMMARY_SYSTEM, blob, 500,
                                      TaskType.REVIEWER_SUMMARY).strip()
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
          2. A model, when configured, for anything needing interpretation. It
             receives only the trimmed case context.
          3. An honest refusal listing what CAN be answered.

        Returns {"reply", "grounded_in", "source"} so the UI can show whether
        an answer came from the record or from a model — and, now, WHICH model,
        since that is no longer fixed.
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
            reply = self._complete(OPS_CHAT_SYSTEM, user_prompt, 4096,
                                   TaskType.OPS_CHAT).strip()
            return {"reply": reply, "source": self._last_route or self.provider,
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

    _last_route: str = ""


class OfflineClient(LLMClient):
    provider = "offline"


class RoutedClient(LLMClient):
    """Everything non-offline. One class, because the router handles the rest.

    This replaces the previous per-provider subclasses (Anthropic, OpenAI,
    Gemini). There is nothing left for them to differ on: choosing a provider,
    retrying, backing off and failing over all happen below this line.
    """

    provider = "router"

    def __init__(self) -> None:
        self._router = LLMRouter(
            redis_url=config.LLM_ROUTER_REDIS_URL or None,
            # The pipeline must always produce a case, even with every
            # provider exhausted — the callers above fall back to templates on
            # exception, and this makes that path reachable rather than fatal.
            allow_offline_fallback=False,
        )
        if not self._router.registry.all():
            raise RuntimeError(
                f"no usable models: {self._router.registry.skipped or 'registry empty'}")

    @property
    def routes_via(self) -> str:
        names = self._router.providers.names()
        return f"router over {', '.join(names)}" if names else "router (no providers)"

    @property
    def router(self) -> LLMRouter:
        """Exposed for /health and diagnostics, not for making calls."""
        return self._router

    def _complete(self, system: str, user: str, max_tokens: int,
                  task_type: TaskType) -> str:
        r = _run(self._router.generate(
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            task_type=task_type, max_tokens=max_tokens,
            # The router filters on context window, so a large case record
            # routes away from short-context models automatically instead of
            # being truncated by one.
            min_context=_estimate_context(system, user, max_tokens),
        ))
        # Recorded so ops_chat can report which model actually answered — with
        # failover, "gemini" would often be a lie.
        self._last_route = r.routed_to
        return r.text


def _estimate_context(system: str, user: str, max_tokens: int) -> int:
    """Rough prompt+completion size, so short-context models are excluded.

    Same 3.6 chars/token bias as the rate limiter, with 15% headroom: the cost
    of over-estimating is skipping a model that would have fit, the cost of
    under-estimating is a truncated compliance summary.
    """
    chars = len(system) + len(user)
    return int((chars / 3.6 + max_tokens) * 1.15)


_CLIENT: Optional[LLMClient] = None


def get_llm() -> LLMClient:
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    if config.LLM_PROVIDER == "offline":
        _CLIENT = OfflineClient()
        return _CLIENT
    try:
        _CLIENT = RoutedClient()
    except Exception as exc:
        log.warning("could not initialise the LLM router (%s); using offline", exc)
        _CLIENT = OfflineClient()
    return _CLIENT


def reset_llm() -> None:
    global _CLIENT
    _CLIENT = None
