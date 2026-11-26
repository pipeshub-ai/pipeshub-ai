"""One run's write identity (OWD-18) as the loop and the engines' Tool Gateway apply it."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants
from app.harness.write_identity.disclosure import (
    ASSISTANT,
    WriteIdentitySettings,
    write_identity_middleware,
)
from harness_core.kernel.hooks.events import HookEvent

if TYPE_CHECKING:
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import ToolEnvironment
    from harness_core.kernel.hooks.registry import HookRegistry
    from harness_core.logs import HarnessLog
    from harness_core.tools.registry import ToolRegistry


async def app_url(config_service: Any) -> str | None:  # noqa: ANN401 - ConfigurationService
    """The web app's public address the session link names; ``None`` when it is not configured. An unreadable store
    means the same: the disclosure then names the session by id, and the write still carries it."""
    if config_service is None:
        return None
    endpoints = await config_service.get_config(
        config_node_constants.ENDPOINTS.value, default={}, raise_on_error=False
    )
    frontend = endpoints.get("frontend") if isinstance(endpoints, dict) else None
    url = frontend.get("publicEndpoint") if isinstance(frontend, dict) else None
    return url if isinstance(url, str) and url.startswith(("http://", "https://")) else None


def agent_name(run: RunContext, env: ToolEnvironment) -> str:
    """The run's agent's name; the built-in assistant's when it has none."""
    agent = env.agent if run.agent is not None else None
    name = agent.get("name") if isinstance(agent, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else ASSISTANT


async def user_write_identity(run: RunContext, env: ToolEnvironment, log: HarnessLog) -> WriteIdentitySettings:
    """Every write as the user, disclosed with the run's agent and session."""
    return WriteIdentitySettings(
        session_id=run.session_id,
        agent_name=agent_name(run, env),
        app_url=await app_url(env.context.config_service),
        log=log,
    )


def use_write_identity(
    hooks: HookRegistry, settings: WriteIdentitySettings, registry: ToolRegistry | None = None
) -> HookRegistry:
    """``hooks`` with the run's write identity applied before every tool call it dispatches; ``registry`` says which
    arguments each tool takes (without it, only text the call already holds gets the disclosure)."""

    def accepts(path: str) -> frozenset[str] | None:
        if registry is None or not registry.has_path(path):
            return None
        return frozenset(parameter.name for parameter in registry.resolve(path).parameters)

    def declared(path: str) -> str:
        if registry is None or not registry.has_path(path):
            return path
        declared_path = getattr(registry.resolve(path), "declared_path", None)
        return declared_path if isinstance(declared_path, str) else path

    hooks.on(HookEvent.PRE_TOOL_USE).use(write_identity_middleware(settings, accepts, declared))  # pyright: ignore[reportUnknownMemberType, reportUnusedCallResult]
    return hooks


__all__ = ["agent_name", "app_url", "use_write_identity", "user_write_identity"]
