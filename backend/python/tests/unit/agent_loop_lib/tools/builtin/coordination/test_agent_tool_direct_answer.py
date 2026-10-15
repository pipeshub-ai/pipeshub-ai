"""`AgentTool(direct_answer_note=...)` + `DirectAnswerTool` (`agent/tool_loop.py`):
the calling model can hand a delegate's output to the user as the final answer
(`final=true`) instead of paying for a second generation that restates it.

Drives a real root `Agent` over one shared `ScriptedTransport`; the number of
model calls on that transport is the load-bearing assertion.
"""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import (
    ScriptedStep,
    ScriptedTransport,
)

_NOTE = "[DIRECT] write for the user"
_RESULT_NOTE = "[NOTE] present in full"
_CHILD_ANSWER = "Here is the complete answer [source](ref1)."


def _delegate(runtime: AgentRuntime, *, direct: bool = True) -> AgentTool:
    spec = AgentSpec(
        name="coding_agent",
        system_prompt="You are the coding agent.",
        tool_names=["task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=4,
    )
    return AgentTool(
        spec, runtime, name="coding_agent", description="Run code.",
        result_note=_RESULT_NOTE, direct_answer_note=_NOTE if direct else None,
    )


def _build(transport: ScriptedTransport, *, direct: bool = True, max_turns: int = 5) -> tuple[Agent, AgentTool]:
    registry = ToolRegistry()
    registry.register_tool(TaskCompleteTool())
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=registry)
    delegate = _delegate(runtime, direct=direct)
    registry.register_tool(delegate)
    spec = AgentSpec(
        name="root",
        system_prompt="You are the root agent.",
        tool_names=["coding_agent", "task_complete"],
        model=ModelSpec(provider="scripted", model="scripted-model"),
        loop=ReActLoop(),
        max_turns=max_turns,
    )
    return Agent(spec, runtime), delegate


def _delegate_call(call_id: str = "d1", **extra: object) -> ToolCall:
    return ToolCall(id=call_id, name="coding_agent", arguments={"goal": "compute it", **extra})


class TestSchema:
    def test_flag_off_has_no_final_parameter_and_no_description_change(self) -> None:
        tool = _delegate(AgentRuntime(), direct=False)
        assert [p.name for p in tool.parameters] == ["goal", "context"]
        assert tool.description == "Run code."

    def test_enabled_adds_optional_boolean_final(self) -> None:
        tool = _delegate(AgentRuntime())
        final = next(p for p in tool.parameters if p.name == "final")
        assert final.required is False
        assert final.type.value == "boolean"
        assert "call it alone" in tool.description.lower()


