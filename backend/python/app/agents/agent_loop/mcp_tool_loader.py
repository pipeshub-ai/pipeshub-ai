"""`MCPToolProvider` — registers attached, authenticated MCP servers' tools into a
per-request `ToolRegistry`.

Parallel to `PipesHubToolLoader` (`tool_loader.py`) for connector toolsets: one
`register_toolset` group per MCP instance, nested under `lazy_tools_wiring.MCP_PARENT`
("mcp") from the moment it's registered (never top-level `parent=None` — see that
constant's docstring for why).

Discovery-first, with graceful degradation: the primary source of truth is a LIVE
`discovery.discover_tools()` call against the instance (accurate, current schemas). When
`attached_tools` is set (graph selection or chat-time filter), the discovered list is
narrowed to those namespaced names before registration. If discovery times out or the
connection fails, this falls back to the attached tool list (name/description only) with a
permissive, schema-less parameter list, so a transient MCP outage degrades a server's tools
to "callable but unvalidated" instead of disappearing outright. A failure with no fallback
available (no live discovery AND `attached_tools is None` — unfiltered assistant path) is a
soft-skip recorded in `context.mcp_tool_load_failures`, mirroring `PipesHubToolLoader`'s
`toolset_load_failures`. This loader never hard-blocks the request — the route already did
that for missing/unauthenticated instances before this ever runs (see `api/routes/agent.py`'s
"LOAD MCP SERVER CONFIGS" block).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from app.agents.agent_loop.lazy_tools_wiring import MCP_PARENT
from app.agents.agent_loop.mcp_access import MCPAccessResolver, ResolvedMCPServer
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.discovery import build_namespaced_tool_name, discover_tools
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.service import credentials_to_discovery_dict, instance_config_from_dict

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 30.0

__all__ = ["MCPToolProvider", "DEFAULT_DISCOVERY_TIMEOUT_SECONDS"]


def _mcp_group_name(server: "ResolvedMCPServer") -> str:
    """`mcp_{instance_name}` — an `mcp_`-prefixed group name (same spirit as
    `discovery.build_namespaced_tool_name`'s `mcp_{server_type}_{tool}` tool names) so a
    user-chosen instance name can never collide with an existing connector toolset's group
    name (e.g. an MCP instance named "slack" would otherwise silently replace the native
    Slack toolset's `ToolsetGroup` — see `ToolRegistry.register_toolset`'s "replaces by
    name" semantics)."""
    normalized = server.name.lower().strip().replace(" ", "_").replace("-", "_")
    return f"mcp_{normalized}"


class MCPToolProvider:
    """`load_into(registry, context)` — the MCP analog of `PipesHubToolLoader.load()`."""

    async def load_into(self, registry: "ToolRegistry", context: "AgentContext") -> None:
        resolved_servers = MCPAccessResolver.resolve(context)
        if not resolved_servers:
            return

        state_logger = context.logger or logger
        session_manager = MCPSessionManager(context)

        outcomes = await asyncio.gather(
            *[self._load_one(server, registry, context, session_manager) for server in resolved_servers],
        )
        loaded_count = sum(1 for ok in outcomes if ok)

        if loaded_count:
            registry.register_toolset(
                MCP_PARENT,
                "Attached MCP (Model Context Protocol) servers. Call list_toolsets(toolset) "
                "with one of these names for a one-line description of every tool inside "
                "it, or fetch_tools(toolset) to load the real schemas directly.",
                [],
            )

        state_logger.info(
            "MCPToolProvider: loaded %d/%d attached MCP instance(s) (failures=%s)",
            loaded_count, len(resolved_servers),
            [f.get("instanceId") for f in context.mcp_tool_load_failures],
        )

    async def _load_one(
        self,
        server: "ResolvedMCPServer",
        registry: "ToolRegistry",
        context: "AgentContext",
        session_manager: "MCPSessionManager",
        *,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
    ) -> bool:
        """Registers one instance's tools + group. Returns whether at least one tool was
        registered. Never raises — any discovery/registration failure is recorded on
        `context.mcp_tool_load_failures` and results in `False`."""
        state_logger = context.logger or logger

        tool_infos, failure_reason = await self._discover_or_fallback(server, timeout_seconds)
        if not tool_infos:
            context.mcp_tool_load_failures.append({
                "instanceId": server.instance_id, "name": server.name, "reason": failure_reason or "error",
            })
            state_logger.warning(
                "MCPToolProvider: no tools available for MCP instance %s (%s), reason=%s",
                server.instance_id, server.name, failure_reason,
            )
            return False

        registered_names: list[str] = []
        for tool_info in tool_infos:
            adapter = MCPToolAdapter(server, tool_info, session_manager)
            try:
                registry.register_tool(adapter)
                registered_names.append(adapter.name)
            except (DuplicateToolNameError, DuplicateToolPathError):
                state_logger.warning("MCPToolProvider: skipping duplicate MCP tool: %s", adapter.name)

        if not registered_names:
            context.mcp_tool_load_failures.append({
                "instanceId": server.instance_id, "name": server.name, "reason": "error",
                "error": "every discovered tool name collided with an already-registered tool",
            })
            return False

        registry.register_toolset(
            _mcp_group_name(server),
            f"{server.display_name} — attached MCP server tools.",
            registered_names,
            parent=MCP_PARENT,
        )
        return True

    async def _discover_or_fallback(
        self, server: "ResolvedMCPServer", timeout_seconds: float,
    ) -> tuple[list[MCPToolInfo] | None, str | None]:
        try:
            config = instance_config_from_dict(server.instance)
            credentials = credentials_to_discovery_dict(server.instance.get("authMode", ""), server.auth)
            tool_infos = await discover_tools(config, credentials, timeout_seconds=timeout_seconds)
            return self._filter_by_attached(server, tool_infos), None
        except Exception as exc:
            logger.warning(
                "MCPToolProvider: live discovery failed for instance %s (%s): %s",
                server.instance_id, server.name, exc,
            )
            fallback = self._tool_infos_from_attached(server)
            if fallback:
                return fallback, None
            return None, "discovery_failed"

    @staticmethod
    def _attached_full_names(server: "ResolvedMCPServer") -> set[str] | None:
        """Allowed namespaced tool names from the attachment, or None = no restriction."""
        if server.attached_tools is None:
            return None
        server_type = server.instance.get("typeId") or server.name
        allowed: set[str] = set()
        for tool in server.attached_tools:
            full_name = tool.get("fullName")
            if isinstance(full_name, str) and full_name:
                allowed.add(full_name)
            name = tool.get("name")
            if isinstance(name, str) and name:
                allowed.add(build_namespaced_tool_name(server_type, name))
        return allowed

    @classmethod
    def _filter_by_attached(
        cls, server: "ResolvedMCPServer", tool_infos: list[MCPToolInfo],
    ) -> list[MCPToolInfo]:
        allowed = cls._attached_full_names(server)
        if allowed is None:
            return tool_infos
        return [ti for ti in tool_infos if ti.namespaced_name in allowed]

    @staticmethod
    def _tool_infos_from_attached(server: "ResolvedMCPServer") -> list[MCPToolInfo]:
        if not server.attached_tools:
            return []
        server_type = server.instance.get("typeId") or server.name
        infos: list[MCPToolInfo] = []
        for tool in server.attached_tools:
            name = tool.get("name")
            if not name:
                continue
            infos.append(MCPToolInfo(
                name=name,
                namespaced_name=tool.get("fullName") or build_namespaced_tool_name(server_type, name),
                description=tool.get("description"),
                input_schema={},
            ))
        return infos
