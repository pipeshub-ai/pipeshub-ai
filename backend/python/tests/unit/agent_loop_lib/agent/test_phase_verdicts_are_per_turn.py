"""Gate verdicts (`critique_plan`, `verify_result`) must be read from the turn
that just ran, not from anywhere earlier in the run.

`Agent.last_tool_result(name)` searches every turn so far. `PhaseDriver` and
`IncrementalLoop` used it after each step as if it answered "did THIS turn
produce a verdict?", so one failed verdict kept being re-counted on every
later turn — even turns where the model was busy fixing the problem and
never called the gate tool again. In `planExecute` and `deep` chat modes
that meant a single failed verification told the model to "finish now with
your best answer" one turn later, and the "you called create_plan but not
critique_plan" reminder never fired after the first critique.

These tests drive a real `Agent` through `ScriptedTransport`; only the LLM
is scripted.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import (
    IncrementalLoop,
    LoopStrategy,
    PlanCritiqueExecuteLoop,
)
from app.agent_loop_lib.agent.phase_driver import PhaseDriver, tool_result_in_turn
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import AgentResult, AgentTurn, Goal, ToolResult
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import ParameterType, Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.verdict_transport import VerdictTransport

_STEPS = [{"id": "s1", "description": "look it up", "domain": "research"}]


class _LookupTool(Tool):
    @property
    def name(self) -> str:
        return "lookup"

    @property
    def short_description(self) -> str:
        return "Looks something up"

    @property
    def description(self) -> str:
        return "Looks something up"

    @property
    def path(self) -> str:
        return "/toolsets/test/lookup"

    @property
    def parameters(self) -> list[ToolParameter]:
        return [ToolParameter(name="q", type=ParameterType.STRING, description="query")]

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=f"found {kwargs['q']}")


def _agent(transport: VerdictTransport, loop: LoopStrategy, *, max_turns: int = 10) -> Agent:
    registry = ToolRegistry()
    for tool in (CreatePlanTool(), CritiquePlanTool(), VerifyResultTool(), TaskCompleteTool(), _LookupTool()):
        registry.register_tool(tool)
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry)
    spec = AgentSpec(
        name="phase-verdict-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=loop,
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


async def _injected_messages(agent: Agent) -> list[str]:
    return [
        m.content for m in await agent.context.messages()
        if m.role == "user" and getattr(m, "injected", False) and isinstance(m.content, str)
    ]


def _call(call_id: str, name: str, **arguments: object) -> ToolCall:
    return ToolCall(id=call_id, name=name, arguments=arguments)


class TestVerifyPhaseCountsOnlyFreshVerdicts:
    async def test_a_revision_turn_without_verify_result_does_not_trigger_finish(self) -> None:
        transport = VerdictTransport([
            {"passed": True},   # critique_plan
            {"passed": False},  # first verify_result
        ])
        transport.add_tool_call(_call("c1", "create_plan", steps=_STEPS))
        transport.add_tool_call(_call("c2", "critique_plan", plan="1. look it up"))
        transport.add_tool_call(_call("c3", "verify_result", output="draft"))
        transport.add_tool_call(_call("c4", "lookup", q="the missing detail"))
        transport.add_tool_call(_call("c5", "task_complete", output="final answer"))
        agent = _agent(transport, PlanCritiqueExecuteLoop(max_verify_rounds=2))

        result = await agent.run(Goal(description="g"))

        assert result.success is True
        assert result.output == "final answer"
        injected = await _injected_messages(agent)
        assert sum("Phase 3 — REPLAN" in m for m in injected) == 1
        assert not any("Phase 3 — FINISH" in m for m in injected)

    async def test_finish_still_fires_after_the_second_real_failed_verdict(self) -> None:
        transport = VerdictTransport([
            {"passed": True},
            {"passed": False},
            {"passed": False},
        ])
        transport.add_tool_call(_call("c1", "create_plan", steps=_STEPS))
        transport.add_tool_call(_call("c2", "critique_plan", plan="1. look it up"))
        transport.add_tool_call(_call("c3", "verify_result", output="draft"))
        transport.add_tool_call(_call("c4", "lookup", q="more"))
        transport.add_tool_call(_call("c5", "verify_result", output="draft 2"))
        transport.add_tool_call(_call("c6", "task_complete", output="best effort"))
        agent = _agent(transport, PlanCritiqueExecuteLoop(max_verify_rounds=2))

        result = await agent.run(Goal(description="g"))

        assert result.output == "best effort"
        injected = await _injected_messages(agent)
        assert sum("Phase 3 — REPLAN" in m for m in injected) == 1
        assert sum("Phase 3 — FINISH" in m for m in injected) == 1


class _PlanningOnlyLoop(LoopStrategy):
    """Runs just `PhaseDriver`'s planning phase with a no-verdict nudge (the
    shape `OrchestratorLoop` uses), then answers from the last turn."""

    def __init__(self) -> None:
        self.outcome = None

    async def run(self, agent: Agent, goal: Goal) -> AgentResult:
        self.outcome = await PhaseDriver(max_planning_rounds=2).run_planning_phase(
            agent, goal, agent.start_turn_index,
            planning_message="PLAN",
            replan_message="REPLAN",
            no_verdict_nudge=lambda _agent: "NUDGE: call critique_plan",
        )
        return await agent.succeed(goal, "planned")


class TestPlanningPhaseCountsOnlyFreshVerdicts:
    async def test_create_plan_only_turn_gets_the_nudge_not_another_replan(self) -> None:
        transport = VerdictTransport([{"passed": False}, {"passed": True}])
        transport.add_tool_call(_call("c1", "critique_plan", plan="1. vague"))
        transport.add_tool_call(_call("c2", "create_plan", steps=_STEPS))
        transport.add_tool_call(_call("c3", "critique_plan", plan="1. look it up"))
        loop = _PlanningOnlyLoop()
        agent = _agent(transport, loop)

        await agent.run(Goal(description="g"))

        assert await _injected_messages(agent) == ["PLAN", "REPLAN", "NUDGE: call critique_plan"]
        assert loop.outcome.passed is True
        assert loop.outcome.turn_index == 3


class TestIncrementalLoopCountsOnlyFreshVerdicts:
    async def test_execution_turns_after_a_verified_step_are_not_counted_as_steps(self) -> None:
        transport = VerdictTransport([{"passed": True}])
        transport.add_tool_call(_call("c1", "verify_result", output="step 1 done"))
        transport.add_tool_call(_call("c2", "lookup", q="step 2"))
        transport.add_tool_call(_call("c3", "lookup", q="step 2 again"))
        transport.add_tool_call(_call("c4", "task_complete", output="all done"))
        agent = _agent(transport, IncrementalLoop(max_steps=2))

        result: AgentResult = await agent.run(Goal(description="g"))

        assert result.success is True
        assert result.output == "all done"
        injected = await _injected_messages(agent)
        assert sum("That step verified" in m for m in injected) == 1


class TestToolResultInTurn:
    def test_reads_the_last_matching_result_of_that_turn_only(self) -> None:
        turn = AgentTurn(tool_results=[
            ToolResult(tool_call_id="1", name="verify_result", content={"passed": False}),
            ToolResult(tool_call_id="2", name="lookup", content="x"),
            ToolResult(tool_call_id="3", name="verify_result", content={"passed": True}),
        ])

        assert tool_result_in_turn(turn, "verify_result") == {"passed": True}
        assert tool_result_in_turn(turn, "critique_plan") is None
        assert tool_result_in_turn(None, "verify_result") is None
