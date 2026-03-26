"""Unit tests for app.modules.agents.deep.context_manager — pure functions."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.modules.agents.deep.context_manager import (
    _compact_dict,
    _compact_list,
    _format_reference_data,
    _summarize_conversations_async,
    _summarize_conversations_sync,
    _truncate_string,
    build_conversation_messages,
    build_respond_conversation_context,
    build_sub_agent_context,
    compact_conversation_history,
    compact_conversation_history_async,
    compact_tool_results,
    consolidate_batch_summaries,
    group_tool_results_into_batches,
    summarize_batch,
    TRUNCATION_MARKER,
)

log = logging.getLogger("test")
log.setLevel(logging.CRITICAL)


# ============================================================================
# _truncate_string
# ============================================================================

class TestTruncateString:
    def test_short_string_unchanged(self):
        assert _truncate_string("hello", 100) == "hello"

    def test_exact_limit_unchanged(self):
        s = "x" * 50
        assert _truncate_string(s, 50) == s

    def test_long_string_truncated(self):
        s = "x" * 100
        result = _truncate_string(s, 50)
        assert len(result) < 100
        assert result.endswith(TRUNCATION_MARKER)
        assert result.startswith("x" * 50)


# ============================================================================
# _compact_list
# ============================================================================

class TestCompactList:
    def test_short_list_unchanged(self):
        lst = [1, 2]
        assert _compact_list(lst, 1000) == lst

    def test_long_list_keeps_first_three(self):
        lst = list(range(20))
        result = _compact_list(lst, 10)
        assert result[:3] == [0, 1, 2]
        assert len(result) == 4  # 3 items + note
        assert "_note" in result[3]
        assert "17 more items" in result[3]["_note"]

    def test_list_within_budget_unchanged(self):
        lst = [{"a": 1}]
        result = _compact_list(lst, 100000)
        assert result == lst

    def test_empty_list(self):
        assert _compact_list([], 100) == []


# ============================================================================
# _compact_dict
# ============================================================================

class TestCompactDict:
    def test_small_dict_unchanged(self):
        d = {"name": "test", "id": "123"}
        result = _compact_dict(d, 10000)
        assert result == d

    def test_priority_keys_preserved(self):
        d = {"id": "abc", "name": "test", "status": "ok",
             "massive_field": "x" * 5000}
        result = _compact_dict(d, 100)
        assert result["id"] == "abc"
        assert result["name"] == "test"
        assert result["status"] == "ok"

    def test_long_string_values_truncated(self):
        d = {"data": "x" * 500}
        result = _compact_dict(d, 50)
        if "data" in result:
            assert len(str(result["data"])) <= 250  # _STR_VALUE_MAX_LEN + "..."

    def test_budget_exhaustion_marks_truncated(self):
        d = {f"key_{i}": "value" * 100 for i in range(50)}
        result = _compact_dict(d, 100)
        assert result.get("_truncated") is True or len(result) < len(d)

    def test_nested_dict_compacted(self):
        d = {"id": "1", "nested": {"inner": "x" * 1000}}
        result = _compact_dict(d, 500)
        assert "id" in result

    def test_nested_list_compacted(self):
        d = {"id": "1", "items": list(range(100))}
        result = _compact_dict(d, 500)
        assert "id" in result

    def test_empty_dict(self):
        assert _compact_dict({}, 100) == {}


# ============================================================================
# compact_tool_results
# ============================================================================

class TestCompactToolResults:
    def test_preserves_metadata(self):
        results = [
            {"tool_name": "search", "status": "success", "duration_ms": 150,
             "result": "short result"},
        ]
        compacted = compact_tool_results(results)
        assert len(compacted) == 1
        assert compacted[0]["tool_name"] == "search"
        assert compacted[0]["status"] == "success"
        assert compacted[0]["duration_ms"] == 150

    def test_truncates_long_string_result(self):
        results = [
            {"tool_name": "t", "status": "ok", "result": "x" * 5000},
        ]
        compacted = compact_tool_results(results, max_chars=100)
        assert len(compacted[0]["result"]) < 5000

    def test_compacts_dict_result(self):
        results = [
            {"tool_name": "t", "status": "ok",
             "result": {"id": "keep", "data": "x" * 5000}},
        ]
        compacted = compact_tool_results(results, max_chars=500)
        assert compacted[0]["result"]["id"] == "keep"

    def test_compacts_list_result(self):
        results = [
            {"tool_name": "t", "status": "ok",
             "result": list(range(100))},
        ]
        compacted = compact_tool_results(results, max_chars=10)
        assert len(compacted[0]["result"]) <= 4  # 3 items + note

    def test_none_result(self):
        results = [{"tool_name": "t", "status": "ok", "result": None}]
        compacted = compact_tool_results(results)
        assert compacted[0]["result"] is None

    def test_non_string_non_dict_non_list_result(self):
        results = [{"tool_name": "t", "status": "ok", "result": 42}]
        compacted = compact_tool_results(results)
        assert "42" in compacted[0]["result"]

    def test_error_field_preserved(self):
        results = [{"tool_name": "t", "status": "error", "result": None,
                     "error": "timeout"}]
        compacted = compact_tool_results(results)
        assert compacted[0]["error"] == "timeout"

    def test_error_field_truncated(self):
        results = [{"tool_name": "t", "status": "error", "result": None,
                     "error": "x" * 1000}]
        compacted = compact_tool_results(results)
        assert len(compacted[0]["error"]) <= 500

    def test_no_duration_ms(self):
        results = [{"tool_name": "t", "status": "ok", "result": "r"}]
        compacted = compact_tool_results(results)
        assert "duration_ms" not in compacted[0]

    def test_empty_results(self):
        assert compact_tool_results([]) == []

    def test_missing_fields_defaults(self):
        results = [{}]
        compacted = compact_tool_results(results)
        assert compacted[0]["tool_name"] == "unknown"
        assert compacted[0]["status"] == "unknown"


# ============================================================================
# _format_reference_data
# ============================================================================

class TestFormatReferenceData:
    def test_empty_returns_empty(self):
        assert _format_reference_data([]) == ""

    def test_single_item(self):
        data = [{"type": "jira_issue", "key": "PA-1", "id": "123", "url": "https://x.com"}]
        result = _format_reference_data(data)
        assert "PA-1" in result
        assert "jira_issue" in result
        assert "https://x.com" in result

    def test_caps_at_50_items(self):
        data = [{"type": "issue", "key": f"K-{i}"} for i in range(100)]
        result = _format_reference_data(data)
        assert "K-49" in result
        assert "K-50" not in result

    def test_includes_all_standard_keys(self):
        data = [{"type": "t", "id": "1", "key": "k", "name": "n",
                 "title": "ti", "number": "99", "owner": "o",
                 "repo": "r", "url": "u"}]
        result = _format_reference_data(data)
        for val in ["1", "k", "n", "ti", "99", "o", "r", "u"]:
            assert val in result


# ============================================================================
# _summarize_conversations_sync
# ============================================================================

class TestSummarizeConversationsSync:
    def test_basic_summary(self):
        convs = [
            {"role": "user_query", "content": "What is X?"},
            {"role": "bot_response", "content": "X is Y."},
        ]
        result = _summarize_conversations_sync(convs, log)
        assert "Previous conversation summary" in result
        assert "User: What is X?" in result
        assert "Assistant: X is Y." in result

    def test_truncates_long_messages(self):
        convs = [
            {"role": "user_query", "content": "U" * 500},
            {"role": "bot_response", "content": "B" * 500},
        ]
        result = _summarize_conversations_sync(convs, log)
        assert "..." in result

    def test_skips_empty_content(self):
        convs = [{"role": "user_query", "content": ""}]
        result = _summarize_conversations_sync(convs, log)
        assert "User:" not in result

    def test_unknown_role_ignored(self):
        convs = [{"role": "system", "content": "hidden"}]
        result = _summarize_conversations_sync(convs, log)
        assert "hidden" not in result


# ============================================================================
# build_conversation_messages
# ============================================================================

class TestBuildConversationMessages:
    def test_empty_returns_empty(self):
        assert build_conversation_messages([], log) == []

    def test_user_and_bot_messages(self):
        convs = [
            {"role": "user_query", "content": "Hi"},
            {"role": "bot_response", "content": "Hello"},
        ]
        msgs = build_conversation_messages(convs, log)
        assert len(msgs) == 2
        assert isinstance(msgs[0], HumanMessage)
        assert isinstance(msgs[1], AIMessage)

    def test_sliding_window(self):
        convs = []
        for i in range(60):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})
        msgs = build_conversation_messages(convs, log, max_pairs=5)
        # 60 iterations = 60 pairs. Last 5 pairs = q55..q59 + a55..a59 = 10 messages
        assert len(msgs) == 10
        assert msgs[0].content == "q55"

    def test_empty_content_skipped(self):
        convs = [
            {"role": "user_query", "content": ""},
            {"role": "bot_response", "content": "response"},
        ]
        msgs = build_conversation_messages(convs, log)
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)

    def test_reference_data_appended(self):
        convs = [
            {"role": "user_query", "content": "search"},
            {"role": "bot_response", "content": "result",
             "referenceData": [{"type": "jira_issue", "key": "PA-1"}]},
        ]
        msgs = build_conversation_messages(convs, log, include_reference_data=True)
        assert len(msgs) == 2
        assert "PA-1" in msgs[1].content

    def test_reference_data_as_standalone_when_no_ai_last(self):
        convs = [
            {"role": "user_query", "content": "search"},
            {"role": "bot_response", "content": "result",
             "referenceData": [{"type": "issue", "key": "X"}]},
            {"role": "user_query", "content": "follow up"},
        ]
        msgs = build_conversation_messages(convs, log, include_reference_data=True)
        # Last message is HumanMessage, so ref data gets its own AIMessage
        assert isinstance(msgs[-1], (HumanMessage, AIMessage))

    def test_bot_response_without_user_creates_orphan_pair(self):
        convs = [{"role": "bot_response", "content": "orphan"}]
        msgs = build_conversation_messages(convs, log)
        assert len(msgs) == 1
        assert isinstance(msgs[0], AIMessage)


# ============================================================================
# build_respond_conversation_context
# ============================================================================

class TestBuildRespondConversationContext:
    def test_with_summary_and_recent(self):
        convs = [
            {"role": "user_query", "content": "What is X?"},
            {"role": "bot_response", "content": "X is Y."},
        ]
        msgs = build_respond_conversation_context(convs, "Prior chat about Z.", log)
        assert len(msgs) >= 2  # summary + messages
        assert "Prior chat about Z" in msgs[0].content

    def test_no_summary(self):
        convs = [
            {"role": "user_query", "content": "Hi"},
        ]
        msgs = build_respond_conversation_context(convs, None, log)
        assert len(msgs) == 1
        assert isinstance(msgs[0], HumanMessage)

    def test_empty_conversations(self):
        msgs = build_respond_conversation_context([], "summary", log)
        assert len(msgs) == 1  # just summary

    def test_truncates_long_bot_response(self):
        convs = [
            {"role": "bot_response", "content": "x" * 1000},
        ]
        msgs = build_respond_conversation_context(convs, None, log)
        assert "truncated" in msgs[0].content

    def test_recent_pairs_limit(self):
        convs = []
        for i in range(20):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})
        msgs = build_respond_conversation_context(convs, None, log, max_recent_pairs=2)
        # Last 4 items (2 pairs * 2)
        assert len(msgs) == 4

    def test_empty_content_skipped(self):
        convs = [{"role": "user_query", "content": ""}]
        msgs = build_respond_conversation_context(convs, None, log)
        assert len(msgs) == 0


# ============================================================================
# compact_conversation_history
# ============================================================================

class TestCompactConversationHistory:
    def test_empty_returns_none_empty(self):
        summary, recent = compact_conversation_history([], MagicMock(), log)
        assert summary is None
        assert recent == []

    def test_short_history_no_summary(self):
        convs = [
            {"role": "user_query", "content": "q1"},
            {"role": "bot_response", "content": "a1"},
        ]
        summary, recent = compact_conversation_history(convs, MagicMock(), log)
        assert summary is None
        assert recent == convs

    def test_long_history_splits(self):
        convs = []
        for i in range(20):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})
        summary, recent = compact_conversation_history(convs, MagicMock(), log)
        assert summary is not None
        assert "Previous conversation summary" in summary
        assert len(recent) == 10  # 5 pairs * 2


# ============================================================================
# compact_conversation_history_async
# ============================================================================

class TestCompactConversationHistoryAsync:
    @pytest.mark.asyncio
    async def test_empty_returns_none_empty(self):
        summary, recent = await compact_conversation_history_async([], MagicMock(), log)
        assert summary is None
        assert recent == []

    @pytest.mark.asyncio
    async def test_short_history_no_summary(self):
        convs = [
            {"role": "user_query", "content": "q"},
            {"role": "bot_response", "content": "a"},
        ]
        summary, recent = await compact_conversation_history_async(convs, MagicMock(), log)
        assert summary is None
        assert recent == convs

    @pytest.mark.asyncio
    async def test_long_history_uses_llm(self):
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "Summary of conversation"
        llm.ainvoke = AsyncMock(return_value=resp)

        convs = []
        for i in range(20):
            convs.append({"role": "user_query", "content": f"q{i}"})
            convs.append({"role": "bot_response", "content": f"a{i}"})

        summary, recent = await compact_conversation_history_async(convs, llm, log)
        assert summary is not None
        assert len(recent) == 10


# ============================================================================
# build_sub_agent_context
# ============================================================================

class TestBuildSubAgentContext:
    def test_basic_context(self):
        task = {"task_id": "t1", "description": "Do something"}
        ctx = build_sub_agent_context(task, [], None, "What is X?", log)
        assert "What is X?" in ctx

    def test_with_conversation_summary(self):
        task = {"task_id": "t1"}
        ctx = build_sub_agent_context(task, [], "Previous: discussed Y", "q", log)
        assert "Previous: discussed Y" in ctx

    def test_with_recent_conversations(self):
        task = {"task_id": "t1"}
        recent = [
            {"role": "user_query", "content": "prior q"},
            {"role": "bot_response", "content": "prior a"},
        ]
        ctx = build_sub_agent_context(task, [], None, "q", log, recent_conversations=recent)
        assert "prior q" in ctx
        assert "prior a" in ctx

    def test_dependency_results_included(self):
        task = {"task_id": "t2", "depends_on": ["t1"]}
        completed = [
            {"task_id": "t1", "status": "success",
             "result": {"response": "dependency output"}},
        ]
        ctx = build_sub_agent_context(task, completed, None, "q", log)
        assert "dependency output" in ctx

    def test_failed_dependency_noted(self):
        task = {"task_id": "t2", "depends_on": ["t1"]}
        completed = [
            {"task_id": "t1", "status": "error",
             "error": "timeout connecting to API"},
        ]
        ctx = build_sub_agent_context(task, completed, None, "q", log)
        assert "FAILED" in ctx
        assert "timeout" in ctx

    def test_no_dependencies(self):
        task = {"task_id": "t1"}
        ctx = build_sub_agent_context(task, [], None, "q", log)
        assert "previous steps" not in ctx.lower()

    def test_recent_conversations_limited_to_three(self):
        task = {"task_id": "t1"}
        recent = [{"role": "user_query", "content": f"q{i}"} for i in range(10)]
        ctx = build_sub_agent_context(task, [], None, "q", log, recent_conversations=recent)
        assert "q9" in ctx
        assert "q0" not in ctx

    def test_dependency_result_dict_without_response(self):
        task = {"task_id": "t2", "depends_on": ["t1"]}
        completed = [
            {"task_id": "t1", "status": "success",
             "result": {"data": "some value"}},
        ]
        ctx = build_sub_agent_context(task, completed, None, "q", log)
        assert "some value" in ctx

    def test_dependency_result_non_dict(self):
        task = {"task_id": "t2", "depends_on": ["t1"]}
        completed = [
            {"task_id": "t1", "status": "success", "result": "plain string result"},
        ]
        ctx = build_sub_agent_context(task, completed, None, "q", log)
        assert "plain string result" in ctx


# ============================================================================
# group_tool_results_into_batches
# ============================================================================

class TestGroupToolResultsIntoBatches:
    def test_empty_messages(self):
        assert group_tool_results_into_batches([]) == []

    def test_no_tool_messages(self):
        msgs = [HumanMessage(content="hi"), AIMessage(content="hello")]
        assert group_tool_results_into_batches(msgs) == []

    def test_single_tool_message(self):
        msg = ToolMessage(content="result data", tool_call_id="tc1", name="search")
        batches = group_tool_results_into_batches([msg])
        assert len(batches) == 1
        assert "[Tool: search]" in batches[0]
        assert "result data" in batches[0]

    def test_dict_content_serialized(self):
        msg = ToolMessage(content={"key": "value"}, tool_call_id="tc1", name="t")
        batches = group_tool_results_into_batches([msg])
        assert "key" in batches[0]
        assert "value" in batches[0]

    def test_list_content_serialized(self):
        msg = ToolMessage(content=[1, 2, 3], tool_call_id="tc1", name="t")
        batches = group_tool_results_into_batches([msg])
        assert "1" in batches[0] and "2" in batches[0] and "3" in batches[0]

    def test_batching_by_size(self):
        msgs = [
            ToolMessage(content="x" * 15000, tool_call_id=f"tc{i}", name=f"t{i}")
            for i in range(3)
        ]
        batches = group_tool_results_into_batches(msgs, max_chars_per_batch=20000)
        assert len(batches) >= 2

    def test_non_tool_messages_ignored(self):
        msgs = [
            HumanMessage(content="query"),
            ToolMessage(content="result", tool_call_id="tc1", name="t"),
            AIMessage(content="response"),
        ]
        batches = group_tool_results_into_batches(msgs)
        assert len(batches) == 1
        assert "result" in batches[0]

    def test_non_string_content(self):
        msg = ToolMessage(content=42, tool_call_id="tc1", name="t")
        batches = group_tool_results_into_batches([msg])
        assert "42" in batches[0]


# ============================================================================
# _summarize_conversations_async
# ============================================================================

class TestSummarizeConversationsAsync:
    """Tests for LLM-based async summarization."""

    @pytest.mark.asyncio
    async def test_success_returns_llm_summary(self):
        """LLM summarisation succeeds and returns trimmed content."""
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "  Concise summary of conversation  "
        llm.ainvoke = AsyncMock(return_value=resp)

        convs = [
            {"role": "user_query", "content": "What is X?"},
            {"role": "bot_response", "content": "X is Y."},
        ]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await _summarize_conversations_async(convs, llm, log)

        assert result == "Concise summary of conversation"
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_failure_falls_back_to_sync(self):
        """When the LLM call raises, the function falls back to sync summary."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("LLM unavailable"))

        convs = [
            {"role": "user_query", "content": "Hello"},
            {"role": "bot_response", "content": "Hi there"},
        ]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await _summarize_conversations_async(convs, llm, log)

        assert "Previous conversation summary" in result
        assert "User: Hello" in result

    @pytest.mark.asyncio
    async def test_empty_conversations_returns_empty(self):
        """All empty-content conversations yield an empty string."""
        llm = AsyncMock()
        convs = [
            {"role": "user_query", "content": ""},
            {"role": "bot_response", "content": ""},
        ]

        result = await _summarize_conversations_async(convs, llm, log)
        assert result == ""
        llm.ainvoke.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_response_without_content_attr(self):
        """When response has no .content attribute, falls back to str()."""
        llm = AsyncMock()
        resp = "plain string response"
        llm.ainvoke = AsyncMock(return_value=resp)

        convs = [
            {"role": "user_query", "content": "Q"},
            {"role": "bot_response", "content": "A"},
        ]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await _summarize_conversations_async(convs, llm, log)

        assert result == "plain string response"

    @pytest.mark.asyncio
    async def test_truncates_long_content_to_500(self):
        """Content passed to the prompt is truncated to 500 chars per message."""
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "summary"
        llm.ainvoke = AsyncMock(return_value=resp)

        convs = [
            {"role": "user_query", "content": "U" * 1000},
            {"role": "bot_response", "content": "B" * 1000},
        ]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            await _summarize_conversations_async(convs, llm, log)

        # Verify the prompt text was built (ainvoke was called)
        call_args = llm.ainvoke.call_args
        prompt_msgs = call_args[0][0]
        prompt_text = prompt_msgs[0].content
        # User content truncated to 500
        assert "U" * 500 in prompt_text
        assert "U" * 501 not in prompt_text


