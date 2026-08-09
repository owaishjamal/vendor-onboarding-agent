"""Supervisor / specialist / synthesizer, over the same router.

                        USER
                          │
                     SUPERVISOR            classify the request
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
    RESEARCH           CODING            GENERAL       specialists
        │                 │                 │
    web tools         code tools         db tools
        └─────────────────┼─────────────────┘
                          ▼
                     SYNTHESIZER          one answer
                          │
                        USER

EVERY NODE CALLS THE SAME ROUTER, and none of them names a provider. The
classifier asks for `classification` and gets a cheap fast model; the research
agent asks for `research` and gets a reasoning model with tools. That is the
router's value made concrete: routing policy is one YAML file, not a decision
scattered across four agents.

ON LANGGRAPH
    The brief asked for LangGraph compatibility. This builds a real
    StateGraph when langgraph is installed, and runs the identical node
    functions on a small built-in executor when it is not.

    That is not hedging. The nodes are plain `async (state) -> state`
    functions with no framework types in their signatures, which is what makes
    both runners possible — and it means the agent logic is unit-testable
    without standing up a graph at all. Adding a heavyweight dependency to a
    deployment whose whole selling point is "one container, one URL" needed to
    be optional.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional, TypedDict

from backend.app.llm.router.schemas import Message, TaskType
from backend.app.llm.router.tools import ToolExecutor, run_tool_loop

log = logging.getLogger("vo.llm.agents")

try:                                                   # pragma: no cover
    from langgraph.graph import END, StateGraph
    HAS_LANGGRAPH = True
except ImportError:                                    # pragma: no cover
    HAS_LANGGRAPH = False


class AgentState(TypedDict, total=False):
    question: str
    route: str
    findings: list[dict[str, Any]]
    answer: str
    trace: list[str]
    error: str


ROUTES = ("research", "coding", "general")

SUPERVISOR_SYSTEM = (
    "You route a question to exactly one specialist.\n"
    "  research — needs external facts, current information, or lookups\n"
    "  coding   — writing, reviewing, debugging or explaining code\n"
    "  general  — everything else\n"
    "Reply with one word: research, coding, or general. Nothing else."
)

SYNTHESIZER_SYSTEM = (
    "You write the final answer from a specialist's findings.\n"
    "Use only what the findings contain. If they do not answer the question, "
    "say so plainly rather than filling the gap from memory. Be concise."
)


class AgentGraph:
    """The graph, runnable with or without LangGraph."""

    def __init__(self, router, *, tools: Optional[dict[str, ToolExecutor]] = None):
        self.router = router
        self.tools = tools or {}

    # -- nodes: plain async functions, no framework types -------------------

    async def supervisor(self, state: AgentState) -> AgentState:
        """Classify. Deliberately the cheapest call in the graph.

        `classification` routes to a small fast model — spending a large
        reasoning model on a one-word answer is exactly the waste the task-type
        system exists to prevent.
        """
        r = await self.router.generate(
            [Message(role="system", content=SUPERVISOR_SYSTEM),
             Message(role="user", content=state["question"])],
            task_type=TaskType.CLASSIFICATION, max_tokens=8)

        word = (r.text or "").strip().lower().split()[:1]
        route = word[0].strip(".,!") if word else "general"
        if route not in ROUTES:
            # A classifier that returns something unexpected must not stop the
            # request; general handles anything.
            log.debug("supervisor returned %r; defaulting to general", r.text[:60])
            route = "general"

        return {**state, "route": route,
                "trace": [*state.get("trace", []),
                          f"supervisor -> {route} ({r.routed_to})"]}

    async def research(self, state: AgentState) -> AgentState:
        return await self._specialist(state, "research", TaskType.RESEARCH,
                                      "You research thoroughly and cite what "
                                      "you found. Use the tools available.")

    async def coding(self, state: AgentState) -> AgentState:
        return await self._specialist(state, "coding", TaskType.CODING,
                                      "You are a careful engineer. Prefer "
                                      "working code and state your assumptions.")

    async def general(self, state: AgentState) -> AgentState:
        return await self._specialist(state, "general", TaskType.GENERAL,
                                      "Answer clearly and concisely.")

    async def _specialist(self, state: AgentState, name: str,
                          task_type: TaskType, system: str) -> AgentState:
        msgs = [Message(role="system", content=system),
                Message(role="user", content=state["question"])]
        executor = self.tools.get(name)

        try:
            if executor and executor.names:
                # Tools present: the loop re-routes on every hop, so a rate
                # limit between tool calls is invisible to the agent.
                r = await run_tool_loop(self.router, msgs, executor,
                                        task_type=TaskType.AGENTIC)
            else:
                r = await self.router.generate(msgs, task_type=task_type,
                                               max_tokens=2048)
        except Exception as exc:
            log.warning("%s agent failed: %s", name, exc)
            return {**state, "error": f"{type(exc).__name__}: {exc}",
                    "trace": [*state.get("trace", []), f"{name} agent failed"]}

        return {
            **state,
            "findings": [*state.get("findings", []),
                         {"agent": name, "content": r.text,
                          "model": r.routed_to, "cost_usd": r.estimated_cost_usd}],
            "trace": [*state.get("trace", []), f"{name} agent ({r.routed_to})"],
        }

    async def synthesizer(self, state: AgentState) -> AgentState:
        findings = state.get("findings") or []
        if not findings:
            return {**state,
                    "answer": state.get("error")
                    or "No specialist produced a finding.",
                    "trace": [*state.get("trace", []), "synthesizer skipped"]}

        # One specialist, no error: nothing to synthesise. Paraphrasing a
        # single good answer costs a call and can only lose information.
        if len(findings) == 1 and not state.get("error"):
            return {**state, "answer": findings[0]["content"],
                    "trace": [*state.get("trace", []), "synthesizer passthrough"]}

        blob = "\n\n".join(f"[{f['agent']}]\n{f['content']}" for f in findings)
        r = await self.router.generate(
            [Message(role="system", content=SYNTHESIZER_SYSTEM),
             Message(role="user",
                     content=f"QUESTION\n{state['question']}\n\nFINDINGS\n{blob}")],
            task_type=TaskType.REASONING, max_tokens=1500)
        return {**state, "answer": r.text,
                "trace": [*state.get("trace", []), f"synthesizer ({r.routed_to})"]}

    # -- runners ------------------------------------------------------------

    def build_langgraph(self):
        """A real StateGraph. Same node functions as the fallback runner."""
        if not HAS_LANGGRAPH:
            raise RuntimeError("langgraph is not installed")

        g = StateGraph(AgentState)
        g.add_node("supervisor", self.supervisor)
        g.add_node("research", self.research)
        g.add_node("coding", self.coding)
        g.add_node("general", self.general)
        g.add_node("synthesizer", self.synthesizer)

        g.set_entry_point("supervisor")
        g.add_conditional_edges("supervisor", lambda s: s.get("route", "general"),
                                {"research": "research", "coding": "coding",
                                 "general": "general"})
        for node in ROUTES:
            g.add_edge(node, "synthesizer")
        g.add_edge("synthesizer", END)
        return g.compile()

    async def run(self, question: str) -> AgentState:
        """Execute the graph. Uses LangGraph when installed."""
        state: AgentState = {"question": question, "findings": [], "trace": []}

        if HAS_LANGGRAPH:
            try:
                return await self.build_langgraph().ainvoke(state)
            except Exception as exc:      # pragma: no cover
                log.warning("langgraph execution failed (%s); "
                            "running the same nodes directly", exc)

        state = await self.supervisor(state)
        node: Callable = {"research": self.research, "coding": self.coding,
                          "general": self.general}[state.get("route", "general")]
        state = await node(state)
        return await self.synthesizer(state)
