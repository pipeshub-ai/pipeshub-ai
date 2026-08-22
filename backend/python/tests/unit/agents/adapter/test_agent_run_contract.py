"""End-to-end contract test: a full `Agent.run(goal)` lifecycle through the
real adapter-layer pieces (`PipesHubStructuredToolAdapter`, Phase 5's hooks
via `PipesHubAgentFactory._build_hooks`), driven by `ScriptedTransport`
instead of a real LangChain model — validates that a scripted tool call
actually executes and that its result lands in
`AgentContext.tool_state["all_tool_results"]` for `RespondPipeline` to read
afterward, using only agent-loop's own `Agent`/`ReActLoop`, per the
migration plan's "bring `test_agent.py` scenarios" guidance."""

from __future__ import annotations

from langchain_core.tools import StructuredTool
from pydantic import BaseModel

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.tool_schema import ToolSchema
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

_CALC_TOOL_NAME = "calc__add"


class _AddArgs(BaseModel):
    a: int
    b: int


def _add(a: int, b: int) -> str:
    return f"sum is {a + b}"


def _make_calc_tool() -> PipesHubStructuredToolAdapter:
    structured_tool = StructuredTool.from_function(
        name="add", description="Add two numbers", args_schema=_AddArgs, func=_add,
    )
    return PipesHubStructuredToolAdapter(structured_tool, "calc", "add")


def _build_agent(context, transport: ScriptedTransport, *, max_turns: int = 15) -> Agent:
    registry = ToolRegistry()
    registry.register_tool(_make_calc_tool())

    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)

    hooks = PipesHubAgentFactory._build_hooks(context)

    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry, hooks=hooks)
    spec = AgentSpec(
        name="pipeshub-agent",
        system_prompt="You are a helpful assistant.",
        tool_names=registry.names(),
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime)


class TestScriptedToolCallLifecycle:
    async def test_successful_tool_call_then_final_text(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        context = make_context()
        transport = ScriptedTransport()
        transport.add_tool_call(ToolCall(id="call-1", name=_CALC_TOOL_NAME, arguments={"a": 3, "b": 4}))
        transport.add_text("The sum is 7.")

        agent = _build_agent(context, transport)
        result = await agent.run(Goal(description="What is 3 + 4?"))

        assert result.success is True
        assert transport.calls  # at least one complete() call was made

        results = context.tool_state.get("all_tool_results", [])
        assert len(results) == 1
        assert results[0]["status"] == "success"
        assert "sum is 7" in results[0]["result"]

    async def test_tool_error_does_not_crash_the_run(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        context = make_context()
        transport = ScriptedTransport()
        # Missing required "b" argument -> the underlying function raises.
        transport.add_tool_call(ToolCall(id="call-1", name=_CALC_TOOL_NAME, arguments={"a": 3}))
        transport.add_text("I couldn't complete that calculation.")

        agent = _build_agent(context, transport)
        result = await agent.run(Goal(description="What is 3 + ?"))

        assert result.success is True
        results = context.tool_state.get("all_tool_results", [])
        assert len(results) == 1
        assert results[0]["status"] == "error"

    async def test_repeated_failures_get_blocked_by_error_tracker(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        context = make_context()
        transport = ScriptedTransport()
        for _ in range(4):
            transport.add_tool_call(ToolCall(id="call-x", name=_CALC_TOOL_NAME, arguments={"a": 1}))
        transport.add_text("Giving up.")

        agent = _build_agent(context, transport, max_turns=6)
        await agent.run(Goal(description="Keep failing"))

        results = context.tool_state.get("all_tool_results", [])
        # 3 real failures accumulate results; the 4th call is denied by
        # ToolErrorTracker before `result_accumulation`'s POST hook runs for
        # it, so it should not add a 4th entry with a fresh execution.
        assert len(results) <= 3
        assert all(r["status"] == "error" for r in results)

    async def test_tool_schema_sent_to_transport_matches_registered_tool(self) -> None:
        from app.agent_loop_lib.core.types import Goal

        context = make_context()
        transport = ScriptedTransport()
        transport.add_text("No tools needed.")

        agent = _build_agent(context, transport)
        await agent.run(Goal(description="Just say hi"))

        assert transport.calls
        tools: list[ToolSchema] | None = transport.calls[0]["tools"]
        assert tools is not None
        assert any(t.name == _CALC_TOOL_NAME for t in tools)