# ============================================================================
# summarize_batch
# ============================================================================

class TestSummarizeBatch:
    """Tests for batch summarization."""

    @pytest.mark.asyncio
    async def test_success_returns_stripped_content(self):
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "  Batch summary output  "
        llm.ainvoke = AsyncMock(return_value=resp)

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await summarize_batch(
                batch_text="tool result data",
                batch_number=1,
                total_batches=2,
                data_type="email",
                llm=llm,
                log=log,
            )

        assert result == "Batch summary output"
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_error_returns_fallback_json(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("timeout"))

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await summarize_batch(
                batch_text="some data here",
                batch_number=2,
                total_batches=3,
                data_type="calendar",
                llm=llm,
                log=log,
            )

        parsed = json.loads(result)
        assert parsed["item_count"] == 0
        assert "timeout" in parsed["error"]
        assert "some data here" in parsed["raw_preview"]

    @pytest.mark.asyncio
    async def test_response_without_content_attr(self):
        """Falls back to str(response) when .content is absent."""
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(return_value="raw string resp")

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await summarize_batch(
                batch_text="data",
                batch_number=1,
                total_batches=1,
                data_type="test",
                llm=llm,
                log=log,
            )

        assert result == "raw string resp"

    @pytest.mark.asyncio
    async def test_batch_text_capped_at_25000(self):
        """Safety cap should prevent excessively long prompts."""
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "ok"
        llm.ainvoke = AsyncMock(return_value=resp)

        # Use a character unlikely to appear in the prompt template
        long_data = "\u2603" * 30000  # snowman character

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            await summarize_batch(
                batch_text=long_data,
                batch_number=1,
                total_batches=1,
                data_type="test",
                llm=llm,
                log=log,
            )

        # The prompt includes batch_text[:25000] so it should not contain all 30k chars
        call_args = llm.ainvoke.call_args
        prompt_text = call_args[0][0][0].content
        char_count = prompt_text.count("\u2603")
        assert char_count <= 25000


