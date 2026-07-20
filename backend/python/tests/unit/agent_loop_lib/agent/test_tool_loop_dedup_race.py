"""`compute_duplicate_flags()` (`agent/tool_loop.py`): repeated `web_search`/
`web_scrape` calls within one turn must be deduplicated even when the model
issues two IDENTICAL calls in the same wave and they're dispatched
concurrently via `asyncio.gather()` in `Agent.step()`.

The dedup check used to live inline inside `execute_tool_call`, AFTER that
coroutine's own `await agent.emit(...)` — a real yield point. Two identical
calls gathered into the same wave could both reach the check before either
had added its own signature to `seen_tool_calls`, and both would execute.
This suite drives the race with a real `asyncio.sleep` inside the tool's
`execute()` (forcing the two coroutines to interleave at a real await
point) and asserts only one of the two duplicate calls actually ran.
"""

from __future__ import annotations

import asyncio

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import compute_duplicate_flags
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tag, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.tools.tags import TAG_DEDUP_EXACT
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _CountingWebSearchTool(Tool):
    """A `web_search` double that sleeps mid-execution, so two concurrently
    dispatched calls actually interleave at a real `await` point — the same
    shape of race the fix closes."""

    def __init__(self, delay: float = 0.02) -> None:
        self._delay = delay
        self.execution_count = 0

    @property
    def name(self) -> str:
        return "web_search"

    @property
    def short_description(self) -> str:
        return "Search the web"

    @property
    def description(self) -> str:
        return "Search the web for a query"

    @property
    def path(self) -> str:
        return "/toolsets/builtin/web_search"

    @property
    def tags(self) -> list[Tag]:
        return [TAG_DEDUP_EXACT]

    @property
    def parameters(self) -> list[ToolParameter]:
        from app.agent_loop_lib.tools.base import ParameterType

        return [ToolParameter(name="query", type=ParameterType.STRING, description="query")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        self.execution_count += 1
        await asyncio.sleep(self._delay)
        return ToolOutput(success=True, data=f"results for {kwargs.get('query')}")


def _build_agent(transport: ScriptedTransport, tool: Tool) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(tool)
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=5,
    )
    return Agent(spec, runtime)


class TestDuplicateWebSearchInSameWave:
    async def test_identical_parallel_calls_dedup_to_one_execution(self) -> None:
        """Two IDENTICAL `web_search` calls issued in the SAME turn (the
        same `asyncio.gather()` wave) must result in exactly one real
        execution — the second gets the duplicate-skip message instead."""
        tool = _CountingWebSearchTool(delay=0.02)
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="call-1", name="web_search", arguments={"query": "same query"}),
            ToolCall(id="call-2", name="web_search", arguments={"query": "same query"}),
        ])
        transport.add_tool_call(ToolCall(id="call-3", name="task_complete", arguments={"output": "done"}))

        agent = _build_agent(transport, tool)
        result = await agent.run(Goal(description="search twice"))

        assert result.success is True
        assert tool.execution_count == 1

        first_turn = result.turns[0]
        assert len(first_turn.tool_results) == 2
        duplicate_results = [tr for tr in first_turn.tool_results if "Duplicate call skipped" in str(tr.content)]
        real_results = [tr for tr in first_turn.tool_results if "results for" in str(tr.content)]
        assert len(duplicate_results) == 1
        assert len(real_results) == 1

    async def test_distinct_queries_both_execute(self) -> None:
        """Two DIFFERENT `web_search` calls in the same wave are not
        duplicates of each other and must both run."""
        tool = _CountingWebSearchTool(delay=0.02)
        transport = ScriptedTransport()
        transport.add_tool_calls([
            ToolCall(id="call-1", name="web_search", arguments={"query": "query a"}),
            ToolCall(id="call-2", name="web_search", arguments={"query": "query b"}),
        ])
        transport.add_tool_call(ToolCall(id="call-3", name="task_complete", arguments={"output": "done"}))

        agent = _build_agent(transport, tool)
        result = await agent.run(Goal(description="search twice, different queries"))

        assert result.success is True
        assert tool.execution_count == 2


def _dedup_registry() -> ToolRegistry:
    """A registry where `web_search`/`web_scrape` carry `TAG_DEDUP_EXACT`
    (as the real builtins do) and `some_other_tool` does not — exercises
    the tag-based dispatch `compute_duplicate_flags()` now uses instead of
    a hardcoded name tuple."""
    registry = ToolRegistry()
    registry.register_tool(_CountingWebSearchTool())

    class _OtherTool(Tool):
        @property
        def name(self) -> str:
            return "some_other_tool"

        @property
        def short_description(self) -> str:
            return "Some other tool"

        @property
        def description(self) -> str:
            return "Some other tool"

        @property
        def path(self) -> str:
            return "/toolsets/builtin/some_other_tool"

        @property
        def parameters(self) -> list[ToolParameter]:
            return []

        async def execute(self, **kwargs: object) -> ToolOutput:
            return ToolOutput(success=True, data=None)

    registry.register_tool(_OtherTool())
    return registry


class TestComputeDuplicateFlagsPrePass:
    """Unit-level coverage of the synchronous pre-pass itself — no `Agent`,
    no `asyncio.gather`, just the pure check-then-add loop."""

    def test_second_identical_call_flagged_duplicate(self) -> None:
        calls = [
            ToolCall(id="a", name="web_search", arguments={"query": "x"}),
            ToolCall(id="b", name="web_search", arguments={"query": "x"}),
        ]
        seen: set[str] = set()
        flags = compute_duplicate_flags(calls, seen, _dedup_registry())
        assert flags == {"a": False, "b": True}

    def test_seen_tool_calls_mutated_in_place_before_any_await(self) -> None:
        """The whole pass runs synchronously — by the time it returns,
        `seen_tool_calls` already reflects every call in the wave, which is
        exactly what makes the race impossible: nothing can observe a
        partially-updated set."""
        calls = [
            ToolCall(id="a", name="web_search", arguments={"query": "x"}),
            ToolCall(id="b", name="web_scrape", arguments={"url": "y"}),
        ]
        seen: set[str] = set()
        compute_duplicate_flags(calls, seen, _dedup_registry())
        assert len(seen) == 2

    def test_non_deduped_tool_names_never_flagged(self) -> None:
        calls = [
            ToolCall(id="a", name="some_other_tool", arguments={"x": 1}),
            ToolCall(id="b", name="some_other_tool", arguments={"x": 1}),
        ]
        seen: set[str] = set()
        flags = compute_duplicate_flags(calls, seen, _dedup_registry())
        assert flags == {"a": False, "b": False}
