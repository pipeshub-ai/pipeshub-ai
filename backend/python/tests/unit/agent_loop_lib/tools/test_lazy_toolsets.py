"""`tools/builtin/lazy_toolsets.py` meta-tools — grant-ceiling enforcement
on `fetch_tools`/`search_tools` (`AgentSpec.tool_names` as a permission
ceiling under `tool_disclosure="lazy"`), provider materialization-on-fetch,
and `search_tools`' global-catalog fallback + `TOOL_UNAVAILABLE` SSE
emission when nothing matches locally."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.types import Goal, ToolCall
from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.lazy_toolsets import (
    FetchToolsTool,
    ListToolsetsTool,
    SearchToolsTool,
)
from app.agent_loop_lib.tools.global_fallback import GlobalToolHit
from app.agent_loop_lib.tools.provider import EagerToolsetProvider
from app.agent_loop_lib.tools.registry import ToolRegistry


class _SimpleTool(Tool):
    def __init__(self, name: str, path: str | None = None, description: str = "") -> None:
        self._name = name
        self._path = path or f"/toolsets/test/{name}"
        self._description = description or f"{name} description"

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return self._path

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=self._name)


@dataclass
class _FakeRuntime:
    state_store: Any = None
    timeline_store: Any = None


class _FakeAgent:
    """Minimal duck-typed `AgentHandle` + the extra `observability.py`
    surface `handle()` touches (`spec`/`runtime`/`run_ctx`) — a real `Agent`
    needs a full turn-loop/scope graph neither meta-tool actually depends
    on for these tests."""

    def __init__(self, spec: AgentSpec) -> None:
        self.spec = spec
        self.runtime = _FakeRuntime()
        self.run_ctx = RunContext(role_name=spec.name, model=spec.model.model)
        self._visible_tools: set[str] | None = None
        self.emitted: list[tuple[EventType, dict]] = []

    @property
    def visible_tools(self) -> set[str] | None:
        return self._visible_tools

    @visible_tools.setter
    def visible_tools(self, value: set[str] | None) -> None:
        self._visible_tools = value

    async def emit(self, event_type: EventType, payload: dict) -> None:
        self.emitted.append((event_type, payload))

    def extract_text(self, msg: Any) -> str:
        return ""


@dataclass
class _FakeRouteContext:
    """Duck-typed `RouteContext`: the meta-tools only ever read `.agent`,
    `.spec`, `.goal`, `.turn_index`, `.started_at` off it."""

    agent: _FakeAgent
    spec: AgentSpec
    goal: Goal = field(default_factory=lambda: Goal(description="test goal"))
    turn_index: int = 0
    started_at: str = "2026-01-01T00:00:00+00:00"


def _registry_with_jira_and_slack() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("jira_create_issue", "jira_search_issues", "slack_send_message"):
        registry.register_tool(_SimpleTool(name))
    registry.register_toolset("jira", "Jira issue tracking.", ["jira_create_issue", "jira_search_issues"])
    registry.register_toolset("slack", "Slack messaging.", ["slack_send_message"])
    return registry


def _spec(*, tool_names: list[str], tool_disclosure: str = "lazy") -> AgentSpec:
    return AgentSpec(name="test-agent", tool_names=tool_names, tool_disclosure=tool_disclosure)


class TestFetchToolsGrantCeiling:
    async def test_eager_grant_none_allows_everything(self) -> None:
        registry = _registry_with_jira_and_slack()
        agent = _FakeAgent(_spec(tool_names=[]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = FetchToolsTool(registry)

        result = await tool.handle(ToolCall(id="c1", name="fetch_tools", arguments={"toolset": "jira"}), ctx)

        assert result.is_error is False
        assert agent.visible_tools == {"jira_create_issue", "jira_search_issues"}
        assert "denied" not in result.content

    async def test_lazy_grant_ceiling_drops_ungranted_tools_from_response(self) -> None:
        registry = _registry_with_jira_and_slack()
        # Ceiling only grants one of the two Jira tools.
        agent = _FakeAgent(_spec(tool_names=["jira_create_issue"]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = FetchToolsTool(registry)

        result = await tool.handle(ToolCall(id="c1", name="fetch_tools", arguments={"toolset": "jira"}), ctx)

        assert agent.visible_tools == {"jira_create_issue"}
        returned_names = {t["name"] for t in result.content["tools"]}
        assert returned_names == {"jira_create_issue"}
        assert result.content["denied"] == {
            "reason": "not_granted_to_this_agent", "tools": ["jira_search_issues"],
        }

    async def test_lazy_grant_ceiling_can_deny_the_whole_toolset(self) -> None:
        registry = _registry_with_jira_and_slack()
        agent = _FakeAgent(_spec(tool_names=["slack_send_message"]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = FetchToolsTool(registry)

        result = await tool.handle(ToolCall(id="c1", name="fetch_tools", arguments={"toolset": "jira"}), ctx)

        assert agent.visible_tools is None  # nothing allowed -> never grown
        assert result.content["tools"] == []
        assert set(result.content["denied"]["tools"]) == {"jira_create_issue", "jira_search_issues"}

    async def test_provider_backed_toolset_is_materialized_before_fetch_returns(self) -> None:
        registry = ToolRegistry()
        mcp_tool = _SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")
        registry.register_toolset_provider(EagerToolsetProvider("jira_mcp", "Jira via MCP.", [mcp_tool]))
        agent = _FakeAgent(_spec(tool_names=[]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = FetchToolsTool(registry)

        assert not registry.is_materialized("mcp_create_issue")
        result = await tool.handle(ToolCall(id="c1", name="fetch_tools", arguments={"toolset": "jira_mcp"}), ctx)

        assert registry.is_materialized("mcp_create_issue")
        assert [t["name"] for t in result.content["tools"]] == ["mcp_create_issue"]
        assert agent.visible_tools == {"mcp_create_issue"}


class TestSearchToolsGrantCeiling:
    async def test_lazy_grant_ceiling_filters_matches_and_reports_denied(self) -> None:
        registry = _registry_with_jira_and_slack()
        agent = _FakeAgent(_spec(tool_names=["jira_create_issue"]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = SearchToolsTool(registry)

        result = await tool.handle(ToolCall(id="c1", name="search_tools", arguments={"query": "jira issue"}), ctx)

        matched_names = {m["name"] for m in result.content["matches"]}
        assert matched_names == {"jira_create_issue"}
        assert result.content["denied"] == {
            "reason": "not_granted_to_this_agent", "tools": ["jira_search_issues"],
        }
        assert agent.visible_tools == {"jira_create_issue"}

    async def test_eager_grant_none_makes_matches_visible(self) -> None:
        registry = _registry_with_jira_and_slack()
        agent = _FakeAgent(_spec(tool_names=[]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = SearchToolsTool(registry)

        result = await tool.handle(ToolCall(id="c1", name="search_tools", arguments={"query": "jira issue"}), ctx)

        assert result.content["matches"]
        matched_names = {m["name"] for m in result.content["matches"]}
        assert matched_names.issubset(agent.visible_tools)


class _FakeGlobalFallback:
    def __init__(self, hits: list[GlobalToolHit]) -> None:
        self._hits = hits
        self.queries: list[str] = []

    async def search(self, query: str, limit: int) -> list[GlobalToolHit]:
        self.queries.append(query)
        return self._hits[:limit]


class TestSearchToolsGlobalFallback:
    async def test_local_match_never_consults_global_fallback(self) -> None:
        registry = _registry_with_jira_and_slack()
        fallback = _FakeGlobalFallback([GlobalToolHit(name="confluence_search", toolset="confluence", description="x")])
        tool = SearchToolsTool(registry, global_fallback=fallback)

        result = await tool.execute(query="jira issue", limit=5)

        assert fallback.queries == []
        assert "unavailable" not in result.data

    async def test_zero_local_hits_falls_back_to_global_catalog(self) -> None:
        registry = _registry_with_jira_and_slack()
        hit = GlobalToolHit(name="confluence_search_pages", toolset="confluence", description="Search Confluence pages")
        fallback = _FakeGlobalFallback([hit])
        tool = SearchToolsTool(registry, global_fallback=fallback)

        result = await tool.execute(query="confluence wiki page", limit=5)

        assert fallback.queries == ["confluence wiki page"]
        assert result.data["matches"] == []
        assert result.data["unavailable"] == [
            {"name": "confluence_search_pages", "toolset": "confluence",
             "description": "Search Confluence pages", "reason": "not_attached"},
        ]

    async def test_no_fallback_configured_zero_hits_returns_plain_empty_result(self) -> None:
        registry = _registry_with_jira_and_slack()
        tool = SearchToolsTool(registry)

        result = await tool.execute(query="nothing matches this at all", limit=5)

        assert result.data["matches"] == []
        assert "unavailable" not in result.data

    async def test_handle_emits_tool_unavailable_sse_event_per_hit(self) -> None:
        registry = _registry_with_jira_and_slack()
        hit = GlobalToolHit(name="confluence_search_pages", toolset="confluence", description="Search Confluence pages")
        fallback = _FakeGlobalFallback([hit])
        agent = _FakeAgent(_spec(tool_names=[]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = SearchToolsTool(registry, global_fallback=fallback)

        result = await tool.handle(
            ToolCall(id="c1", name="search_tools", arguments={"query": "confluence wiki page"}), ctx,
        )

        assert result.content["unavailable"] == [
            {"name": "confluence_search_pages", "toolset": "confluence",
             "description": "Search Confluence pages", "reason": "not_attached"},
        ]
        unavailable_events = [payload for event_type, payload in agent.emitted if event_type == EventType.TOOL_UNAVAILABLE]
        assert unavailable_events == [{
            "tool": "confluence_search_pages", "toolset": "confluence",
            "reason": "not_attached", "message": "Search Confluence pages",
        }]


class _SlowMaterializeProvider:
    """A provider-backed toolset whose `materialize()` has a genuine
    `await` point, gated by an `asyncio.Event` — lets a test force two
    `fetch_tools`/`search_tools` calls to have their `agent.visible_tools`
    update land in an interleaved order, exercising the `|=`-not-stale-
    snapshot fix (fix-registry-races)."""

    def __init__(self, name: str, tools: list[Tool]) -> None:
        from app.agent_loop_lib.tools.provider import ToolsetSummary

        self._summary = ToolsetSummary(name=name, description=f"{name} desc")
        self._tools_by_name = {tool.name: tool for tool in tools}
        self._release = asyncio.Event()

    def summary(self):
        return self._summary

    def list_tools(self):
        return [tool.to_summary() for tool in self._tools_by_name.values()]

    def release(self) -> None:
        self._release.set()

    async def materialize(self, name: str):
        await self._release.wait()
        return self._tools_by_name[name]


class TestConcurrentVisibilityGrowth:
    async def test_two_concurrent_fetch_tools_calls_both_land_in_visible_tools(self) -> None:
        registry = ToolRegistry()
        jira_provider = _SlowMaterializeProvider("jira_mcp", [_SimpleTool("jira_create_issue", "/mcp/jira/create")])
        slack_provider = _SlowMaterializeProvider("slack_mcp", [_SimpleTool("slack_send_message", "/mcp/slack/send")])
        registry.register_toolset_provider(jira_provider)
        registry.register_toolset_provider(slack_provider)
        agent = _FakeAgent(_spec(tool_names=[]))
        ctx = _FakeRouteContext(agent=agent, spec=agent.spec)
        tool = FetchToolsTool(registry)

        task_jira = asyncio.create_task(
            tool.handle(ToolCall(id="c1", name="fetch_tools", arguments={"toolset": "jira_mcp"}), ctx)
        )
        task_slack = asyncio.create_task(
            tool.handle(ToolCall(id="c2", name="fetch_tools", arguments={"toolset": "slack_mcp"}), ctx)
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        jira_provider.release()
        slack_provider.release()
        await asyncio.gather(task_jira, task_slack)

        assert agent.visible_tools == {"jira_create_issue", "slack_send_message"}


class TestListToolsetsTool:
    async def test_no_toolset_argument_returns_top_level_overview(self) -> None:
        registry = _registry_with_jira_and_slack()
        tool = ListToolsetsTool(registry)
        result = await tool.execute()
        names = {entry["name"] for entry in result.data["toolsets"]}
        assert names == {"jira", "slack"}

    async def test_toolset_argument_drills_into_per_tool_descriptions(self) -> None:
        registry = _registry_with_jira_and_slack()
        tool = ListToolsetsTool(registry)
        result = await tool.execute(toolset="jira")
        assert {t["name"] for t in result.data["tools"]} == {"jira_create_issue", "jira_search_issues"}

    async def test_unknown_toolset_returns_error_with_overview(self) -> None:
        registry = _registry_with_jira_and_slack()
        tool = ListToolsetsTool(registry)
        result = await tool.execute(toolset="does_not_exist")
        assert "error" in result.data
        assert result.data["toolsets"]
