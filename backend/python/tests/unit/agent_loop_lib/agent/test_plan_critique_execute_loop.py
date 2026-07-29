"""`PlanCritiqueExecuteLoop` (`app/agent_loop_lib/agent/loops.py`) end-to-end
through a real `Agent` + `ScriptedTransport` — confirms the loop still
wires `PhaseDriver` correctly (right messages, right tool names, right
phase transitions) after the phase-looping mechanics were extracted out of
it. `PhaseDriver`'s own branch coverage (replan/retry/round-budget/no-
verdict-nudge) lives in `test_phase_driver.py` against a fake agent — this
file only needs the "everything passes" happy path, since `critique_plan`/
`verify_result`'s underlying `complete_structured()` call always reports
`passed: true` by default when scripted with no explicit data (see
`ResultCritic`/`PlanCritic`'s `raw.get("passed", True)`)."""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import PlanCritiqueExecuteLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.critique_plan import CritiquePlanTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.builtin.planning.verify_result import VerifyResultTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_STEPS = [{"id": "s1", "description": "do the thing", "domain": "generic"}]


def _build_agent(transport: ScriptedTransport, *, max_turns: int = 8) -> Agent:
    registry = ToolRegistry()
    for tool in (CreatePlanTool(), CritiquePlanTool(), VerifyResultTool(), TaskCompleteTool()):
        registry.register_tool(tool)
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="plan-critique-execute-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=PlanCritiqueExecuteLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


def _happy_path_transport() -> ScriptedTransport:
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="c1", name="create_plan", arguments={"steps": _STEPS}))
    transport.add_tool_call(ToolCall(id="c2", name="critique_plan", arguments={"plan": "1. do the thing"}))
    transport.add_text("critique's own complete_structured call consumes this slot")
    transport.add_tool_call(ToolCall(id="c3", name="verify_result", arguments={"output": "draft answer"}))
    transport.add_text("verify_result's own complete_structured call consumes this slot")
    transport.add_tool_call(ToolCall(id="c4", name="task_complete", arguments={"output": "final answer"}))
    return transport


def _user_message_texts(transport: ScriptedTransport, call_index: int) -> list[str]:
    messages = transport.calls[call_index]["messages"]
    return [str(m.content) for m in messages if m.role == "user"]


class TestHappyPath:
    async def test_run_succeeds_and_reaches_task_complete(self) -> None:
        transport = _happy_path_transport()
        agent = _build_agent(transport)

        result = await agent.run(Goal(description="g"))

        assert result.success is True
        assert result.output == "final answer"

    async def test_phase_messages_appear_in_order_with_no_replan_or_revise(self) -> None:
        transport = _happy_path_transport()
        agent = _build_agent(transport)

        await agent.run(Goal(description="g"))

        # The task_complete call (last `complete()` invocation) is the one
        # whose message history has accumulated every injected phase
        # message so far — check ordering there instead of per-call.
        texts = _user_message_texts(transport, call_index=len(transport.calls) - 1)
        joined = "\n---\n".join(texts)
        plan_idx = joined.find("Phase 1 — PLAN")
        execute_idx = joined.find("Phase 2 — EXECUTE")
        assert plan_idx != -1
        assert execute_idx != -1
        assert plan_idx < execute_idx
        assert "REPLAN" not in joined
        assert "REVISE" not in joined
        assert "FINISH" not in joined
