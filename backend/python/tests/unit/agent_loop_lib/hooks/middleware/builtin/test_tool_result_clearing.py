"""Tests for the tool_result_clearing context-shaping middleware."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    _TOOL_REF_PREFIX,
    _is_already_compact,
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext


def _is_compacted(msg) -> bool:
    """True when a ToolMessage has been replaced by a metadata reference."""
    return isinstance(msg.content, str) and _is_already_compact(msg.content)


def _assistant_call(call_id: str, name: str, arguments: dict | None = None) -> AssistantMessage:
    return AssistantMessage(tool_calls=[ToolCall(id=call_id, name=name, arguments=arguments or {})])


def _assistant_parallel(*calls: tuple[str, str]) -> AssistantMessage:
    """AssistantMessage with multiple parallel tool_calls."""
    return AssistantMessage(tool_calls=[ToolCall(id=cid, name=name, arguments={}) for cid, name in calls])


def _tool_result(call_id: str, content: str = "data") -> ToolMessage:
    return ToolMessage(content=content, tool_call_id=call_id)


def _tool_result_with_artifact(call_id: str, artifact_id: str, content: str = "data") -> ToolMessage:
    return ToolMessage(
        content=content,
        tool_call_id=call_id,
        artifact_meta=ToolMessageMeta(
            artifact_id=artifact_id,
            summary=content[:50],
            original_token_count=len(content) // 4,
            turn_index=0,
        ),
    )


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
        assert _is_compacted(result[2])
        assert "tool: jira_search" in result[2].content
        assert _is_compacted(result[4])
        assert "tool: confluence_search" in result[4].content
        assert result[6].content == "recent slack data"
        assert result[8].content == "recent email data"

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
        assert result[2].content == "the plan"
        assert result[4].content == "critique feedback"
        assert result[6].content == "revised plan"
        assert result[8].content == "second critique"

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
        assert result[2].content == "the plan"
        assert _is_compacted(result[4])
        assert "tool: jira_search" in result[4].content
        assert result[6].content == "critique"
        assert result[8].content == "slack data"

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
        assert _is_compacted(result[2])
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
        assert result[2].content == "the plan"
        assert _is_compacted(result[3])
        assert result[5].content == "slack data"


class TestTurnBasedGrouping:
    """Parallel tool calls from one AssistantMessage are one logical turn."""

    @pytest.mark.asyncio
    async def test_parallel_calls_are_one_turn(self) -> None:
        """7 parallel calls in turn 1, 1 call in turn 2.
        keep_last_n_turns=1 should keep turn 2, clear all of turn 1."""
        messages = [
            UserMessage(content="go"),
            _assistant_parallel(
                ("c1", "jira_search"), ("c2", "jira_search"),
                ("c3", "jira_search"), ("c4", "jira_search"),
                ("c5", "jira_search"), ("c6", "jira_search"),
                ("c7", "jira_search"),
            ),
            _tool_result("c1", "r1"), _tool_result("c2", "r2"),
            _tool_result("c3", "r3"), _tool_result("c4", "r4"),
            _tool_result("c5", "r5"), _tool_result("c6", "r6"),
            _tool_result("c7", "r7"),
            _assistant_call("c8", "summarize"),
            _tool_result("c8", "summary"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        for i in range(2, 9):
            assert _is_compacted(result[i]), f"index {i} should be compacted"
            assert "tool: jira_search" in result[i].content
        assert result[10].content == "summary"

    @pytest.mark.asyncio
    async def test_parallel_calls_kept_as_unit(self) -> None:
        """keep_last_n_turns=1 with 2 turns (both parallel).
        Turn 2 should be fully kept, turn 1 fully cleared."""
        messages = [
            UserMessage(content="go"),
            _assistant_parallel(("c1", "search"), ("c2", "search")),
            _tool_result("c1", "old1"), _tool_result("c2", "old2"),
            _assistant_parallel(("c3", "search"), ("c4", "search")),
            _tool_result("c3", "new1"), _tool_result("c4", "new2"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        assert _is_compacted(result[2])
        assert _is_compacted(result[3])
        assert result[5].content == "new1"
        assert result[6].content == "new2"


class TestArtifactAwareness:
    """Artifact-bearing messages get compact references, not generic stubs."""

    @pytest.mark.asyncio
    async def test_artifact_message_gets_compact_reference(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search"),
            _tool_result_with_artifact("c1", "artifact_1", "big jira data"),
            _assistant_call("c2", "slack_search"),
            _tool_result("c2", "recent slack data"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        assert "[artifact:artifact_1]" in result[2].content
        assert "retrieve_artifact_content" in result[2].content
        assert result[4].content == "recent slack data"

    @pytest.mark.asyncio
    async def test_non_artifact_message_gets_metadata_ref(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search", {"query": "urgent bugs"}),
            _tool_result("c1", "old jira data with details"),
            _assistant_call("c2", "slack_search"),
            _tool_result("c2", "recent slack data"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        ref = result[2].content
        assert ref.startswith("tool: jira_search")
        assert "tool_call_id: c1" in ref
        assert '"query": "urgent bugs"' in ref
        assert "summary: old jira data with details" in ref
        assert "hint: call jira_search again" in ref
        assert result[4].content == "recent slack data"

    @pytest.mark.asyncio
    async def test_error_result_labeled_as_error(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "confluence__get_space", {"space_key": "ENG"}),
            ToolMessage(content="API rate limit exceeded", tool_call_id="c1", is_error=True),
            _assistant_call("c2", "slack_search"),
            _tool_result("c2", "recent"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        ref = result[2].content
        assert "tool: confluence__get_space" in ref
        assert "error: API rate limit exceeded" in ref

    @pytest.mark.asyncio
    async def test_mixed_artifact_and_non_artifact(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_parallel(("c1", "jira_search"), ("c2", "confluence_search")),
            _tool_result_with_artifact("c1", "art_1", "big jira data"),
            _tool_result("c2", "small confluence data"),
            _assistant_call("c3", "summarize"),
            _tool_result("c3", "summary"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        assert "[artifact:art_1]" in result[2].content
        assert _is_compacted(result[3])
        assert "tool: confluence_search" in result[3].content
        assert result[5].content == "summary"


class TestRetrieveArtifactContentClearing:
    """retrieve_artifact_content results use the same metadata ref format."""

    @pytest.mark.asyncio
    async def test_retrieve_result_preserves_artifact_id_in_args(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "retrieve_artifact_content", {"artifact_id": "artifact_5"}),
            _tool_result("c1", "full artifact content " * 500),
            _assistant_call("c2", "jira_search"),
            _tool_result("c2", "recent data"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        ref = result[2].content
        assert "artifact_5" in ref
        assert "retrieve_artifact_content" in ref
        assert result[4].content == "recent data"

    @pytest.mark.asyncio
    async def test_retrieve_without_artifact_id_still_gets_metadata(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "retrieve_artifact_content", {}),
            _tool_result("c1", "some content"),
            _assistant_call("c2", "slack_search"),
            _tool_result("c2", "recent"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        ref = result[2].content
        assert "tool: retrieve_artifact_content" in ref
        assert "summary: some content" in ref

    @pytest.mark.asyncio
    async def test_regular_tools_get_metadata_ref_alongside_retrieve(self) -> None:
        messages = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search"),
            _tool_result("c1", "old data"),
            _assistant_call("c2", "retrieve_artifact_content", {"artifact_id": "artifact_3"}),
            _tool_result("c2", "retrieved content"),
            _assistant_call("c3", "summarize"),
            _tool_result("c3", "summary"),
        ]
        result = await _run_clearing(messages, keep_last_n_turns=1)
        assert _is_compacted(result[2])
        assert "tool: jira_search" in result[2].content
        assert "artifact_3" in result[4].content
        assert result[6].content == "summary"
