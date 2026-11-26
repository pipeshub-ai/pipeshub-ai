from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tag,
    Tool,
    ToolOutput,
    ToolParameter,
)
from app.agent_loop_lib.tools.global_fallback import GlobalCatalogFallback
from app.agent_loop_lib.tools.index import KeywordToolIndex, ToolIndex
from app.agent_loop_lib.tools.registry import ToolDeferral, ToolRegistry
from app.agent_loop_lib.tools.special_route import RouteContext
from app.agent_loop_lib.tools.tags import TAG_EFFECT_READ
from harness_core.tools.call_tool import (
    CALL_TOOL,
    CALL_TOOL_DESCRIPTION,
    CALL_TOOL_INPUT_SCHEMA,
    unwrap_call,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.agent.spec import AgentSpec


def _grant_set(spec: "AgentSpec") -> set[str] | None:
    """`None` means "no ceiling" (`spec.tool_names` empty — the common,
    non-PipesHub case where a role's tools ARE the whole registry). A
    non-`None` set is the permission ceiling `fetch_tools`/`search_tools`
    must never grow `agent.visible_tools` beyond, regardless of
    `tool_disclosure` — see `AgentSpec.tool_disclosure`'s docstring."""
    return set(spec.tool_names) if spec.tool_names else None


def how_to_call(registry: ToolRegistry, grant: set[str] | None) -> str | None:
    """What a result that answers tools tells the model about calling them: through ``call_tool`` when the agent
    has it (its tool list is fixed for the chat, R4), else nothing (every tool it may call is already listed)."""
    if not registry.has(CALL_TOOL) or (grant is not None and CALL_TOOL not in grant):
        return None
    return (
        "Your tool list stays the same for this chat. Call any tool above with call_tool, passing its exact name "
        "and arguments that match its inputSchema."
    )


SEARCH_LIMIT = 5


def _limit(value: Any) -> int:
    return int(value or SEARCH_LIMIT)


def _withheld(registry: ToolRegistry, grant: set[str], denied: list[str]) -> dict[str, Any]:
    """Tools outside the grant, split into those a granted tool unlocks later
    and those this agent never gets. A model that looks up a deferred tool
    before running what unlocks it would otherwise conclude it is unavailable."""
    deferred: dict[str, ToolDeferral] = {}
    for name in denied:
        deferral = registry.deferral(name)
        if deferral is not None and deferral.unlocked_by in grant:
            deferred[name] = deferral
    withheld: dict[str, Any] = {}
    never = [name for name in denied if name not in deferred]
    if never:
        withheld["denied"] = {"reason": "not_granted_to_this_agent", "tools": never}
    if deferred:
        withheld["deferred"] = [
            {"tool": name, "available_after": d.unlocked_by, "condition": d.condition,
             "message": f"{name} is not callable yet. It becomes callable once {d.unlocked_by} {d.condition}."}
            for name, d in deferred.items()
        ]
    return withheld


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
    def tags(self) -> list[Tag]:
        return [TAG_EFFECT_READ]

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
    """Meta-tool: answers the real tool schemas of one toolset (for the model
    to read). It never changes the agent's tool list, which is fixed for the
    chat (R4): the model calls an answered tool through ``call_tool``."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "fetch_tools"

    @property
    def short_description(self) -> str:
        return "Get the tool schemas of a toolset; call its tools with call_tool."

    @property
    def description(self) -> str:
        return (
            "Get the tool schemas of a toolset named by list_toolsets. Call a "
            "tool it answers with call_tool. To narrow by intent instead of a "
            "known toolset name, use search_tools."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/fetch_tools"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_EFFECT_READ]

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
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="fetch_tools")
        await obs.append_timeline(agent, "tool_call", "Calling tool: fetch_tools", "running_tool", {"tool": "fetch_tools", "args": call.arguments})
        return await self.resolve(call, grant=_grant_set(ctx.spec))

    async def resolve(self, call: ToolCall, *, grant: set[str] | None) -> CoreToolResult:
        """The call within ``grant``: schemas of tools outside it are withheld. The durable harness calls this
        as its route."""
        toolset_name = call.arguments.get("toolset", "")
        names = self._registry.tools_in_toolset(toolset_name)
        allowed = [n for n in names if grant is None or n in grant]
        denied = [n for n in names if grant is not None and n not in grant]
        result = await self.execute(**call.arguments)
        data = result.data
        if result.success and isinstance(data, dict) and "tools" in data:
            # Ceiling enforcement: schemas for tools this agent was never granted are dropped, with a pointer
            # saying why, rather than silently answering fewer tools than the toolset has.
            data = {**data, "tools": [t for t in data["tools"] if t.get("name") in allowed]}
            if denied:
                data.update(_withheld(self._registry, grant or set(), denied))
            note = how_to_call(self._registry, grant) if data["tools"] else None
            if note:
                data["how_to_call"] = note
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
        # Builds the real Tool for any provider-backed name in this toolset (tools/provider.py), so a
        # call_tool of one resolves; every name, granted or not, since execute() does not know the grant.
        await self._registry.materialize_many(names)
        schemas = [schema.model_dump(exclude_none=True) for schema in self._registry.schemas(names)]
        return ToolOutput(success=True, data={"toolset": toolset, "tools": schemas})


class SearchToolsTool(Tool):
    """Meta-tool: intent-driven complement to `fetch_tools` — searches EVERY
    registered tool (not just members of a toolset the caller already knows
    the name of) by matching a free-text query against each tool's
    name/short_description/tags, ranks by relevance, and answers each match
    with its input schema. It never changes the agent's tool list, which is
    fixed for the chat (R4): the model calls a match through ``call_tool``.

    Ranking itself lives in `tools/index.py::ToolIndex` (default
    `KeywordToolIndex`) — this class is purely the meta-tool's orchestration
    (grant enforcement, schemas, SSE-facing shape), never
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
            "Find which tool provides a capability, across every registered tool "
            "(regardless of toolset). Matching is keyword-based against tool names "
            "and descriptions, so pass 2-6 words naming the ACTION and the "
            "SYSTEM/RESOURCE — e.g. 'search indexed knowledge', 'create jira issue', "
            "'send slack message', 'search the web'. Do NOT include the user's "
            "question, entity names, or dates, and do not prefix it with phrases like "
            "'use the ... tool to find'; every extra word lowers relevance. Results "
            "are ranked by relevance and carry each tool's input schema; call one "
            "that is not in your tool list with call_tool. Use this when you don't "
            "know which toolset (see list_toolsets) has what you need."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/search_tools"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_EFFECT_READ]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description=(
                    "Capability keywords only (action + system/resource), e.g. 'search "
                    "indexed knowledge'. Not your task or the user's question."
                ),
                required=True,
            ),
            ToolParameter(
                name="limit",
                type=ParameterType.INTEGER,
                description="Max number of results to return",
                required=False,
                default=SEARCH_LIMIT,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        agent = ctx.agent
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="search_tools")
        await obs.append_timeline(agent, "tool_call", "Calling tool: search_tools", "running_tool", {"tool": "search_tools", "args": call.arguments})
        tr, unavailable = await self.resolve(call, grant=_grant_set(ctx.spec))
        for hit in unavailable:
            await agent.emit(EventType.TOOL_UNAVAILABLE, {
                "tool": hit["name"], "toolset": hit["toolset"],
                "reason": hit["reason"], "message": hit["description"],
            })
        return tr

    async def resolve(
        self, call: ToolCall, *, grant: set[str] | None,
    ) -> tuple[CoreToolResult, list[dict[str, Any]]]:
        """The call within ``grant``: matches outside it are withheld. Returns the result and the unavailable hits
        the caller reports. The durable harness calls this as its route."""
        limit = _limit(call.arguments.get("limit"))
        # Ranked whole: a match outside the grant, or one its source refuses to build, never takes the place of one
        # that can run (DOCK-R26-FU-1).
        result = await self.execute(**{**call.arguments, "limit": max(limit, len(self._registry.discover()))})
        unavailable_hits: list[dict[str, Any]] = []
        denied: list[str] = []
        held: dict[str, str] = {}
        content: Any = result.error or "search_tools failed"
        if result.success:
            matches: list[dict[str, Any]] = []
            for match in result.data["matches"]:
                if len(matches) >= limit:
                    break
                if grant is not None and match["name"] not in grant:
                    denied.append(match["name"])
                    continue
                # A provider-backed match is built before its schema is read, so a call_tool of it resolves.
                await self._registry.materialize_many([match["name"]])
                refused = self._registry.refusal(match["name"])
                if refused is not None:
                    held[match["name"]] = refused.message
                    continue
                matches.append(match)
            names = [m["name"] for m in matches]
            schemas = {schema.name: schema.input_schema for schema in self._registry.schemas(names)}
            matches = [{**m, "inputSchema": schemas[m["name"]]} for m in matches if m["name"] in schemas]
            content = {**result.data, "matches": matches}
            note = how_to_call(self._registry, grant) if matches else None
            if note:
                content["how_to_call"] = note
            if denied:
                content.update(_withheld(self._registry, grant or set(), denied))
            if held:
                content["held"] = [{"tool": name, "message": why} for name, why in held.items()]
            unavailable = result.data.get("unavailable")
            if unavailable:
                content["unavailable"] = unavailable
                unavailable_hits = list(unavailable)
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content=content,
            is_error=not result.success,
        ), unavailable_hits

    async def execute(self, **kwargs: Any) -> ToolOutput:
        query: str = kwargs["query"]
        limit = _limit(kwargs.get("limit"))
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