class TestFinalTrue:
    async def test_run_ends_after_the_delegate_without_another_parent_turn(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_text(_CHILD_ANSWER)  # the delegate's own turn
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert result.success is True
        assert len(transport.calls) == 2  # parent decision + child answer; no parent write-up turn
        assert result.output == _CHILD_ANSWER
        assert result.answered_by == "coding_agent"

    async def test_no_result_note_on_a_direct_answer(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_text(_CHILD_ANSWER)
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert _RESULT_NOTE not in str(result.output)
        tool_results = [tr for turn in result.turns for tr in turn.tool_results]
        assert [tr.content for tr in tool_results] == [_CHILD_ANSWER]

    async def test_child_goal_carries_the_direct_answer_note(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_text(_CHILD_ANSWER)
        agent, _ = _build(transport)

        await agent.run(Goal(description="do it"))

        child_first_message = transport.calls[1]["messages"][0]
        assert _NOTE in str(child_first_message.content)

    async def test_string_true_is_accepted(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final="true"))
        transport.add_text(_CHILD_ANSWER)
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert result.answered_by == "coding_agent"
        assert len(transport.calls) == 2

    async def test_timeline_event_is_recorded(self, monkeypatch) -> None:
        recorded: list[tuple[str, dict]] = []

        async def _spy(agent, event_type, summary, status, detail=None) -> None:
            recorded.append((event_type, detail or {}))

        from app.agent_loop_lib.agent import observability

        monkeypatch.setattr(observability, "append_timeline", _spy)
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_text(_CHILD_ANSWER)
        agent, _ = _build(transport)

        await agent.run(Goal(description="do it"))

        [(_, detail)] = [r for r in recorded if r[0] == "delegate_answered_directly"]
        assert detail["delegate"] == "coding_agent"
        assert detail["output_chars"] == len(_CHILD_ANSWER)
        assert detail["duration_s"] >= 0


class TestOrdinaryDelegation:
    async def test_final_false_lets_the_parent_write_the_answer(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=False))
        transport.add_text("child report")
        transport.add_text("parent answer")
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert len(transport.calls) == 3
        assert result.output == "parent answer"
        assert result.answered_by is None
        tool_results = [tr for turn in result.turns for tr in turn.tool_results]
        assert tool_results[0].content == f"child report\n\n{_RESULT_NOTE}"

    async def test_omitted_final_is_ordinary_delegation(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call())
        transport.add_text("child report")
        transport.add_text("parent answer")
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert len(transport.calls) == 3
        assert result.output == "parent answer"

    async def test_flag_off_ignores_final_and_never_adds_the_note(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_text("child report")
        transport.add_text("parent answer")
        agent, _ = _build(transport, direct=False)

        result = await agent.run(Goal(description="do it"))

        assert len(transport.calls) == 3
        assert result.output == "parent answer"
        assert _NOTE not in str(transport.calls[1]["messages"][0].content)

    async def test_nested_caller_cannot_hand_off(self) -> None:
        """A delegate calling another delegate is not talking to the user."""
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))  # root -> coding_agent (final)
        transport.add_tool_call(ToolCall(id="n1", name="web_agent", arguments={"goal": "look", "final": True}))
        transport.add_text("web findings")  # web_agent's turn
        transport.add_text(_CHILD_ANSWER)  # coding_agent's closing turn
        agent, delegate = _build(transport)
        registry = delegate._runtime.tool_registry
        web = AgentTool(
            AgentSpec(
                name="web_agent", system_prompt="web", tool_names=[],
                model=ModelSpec(provider="scripted", model="scripted-model"), loop=ReActLoop(), max_turns=3,
            ),
            delegate._runtime, name="web_agent", direct_answer_note=_NOTE,
        )
        registry.register_tool(web)
        delegate.spec.tool_names.append("web_agent")

        result = await agent.run(Goal(description="do it"))

        assert result.output == _CHILD_ANSWER  # coding_agent kept going after web_agent returned
        assert len(transport.calls) == 4


class TestFallbacksStayDelegation:
    async def test_child_failure_returns_control_to_the_parent(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_error(RuntimeError("boom"))
        transport.add_text("parent recovers")
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert result.success is True
        assert result.output == "parent recovers"
        assert result.answered_by is None
        tool_results = [tr for turn in result.turns for tr in turn.tool_results]
        assert tool_results[0].is_error is True

    async def test_needs_input_escalation_returns_control_to_the_parent(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_tool_call(ToolCall(
            id="t1", name="task_complete",
            arguments={"output": "partial", "needs_input": "the project key"},
        ))
        transport.add_text("parent asks the user")
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert result.output == "parent asks the user"
        assert result.answered_by is None
        tool_results = [tr for turn in result.turns for tr in turn.tool_results]
        assert "[ESCALATION]" in str(tool_results[0].content)

    async def test_empty_output_returns_control_to_the_parent(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_text("   ")  # the delegate ends its run with a blank reply
        transport.add_text("parent answers itself")
        agent, _ = _build(transport)

        result = await agent.run(Goal(description="do it"))

        assert result.output == "parent answers itself"
        assert result.answered_by is None

    async def test_no_answer_state_leaks_after_a_fallback(self) -> None:
        transport = ScriptedTransport()
        transport.add_tool_call(_delegate_call(final=True))
        transport.add_error(RuntimeError("boom"))
        transport.add_text("parent recovers")
        agent, delegate = _build(transport)

        await agent.run(Goal(description="do it"))

        assert delegate._direct_answers == {}


class TestForcedFinalAnswerTurn:
    async def test_a_delegate_call_in_the_wrap_up_turn_is_dropped_not_executed(self) -> None:
        busy = ToolCall(id="b", name="coding_agent", arguments={"goal": "again", "final": False})
        transport = ScriptedTransport(script=[
            ScriptedStep(message=AssistantMessage(tool_calls=[busy])),
            ScriptedStep(message=AssistantMessage(content="child report")),
            # Wrap-up turn after max_turns=1: the model calls the delegate again with final=true.
            ScriptedStep(message=AssistantMessage(
                content="Final text", tool_calls=[_delegate_call("late", final=True)],
            )),
        ])
        agent, _ = _build(transport, max_turns=1)

        result = await agent.run(Goal(description="do it"))

        assert result.success is True
        assert result.output == "Final text"
        assert result.answered_by is None
        assert len(transport.calls) == 3  # no extra delegate run for the dropped call
