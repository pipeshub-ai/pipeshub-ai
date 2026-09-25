"""`replan` for an agent that keeps a todo list: the todos are the plan it
revises, and the plan `create_plan` stored is left as it was."""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.modules.pipeline.planner.base import STRUCTURED_PLAN_SLOT
from app.agent_loop_lib.modules.pipeline.planner.replanner import _REPLAN_SYSTEM
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.planning.create_plan import CreatePlanTool
from app.agent_loop_lib.tools.builtin.planning.replan import ReplanTool
from app.agent_loop_lib.tools.builtin.planning.todos import WriteTodosTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_STEPS = [{"id": "fetch", "description": "Look up this week's open tickets", "domain": "research"}]


class TestReplanWithTodos:
    async def test_the_todos_are_the_prior_plan_and_the_stored_plan_is_untouched(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="plan", name="create_plan", arguments={"steps": _STEPS}))
        transport.add_tool_call(ToolCall(id="todos", name="write_todos", arguments={
            "todos": [{"content": "fetch tickets", "status": "in_progress"}],
        }))
        transport.add_tool_call(ToolCall(id="replan", name="replan", arguments={"reason": "source down"}))
        transport.add_text("1. fetch: use the backup source")
        transport.add_text("Done.")
        registry = ToolRegistry()
        for tool in (CreatePlanTool(), WriteTodosTool(), ReplanTool()):
            registry.register_tool(tool)
        transports = TransportRegistry()
        transports.register("scripted", lambda: transport)
        agent = Agent(
            AgentSpec(
                name="todo-agent",
                system_prompt="You are a helpful assistant.",
                model=ModelSpec(provider="scripted", model="scripted-model"),
                loop=ReActLoop(),
                max_turns=6,
            ),
            AgentRuntime(transport_registry=transports, tool_registry=registry),
        )

        result = await agent.run(Goal(description="Summarise this week's tickets"))

        assert result.success is True
        prompt = next(str(c["messages"][0].content) for c in transport.calls if c["system"] == _REPLAN_SYSTEM)
        assert "Prior plan:\n- fetch tickets" in prompt
        assert "**fetch**" not in prompt
        stored = agent.scope.get(STRUCTURED_PLAN_SLOT)
        assert [s.id for s in stored.steps] == ["fetch"]
        assert "**fetch**" in stored.text
