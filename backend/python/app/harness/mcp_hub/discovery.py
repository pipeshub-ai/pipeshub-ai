"""Tool discovery for one instance as one credential owner: the hub's way when ``MCP_HUB`` is on, today's otherwise.

On: the tool list comes from the hub's cache or a pooled session, names are checked
against the MCP rule and exported (§ names), descriptions escaped; a refused name is
logged once per process. Off: a STDIO instance's runner (P5.10) and a one-shot
``discover_tools``, exactly as before.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, cast

from app.agents.mcp.client import MCPConnectionError
from app.agents.mcp.discovery import build_auth_env_and_headers, discover_tools
from app.agents.mcp.errors import MCPConnectTimeoutError
from app.agents.mcp.models import MCPToolInfo
from app.harness.mcp_hub.hub import HubTarget, process_mcp_hub
from app.harness.mcp_hub.settings import hub_enabled
from app.harness.mcp_runner.routing import stdio_target
from harness_core.logs import HarnessLog
from harness_core.mcp_hub.names import (
    export_description,
    export_names,
    export_namespace,
    export_schema,
)
from harness_core.mcp_hub.types import SessionKey

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.agents.mcp.models import MCPServerConfig
    from harness_core.mcp_hub.manifest import ToolDescriptor
    from harness_core.mcp_hub.types import CachedTools

_LOG = HarnessLog.named("app.harness.mcp_hub")
_LOGGED_LIMIT = 4096
_logged: OrderedDict[tuple[str, str, str, str], None] = OrderedDict()


def _log_refused_once(tenant: str, server_id: str, tool: str, reason_code: str) -> None:
    key = (tenant, server_id, tool, reason_code)
    if key in _logged:
        return
    _logged[key] = None
    if len(_logged) > _LOGGED_LIMIT:
        _ = _logged.popitem(last=False)
    _LOG.warning("mcp_hub.tool_refused", orgId=tenant, mcpServer=server_id, tool=tool[:128], reasonCode=reason_code)


def server_namespace(config: MCPServerConfig) -> str:
    return export_namespace(config.type_id or config.name)


def tool_infos(config: MCPServerConfig, tools: Sequence[ToolDescriptor], *, tenant: str) -> list[MCPToolInfo]:
    """The tools whose names pass, under their exported names, with descriptions and schema prose escaped."""
    exported, refused = export_names(server_namespace(config), [t.name for t in tools])
    for raw, refusal in refused.items():
        _log_refused_once(tenant, config.id, raw, refusal.reason_code)
    return [
        MCPToolInfo(
            name=t.name,
            namespaced_name=exported[t.name],
            description=export_description(t.description),
            input_schema=cast("dict[str, Any]", export_schema(dict(t.input_schema))),
            annotations=dict(t.hints),
        )
        for t in tools
        if t.name in exported
    ]


async def hub_target(
    config: MCPServerConfig,
    credentials: Mapping[str, Any],
    *,
    tenant: str,
    owner: str,
    principal: str | None = None,
) -> HubTarget:
    env, headers = build_auth_env_and_headers(config, dict(credentials))
    runner = await stdio_target(config, credentials, tenant=tenant, owner=owner, principal=principal)
    return HubTarget(config=config, key=SessionKey(tenant, config.id, owner), env=env, headers=headers, runner=runner)


async def _hub_discover(
    config: MCPServerConfig,
    credentials: Mapping[str, Any],
    *,
    tenant: str,
    owner: str,
    principal: str | None,
    timeout_seconds: float,
    refresh: bool,
) -> CachedTools:
    target = await hub_target(config, credentials, tenant=tenant, owner=owner, principal=principal)
    hub = process_mcp_hub.get()
    if not refresh and (cached := await hub.cached(target)) is not None:
        return cached

    async def listed() -> CachedTools:
        try:
            return await asyncio.wait_for(hub.discover(target, refresh=refresh), timeout=timeout_seconds)
        except MCPConnectTimeoutError:
            # Also a TimeoutError: the server's own connect timeout, which already says how long it waited.
            raise
        except TimeoutError as exc:
            raise MCPConnectionError(
                f"Timed out discovering tools for MCP instance {config.id} after {timeout_seconds}s"
            ) from exc

    # Outside the timeout, so a listing that timed out counts against the server (MX-08).
    return await hub.breakers.run(target.key, config.name, listed)


async def discover_tools_for(
    config: MCPServerConfig,
    credentials: Mapping[str, Any],
    *,
    tenant: str,
    owner: str,
    principal: str | None = None,
    timeout_seconds: float,
    refresh: bool = False,
) -> list[MCPToolInfo]:
    if not hub_enabled():
        runner = await stdio_target(config, credentials, tenant=tenant, owner=owner, principal=principal)
        return await discover_tools(config, dict(credentials), timeout_seconds=timeout_seconds, runner=runner)
    infos, _ = await discover_listing_for(
        config, credentials, tenant=tenant, owner=owner, principal=principal,
        timeout_seconds=timeout_seconds, refresh=refresh,
    )
    return infos


async def discover_listing_for(
    config: MCPServerConfig,
    credentials: Mapping[str, Any],
    *,
    tenant: str,
    owner: str,
    principal: str | None = None,
    timeout_seconds: float,
    refresh: bool = False,
) -> tuple[list[MCPToolInfo], dict[str, ToolDescriptor]]:
    """The hub's tools as ``discover_tools_for`` gives them, and each one as its server wrote it: the description
    scanner reads the server's own text (MX-04), the model only the exported one."""
    cached = await _hub_discover(
        config, credentials, tenant=tenant, owner=owner, principal=principal,
        timeout_seconds=timeout_seconds, refresh=refresh,
    )
    return tool_infos(config, cached.tools, tenant=tenant), {t.name: t for t in cached.tools}


async def live_tools(
    config: MCPServerConfig,
    credentials: Mapping[str, Any],
    *,
    tenant: str,
    owner: str,
    timeout_seconds: float,
) -> tuple[list[MCPToolInfo], dict[str, str], dict[str, ToolDescriptor]]:
    """What the server lists now (the cache refreshed), each refused raw name's reason code (the hub is on), and
    each tool as the server wrote it."""
    cached = await _hub_discover(
        config, credentials, tenant=tenant, owner=owner, principal=None,
        timeout_seconds=timeout_seconds, refresh=True,
    )
    _, refused = export_names(server_namespace(config), [t.name for t in cached.tools])
    return (
        tool_infos(config, cached.tools, tenant=tenant),
        {raw: r.reason_code for raw, r in refused.items()},
        {t.name: t for t in cached.tools},
    )


__all__ = ["discover_listing_for", "discover_tools_for", "hub_target", "live_tools", "server_namespace", "tool_infos"]
