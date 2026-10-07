"""Joins the two MCP fields on `AgentContext` into a flat, runtime-ready list.

`context.mcp_servers` (attached refs — instanceId/name/tools) and
`context.mcp_server_configs` (SENSITIVE: resolved `{"instance": ..., "auth": ...,
"ownerId": ...}` per instanceId) are both already fully resolved by
`api/routes/agent.py`'s chat handler — that route hard-blocks the whole request before it
ever reaches the agent-loop runtime if an attached instance is missing or unauthenticated
(see its "LOAD MCP SERVER CONFIGS" block). `MCPAccessResolver` is intentionally NOT another
etcd round-trip: it only joins the two lists the route already built into one list of
`ResolvedMCPServer` for `MCPToolProvider`/`MCPSessionManager` to act on directly.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = ["ResolvedMCPServer", "MCPAccessResolver"]


@dataclass
class ResolvedMCPServer:
    """One attached MCP instance, ready for tool discovery/execution.

    `auth` is mutated in place by `MCPSessionManager` after an on-demand OAuth
    refresh (see that module) so a subsequent call against the SAME
    `ResolvedMCPServer` within this request sees the fresh token without a
    second `MCPAccessResolver.resolve()` pass.
    """

    instance_id: str
    name: str
    display_name: str
    instance: dict[str, Any]  # raw stored instance metadata (transport/url/authMode/...) - no secrets
    auth: dict[str, Any]  # SENSITIVE: resolved credential record ({} for auth_mode "none")
    owner_id: str  # credential_lookup_id (agentKey for service accounts, else the executing user) - the id the SAME credential record is persisted under, needed to refresh+persist correctly
    attached_tools: list[dict[str, Any]] | None  # explicit tool selection (graph attachment or chat filter); None = discover everything (unfiltered assistant path)


class MCPAccessResolver:
    """Joins `context.mcp_servers` with `context.mcp_server_configs`."""

    @staticmethod
    def resolve(context: "AgentContext") -> list[ResolvedMCPServer]:
        resolved: list[ResolvedMCPServer] = []
        for mcp_server in context.mcp_servers:
            instance_id = mcp_server.get("instanceId")
            if not instance_id:
                continue

            config = context.mcp_server_configs.get(instance_id)
            if not config or not config.get("instance"):
                # The chat route already hard-blocks on a missing/unauthenticated
                # instance for non-assistant agents; reaching this branch just
                # means the instance fell out of `mcp_server_configs` for some
                # other reason it already logged — skip rather than raise,
                # mirroring how `PipesHubToolLoader` skips an unconfigured
                # toolset instead of failing the whole request.
                logger.debug(
                    "MCPAccessResolver: no resolved config for instance %s, skipping", instance_id,
                )
                continue

            resolved.append(ResolvedMCPServer(
                instance_id=instance_id,
                name=mcp_server.get("name") or instance_id,
                display_name=mcp_server.get("displayName") or mcp_server.get("name") or instance_id,
                instance=config["instance"],
                auth=config.get("auth") or {},
                owner_id=config.get("ownerId") or context.user_id,
                attached_tools=mcp_server.get("tools"),
            ))
        return resolved
