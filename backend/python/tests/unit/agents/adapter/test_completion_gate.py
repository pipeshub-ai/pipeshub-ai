"""Tests for `app.agents.agent_loop.hooks.completion_gate` — the POST_MODEL
middleware that recovers from empty model responses (no text, no tool
calls) and gives a "not found" answer one more look. See the module
docstring for the full rationale."""

from __future__ import annotations

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, ToolCall, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.completion_gate import (
    completion_gate,
    is_abstaining_answer,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


def _make_context(**overrides) -> AgentContext:
    defaults: dict = {"org_id": "org-1", "user_id": "user-1", "user_email": "u@example.com"}
    defaults.update(overrides)
    return AgentContext(**defaults)


def _turn_scope(tool_names: list[str]) -> TurnScope:
    spec = AgentSpec(
        name="agent-under-test", system_prompt="x", tool_names=tool_names,
        model=ModelSpec(provider="scripted", model="m"),
    )
    run_scope = RunScope(
        identity=RunContext(role_name="agent-under-test", model="m"),
        spec=spec, runtime=AgentRuntime(), goal=Goal(description="g"),
    )
    return TurnScope(run=run_scope, turn_index=0)


class TestCompletionGate:
    async def test_noop_when_tool_calls_present(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content=""),
            tool_calls=[ToolCall(id="1", name="run_code", arguments={})],
            scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_nudges_on_empty_response(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content=""), scope=_turn_scope([]))
        assert ctx.recovery_message is not None
        assert isinstance(ctx.recovery_message, UserMessage)
        assert ctx.recovery_message.injected is True
        assert context.completion_gate_nudges == 1

    async def test_nudge_is_a_request_not_a_bracketed_system_command(self) -> None:
        # A bracketed "[System: ...]" note mid-run is what the provider content
        # filter rejects; the nudge must read as an ordinary request.
        ctx = await run_post_model(
            completion_gate(_make_context()), AssistantMessage(content=""), scope=_turn_scope([]),
        )
        text = ctx.recovery_message.content
        assert "[System" not in text and not text.startswith("[")
        assert "please" in text.lower()

    async def test_no_nudge_when_text_present(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The answer is 42."), scope=_turn_scope(["run_code"]),
        )
        assert ctx.recovery_message is None

    async def test_bounded_by_max_nudges(self) -> None:
        context = _make_context()
        gate = completion_gate(context, max_nudges=1)
        scope = _turn_scope(["run_code"])

        first = await run_post_model(gate, AssistantMessage(content=""), scope=scope)
        second = await run_post_model(gate, AssistantMessage(content=""), scope=scope)

        assert first.recovery_message is not None
        assert second.recovery_message is None

    async def test_skips_truncated_response(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        message = AssistantMessage(content="", truncated=True)
        ctx = await run_post_model(gate, message, scope=_turn_scope(["run_code"]))
        assert ctx.recovery_message is None

    async def test_works_without_a_scope(self) -> None:
        """Empty response nudge fires even without a TurnScope."""
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content=""))
        assert ctx.recovery_message is not None

    async def test_no_nudge_without_scope_when_text_present(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(gate, AssistantMessage(content="some text, no tool call"))
        assert ctx.recovery_message is None


_KNOWLEDGE_TOOL = "retrieval__search_internal_knowledge"
_ABSTAINING = "The record does not specify the colour of the dress."


def _knowledge_scope(*, max_turns: int = 15, tool_names: list[str] | None = None) -> TurnScope:
    scope = _turn_scope([_KNOWLEDGE_TOOL] if tool_names is None else tool_names)
    scope.run.spec.max_turns = max_turns
    return scope


class TestAbstentionNudge:
    async def test_abstaining_answer_with_turns_left_is_nudged_once(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        scope = _knowledge_scope()

        first = await run_post_model(gate, AssistantMessage(content=_ABSTAINING), turn_index=1, scope=scope)
        second = await run_post_model(gate, AssistantMessage(content=_ABSTAINING), turn_index=3, scope=scope)

        assert isinstance(first.recovery_message, UserMessage)
        assert first.recovery_message.injected is True
        assert "own record" in first.recovery_message.content
        assert second.recovery_message is None

    async def test_answer_stating_the_fact_is_not_nudged(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content="The dress was yellow, embroidered with flowers."),
            turn_index=1, scope=_knowledge_scope(),
        )
        assert ctx.recovery_message is None
        assert context.completion_gate_abstention_nudged is False

    async def test_no_nudge_when_turns_are_nearly_used_up(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content=_ABSTAINING), turn_index=12, scope=_knowledge_scope(max_turns=15),
        )
        assert ctx.recovery_message is None

    async def test_no_nudge_without_knowledge_tools(self) -> None:
        context = _make_context()
        gate = completion_gate(context)
        ctx = await run_post_model(
            gate, AssistantMessage(content=_ABSTAINING), turn_index=1,
            scope=_knowledge_scope(tool_names=["run_code"]),
        )
        assert ctx.recovery_message is None


class TestIsAbstainingAnswer:
    def test_detects_not_found_openings(self) -> None:
        for text in (
            _ABSTAINING,
            "I could not find any information about the launch date.",
            "I searched the records. However, none of them mention who approved it.",
            "The owner is not specified in the retrieved documents.",
        ):
            assert is_abstaining_answer(text), text

    def test_ignores_answers_that_lead_with_the_fact(self) -> None:
        for text in (
            "There are 4 open incidents this week.",
            "Sarah Chen owns the account. She leads the platform team. "
            "The record does not specify her start date.",
        ):
            assert not is_abstaining_answer(text), text


def _agent(transport: ScriptedTransport, context: AgentContext, *, max_turns: int) -> Agent:
    transports = TransportRegistry()
    transports.register("scripted", lambda: transport)
    runtime = AgentRuntime(transport_registry=transports, tool_registry=ToolRegistry())
    spec = AgentSpec(
        name="agent-under-test", system_prompt="x", tool_names=[_KNOWLEDGE_TOOL],
        model=ModelSpec(provider="scripted", model="m"), loop=ReActLoop(), max_turns=max_turns,
    )
    agent = Agent(spec, runtime)
    agent.runtime.hooks.on(HookEvent.POST_MODEL).use(completion_gate(context))
    return agent


class TestAbstentionNudgeInTheLoop:
    async def test_loop_continues_after_one_nudge_and_a_second_abstention_ends_the_run(self) -> None:
        transport = ScriptedTransport().add_text(_ABSTAINING).add_text("The colour is still not specified.")
        agent = _agent(transport, _make_context(), max_turns=10)

        result = await agent.run(Goal(description="What colour was the dress?"))

        assert result.success is True
        assert result.output == "The colour is still not specified."
        assert len(transport.calls) == 2
        nudges = [m for m in transport.calls[1]["messages"] if isinstance(m, UserMessage) and m.injected]
        assert len(nudges) == 1

    async def test_run_ends_on_first_abstention_when_turns_are_nearly_used_up(self) -> None:
        transport = ScriptedTransport().add_text(_ABSTAINING)
        agent = _agent(transport, _make_context(), max_turns=2)

        result = await agent.run(Goal(description="What colour was the dress?"))

        assert result.output == _ABSTAINING
        assert len(transport.calls) == 1
