from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec

if TYPE_CHECKING:
    from app.agent_loop_lib.roles.base import Role
    from app.agent_loop_lib.roles.registry import RoleRegistry
    from app.agent_loop_lib.runtime.runtime import AgentRuntime

__all__ = ["AgentFactory"]


class AgentFactory:
    """Layer 4: builds `AgentSpec`s from `Role` data (+ this runtime's
    provider/model defaults). `ControlPlane.make_spec()` and dynamic
    role-based tools (spawn_agent, best_of_n, handoff) both go through
    this — a `Role` alone can define a complete agent (system prompt,
    allowed tools, model, loop shape, middleware, and even named
    sub-agents), letting `roles/builtin/*.py` be pure data.
    """

    def __init__(
        self,
        runtime: "AgentRuntime",
        role_registry: "RoleRegistry",
        *,
        default_provider: str = "anthropic",
        default_model: str = "claude-sonnet-4-6",
    ) -> None:
        self._runtime = runtime
        self._role_registry = role_registry
        self._default_provider = default_provider
        self._default_model = default_model

    def from_role(self, role_name: str, **overrides: Any) -> AgentSpec:
        try:
            role = self._role_registry.resolve(role_name)
        except Exception as exc:
            raise ValueError(
                f"Unknown role: {role_name!r}. Available: {self._role_registry.names()}"
            ) from exc
        return self.from_role_obj(role, **overrides)

    def from_role_obj(self, role: "Role", **overrides: Any) -> AgentSpec:
        model = ModelSpec(provider=self._default_provider, model=role.model or self._default_model)
        model_override = overrides.pop("model", None)
        if isinstance(model_override, str):
            model = model.model_copy(update={"model": model_override})
        elif isinstance(model_override, ModelSpec):
            model = model_override

        tool_names = overrides.pop("tool_names", None)
        if tool_names is None:
            tool_names = list(role.allowed_tools)

        spec_kwargs: dict[str, Any] = {
            "name": role.name,
            "description": role.description,
            "system_prompt": role.system_prompt,
            "tool_names": tool_names,
            "capabilities": list(role.capabilities),
            "model": model,
            "middleware": list(role.middleware),
        }
        if role.loop is not None:
            spec_kwargs["loop"] = role.loop
        if role.mode is not None:
            spec_kwargs["mode"] = role.mode
        spec_kwargs.update(overrides)

        spec = AgentSpec(**spec_kwargs)
        if role.sub_agents:
            spec = self.wire_sub_agents(spec, role.sub_agents)
        return spec

    def wire_sub_agents(self, spec: AgentSpec, sub_agents: dict[str, AgentSpec]) -> AgentSpec:
        """Registers each named child `AgentSpec` as an `AgentTool` on this
        runtime's shared tool registry (idempotent: re-registering the same
        name is a no-op) and adds its name to the parent spec's tool list —
        so a `Role` can declare full agent-to-agent composition purely as
        data (see `examples/02_orchestrator.py`)."""
        from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool

        registry = self._runtime.tool_registry
        tool_names = list(spec.tool_names)
        for tool_name, child_spec in sub_agents.items():
            if not registry.has(tool_name):
                registry.register_tool(AgentTool(child_spec, self._runtime, name=tool_name))
            if tool_name not in tool_names:
                tool_names.append(tool_name)
        return spec.model_copy(update={"tool_names": tool_names})
