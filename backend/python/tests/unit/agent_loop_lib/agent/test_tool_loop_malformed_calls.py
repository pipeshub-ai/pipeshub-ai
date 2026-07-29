"""`execute_tool_call`'s malformed-argument short-circuit (`agent/
tool_loop.py`): a `ToolCall` carrying `MALFORMED_TOOL_CALL_ARGS_KEY` must
produce a corrective error `ToolMessage` and let the run continue — never
executed against the sentinel data, and never silently dropped (which
would make the turn look like a plain no-tool-call response and end the
run without ever actually invoking the tool the model meant to call).

In production this sentinel is attached by `app/agents/agent_loop/
converters.py::_recover_invalid_tool_call` from LangChain's own
`invalid_tool_calls` (see `test_converters.py`) — this file exercises the
loop-side handling in isolation via a directly-constructed `ToolCall`.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import (
    MALFORMED_TOOL_CALL_ARGS_KEY,
    MALFORMED_TOOL_CALL_ERROR_KEY,
    ToolCall,
)
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _build_agent(transport: ScriptedTransport, *, max_turns: int = 5) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


class TestMalformedToolCallShortCircuit:
    async def test_malformed_call_produces_corrective_error_and_continues(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="call-1", name="run_code",
            arguments={
                MALFORMED_TOOL_CALL_ARGS_KEY: '{"code": "print(1)"  BROKEN',
                MALFORMED_TOOL_CALL_ERROR_KEY: "Expecting ',' delimiter",
            },
        ))
        transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "done"}))

        agent = _build_agent(transport)
        result = await agent.run(Goal(description="do something"))

        assert result.success is True
        # The malformed call did not silently end the run as a no-tool-call
        # "success" — the transport was called a second time, and the
        # failed call produced a corrective error result instead of being
        # dropped/ignored.
        assert len(transport.calls) == 2
        first_turn = result.turns[0]
        assert len(first_turn.tool_results) == 1
        malformed_result = first_turn.tool_results[0]
        assert malformed_result.is_error is True
        assert "run_code" in malformed_result.content
        assert "valid JSON" in malformed_result.content

    async def test_malformed_call_never_reaches_the_real_tool(self) -> None:
        """A sentinel-carrying call must never be dispatched through the
        registry at all — not even a "tool not found" lookup — since
        resolving is pointless work on data that was never real arguments
        in the first place."""
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(
            id="call-1", name="does_not_exist_and_never_will",
            arguments={MALFORMED_TOOL_CALL_ARGS_KEY: "{bad", MALFORMED_TOOL_CALL_ERROR_KEY: "e"},
        ))
        transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "done"}))

        agent = _build_agent(transport)
        result = await agent.run(Goal(description="do something"))

        assert result.success is True
        malformed_result = result.turns[0].tool_results[0]
        assert malformed_result.is_error is True
        assert "Unknown tool" not in malformed_result.content
