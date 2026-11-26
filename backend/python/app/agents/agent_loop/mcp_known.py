"""What a run knows about an attached MCP server before it asks the server anything (ADR-OWD-07 D-LAZY-1).

A harness run lists a server only when it first loads or calls one of its tools (D-LAZY-4). Until then it knows the
server from what is stored: the instance (its name and description), its catalog entry, and the tools its last
listing for this sign-in found (main's per-sign-in tool catalog, ``app.agents.mcp.tool_cache``). That knowledge
names the source to the model, ranks it in a search, and counts its tools; nothing a run calls is taken from it.
Reading it opens no session.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.agents.agent_loop.connected_sources import source_summary
from app.agents.mcp import tool_cache
from app.agents.mcp.service import instance_config_from_dict

if TYPE_CHECKING:
    from collections.abc import Iterable

    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer
    from harness_core.mcp_hub.manifest import ToolDescriptor


@dataclass(frozen=True, slots=True)
class KnownTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    annotations: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class KnownServer:
    instance_id: str
    name: str
    summary: str
    # The tools the last stored listing for this sign-in found, narrowed to the attachment; ``None`` when none is
    # stored (a server this sign-in never listed, or one whose list expired).
    tools: tuple[KnownTool, ...] | None
    instructions: str | None = None

    def count(self, attached: int | None) -> int | None:
        """How many tools the run may call: the attachment's own count, else the stored list's."""
        if attached is not None:
            return attached
        return len(self.tools) if self.tools is not None else None


def template_description(server: ResolvedMCPServer) -> str | None:
    """What the server's catalog entry says it covers (Rovo: "Jira, Confluence, Bitbucket, and more ...")."""
    from app.agents.mcp.registry import get_mcp_registry

    registry = get_mcp_registry()
    registry.auto_discover_templates()
    template = registry.get_template(str(server.instance.get("typeId") or ""))
    return template.description if template is not None else None


def _attached(server: ResolvedMCPServer) -> set[str] | None:
    if server.attached_tools is None:
        return None
    return {str(t["name"]) for t in server.attached_tools if isinstance(t.get("name"), str) and t["name"]}


async def known_server(context: AgentContext, server: ResolvedMCPServer) -> KnownServer:
    hit = None
    if context.config_service is not None:
        hit = await tool_cache.read(
            context.config_service, instance=server.instance, config=instance_config_from_dict(server.instance),
            owner_id=server.owner_id, auth=server.auth,
        )
    instructions = hit.catalog.instructions if hit is not None else None
    tools: tuple[KnownTool, ...] | None = None
    if hit is not None:
        allowed = _attached(server)
        tools = tuple(
            KnownTool(t.name, t.description or "", dict(t.input_schema), dict(t.annotations) if t.annotations else None)
            for t in hit.catalog.tools
            if allowed is None or t.name in allowed
        )
    summary = source_summary(
        description=server.instance.get("description"), template=template_description(server),
        instructions=instructions, tools=[t.name for t in tools or ()],
    )
    return KnownServer(server.instance_id, server.display_name, summary, tools, instructions)


async def known_servers(context: AgentContext, servers: Iterable[ResolvedMCPServer]) -> list[KnownServer]:
    return [await known_server(context, server) for server in servers]


async def remember_listing(
    context: AgentContext, server: ResolvedMCPServer, listed: Iterable[ToolDescriptor], instructions: str | None,
) -> None:
    """Stores what a live listing found as this sign-in's catalog, the server's own text, as main's chat stores it:
    the next run knows the server's tools without listing it."""
    if context.config_service is None:
        return
    catalog = tool_cache.CachedCatalog(
        tools=[
            tool_cache.CachedTool(
                name=t.name, description=t.description, input_schema=dict(t.input_schema),
                annotations=dict(t.hints) or None,
            )
            for t in listed
        ],
        instructions=instructions,
    )
    config = instance_config_from_dict(server.instance)
    stored = await tool_cache.read(
        context.config_service, instance=server.instance, config=config, owner_id=server.owner_id, auth=server.auth,
    )
    # A listing served from the hub's cache finds what is stored: no write per run.
    if stored is not None and stored.pointer.digest == tool_cache.digest(catalog) and not tool_cache.needs_refresh(stored.pointer):
        return
    await tool_cache.write(
        context.config_service, instance=server.instance, config=config,
        owner_id=server.owner_id, auth=server.auth, catalog=catalog,
    )


__all__ = ["KnownServer", "KnownTool", "known_server", "known_servers", "remember_listing", "template_description"]
