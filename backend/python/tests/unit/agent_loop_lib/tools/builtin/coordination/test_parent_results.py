"""Unit tests for `parent_results.py` — the parent -> child data-handoff
collector/formatter/serializer that `AgentTool.handle()` uses to give a
statically composed child (e.g. `coding_agent`) deterministic access to
the calling agent's own recent tool results."""

from __future__ import annotations

import json

from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    Message,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.tools.builtin.coordination.parent_results import (
    ParentToolResult,
    collect_parent_tool_results,
    extract_dependency_results,
    format_parent_results_digest,
    parent_results_as_json,
)


def _assistant_call(call_id: str, name: str) -> AssistantMessage:
    return AssistantMessage(tool_calls=[ToolCall(id=call_id, name=name, arguments={})])


class TestCollectParentToolResults:
    def test_pairs_tool_message_with_preceding_call_name(self) -> None:
        messages: list[Message] = [
            UserMessage(content="find my tickets and build a report"),
            _assistant_call("c1", "jira_search_issues"),
            ToolMessage(content='{"tickets": ["A-1", "A-2"]}', tool_call_id="c1"),
        ]
        results = collect_parent_tool_results(messages)
        assert results == [ParentToolResult(tool_name="jira_search_issues", content='{"tickets": ["A-1", "A-2"]}')]

    def test_only_considers_messages_after_last_real_user_message(self) -> None:
        """A stale result from an earlier leg of the conversation (before
        the CURRENT user turn) must not leak into a later delegation."""
        messages: list[Message] = [
            UserMessage(content="first ask"),
            _assistant_call("c0", "jira_search_issues"),
            ToolMessage(content="STALE RESULT", tool_call_id="c0"),
            UserMessage(content="second ask"),
            _assistant_call("c1", "jira_search_issues"),
            ToolMessage(content="FRESH RESULT", tool_call_id="c1"),
        ]
        results = collect_parent_tool_results(messages)
        assert len(results) == 1
        assert results[0].content == "FRESH RESULT"

    def test_injected_user_message_does_not_create_boundary(self) -> None:
        """A programmatically injected UserMessage (recovery nudge, phase
        transition) must not hide earlier tool results from a child. This
        reproduces the completion-gate bug: Jira data gathered before a
        recovery nudge must still reach coding_agent."""
        messages: list[Message] = [
            UserMessage(content="analyze tickets and generate a report"),
            _assistant_call("c1", "jira_search_issues"),
            ToolMessage(content='{"tickets": ["PROJ-1"]}', tool_call_id="c1"),
            _assistant_call("c2", "confluence_search"),
            ToolMessage(content='{"pages": ["Design Doc"]}', tool_call_id="c2"),
            # Completion gate injects a recovery nudge — must NOT hide the above
            UserMessage(content="[System: produce a file]", injected=True),
            _assistant_call("c3", "coding_agent"),
        ]
        results = collect_parent_tool_results(messages)
        assert len(results) == 2
        assert results[0].tool_name == "jira_search_issues"
        assert results[1].tool_name == "confluence_search"

    def test_excludes_errored_tool_results(self) -> None:
        messages: list[Message] = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search_issues"),
            ToolMessage(content="rate limited", tool_call_id="c1", is_error=True),
        ]
        assert collect_parent_tool_results(messages) == []

    def test_excludes_sandbox_tool_results_by_default(self) -> None:
        messages: list[Message] = [
            UserMessage(content="go"),
            _assistant_call("c1", "run_code"),
            ToolMessage(content="stdout from a prior run", tool_call_id="c1"),
            _assistant_call("c2", "jira_search_issues"),
            ToolMessage(content="ticket data", tool_call_id="c2"),
        ]
        results = collect_parent_tool_results(messages)
        assert len(results) == 1
        assert results[0].tool_name == "jira_search_issues"

    def test_skips_empty_tool_content(self) -> None:
        messages: list[Message] = [
            UserMessage(content="go"),
            _assistant_call("c1", "jira_search_issues"),
            ToolMessage(content="", tool_call_id="c1"),
        ]
        assert collect_parent_tool_results(messages) == []

    def test_no_user_message_falls_back_to_whole_list(self) -> None:
        messages: list[Message] = [
            _assistant_call("c1", "jira_search_issues"),
            ToolMessage(content="ticket data", tool_call_id="c1"),
        ]
        results = collect_parent_tool_results(messages)
        assert len(results) == 1
        assert results[0].content == "ticket data"

    def test_unmatched_tool_call_id_falls_back_to_unknown_tool(self) -> None:
        messages: list[Message] = [
            UserMessage(content="go"),
            ToolMessage(content="orphaned result", tool_call_id="missing-call"),
        ]
        results = collect_parent_tool_results(messages)
        assert results == [ParentToolResult(tool_name="unknown_tool", content="orphaned result")]


