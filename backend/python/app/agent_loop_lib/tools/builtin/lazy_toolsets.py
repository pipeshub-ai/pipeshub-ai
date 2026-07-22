from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.global_fallback import GlobalCatalogFallback
from app.agent_loop_lib.tools.index import KeywordToolIndex, ToolIndex
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.special_route import RouteContext

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec


def _grant_set(spec: "AgentSpec") -> set[str] | None:
    """`None` means "no ceiling" (`spec.tool_names` empty — the common,
    non-PipesHub case where a role's tools ARE the whole registry). A
    non-`None` set is the permission ceiling `fetch_tools`/`search_tools`
    must never grow `agent.visible_tools` beyond, regardless of
    `tool_disclosure` — see `AgentSpec.tool_disclosure`'s docstring."""
    return set(spec.tool_names) if spec.tool_names else None


class ListToolsetsTool(Tool):
    """Meta-tool: overview of every registered capability group, without
    paying the token cost of every tool's full schema. Two tiers:
    - no `toolset` argument: the top-level tree (same content as the system
      prompt's toolset overview — useful once that section has scrolled out
      of the model's attention, or to drill into a category's children).
    - `toolset` given: one line PER TOOL inside it (name + short_description)
      — cheaper than `fetch_tools` (no full schemas/parameters), enough for
      the model to decide whether it's worth fetching.
    Pairs with `fetch_tools` — the visibility side effect of fetch_tools is
    applied in agent/tool_loop.py."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "list_toolsets"

    @property
    def short_description(self) -> str:
        return "List available capability groups (toolsets), or the tools inside one."

    @property
    def description(self) -> str:
        return (
            "List available capability groups (toolsets) with a one-line overview of "
            "each. Pass `toolset` to instead see a one-line description of every tool "
            "inside that group (cheaper than fetch_tools — no full schemas). Call "
            "fetch_tools(toolset) once you know which tool(s) you actually need."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/list_toolsets"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="toolset",
                type=ParameterType.STRING,
                description="Optional toolset name to drill into (see the top-level overview)",
                required=False,
                default=None,
            ),
        ]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        toolset_name = kwargs.get("toolset")
        if not toolset_name:
            return ToolOutput(success=True, data={"toolsets": self._registry.toolset_overview()})
        detail = self._registry.toolset_detail(toolset_name)
        if detail is None:
            return ToolOutput(success=True, data={
                "error": f"Unknown toolset: {toolset_name}",
                "toolsets": self._registry.toolset_overview(),
            })
        return ToolOutput(success=True, data=detail)


class FetchToolsTool(Tool):
    """Meta-tool: loads the real tool schemas for one toolset into the
    result (for the model to read) — `handle()` below additionally makes
    those tools *callable* from the next turn onward, by growing
    `agent.visible_tools` (mirroring how spawn_agent/clarify are
    special-cased). Calling `execute()` directly outside the agent loop
    (e.g. in a unit test) just returns the schemas; it has no other side
    effects on its own.
    """

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "fetch_tools"

    @property
    def short_description(self) -> str:
        return "Load the tool schemas for a toolset, making its tools callable."

    @property
    def description(self) -> str:
        return (
            "Load the tool schemas for a toolset named by list_toolsets, "
            "making those tools callable on subsequent turns. To narrow by "
            "intent instead of a known toolset name, use search_tools."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/fetch_tools"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="toolset",
                type=ParameterType.STRING,
                description="Toolset name, as returned by list_toolsets",
                required=True,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        agent = ctx.agent
        toolset_name = call.arguments.get("toolset", "")
        names = self._registry.tools_in_toolset(toolset_name)
        grant = _grant_set(ctx.spec)
        allowed = [n for n in names if grant is None or n in grant]
        denied = [n for n in names if grant is not None and n not in grant]
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="fetch_tools")
        await obs.append_timeline(agent, "tool_call", "Calling tool: fetch_tools", "running_tool", {"tool": "fetch_tools", "args": call.arguments})
        # `execute()` materializes every tool in the toolset (see below) —
        # run it BEFORE growing `agent.visible_tools`, since the turn loop
        # resolves a visible tool's schema synchronously next turn and has
        # no opportunity to await materialization at that point.
        result = await self.execute(**call.arguments)
        if allowed:
            # `|=` (grow in place) rather than `= stale_snapshot | allowed`
            # — a concurrent `search_tools`/`fetch_tools` call in the same
            # gathered turn could otherwise read `agent.visible_tools`
            # before this one's write lands and overwrite it, losing that
            # call's own additions.
            agent.visible_tools = agent.visible_tools or set()
            agent.visible_tools |= set(allowed)

        data = result.data
        if result.success and denied and isinstance(data, dict) and "tools" in data:
            # Ceiling enforcement: schemas for tools this agent was never
            # granted are dropped from the response — never surfaced as
            # callable, and never counted toward `agent.visible_tools`
            # above — with a `denied` pointer so the model (and, via SSE,
            # the user) understands WHY they're missing rather than
            # silently getting fewer tools than the toolset advertised.
            data = {
                **data,
                "tools": [t for t in data["tools"] if t.get("name") in allowed],
                "denied": {"reason": "not_granted_to_this_agent", "tools": denied},
            }
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content=data if result.success else (result.error or "fetch_tools failed"),
            is_error=not result.success,
        )

    async def execute(self, **kwargs: Any) -> ToolOutput:
        toolset: str = kwargs["toolset"]
        names = self._registry.tools_in_toolset(toolset)
        if not names:
            return ToolOutput(success=True, data={"error": f"Unknown toolset: {toolset}", "tools": []})
        # No-op for already-materialized/plain tools; builds the real Tool
        # for any provider-backed (e.g. future MCP) name in this toolset —
        # see tools/provider.py. Materializes every name in the toolset,
        # not just the eventually-granted ones (handle() doesn't know the
        # grant until after this returns) — an acceptable trade-off since
        # provider-backed toolsets are the exception, not the common case.
        await self._registry.materialize_many(names)
        schemas = [schema.model_dump(exclude_none=True) for schema in self._registry.schemas(names)]
        return ToolOutput(success=True, data={"toolset": toolset, "tools": schemas})


class SearchToolsTool(Tool):
    """Meta-tool: intent-driven complement to `fetch_tools` — searches EVERY
    registered tool (not just members of a toolset the caller already knows
    the name of) by matching a free-text query against each tool's
    name/short_description/tags, ranks by relevance, and (by default) grows
    `agent.visible_tools` for the matches — the same visibility side effect
    `fetch_tools` applies, so a matched tool is callable on the very next
    turn without a separate fetch_tools(toolset) round-trip.

    Ranking itself lives in `tools/index.py::ToolIndex` (default
    `KeywordToolIndex`) — this class is purely the meta-tool's orchestration
    (visibility side effects, grant enforcement, SSE-facing shape), never
    the scoring algorithm, so a different `ToolIndex` (e.g. embedding-backed)
    swaps in via the constructor without touching this file. The same index
    instance should be shared with `hooks/middleware/builtin/tool_preloading.py`
    for one deployment so both search paths rank identically.

    `global_fallback`, when given, is consulted ONLY when the local index
    has zero hits — see `tools/global_fallback.py`. A hit there means the
    tool exists (elsewhere) but isn't attached to this agent; `handle()`
    emits `EventType.TOOL_UNAVAILABLE` for each one so a streaming frontend
    can render an attach/connect prompt instead of a dead end.
    """

    def __init__(
        self,
        registry: ToolRegistry,
        index: ToolIndex | None = None,
        global_fallback: GlobalCatalogFallback | None = None,
    ) -> None:
        self._registry = registry
        self._index = index or KeywordToolIndex()
        self._global_fallback = global_fallback

    @property
    def name(self) -> str:
        return "search_tools"

    @property
    def short_description(self) -> str:
        return "Search for tools matching a described need."

    @property
    def description(self) -> str:
        return (
            "Search across every registered tool (regardless of toolset) by "
            "describing what you need, e.g. 'edit a file' or 'search the web'. "
            "Results are ranked by relevance and, by default, made callable "
            "immediately — no separate fetch_tools call needed. Use this when "
            "you don't know which toolset (see list_toolsets) has what you need."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/search_tools"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Natural-language description of the capability you need",
                required=True,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Max number of results to return",
                required=False,
                default=5,
            ),
            ToolParameter(
                name="make_visible",
                type=ParameterType.BOOLEAN,
                description="Whether matched tools become callable immediately",
                required=False,
                default=True,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        agent = ctx.agent
        make_visible = call.arguments.get("make_visible", True)
        grant = _grant_set(ctx.spec)
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="search_tools")
        await obs.append_timeline(agent, "tool_call", "Calling tool: search_tools", "running_tool", {"tool": "search_tools", "args": call.arguments})
        result = await self.execute(**call.arguments)

        made_visible: list[str] = []
        denied: list[str] = []
        content: Any = result.error or "search_tools failed"
        if result.success:
            matches = result.data["matches"]
            if grant is not None:
                denied = [m["name"] for m in matches if m["name"] not in grant]
                matches = [m for m in matches if m["name"] in grant]
            if make_visible and matches:
                names = [m["name"] for m in matches]
                # Same ordering requirement as fetch_tools.handle: any
                # provider-backed match must be materialized before it's
                # added to visible_tools, since the next turn resolves
                # schemas synchronously.
                await self._registry.materialize_many(names)
                # Same `|=`-not-stale-snapshot rationale as `FetchToolsTool.
                # handle()` above.
                agent.visible_tools = agent.visible_tools or set()
                made_visible = [n for n in names if n not in agent.visible_tools]
                agent.visible_tools |= set(names)
            content = {**result.data, "matches": matches, "made_visible": made_visible}
            if denied:
                content["denied"] = {"reason": "not_granted_to_this_agent", "tools": denied}
            unavailable = result.data.get("unavailable")
            if unavailable:
                content["unavailable"] = unavailable
                for hit in unavailable:
                    await agent.emit(EventType.TOOL_UNAVAILABLE, {
                        "tool": hit["name"], "toolset": hit["toolset"],
                        "reason": hit["reason"], "message": hit["description"],
                    })
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content=content,
            is_error=not result.success,
        )

    async def execute(self, **kwargs: Any) -> ToolOutput:
        query: str = kwargs["query"]
        limit: int = int(kwargs.get("limit") or 5)
        matches = await self._index.search(self._registry, query, limit)
        if not matches:
            unavailable = await self._search_global_fallback(query, limit)
            if not unavailable:
                return ToolOutput(success=True, data={
                    "matches": [],
                    "message": "No tools matched your query. Try list_toolsets for a category overview.",
                })
            return ToolOutput(success=True, data={
                "matches": [],
                "unavailable": unavailable,
                "message": (
                    "No attached tool matched your query, but a matching tool "
                    "exists — see `unavailable` for what to tell the user."
                ),
            })
        return ToolOutput(success=True, data={
            "matches": [
                {
                    "name": m.summary.name,
                    "description": m.summary.short_description,
                    "toolset": m.toolset,
                    "relevance": m.relevance,
                    "path": m.summary.path,
                }
                for m in matches
            ],
        })

    async def _search_global_fallback(self, query: str, limit: int) -> list[dict[str, Any]]:
        if self._global_fallback is None:
            return []
        hits = await self._global_fallback.search(query, limit)
        return [
            {"name": h.name, "toolset": h.toolset, "description": h.description, "reason": h.reason}
            for h in hits
        ]
