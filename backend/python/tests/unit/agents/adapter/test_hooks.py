"""Phase 5 hook middleware, each exercised in isolation via
`support/hook_helpers.py`'s `run_pre_tool`/`run_post_tool`/`run_pre_turn`
(vendored from agent-loop's `tests/hooks/_helpers.py`, Phase 9)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.memory import conversation_enrichment
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
    run_pre_turn,
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


class TestConversationEnrichment:
    async def test_noop_when_no_previous_conversations(self) -> None:
        context = _make_context()
        context.previous_conversations = []
        middleware = conversation_enrichment(context)

        ctx = await run_pre_turn(middleware, turn_index=0)
        assert ctx.scope is None  # helper doesn't set a scope by default

    async def test_appends_reminder_for_follow_up_query(self) -> None:
        context = _make_context()
        context.previous_conversations = [
            {"role": "user_query", "content": "search jira for bug 123"},
            {"role": "bot_response", "content": "Found ticket JIRA-123 about a login bug."},
        ]
        middleware = conversation_enrichment(context)

        goal = Goal(description="yes")
        run_scope = SimpleNamespace(goal=goal)
        turn_scope = SimpleNamespace(run=run_scope)

        ctx = await run_pre_turn(middleware, turn_index=0, scope=turn_scope)
        assert ctx.scope is turn_scope
        assert len(goal.constraints) == 1
        assert "follow-up" in goal.constraints[0].lower()

    async def test_only_fires_on_first_turn(self) -> None:
        context = _make_context()
        context.previous_conversations = [{"role": "user_query", "content": "yes"}]
        middleware = conversation_enrichment(context)

        goal = Goal(description="yes")
        run_scope = SimpleNamespace(goal=goal)
        turn_scope = SimpleNamespace(run=run_scope)

        await run_pre_turn(middleware, turn_index=1, scope=turn_scope)
        assert goal.constraints == []
