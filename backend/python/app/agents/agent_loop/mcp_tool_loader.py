"""`MCPToolProvider` — registers attached, authenticated MCP servers' tools into a
per-request `ToolRegistry`.

Parallel to `PipesHubToolLoader` (`tool_loader.py`) for connector toolsets: one
`register_toolset` group per MCP instance, nested under `lazy_tools_wiring.MCP_PARENT`
("mcp") from the moment it's registered (never top-level `parent=None` — see that
constant's docstring for why).

Discovery-first, no schema-less fallback: the ONLY source of truth is a LIVE
`discovery.discover_tools()` call against the instance (accurate, current schemas). When
`attached_tools` is set (graph selection or chat-time filter), the discovered list is
narrowed to those namespaced names before registration. If discovery times out or the
connection fails, NOTHING is registered for that instance — this used to fall back to the
attached tool list (name/description only) with `input_schema={}`, which is unusable rather
than "callable but unvalidated": the frontend (`sidebar-mcp-utils.ts`) and `_parse_mcp_servers`
(`api/routes/agent.py`) both strip `inputSchema` before persisting an agent's attached tools,
so that fallback tool had NO parameters for the LLM to fill in at all, and every call to it
either omitted required arguments or hallucinated ones the server actually needed — the exact
"incomplete arguments, frequent tool-call failures" symptom this whole module exists to avoid.
An unreachable server disappearing outright, with the failure recorded in
`context.mcp_tool_load_failures` (mirroring `PipesHubToolLoader`'s `toolset_load_failures`) and
surfaced to the model via `capability_summary.py`/`PipesHubGlobalCatalogFallback`, is more
legible than a tool that LOOKS callable and silently fails almost every call. This loader never
hard-blocks the request — the route already did that for missing/unauthenticated instances
before this ever runs (see `api/routes/agent.py`'s "LOAD MCP SERVER CONFIGS" block).
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from app.agents.agent_loop.lazy_tools_wiring import MCP_PARENT
from app.agents.agent_loop.mcp_access import MCPAccessResolver, ResolvedMCPServer
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_sign_in import note_sign_in_needed
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.tool_approvals import TOOLS_BY_PATH
from app.agents.mcp.failure import classify_mcp_failure, log_discover_end
from app.agents.mcp.naming import namespace_key, unique_keys
from app.agents.mcp.models import MCPToolInfo
from app.utils.logging_fields import log_event

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext

logger = logging.getLogger(__name__)

__all__ = ["MCPToolProvider"]


# `tool_state` key holding each instance's namespace and group for this request — read by
# `lazy_tools_wiring.PipesHubGlobalCatalogFallback` so it reports the same names.
MCP_NAMES_STATE_KEY = "mcp_names"


def _unique_per_request(
    servers: list["ResolvedMCPServer"], key_of: "Callable[[ResolvedMCPServer], str]",
) -> dict[str, str]:
    return unique_keys(
        servers, key_of=key_of, id_of=lambda s: s.instance_id, created_of=lambda s: s.instance.get("createdAt"),
    )


def _current_name(server: "ResolvedMCPServer") -> str:
    # The listings name tools after the instance as it is now, not the attachment snapshot.
    return server.instance.get("name") or server.name


def _assign_names(servers: list["ResolvedMCPServer"], context: "AgentContext") -> dict[str, dict[str, str]]:
    """Tool namespace (`mcp_{namespace}_{tool}`) and group name (`mcp_{name}`) per instance.
    A namespace the route assigned over the user's whole visible set (the assistant) is kept,
    so chat names match the listing a selection came from; the rest are made unique here.
    Group names are `mcp_`-prefixed so an instance named "slack" can't replace the native
    Slack toolset's group (`ToolRegistry.register_toolset` replaces by name)."""
    computed = _unique_per_request(servers, lambda s: namespace_key(s.instance.get("typeId"), _current_name(s)))
    groups = _unique_per_request(servers, lambda s: namespace_key(None, _current_name(s)))
    names = {
        server.instance_id: {
            "namespace": server.namespace or computed[server.instance_id],
            "group": f"mcp_{groups[server.instance_id]}",
        }
        for server in servers
    }
    context.tool_state[MCP_NAMES_STATE_KEY] = names
    return names


