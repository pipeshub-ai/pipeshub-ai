"""`_finish_after_max_turns` (`agent/loops.py`): when `max_turns` is
exhausted, a run that already produced substantive assistant text should
come back as a degraded SUCCESS carrying that text, not an opaque failure
— a slower-converging small model hits the turn cap mid-answer far more
often than a large one does, and throwing away everything it already said
is strictly worse than handing it back flagged as a partial result."""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedStep, ScriptedTransport


class _EchoTool(Tool):
    """Always-succeeds tool with no `task_complete`-like exit — lets a
    scripted run keep calling it every turn until `max_turns` is hit."""

    @property
    def name(self) -> str:
        return "echo"

    @property
    def short_description(self) -> str:
        return "Echoes text"

    @property
    def description(self) -> str:
        return "Echoes the given text back"

    @property
    def path(self) -> str:
        return "/toolsets/test/echo"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="text", type=ParameterType.STRING, description="text to echo")]

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=f"echo: {kwargs['text']}")


def _build_agent(transport: ScriptedTransport, *, max_turns: int = 3) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(_EchoTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="agent-under-test",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


class TestMaxTurnsDegradation:
    async def test_returns_degraded_success_with_last_turns_text(self) -> None:
        call = ToolCall(id="c", name="echo", arguments={"text": "hi"})
        substantive_text = (
            "I've gathered most of the relevant data; the report should cover "
            "sections on revenue, headcount, and churn, each with a short summary."
        )
        transport = ScriptedTransport(script=[
            ScriptedStep(message=AssistantMessage(content="", tool_calls=[call])),
            ScriptedStep(message=AssistantMessage(content="", tool_calls=[call])),
            ScriptedStep(message=AssistantMessage(content=substantive_text, tool_calls=[call])),
        ])

        agent = _build_agent(transport, max_turns=3)
        result = await agent.run(Goal(description="Write a report"))

        assert result.success is True
        assert result.error is None
        assert substantive_text in result.output

    async def test_still_fails_when_no_substantive_text_was_ever_produced(self) -> None:
        call = ToolCall(id="c", name="echo", arguments={"text": "hi"})
        transport = ScriptedTransport(script=[
            ScriptedStep(message=AssistantMessage(content="", tool_calls=[call])),
            ScriptedStep(message=AssistantMessage(content="", tool_calls=[call])),
            ScriptedStep(message=AssistantMessage(content="", tool_calls=[call])),
        ])

        agent = _build_agent(transport, max_turns=3)
        result = await agent.run(Goal(description="Write a report"))

        assert result.success is False
        assert "Exceeded max_turns" in result.error

    async def test_fragment_shorter_than_threshold_still_fails(self) -> None:
        call = ToolCall(id="c", name="echo", arguments={"text": "hi"})
        transport = ScriptedTransport(script=[
            ScriptedStep(message=AssistantMessage(content="", tool_calls=[call])),
            ScriptedStep(message=AssistantMessage(content="Sure!", tool_calls=[call])),
        ])

        agent = _build_agent(transport, max_turns=2)
        result = await agent.run(Goal(description="Write a report"))

        assert result.success is False
