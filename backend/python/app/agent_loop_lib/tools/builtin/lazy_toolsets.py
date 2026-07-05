from __future__ import annotations

import re
from typing import Any

from app.agent_loop_lib.agent import observability as obs
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.core.types import ToolResult as CoreToolResult
from app.agent_loop_lib.tools.base import (
    ParameterType,
    Tool,
    ToolOutput,
    ToolParameter,
    ToolSummary,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.special_route import RouteContext


class ListToolsetsTool(Tool):
    """Meta-tool: overview of every registered capability group, without
    paying the token cost of every tool's full schema. Pairs with
    `fetch_tools` — the visibility side effect of fetch_tools is applied in
    agent/tool_loop.py."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "list_toolsets"

    @property
    def short_description(self) -> str:
        return "List available capability groups (toolsets)."

    @property
    def description(self) -> str:
        return (
            "List available capability groups (toolsets) with a one-line "
            "overview of each. Call fetch_tools(toolset) to load the actual "
            "tool schemas for one you need."
        )

    @property
    def path(self) -> str:
        return "/toolsets/builtin/list_toolsets"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data={"toolsets": self._registry.toolset_overview()})


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
            "making those tools callable on subsequent turns."
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
            ToolParameter(
                name="query",
                type=ParameterType.STRING,
                description="Optional hint narrowing which tools you need (unused for now)",
                required=False,
                default=None,
            ),
        ]

    async def handle(self, call: ToolCall, ctx: RouteContext) -> CoreToolResult:
        agent = ctx.agent
        toolset_name = call.arguments.get("toolset", "")
        names = self._registry.tools_in_toolset(toolset_name)
        if names:
            agent.visible_tools = (agent.visible_tools or set()) | set(names)
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="fetch_tools")
        await obs.append_timeline(agent, "tool_call", "Calling tool: fetch_tools", "running_tool", {"tool": "fetch_tools", "args": call.arguments})
        result = await self.execute(**call.arguments)
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content=result.data if result.success else (result.error or "fetch_tools failed"),
            is_error=not result.success,
        )

    async def execute(self, **kwargs: Any) -> ToolOutput:
        toolset: str = kwargs["toolset"]
        names = self._registry.tools_in_toolset(toolset)
        if not names:
            return ToolOutput(success=True, data={"error": f"Unknown toolset: {toolset}", "tools": []})
        schemas = [schema.model_dump(exclude_none=True) for schema in self._registry.schemas(names)]
        return ToolOutput(success=True, data={"toolset": toolset, "tools": schemas})


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _score(query_tokens: set[str], summary: ToolSummary) -> float:
    """Keyword-overlap relevance score (same approach as
    `FilesystemSkillIndex`'s skill search — simple, deterministic, no LLM
    call). Matches against name (underscore-split), short_description, and
    tag key/values; name matches count extra since a query mentioning a
    tool's actual name should rank it first."""
    name_tokens = _tokenize(summary.name.replace("_", " "))
    desc_tokens = _tokenize(summary.short_description)
    tag_tokens = _tokenize(" ".join(f"{t.key} {t.value}" for t in summary.tags))
    corpus = name_tokens | desc_tokens | tag_tokens

    matched = query_tokens & corpus
    if not matched:
        return 0.0
    score = len(matched) / len(query_tokens)
    name_matched = query_tokens & name_tokens
    if name_matched:
        score += 0.5 * (len(name_matched) / len(query_tokens))
    return score


class SearchToolsTool(Tool):
    """Meta-tool: intent-driven complement to `fetch_tools` — searches EVERY
    registered tool (not just members of a toolset the caller already knows
    the name of) by matching a free-text query against each tool's
    name/short_description/tags, ranks by relevance, and (by default) grows
    `agent.visible_tools` for the matches — the same visibility side effect
    `fetch_tools` applies, so a matched tool is callable on the very next
    turn without a separate fetch_tools(toolset) round-trip."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

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
        await obs.write_state(agent, ctx.goal, "running_tool", turn_index=ctx.turn_index, started_at=ctx.started_at, current_tool="search_tools")
        await obs.append_timeline(agent, "tool_call", "Calling tool: search_tools", "running_tool", {"tool": "search_tools", "args": call.arguments})
        result = await self.execute(**call.arguments)

        made_visible: list[str] = []
        if make_visible and result.success:
            names = [m["name"] for m in result.data["matches"]]
            if names:
                current = agent.visible_tools or set()
                made_visible = [n for n in names if n not in current]
                agent.visible_tools = current | set(names)

        content: Any = result.error or "search_tools failed"
        if result.success:
            content = {**result.data, "made_visible": made_visible}
        return CoreToolResult(
            tool_call_id=call.id, name=call.name,
            content=content,
            is_error=not result.success,
        )

    async def execute(self, **kwargs: Any) -> ToolOutput:
        query: str = kwargs["query"]
        limit: int = int(kwargs.get("limit") or 5)
        matches = self._search(query, limit)
        if not matches:
            return ToolOutput(success=True, data={
                "matches": [],
                "message": "No tools matched your query. Try list_toolsets for a category overview.",
            })
        return ToolOutput(success=True, data={"matches": matches})

    def _search(self, query: str, limit: int) -> list[dict[str, Any]]:
        query_tokens = _tokenize(query)
        if not query_tokens:
            return []
        toolset_by_tool = self._toolset_membership()
        scored = [
            (_score(query_tokens, summary), summary)
            for summary in self._registry.discover()
        ]
        scored = [pair for pair in scored if pair[0] > 0]
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [
            {
                "name": summary.name,
                "description": summary.short_description,
                "toolset": toolset_by_tool.get(summary.name),
                "relevance": round(score, 3),
                "path": summary.path,
            }
            for score, summary in scored[:limit]
        ]

    def _toolset_membership(self) -> dict[str, str]:
        membership: dict[str, str] = {}
        for group in self._registry.toolsets():
            for tool_name in group.tool_names:
                membership.setdefault(tool_name, group.name)
        return membership
