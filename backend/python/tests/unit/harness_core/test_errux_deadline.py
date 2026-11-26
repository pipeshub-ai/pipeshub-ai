"""EU-13, EU-14 (QA17-O5): a dependency read on a run's tool call is bounded by the call's remaining time."""

from __future__ import annotations

import asyncio
from typing import Any, override

import pytest

from harness_core import deadline
from harness_core.deadline import (
    DEPENDENCY_READ_S,
    DependencyTimeout,
    bounded_read,
    call_deadline,
    remaining_s,
)
from harness_core.effects.failures import tool_error_code
from harness_core.kernel.core.messages import ToolCall
from harness_core.tools.base import Tool, ToolOutput, ToolParameter
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.time_bound import ToolTimeBounds

# Only a regression reaches it: an unbounded read then fails the test instead of hanging the suite.
GUARD_S = 5


async def never() -> str:
    async with asyncio.timeout(GUARD_S):
        await asyncio.Event().wait()
    raise AssertionError("the read was not bounded")


async def answer() -> str:
    return "rows"


async def test_eu_13_outside_a_call_a_read_is_unchanged() -> None:
    assert remaining_s() is None
    assert await bounded_read("graph", answer()) == "rows"


async def test_eu_13_a_read_that_never_answers_ends_at_the_call_s_remaining_time() -> None:
    with call_deadline(0.05), pytest.raises(DependencyTimeout) as raised:
        _ = await bounded_read("graph", never())
    assert raised.value.dependency == "graph"
    assert raised.value.bound_s <= 0.05
    assert tool_error_code(raised.value) == "timeout"
    assert "graph" in str(raised.value)


async def test_eu_13_the_dependency_s_own_bound_applies_when_the_call_has_longer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deadline, "DEPENDENCY_READ_S", {**DEPENDENCY_READ_S, "vector": 0.05})
    with call_deadline(10_000), pytest.raises(DependencyTimeout) as raised:
        _ = await bounded_read("vector", never())
    assert raised.value.bound_s == pytest.approx(0.05)


async def test_eu_14_a_nested_scope_never_extends_its_parent() -> None:
    with call_deadline(1.0):
        outer = remaining_s()
        with call_deadline(1_000.0):
            inner = remaining_s()
    assert outer is not None and inner is not None
    assert inner <= outer <= 1.0


class _Reads(Tool):
    seen: list[float | None]

    def __init__(self) -> None:
        self.seen = []

    @property
    @override
    def name(self) -> str:
        return "graph_lookup"

    @property
    @override
    def short_description(self) -> str:
        return "lookup"

    @property
    @override
    def description(self) -> str:
        return "Reads the graph."

    @property
    @override
    def path(self) -> str:
        return "/tools/graph/lookup"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return []

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        self.seen.append(remaining_s())
        return ToolOutput(success=True, data=await bounded_read("graph", never()))


async def test_eu_14_the_executor_s_wall_bound_is_the_call_s_deadline() -> None:
    tool = _Reads()
    registry = ToolRegistry()
    registry.register_tool(tool)
    executor = ToolExecutor(registry, bounds=ToolTimeBounds(default_wall_s=0.05, stall_s=5))

    result = await executor.call_tool(ToolCall(id="c1", name="graph_lookup", arguments={}))

    assert tool.seen[0] is not None and 0 < tool.seen[0] <= 0.05
    assert (result.is_error, result.error_code) == (True, "timeout")
