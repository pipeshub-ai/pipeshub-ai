"""Phase 5 hook middleware, each exercised in isolation via
`support/hook_helpers.py`'s `run_pre_tool`/`run_post_tool`/`run_pre_turn`
(vendored from agent-loop's `tests/hooks/_helpers.py`, Phase 9)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.result_accumulation import (
    result_accumulation,
    stash_tool_call_metadata,
)
from app.agents.agent_loop.hooks.tool_blocking import ToolErrorTracker
from tests.unit.agents.adapter.support.hook_helpers import (
    assert_allowed,
    assert_denied,
    run_post_tool,
    run_pre_tool,
)


def _make_context(**tool_state_overrides) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=MagicMock(),
    )
    context.tool_state.update(tool_state_overrides)
    return context


class TestToolErrorTracker:
    async def test_allows_by_default(self) -> None:
        tracker = ToolErrorTracker(threshold=3)
        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path="/connectors/jira/search")
        assert_allowed(ctx)

    async def test_denies_after_consecutive_failures_reach_threshold(self) -> None:
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        for _ in range(3):
            await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path)

        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_denied(ctx)
        assert "3" in ctx.decision_reason or "failed" in ctx.decision_reason.lower()

    async def test_a_tool_whose_service_is_unavailable_is_not_blocked(self) -> None:
        """FIX-P1 S2: three calls that failed because the tool's service was down (a sandbox broker restarting)
        say nothing about how the model uses the tool: they neither block it nor break a streak of real failures."""
        tracker = ToolErrorTracker(threshold=3)
        path = "/toolsets/coding_sandbox/run_code"
        await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="bad args"), tool_path=path)
        for _ in range(3):
            down = ToolOutput(success=False, error="sandbox broker unavailable", error_code="unavailable")
            await run_post_tool(tracker.post_tool_use, down, tool_path=path)
        assert_allowed(await run_pre_tool(tracker.pre_tool_use, tool_path=path))
        for _ in range(2):
            await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="bad args"), tool_path=path)
        assert_denied(await run_pre_tool(tracker.pre_tool_use, tool_path=path))

    async def test_success_resets_the_streak(self) -> None:
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path)
        await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path)
        await run_post_tool(tracker.post_tool_use, ToolOutput(success=True, data="ok"), tool_path=path)

        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_allowed(ctx)

    async def test_blocked_tool_never_reaches_next_fn(self) -> None:
        tracker = ToolErrorTracker(threshold=1)
        path = "/connectors/jira/search"
        await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path)

        called = False

        async def _next() -> None:
            nonlocal called
            called = True

        from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

        ctx = ToolCallContext(tool_path=path, tool_input={})
        await tracker.pre_tool_use(ctx, _next)
        assert called is False
        assert_denied(ctx)

    async def test_different_tool_paths_tracked_independently(self) -> None:
        tracker = ToolErrorTracker(threshold=1)
        await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="x"), tool_path="/a")

        ctx_a = await run_pre_tool(tracker.pre_tool_use, tool_path="/a")
        ctx_b = await run_pre_tool(tracker.pre_tool_use, tool_path="/b")
        assert_denied(ctx_a)
        assert_allowed(ctx_b)

    async def test_parallel_failures_in_same_turn_count_once(self) -> None:
        """Several parallel calls to the same tool within one turn (gathered
        concurrently — see `Agent.step()`) fail together as one attempt, not
        `threshold` independent tries in a row. Without turn-awareness this
        would block the tool after a single turn's fan-out."""
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        scope = SimpleNamespace(turn=SimpleNamespace(turn_index=0))
        for _ in range(5):
            await run_post_tool(
                tracker.post_tool_use, ToolOutput(success=False, error="boom"),
                tool_path=path, scope=scope,
            )

        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_allowed(ctx)

    async def test_failures_across_separate_turns_still_accumulate(self) -> None:
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        for turn_index in range(3):
            scope = SimpleNamespace(turn=SimpleNamespace(turn_index=turn_index))
            await run_post_tool(
                tracker.post_tool_use, ToolOutput(success=False, error="boom"),
                tool_path=path, scope=scope,
            )

        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_denied(ctx)

    async def test_without_scope_falls_back_to_call_aware_counting(self) -> None:
        """No `turn_index` available (e.g. a caller that never attaches a
        scope) preserves the old call-aware counting exactly."""
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        for _ in range(3):
            await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path)

        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_denied(ctx)

    async def test_success_after_turn_aware_failures_resets_last_failed_turn(self) -> None:
        """A success must clear the turn-tracking too, not just the
        counter — otherwise a later failure replaying the SAME turn_index
        (e.g. a resumed run) would be silently ignored as "already counted"."""
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        scope0 = SimpleNamespace(turn=SimpleNamespace(turn_index=0))
        await run_post_tool(
            tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path, scope=scope0,
        )
        await run_post_tool(
            tracker.post_tool_use, ToolOutput(success=True, data="ok"), tool_path=path, scope=scope0,
        )
        await run_post_tool(
            tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path, scope=scope0,
        )

        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_allowed(ctx)

    @staticmethod
    def _turn(index: int) -> SimpleNamespace:
        return SimpleNamespace(turn=SimpleNamespace(turn_index=index))

    async def _block_at_turn_2(self, tracker: ToolErrorTracker, path: str) -> None:
        for turn_index in range(3):
            await run_post_tool(
                tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path,
                scope=self._turn(turn_index),
            )

    async def test_evalfix_f1_a_block_is_lifted_after_its_cooldown_and_the_message_says_when(self) -> None:
        """FIX-P1-F1: the model was told the tool was "temporarily disabled", but nothing ever lifted the block (a
        denied call records nothing). The pause now lasts a stated number of turns, and the message says how many."""
        tracker = ToolErrorTracker(threshold=3, cooldown_turns=2)
        path = "/connectors/jira/search"
        await self._block_at_turn_2(tracker, path)

        first = await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(3))
        assert_denied(first)
        assert "2 turns" in first.decision_reason
        assert "temporarily" not in first.decision_reason
        last = await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(4))
        assert_denied(last)
        assert "1 turn" in last.decision_reason
        assert_allowed(await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(5)))

    async def test_evalfix_f1_a_failure_after_the_pause_pauses_it_again_and_a_success_clears_it(self) -> None:
        tracker = ToolErrorTracker(threshold=3, cooldown_turns=2)
        path = "/connectors/jira/search"
        await self._block_at_turn_2(tracker, path)
        await run_post_tool(
            tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path, scope=self._turn(5),
        )
        assert_denied(await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(6)))
        assert_allowed(await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(8)))
        await run_post_tool(
            tracker.post_tool_use, ToolOutput(success=True, data="ok"), tool_path=path, scope=self._turn(8),
        )
        await run_post_tool(
            tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path, scope=self._turn(9),
        )
        assert_allowed(await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(10)))

    async def test_evalfix_f1_denials_replayed_from_the_journal_do_not_extend_the_pause(self) -> None:
        """A resumed run rebuilds the streaks from its journaled outcomes, where a denied call reads as a failure. A
        live run never records a denial, so the rebuilt pause must end on the same turn."""
        tracker = ToolErrorTracker(threshold=3, cooldown_turns=2)
        path = "/connectors/jira/search"
        await self._block_at_turn_2(tracker, path)
        for turn_index in (3, 4):
            tracker.record(path, is_error=True, turn_index=turn_index)
        assert_allowed(await run_pre_tool(tracker.pre_tool_use, tool_path=path, scope=self._turn(5)))

    async def test_evalfix_f1_without_turns_the_block_lasts_the_request_and_says_so(self) -> None:
        tracker = ToolErrorTracker(threshold=3)
        path = "/connectors/jira/search"
        for _ in range(3):
            await run_post_tool(tracker.post_tool_use, ToolOutput(success=False, error="boom"), tool_path=path)
        ctx = await run_pre_tool(tracker.pre_tool_use, tool_path=path)
        assert_denied(ctx)
        assert "rest of this request" in ctx.decision_reason
        assert "temporarily" not in ctx.decision_reason