class TestFormatParentResultsDigest:
    def test_empty_results_yields_empty_string(self) -> None:
        assert format_parent_results_digest([]) == ""

    def test_renders_tool_name_header_and_content(self) -> None:
        digest = format_parent_results_digest([ParentToolResult(tool_name="jira_search_issues", content="A-1, A-2")])
        assert "### Result from `jira_search_issues`" in digest
        assert "A-1, A-2" in digest

    def test_truncates_individual_result_over_per_result_cap(self) -> None:
        long_content = "x" * 100
        digest = format_parent_results_digest(
            [ParentToolResult(tool_name="t", content=long_content)], per_result_chars=10,
        )
        assert "x" * 10 in digest
        assert "x" * 11 not in digest
        assert "[truncated]" in digest

    def test_omits_results_once_total_budget_spent(self) -> None:
        results = [ParentToolResult(tool_name=f"tool{i}", content="y" * 50) for i in range(5)]
        digest = format_parent_results_digest(results, per_result_chars=50, total_chars=120)
        assert "tool0" in digest
        assert "more result(s) omitted for length" in digest


class TestParentResultsAsJson:
    def test_empty_results_yields_none(self) -> None:
        assert parent_results_as_json([]) is None

    def test_round_trips_plain_text_content(self) -> None:
        encoded = parent_results_as_json([ParentToolResult(tool_name="jira_search_issues", content="plain text")])
        assert encoded is not None
        decoded = json.loads(encoded)
        assert "_meta" in decoded
        assert decoded["_meta"]["truncated"] is False
        assert decoded["results"] == [{"tool": "jira_search_issues", "content": "plain text"}]

    def test_decodes_json_encoded_content_into_structured_data(self) -> None:
        encoded = parent_results_as_json(
            [ParentToolResult(tool_name="jira_search_issues", content='{"tickets": ["A-1"]}')],
        )
        decoded = json.loads(encoded)
        assert decoded["results"][0]["content"] == {"tickets": ["A-1"]}

    def test_truncates_when_over_max_bytes(self) -> None:
        big = ParentToolResult(tool_name="t", content="z" * 1000)
        encoded = parent_results_as_json([big], max_bytes=10)
        assert encoded is not None
        decoded = json.loads(encoded)
        assert decoded["_meta"]["truncated"] is True


class TestExtractDependencyResults:
    def test_extracts_single_dependency_from_goal(self) -> None:
        goal = (
            "## Results from prerequisite tasks\n\n"
            "### Result from prerequisite task 'fetch_jira'\n"
            '{"tickets": ["A-1", "A-2"]}\n\n'
            "## Your task\n\n"
            "Build a report from the tickets above."
        )
        messages: list[Message] = [UserMessage(content=goal)]
        results = extract_dependency_results(messages)
        assert len(results) == 1
        assert results[0].tool_name == "dependency:fetch_jira"
        assert '"tickets"' in results[0].content

    def test_extracts_multiple_dependencies(self) -> None:
        goal = (
            "## Results from prerequisite tasks\n\n"
            "### Result from prerequisite task 'fetch_jira'\n"
            "jira data here\n\n"
            "### Result from prerequisite task 'fetch_confluence'\n"
            "confluence data here\n\n"
            "## Your task\n\n"
            "Combine both."
        )
        messages: list[Message] = [UserMessage(content=goal)]
        results = extract_dependency_results(messages)
        assert len(results) == 2
        assert results[0].tool_name == "dependency:fetch_jira"
        assert "jira data" in results[0].content
        assert results[1].tool_name == "dependency:fetch_confluence"
        assert "confluence data" in results[1].content

    def test_returns_empty_when_no_dependency_header(self) -> None:
        messages: list[Message] = [
            UserMessage(content="Build a PDF report from Jira tickets."),
        ]
        results = extract_dependency_results(messages)
        assert results == []

    def test_returns_empty_when_no_user_message(self) -> None:
        messages: list[Message] = [
            _assistant_call("c1", "some_tool"),
        ]
        results = extract_dependency_results(messages)
        assert results == []

    def test_dependency_results_precede_tool_results_in_merge(self) -> None:
        """When merged in AgentTool.handle(), dependency results should
        come before the agent's own tool results."""
        goal = (
            "## Results from prerequisite tasks\n\n"
            "### Result from prerequisite task 'fetch_jira'\n"
            "upstream jira data\n\n"
            "## Your task\n\n"
            "Process this data."
        )
        messages: list[Message] = [
            UserMessage(content=goal),
            _assistant_call("c1", "local_tool"),
            ToolMessage(content="local result", tool_call_id="c1"),
        ]
        dep_results = extract_dependency_results(messages)
        tool_results = collect_parent_tool_results(messages)
        merged = dep_results + tool_results
        assert len(merged) == 2
        assert merged[0].tool_name == "dependency:fetch_jira"
        assert merged[1].tool_name == "local_tool"
