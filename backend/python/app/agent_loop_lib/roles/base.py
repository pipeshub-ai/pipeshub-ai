from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Role(BaseModel):
    """Role as data — a named preset an `AgentFactory` turns into a full
    `AgentSpec`, never a subclass. Every field beyond `name`/`system_prompt`
    is optional so a role can be as small as "a prompt" or as complete as
    "a prompt, its own model, loop shape, deterministic middleware, and a
    set of named sub-agents to compose in" (see `examples/02_orchestrator.py`).
    """

    name: str
    system_prompt: str
    allowed_tools: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    description: str = ""

    # Optional per-role overrides of AgentFactory's defaults (see
    # runtime/factory.py::AgentFactory.from_role_obj). None means "use the
    # factory's default" for each.
    model: str | None = None
    loop: Any = None          # LoopStrategy | None
    mode: str | None = None
    middleware: list[Any] = Field(default_factory=list)  # Callable[[HookRegistry], None]

    # Static agent-to-agent composition: name -> child AgentSpec, wired by
    # AgentFactory into `agent_as_tool()` wrappers and added to
    # `allowed_tools` automatically — lets a role declare a full
    # orchestrator (this role + its sub-agents) as pure data.
    sub_agents: dict[str, Any] = Field(default_factory=dict)  # name -> AgentSpec

    model_config = {"arbitrary_types_allowed": True}
