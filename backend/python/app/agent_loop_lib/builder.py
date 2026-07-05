from __future__ import annotations

from typing import Any

from app.agent_loop_lib.control_plane.config import (
    BrowserSandboxConfig,
    ControlPlaneConfig,
    DBSandboxConfig,
    OSSandboxConfig,
)

"""`AgentBuilder` / `create_agent` — fluent DX wrapping ControlPlane wiring
for library consumers who just want a working Agent without learning
ControlPlaneConfig's full surface area first. Every builder method is a thin
setter over the same config the CLI already constructs by hand — nothing
here bypasses ControlPlane; `.build()` still goes through the exact
`ControlPlane(cfg).start()` -> `make_spec()` -> `Agent(spec, runtime)`
pipeline.
"""


class BuiltAgent:
    """A ready-to-run `Agent` plus the `ControlPlane` that built it, bundled
    so callers who provisioned stateful resources (sandboxes, SQLite storage)
    have an obvious way to release them. Delegates attribute access to the
    wrapped `Agent` (`.run`, `._todos`, etc. all still work) so this is a
    non-breaking wrapper, not a new API surface to learn."""

    def __init__(self, agent: Any, control_plane: Any) -> None:
        self.agent = agent
        self.control_plane = control_plane

    async def run(self, goal: Any) -> Any:
        from app.agent_loop_lib.core.types import Goal
        if isinstance(goal, str):
            goal = Goal(description=goal)
        return await self.agent.run(goal)

    async def close(self) -> None:
        await self.control_plane.stop()

    async def __aenter__(self) -> "BuiltAgent":
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self.agent, name)


