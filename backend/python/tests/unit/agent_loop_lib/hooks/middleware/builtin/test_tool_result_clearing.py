"""Tests for the tool_result_clearing context-shaping middleware."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    _CLEARED_PLACEHOLDER,
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def _assistant_call(call_id: str, name: str) -> AssistantMessage:
    return AssistantMessage(tool_calls=[ToolCall(id=call_id, name=name, arguments={})])


def _tool_result(call_id: str, content: str = "data") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id)


async def _run_clearing(messages, *, keep_last_n_turns=3, trigger_ratio=0.0, protected_tool_names=None):
    """Helper: runs the clearing middleware and returns the shaped messages.

    `trigger_ratio=0.0` means clearing always activates (no budget guard)
    — tests that need the guard can override it.
    """
    budget = ContextBudget(max_tokens=100_000)
    ctx = ModelCallContext(messages=list(messages), budget=budget)
    middleware = shape_tool_result_clearing(
        keep_last_n_turns=keep_last_n_turns,
        trigger_ratio=trigger_ratio,
        protected_tool_names=protected_tool_names,
    )
    await middleware(ctx, _noop_next)
    return ctx.messages


async def _noop_next():
    pass


class TestToolResultClearing:
    @pytest.mark.asyncio
    async def test_clears_old_tool_results_beyond_keep_last_n(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search"),
            _tool_result("c1", "old jira data"),
            _assistant_call("c2", "confluence_search"),
            _tool_result("c2", "old confluence data"),
            _assistant_call("c3", "slack_search"),
            _tool_result("c3", "recent slack data"),
            _assistant_call("c4", "email_search"),
            _tool_result("c4", "recent email data"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=2)
        assert result[2].content == _CLEARED_PLACEHOLDER  # c1 cleared
        assert result[4].content == _CLEARED_PLACEHOLDER  # c2 cleared
        assert result[6].content == "recent slack data"   # c3 kept
        assert result[8].content == "recent email data"   # c4 kept

    @pytest.mark.asyncio
    async def test_protected_tool_names_are_never_cleared(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "create_plan"),
            _tool_result("c1", "the plan"),
            _assistant_call("c2", "critique_plan"),
            _tool_result("c2", "critique feedback"),
            _assistant_call("c3", "create_plan"),
            _tool_result("c3", "revised plan"),
            _assistant_call("c4", "critique_plan"),
            _tool_result("c4", "second critique"),
        ]
        result = await _run_clearing(
            messages,
            keep_last_n_turns=2,
            protected_tool_names=frozenset({"create_plan", "critique_plan"}),
        )
        assert result[2].content == "the plan"          # protected
        assert result[4].content == "critique feedback"  # protected
        assert result[6].content == "revised plan"       # protected (also recent)
        assert result[8].content == "second critique"    # protected (also recent)

    @pytest.mark.asyncio
    async def test_protected_tools_mixed_with_unprotected(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "create_plan"),
            _tool_result("c1", "the plan"),
            _assistant_call("c2", "jira_search"),
            _tool_result("c2", "jira data"),
            _assistant_call("c3", "critique_plan"),
            _tool_result("c3", "critique"),
            _assistant_call("c4", "slack_search"),
            _tool_result("c4", "slack data"),
        ]
        result = await _run_clearing(
            messages,
            keep_last_n_turns=1,
            protected_tool_names=frozenset({"create_plan", "critique_plan"}),
        )
        assert result[2].content == "the plan"            # protected
        assert result[4].content == _CLEARED_PLACEHOLDER  # unprotected, old
        assert result[6].content == "critique"            # protected
        assert result[8].content == "slack data"          # unprotected but recent (last 1)

    @pytest.mark.asyncio
    async def test_no_protected_names_behaves_as_before(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search"),
            _tool_result("c1", "old data"),
            _assistant_call("c2", "slack_search"),
            _tool_result("c2", "recent data"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        assert result[2].content == _CLEARED_PLACEHOLDER
        assert result[4].content == "recent data"

    @pytest.mark.asyncio
    async def test_orphan_tool_message_without_matching_call_is_not_protected(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "create_plan"),
            _tool_result("c1", "the plan"),
            ToolMessage(content="orphan result", tool_call_id="missing"),
            _assistant_call("c2", "slack_search"),
            _tool_result("c2", "slack data"),
        ]
        result = await _run_clearing(
            messages,
            keep_last_n_turns=1,
            protected_tool_names=frozenset({"create_plan"}),
        )
        assert result[2].content == "the plan"            # protected
        assert result[3].content == _CLEARED_PLACEHOLDER  # orphan, not protected
        assert result[5].content == "slack data"          # recent
