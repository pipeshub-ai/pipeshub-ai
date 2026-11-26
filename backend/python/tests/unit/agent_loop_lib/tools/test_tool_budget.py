"""One model request carries at most `MAX_TOOLS_PER_REQUEST` tool schemas (`tools/budget.py`).

Live test: "compare my Linear issues with my ClickUp tasks" loaded both servers whole with
fetch_tools (68 + 61 tools), and OpenAI refused the request: "Invalid 'tools': array too long.
Expected an array with maximum length 128, but got an array with length 153"."""

from __future__ import annotations

from types import SimpleNamespace

from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.core.types import ToolCall
from app.agent_loop_lib.hooks.middleware.builtin.tool_preloading import _select_toolsets
from app.agent_loop_lib.tools.budget import MAX_TOOLS_PER_REQUEST
from app.agent_loop_lib.tools.builtin.lazy_toolsets import (
    FetchToolsTool,
    SearchToolsTool,
)
from app.agent_loop_lib.tools.index import ToolMatch
from app.agent_loop_lib.tools.registry import ToolRegistry
from tests.unit.agent_loop_lib.tools.test_lazy_toolsets import (
    _FakeAgent,
    _FakeRouteContext,
    _SimpleTool,
    _spec,
)


def _two_big_servers() -> ToolRegistry:
    registry = ToolRegistry()
    for group, count in (("mcp_linear", 68), ("mcp_clickup", 61)):
        names = [f"{group}_tool_{n:02d}" for n in range(count)]
        for name in names:
            registry.register_tool(_SimpleTool(name, description=f"{group} task issue tool {name}"))
        registry.register_toolset(group, f"{group} — attached MCP server tools.", names)
    registry.register_tool(_SimpleTool("zz_task_complete", description="Finish the task"))
    return registry


async def _fetch(registry: ToolRegistry, agent: _FakeAgent, toolset: str):  # noqa: ANN202
    call = ToolCall(id=f"fetch-{toolset}", name="fetch_tools", arguments={"toolset": toolset})
    return await FetchToolsTool(registry).handle(call, _FakeRouteContext(agent=agent, spec=agent.spec))


class TestFetchingNeverGrowsTheRequest:
    """R4 (recs2): fetch_tools and search_tools answer schemas and never add to the list a request carries, so
    loading both servers whole can no longer push a request past the limit (the live refusal above)."""

    async def test_both_big_toolsets_fetched_leave_the_request_as_it_was(self) -> None:
        registry = _two_big_servers()
        agent = _FakeAgent(_spec(tool_names=[]))
        spec = AgentSpec(name="a", tool_names=[], tool_disclosure="lazy")
        before = [s.name for s in tool_schemas_for_turn(agent, spec, SimpleNamespace(tool_registry=registry))]

        first = await _fetch(registry, agent, "mcp_linear")
        second = await _fetch(registry, agent, "mcp_clickup")

        assert (len(first.content["tools"]), len(second.content["tools"])) == (68, 61)
        after = [s.name for s in tool_schemas_for_turn(agent, spec, SimpleNamespace(tool_registry=registry))]
        assert after == before == ["zz_task_complete"]

    async def test_search_answers_matches_without_loading_them(self) -> None:
        registry = _two_big_servers()
        agent = _FakeAgent(_spec(tool_names=[]))
        call = ToolCall(id="s1", name="search_tools", arguments={"query": "mcp_clickup task", "limit": 10})

        result = await SearchToolsTool(registry).handle(call, _FakeRouteContext(agent=agent, spec=agent.spec))

        assert len(result.content["matches"]) == 10
        assert "made_visible" not in result.content and "not_loaded" not in result.content
        assert agent.visible_tools is None


class TestTheRequestItself:
    def test_never_carries_more_than_the_limit_and_keeps_the_essentials(self) -> None:
        registry = _two_big_servers()
        spec = AgentSpec(name="a", tool_names=[], tool_disclosure="lazy")
        agent = SimpleNamespace(visible_tools=set(registry.names()))

        schemas = tool_schemas_for_turn(agent, spec, SimpleNamespace(tool_registry=registry))

        names = [s.name for s in schemas]
        assert len(names) == MAX_TOOLS_PER_REQUEST
        # Sorted last, ungrouped: an essential the model needs to finish.
        assert "zz_task_complete" in names


class TestPreloading:
    def test_a_second_big_toolset_is_pointed_at_not_loaded(self) -> None:
        registry = _two_big_servers()
        summary = {s.name: s for s in registry.discover()}
        matches = [
            ToolMatch(summary=summary["mcp_linear_tool_00"], relevance=0.9, toolset="mcp_linear"),
            ToolMatch(summary=summary["mcp_clickup_tool_00"], relevance=0.8, toolset="mcp_clickup"),
        ]

        unlocked, pointers, unlock_names = _select_toolsets(
            registry, matches, grant=None, preload_threshold=0.75, mention_threshold=0.4, visible={"zz_task_complete"},
        )

        assert [name for name, _ in unlocked] == ["mcp_linear"]
        assert [name for name, _ in pointers] == ["mcp_clickup"]
        assert len(unlock_names) == 68
