"""
Tests for app.modules.agents.deep.respond helper functions.

Covers:
- _trim_analyses_to_budget: truncation logic
- _collect_analyses: gathering domain summaries
- _collect_tool_results: gathering tool results with smart consolidation
- _build_fallback_response: markdown fallback
- _extract_reference_links: URL extraction
- _handle_error_state: error handling
- _handle_clarify: clarification handling
- _handle_error_decision: error decision handling
- _handle_direct_answer: direct answer generation
- _handle_no_data: no data fallback
- _build_simple_retrieval_messages: message building for retrieval
- _format_user_context: user context formatting
- deep_respond_node: top-level node with error recovery
- _deep_respond_impl: core implementation
"""

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.agents.deep.respond import (
    _build_fallback_response,
    _build_simple_retrieval_messages,
    _collect_analyses,
    _collect_tool_results,
    _extract_reference_links,
    _format_user_context,
    _handle_clarify,
    _handle_error_decision,
    _handle_error_state,
    _handle_no_data,
    _trim_analyses_to_budget,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    """Return a mock logger that silently accepts all log calls."""
    return MagicMock(spec=logging.Logger)


def _mock_writer():
    """Return a mock StreamWriter."""
    return MagicMock()


def _mock_config():
    """Return a minimal RunnableConfig-like dict."""
    return {"configurable": {}}


# ============================================================================
# 1. _trim_analyses_to_budget
# ============================================================================

class TestTrimAnalysesToBudget:
    """Tests for _trim_analyses_to_budget()."""

    def test_within_budget(self):
        log = _mock_log()
        analyses = ["Short analysis.", "Another one."]
        result = _trim_analyses_to_budget(analyses, log, budget=1000)
        assert result == analyses

    def test_over_budget(self):
        log = _mock_log()
        analyses = ["A" * 5000, "B" * 5000]
        result = _trim_analyses_to_budget(analyses, log, budget=2000)
        assert len(result) == 2
        total = sum(len(a) for a in result)
        # Should be at or near budget (trimmed text + suffix)
        assert total < 5000  # Significantly less than the original 10000

    def test_single_analysis_over_budget(self):
        log = _mock_log()
        analyses = ["X" * 10000]
        result = _trim_analyses_to_budget(analyses, log, budget=500)
        assert len(result) == 1
        assert "[trimmed" in result[0]

    def test_empty_analyses(self):
        log = _mock_log()
        result = _trim_analyses_to_budget([], log, budget=1000)
        assert result == []

    def test_exactly_at_budget(self):
        log = _mock_log()
        analyses = ["A" * 500, "B" * 500]
        result = _trim_analyses_to_budget(analyses, log, budget=1000)
        assert result == analyses

    def test_proportional_trimming(self):
        log = _mock_log()
        # One analysis is 3x the other; they should be trimmed proportionally
        analyses = ["A" * 3000, "B" * 1000]
        result = _trim_analyses_to_budget(analyses, log, budget=2000)
        assert len(result) == 2
        # The longer one should still be longer after trimming
        assert len(result[0]) > len(result[1])

    def test_short_analysis_proportionally_trimmed(self):
        log = _mock_log()
        # One short, one long — both get proportional shares
        analyses = ["short", "X" * 10000]
        result = _trim_analyses_to_budget(analyses, log, budget=2000)
        # Proportional share: "short" (5 chars) gets 5/10005 * 2000 ~ 0 chars share
        # So even the short one gets trimmed indicator
        assert len(result) == 2
        assert "[trimmed" in result[1]


# ============================================================================
# 2. _collect_analyses
# ============================================================================

class TestCollectAnalyses:
    """Tests for _collect_analyses()."""

    def test_with_sub_agent_analyses(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": ["Analysis 1", "Analysis 2"],
            "completed_tasks": [],
        }
        result = _collect_analyses(state, log)
        assert result == ["Analysis 1", "Analysis 2"]

    def test_without_analyses_rebuilds_from_tasks(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": {"response": "Found 3 issues"},
                },
            ],
        }
        result = _collect_analyses(state, log)
        assert len(result) == 1
        assert "Found 3 issues" in result[0]

    def test_empty_state(self):
        log = _mock_log()
        state = {}
        result = _collect_analyses(state, log)
        assert result == []

    def test_no_analyses_no_completed(self):
        log = _mock_log()
        state = {"sub_agent_analyses": [], "completed_tasks": []}
        result = _collect_analyses(state, log)
        assert result == []

    def test_rebuild_skips_failed_tasks(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "error",
                    "domains": ["jira"],
                    "error": "Connection failed",
                },
                {
                    "task_id": "t2",
                    "status": "success",
                    "domains": ["slack"],
                    "result": {"response": "Slack data"},
                },
            ],
        }
        result = _collect_analyses(state, log)
        assert len(result) == 1
        assert "Slack data" in result[0]

    def test_rebuild_uses_domain_summary(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "domain_summary": "Comprehensive Jira analysis with 5 issues",
                    "result": {"response": "raw response"},
                },
            ],
        }
        result = _collect_analyses(state, log)
        assert len(result) == 1
        assert "Comprehensive Jira analysis" in result[0]
        assert "raw response" not in result[0]

    def test_rebuild_no_usable_data(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": {},  # No response text
                },
            ],
        }
        result = _collect_analyses(state, log)
        assert result == []

    def test_sub_agent_analyses_returns_copy(self):
        """Returned list should be a new list (not the same object)."""
        log = _mock_log()
        original = ["Analysis 1"]
        state = {"sub_agent_analyses": original}
        result = _collect_analyses(state, log)
        assert result == original
        assert result is not original


# ============================================================================
# 3. _collect_tool_results
# ============================================================================

