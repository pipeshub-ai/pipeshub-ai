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
