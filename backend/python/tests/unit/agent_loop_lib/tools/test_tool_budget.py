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


class TestFetchTools:
    async def test_a_toolset_that_would_pass_the_limit_isnt_loaded(self) -> None:
        registry = _two_big_servers()
        agent = _FakeAgent(_spec(tool_names=[]))

        first = await _fetch(registry, agent, "mcp_linear")
        second = await _fetch(registry, agent, "mcp_clickup")

        assert first.is_error is False and len(agent.visible_tools) == 68
        assert second.is_error is True
        assert second.content.startswith("Not loaded: mcp_clickup has 61 tools")
        assert "search_tools" in second.content
        assert len(agent.visible_tools) == 68

    async def test_one_that_fits_loads(self) -> None:
        registry = _two_big_servers()
        agent = _FakeAgent(_spec(tool_names=[]))
        assert (await _fetch(registry, agent, "mcp_clickup")).is_error is False
        assert len(agent.visible_tools) == 61


class TestSearchTools:
    async def test_it_makes_callable_only_what_fits(self) -> None:
        registry = _two_big_servers()
        agent = _FakeAgent(_spec(tool_names=[]))
        # 3 short of the limit; the best matches (tools 00-03) aren't loaded yet.
        agent.visible_tools = {f"mcp_linear_tool_{n:02d}" for n in range(68)} | {
            f"mcp_clickup_tool_{n:02d}" for n in range(4, 61)
        }
        assert len(agent.visible_tools) == MAX_TOOLS_PER_REQUEST - 3
        call = ToolCall(id="s1", name="search_tools", arguments={"query": "mcp_clickup task", "limit": 10})

        result = await SearchToolsTool(registry).handle(call, _FakeRouteContext(agent=agent, spec=agent.spec))

        assert len(agent.visible_tools) == MAX_TOOLS_PER_REQUEST
        assert len(result.content["made_visible"]) == 3
        assert result.content["not_loaded"]["tools"] == ["mcp_clickup_tool_03"]
        assert not set(result.content["not_loaded"]["tools"]) & agent.visible_tools


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
