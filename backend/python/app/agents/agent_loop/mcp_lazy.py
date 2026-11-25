"""A run's attached MCP servers, each listed at its first use and pinned there (ADR-OWD-07 D-LAZY-3..6).

A server is registered as one lazy group (``ToolsetProvider``): its granted tools are named, ranked and counted from
what is stored (``mcp_known``), and none is built. The first time the run loads one of them (a tool search or
``fetch_tools`` answering it, ``list_toolsets`` describing the group) or calls one, the server is listed once for the
attempt, through the same load the claim used (approvals, the description scanner, the run's pins), and its tools
are built from that listing. What that listing served is the run's pin of the server: journalled before any result
reaches the model (``PINS_SLOT``, with the outcome of the call that first used it), and every later listing in the
run, a resumed attempt's included, is held to it. A tool that changed since is refused, and one added since is not
the run's. A server whose first listing fails, or whose tools the scanner holds back, is told to the person then.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from app.agents.agent_loop.connected_sources import (
    MCP_PARENT,
    SOURCES_STATE_KEY,
    group_description,
    known_source,
    tools_summary,
)
from app.agents.agent_loop.mcp_access import MCPAccessResolver
from app.agents.agent_loop.mcp_known import KnownServer, KnownTool, known_server
from app.agents.agent_loop.mcp_tool_loader import (
    CHANGED_STATE_KEY,
    HELD_STATE_KEY,
    MCP_PARENT_DESCRIPTION,
    MCPToolProvider,
    exported_tool_name,
    server_names,
    shared_server_types,
)
from app.core.org_context import spawn_in_request
from app.harness.mcp_hub.settings import harness_surface, hub_enabled
from harness_core.tools.base import ToolSummary
from harness_core.tools.errors import DuplicateToolNameError, DuplicateToolPathError
from harness_core.tools.provider import MaterializeRefused, ToolsetSummary

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.connected_sources import ConnectedSource
    from app.agents.agent_loop.context import AgentContext
    from app.agents.agent_loop.mcp_access import ResolvedMCPServer
    from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
    from harness_core.journal.types import JournalEvent, NewEvent

# The run slot each server's first-use pin is journalled in: instance id -> the server's tool name -> its digest.
PINS_SLOT: Final = "mcp.pins"
NOTICE: Final = "x.pipeshub.run.notice"
MCP_SOURCE: Final = "mcp"
# Why a first listing gave nothing, as ``x.pipeshub.run.notice`` ``mcp_unavailable`` names it (MCPNOTE-1).
_HELD_REASONS: Final = {"mcp_tool_description_flagged": "flagged", "mcp_no_tools_offered": "no_tools"}
MAX_HELD_NAMED: Final = 20
# The notice's `instance` is the contract's opaque Id: never a URL or anything else the instance id might hold.
_NOTICE_INSTANCE: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def notice_reason(code: str) -> str:
    """The person-facing reason of a failed first listing (``mcp_unavailable``'s ``reason``)."""
    from app.agents.mcp.failure import SIGN_IN_FAILURES

    if code in {r.value for r in SIGN_IN_FAILURES}:
        return "sign_in"
    if code in ("mcp_tool_unapproved", "mcp_tool_changed"):
        return "not_approved"
    return _HELD_REASONS.get(code, "unreachable")


def refusal_text(server: str, reason: str) -> str:
    """What a search or a call answers about a server whose first listing gave nothing."""
    return {
        "sign_in": f"{server} needs the user to sign in again before its tools can be used. Tell the user.",
        "not_approved": f"None of {server}'s tools is approved for this chat, so none was used. Tell the user.",
        "no_tools": f"{server} answered but offers no tools this chat can use.",
        "flagged": f"{server}'s tools were held back by PipesHub's safety check, so none was used. Tell the user.",
    }.get(reason, f"{server} didn't answer, so its tools couldn't be used. Carry on without it and tell the user.")


def held_refusal(name: str, server: str, tool: str, code: str) -> MaterializeRefused:
    """A tool the hub's admission held at its listing, refused for its own reason: not approved, or changed since
    an admin approved it."""
    if code == "mcp_tool_changed":
        return MaterializeRefused(
            name, "changed",
            f"The {server} MCP server changed the tool {tool} since an admin approved it, so it wasn't called. "
            "Tell the user: an admin needs to review and approve it again.",
        )
    return MaterializeRefused(
        name, "not_approved",
        f"The {server} MCP server's tool {tool} isn't approved for this chat, so it wasn't called. "
        "Tell the user: an admin needs to approve it.",
    )


@dataclass(frozen=True, slots=True)
class KnownCall:
    """A granted tool of a server not listed yet, as the run knows it: the name it is called by, its server, its own
    name and what the stored catalog says of it (``None``: nothing stored for it)."""

    name: str
    instance_id: str
    server: str
    tool: str
    stored: KnownTool | None


@dataclass
class _Server:
    resolved: ResolvedMCPServer
    names: dict[str, str]
    known: KnownServer
    # Granted tools by the server's own name -> the name the model calls them by.
    granted: dict[str, str]
    listing: asyncio.Future[dict[str, MCPToolAdapter]] | None = None
    refused: dict[str, MaterializeRefused] = field(default_factory=dict)
    # Granted whole (D-LAZY-2): every tool its first listing offers is the run's, named before that from the stored
    # catalog when there is one.
    whole: bool = False


class _ServerProvider:
    """One server's lazy group: names and summaries now, the tools at its first listing."""

    def __init__(self, servers: LazyMcpServers, server: _Server) -> None:
        self._servers = servers
        self._server = server

    def summary(self) -> ToolsetSummary:
        s = self._server
        return ToolsetSummary(s.names["group"], group_description(s.resolved.display_name, s.known.summary), MCP_PARENT)

    def list_tools(self) -> list[ToolSummary]:
        s = self._server
        stored = {t.name: t for t in s.known.tools or ()}
        return [
            ToolSummary(
                name=name,
                short_description=(stored[tool].description.split("\n", 1)[0] if tool in stored else "")
                or f"{s.resolved.display_name}: {tool}",
                path=f"/mcp/{s.resolved.instance_id}/{tool}",
            )
            for tool, name in sorted(s.granted.items())
        ]

    async def materialize(self, name: str) -> MCPToolAdapter:
        s = self._server
        listed = await self._servers.listed(s)
        tool = next((t for t, n in s.granted.items() if n == name), None)
        adapter = listed.get(tool) if tool is not None else None
        if adapter is None:
            raise s.refused.get(tool or "") or MaterializeRefused(
                name, "not_offered", f"The {s.resolved.display_name} MCP server no longer offers the tool {tool or name}.",
            )
        return adapter


class LazyMcpServers:
    """The run's MCP servers, listed at first use. One per attempt; ``pins`` carries a resumed run's pins, and
    ``carried`` the pins a child's parent took at its own first use of a server (R31HOT2)."""

    def __init__(
        self,
        context: AgentContext,
        *,
        base_pins: Mapping[str, Mapping[str, str]] | None = None,
        carried: Mapping[str, Mapping[str, str]] | None = None,
    ) -> None:
        self._context = context
        # The admission's version-2 pins (D-MCP-5): what a first listing is held to when no earlier attempt pinned.
        self._base = {k: dict(v) for k, v in (base_pins or {}).items()}
        # D-LAZY-5: a server the parent listed is the run's at the parent's pin from the start, and its tools are
        # built from the stored catalog that listing wrote when it matches the pin, so the job does not list it again.
        self._carried = frozenset(instance for instance, tools in (carried or {}).items() if tools)
        # The servers whose pin is a listing already taken, the parent's (carried) or this run's own (restored).
        self._taken: set[str] = set(self._carried)
        self.pins: dict[str, dict[str, str]] = {k: dict(v) for k, v in (carried or {}).items() if v}
        self._servers: dict[str, _Server] = {}
        self._pending_slots: dict[str, JsonValue] = {}
        self._pending_events: list[NewEvent] = []
        self._shared: frozenset[str] = frozenset()
        self._registry: ToolRegistry | None = None

    def restore(self, pins: Mapping[str, Mapping[str, str]]) -> None:
        """The pins an earlier attempt of this run journalled. Each is a listing the run already took, as a carried
        pin is: a resumed attempt builds the server from the stored catalog when it matches (R43MCP-FU-3: with a
        short hub schema TTL, a parent resuming after its job listed its own first-use servers live again)."""
        self.pins.update({k: dict(v) for k, v in pins.items()})
        self._taken |= {k for k, v in pins.items() if v}

    async def register(
        self, registry: ToolRegistry, granted: Iterable[str], whole: Collection[str] = ()
    ) -> list[tuple[str, str, str]]:
        """Each attached server's lazy group, with the granted tools named; ``(name, tool_id, instance)`` per tool.
        A server granted ``whole`` is named by its stored catalog, or by nothing until its first listing. Reads only
        what is stored: no server is asked anything."""
        self._registry = registry
        resolved = await MCPAccessResolver.resolve(self._context)
        if not resolved:
            return []
        names = server_names(resolved, self._context)
        self._shared = shared_server_types(resolved)
        with harness_surface(on=self._context.harness_run):
            hub = hub_enabled()
        by_instance: dict[str, list[str]] = {}
        for tool_id in granted:
            source, _, rest = tool_id.partition("@")
            if source == MCP_SOURCE and "/" in rest:
                instance, tool = rest.split("/", 1)
                by_instance.setdefault(instance, []).append(tool)
        out: list[tuple[str, str, str]] = []
        for server in resolved:
            is_whole = server.instance_id in whole
            tools = by_instance.get(server.instance_id, [])
            if not tools and not is_whole:
                continue
            known = await known_server(self._context, server)
            if is_whole:
                tools = [t.name for t in known.tools or ()]
            pin = self.pins.get(server.instance_id) if server.instance_id in self._carried else None
            if pin is not None:
                # Only what the parent's listing served is the run's; a whole server is named by its pin even when
                # nothing of it is stored.
                tools = sorted(pin) if is_whole else [t for t in tools if t in pin]
            ns = names[server.instance_id]["namespace"]
            granted_names = {
                tool: exported_tool_name(server, ns, tool, shared=self._shared, hub=hub) for tool in sorted(set(tools))
            }
            entry = _Server(server, names[server.instance_id], known, granted_names, whole=is_whole)
            try:
                registry.register_toolset_provider(_ServerProvider(self, entry))
            except DuplicateToolNameError:
                # Another source registered a tool of the same name first: the server's tools stay unreachable, as
                # a load skips a duplicate.
                continue
            self._servers[server.instance_id] = entry
            self._context.tool_state.setdefault(SOURCES_STATE_KEY, {})[entry.names["group"]] = {
                "name": server.display_name, "summary": known.summary,
            }
            out += [(name, f"mcp@{server.instance_id}/{tool}", server.instance_id) for tool, name in granted_names.items()]
        if self._servers:
            registry.register_toolset(MCP_PARENT, MCP_PARENT_DESCRIPTION, [])
        return out

    def sources(self) -> list[ConnectedSource]:
        """The servers as stored metadata names them (D-LAZY-7), each with its granted count; ``None`` for a server
        granted whole whose tools the run has not named yet."""
        return [
            known_source(
                s.resolved.display_name, s.names["group"], s.known.summary,
                None if s.whole and not s.granted else len(s.granted),
            )
            for s in self._servers.values()
        ]

    def __bool__(self) -> bool:
        return bool(self._servers)

    def owns(self, name: str) -> bool:
        return self.tool_id(name) is not None

    def tool_id(self, name: str) -> str | None:
        """The run's tool id of one of its servers' tools, named now or by a first listing since."""
        for s in self._servers.values():
            for tool, named in s.granted.items():
                if named == name:
                    return f"{MCP_SOURCE}@{s.resolved.instance_id}/{tool}"
        return None

    def granted_names(self) -> set[str]:
        return {name for s in self._servers.values() for name in s.granted.values()}

    def unlisted(self) -> list[KnownCall]:
        """The granted tools of the servers this attempt has not listed yet, each with what is stored of it: read
        without asking any server, so a caller can choose which first use is worth making."""
        out: list[KnownCall] = []
        for s in self._servers.values():
            if s.listing is not None:
                continue
            stored = {t.name: t for t in s.known.tools or ()}
            out += [
                KnownCall(name, s.resolved.instance_id, s.resolved.display_name, tool, stored.get(tool))
                for tool, name in sorted(s.granted.items())
            ]
        return out

    async def name_unnamed(self) -> None:
        """The first use of each server granted whole that names no tool yet (no stored catalog): listed, so its
        tools have names."""
        for server in [s for s in self._servers.values() if s.whole and s.listing is None and not s.granted]:
            await self.listed(server)

    async def warm(self, query: str) -> None:
        """A search's first use of the servers granted whole that the run cannot name a tool of yet: each whose own
        words (its name, what it covers) the query shares is listed, so the search can answer its tools, and each that
        nothing describes, whose words cannot rule it out (R36LIVE-FU-2: a server attached without a description and
        never listed was invisible to every search). The listings run together: one round trip, not one per server."""
        words = _words(query)
        cold = [
            s for s in self._servers.values()
            if s.whole and not s.granted
            and (not s.known.summary or words & _words(f"{s.resolved.display_name} {s.known.summary}"))
        ]
        await asyncio.gather(*(self.listed(s) for s in cold))

    async def warm_group(self, group: str) -> None:
        """``fetch_tools`` naming a server's group is its first use."""
        for s in self._servers.values():
            if s.names["group"] == group:
                await self.listed(s)

    async def listed(self, server: _Server) -> dict[str, MCPToolAdapter]:
        """The server's first listing in this attempt, shared by every caller (and shielded from a stopped one)."""
        if server.listing is None:
            # The run's own work, in its org context: a tracked task, shared by every caller of this attempt.
            server.listing = spawn_in_request(self._list(server), name=f"mcp.first_listing:{server.resolved.instance_id}")
        return await asyncio.shield(server.listing)

    async def _list(self, server: _Server) -> dict[str, MCPToolAdapter]:
        instance = server.resolved.instance_id
        pinned = self.pins.get(instance) or self._base.get(instance)
        adapters, failure = await MCPToolProvider().list_server(
            server.resolved, self._context, server.names, shared=self._shared, pins=pinned,
            stored_first=instance in self._taken,
        )
        by_tool = {a.tool_name: a for a in adapters if server.whole or a.tool_name in server.granted}
        if pinned is not None and instance in self.pins:
            # A tool the run's pin does not hold was added after its first use: not this run's.
            by_tool = {t: a for t, a in by_tool.items() if t in self.pins[instance]}
        label = server.resolved.display_name
        for tool, code in self._context.tool_state.get(HELD_STATE_KEY, {}).get(instance, {}).items():
            server.refused[tool] = held_refusal(server.granted.get(tool, tool), label, tool, code)
        changed = self._context.tool_state.get(CHANGED_STATE_KEY, {}).get(instance, [])
        for tool in changed:
            server.refused[tool] = MaterializeRefused(
                server.granted.get(tool, tool), "changed",
                f"The {label} MCP server changed the tool {tool} since this chat first used it, so it wasn't called. "
                "It can be used in a new chat.",
            )
        if failure is not None and not by_tool:
            reason = notice_reason(failure)
            text = refusal_text(label, reason)
            for tool, name in server.granted.items():
                server.refused.setdefault(tool, MaterializeRefused(name, reason, text))
            notice: dict[str, Any] = {"code": "mcp_unavailable", "item": label[:200], "reason": reason}
            if reason == "not_approved" and _NOTICE_INSTANCE.match(instance):
                # The admin's link opens this server's approvals.
                notice["instance"] = instance
            self._notice(notice)
        held = self._held(instance)
        if held and by_tool:
            self._notice({"code": "mcp_tool_held", "item": label[:200], "tools": held[:MAX_HELD_NAMED]})
        if instance not in self.pins and by_tool:
            self.pins[instance] = {t: a.descriptor_digest for t, a in sorted(by_tool.items())}
            self._pending_slots[PINS_SLOT] = {k: dict(v) for k, v in sorted(self.pins.items())}
        if server.whole:
            self._grow(server, by_tool)
        return by_tool

    def _grow(self, server: _Server, by_tool: Mapping[str, MCPToolAdapter]) -> None:
        """A server granted whole: the tools its first listing offered join the run, registered and in its group."""
        registry = self._registry
        if registry is None:
            return
        for tool, adapter in sorted(by_tool.items()):
            if tool in server.granted:
                continue
            server.granted[tool] = adapter.name
            try:
                registry.register_tool(adapter)
            except (DuplicateToolNameError, DuplicateToolPathError):
                del server.granted[tool]
        # A server nothing described until now is described by the tools its listing offered (R36LIVE-FU-2).
        summary = server.known.summary or tools_summary(server.granted)
        registry.register_toolset(
            server.names["group"], group_description(server.resolved.display_name, summary),
            sorted(server.granted.values()), parent=MCP_PARENT,
        )
        if summary != server.known.summary:
            self._context.tool_state.setdefault(SOURCES_STATE_KEY, {})[server.names["group"]] = {
                "name": server.resolved.display_name, "summary": summary,
            }

    def _held(self, instance: str) -> list[str]:
        from harness_core.mcp_hub.errors import ToolDescriptionFlagged

        held: list[str] = []
        for failure in self._context.mcp_tool_load_failures:
            if failure.get("instanceId") == instance and failure.get("reason") == ToolDescriptionFlagged.reason_code:
                held += [str(t) for t in failure.get("tools") or ()]
        return sorted(set(held))[:MAX_HELD_NAMED]

    def _notice(self, data: dict[str, Any]) -> None:
        from harness_core.journal.types import NewEvent

        self._pending_events.append(NewEvent(NOTICE, data, critical=False))

    def named(self, called: str) -> str | None:
        """The name the run registers the tool ``called`` stands for, among the tools its servers are known to have: the
        name itself, its server's own name for it (``create_ticket``), or the one name it ends (``…_create_ticket``)
        when exactly one tool does. ``None``: no known tool, or more than one."""
        if self.owns(called):
            return called
        # An engine's MCP client may keep its own server prefix on the name (``pipeshub__create_ticket``).
        called = called.rpartition("__")[2] or called
        if self.owns(called):
            return called
        by_tool = [named for s in self._servers.values() for tool, named in s.granted.items() if tool == called]
        if len(by_tool) == 1:
            return by_tool[0]
        ending = [n for n in self.granted_names() if n.endswith(f"_{called}")]
        return ending[0] if len(ending) == 1 and not by_tool else None

    async def prepare(self, registry: ToolRegistry, names: Iterable[str]) -> dict[str, str]:
        """A call's first use (D-LAZY-4, R27REST-FU-2): each name a call gives, whichever form it is spelled in, built
        before the call is planned or resolved, its server listed once for the attempt. A name no known tool has
        lists the servers granted whole that name no tool yet, the only place it can be. Returns each name that
        stands for another, with the name the tool runs under."""
        renamed: dict[str, str] = {}
        for called in names:
            if registry.has(called):
                continue
            name = self.named(called)
            if name is None:
                for server in [s for s in self._servers.values() if s.whole and s.listing is None]:
                    await self.listed(server)
                name = self.named(called)
            if name is None:
                continue
            await registry.materialize_many([name])
            if name != called:
                renamed[called] = name
        return renamed

    def drain(self) -> tuple[dict[str, JsonValue], tuple[NewEvent, ...]]:
        """What first use changed since the last call's outcome: journalled with the next one (the pin first)."""
        slots, events = self._pending_slots, tuple(self._pending_events)
        self._pending_slots, self._pending_events = {}, []
        return slots, events


class PinRestore:
    """A resumed attempt's servers held to the pins its run journalled at their first use (D-LAZY-5)."""

    def __init__(self, servers: LazyMcpServers) -> None:
        self._servers = servers

    async def restore(self, events: Sequence[JournalEvent]) -> None:
        pins: dict[str, dict[str, str]] = {}
        for event in events:
            if event.type != "loop.state_saved":
                continue
            slots = event.data.get("slots") if isinstance(event.data, dict) else None
            if isinstance(slots, dict):
                pins.update(pins_of(slots))
        if pins:
            self._servers.restore(pins)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def pins_of(slots: Mapping[str, Any]) -> dict[str, dict[str, str]]:
    """The journalled first-use pins in a run's slots."""
    raw = slots.get(PINS_SLOT)
    if not isinstance(raw, dict):
        return {}
    return {
        str(instance): {str(t): str(d) for t, d in tools.items() if isinstance(d, str)}
        for instance, tools in raw.items()
        if isinstance(tools, dict)
    }


__all__ = ["PINS_SLOT", "KnownCall", "LazyMcpServers", "PinRestore", "held_refusal", "notice_reason", "pins_of", "refusal_text"]
