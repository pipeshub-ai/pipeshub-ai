"""Deep mode's fallback dispatch phase (`OrchestratorLoop`, Phase 2 without a
structured plan) must judge each turn by the `spawn_agent` calls made in
THAT turn.

The phase gate refuses `spawn_agent` during planning, and models do try it
there. The loop used to look up "the most recent spawn_agent result anywhere
in the run", so a planning-phase refusal made every later dispatch turn
without a spawn look like a failed dispatch: the model was told "all
spawn_agent calls this turn failed" when it had made none, and after two
such turns the loop moved on to verification having dispatched nothing.

Drives a real `Agent` + `AgentRuntime` with the production phase gate;
only the LLM is scripted.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.loops.orchestrator import (
    COORDINATION_TOOL_NAMES,
    OrchestratorLoop,
    domain_spec_factory,
    install_phase_gate,
    register_coordination_tools,
)
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_RETRY_MARKER = "Phase 2 -- RETRY"
_STEPS = [{"id": "s1", "description": "summarise", "domain": "analysis", "tool_names": []}]


def _build(transport: ScriptedTransport, *, max_turns: int = 10) -> Agent:
    registry = ToolRegistry()
    register_coordination_tools(registry)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(
        transport_registry=transports,
        tool_registry=registry,
        spec_factory=domain_spec_factory(
            provider="scripted", model_name="scripted-model", default_tool_names=[],
        ),
    )
    install_phase_gate(runtime.hooks)
    spec = AgentSpec(
        name="pipeshub-orchestrator",
        system_prompt="You are a deep-agent orchestrator.",
        tool_names=list(COORDINATION_TOOL_NAMES),
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=OrchestratorLoop(max_planning_rounds=1),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


def _plan_with_an_early_spawn(transport: ScriptedTransport) -> None:
    """Phase 1: an early `spawn_agent` (refused by the gate), then a passing
    critique. No structured plan is stored, so Phase 2 falls back to asking
    the model to dispatch."""
    transport.add_tool_call(ToolCall(
        id="early", name="spawn_agent",
        arguments={"role": "analysis", "goal": "jump the gun", "reasoning": "eager"},
    ))
    transport.add_tool_call(ToolCall(id="crit", name="critique_plan", arguments={"plan": "1. summarise"}))
    transport.add_text("_")  # critique_plan's complete_structured() filler


async def _injected(agent: Agent) -> list[str]:
    return [
        m.content for m in await agent.context.messages()
        if m.role == "user" and getattr(m, "injected", False) and isinstance(m.content, str)
    ]


class TestFallbackDispatchJudgesTheCurrentTurn:
    async def test_early_refused_spawn_is_really_refused(self) -> None:
        transport = ScriptedTransport()
        _plan_with_an_early_spawn(transport)
        transport.add_text("Here is the answer.")
        agent = _build(transport)

        await agent.run(Goal(description="Summarise my week"))

        early = agent.scope.turns[0].tool_results[0]
        assert early.name == "spawn_agent"
        assert early.is_error is True

    async def test_turn_without_spawn_is_not_reported_as_a_failed_dispatch(self) -> None:
        transport = ScriptedTransport()
        _plan_with_an_early_spawn(transport)
        transport.add_tool_call(ToolCall(id="replan", name="create_plan", arguments={"steps": _STEPS}))
        transport.add_text("Here is the answer.")
        agent = _build(transport)

        result = await agent.run(Goal(description="Summarise my week"))

        assert result.success is True
        assert result.output == "Here is the answer."
        assert not any(_RETRY_MARKER in m for m in await _injected(agent))

    async def test_a_failed_spawn_in_this_turn_still_gets_the_retry_nudge(self) -> None:
        transport = ScriptedTransport()
        _plan_with_an_early_spawn(transport)
        transport.add_tool_call(ToolCall(
            id="bad", name="spawn_agent",
            arguments={
                "role": "analysis", "goal": "summarise", "reasoning": "r",
                "task_id": "s2", "depends_on": ["no-such-task"],
            },
        ))
        transport.add_text("Here is the answer.")
        agent = _build(transport)

        result = await agent.run(Goal(description="Summarise my week"))

        assert result.success is True
        dispatch_turn = agent.scope.turns[2]
        assert [tr.is_error for tr in dispatch_turn.tool_results] == [True]
        assert sum(_RETRY_MARKER in m for m in await _injected(agent)) == 1