class AgentBuilder:
    def __init__(self) -> None:
        self._role_name: str = "assistant"
        self._custom_role: Any = None
        self._cfg_kwargs: dict[str, Any] = {}
        self._overrides: dict[str, Any] = {}

    # -- transport / model --

    def model(self, name: str) -> "AgentBuilder":
        self._cfg_kwargs["model"] = name
        return self

    def api_key(self, key: str) -> "AgentBuilder":
        self._cfg_kwargs["api_key"] = key
        return self

    def transport(self, name: str) -> "AgentBuilder":
        self._cfg_kwargs["transport"] = name
        return self

    # -- role --

    def role(self, role: Any) -> "AgentBuilder":
        """Accepts either a builtin role name (str, resolved against
        roles/registry.py's default_registry at build() time) or a `Role`
        instance (registered on the fly, for library consumers with their
        own custom roles who don't want to touch the global registry)."""
        from app.agent_loop_lib.roles.base import Role
        if isinstance(role, Role):
            self._custom_role = role
            self._role_name = role.name
        else:
            self._role_name = role
        return self

    # -- tools / toolsets --

    def tools(self, names: list[str]) -> "AgentBuilder":
        """Which builtin tools ControlPlane registers at all. Pass ["all"]
        for every builtin tool."""
        self._cfg_kwargs["tools"] = list(names)
        return self

    def toolsets(self, names: list[str]) -> "AgentBuilder":
        """Pin these Phase-1 lazy-toolset groups as always-visible instead
        of gated behind fetch_tools (see LazyToolsetsConfig.pinned_toolsets)."""
        self._cfg_kwargs["lazy_toolsets"] = {"pinned_toolsets": list(names)}
        return self

    # -- memory / knowledge --

    def memory(self, provider: str) -> "AgentBuilder":
        self._cfg_kwargs["memory"] = provider
        return self

    def knowledge(self, provider: str) -> "AgentBuilder":
        self._cfg_kwargs["knowledge"] = provider
        return self

    # -- workspace / skills / commands --

    def workspace(self, kind: str, root: str | None = None) -> "AgentBuilder":
        self._cfg_kwargs["workspace"] = kind
        if root is not None:
            self._cfg_kwargs["workspace_root"] = root
        return self

    def skills_dir(self, path: str) -> "AgentBuilder":
        """Sets the primary (writable) skills root — see `SkillManagerConfig.
        skills_dir`. Call again to instead set an additional read-only root
        via `extra_skills_dir()`."""
        skill_manager_kwargs = dict(self._cfg_kwargs.get("skill_manager") or {})
        skill_manager_kwargs["skills_dir"] = path
        self._cfg_kwargs["skill_manager"] = skill_manager_kwargs
        return self

    def extra_skills_dir(self, path: str) -> "AgentBuilder":
        """Adds a read-only skills root (npm-installed skill packs, a
        shared team directory, ...) — see `SkillManagerConfig.extra_skills_dirs`."""
        skill_manager_kwargs = dict(self._cfg_kwargs.get("skill_manager") or {})
        skill_manager_kwargs.setdefault("extra_skills_dirs", list(skill_manager_kwargs.get("extra_skills_dirs", [])))
        skill_manager_kwargs["extra_skills_dirs"] = [*skill_manager_kwargs["extra_skills_dirs"], path]
        self._cfg_kwargs["skill_manager"] = skill_manager_kwargs
        return self

    def commands_dir(self, path: str) -> "AgentBuilder":
        self._cfg_kwargs.setdefault("commands_dirs", [])
        self._cfg_kwargs["commands_dirs"].append(path)
        return self

    # -- sandboxes (Phase 3 taxonomy) --

    def os_sandbox(self, **kwargs: Any) -> "AgentBuilder":
        self._cfg_kwargs["os_sandbox"] = OSSandboxConfig(enabled=True, **kwargs)
        return self

    def db_sandbox(self, **kwargs: Any) -> "AgentBuilder":
        self._cfg_kwargs["db_sandbox"] = DBSandboxConfig(enabled=True, **kwargs)
        return self

    def browser_sandbox(self, **kwargs: Any) -> "AgentBuilder":
        self._cfg_kwargs["browser_sandbox"] = BrowserSandboxConfig(enabled=True, **kwargs)
        return self

    # -- hooks / storage / mode / budget --

    def hooks(self, names: list[str]) -> "AgentBuilder":
        self._cfg_kwargs["hooks"] = list(names)
        return self

    def storage(self, backend: str, path: str = ":memory:") -> "AgentBuilder":
        self._cfg_kwargs["storage"] = backend
        self._cfg_kwargs["storage_path"] = path
        return self

    def mode(self, mode: str) -> "AgentBuilder":
        self._cfg_kwargs["mode"] = mode
        return self

    def max_turns(self, n: int) -> "AgentBuilder":
        self._overrides["max_turns"] = n
        return self

    def loop_strategy(self, strategy: Any) -> "AgentBuilder":
        """Swap the turn loop's shape (see `agent/loops.py`) — e.g.
        `.loop_strategy(ReflexionLoop())` or
        `.loop_strategy(PlanExecuteLoop())`. Omit for the default ReAct
        loop."""
        self._overrides["loop"] = strategy
        return self

    def middleware(self, *installers: Any) -> "AgentBuilder":
        """Attach per-agent deterministic middleware installers (each a
        `Callable[[HookRegistry], None]`) that travel with this spec
        wherever it's used — see `AgentSpec.middleware`."""
        self._overrides.setdefault("middleware", [])
        self._overrides["middleware"].extend(installers)
        return self

    def sub_agent(self, name: str, spec: Any) -> "AgentBuilder":
        """Statically compose a child `AgentSpec` as a named tool on this
        agent (see `AgentFactory._wire_sub_agents`)."""
        self._overrides.setdefault("sub_agents", {})
        self._overrides["sub_agents"][name] = spec
        return self

    # -- escape hatches --

    def config(self, **kwargs: Any) -> "AgentBuilder":
        """Merge arbitrary ControlPlaneConfig fields not covered by a
        dedicated method above."""
        self._cfg_kwargs.update(kwargs)
        return self

    def spec_overrides(self, **kwargs: Any) -> "AgentBuilder":
        """Merge arbitrary `AgentSpec` field overrides, applied at
        `make_spec()` time (e.g. `max_turns`, `output_style`,
        `extra_prompt_sections`)."""
        self._overrides.update(kwargs)
        return self

    # -- build --

    async def build(self) -> BuiltAgent:
        from app.agent_loop_lib.agent import Agent
        from app.agent_loop_lib.control_plane.control_plane import ControlPlane

        cfg = ControlPlaneConfig(**self._cfg_kwargs)
        control_plane = ControlPlane(cfg)
        await control_plane.start()
        if self._custom_role is not None:
            control_plane.role_registry.register(self._custom_role)
        sub_agents = self._overrides.pop("sub_agents", None)
        spec = control_plane.make_spec(self._role_name, **self._overrides)
        if sub_agents:
            from app.agent_loop_lib.runtime.factory import AgentFactory
            spec = AgentFactory(
                control_plane.runtime, control_plane.role_registry,
            ).wire_sub_agents(spec, sub_agents)
        agent = Agent(spec, control_plane.runtime)
        return BuiltAgent(agent, control_plane)


async def create_agent(role: str | Any = "assistant", **kwargs: Any) -> BuiltAgent:
    """One-line `AgentBuilder` shortcut: `create_agent(role="coder",
    model="claude-opus-4-8", tools=["all"])`. Keyword arguments map to
    `AgentBuilder.config()` (i.e. raw `ControlPlaneConfig` fields) except for
    the handful with dedicated builder methods below, which are more
    ergonomic (list wrapping, sandbox sub-configs, ...)."""
    builder = AgentBuilder().role(role)
    dedicated = {
        "model": builder.model,
        "api_key": builder.api_key,
        "transport": builder.transport,
        "tools": builder.tools,
        "memory": builder.memory,
        "knowledge": builder.knowledge,
        "mode": builder.mode,
    }
    remaining = dict(kwargs)
    for key, setter in dedicated.items():
        if key in remaining:
            setter(remaining.pop(key))
    if remaining:
        builder.config(**remaining)
    return await builder.build()
