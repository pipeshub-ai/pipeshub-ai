"""The tool list an agent would send, read before its run starts (C5.01 lazy disclosure).

Request builders and the prompt-cache checks ask for a turn's tool list from an agent that has not run: its run
scope does not exist yet, so nothing it is told to remember is kept, and the lazy path must still answer.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput
from app.agent_loop_lib.tools.registry import ToolRegistry


def _tool(tool_name: str) -> Tool:
    class _T(Tool):
        name = tool_name
        short_description = description = f"tool {tool_name}"
        path = f"/toolsets/t/{tool_name}"
        parameters = []

        async def execute(self, **_: object) -> ToolOutput:
            return ToolOutput(success=True, data="")

    return _T()


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("search", "fetch_tools", "jira_create", "jira_find"):
        registry.register_tool(_tool(name))
    registry.register_toolset("jira", "Jira", ["jira_create", "jira_find"])
    return registry


def _spec(disclosure: str) -> AgentSpec:
    return AgentSpec(
        name="a", system_prompt="s", model=ModelSpec(provider="x", model="y"),
        tool_names=["search", "fetch_tools", "jira_create", "jira_find"], tool_disclosure=disclosure,
    )


def test_c5_01_a_lazy_agent_that_has_not_run_lists_its_essentials() -> None:
    spec = _spec("lazy")
    agent = Agent(spec, AgentRuntime(tool_registry=_registry()))
    assert agent.scope is None
    assert [s.name for s in tool_schemas_for_turn(agent, spec, agent.runtime)] == ["fetch_tools", "search"]


def test_c5_01_an_eager_agent_that_has_not_run_lists_its_grant() -> None:
    spec = _spec("eager")
    agent = Agent(spec, AgentRuntime(tool_registry=_registry()))
    assert {s.name for s in tool_schemas_for_turn(agent, spec, agent.runtime)} == set(spec.tool_names)
