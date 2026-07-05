"""Vendored from agent-loop's `tests/support/agent_factory.py` (Phase 9).
Lets adapter tests build a bare agent-loop `Agent` directly from a
`ScriptedTransport`, a `ToolRegistry`, and a `HookRegistry` — bypassing
`PipesHubAgentFactory`/`LangChainTransport` entirely — for tests that only
care about tool/hook/prompt wiring, not LangChain integration.
"""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.roles.base import Role
from app.agent_loop_lib.runtime.runtime import AgentRuntime

_RUNTIME_FIELDS = {
    "transport_registry", "tool_registry", "hooks", "event_emitter", "budget",
    "cancellation_token", "memory", "knowledge", "skills", "checkpoint_store",
    "session_store", "state_store", "hil_store", "approval_store", "timeline_store",
    "role_registry", "spec_factory",
}

_SPEC_FIELDS = {
    "name", "description", "capabilities", "system_prompt", "tool_names",
    "pinned_toolsets", "model", "loop", "max_turns", "mode", "output_style",
    "prompt_section_order", "extra_prompt_sections", "middleware",
}


def make_agent_spec(role: Role | None = None, **kwargs: Any) -> AgentSpec:
    role = role or Role(name="assistant", system_prompt="You are helpful.")
    provider = kwargs.pop("provider", "scripted")
    model_name = kwargs.pop("model", "scripted-model")
    thinking_budget = kwargs.pop("thinking_budget", None)
    effort = kwargs.pop("effort", None)
    loop_strategy = kwargs.pop("loop_strategy", None)

    spec_kwargs: dict[str, Any] = {
        "name": role.name,
        "description": role.description,
        "capabilities": list(role.capabilities),
        "system_prompt": role.system_prompt,
        "tool_names": list(role.allowed_tools),
        "model": ModelSpec(provider=provider, model=model_name, thinking_budget=thinking_budget, effort=effort),
    }
    if loop_strategy is not None:
        spec_kwargs["loop"] = loop_strategy
    for key in list(kwargs):
        if key in _SPEC_FIELDS:
            spec_kwargs[key] = kwargs.pop(key)
    return AgentSpec(**spec_kwargs)


def make_agent_runtime(**kwargs: Any) -> AgentRuntime:
    runtime_kwargs = {k: v for k, v in kwargs.items() if k in _RUNTIME_FIELDS}
    return AgentRuntime(**runtime_kwargs)


def make_agent(role: Role | None = None, *, session_id: str | None = None, **kwargs: Any) -> Agent:
    spec_kwargs = {k: v for k, v in kwargs.items() if k in _SPEC_FIELDS or k in ("provider", "model", "thinking_budget", "effort", "loop_strategy")}
    spec = make_agent_spec(role, **spec_kwargs)
    runtime = make_agent_runtime(**kwargs)
    return Agent(spec, runtime, session_id=session_id or kwargs.get("session_id"))


__all__ = ["make_agent", "make_agent_spec", "make_agent_runtime"]