class MCPToolProvider:
    """`load_into(registry, context)` — the MCP analog of `PipesHubToolLoader.load()`."""

    async def load_into(self, registry: "ToolRegistry", context: "AgentContext") -> None:
        resolved_servers = MCPAccessResolver.resolve(context)
        if not resolved_servers:
            return

        state_logger = context.logger or logger
        session_manager = MCPSessionManager(context)
        names = _assign_names(resolved_servers, context)

        outcomes = await asyncio.gather(
            *[
                self._load_one(server, registry, context, session_manager, names[server.instance_id])
                for server in resolved_servers
            ],
        )
        loaded_count = sum(1 for registered in outcomes if registered)

        if loaded_count:
            registry.register_toolset(
                MCP_PARENT,
                "Attached MCP (Model Context Protocol) servers. Call list_toolsets(toolset) "
                "with one of these names for a one-line description of every tool inside "
                "it, or fetch_tools(toolset) to load the real schemas directly.",
                [],
            )

        log_event(
            state_logger, "mcp.load.end", servers=len(resolved_servers), servers_loaded=loaded_count,
            tools_listed=sum(outcomes), failed=len(context.mcp_tool_load_failures),
        )

    async def _load_one(
        self,
        server: "ResolvedMCPServer",
        registry: "ToolRegistry",
        context: "AgentContext",
        session_manager: "MCPSessionManager",
        names: dict[str, str],
    ) -> int:
        """Registers one instance's tools + group. Returns how many tools were registered.
        Never raises — any discovery/registration failure is recorded on
        `context.mcp_tool_load_failures` and results in 0."""
        state_logger = context.logger or logger

        started = time.perf_counter()
        tool_infos, failure, instructions = await self._discover(server, session_manager, names["namespace"])
        failure_reason = classify_mcp_failure(failure).value if failure is not None else None
        if not tool_infos:
            entry = {"instanceId": server.instance_id, "name": server.name, "reason": failure_reason or "error"}
            # In an agent chat only: the assistant attaches every server a person has, and a
            # refused listing isn't cached, so the card would follow every unrelated reply.
            if failure is not None and not context.is_assistant and note_sign_in_needed(context, server, failure):
                entry["signInHere"] = True
            context.mcp_tool_load_failures.append(entry)
            log_discover_end(
                state_logger, server.instance_id, failure, source="turn", mcp_reason=failure_reason,
                duration_ms=round((time.perf_counter() - started) * 1000),
            )
            return 0

        registered_names: list[str] = []
        by_path = context.tool_state.setdefault(TOOLS_BY_PATH, {})
        for tool_info in tool_infos:
            adapter = MCPToolAdapter(server, tool_info, session_manager, context=context)
            try:
                registry.register_tool(adapter)
                registered_names.append(adapter.name)
                # How the approval gate finds the server and the tool's hints for a call.
                by_path[adapter.path] = adapter
            except (DuplicateToolNameError, DuplicateToolPathError):
                state_logger.warning("MCPToolProvider: skipping duplicate MCP tool: %s", adapter.name)

        if not registered_names:
            context.mcp_tool_load_failures.append({
                "instanceId": server.instance_id, "name": server.name, "reason": "error",
                "error": "every discovered tool name collided with an already-registered tool",
            })
            return 0

        registry.register_toolset(
            names["group"],
            f"{server.display_name} — attached MCP server tools.",
            registered_names,
            parent=MCP_PARENT,
        )
        if instructions:
            context.mcp_server_instructions.append({
                "instanceId": server.instance_id, "name": server.display_name, "instructions": instructions,
            })
        return len(registered_names)

    async def _discover(
        self, server: "ResolvedMCPServer", session_manager: "MCPSessionManager", namespace: str,
    ) -> tuple[list[MCPToolInfo] | None, BaseException | None, str | None]:
        """The attached tools, the failure when there are none, and the server's instructions."""
        try:
            tool_infos, instructions = await session_manager.tools(server, namespace)
            return self._filter_by_attached(server, tool_infos), None, instructions
        except Exception as exc:
            return None, exc, None

    @staticmethod
    def _attached_names(server: "ResolvedMCPServer") -> tuple[set[str], set[str]] | None:
        """(raw tool names, full names) the attachment allows, or None = no restriction."""
        if server.attached_tools is None:
            return None
        raw: set[str] = set()
        full: set[str] = set()
        for tool in server.attached_tools:
            if isinstance(name := tool.get("name"), str) and name:
                raw.add(name)
            if isinstance(full_name := tool.get("fullName"), str) and full_name:
                full.add(full_name)
        return raw, full

    @classmethod
    def _filter_by_attached(
        cls, server: "ResolvedMCPServer", tool_infos: list[MCPToolInfo],
    ) -> list[MCPToolInfo]:
        """By the server's own tool name first: the full name an agent or project saved
        depends on how names were built then (legacy `{name}.{tool}`, an untagged namespace),
        but the raw name within this instance doesn't."""
        allowed = cls._attached_names(server)
        if allowed is None:
            return tool_infos
        raw, full = allowed
        return [ti for ti in tool_infos if ti.name in raw or ti.namespaced_name in full]
