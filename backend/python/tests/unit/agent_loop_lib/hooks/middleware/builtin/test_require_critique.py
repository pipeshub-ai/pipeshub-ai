"""Tests for `hooks/middleware/builtin/require_critique.py` — the PRE/POST
tool-use middleware pair that denies execution-phase tools until a
low-confidence plan has been explicitly re-reviewed via `critique_plan`.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope
from app.agent_loop_lib.core.types import Confidence, Goal
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin.require_critique import (
    PENDING_CRITIQUE,
    _parse_confidence,
    require_critique,
)
from app.agent_loop_lib.hooks.middleware.context import ToolCallContext, ToolResultContext
from app.agent_loop_lib.hooks.middleware.decisions import PostDecision, PreDecision
from app.agent_loop_lib.hooks.registry import HookRegistry
from app.agent_loop_lib.tools.base import ToolOutput


def _run_scope() -> RunScope:
    return RunScope(
        identity=SimpleNamespace(), spec=SimpleNamespace(), runtime=SimpleNamespace(),
        goal=Goal(description="g"),
    )


def _tool_scope(run: RunScope) -> ToolScope:
    turn = TurnScope(run=run, turn_index=0)
    return ToolScope(turn=turn, call=SimpleNamespace(), tool_path="unused")


def _call_ctx(tool_path: str, run: RunScope | None) -> ToolCallContext:
    scope = _tool_scope(run) if run is not None else None
    return ToolCallContext(tool_path=tool_path, tool_input={}, scope=scope)


def _result_ctx(
    tool_path: str, run: RunScope | None, *, success: bool = True, data: object = None,
) -> ToolResultContext:
    scope = _tool_scope(run) if run is not None else None
    return ToolResultContext(
        tool_path=tool_path, tool_use_id=uuid4(),
        tool_response=ToolOutput(success=success, data=data), scope=scope,
    )


class TestParseConfidence:
    def test_valid_string_parses_to_enum(self) -> None:
        assert _parse_confidence("low") is Confidence.LOW
        assert _parse_confidence("high") is Confidence.HIGH

    def test_none_is_unrecognizable(self) -> None:
        assert _parse_confidence(None) is None

    def test_unknown_value_is_unrecognizable(self) -> None:
        assert _parse_confidence("not-a-real-level") is None


class TestInstall:
    def test_is_idempotent_for_the_same_require_critique_call(self) -> None:
        kernel = HookRegistry()
        install = require_critique()
        install(kernel)
        install(kernel)
        assert len(kernel.on(HookEvent.PRE_TOOL_USE)._stack) == 1
        assert len(kernel.on(HookEvent.POST_TOOL_USE)._stack) == 1

    def test_two_independent_calls_each_install_their_own_pair(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        require_critique(confidence_threshold=Confidence.MEDIUM)(kernel)
        assert len(kernel.on(HookEvent.PRE_TOOL_USE)._stack) == 2
        assert len(kernel.on(HookEvent.POST_TOOL_USE)._stack) == 2


class TestPreToolUseGating:
    async def test_no_run_scope_allows_through(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        ctx = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/x/write_file", None))
        assert ctx.decision == PreDecision.ALLOW

    async def test_no_pending_critique_allows_through(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        ctx = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/x/write_file", run))
        assert ctx.decision == PreDecision.ALLOW

    async def test_pending_critique_denies_execution_tool(self) -> None:
        kernel = HookRegistry()
        require_critique(confidence_threshold=Confidence.HIGH)(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        ctx = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/jira/write_file", run))

        assert ctx.decision == PreDecision.DENY
        assert "critique_plan" in ctx.decision_reason
        assert "'high'" in ctx.decision_reason

    async def test_pending_critique_short_circuits_later_middleware(self) -> None:
        """Denying is only meaningful if it actually stops the pipeline —
        verified here via a spy middleware registered after require_critique
        on the same PRE_TOOL_USE pipeline."""
        kernel = HookRegistry()
        require_critique()(kernel)
        spy_calls: list[str] = []

        async def _spy(ctx: ToolCallContext, next_fn) -> None:
            spy_calls.append(ctx.tool_path)
            await next_fn()

        kernel.on(HookEvent.PRE_TOOL_USE).use(_spy)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/jira/write_file", run))

        assert spy_calls == []

    async def test_allowed_suffixes_bypass_the_gate_while_pending(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        for suffix in ("create_plan", "critique_plan", "replan", "verify_result", "request_review", "write_todos", "clarify"):
            ctx = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx(f"/toolsets/x/{suffix}", run))
            assert ctx.decision == PreDecision.ALLOW, suffix

    async def test_task_complete_is_not_in_the_allowlist(self) -> None:
        """Deliberately excluded: declaring the run done on an unreviewed
        plan is exactly the outcome this middleware exists to prevent."""
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        ctx = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/x/task_complete", run))
        assert ctx.decision == PreDecision.DENY


class TestPostToolUseStateTransitions:
    async def test_no_run_scope_is_a_noop(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        ctx = await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", None, data={"confidence": "low"})
        )
        assert ctx.decision == PostDecision.CONTINUE

    async def test_low_confidence_plan_sets_pending(self) -> None:
        kernel = HookRegistry()
        require_critique(confidence_threshold=Confidence.HIGH)(kernel)
        run = _run_scope()

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data={"confidence": "low"})
        )

        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_confidence_meeting_threshold_clears_pending(self) -> None:
        kernel = HookRegistry()
        require_critique(confidence_threshold=Confidence.HIGH)(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data={"confidence": "high"})
        )

        assert run.get(PENDING_CRITIQUE).pending is False

    async def test_threshold_is_configurable(self) -> None:
        kernel = HookRegistry()
        require_critique(confidence_threshold=Confidence.MEDIUM)(kernel)
        run = _run_scope()

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data={"confidence": "medium"})
        )
        assert run.get(PENDING_CRITIQUE).pending is False

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data={"confidence": "low"})
        )
        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_unparseable_confidence_leaves_pending_untouched(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data={"confidence": "gibberish"})
        )

        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_non_dict_data_leaves_pending_untouched(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data="not a dict")
        )

        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_failed_create_plan_leaves_pending_untouched(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, success=False, data={"confidence": "low"})
        )

        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_passed_critique_clears_pending(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/critique_plan", run, data={"passed": True})
        )

        assert run.get(PENDING_CRITIQUE).pending is False

    async def test_failed_critique_leaves_pending_untouched(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/critique_plan", run, data={"passed": False})
        )

        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_unsuccessful_critique_leaves_pending_untouched(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/critique_plan", run, success=False, data={"passed": True})
        )

        assert run.get(PENDING_CRITIQUE).pending is True

    async def test_unrelated_tool_leaves_pending_untouched(self) -> None:
        kernel = HookRegistry()
        require_critique()(kernel)
        run = _run_scope()
        run.get(PENDING_CRITIQUE).pending = True

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/web_search", run, data={"confidence": "low"})
        )

        assert run.get(PENDING_CRITIQUE).pending is True


class TestFullCycle:
    async def test_low_confidence_plan_blocks_until_passed_critique(self) -> None:
        """End-to-end through both middleware: a low-confidence plan blocks
        execution tools, and only a PASSED critique lifts the block."""
        kernel = HookRegistry()
        require_critique(confidence_threshold=Confidence.HIGH)(kernel)
        run = _run_scope()

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/create_plan", run, data={"confidence": "low"})
        )
        blocked = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/x/write_file", run))
        assert blocked.decision == PreDecision.DENY

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/critique_plan", run, data={"passed": False})
        )
        still_blocked = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/x/write_file", run))
        assert still_blocked.decision == PreDecision.DENY

        await kernel.on(HookEvent.POST_TOOL_USE).dispatch(
            _result_ctx("/toolsets/x/critique_plan", run, data={"passed": True})
        )
        allowed = await kernel.on(HookEvent.PRE_TOOL_USE).dispatch(_call_ctx("/toolsets/x/write_file", run))
        assert allowed.decision == PreDecision.ALLOW
