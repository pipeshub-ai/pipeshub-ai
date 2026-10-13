"""`AgentRuntime.run_child()` reports a child's model calls to `usage_sink`:
a child is a separate `Agent` whose `usage` no caller of `run_child()` sees,
so without it a request's sub-agent calls were missing from its usage."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.types import Goal
from tests.unit.agent_loop_lib.runtime.test_run_child_streaming import _build_runtime, _child_spec
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _transport() -> ScriptedTransport:
    transport = ScriptedTransport()
    transport.add_text("done", usage=TokenUsage(input_tokens=120, output_tokens=7, cache_read_tokens=100))
    return transport


class TestRunChildReportsUsage:
    async def test_each_child_call_reaches_the_sink_with_the_child_name(self) -> None:
        runtime = _build_runtime(_transport())
        seen: list[tuple[str, TokenUsage]] = []
        runtime.usage_sink = lambda source, usage: seen.append((source, usage))

        await runtime.run_child(_child_spec(), Goal(description="research X"), None)

        assert seen == [(
            "sub_agent:researcher",
            TokenUsage(input_tokens=120, output_tokens=7, cache_read_tokens=100),
        )]

    async def test_no_sink_reports_nothing_and_changes_nothing(self) -> None:
        runtime = _build_runtime(_transport())

        result = await runtime.run_child(_child_spec(), Goal(description="research X"), None)

        assert result.success is True

    async def test_a_failing_sink_keeps_the_child_result(self) -> None:
        runtime = _build_runtime(_transport())

        def broken(_source: str, _usage: TokenUsage) -> None:
            raise RuntimeError("accounting bug")

        runtime.usage_sink = broken

        result = await runtime.run_child(_child_spec(), Goal(description="research X"), None)

        assert result.success is True

    async def test_a_child_that_raises_still_reports_what_it_spent(self) -> None:
        runtime = _build_runtime(_transport())
        seen: list[str] = []
        runtime.usage_sink = lambda source, _usage: seen.append(source)
        original_run = Agent.run

        async def _run_then_fail(self: Agent, goal: Goal, **kwargs: object) -> object:
            await original_run(self, goal, **kwargs)
            raise RuntimeError("child crashed after its model call")

        with patch.object(Agent, "run", _run_then_fail), pytest.raises(RuntimeError):
            await runtime.run_child(_child_spec(), Goal(description="research X"), None)

        assert seen == ["sub_agent:researcher"]