class CallToolTool(Tool):
    """Meta-tool: calls a tool that is not in the agent's tool list (R4: the list is fixed for the chat). Every
    executor unwraps it into the named tool's own call before anything runs (``harness_core.tools.call_tool``),
    so grant, validation, approval and journal are that tool's; ``execute`` answers only a malformed call."""

    @property
    def name(self) -> str:
        return CALL_TOOL

    @property
    def short_description(self) -> str:
        return "Call a tool search_tools or fetch_tools answered, by name."

    @property
    def description(self) -> str:
        return CALL_TOOL_DESCRIPTION

    @property
    def path(self) -> str:
        return "/toolsets/builtin/call_tool"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_EFFECT_READ]

    @property
    def parameters(self) -> list[ToolParameter]:
        return [
            ToolParameter(name="name", type=ParameterType.STRING, description="The tool's exact name.", required=True),
            ToolParameter(
                name="arguments", type=ParameterType.OBJECT, description="The tool's arguments.", required=False,
            ),
        ]

    @property
    def raw_input_schema(self) -> dict[str, Any] | None:
        return dict(CALL_TOOL_INPUT_SCHEMA)

    async def execute(self, **kwargs: Any) -> ToolOutput:
        target = unwrap_call(kwargs)
        if isinstance(target, str):
            return ToolOutput(success=False, error=target)
        return ToolOutput(success=False, error=f"{target[0]} could not be called here; call it by its own name.")