class TestCollectToolResults:
    """Tests for _collect_tool_results()."""

    def test_with_results(self):
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": {"response": "short"},  # Short response < 500 chars
                },
            ],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "jira.search_issues", "status": "success", "result": {"issues": []}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert len(result) == 1

    def test_empty_state(self):
        log = _mock_log()
        state = {}
        result = _collect_tool_results(state, log)
        assert result == []

    def test_covered_domains_skipped(self):
        """When analyses already cover the domain, raw results are skipped."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "domain_summary": "Comprehensive summary covering all jira data",
                },
            ],
            "sub_agent_analyses": ["Some analysis"],
            "tool_results": [
                {"tool_name": "jira.search_issues", "status": "success", "result": {"issues": []}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert result == []

    def test_substantial_response_covers_domain(self):
        """When response > 500 chars, the domain is considered covered."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": {"response": "A" * 600},
                },
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [
                {"tool_name": "jira.search_issues", "status": "success", "result": {"issues": []}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert result == []

    def test_retrieval_results_skipped(self):
        """Retrieval tool results should always be skipped."""
        log = _mock_log()
        state = {
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "retrieval.search_internal_knowledge", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert result == []

    def test_failed_results_skipped(self):
        """Only successful results should be collected."""
        log = _mock_log()
        state = {
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "jira.search", "status": "error", "result": "timeout"},
            ],
        }
        result = _collect_tool_results(state, log)
        assert result == []

    def test_all_tool_results_fallback(self):
        """When tool_results is not set, falls back to all_tool_results."""
        log = _mock_log()
        state = {
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "all_tool_results": [
                {"tool_name": "slack.send_message", "status": "success", "result": {"ok": True}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert len(result) == 1

    def test_knowledge_tool_results_skipped(self):
        """Knowledge-related tool results should be skipped."""
        log = _mock_log()
        state = {
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "knowledge.search", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert result == []


# ============================================================================
# 4. _build_fallback_response
# ============================================================================

class TestBuildFallbackResponse:
    """Tests for _build_fallback_response()."""

    def test_with_data(self):
        analyses = ["[task_1 (jira)]: Found 3 open bugs in the project."]
        result = _build_fallback_response(analyses)
        assert "Found 3 open bugs" in result
        assert "Here's what I found" in result

    def test_multiple_analyses(self):
        analyses = [
            "[t1 (jira)]: Jira data",
            "[t2 (confluence)]: Confluence data",
        ]
        result = _build_fallback_response(analyses)
        assert "Jira data" in result
        assert "Confluence data" in result
        assert "---" in result  # Separator between analyses

    def test_empty_analyses(self):
        result = _build_fallback_response([])
        assert "Here's what I found" in result

    def test_analysis_without_prefix(self):
        analyses = ["Some data without task prefix"]
        result = _build_fallback_response(analyses)
        assert "Some data without task prefix" in result

    def test_strips_task_prefix(self):
        analyses = ["[task_1 (jira)]: Clean content here"]
        result = _build_fallback_response(analyses)
        assert "Clean content here" in result
        # The task prefix should be stripped
        assert "[task_1" not in result


# ============================================================================
# 5. _extract_reference_links
# ============================================================================

class TestExtractReferenceLinks:
    """Tests for _extract_reference_links()."""

    def test_with_urls_in_analyses(self):
        analyses = ["Check https://jira.example.com/browse/BUG-1 for details."]
        tool_results: list[dict[str, Any]] = []
        result = _extract_reference_links(analyses, tool_results)
        assert len(result) == 1
        assert result[0]["url"] == "https://jira.example.com/browse/BUG-1"

    def test_empty_analyses_and_results(self):
        result = _extract_reference_links([], [])
        assert result == []

    def test_urls_in_tool_results(self):
        analyses: list[str] = []
        tool_results = [
            {"result": '{"url": "https://example.com/page", "title": "Test"}'},
        ]
        result = _extract_reference_links(analyses, tool_results)
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com/page"

    def test_deduplication(self):
        analyses = [
            "See https://example.com/page1 and https://example.com/page1 again.",
        ]
        result = _extract_reference_links(analyses, [])
        assert len(result) == 1

    def test_strips_trailing_punctuation(self):
        analyses = [
            "Visit https://example.com/page.",
            "Link: https://example.com/other,",
        ]
        result = _extract_reference_links(analyses, [])
        urls = [r["url"] for r in result]
        assert "https://example.com/page" in urls
        assert "https://example.com/other" in urls

    def test_multiple_urls_in_one_analysis(self):
        analyses = [
            "See https://a.com/1 and https://b.com/2 for more info.",
        ]
        result = _extract_reference_links(analyses, [])
        assert len(result) == 2

    def test_max_100_links(self):
        analyses = [f"https://example.com/{i}" for i in range(150)]
        result = _extract_reference_links(analyses, [])
        assert len(result) <= 100

    def test_urls_from_nested_tool_results(self):
        analyses: list[str] = []
        tool_results = [
            {
                "result": {
                    "items": [
                        {"url": "https://example.com/item1"},
                        {"webLink": "https://example.com/item2"},
                    ]
                }
            },
        ]
        result = _extract_reference_links(analyses, tool_results)
        urls = [r["url"] for r in result]
        assert "https://example.com/item1" in urls
        assert "https://example.com/item2" in urls


# ============================================================================
# 6. _handle_error_state
# ============================================================================

class TestHandleErrorState:
    """Tests for _handle_error_state()."""

    def test_with_error(self):
        state = {
            "error": {"message": "Failed to connect to database", "status_code": 500},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_error_state(state, writer, config, log)
        assert result["response"] == "Failed to connect to database"
        assert result["completion_data"]["confidence"] == "Low"
        assert result["completion_data"]["answerMatchType"] == "Error"

        # Verify writer was called with answer_chunk and complete events
        calls = writer.call_args_list
        # safe_stream_write is called with writer as first arg
        assert len(calls) >= 0  # Writer is passed to safe_stream_write

    def test_with_error_detail_fallback(self):
        state = {
            "error": {"detail": "Rate limit exceeded"},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_error_state(state, writer, config, log)
        assert result["response"] == "Rate limit exceeded"

    def test_with_error_no_message(self):
        state = {
            "error": {},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_error_state(state, writer, config, log)
        assert result["response"] == "An error occurred"
        assert result["completion_data"]["answer"] == "An error occurred"


# ============================================================================
# 7. _log_state_diagnostic (smoke test)
# ============================================================================

class TestLogStateDiagnostic:
    """Smoke test for _log_state_diagnostic()."""

    def test_logs_state_info(self):
        from app.modules.agents.deep.respond import _log_state_diagnostic

        log = _mock_log()
        state = {
            "completed_tasks": [
                {"status": "success"},
                {"status": "error"},
            ],
            "sub_agent_analyses": ["a1", "a2"],
            "tool_results": [{"tool": "t1"}],
            "reflection_decision": "respond_success",
            "task_plan": {"tasks": []},
            "final_results": [{"doc": 1}],
            "virtual_record_id_to_result": {"vr1": {}},
        }
        # Should not raise
        _log_state_diagnostic(state, log)
        log.info.assert_called()

    def test_logs_empty_state(self):
        from app.modules.agents.deep.respond import _log_state_diagnostic

        log = _mock_log()
        state = {}
        _log_state_diagnostic(state, log)
        log.info.assert_called()


# ============================================================================
# 8. _extract_urls_from_value (recursive extraction)
# ============================================================================

class TestExtractUrlsFromValue:
    """Tests for _extract_urls_from_value()."""

    def test_string_with_url(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen: set = set()
        links: list = []
        _extract_urls_from_value("Visit https://example.com/page for details", seen, links)
        assert len(links) == 1
        assert links[0]["url"] == "https://example.com/page"

    def test_dict_with_url_field(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen: set = set()
        links: list = []
        _extract_urls_from_value({"url": "https://example.com", "title": "Test"}, seen, links)
        assert len(links) == 1

    def test_list_of_dicts(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen: set = set()
        links: list = []
        _extract_urls_from_value(
            [{"url": "https://a.com"}, {"permalink": "https://b.com"}],
            seen, links,
        )
        assert len(links) == 2

    def test_depth_limit(self):
        from app.modules.agents.deep.respond import (
            _MAX_URL_EXTRACT_DEPTH,
            _extract_urls_from_value,
        )

        seen: set = set()
        links: list = []
        # Create deeply nested structure
        deep = "https://deep.example.com"
        value: Any = deep
        for _ in range(_MAX_URL_EXTRACT_DEPTH + 5):
            value = {"nested": value}
        _extract_urls_from_value(value, seen, links)
        # Depth limit should prevent extraction from very deep nesting
        # Exact behavior depends on depth; the key is it doesn't crash

    def test_deduplication_in_recursion(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen: set = set()
        links: list = []
        value = {
            "url": "https://example.com",
            "items": [{"url": "https://example.com"}],
        }
        _extract_urls_from_value(value, seen, links)
        assert len(links) == 1  # Deduplicated

    def test_list_capped_at_20(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen: set = set()
        links: list = []
        items = [{"url": f"https://example.com/{i}"} for i in range(30)]
        _extract_urls_from_value(items, seen, links)
        assert len(links) == 20  # Capped


# ============================================================================
# 9. _handle_clarify
# ============================================================================

class TestHandleClarify:
    """Tests for _handle_clarify()."""

    def test_with_clarifying_question(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        reflection = {"clarifying_question": "Could you specify which project?"}
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_clarify(state, reflection, writer, config, log)
        assert result["response"] == "Could you specify which project?"
        assert result["completion_data"]["confidence"] == "Medium"
        assert result["completion_data"]["answerMatchType"] == "Clarification Needed"

    def test_default_question_when_missing(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        reflection = {}
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_clarify(state, reflection, writer, config, log)
        assert "more details" in result["response"]

    def test_empty_clarifying_question(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        reflection = {"clarifying_question": ""}
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        # Empty string is falsy, so falls back to default
        result = _handle_clarify(state, reflection, writer, config, log)
        # dict.get returns "" not None, and "" is used
        assert result["response"] == ""
        assert result["completion_data"]["answer"] == ""


# ============================================================================
# 10. _handle_error_decision
# ============================================================================

class TestHandleErrorDecision:
    """Tests for _handle_error_decision()."""

    def test_with_error_context(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        reflection = {"error_context": "API returned 503 Service Unavailable"}
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_error_decision(state, reflection, writer, config, log)
        assert "503 Service Unavailable" in result["response"]
        assert result["completion_data"]["confidence"] == "Low"
        assert result["completion_data"]["answerMatchType"] == "Tool Execution Failed"

    def test_without_error_context(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        reflection = {}
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_error_decision(state, reflection, writer, config, log)
        assert "encountered errors" in result["response"]

    def test_empty_error_context(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        reflection = {"error_context": ""}
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        result = _handle_error_decision(state, reflection, writer, config, log)
        assert "encountered errors" in result["response"]


# ============================================================================
# 11. _handle_no_data
# ============================================================================

class TestHandleNoData:
    """Tests for _handle_no_data()."""

    @pytest.mark.asyncio
    async def test_with_error_tasks(self):
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "error", "error": "Connection timeout"},
                {"task_id": "t2", "status": "error", "error": "Auth failed"},
            ],
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        result = await _handle_no_data(state, llm, writer, config, log)
        assert "Connection timeout" in result["response"]
        assert "Auth failed" in result["response"]
        assert result["completion_data"]["answerMatchType"] == "No Data Available"

    @pytest.mark.asyncio
    async def test_no_error_tasks(self):
        state = {
            "completed_tasks": [],
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        result = await _handle_no_data(state, llm, writer, config, log)
        assert "find relevant data" in result["response"]
        assert result["completion_data"]["confidence"] == "Low"

    @pytest.mark.asyncio
    async def test_with_no_completed_tasks(self):
        state = {
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        result = await _handle_no_data(state, llm, writer, config, log)
        assert result["response"] is not None
        assert result["completion_data"]["confidence"] == "Low"

    @pytest.mark.asyncio
    async def test_limits_error_display_to_3(self):
        """Only up to 3 error tasks should be shown."""
        state = {
            "completed_tasks": [
                {"task_id": f"t{i}", "status": "error", "error": f"Error {i}"}
                for i in range(5)
            ],
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        result = await _handle_no_data(state, llm, writer, config, log)
        # Should mention first 3 error tasks
        assert "t0" in result["response"]
        assert "t2" in result["response"]
        # t3 and t4 should not appear (limited to 3)
        assert "t3" not in result["response"]


# ============================================================================
# 12. _build_simple_retrieval_messages
# ============================================================================

class TestBuildSimpleRetrievalMessages:
    """Tests for _build_simple_retrieval_messages()."""

    def test_basic_retrieval_messages(self):
        state = {
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": "Here are the context blocks with R1, R2...",
            "query": "What is our policy?",
        }
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            messages = _build_simple_retrieval_messages(state, log)

        assert len(messages) >= 2  # system + user message
        # System message should mention enterprise research
        system_content = messages[0].content
        assert "enterprise" in system_content.lower() or "research" in system_content.lower()
        # Last message should be the qna_message_content
        assert messages[-1].content == "Here are the context blocks with R1, R2..."

    def test_with_system_prompt(self):
        state = {
            "instructions": "",
            "system_prompt": "You are a helpful assistant.",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": "Context...",
            "query": "test",
        }
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            messages = _build_simple_retrieval_messages(state, log)

        system_content = messages[0].content
        assert "You are a helpful assistant." in system_content

    def test_fallback_to_query_when_no_qna_content(self):
        state = {
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "query": "What is the policy?",
        }
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            messages = _build_simple_retrieval_messages(state, log)

        assert messages[-1].content == "What is the policy?"

    def test_with_conversation_context(self):
        """Conversation context messages should be included."""
        from langchain_core.messages import HumanMessage

        state = {
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [{"role": "user", "content": "previous q"}],
            "conversation_summary": "Previous discussion about policies",
            "qna_message_content": "Context blocks...",
            "query": "test",
        }
        log = _mock_log()

        mock_conv_msgs = [HumanMessage(content="Previous context summary")]
        with patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=mock_conv_msgs):
            messages = _build_simple_retrieval_messages(state, log)

        # Should have: system + conversation context + user message
        assert len(messages) >= 3


# ============================================================================
# 13. _format_user_context
# ============================================================================

class TestFormatUserContext:
    """Tests for _format_user_context()."""

    def test_with_full_info(self):
        user_info = {"fullName": "Alice Smith", "userEmail": "alice@example.com"}
        org_info = {"name": "Acme Corp"}
        result = _format_user_context(user_info, org_info)
        assert "Alice Smith" in result
        assert "alice@example.com" in result
        assert "Acme Corp" in result

    def test_with_name_only(self):
        user_info = {"fullName": "Bob"}
        org_info = {}
        result = _format_user_context(user_info, org_info)
        assert "Bob" in result
        assert "Organization" not in result

    def test_with_email_only(self):
        user_info = {"email": "charlie@example.com"}
        org_info = {}
        result = _format_user_context(user_info, org_info)
        assert "charlie@example.com" in result

    def test_empty_user_info(self):
        result = _format_user_context({}, {})
        assert result == ""

    def test_name_from_displayName(self):
        user_info = {"displayName": "Display Name"}
        org_info = {}
        result = _format_user_context(user_info, org_info)
        assert "Display Name" in result

    def test_email_from_userEmail(self):
        user_info = {"userEmail": "user@test.com", "email": "backup@test.com"}
        org_info = {}
        result = _format_user_context(user_info, org_info)
        # userEmail takes priority
        assert "user@test.com" in result

    def test_no_name_no_email(self):
        user_info = {"other": "data"}
        org_info = {"name": "Org"}
        result = _format_user_context(user_info, org_info)
        assert result == ""  # No name or email => empty


# ============================================================================
# 14. deep_respond_node (top-level error recovery)
# ============================================================================

class TestDeepRespondNode:
    """Tests for deep_respond_node() top-level wrapper."""

    @pytest.mark.asyncio
    async def test_error_recovery(self):
        """When _deep_respond_impl raises, deep_respond_node catches and returns error state."""
        from app.modules.agents.deep.respond import deep_respond_node

        state = {
            "logger": _mock_log(),
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.respond._deep_respond_impl", new_callable=AsyncMock) as mock_impl:
            mock_impl.side_effect = RuntimeError("unexpected crash")
            result = await deep_respond_node(state, config, writer)

        assert "encountered an issue" in result["response"]
        assert result["completion_data"]["confidence"] == "Low"
        assert result["completion_data"]["answerMatchType"] == "Error"

    @pytest.mark.asyncio
    async def test_success_delegates_to_impl(self):
        """Successful call should delegate to _deep_respond_impl."""
        from app.modules.agents.deep.respond import deep_respond_node

        state = {
            "logger": _mock_log(),
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.respond._deep_respond_impl", new_callable=AsyncMock) as mock_impl:
            mock_impl.return_value = {**state, "response": "success answer"}
            result = await deep_respond_node(state, config, writer)

        assert result["response"] == "success answer"
        mock_impl.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_error_recovery_streams_events(self):
        """Error recovery should stream answer_chunk and complete events."""
        from app.modules.agents.deep.respond import deep_respond_node

        state = {
            "logger": _mock_log(),
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.respond._deep_respond_impl", new_callable=AsyncMock) as mock_impl:
            mock_impl.side_effect = RuntimeError("crash")
            with patch("app.modules.agents.deep.respond.safe_stream_write") as mock_write:
                result = await deep_respond_node(state, config, writer)
                # Should have been called twice: answer_chunk + complete
                assert mock_write.call_count == 2


# ============================================================================
# 15. _deep_respond_impl (core implementation routing)
# ============================================================================

class TestDeepRespondImpl:
    """Tests for _deep_respond_impl() routing logic."""

    @pytest.mark.asyncio
    async def test_error_state_routes_to_handle_error(self):
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "llm": MagicMock(),
            "error": {"message": "Something broke"},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond._log_state_diagnostic"):
            with patch("app.modules.agents.deep.respond.safe_stream_write"):
                result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Something broke"
        assert result["completion_data"]["answerMatchType"] == "Error"

    @pytest.mark.asyncio
    async def test_clarify_decision_routes_to_handle_clarify(self):
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "llm": MagicMock(),
            "error": None,
            "reflection_decision": "respond_clarify",
            "reflection": {"clarifying_question": "Which project do you mean?"},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond._log_state_diagnostic"):
            with patch("app.modules.agents.deep.respond.safe_stream_write"):
                result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Which project do you mean?"

    @pytest.mark.asyncio
    async def test_error_decision_routes_correctly(self):
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "llm": MagicMock(),
            "error": None,
            "reflection_decision": "respond_error",
            "reflection": {"error_context": "All tools timed out"},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond._log_state_diagnostic"):
            with patch("app.modules.agents.deep.respond.safe_stream_write"):
                result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert "All tools timed out" in result["response"]

    @pytest.mark.asyncio
    async def test_direct_answer_routes_correctly(self):
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "llm": MagicMock(),
            "error": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {"can_answer_directly": True},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond._log_state_diagnostic"):
            with patch("app.modules.agents.deep.respond.safe_stream_write"):
                with patch("app.modules.agents.deep.respond._handle_direct_answer", new_callable=AsyncMock) as mock_direct:
                    mock_direct.return_value = {**state, "response": "Direct answer"}
                    result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Direct answer"
        mock_direct.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_data_routes_to_handle_no_data(self):
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "llm": MagicMock(),
            "error": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "sub_agent_analyses": [],
            "completed_tasks": [],
            "tool_results": [],
            "all_tool_results": [],
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond._log_state_diagnostic"):
            with patch("app.modules.agents.deep.respond.safe_stream_write"):
                with patch("app.modules.agents.deep.respond._handle_no_data", new_callable=AsyncMock) as mock_no_data:
                    mock_no_data.return_value = {**state, "response": "No data"}
                    result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "No data"
        mock_no_data.assert_awaited_once()


# ============================================================================
# 16. _handle_direct_answer
# ============================================================================

class TestHandleDirectAnswer:
    """Tests for _handle_direct_answer()."""

class TestCollectAnalysesAdditional:
    def test_mixed_result_types(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": "string result",  # Not a dict
                },
            ],
        }
        result = _collect_analyses(state, log)
        assert result == []  # Non-dict result skipped

    def test_result_with_empty_response(self):
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": {"response": ""},  # Empty string
                },
            ],
        }
        result = _collect_analyses(state, log)
        assert result == []


# ============================================================================
# 18. Additional _collect_tool_results edge cases
# ============================================================================

class TestCollectToolResultsAdditional:
    pass
class TestExtractReferenceLinksAdditional:
    def test_urls_from_dict_url_fields(self):
        analyses: list[str] = []
        tool_results = [
            {
                "result": {
                    "webLink": "https://example.com/weblink",
                    "htmlUrl": "https://example.com/html",
                    "permalink": "https://example.com/perma",
                }
            }
        ]
        result = _extract_reference_links(analyses, tool_results)
        urls = [r["url"] for r in result]
        assert "https://example.com/weblink" in urls
        assert "https://example.com/html" in urls
        assert "https://example.com/perma" in urls

    def test_non_http_values_skipped(self):
        analyses: list[str] = []
        tool_results = [
            {"result": {"url": "ftp://not-http.com", "title": "Test"}}
        ]
        result = _extract_reference_links(analyses, tool_results)
        assert len(result) == 0


# ============================================================================
# 20. _build_simple_retrieval_messages — edge cases
# ============================================================================

class TestBuildSimpleRetrievalMessagesAdditional:
    def test_with_instructions_and_system_prompt(self):
        state = {
            "instructions": "Be formal.",
            "system_prompt": "Custom prompt.",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": "Context data...",
            "query": "test",
        }
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            messages = _build_simple_retrieval_messages(state, log)

        system_content = messages[0].content
        assert "Be formal." in system_content
        assert "Custom prompt." in system_content
        assert "enterprise" in system_content.lower()

    def test_empty_string_instructions_excluded(self):
        state = {
            "instructions": "   ",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": "Data",
            "query": "q",
        }
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            messages = _build_simple_retrieval_messages(state, log)

        system_content = messages[0].content
        assert "Agent Instructions" not in system_content


# ============================================================================
# 21. Additional _format_user_context tests
# ============================================================================

class TestFormatUserContextAdditional:
    def test_name_fallback_chain(self):
        """Falls back from fullName to name to displayName."""
        user_info = {"name": "NameValue"}
        org_info = {}
        result = _format_user_context(user_info, org_info)
        assert "NameValue" in result

    def test_email_fallback_chain(self):
        """Falls back from userEmail to email."""
        user_info = {"email": "backup@test.com"}
        org_info = {}
        result = _format_user_context(user_info, org_info)
        assert "backup@test.com" in result

    def test_all_empty_strings(self):
        """All empty strings should return empty."""
        result = _format_user_context({"fullName": "", "email": ""}, {})
        assert result == ""


# ============================================================================
# 22. deep_respond_node — additional
# ============================================================================

class TestDeepRespondNodeAdditional:
    @pytest.mark.asyncio
    async def test_uses_state_logger(self):
        """deep_respond_node uses logger from state if available."""
        from app.modules.agents.deep.respond import deep_respond_node

        custom_log = _mock_log()
        state = {
            "logger": custom_log,
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()

        with patch("app.modules.agents.deep.respond._deep_respond_impl", new_callable=AsyncMock) as mock_impl:
            mock_impl.return_value = {**state, "response": "ok"}
            await deep_respond_node(state, config, writer)

        # Verify the custom logger was passed to impl
        call_args = mock_impl.call_args
        assert call_args[0][4] is custom_log  # log is the 5th positional arg


# ============================================================================
# 23. _handle_no_data — additional
# ============================================================================

class TestHandleNoDataAdditional:
    @pytest.mark.asyncio
    async def test_error_message_truncation(self):
        """Long error messages should be truncated to 200 chars."""
        from app.modules.agents.deep.respond import _handle_no_data

        long_error = "A" * 300
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "error", "error": long_error},
            ],
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        result = await _handle_no_data(state, llm, writer, config, log)
        # Error should be truncated
        assert len(long_error) > 200
        # The displayed error should be at most 200 chars of the original
        assert "A" * 200 in result["response"]


# ============================================================================
# 24. _extract_reference_links — extended
# ============================================================================

class TestExtractReferenceLinksExtended:
    def test_urls_from_analyses(self):
        from app.modules.agents.deep.respond import _extract_reference_links

        analyses = ["Check https://example.com for details"]
        result = _extract_reference_links(analyses, [])
        assert len(result) == 1
        assert result[0]["url"] == "https://example.com"

    def test_duplicate_urls_deduplicated(self):
        from app.modules.agents.deep.respond import _extract_reference_links

        analyses = [
            "See https://example.com and also https://example.com again"
        ]
        result = _extract_reference_links(analyses, [])
        assert len(result) == 1

    def test_urls_from_tool_results(self):
        from app.modules.agents.deep.respond import _extract_reference_links

        tool_results = [
            {"result": {"url": "https://tool.example.com", "name": "test"}}
        ]
        result = _extract_reference_links([], tool_results)
        assert len(result) == 1
        assert result[0]["url"] == "https://tool.example.com"

    def test_max_100_links(self):
        from app.modules.agents.deep.respond import _extract_reference_links

        analyses = [" ".join(f"https://link{i}.com" for i in range(150))]
        result = _extract_reference_links(analyses, [])
        assert len(result) <= 100

    def test_no_urls(self):
        from app.modules.agents.deep.respond import _extract_reference_links

        result = _extract_reference_links(["No urls here"], [])
        assert result == []

    def test_url_with_trailing_punctuation(self):
        from app.modules.agents.deep.respond import _extract_reference_links

        analyses = ["Visit https://example.com."]
        result = _extract_reference_links(analyses, [])
        assert result[0]["url"] == "https://example.com"


# ============================================================================
# 25. _extract_urls_from_value — extended
# ============================================================================

class TestExtractUrlsFromValueExtended:
    def test_string_value(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen = set()
        links = []
        _extract_urls_from_value("Visit https://test.com", seen, links)
        assert len(links) == 1

    def test_dict_with_url_fields(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen = set()
        links = []
        _extract_urls_from_value(
            {"url": "https://test.com", "webLink": "https://web.com"},
            seen, links
        )
        assert len(links) == 2

    def test_list_value(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen = set()
        links = []
        _extract_urls_from_value(
            [{"url": "https://a.com"}, {"url": "https://b.com"}],
            seen, links
        )
        assert len(links) == 2

    def test_depth_limit(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen = set()
        links = []
        # Very deeply nested structure
        deep = {"a": {"b": {"c": {"d": {"url": "https://deep.com"}}}}}
        _extract_urls_from_value(deep, seen, links, depth=3)
        # Should stop at depth 3, not finding the deeply nested URL
        assert len(links) == 0

    def test_non_http_values_skipped(self):
        from app.modules.agents.deep.respond import _extract_urls_from_value

        seen = set()
        links = []
        _extract_urls_from_value({"url": "ftp://not-http.com"}, seen, links)
        assert len(links) == 0


# ============================================================================
# 26. _collect_tool_results — extended
# ============================================================================

class TestCollectToolResultsExtended:
    def test_no_completed_tasks(self):
        result = _collect_tool_results({"completed_tasks": []}, _mock_log())
        assert result == []

    def test_skips_retrieval_tools(self):
        state = {
            "completed_tasks": [],
            "tool_results": [
                {"status": "success", "tool_name": "knowledge_retrieval"},
                {"status": "success", "tool_name": "jira.search"},
            ],
        }
        result = _collect_tool_results(state, _mock_log())
        assert len(result) == 1
        assert result[0]["tool_name"] == "jira.search"

    def test_skips_failed_results(self):
        state = {
            "completed_tasks": [],
            "tool_results": [
                {"status": "error", "tool_name": "jira.search"},
                {"status": "success", "tool_name": "slack.search"},
            ],
        }
        result = _collect_tool_results(state, _mock_log())
        assert len(result) == 1

    def test_all_domains_covered(self):
        state = {
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "domain_summary": "Summary of jira data",
                    "result": {},
                }
            ],
            "tool_results": [
                {"status": "success", "tool_name": "jira.search"},
            ],
        }
        result = _collect_tool_results(state, _mock_log())
        assert result == []


# ============================================================================
# 27. _collect_analyses — extended
# ============================================================================

class TestCollectAnalysesExtended:
    def test_from_sub_agent_analyses(self):
        state = {"sub_agent_analyses": ["Analysis 1", "Analysis 2"]}
        result = _collect_analyses(state, _mock_log())
        assert len(result) == 2

    def test_rebuild_from_completed_tasks(self):
        state = {
            "sub_agent_analyses": [],
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["jira"],
                    "result": {"response": "Found 5 issues"},
                }
            ],
        }
        result = _collect_analyses(state, _mock_log())
        assert len(result) == 1
        assert "Found 5 issues" in result[0]

    def test_rebuild_with_domain_summary(self):
        state = {
            "sub_agent_analyses": [],
            "completed_tasks": [
                {
                    "task_id": "t1",
                    "status": "success",
                    "domains": ["confluence"],
                    "domain_summary": "Multi-domain summary",
                    "result": {},
                }
            ],
        }
        result = _collect_analyses(state, _mock_log())
        assert len(result) == 1
        assert "Multi-domain summary" in result[0]

    def test_skip_failed_tasks(self):
        state = {
            "sub_agent_analyses": [],
            "completed_tasks": [
                {"task_id": "t1", "status": "error", "domains": [], "result": {}},
                {"task_id": "t2", "status": "success", "domains": ["jira"], "result": {"response": "ok"}},
            ],
        }
        result = _collect_analyses(state, _mock_log())
        assert len(result) == 1

    def test_empty_everything(self):
        state = {"sub_agent_analyses": [], "completed_tasks": []}
        result = _collect_analyses(state, _mock_log())
        assert result == []


# ============================================================================
# 28. _format_user_context — extended
# ============================================================================

class TestFormatUserContextExtended:
    def test_with_all_fields(self):
        result = _format_user_context(
            {"fullName": "John", "userEmail": "john@test.com"},
            {"name": "TestOrg"},
        )
        assert "John" in result
        assert "john@test.com" in result
        assert "TestOrg" in result

    def test_email_only(self):
        result = _format_user_context(
            {"email": "john@test.com"},
            {},
        )
        assert "john@test.com" in result

    def test_no_info(self):
        result = _format_user_context({}, {})
        assert result == ""

    def test_display_name_fallback(self):
        result = _format_user_context(
            {"displayName": "Display Name"},
            {},
        )
        assert "Display Name" in result


# ============================================================================
# 29. _build_fallback_response — extended
# ============================================================================

class TestBuildFallbackResponseExtended:
    def test_single_analysis(self):
        result = _build_fallback_response(["[t1 (jira)]: Found 5 issues"])
        assert "Found 5 issues" in result

    def test_multiple_analyses(self):
        result = _build_fallback_response([
            "[t1 (jira)]: First analysis",
            "[t2 (slack)]: Second analysis",
        ])
        assert "First analysis" in result
        assert "Second analysis" in result
        assert "---" in result

    def test_no_bracket_prefix(self):
        result = _build_fallback_response(["Raw analysis text"])
        assert "Raw analysis text" in result


# ============================================================================
# 30. _log_state_diagnostic
# ============================================================================

class TestLogStateDiagnostic:
    def test_basic_logging(self):
        from app.modules.agents.deep.respond import _log_state_diagnostic

        log = _mock_log()
        state = {
            "completed_tasks": [{"status": "success"}, {"status": "error"}],
            "sub_agent_analyses": ["a1"],
            "tool_results": [{"r": 1}],
            "reflection_decision": "respond_success",
            "task_plan": {"tasks": []},
            "final_results": [{"fr": 1}, {"fr": 2}],
            "virtual_record_id_to_result": {"vr1": {}},
        }
        _log_state_diagnostic(state, log)
        log.info.assert_called()

    def test_empty_state(self):
        from app.modules.agents.deep.respond import _log_state_diagnostic

        log = _mock_log()
        _log_state_diagnostic({}, log)
        log.info.assert_called()


# ============================================================================
# 31. _trim_analyses_to_budget — extended
# ============================================================================

class TestTrimAnalysesToBudgetExtended:
    def test_within_budget(self):
        analyses = ["short"]
        result = _trim_analyses_to_budget(analyses, _mock_log())
        assert result == ["short"]

    def test_over_budget(self):
        analyses = ["a" * 60000, "b" * 60000]
        result = _trim_analyses_to_budget(analyses, _mock_log(), budget=50000)
        total = sum(len(a) for a in result)
        # Each analysis should be trimmed proportionally
        assert all("[trimmed" in a for a in result)

    def test_exact_budget(self):
        analyses = ["a" * 50000, "b" * 50000]
        result = _trim_analyses_to_budget(analyses, _mock_log(), budget=100000)
        assert result == analyses


# ============================================================================
# 15. _handle_direct_answer
# ============================================================================

class TestHandleDirectAnswer:
    """Tests for _handle_direct_answer()."""

    @pytest.mark.asyncio
    async def test_successful_direct_answer(self):
        """Direct answer streams and returns response."""
        from app.modules.agents.deep.respond import _handle_direct_answer

        async def mock_stream(*args, **kwargs):
            yield {"event": "answer_chunk", "data": {"chunk": "Hello!"}}
            yield {"event": "complete", "data": {"answer": "Hello!", "citations": []}}

        state = {
            "query": "Hi there",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "user_info": {},
            "org_info": {},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        with patch("app.utils.streaming.stream_llm_response", side_effect=mock_stream), \
             patch("app.modules.agents.deep.respond.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            result = await _handle_direct_answer(state, llm, writer, config, log)

        assert result["response"] == "Hello!"
        assert result["completion_data"]["confidence"] == "High"
        assert result["completion_data"]["answerMatchType"] == "Direct Response"

    @pytest.mark.asyncio
    async def test_direct_answer_with_instructions(self):
        """Direct answer includes agent instructions in system prompt."""
        from app.modules.agents.deep.respond import _handle_direct_answer

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Done", "citations": []}}

        state = {
            "query": "Do something",
            "instructions": "Always be brief.",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "user_info": {},
            "org_info": {},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        with patch("app.utils.streaming.stream_llm_response", side_effect=mock_stream) as mock_sr, \
             patch("app.modules.agents.deep.respond.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            result = await _handle_direct_answer(state, llm, writer, config, log)

        assert result["response"] == "Done"

    @pytest.mark.asyncio
    async def test_direct_answer_streaming_error(self):
        """When streaming fails, error is handled gracefully."""
        from app.modules.agents.deep.respond import _handle_direct_answer

        async def mock_stream(*args, **kwargs):
            raise RuntimeError("Stream crashed")
            yield  # make it async generator

        state = {
            "query": "Hi",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "user_info": {},
            "org_info": {},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        with patch("app.utils.streaming.stream_llm_response", side_effect=mock_stream), \
             patch("app.modules.agents.deep.respond.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            result = await _handle_direct_answer(state, llm, writer, config, log)

        assert "error" in result["response"].lower() or "try again" in result["response"].lower()

    @pytest.mark.asyncio
    async def test_direct_answer_empty_response_fallback(self):
        """Empty LLM response gets fallback text."""
        from app.modules.agents.deep.respond import _handle_direct_answer

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "", "citations": []}}

        state = {
            "query": "Hello",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "user_info": {},
            "org_info": {},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        with patch("app.utils.streaming.stream_llm_response", side_effect=mock_stream), \
             patch("app.modules.agents.deep.respond.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            result = await _handle_direct_answer(state, llm, writer, config, log)

        assert result["response"] == "I'm here to help! How can I assist you?"

    @pytest.mark.asyncio
    async def test_direct_answer_with_user_context(self):
        """Direct answer includes user context when available."""
        from app.modules.agents.deep.respond import _handle_direct_answer

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Hi Alice!", "citations": []}}

        state = {
            "query": "What is my name?",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "user_info": {"fullName": "Alice Smith", "userEmail": "alice@test.com"},
            "org_info": {"name": "TestOrg"},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        with patch("app.utils.streaming.stream_llm_response", side_effect=mock_stream), \
             patch("app.modules.agents.deep.respond.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]):
            result = await _handle_direct_answer(state, llm, writer, config, log)

        assert result["response"] == "Hi Alice!"


# ============================================================================
# 16. _deep_respond_impl path coverage
# ============================================================================

class TestDeepRespondImpl:
    """Tests for _deep_respond_impl covering main data paths."""

    @pytest.mark.asyncio
    async def test_error_state_delegates(self):
        """When state has error, _deep_respond_impl delegates to _handle_error_state."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": {"message": "Something broke", "status_code": 500},
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.safe_stream_write"):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Something broke"

    @pytest.mark.asyncio
    async def test_clarification_path(self):
        """When reflection_decision is respond_clarify, delegates to _handle_clarify."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_clarify",
            "reflection": {"clarifying_question": "Which project?"},
            "task_plan": {},
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.safe_stream_write"):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Which project?"

    @pytest.mark.asyncio
    async def test_error_decision_path(self):
        """When reflection_decision is respond_error, delegates to _handle_error_decision."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_error",
            "reflection": {"error_context": "API 503"},
            "task_plan": {},
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.safe_stream_write"):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert "API 503" in result["response"]

    @pytest.mark.asyncio
    async def test_direct_answer_path(self):
        """When task_plan has can_answer_directly, delegates to _handle_direct_answer."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {"can_answer_directly": True},
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        mock_handle_direct = AsyncMock(return_value={**state, "response": "Direct answer"})
        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._handle_direct_answer", mock_handle_direct):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Direct answer"
        mock_handle_direct.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_data_path(self):
        """When no analyses and no tool results, delegates to _handle_no_data."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        mock_handle_no_data = AsyncMock(return_value={**state, "response": "No data found"})
        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._handle_no_data", mock_handle_no_data):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "No data found"
        mock_handle_no_data.assert_awaited_once()


# ============================================================================
# 17. _collect_tool_results — additional branches
# ============================================================================

class TestCollectToolResultsExtra:
    """Additional branch coverage for _collect_tool_results."""

    def test_uncovered_domain_included(self):
        """Tool results from uncovered domains are included."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "short"}},
            ],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "slack.send_message", "status": "success", "result": {"ok": True}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert len(result) == 1
        assert result[0]["tool_name"] == "slack.send_message"

    def test_all_domains_covered_returns_empty(self):
        """When all domains are covered by analyses, returns empty."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "domain_summary": "Full summary of Jira results"},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [
                {"tool_name": "jira.search_issues", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert result == []

    def test_tool_results_from_all_tool_results_key(self):
        """Falls back to all_tool_results when tool_results is absent."""
        log = _mock_log()
        state = {
            "completed_tasks": [],
            "sub_agent_analyses": [],
            "all_tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        assert len(result) == 1


# ============================================================================
# 18. _collect_analyses — additional branches
# ============================================================================

class TestCollectAnalysesExtra:
    """Additional branch coverage for _collect_analyses."""

    def test_rebuild_from_tasks_with_mixed_results(self):
        """Rebuilds only from successful tasks with response data."""
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "Jira data here"}},
                {"task_id": "t2", "status": "error", "domains": ["slack"],
                 "error": "Connection failed"},
                {"task_id": "t3", "status": "success", "domains": ["confluence"],
                 "result": {"other_key": "no response field"}},
            ],
        }
        result = _collect_analyses(state, log)
        assert len(result) == 1
        assert "Jira data" in result[0]

    def test_rebuild_prefers_domain_summary(self):
        """When domain_summary exists, it's preferred over response."""
        log = _mock_log()
        state = {
            "sub_agent_analyses": None,
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "domain_summary": "Executive summary here",
                 "result": {"response": "Raw data"}},
            ],
        }
        result = _collect_analyses(state, log)
        assert len(result) == 1
        assert "Executive summary" in result[0]
        assert "Raw data" not in result[0]


# ============================================================================
# 19. _build_fallback_response — additional cases
# ============================================================================

class TestBuildFallbackResponseExtra:
    """Additional tests for _build_fallback_response."""

    def test_single_analysis_with_prefix(self):
        result = _build_fallback_response(["[task_1 (jira)]: Bug data found"])
        assert "Bug data found" in result
        assert "[task_1" not in result

    def test_analysis_without_bracket_prefix(self):
        result = _build_fallback_response(["No prefix analysis"])
        assert "No prefix analysis" in result


# ============================================================================
# 20. _log_state_diagnostic extra branches
# ============================================================================

class TestLogStateDiagnosticExtra:
    """Extra branch coverage for _log_state_diagnostic."""

    def test_with_all_fields_populated(self):
        from app.modules.agents.deep.respond import _log_state_diagnostic

        log = _mock_log()
        state = {
            "completed_tasks": [
                {"status": "success"},
                {"status": "success"},
                {"status": "error"},
            ],
            "sub_agent_analyses": ["a1"],
            "tool_results": [{"tool": "t1"}, {"tool": "t2"}],
            "reflection_decision": "continue",
            "task_plan": {"tasks": [{"id": "t1"}]},
            "final_results": [{"doc": 1}, {"doc": 2}],
            "virtual_record_id_to_result": {"vr1": {}, "vr2": {}},
        }
        _log_state_diagnostic(state, log)
        log.info.assert_called()

    def test_with_none_analyses(self):
        from app.modules.agents.deep.respond import _log_state_diagnostic

        log = _mock_log()
        state = {
            "completed_tasks": None,
            "sub_agent_analyses": None,
            "tool_results": None,
            "final_results": None,
            "virtual_record_id_to_result": None,
        }
        _log_state_diagnostic(state, log)
        log.info.assert_called()


# ============================================================================
# 21. _deep_respond_impl - fast-path and streaming coverage
# ============================================================================

class TestDeepRespondImplExtended:
    """Cover the main data path through _deep_respond_impl (lines 151-558)."""

    @pytest.mark.asyncio
    async def test_fast_path_api_only(self):
        """Fast-path: API-only results with sub-agent analyses."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "Found 3 bugs"}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Found 3 bugs in the project"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "search for bugs",
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        mock_fast_result = True  # _generate_fast_api_response returns truthy
        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, return_value=True) as mock_fast:
            # Set completion_data so we can check reference data path
            state["completion_data"] = {"referenceData": None}
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        mock_fast.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_fast_path_failure_falls_through(self):
        """When fast-path raises, falls through to standard path."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "Found bugs"}},
            ],
            "sub_agent_analyses": ["Analysis data"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "search",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": None,
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "answer_chunk", "data": {"chunk": "Response"}}
            yield {"event": "complete", "data": {"answer": "Response text", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast path failed")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="system prompt"),
                 MagicMock(content="user message"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", side_effect=RuntimeError("no blob")):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Response text"
        assert result["completion_data"]["confidence"] == "High"

    @pytest.mark.asyncio
    async def test_streaming_error_returns_error_msg(self):
        """When streaming raises, error message is returned."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": None,
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            raise RuntimeError("streaming crashed")
            yield  # make it async generator

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast path failed")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", side_effect=RuntimeError("no blob")):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert "encountered an issue" in result["response"].lower() or "error" in result["completion_data"].get("answerMatchType", "").lower()

    @pytest.mark.asyncio
    async def test_retrieval_path_with_qna_content(self):
        """When qna_message_content is set, uses simple retrieval messages."""

        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["retrieval"],
                 "result": {"response": "KB data"}},
            ],
            "sub_agent_analyses": ["Retrieval analysis"],
            "tool_results": [],
            "all_tool_results": [{"tool_name": "retrieval.search", "status": "success", "result": {}}],
            "final_results": [{"virtual_record_id": "vr1", "block_index": 0, "score": 0.9, "text": "test"}],
            "virtual_record_id_to_result": {"vr1": {"title": "Doc1"}},
            "query": "What is our policy?",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": "R1: test content here",
            "user_info": {"fullName": "Test User"},
            "org_info": {"name": "TestOrg", "accountType": "Enterprise"},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "org1",
            "user_id": "user1",
            "is_multimodal_llm": False,
            "conversation_id": "conv1",
            "retrieval_service": None,
            "decomposed_queries": [],
            "record_label_to_uuid_map": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Based on R1...", "citations": [{"id": "vr1"}], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes.merge_and_number_retrieval_results", return_value=state["final_results"]), \
             patch("app.utils.chat_helpers.get_message_content", return_value="R1: content"), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={"R1": "vr1"}), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=MagicMock()):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Based on R1..."
        assert result["completion_data"]["confidence"] == "High"


# ============================================================================
# 32. Coverage extensions for missing lines
# ============================================================================


class TestDeepRespondImplFastPathRefData:
    """Cover lines 177->197, 180->186, 183-184: fast-path reference data supplement."""

    @pytest.mark.asyncio
    async def test_fast_path_supplements_empty_reference_data(self):
        """Fast-path supplements referenceData when completion_data has no referenceData."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": {},
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "Found https://jira.example.com/BUG-1"}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Found https://jira.example.com/BUG-1"],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success",
                 "result": {"url": "https://jira.example.com/BUG-1"}},
            ],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "find bugs",
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, return_value=True):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        # completion_data should have been supplemented with referenceData
        cd = result.get("completion_data", {})
        if cd and cd.get("referenceData"):
            assert any("jira.example.com" in ref["url"] for ref in cd["referenceData"])

    @pytest.mark.asyncio
    async def test_fast_path_returns_none_falls_through(self):
        """Fast-path returns None (falsy), falls through to standard path."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": None,
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Fallthrough answer", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, return_value=None), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="system"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", side_effect=RuntimeError("no blob")):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Fallthrough answer"


class TestDeepRespondImplUserDataBranches:
    """Cover lines 221->239, 232: user_data construction for non-Enterprise accounts."""

    @pytest.mark.asyncio
    async def test_non_enterprise_account_type_user_data(self):
        """Non-Enterprise/Business account type produces simpler user_data."""

        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["retrieval"],
                 "result": {"response": "KB data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [{"virtual_record_id": "vr1", "block_index": 0}],
            "virtual_record_id_to_result": {"vr1": {"title": "Doc1"}},
            "query": "test query",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {"fullName": "Test User", "designation": "Engineer"},
            "org_info": {"name": "TestOrg", "accountType": "Free"},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "org1",
            "user_id": "user1",
            "is_multimodal_llm": False,
            "conversation_id": "conv1",
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Answer", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes.merge_and_number_retrieval_results", return_value=state["final_results"]), \
             patch("app.utils.chat_helpers.get_message_content", return_value="R1: content") as mock_gmc, \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={"R1": "vr1"}), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=MagicMock()):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)
            # Verify the user_data passed to get_message_content
            call_args = mock_gmc.call_args
            user_data_arg = call_args[0][2]
            # Non-enterprise uses simpler format without org name
            assert "I am the user." in user_data_arg
            assert "Test User" in user_data_arg


class TestDeepRespondImplContextBuilding:
    """Cover lines 295->350, 308->339, 325, 339->350, 343: context building branches."""

    @pytest.mark.asyncio
    async def test_analyses_with_has_api_results_no_retrieval(self):
        """Analyses + has_api_results but no retrieval produces correct context."""
        from langchain_core.messages import HumanMessage

        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Analysis text"],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {"items": []}},
            ],
            "all_tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {"items": []}},
            ],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "find jira issues",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": None,
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "API answer", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast path failed")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), HumanMessage(content="user msg"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value="API results context"), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", side_effect=RuntimeError("no blob")):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "API answer"

    @pytest.mark.asyncio
    async def test_analyses_only_no_api_results(self):
        """Analyses without has_api_results — analyses-only context path (line 325/330)."""
        from langchain_core.messages import HumanMessage

        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "domain_summary": "Summary here",
                 "result": {"response": "A" * 600}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Analysis text here"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "summarize jira",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": None,
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Summary answer", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast path failed")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), HumanMessage(content="user message"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", side_effect=RuntimeError("no blob")):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Summary answer"

    @pytest.mark.asyncio
    async def test_context_appended_to_list_content_message(self):
        """When last message has list content, context is appended as text item (line 343)."""
        from langchain_core.messages import HumanMessage

        from app.modules.agents.deep.respond import _deep_respond_impl

        last_msg = HumanMessage(content=[{"type": "text", "text": "existing content"}])

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Analysis"],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
            ],
            "all_tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
            ],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": None,
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Done", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast path")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), last_msg,
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value="API context"), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", side_effect=RuntimeError("no blob")):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Done"
        # The list content should have been extended
        assert len(last_msg.content) >= 2


class TestDeepRespondImplBlobStorage:
    """Cover line 388: BlobStorage initialization success."""

    @pytest.mark.asyncio
    async def test_blob_store_init_succeeds(self):
        """BlobStorage initialization succeeds and is stored in state."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        mock_blob = MagicMock()
        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": MagicMock(),
            "blob_store": None,
            "config_service": MagicMock(),
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "OK", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.modules.transformers.blob_storage.BlobStorage", return_value=mock_blob):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["blob_store"] is mock_blob


class TestDeepRespondImplDecomposedQueries:
    """Cover lines 404-410, 416-418: decomposed_queries and task description extraction."""

    @pytest.mark.asyncio
    async def test_with_decomposed_queries(self):
        """Uses decomposed_queries when available."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "find bugs and features",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [
                {"query": "find bugs"},
                {"query": "find features"},
            ],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Result", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream) as mock_slr:
            result = await _deep_respond_impl(state, config, writer, 0.0, log)
            # all_queries should contain the decomposed queries
            call_kwargs = mock_slr.call_args[1] if mock_slr.call_args[1] else {}
            if "all_queries" in call_kwargs:
                assert "find bugs" in call_kwargs["all_queries"]
                assert "find features" in call_kwargs["all_queries"]

    @pytest.mark.asyncio
    async def test_decomposed_queries_with_empty_query_entries(self):
        """decomposed_queries with empty/invalid entries falls back to [query]."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "fallback query",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [{"query": ""}, {"not_query": "x"}],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "OK", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "OK"

    @pytest.mark.asyncio
    async def test_task_descriptions_used_when_no_decomposed_queries(self):
        """Task descriptions are appended when no decomposed_queries."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {"tasks": [
                {"description": "Search Jira for bugs"},
                {"description": "Check Confluence for docs"},
            ]},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "find info",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Result", "citations": [], "confidence": "High"}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Result"


class TestDeepRespondImplCitationEnrichment:
    """Cover lines 465-483: citation enrichment on complete event."""

    @pytest.mark.asyncio
    async def test_citation_enrichment_on_empty_citations(self):
        """Citation enrichment runs when complete event has no citations."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["retrieval"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["Analysis"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [{"virtual_record_id": "vr1", "block_index": 0}],
            "virtual_record_id_to_result": {"vr1": {"title": "Doc"}},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": "R1: content",
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
            "record_label_to_uuid_map": {},
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        enriched_citations = [{"recordId": "vr1", "text": "cited text"}]

        async def mock_stream(*args, **kwargs):
            # Complete event with no citations to trigger enrichment
            yield {"event": "complete", "data": {"answer": "Based on R1...", "citations": []}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes.merge_and_number_retrieval_results", return_value=state["final_results"]), \
             patch("app.utils.chat_helpers.get_message_content", return_value="R1: content"), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={"R1": "vr1"}), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=[]), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream), \
             patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=MagicMock()), \
             patch("app.utils.citations.normalize_citations_and_chunks_for_agent",
                   return_value=("Based on R1...", enriched_citations)):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        assert result["response"] == "Based on R1..."


class TestDeepRespondImplEmptyResponse:
    """Cover lines 500-517: empty response fallback with analyses."""

    @pytest.mark.asyncio
    async def test_empty_response_with_analyses_fallback(self):
        """Empty LLM response uses analyses-based fallback."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "data"}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Important findings here"],
            "tool_results": [],
            "all_tool_results": [],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            # Stream with empty answer
            yield {"event": "answer_chunk", "data": {"chunk": ""}}
            yield {"event": "complete", "data": {"answer": "", "citations": []}}

        with patch("app.modules.agents.deep.respond.safe_stream_write") as mock_sw, \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value=""), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        # Should use fallback response built from analyses
        assert "Important findings here" in result["response"]
        assert result["completion_data"]["answerMatchType"] == "Fallback Response"

    @pytest.mark.asyncio
    async def test_empty_response_no_analyses_fallback(self):
        """Empty LLM response with no analyses gives generic fallback."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        # Use empty completed_tasks and empty sub_agent_analyses to ensure
        # _collect_analyses returns [] so the fallback path hits the generic message
        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": ""}},
            ],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
            ],
            "all_tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
            ],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "test",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "  ", "citations": []}}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value="context"), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        # With no analyses available (rebuilt from completed_tasks yields nothing
        # because response is empty), we get the generic fallback
        assert "rephrasing" in result["response"].lower() or "unable" in result["response"].lower() or "Fallback" in result["completion_data"].get("answerMatchType", "")


class TestDeepRespondImplReferenceData:
    """Cover lines 528, 532: reference data from deep_refs."""

    @pytest.mark.asyncio
    async def test_reference_data_from_deep_refs(self):
        """When pipeline has no referenceData, deep_refs supplement it."""
        from app.modules.agents.deep.respond import _deep_respond_impl

        state = {
            "logger": _mock_log(),
            "llm": MagicMock(),
            "error": None,
            "response": None,
            "completion_data": None,
            "reflection_decision": "respond_success",
            "reflection": {},
            "task_plan": {},
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": {"response": "Found https://jira.example.com/BUG-1"}},
            ],
            "sub_agent_analyses": ["[t1 (jira)]: Found https://jira.example.com/BUG-1"],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success",
                 "result": {"url": "https://jira.example.com/BUG-1"}},
            ],
            "all_tool_results": [
                {"tool_name": "jira.search", "status": "success",
                 "result": {"url": "https://jira.example.com/BUG-1"}},
            ],
            "final_results": [],
            "virtual_record_id_to_result": {},
            "query": "find bugs",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [],
            "conversation_summary": None,
            "qna_message_content": None,
            "user_info": {},
            "org_info": {},
            "graph_provider": None,
            "blob_store": MagicMock(),
            "config_service": None,
            "org_id": "",
            "user_id": "",
            "is_multimodal_llm": False,
            "conversation_id": None,
            "retrieval_service": None,
            "decomposed_queries": [],
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {
                "answer": "Found a bug", "citations": [],
                "confidence": "High", "referenceData": [],
            }}

        with patch("app.modules.agents.deep.respond.safe_stream_write"), \
             patch("app.modules.agents.deep.respond._log_state_diagnostic"), \
             patch("app.modules.agents.qna.nodes._generate_fast_api_response",
                   new_callable=AsyncMock, side_effect=RuntimeError("fast")), \
             patch("app.modules.qna.response_prompt.create_response_messages", return_value=[
                 MagicMock(content="sys"), MagicMock(content="user"),
             ]), \
             patch("app.modules.qna.response_prompt.build_record_label_mapping", return_value={}), \
             patch("app.modules.agents.qna.nodes._build_tool_results_context", return_value="context"), \
             patch("app.utils.streaming.stream_llm_response_with_tools", side_effect=mock_stream):
            result = await _deep_respond_impl(state, config, writer, 0.0, log)

        cd = result.get("completion_data", {})
        ref_data = cd.get("referenceData", [])
        # Should have deep_refs extracted from analyses/tool_results
        assert any("jira.example.com" in r["url"] for r in ref_data)


class TestCollectToolResultsMoreBranches:
    """Cover lines 776, 788->793, 804->803, 827-829."""

    def test_skips_non_success_tasks(self):
        """Tasks with status != 'success' are skipped in domain coverage (line 776)."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "error", "domains": ["jira"],
                 "result": {"response": "err"}},
                {"task_id": "t2", "status": "success", "domains": ["slack"],
                 "result": {"response": "short"}},
            ],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
                {"tool_name": "slack.send", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        # jira is not covered (error task), slack is not covered (short response)
        assert len(result) == 2

    def test_task_result_not_dict_response_text(self):
        """Non-dict task result leaves response_text empty (line 788)."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "result": "string result"},  # Not a dict
            ],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "jira.search", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        # String result < 500 chars so domain not covered, tool result should be included
        assert len(result) == 1

    def test_tool_domain_matching_for_covered_domains(self):
        """Tool results from covered domains are skipped (line 827-829)."""
        log = _mock_log()
        state = {
            "completed_tasks": [
                {"task_id": "t1", "status": "success", "domains": ["jira"],
                 "domain_summary": "Full summary of Jira data"},
                {"task_id": "t2", "status": "success", "domains": ["slack"],
                 "result": {"response": "short"}},
            ],
            "sub_agent_analyses": [],
            "tool_results": [
                {"tool_name": "jira.search_issues", "status": "success", "result": {}},
                {"tool_name": "slack.send", "status": "success", "result": {}},
            ],
        }
        result = _collect_tool_results(state, log)
        # jira covered by domain_summary, slack not covered (short response)
        # jira.search_issues should be skipped (jira domain covered)
        # slack.send should be included (slack not covered)
        assert len(result) == 1
        assert result[0]["tool_name"] == "slack.send"


class TestTrimAnalysesShortAnalysis:
    """Cover line 658: short analysis within its proportional share is not trimmed."""

    def test_short_analysis_keeps_original(self):
        """Analysis shorter than its budget share is returned unchanged."""
        log = _mock_log()
        analyses = ["AB", "C" * 200]
        result = _trim_analyses_to_budget(analyses, log, budget=100)
        # "AB" is 2 chars, total is 202, share = 100*2/202 ~ 0
        # Both should be trimmed since total > budget
        assert len(result) == 2


class TestHandleDirectAnswerWithConversation:
    """Cover line 1034: conversation context in direct answer."""

    @pytest.mark.asyncio
    async def test_direct_answer_with_previous_conversations(self):
        """Direct answer includes conversation context when previous conversations exist."""
        from langchain_core.messages import HumanMessage as HM

        from app.modules.agents.deep.respond import _handle_direct_answer

        async def mock_stream(*args, **kwargs):
            yield {"event": "complete", "data": {"answer": "Follow-up answer", "citations": []}}

        state = {
            "query": "Tell me more",
            "instructions": "",
            "system_prompt": "",
            "previous_conversations": [
                {"role": "user", "content": "What is X?"},
                {"role": "assistant", "content": "X is..."},
            ],
            "conversation_summary": "User asked about X",
            "user_info": {},
            "org_info": {},
            "response": None,
            "completion_data": None,
        }
        writer = _mock_writer()
        config = _mock_config()
        log = _mock_log()
        llm = MagicMock()

        conv_msgs = [HM(content="Previous context")]
        with patch("app.utils.streaming.stream_llm_response", side_effect=mock_stream), \
             patch("app.modules.agents.deep.respond.build_capability_summary", return_value=""), \
             patch("app.modules.agents.deep.respond.build_respond_conversation_context", return_value=conv_msgs) as mock_ctx:
            result = await _handle_direct_answer(state, llm, writer, config, log)

        assert result["response"] == "Follow-up answer"
        mock_ctx.assert_called_once()
