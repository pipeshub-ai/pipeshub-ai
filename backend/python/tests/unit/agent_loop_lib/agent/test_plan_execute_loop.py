"""`PlanExecuteLoop` (`app/agent_loop_lib/agent/loops.py`): calls a
`Planner` once upfront and injects its raw `plan.text` as a user message
verbatim before handing off to `ReActLoop` — no phases, no parsing."""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import PlanExecuteLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.base import Plan
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class _FakePlanner:
    """A `Planner`-shaped stub returning a pre-built `Plan` — exercises
    `PlanExecuteLoop` in isolation without any real LLM planning call."""

    def __init__(self, plan: Plan) -> None:
        self._plan = plan

    async def plan(self, goal: Goal) -> Plan:
        return self._plan


def _build_agent(transport: ScriptedTransport, planner: _FakePlanner) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry)
    spec = AgentSpec(
        name="planner-agent",
        system_prompt="You are a helpful assistant.",
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=PlanExecuteLoop(planner),
        max_turns=5,
    )
    return Agent(spec, runtime)


def _scripted_single_turn() -> ScriptedTransport:
    return ScriptedTransport().add_tool_call(
        ToolCall(id="1", name="task_complete", arguments={"output": "done"}),
    )


def _user_message_texts(transport: ScriptedTransport) -> list[str]:
    messages = transport.calls[0]["messages"]
    return [str(m.content) for m in messages if m.role == "user"]


class TestPlanExecuteLoopInjection:
    async def test_injects_plan_text_verbatim_when_present(self) -> None:
        plan = Plan(
            goal=Goal(description="g"),
            text="1. Research: Gather data\n2. Write: Draft the report",
        )
        transport = _scripted_single_turn()
        agent = _build_agent(transport, _FakePlanner(plan))

        await agent.run(Goal(description="g"))

        user_texts = _user_message_texts(transport)
        assert any(plan.text in text for text in user_texts)

    async def test_empty_text_injects_nothing_extra(self) -> None:
        plan = Plan(goal=Goal(description="g"), text="")
        transport = _scripted_single_turn()
        agent = _build_agent(transport, _FakePlanner(plan))

        await agent.run(Goal(description="g"))

        user_texts = _user_message_texts(transport)
        assert not any("## Execution Plan" in text for text in user_texts)
