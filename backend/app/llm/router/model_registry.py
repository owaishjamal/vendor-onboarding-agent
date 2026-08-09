"""Load models.yaml, validate it, and answer "what could serve this request?"

The registry is the only place that knows model names. Everything downstream —
scoring, rate limiting, health, the adapters — works with ModelSpec objects, so
renaming a model or retuning a limit is a YAML edit.

A provider with no API key in the environment is dropped at load, not at call
time. Discovering a missing key on the third fallback, mid-request, produces a
confusing error; discovering it at startup produces a log line saying exactly
which key is absent.
"""

from __future__ import annotations

import logging
import os
import pathlib
from typing import Any, Iterable, Optional

import yaml
from pydantic import BaseModel, Field, field_validator

from backend.app.llm.router.schemas import Capability, TaskType

log = logging.getLogger("vo.llm.registry")

DEFAULT_CONFIG = pathlib.Path(__file__).with_name("models.yaml")


class Defaults(BaseModel):
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    max_backoff_seconds: float = 8.0
    max_retries: int = 2
    request_timeout_seconds: float = 60.0


class ModelSpec(BaseModel):
    """One model on one provider — the unit of routing, limiting and health."""

    provider: str
    name: str
    priority: int = 5
    context_window: int = 8192
    max_output_tokens: int = 4096
    rpm: int = 30
    tpm: int = 6000
    capabilities: set[Capability] = Field(default_factory=set)
    cost_per_1m_input: float = 0.0
    cost_per_1m_output: float = 0.0
    enabled: bool = True

    # Provider-level, copied down so a candidate is self-contained and the
    # scorer never has to hold two objects to answer one question.
    base_url: str = ""
    adapter: str = "openai_compatible"
    api_key_env: str = ""

    @field_validator("capabilities", mode="before")
    @classmethod
    def _coerce(cls, v: Any) -> set[Capability]:
        """Unknown capability strings are dropped, not fatal.

        models.yaml is edited by operators under time pressure, often to add a
        model in a hurry. A typo in one tag should cost that tag, not the
        entire registry — the model simply becomes ineligible for tasks
        requiring what was misspelled, which is visible and recoverable.
        """
        out: set[Capability] = set()
        for item in v or []:
            if isinstance(item, Capability):
                out.add(item)
                continue
            try:
                out.add(Capability(str(item)))
            except ValueError:
                log.debug("ignoring unknown capability %r", item)
        return out

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.name}"

    def supports(self, caps: Iterable[Capability]) -> bool:
        return set(caps).issubset(self.capabilities)

    def cost_for(self, input_tokens: int, output_tokens: int) -> float:
        return round(
            input_tokens / 1_000_000 * self.cost_per_1m_input
            + output_tokens / 1_000_000 * self.cost_per_1m_output, 8)


class ModelRegistry:
    def __init__(self, config_path: Optional[pathlib.Path] = None,
                 *, require_keys: bool = True):
        self.path = pathlib.Path(
            os.getenv("LLM_ROUTER_MODELS") or config_path or DEFAULT_CONFIG)
        raw = yaml.safe_load(self.path.read_text()) or {}

        self.defaults = Defaults(**(raw.get("defaults") or {}))
        self._task_caps = self._load_task_capabilities(raw.get("task_capabilities") or {})
        self.models: dict[str, ModelSpec] = {}
        self.providers: dict[str, dict[str, Any]] = {}
        self.skipped: dict[str, str] = {}      # provider -> why, for diagnostics

        for pname, pconf in (raw.get("providers") or {}).items():
            pconf = pconf or {}
            if not pconf.get("enabled", True):
                self.skipped[pname] = "disabled in configuration"
                continue

            key_env = pconf.get("api_key_env", "")
            if require_keys and key_env and not os.getenv(key_env):
                # Not an error: running with only one of three keys is the
                # normal case in development, and the router is designed to
                # work with whatever is present.
                self.skipped[pname] = f"{key_env} not set"
                continue

            self.providers[pname] = pconf
            for mname, mconf in (pconf.get("models") or {}).items():
                mconf = mconf or {}
                if not mconf.get("enabled", True):
                    continue
                spec = ModelSpec(
                    provider=pname, name=mname,
                    base_url=pconf.get("base_url", ""),
                    adapter=pconf.get("adapter", "openai_compatible"),
                    api_key_env=key_env,
                    **{k: v for k, v in mconf.items() if k != "enabled"})
                self.models[spec.key] = spec

        log.info("model registry: %d model(s) across %d provider(s)%s",
                 len(self.models), len(self.providers),
                 f"; skipped {self.skipped}" if self.skipped else "")

    # -- task types ---------------------------------------------------------

    @staticmethod
    def _load_task_capabilities(raw: dict) -> dict[str, set[Capability]]:
        out: dict[str, set[Capability]] = {}
        for task, caps in raw.items():
            resolved: set[Capability] = set()
            for c in caps or []:
                try:
                    resolved.add(Capability(str(c)))
                except ValueError:
                    log.warning("task %r requires unknown capability %r — ignoring",
                                task, c)
            out[str(task)] = resolved
        return out

    def capabilities_for(self, task: TaskType | str) -> set[Capability]:
        return set(self._task_caps.get(
            task.value if isinstance(task, TaskType) else str(task), set()))

    # -- lookup -------------------------------------------------------------

    def get(self, key: str) -> Optional[ModelSpec]:
        return self.models.get(key)

    def all(self) -> list[ModelSpec]:
        return list(self.models.values())

    def candidates(self, *, capabilities: Iterable[Capability] = (),
                   min_context: int = 0,
                   needs_tools: bool = False) -> list[ModelSpec]:
        """Every model that COULD serve this, ignoring live state.

        Deliberately separate from scoring: this answers "is it capable?",
        scoring answers "is it available and best?". Keeping them apart is what
        makes "no model has vision" distinguishable from "the vision model is
        rate-limited" — two situations that need very different responses.
        """
        required = set(capabilities)
        if needs_tools:
            required.add(Capability.TOOL_CALLING)
        out = [m for m in self.models.values()
               if m.supports(required) and m.context_window >= min_context]
        return sorted(out, key=lambda m: (m.priority, m.cost_per_1m_output))


_REGISTRY: Optional[ModelRegistry] = None


def get_registry(*, refresh: bool = False) -> ModelRegistry:
    global _REGISTRY
    if _REGISTRY is None or refresh:
        _REGISTRY = ModelRegistry()
    return _REGISTRY


def reset_registry() -> None:
    """Drop the cached registry. Used by tests that change the environment."""
    global _REGISTRY
    _REGISTRY = None
