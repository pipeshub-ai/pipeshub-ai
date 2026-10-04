"""The simpler loop shapes: single-shot helpers, self-critique between turns,
and plan-then-execute.

Real `Agent`, real planner and tools; only the LLM is scripted.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import (
    LoopStrategy,
    PlanExecuteLoop,
    ReflexionLoop,
    SingleShotLoop,
)
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.default import DefaultPlanner
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

_GOAL = Goal(description="Find the release date")


class _FlakyTool(Tool):
    """Fails on the first call, succeeds after."""

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


def _agent(
    transport: ScriptedTransport, loop: LoopStrategy, *, max_turns: int = 4, tool: Tool | None = None,
) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(tool or _FlakyTool())
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    spec = AgentSpec(
        name="loop-shape-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=loop,
        max_turns=max_turns,
    )
    return Agent(spec, AgentRuntime(transport_registry=transports, tool_registry=registry))


def _users(transport: ScriptedTransport, call_index: int) -> list[str]:
    return [m.content for m in transport.calls[call_index]["messages"] if isinstance(m, UserMessage)]


class TestSingleShot:
    async def test_one_tool_turn_then_it_ends_with_that_turns_text(self) -> None:
        tool = _FlakyTool()
        call = ToolCall(id="l", name="lookup", arguments={"q": "x"})
        transport = ScriptedTransport([ScriptedStep(message=AssistantMessage(content="Checking the index.", tool_calls=[call]))])
        agent = _agent(transport, SingleShotLoop(), tool=tool)

        result = await agent.run(_GOAL)

        assert len(transport.calls) == 1
        assert tool.calls == 1
        assert result.success is True
        assert result.output == "Checking the index."
        assert len(result.turns) == 1


class TestReflexion:
    async def test_a_failing_tool_call_triggers_a_self_critique_note(self) -> None:
        transport = (
            ScriptedTransport()
            .add_tool_call(ToolCall(id="a", name="lookup", arguments={"q": "release"}))
            .add_tool_call(ToolCall(id="b", name="lookup", arguments={"q": "release"}))
            .add_text("It ships March 3.")
        )
        agent = _agent(transport, ReflexionLoop())

        result = await agent.run(_GOAL)

        assert result.output == "It ships March 3."
        critique = [m for m in _users(transport, 1) if m.startswith("Self-critique")]
        assert critique == [
            "Self-critique: the last turn had 1 failing tool call(s) (lookup). "
            "Reconsider your approach before trying again — don't just repeat the same call."
        ]
        assert sum(m.startswith("Self-critique") for m in _users(transport, 2)) == 1

    async def test_a_custom_critic_is_used_and_max_turns_still_ends_the_run(self) -> None:
        seen: list[int] = []

        async def _critic(turn) -> str | None:
            seen.append(len(turn.tool_results))
            return None

        transport = ScriptedTransport()
        for i in range(2):
            transport.add_tool_call(ToolCall(id=f"c{i}", name="lookup", arguments={"q": "x"}))
        agent = _agent(transport, ReflexionLoop(critique_fn=_critic), max_turns=2)

        result = await agent.run(_GOAL)

        assert seen == [1, 1]
        assert result.success is False
        assert result.error == "Exceeded max_turns=2"


class TestPlanThenExecute:
    async def test_the_plan_is_pinned_into_the_conversation_before_execution(self) -> None:
        transport = ScriptedTransport().add_text("1. Search the changelog\n2. Report the date").add_text("March 3.")
        registry_transports = TransportRegistry()
        registry_transports.register("scripted", lambda: transport)
        planner = DefaultPlanner(ModelSpec(provider="scripted", model="scripted-model").resolve(registry_transports))
        agent = _agent(transport, PlanExecuteLoop(planner))

        result = await agent.run(_GOAL)

        assert result.output == "March 3."
        plan_messages = [m for m in await agent.context.messages() if getattr(m, "pinned", False)]
        assert [m.content for m in plan_messages] == [
            "## Execution Plan\n\n1. Search the changelog\n2. Report the date\n\nExecute this plan step by step."
        ]

    async def test_an_empty_plan_is_not_injected(self) -> None:
        transport = ScriptedTransport().add_text("").add_text("March 3.")
        registry_transports = TransportRegistry()
        registry_transports.register("scripted", lambda: transport)
        planner = DefaultPlanner(ModelSpec(provider="scripted", model="scripted-model").resolve(registry_transports))
        agent = _agent(transport, PlanExecuteLoop(planner))

        result = await agent.run(_GOAL)

        assert result.output == "March 3."
        assert not any(getattr(m, "pinned", False) for m in await agent.context.messages())
