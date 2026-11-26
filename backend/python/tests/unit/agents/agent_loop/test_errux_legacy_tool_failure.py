"""EU-6: today's loop shows a failed tool by its code, never its exception text; the model keeps the detail."""

from __future__ import annotations

from typing import Any, override
from unittest.mock import AsyncMock, MagicMock

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import EventType
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.protocol.agui_emitter import AGUIEventEmitter
from harness_core.kernel.agent.streaming import stream
from harness_core.tools.base import Tool, ToolOutput, ToolParameter
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

DETAIL = "Cannot connect to host slack.example:443 ssl:default [Connection refused]"


class _Slack(Tool):
    @property
    @override
    def name(self) -> str:
        return "slack__send_message"

    @property
    @override
    def short_description(self) -> str:
        return "send"

    @property
    @override
    def description(self) -> str:
        return "Send a Slack message."

    @property
    @override
    def path(self) -> str:
        return "/tools/slack/send_message"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return []

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        raise ConnectionRefusedError(111, DETAIL)


def _agent(transport: ScriptedTransport) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    registry.register_tool(_Slack())
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(name="a", system_prompt="s", model=ModelSpec(provider="scripted", model="m"), max_turns=4)
    return Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=registry))


async def test_eu_6_a_raising_tool_reaches_the_stream_as_its_code() -> None:
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="call-1", name="slack__send_message", arguments={}))
    transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "done"}))
    agent = _agent(transport)

    events = [e async for e in stream(agent, Goal(description="tell the team"))]

    results = [e for e in events if e.event_type == EventType.TOOL_CALL_END and e.payload.get("tool_call_id") == "call-1"]
    assert len(results) == 1
    payload = results[0].payload
    assert (payload["is_error"], payload["content"], payload["error_code"]) == (True, "", "unavailable")
    assert payload["nothing_sent"] is True, "an untagged tool is a write, as the durable intent classifies it"
    assert DETAIL not in repr(payload)

    sink = MagicMock()
    sink.write = AsyncMock(return_value=True)
    await AGUIEventEmitter(sink, thread_id="t").emit(results[0])
    frame = sink.write.await_args_list[-1].args[0]["data"]
    assert (frame["type"], frame["status"], frame["content"], frame["errorCode"], frame["nothingSent"]) == (
        "TOOL_CALL_RESULT", "failed", "", "unavailable", True,
    )

    second_request = transport.calls[1]
    assert DETAIL in repr(second_request), "the model reads the detail to recover"
