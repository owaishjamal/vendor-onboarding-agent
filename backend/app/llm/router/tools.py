"""Tool calling: registry, executor, and the call loop.

    model ──► tool_call ──► ToolExecutor ──► result ──► model ──► answer

The loop lives here rather than in the router because it is a different
concern: the router routes ONE exchange, and a tool conversation is several
of them. Keeping them apart means each hop through the loop is independently
routed — if Groq rate-limits after the first tool call, the second hop
transparently continues on Cerebras.

FOUR THINGS THE EXECUTOR REFUSES TO ASSUME

  * that the model called a tool that exists — it hallucinates names
  * that the arguments match the schema — they often do not
  * that a tool will not raise — they call networks and databases
  * that the loop will terminate — a model can call the same tool forever

Each of those returns a structured error TO THE MODEL rather than raising,
because a model told "that tool does not exist, here are the ones that do"
usually recovers, whereas an exception ends the conversation.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional, Sequence

from backend.app.llm.router.schemas import (
    LLMResponse, Message, TaskType, ToolCall, ToolSpec,
)

log = logging.getLogger("vo.llm.tools")


@dataclass
class ToolResult:
    tool_call_id: str
    name: str
    content: str
    ok: bool = True
    latency_ms: int = 0


class Tool:
    """A callable plus the schema the model is shown."""

    def __init__(self, name: str, description: str,
                 parameters: dict[str, Any],
                 fn: Callable[..., Any | Awaitable[Any]]):
        self.spec = ToolSpec(name=name, description=description,
                             parameters=parameters)
        self.fn = fn

    async def __call__(self, **kwargs: Any) -> Any:
        if inspect.iscoroutinefunction(self.fn):
            return await self.fn(**kwargs)
        # Sync tools run in a thread so a blocking DB or HTTP call cannot
        # stall the event loop the router is sharing with everything else.
        return await asyncio.to_thread(lambda: self.fn(**kwargs))


class ToolExecutor:
    """Holds the tools and runs them. Generic — knows nothing about the app."""

    def __init__(self, tools: Sequence[Tool] = ()):
        self._tools: dict[str, Tool] = {t.spec.name: t for t in tools}

    def register(self, tool: Tool) -> None:
        self._tools[tool.spec.name] = tool

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self._tools.values()]

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    async def execute(self, call: ToolCall) -> ToolResult:
        started = time.monotonic()
        tool = self._tools.get(call.name)

        if tool is None:
            # Hallucinated tool name. Listing the real ones turns a dead end
            # into a correction the model can act on.
            return ToolResult(
                call.id, call.name,
                json.dumps({"error": f"No tool named '{call.name}'.",
                            "available_tools": self.names}),
                ok=False)

        try:
            result = await tool(**call.arguments)
            payload = (result if isinstance(result, str)
                       else json.dumps(result, default=str))
            return ToolResult(call.id, call.name, payload, ok=True,
                              latency_ms=int((time.monotonic() - started) * 1000))
        except TypeError as exc:
            # Wrong or missing arguments. The schema goes back with the error
            # so the model can see what it should have sent.
            return ToolResult(
                call.id, call.name,
                json.dumps({"error": f"Invalid arguments: {exc}",
                            "expected_schema": tool.spec.parameters}),
                ok=False, latency_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:
            log.warning("tool %s failed: %s", call.name, exc)
            return ToolResult(
                call.id, call.name,
                json.dumps({"error": f"{type(exc).__name__}: {exc}"}),
                ok=False, latency_ms=int((time.monotonic() - started) * 1000))

    async def execute_all(self, calls: Sequence[ToolCall]) -> list[ToolResult]:
        """Parallel: a model asking for three lookups should not wait 3x."""
        if not calls:
            return []
        if len(calls) == 1:
            return [await self.execute(calls[0])]
        return list(await asyncio.gather(*(self.execute(c) for c in calls)))


async def run_tool_loop(router, messages: list[Message], executor: ToolExecutor,
                        *, task_type: TaskType | str = TaskType.AGENTIC,
                        max_iterations: int = 6,
                        max_tokens: int = 2048,
                        **kw) -> LLMResponse:
    """Drive model → tools → model until it answers in prose.

    `max_iterations` is a hard stop, not a suggestion. A model that keeps
    calling the same tool will otherwise run until the quota is gone; six is
    generous for real work and cheap when something goes wrong.

    Every iteration is a fresh router.generate, so provider selection is
    re-evaluated at each hop.
    """
    convo = list(messages)
    specs = executor.specs()
    total_in = total_out = 0
    cost = 0.0
    used: list[str] = []

    for iteration in range(max_iterations):
        r = await router.generate(convo, task_type=task_type, tools=specs,
                                  max_tokens=max_tokens, **kw)
        total_in += r.usage.input_tokens
        total_out += r.usage.output_tokens
        cost += r.estimated_cost_usd

        if not r.tool_calls:
            r.usage.input_tokens, r.usage.output_tokens = total_in, total_out
            r.estimated_cost_usd = cost
            if used:
                r.finish_reason = f"answered after tools: {', '.join(used)}"
            return r

        convo.append(Message(
            role="assistant", content=r.text,
            tool_calls=[{"id": c.id, "type": "function",
                         "function": {"name": c.name,
                                      "arguments": json.dumps(c.arguments)}}
                        for c in r.tool_calls]))

        for res in await executor.execute_all(r.tool_calls):
            used.append(res.name)
            convo.append(Message(role="tool", content=res.content,
                                 tool_call_id=res.tool_call_id, name=res.name))

    # Out of iterations. Ask once more with tools withheld, so the model has
    # to answer from what it already gathered instead of calling again.
    log.warning("tool loop hit %d iterations; forcing a final answer",
                max_iterations)
    final = await router.generate(
        convo + [Message(role="user",
                         content="Answer now using the information gathered. "
                                 "Do not call any more tools.")],
        task_type=task_type, max_tokens=max_tokens, **kw)
    final.usage.input_tokens = total_in + final.usage.input_tokens
    final.usage.output_tokens = total_out + final.usage.output_tokens
    final.estimated_cost_usd = cost + final.estimated_cost_usd
    final.finish_reason = "tool_iteration_limit"
    return final
