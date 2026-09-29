"""Regression coverage for the optin-turn-guards todo: `install_turn_guards()`
must NOT install `supervisor_confidence_gate`/`stall_detection` unconditionally
— both change model-visible behavior (blocking a tool result; injecting
warning/directive messages) and should only run for roles that explicitly
opt in via `install_supervisor_confidence_gate()`/`install_stall_detection()`.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import AssistantMessage, ToolMessage, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import AgentTurn, Goal, ToolResult
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.turn_guards import (
    install_stall_detection,
    install_supervisor_confidence_gate,
    install_turn_guards,
    warn_before_deadline,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext, ToolResultContext, TurnContext
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import ToolOutput
from app.agent_loop_lib.tools.tags import TAG_PLANNING_CREATE_PLAN


def _low_confidence_create_plan_ctx() -> ToolResultContext:
    return ToolResultContext(
        tool_path="/toolsets/builtin/create_plan",
        tool_use_id=uuid4(),
        tool_response=ToolOutput(success=True, data={"plan": "x", "confidence": "low"}),
        tags=(TAG_PLANNING_CREATE_PLAN,),
    )


def _run_scope() -> RunScope:
    """Minimal `RunScope` for exercising `StateSlot` reads/writes —
    `identity`/`spec`/`runtime` are never touched by `stall_detection`."""
    return RunScope(
        identity=SimpleNamespace(), spec=SimpleNamespace(), runtime=SimpleNamespace(),
        goal=Goal(description="g"),
    )


def _error_turn_ctx(turn_scope: TurnScope) -> TurnContext:
    turn = AgentTurn(
        messages=[AssistantMessage(content="")],
        tool_results=[ToolResult(tool_call_id="c", name="t", content="boom", is_error=True)],
    )
    return TurnContext(turn_index=turn_scope.turn_index, turn=turn, scope=turn_scope)


def _model_ctx(turn_scope: TurnScope) -> ModelCallContext:
    return ModelCallContext(
        messages=[], budget=ContextBudget(max_tokens=1000), scope=turn_scope,
        turn_index=5, max_turns=20,
    )


class TestInstallTurnGuardsIsMinimal:
    @pytest.mark.asyncio
    async def test_does_not_install_supervisor_confidence_gate(self) -> None:
        kernel = HookRegistry()
        install_turn_guards(kernel)
        ctx = await kernel.on(HookEvent.POST_TOOL_USE).dispatch(_low_confidence_create_plan_ctx())
        # No supervisor gate installed -> nothing ever blocks the result.
        assert ctx.decision.value == "continue"

    @pytest.mark.asyncio
    async def test_does_not_install_stall_detection(self) -> None:
        """Without opting in, PRE_MODEL never gets a stall warning injected
        even after several consecutive error-heavy turns."""
        kernel = HookRegistry()
        install_turn_guards(kernel)
        run_scope = _run_scope()
        turn_scope = TurnScope(run=run_scope, turn_index=0)
        for _ in range(10):
            await kernel.on(HookEvent.POST_TURN).dispatch(_error_turn_ctx(turn_scope))
        model_ctx = _model_ctx(turn_scope)
        await kernel.on(HookEvent.PRE_MODEL).dispatch(model_ctx)
        assert model_ctx.messages == []


class TestOptInInstallers:
    @pytest.mark.asyncio
    async def test_supervisor_confidence_gate_blocks_once_opted_in(self) -> None:
        kernel = HookRegistry()
        install_turn_guards(kernel)
        install_supervisor_confidence_gate(kernel)
        ctx = await kernel.on(HookEvent.POST_TOOL_USE).dispatch(_low_confidence_create_plan_ctx())
        assert ctx.decision.value == "block"

    def test_supervisor_confidence_gate_idempotent(self) -> None:
        kernel = HookRegistry()
        install_supervisor_confidence_gate(kernel)
        install_supervisor_confidence_gate(kernel)
        pipeline = kernel.on(HookEvent.POST_TOOL_USE)
        assert len(pipeline._stack) == 1

    @pytest.mark.asyncio
    async def test_stall_detection_warns_once_opted_in(self) -> None:
        kernel = HookRegistry()
        install_turn_guards(kernel)
        install_stall_detection(kernel, warn_after=2, fail_after=10)
        run_scope = _run_scope()
        turn_scope = TurnScope(run=run_scope, turn_index=0)
        for _ in range(3):
            await kernel.on(HookEvent.POST_TURN).dispatch(_error_turn_ctx(turn_scope))
        model_ctx = _model_ctx(turn_scope)
        await kernel.on(HookEvent.PRE_MODEL).dispatch(model_ctx)
        assert len(model_ctx.messages) == 1
        assert "Warning" in str(model_ctx.messages[0].content)

    def test_stall_detection_idempotent(self) -> None:
        kernel = HookRegistry()
        install_stall_detection(kernel)
        install_stall_detection(kernel)
        assert len(kernel.on(HookEvent.POST_TURN)._stack) == 1
        assert len(kernel.on(HookEvent.PRE_MODEL)._stack) == 1


class _Registry:
    def __init__(self, *names: str) -> None:
        self._names = set(names)

    def has(self, name: str) -> bool:
        return name in self._names


def _scope_with(*, registered: tuple[str, ...], granted: list[str]) -> TurnScope:
    run = RunScope(
        identity=SimpleNamespace(),
        spec=SimpleNamespace(tool_names=granted),
        runtime=SimpleNamespace(tool_registry=_Registry(*registered)),
        goal=Goal(description="g"),
    )
    return TurnScope(run=run, turn_index=13)


_HISTORY_FOOTER = "\n\n[loop: step 13/15, stale_rounds=0]"


def _history() -> list:
    return [
        UserMessage(content="question"),
        ToolMessage(tool_call_id="c1", content="first result", step_footer=_HISTORY_FOOTER),
        ToolMessage(tool_call_id="c2", content="second result", step_footer=_HISTORY_FOOTER),
    ]


async def _deadline_messages(
    *, turn_index: int, max_turns: int, scope: TurnScope | None, messages: list | None = None,
) -> list:
    ctx = ModelCallContext(
        messages=list(messages if messages is not None else _history()),
        budget=ContextBudget(max_tokens=1000), scope=scope,
        turn_index=turn_index, max_turns=max_turns,
    )

    async def _next() -> None:
        return None

    await warn_before_deadline()(ctx, _next)
    return ctx.messages


def _note(messages: list) -> str:
    return messages[-1].step_footer.removeprefix(_HISTORY_FOOTER)


class TestDeadlineWarning:
    """The wrap-up note two turns before the cap. It rides on the latest tool
    result's loop footer: as an injected user message, "stop and answer now"
    read as a prompt attack and Azure OpenAI's content filter rejected every
    such call in evaluation runs, which the agent turned into a canned
    refusal."""

    @pytest.mark.asyncio
    async def test_fires_once_two_turns_before_the_cap(self) -> None:
        fired = [
            turn for turn in range(15)
            if _note(await _deadline_messages(turn_index=turn, max_turns=15, scope=None))
        ]

        assert fired == [13]

    @pytest.mark.asyncio
    async def test_it_adds_no_message_and_extends_only_the_latest_footer(self) -> None:
        history = _history()

        messages = await _deadline_messages(
            turn_index=13, max_turns=15, scope=None, messages=history,
        )

        assert len(messages) == len(history)
        assert not any(isinstance(m, UserMessage) for m in messages[1:])
        assert messages[-2].step_footer == _HISTORY_FOOTER
        assert messages[-1].step_footer.startswith(_HISTORY_FOOTER)
        assert messages[-1].content == "second result"

    @pytest.mark.asyncio
    async def test_the_stored_history_is_not_changed(self) -> None:
        history = _history()

        await _deadline_messages(turn_index=13, max_turns=15, scope=None, messages=history)

        assert history[-1].step_footer == _HISTORY_FOOTER

    @pytest.mark.asyncio
    async def test_without_a_tool_result_to_carry_it_nothing_is_added(self) -> None:
        messages = await _deadline_messages(
            turn_index=13, max_turns=15, scope=None, messages=[UserMessage(content="q")],
        )

        assert [m.content for m in messages] == ["q"]

    @pytest.mark.asyncio
    async def test_an_agent_without_task_complete_is_asked_for_plain_text(self) -> None:
        scope = _scope_with(registered=("knowledgegraph__search",), granted=[])

        note = _note(await _deadline_messages(turn_index=13, max_turns=15, scope=scope))

        assert "task_complete" not in note
        assert "plain text, without calling tools" in note

    @pytest.mark.asyncio
    async def test_an_agent_with_task_complete_is_told_to_call_it(self) -> None:
        scope = _scope_with(registered=("task_complete", "web_search"), granted=[])

        note = _note(await _deadline_messages(turn_index=13, max_turns=15, scope=scope))

        assert "call task_complete" in note

    @pytest.mark.asyncio
    async def test_a_registered_but_ungranted_tool_is_not_named(self) -> None:
        scope = _scope_with(registered=("task_complete", "search"), granted=["search"])

        note = _note(await _deadline_messages(turn_index=13, max_turns=15, scope=scope))

        assert "task_complete" not in note

    @pytest.mark.asyncio
    async def test_the_note_is_loop_state_not_a_system_message(self) -> None:
        note = _note(await _deadline_messages(turn_index=13, max_turns=15, scope=None))

        assert note.startswith("\n[loop: ")
        assert "System:" not in note
