"""ScriptedModel drives the legacy agent_loop_lib Agent through the adapter."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReflexionLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.exceptions import TransportError
from app.agent_loop_lib.core.messages import UserMessage
from app.agent_loop_lib.core.streaming import StreamCompleteEvent, TextDeltaEvent
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.harness.testing_adapters import ScriptedModelTransport
from harness_core.testing import ScriptedModel, parse_script

SCRIPT = """
id: flaky-lookup
turns:
  - expect: { last_user_contains: "release date" }
    reply: { tool_calls: [{ id: toolu_a, name: lookup, args: { q: release } }] }
  - expect: { tool_result_for: lookup, status: failed }
    reply: { tool_calls: [{ id: toolu_b, name: lookup, args: { q: release } }] }
  - expect: { tool_result_for: lookup, status: succeeded }
    reply: { text: "It ships March 3.", usage: { input_tokens: 50, output_tokens: 5 } }
"""


class _FlakyLookup(Tool):
    def __init__(self) -> None:
        self.calls = 0

    @property
    def name(self) -> str:
        return "lookup"

    @property
    def short_description(self) -> str:
        return "Looks it up"

    @property
    def description(self) -> str:
        return "Looks it up"

    @property
    def path(self) -> str:
        return "/toolsets/test/lookup"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="q")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        self.calls += 1
        if self.calls == 1:
            return ToolOutput(success=False, error="index not ready")
        return ToolOutput(success=True, data="March 3")


def _agent(transport: ScriptedModelTransport, tool: Tool) -> Agent:
    tools = ToolRegistry()
    tools.register_tool(tool)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="adapter-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReflexionLoop(),
        max_turns=4,
    )
    return Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=tools))


async def test_yaml_script_drives_a_real_agent_through_tool_failure_and_success() -> None:
    model = ScriptedModel(parse_script(SCRIPT))
    tool = _FlakyLookup()

    result = await _agent(ScriptedModelTransport(model), tool).run(
        Goal(description="Find the release date")
    )

    assert result.output == "It ships March 3."
    assert tool.calls == 2
    model.assert_exhausted()
    assert model.usage_total.input_tokens == 50


async def test_stream_maps_deltas_and_errors() -> None:
    model = ScriptedModel(
        parse_script(
            "turns: [{reply: {text: abcd, chunks: 2}},"
            " {reply: {error: {kind: rate_limited, retry_after_s: 1}}}]"
        )
    )
    transport = ScriptedModelTransport(model)
    events = [e async for e in transport.stream([UserMessage(content="hi")])]
    assert [e.delta for e in events if isinstance(e, TextDeltaEvent)] == ["ab", "cd"]
    assert isinstance(events[-1], StreamCompleteEvent)
    assert events[-1].response.message.text == "abcd"

    with pytest.raises(TransportError) as info:
        await transport.complete([UserMessage(content="hi")])
    assert (info.value.status_code, info.value.retryable) == (429, True)
