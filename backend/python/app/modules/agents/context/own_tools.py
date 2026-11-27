"""In a run others read, the assistant is offered none of the person's own tools.

That is their personal toolsets and the MCP servers they added for themselves. The run's tools, its prompts (the tool
list, "Available Actions", "Needs Authentication", "Unavailable MCP Servers", the stubs of tools that are not
connected, the router's capability block) and its sub-agents all read the lists this trims, so none of those names
reaches a model call. The person uses them in a private ask or a solo chat.

Organization toolsets and servers stay, and so does everything a saved agent is configured with: its tools are its
configuration, not the person's.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.modules.agents.context.named_sources import runs_as_assistant

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

PERSONAL_SCOPE = "personal"


def _personal(record: object) -> bool:
    return isinstance(record, Mapping) and record.get("scope") == PERSONAL_SCOPE


def keep_own_tools_out(context: AgentContext) -> None:
    """Drops the person's personal toolsets and own MCP servers from `context`, and the notes about tools that are
    not connected, when others read the run's answer. Called once, as the context is built."""
    if not (runs_as_assistant(context) and context.read_by_others):
        return
    from app.modules.agents.qna.chat_state import (
        _build_tool_to_toolset_map,
        _extract_tools_from_toolsets,
    )

    state = context.tool_state
    toolsets = [ts for ts in context.agent_toolsets if not _personal(ts)]
    dropped_toolsets = {ts.get("instanceId") for ts in context.agent_toolsets if _personal(ts)}
    servers = [
        server for server in context.mcp_servers
        if not _personal((context.mcp_server_configs.get(server.get("instanceId") or "") or {}).get("instance"))
    ]
    kept_servers = {server.get("instanceId") for server in servers}
    dropped_servers = {server.get("instanceId") for server in context.mcp_servers} - kept_servers

    if dropped_toolsets:
        context.agent_toolsets = toolsets
        context.tool_to_toolset_map = _build_tool_to_toolset_map(toolsets)
        context.toolset_configs = {k: v for k, v in context.toolset_configs.items() if k not in dropped_toolsets}
        state.update({
            "agent_toolsets": toolsets,
            "tools": _extract_tools_from_toolsets(toolsets),
            "tool_to_toolset_map": context.tool_to_toolset_map,
            "toolset_configs": context.toolset_configs,
        })
    if dropped_servers:
        context.mcp_servers = servers
        context.mcp_server_configs = {k: v for k, v in context.mcp_server_configs.items() if k not in dropped_servers}
        state.update({"mcp_servers": servers, "mcp_server_configs": context.mcp_server_configs})
    # The assistant is offered only tools the person has connected, so every stub of one that is not is about
    # their own sign-in.
    state["unavailable_tools"] = []


__all__ = ["PERSONAL_SCOPE", "keep_own_tools_out"]
