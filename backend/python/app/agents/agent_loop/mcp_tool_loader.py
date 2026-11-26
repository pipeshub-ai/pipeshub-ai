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

from app.agent_loop_lib.tools.errors import (
    DuplicateToolNameError,
    DuplicateToolPathError,
)
from app.agents.agent_loop.connected_sources import (
    SOURCES_STATE_KEY,
    group_description,
    source_summary,
)
from app.agents.agent_loop.lazy_tools_wiring import MCP_PARENT
from app.agents.agent_loop.mcp_access import MCPAccessResolver, ResolvedMCPServer
from app.agents.agent_loop.mcp_known import remember_listing, template_description
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_sign_in import note_sign_in_needed
from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.agent_loop.tool_approvals import TOOLS_BY_PATH
from app.agents.mcp.client import typed_refusal
from app.agents.mcp.failure import classify_mcp_failure, log_discover_end
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.naming import build_namespaced_tool_name, namespace_key, unique_keys
from app.agents.mcp.service import (
    credentials_to_discovery_dict,
    instance_config_from_dict,
)
from app.harness.mcp_hub.discovery import discover_listing_for
from app.harness.mcp_hub.manifests import admit_tools
from app.harness.mcp_hub.settings import harness_surface, hub_enabled
from app.harness.tool_identity.settings import instance_tool_ids_enabled
from app.utils.logging_fields import log_event
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from harness_core.mcp_hub.errors import (
    ToolChanged,
    ToolDescriptionFlagged,
    ToolUnapproved,
)
from harness_core.mcp_hub.names import export_description, export_name
from harness_core.tool_identity import scoped_mcp_tool_name, shared_types, type_key

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Mapping
    from typing import Any

    from app.agent_loop_lib.tools.registry import ToolRegistry
    from app.agents.agent_loop.context import AgentContext
    from harness_core.mcp_hub.manifest import ToolDescriptor

logger = logging.getLogger(__name__)

DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 30.0
# Admission waits on this, under Node's 10 s access-check timeout (`harness-agent-access.ts`).
ADMISSION_DISCOVERY_TIMEOUT_SECONDS = 5.0

__all__ = [
    "ADMISSION_DISCOVERY_TIMEOUT_SECONDS",
    "DEFAULT_DISCOVERY_TIMEOUT_SECONDS",
    "MCPToolProvider",
    "NO_TOOLS_OFFERED",
    "answered_listings",
    "mcp_digests",
    "server_label",
]


def mcp_digests(registry: "ToolRegistry") -> dict[str, dict[str, str]]:
    """The MCP tools ``registry`` holds, per instance, by server-side name, with their descriptor digests."""
    digests: dict[str, dict[str, str]] = {}
    for name in registry.names():
        tool = registry.resolve_by_name(name)
        if isinstance(tool, MCPToolAdapter):
            digests.setdefault(tool.instance_id, {})[tool.tool_name] = tool.descriptor_digest
    return digests


# A server whose listing answered without one tool this load may register (none listed, none of the attached ones, or
# none at its pin; WP-84): what it offers changed, so waiting for it is no use.
NO_TOOLS_OFFERED = "mcp_no_tools_offered"
# Why no tool of a server whose listing answered was registered (``_discover_pooled``, ``_load_one``): not an outage.
_HELD_LISTING = frozenset({
    ToolChanged.reason_code, ToolUnapproved.reason_code, ToolDescriptionFlagged.reason_code, NO_TOOLS_OFFERED,
})
# How many held tools of one server a notice names.
MAX_HELD_NAMED = 20


def server_label(server: "Mapping[str, Any]") -> str:
    """The name a person knows an attached MCP server by."""
    for key in ("displayName", "name"):
        if isinstance(value := server.get(key), str) and value.strip():
            return value.strip()
    return "An MCP server"


