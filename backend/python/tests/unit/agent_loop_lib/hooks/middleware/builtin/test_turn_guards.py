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
from app.agent_loop_lib.core.messages import AssistantMessage
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


def _deadline_ctx(
    *, registered: tuple[str, ...] | None, granted: tuple[str, ...] = (), turn_index: int = 18,
) -> ModelCallContext:
    """`registered=None` builds a context with no run scope at all."""
    scope = None
    if registered is not None:
        registry = SimpleNamespace(has=lambda name: name in registered)
        run = RunScope(
            identity=SimpleNamespace(), spec=SimpleNamespace(tool_names=list(granted)),
            runtime=SimpleNamespace(tool_registry=registry), goal=Goal(description="g"),
        )
        scope = TurnScope(run=run, turn_index=turn_index)
    return ModelCallContext(
        messages=[], budget=ContextBudget(max_tokens=1000), scope=scope,
        turn_index=turn_index, max_turns=20,
    )


async def _warning(ctx: ModelCallContext) -> str | None:
    async def _next() -> None:
        return None

    await warn_before_deadline()(ctx, _next)
    return str(ctx.messages[-1].content) if ctx.messages else None


class TestWarnBeforeDeadline:
    """Told to call `task_complete` when it has no such tool, the model has
    answered with a canned refusal; the warning names it only when the run
    can call it."""

    @pytest.mark.asyncio
    async def test_names_task_complete_when_it_is_registered_and_granted(self) -> None:
        warning = await _warning(_deadline_ctx(registered=("task_complete",)))
        assert warning is not None
        assert "call task_complete immediately" in warning

    @pytest.mark.asyncio
    async def test_explicit_grant_must_include_task_complete(self) -> None:
        granted = await _warning(_deadline_ctx(registered=("task_complete",), granted=("task_complete", "web_search")))
        not_granted = await _warning(_deadline_ctx(registered=("task_complete",), granted=("web_search",)))
        assert "task_complete" in granted
        assert "task_complete" not in not_granted

    @pytest.mark.asyncio
    async def test_asks_for_a_plain_answer_when_task_complete_is_not_registered(self) -> None:
        warning = await _warning(_deadline_ctx(registered=("sql__execute_sql_query",)))
        assert warning is not None
        assert "task_complete" not in warning
        assert "Reply with your final answer" in warning
        assert warning.startswith("[System: You have 2 turns remaining. Stop gathering information now.")

    @pytest.mark.asyncio
    async def test_without_a_run_scope_it_names_no_tool(self) -> None:
        warning = await _warning(_deadline_ctx(registered=None))
        assert warning is not None
        assert "task_complete" not in warning

    @pytest.mark.asyncio
    async def test_fires_only_two_turns_before_the_limit(self) -> None:
        for turn_index in (0, 17, 19, 20):
            assert await _warning(_deadline_ctx(registered=(), turn_index=turn_index)) is None