class TestResultAccumulation:
    async def test_stash_and_accumulate_success(self) -> None:
        context = _make_context()
        pre_ctx = await run_pre_tool(
            stash_tool_call_metadata, tool_path="/connectors/jira/search", tool_input={"q": "bug"}
        )
        post_ctx = await run_post_tool(
            result_accumulation(context),
            ToolOutput(success=True, data="found 3 issues"),
            tool_path="/connectors/jira/search",
            tool_use_id=pre_ctx.tool_use_id,
            metadata=pre_ctx.metadata,
        )
        results = context.tool_state["all_tool_results"]
        assert len(results) == 1
        entry = results[0]
        assert entry["status"] == "success"
        assert entry["result"] == "found 3 issues"
        assert entry["args"] == {"q": "bug"}
        assert entry["tool_id"] == str(post_ctx.tool_use_id)
        assert entry["duration_ms"] >= 0

    async def test_accumulate_failure_formats_error_message(self) -> None:
        context = _make_context()
        pre_ctx = await run_pre_tool(stash_tool_call_metadata, tool_path="/a", tool_input={})
        await run_post_tool(
            result_accumulation(context),
            ToolOutput(success=False, error="not found"),
            tool_path="/a",
            metadata=pre_ctx.metadata,
        )
        entry = context.tool_state["all_tool_results"][0]
        assert entry["status"] == "error"
        assert entry["result"] == "Error: not found"

    async def test_multiple_calls_append_in_order(self) -> None:
        context = _make_context()
        for i in range(3):
            pre_ctx = await run_pre_tool(stash_tool_call_metadata, tool_path=f"/tool{i}", tool_input={})
            await run_post_tool(
                result_accumulation(context),
                ToolOutput(success=True, data=f"result{i}"),
                tool_path=f"/tool{i}",
                metadata=pre_ctx.metadata,
            )
        results = context.tool_state["all_tool_results"]
        assert [r["result"] for r in results] == ["result0", "result1", "result2"]