def answered_listings(context: "AgentContext", digests: "Mapping[str, Mapping[str, str]]") -> dict[str, str]:
    """The attached servers whose listing answered on this load, by instance id, with their labels: those that
    registered a tool (``digests``), and those that registered none for what they offer (every listed tool held as
    changed or not approved, or none of the attached tools listed). One that failed or timed out did not answer."""
    held = {
        str(failure.get("instanceId"))
        for failure in context.mcp_tool_load_failures
        if failure.get("reason") in _HELD_LISTING
    }
    return {
        instance_id: server_label(server)
        for server in context.mcp_servers
        if isinstance(instance_id := server.get("instanceId"), str) and (instance_id in digests or instance_id in held)
    }


def _server_type(server: "ResolvedMCPServer") -> str:
    return server.instance.get("typeId") or server.name


def _scoped_tool_infos(server: "ResolvedMCPServer", tool_infos: list[MCPToolInfo]) -> list[MCPToolInfo]:
    """With HARNESS_INSTANCE_TOOL_IDS on (P2.2): one instance's tools under names carrying its tag,
    described with its name, so a model tells two instances of one type apart; the call still
    reaches the server's own tool name."""
    label = export_description(server.display_name, limit=64)
    return [
        info.model_copy(
            update={
                "namespaced_name": scoped_mcp_tool_name(_server_type(server), server.instance_id, info.name),
                "description": f"[{label}] {info.description or info.name}",
            }
        )
        for info in tool_infos
    ]


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


MCP_PARENT_DESCRIPTION = (
    "Attached MCP (Model Context Protocol) servers. Call list_toolsets(toolset) "
    "with one of these names for a one-line description of every tool inside "
    "it, or fetch_tools(toolset) to load the real schemas directly."
)
# `tool_state` key: per instance, the listed tools held back because they differ from the run's pin (WP-84, D-LAZY-5).
CHANGED_STATE_KEY = "_mcp_changed_since_pinned"
# `tool_state` key: per instance, the listed tools the hub's admission held, each with its reason code
# (`mcp_tool_unapproved`, `mcp_tool_changed`): a first use names that reason, not "no longer offered" (DOCK-R26-FU-1).
HELD_STATE_KEY = "_mcp_held_at_admission"


def server_names(servers: list["ResolvedMCPServer"], context: "AgentContext") -> dict[str, dict[str, str]]:
    """Each instance's tool namespace and group, as a load names them (and records them for this request)."""
    return _assign_names(servers, context)


def shared_server_types(servers: list["ResolvedMCPServer"]) -> frozenset[str]:
    """The MCP types two attached instances share (P2.2): their tools carry the instance's tag."""
    if not instance_tool_ids_enabled():
        return frozenset()
    return shared_types((_server_type(s), s.instance_id) for s in servers)


def exported_tool_name(
    server: "ResolvedMCPServer", namespace: str, tool: str, *, shared: frozenset[str], hub: bool,
) -> str:
    """The name a load registers one of a server's tools under, known without listing it (D-LAZY-3)."""
    if type_key(_server_type(server)) in shared:
        return scoped_mcp_tool_name(_server_type(server), server.instance_id, tool)
    return export_name(namespace, tool) if hub else build_namespaced_tool_name(namespace, tool)