# ============================================================================
# consolidate_batch_summaries
# ============================================================================

class TestConsolidateBatchSummaries:
    """Tests for merging multiple batch summaries."""

    @pytest.mark.asyncio
    async def test_success_returns_consolidated_content(self):
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "  ## Consolidated Report  "
        llm.ainvoke = AsyncMock(return_value=resp)

        summaries = ["Batch 1 summary", "Batch 2 summary"]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await consolidate_batch_summaries(
                batch_summaries=summaries,
                domain="outlook",
                task_description="Summarize recent emails",
                time_context="last 7 days",
                llm=llm,
                log=log,
            )

        assert result == "## Consolidated Report"
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_llm_error_returns_concatenated_fallback(self):
        llm = AsyncMock()
        llm.ainvoke = AsyncMock(side_effect=RuntimeError("API error"))

        summaries = ["Summary A", "Summary B"]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await consolidate_batch_summaries(
                batch_summaries=summaries,
                domain="slack",
                task_description="Find messages",
                time_context="",
                llm=llm,
                log=log,
            )

        assert "## Slack Summary" in result
        assert "**Batch 1**" in result
        assert "Summary A" in result
        assert "**Batch 2**" in result
        assert "Summary B" in result

    @pytest.mark.asyncio
    async def test_long_summaries_truncated(self):
        """Summaries exceeding _MAX_SUMMARIES_TEXT_LEN are capped."""
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "consolidated"
        llm.ainvoke = AsyncMock(return_value=resp)

        # Each summary is large enough that combined they exceed 50000
        summaries = ["x" * 30000, "y" * 30000]

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            result = await consolidate_batch_summaries(
                batch_summaries=summaries,
                domain="test",
                task_description="desc",
                time_context="now",
                llm=llm,
                log=log,
            )

        assert result == "consolidated"
        # Verify the prompt was still sent (with truncated input)
        llm.ainvoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_time_context_uses_not_specified(self):
        """When time_context is falsy the prompt gets 'Not specified'."""
        llm = AsyncMock()
        resp = MagicMock()
        resp.content = "report"
        llm.ainvoke = AsyncMock(return_value=resp)

        with patch("app.modules.agents.deep.state.get_opik_config", return_value={}):
            await consolidate_batch_summaries(
                batch_summaries=["s1"],
                domain="d",
                task_description="t",
                time_context="",
                llm=llm,
                log=log,
            )

        call_args = llm.ainvoke.call_args
        prompt_text = call_args[0][0][0].content
        assert "Not specified" in prompt_text