class MCPToolProvider:
    """`load_into(registry, context)` — the MCP analog of `PipesHubToolLoader.load()`."""

    async def list_server(
        self,
        server: "ResolvedMCPServer",
        context: "AgentContext",
        names: dict[str, str],
        *,
        shared: frozenset[str] = frozenset(),
        pins: "Mapping[str, str] | None" = None,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        stored_first: bool = False,
    ) -> tuple[list[MCPToolAdapter], str | None]:
        """One server listed now, as a load lists it (approvals, scanner, ``pins``): its adapters, and why it gave
        none when it gave none (the failure's reason code). The adapters are not registered anywhere yet; the
        approval gate already knows them (``TOOLS_BY_PATH``). ``stored_first``: ``pins`` is a listing already taken
        (a parent's, R31HOT2), so the stored catalog serves it when it holds every pinned tool at its digest."""
        from app.agent_loop_lib.tools.registry import ToolRegistry

        scratch = ToolRegistry()
        before = len(context.mcp_tool_load_failures)
        with harness_surface(on=context.harness_run):
            ok = await self._load_one(
                server, scratch, context, MCPSessionManager(context), names,
                timeout_seconds=timeout_seconds, shared=shared, pins=pins or {}, stored_first=stored_first,
            )
        adapters = [t for n in scratch.names() if isinstance(t := scratch.resolve_by_name(n), MCPToolAdapter)]
        if ok:
            return adapters, None
        failures = [f for f in context.mcp_tool_load_failures[before:] if f.get("instanceId") == server.instance_id]
        return adapters, str(failures[-1].get("reason") or "error") if failures else "error"

    async def load_into(
        self,
        registry: "ToolRegistry",
        context: "AgentContext",
        *,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        pinned: "Mapping[str, Mapping[str, str]] | None" = None,
        carried: "Collection[str]" = (),
    ) -> None:
        """``pinned``: a durable claim's descriptor digests per instance, by the server's own tool name, given while
        the hub is off (no approval governs the listing then): a listed tool at another digest than its pin is held as
        changed and never registered (WP-84), as the hub holds one that moved from its approval. ``carried``: the
        instances whose pins are a listing the parent already took (D-LAZY-5): built from the stored catalog that
        listing wrote when it matches the pin, as a first use builds them, so a claim never lists them again
        (R36COVER-FU-7: the hub's pooled discovery answered from its schema cache only while that was fresh)."""
        with harness_surface(on=context.harness_run):
            await self._load_into(registry, context, timeout_seconds=timeout_seconds, pinned=pinned, carried=carried)

    async def _load_into(
        self,
        registry: "ToolRegistry",
        context: "AgentContext",
        *,
        timeout_seconds: float,
        pinned: "Mapping[str, Mapping[str, str]] | None" = None,
        carried: "Collection[str]" = (),
    ) -> None:
        resolved_servers = await MCPAccessResolver.resolve(context)
        if not resolved_servers:
            return
        pins = pinned or {}

        state_logger = context.logger or logger
        session_manager = MCPSessionManager(context)
        names = _assign_names(resolved_servers, context)
        # Two instances of one MCP type on one agent (P2.2): each instance's tools carry its tag.
        shared = (
            shared_types((_server_type(s), s.instance_id) for s in resolved_servers)
            if instance_tool_ids_enabled()
            else frozenset[str]()
        )

        outcomes = await asyncio.gather(
            *[
                self._load_one(
                    server, registry, context, session_manager, names[server.instance_id],
                    timeout_seconds=timeout_seconds, shared=shared, pins=pins.get(server.instance_id, {}),
                    stored_first=server.instance_id in carried,
                )
                for server in resolved_servers
            ],
        )
        loaded_count = sum(1 for registered in outcomes if registered)

        if loaded_count:
            registry.register_toolset(MCP_PARENT, MCP_PARENT_DESCRIPTION, [])

        log_event(
            state_logger, "mcp.load.end", servers=len(resolved_servers), servers_loaded=loaded_count,
            tools_listed=sum(outcomes), failed=len(context.mcp_tool_load_failures),
        )

    async def live_digests(
        self, context: "AgentContext", *, timeout_seconds: float = ADMISSION_DISCOVERY_TIMEOUT_SECONDS
    ) -> dict[str, dict[str, str]]:
        """What the attached instances list now, as ``mcp_digests``: what a durable run pins while no approval
        governs MCP (the hub off). An instance that fails or does not answer within ``timeout_seconds``, an OAuth
        refresh included, is absent; the ones that answered are kept."""
        digests, _ = await self.live_listing(context, timeout_seconds=timeout_seconds)
        return digests

    async def live_listing(
        self, context: "AgentContext", *, timeout_seconds: float = ADMISSION_DISCOVERY_TIMEOUT_SECONDS
    ) -> tuple[dict[str, dict[str, str]], frozenset[str]]:
        """``live_digests`` and the instances whose listing answered (``answered_listings``): under the hub, the
        tools it admits, which are the ones a claim's load registers."""
        from app.agent_loop_lib.tools.registry import ToolRegistry

        registry = ToolRegistry()
        try:
            await asyncio.wait_for(
                self.load_into(registry, context, timeout_seconds=timeout_seconds), timeout=timeout_seconds
            )
        except TimeoutError:
            (context.logger or logger).warning(
                "MCPToolProvider: MCP discovery for a run definition exceeded %ss", timeout_seconds
            )
        digests = mcp_digests(registry)
        return digests, frozenset(answered_listings(context, digests))

    async def _load_one(
        self,
        server: "ResolvedMCPServer",
        registry: "ToolRegistry",
        context: "AgentContext",
        session_manager: "MCPSessionManager",
        names: dict[str, str],
        *,
        timeout_seconds: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
        shared: frozenset[str] = frozenset(),
        pins: "Mapping[str, str] | None" = None,
        stored_first: bool = False,
    ) -> int:
        """Registers one instance's tools + group. Returns how many tools were registered.
        Never raises — any discovery/registration failure is recorded on
        `context.mcp_tool_load_failures` and results in 0."""
        state_logger = context.logger or logger

        started = time.perf_counter()

        stored = (
            await self._stored_pin(server, context, names["namespace"], pins, shared=shared)
            if stored_first and pins
            else None
        )
        if stored is not None:
            tool_infos, failure, instructions = stored, None, None
        else:
            tool_infos, failure, instructions = await self._discover(
                server, context, session_manager, names["namespace"], timeout_seconds,
            )
        failure_reason = _failure_reason(failure)
        if not tool_infos:
            # A listing that answered (an empty list, not a failure) is no outage: its server offers none of them.
            reason = failure_reason or (NO_TOOLS_OFFERED if tool_infos is not None else "error")
            entry = {"instanceId": server.instance_id, "name": server.name, "reason": reason}
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

        if type_key(_server_type(server)) in shared:
            tool_infos = _scoped_tool_infos(server, tool_infos)
        registered_names: list[str] = []
        changed: list[str] = []
        by_path = context.tool_state.setdefault(TOOLS_BY_PATH, {})
        registered_tools: list[str] = []
        for tool_info in tool_infos:
            adapter = MCPToolAdapter(server, tool_info, session_manager, context=context)
            pin = (pins or {}).get(tool_info.name)
            if pin is not None and adapter.descriptor_digest != pin:
                changed.append(tool_info.name)
                continue
            try:
                registry.register_tool(adapter)
                registered_names.append(adapter.name)
                registered_tools.append(tool_info.name)
                # How the approval gate finds the server and the tool's hints for a call.
                by_path[adapter.path] = adapter
            except (DuplicateToolNameError, DuplicateToolPathError):
                state_logger.warning("MCPToolProvider: skipping duplicate MCP tool: %s", adapter.name)

        if changed:
            state_logger.warning(
                "MCPToolProvider: MCP instance %s changed %s since the run was admitted; not registered",
                server.instance_id, sorted(changed),
            )
            context.tool_state.setdefault(CHANGED_STATE_KEY, {})[server.instance_id] = sorted(changed)
        if not registered_names and changed:
            context.mcp_tool_load_failures.append(
                {"instanceId": server.instance_id, "name": server.name, "reason": ToolChanged.reason_code}
            )
            return 0
        if not registered_names:
            context.mcp_tool_load_failures.append({
                "instanceId": server.instance_id, "name": server.name, "reason": "error",
                "error": "every discovered tool name collided with an already-registered tool",
            })
            return 0

        summary = source_summary(
            description=server.instance.get("description"),
            template=template_description(server),
            instructions=instructions, tools=registered_tools,
        )
        registry.register_toolset(
            names["group"], group_description(server.display_name, summary), registered_names, parent=MCP_PARENT,
        )
        context.tool_state.setdefault(SOURCES_STATE_KEY, {})[names["group"]] = {
            "name": server.display_name, "summary": summary,
        }
        if instructions:
            context.mcp_server_instructions.append({
                "instanceId": server.instance_id, "name": server.display_name, "instructions": instructions,
            })
        return len(registered_names)

    async def _discover(
        self,
        server: "ResolvedMCPServer",
        context: "AgentContext",
        session_manager: "MCPSessionManager",
        namespace: str,
        timeout_seconds: float,
    ) -> tuple[list[MCPToolInfo] | None, BaseException | None, str | None]:
        """The attached tools, the failure when there are none, and the server's instructions."""
        try:
            if hub_enabled():
                return await self._discover_pooled_refreshing(server, context, session_manager, namespace, timeout_seconds)
            tool_infos, instructions = await asyncio.wait_for(
                session_manager.tools(server, namespace), timeout=timeout_seconds,
            )
            return self._filter_by_attached(server, tool_infos), None, instructions
        except Exception as exc:
            return None, exc, None

    async def _stored_pin(
        self,
        server: "ResolvedMCPServer",
        context: "AgentContext",
        namespace: str,
        pins: "Mapping[str, str]",
        *,
        shared: frozenset[str],
    ) -> list[MCPToolInfo] | None:
        """R31HOT2 (ADR-OWD-07 D-LAZY-5): the pinned tools of a server a parent already listed, from this sign-in's
        stored catalog, which that listing wrote (D-LAZY-1), admitted as a listing is (approvals, the scanner, the
        attachment). Only when every pinned tool is there at its pinned digest: the digest is the trust, so a stale or
        rewritten catalog is never used, and ``None`` lists the server instead. MCP_HUB on only: with it off, main's session
        already reads the stored catalog and lists once before its first call."""
        if not hub_enabled() or context.config_service is None:
            return None
        from app.agents.mcp import tool_cache
        from app.harness.mcp_hub.discovery import tool_infos as hub_tool_infos
        from app.harness.mcp_hub.manifests import descriptor_of
        from harness_core.mcp_hub.annotations import declared_hints
        from harness_core.mcp_hub.manifest import ToolDescriptor

        config = instance_config_from_dict(server.instance)
        hit = await tool_cache.read(
            context.config_service, instance=server.instance, config=config,
            owner_id=server.owner_id, auth=server.auth,
        )
        if hit is None or not set(pins) <= {t.name for t in hit.catalog.tools}:
            return None
        raw = {
            t.name: ToolDescriptor(t.name, t.description, dict(t.input_schema), hints=declared_hints(t.annotations))
            for t in hit.catalog.tools
        }
        # Exported over the whole catalog, as its listing was, so every name is the one that listing gave.
        tool_infos = [
            info.model_copy(update={"namespaced_name": export_name(namespace, info.name)})
            for info in hub_tool_infos(config, list(raw.values()), tenant=context.org_id)
            if info.name in pins
        ]
        admitted, failure, _ = await self._admitted(server, context, tool_infos, raw)
        if failure is not None or not admitted:
            return None
        # What approvals, the scanner or the attachment hold back here they hold back from a live listing too.
        scoped = _scoped_tool_infos(server, admitted) if type_key(_server_type(server)) in shared else admitted
        if any(descriptor_of(info).digest() != pins[info.name] for info in scoped):
            return None
        return admitted

    async def _discover_pooled_refreshing(
        self, server: "ResolvedMCPServer", context: "AgentContext", session_manager: "MCPSessionManager",
        namespace: str, timeout_seconds: float,
    ) -> tuple[list[MCPToolInfo] | None, BaseException | None, str | None]:
        """The pooled listing; an OAuth server that rejects the stored access token is listed again once its tokens
        are refreshed (R36-COVER: a saved agent's, Slack's and a schedule's runs list at assembly, so an expired
        token dropped the server's tools from the run and its calls failed, where the session's own listing and
        calls refresh)."""
        try:
            return await self._discover_pooled(server, context, namespace, timeout_seconds)
        except Exception as exc:
            failure = exc
        # Retried outside the `except`: a retry failing there would carry the first error as its context.
        if not await session_manager.refresh_rejected(server, failure):
            raise failure
        return await self._discover_pooled(server, context, namespace, timeout_seconds)

    async def _discover_pooled(
        self, server: "ResolvedMCPServer", context: "AgentContext", namespace: str, timeout_seconds: float,
    ) -> tuple[list[MCPToolInfo] | None, BaseException | None, str | None]:
        """MCP_HUB on (P2.8): cached and pooled, names checked, URLs pinned, only approved tools."""
        config = instance_config_from_dict(server.instance)
        credentials = credentials_to_discovery_dict(server.instance.get("authMode", ""), server.auth)
        tool_infos, raw = await discover_listing_for(
            config, credentials, tenant=context.org_id, owner=server.owner_id,
            principal=server.credential_owner, timeout_seconds=timeout_seconds,
        )
        # The hub's export name in this turn's namespace: today's name whenever it fits 52 characters (room for an
        # engine's tool prefix), else escaped and digested, so a long name never refuses an engine run.
        tool_infos = [info.model_copy(update={"namespaced_name": export_name(namespace, info.name)}) for info in tool_infos]
        # D-LAZY-1: the listing is this sign-in's stored catalog from now on, so a later run knows the server unlisted.
        await remember_listing(context, server, [raw[info.name] for info in tool_infos if info.name in raw], None)
        return await self._admitted(server, context, tool_infos, raw)

    async def _admitted(
        self,
        server: "ResolvedMCPServer",
        context: "AgentContext",
        tool_infos: list[MCPToolInfo],
        raw: "Mapping[str, ToolDescriptor]",
    ) -> tuple[list[MCPToolInfo] | None, BaseException | None, str | None]:
        """A listing's tools as the run may have them: approved (or not held by approvals), passed by the scanner and
        attached."""
        admitted, review = await admit_tools(
            context.config_service, tool_infos, tenant=context.org_id,
            instance_id=server.instance_id, at_ms=get_epoch_timestamp_in_ms(),
            instance=server.instance, raw=raw,
        )
        attached = self._filter_by_attached(server, admitted)
        held = {
            d.name: d.reason_code for d in review.disabled
            if d.reason_code in (ToolUnapproved.reason_code, ToolChanged.reason_code)
        }
        if held:
            context.tool_state.setdefault(HELD_STATE_KEY, {})[server.instance_id] = held
        flagged = sorted(d.name for d in review.disabled if d.reason_code == ToolDescriptionFlagged.reason_code)
        if flagged:
            # D-MCP-5: the person is told which tools the description scanner held back, never left to guess.
            context.mcp_tool_load_failures.append({
                "instanceId": server.instance_id, "name": server.name,
                "reason": ToolDescriptionFlagged.reason_code, "tools": flagged[:MAX_HELD_NAMED],
            })
        if not attached and review.disabled:
            if len(flagged) == len(review.disabled):
                return None, ToolDescriptionFlagged(), None
            changed = any(d.reason_code == ToolChanged.reason_code for d in review.disabled)
            return None, (ToolChanged() if changed else ToolUnapproved()), None
        return attached, None, None

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


def _failure_reason(failure: BaseException | None) -> str | None:
    """A harness refusal's own reason code (STDIO launch refused, a tool changed or unapproved), else main's classification."""
    if failure is None:
        return None
    reason = getattr(failure, "reason_code", None)
    if isinstance(reason, str) and reason:
        return reason
    refused = typed_refusal(failure) if isinstance(failure, Exception) else None
    if refused is not None:
        return refused.reason_code
    return classify_mcp_failure(failure).value
