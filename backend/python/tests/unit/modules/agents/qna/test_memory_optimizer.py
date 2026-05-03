"""
Tests for app.modules.agents.qna.memory_optimizer.

Covers:
- prune_state: State pruning for memory efficiency
- compress_documents: Document compression and deduplication
- compress_context: Context string truncation
- optimize_messages: Message list optimization
- get_state_memory_size: Memory size calculation
- check_memory_health: Health check with warnings/recommendations
- auto_optimize_state: Automatic optimization based on health
"""

import logging
import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.modules.agents.qna.memory_optimizer import (
    COMPRESS_THRESHOLD,
    MAX_DOCUMENT_SIZE,
    MAX_MESSAGE_HISTORY,
    MAX_TOOL_RESULTS,
    MAX_TOTAL_CONTEXT_SIZE,
    TRUNCATE_THRESHOLD,
    auto_optimize_state,
    check_memory_health,
    compress_context,
    compress_documents,
    get_state_memory_size,
    optimize_messages,
    prune_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    """Return a mock logger that silently accepts all log calls."""
    return MagicMock(spec=logging.Logger)


def _make_message(msg_type: str = "human", content: str = "test") -> MagicMock:
    """Create a mock message with type and content attributes."""
    msg = MagicMock()
    msg.type = msg_type
    msg.content = content
    return msg


# ============================================================================
# 1. prune_state
# ============================================================================

class TestPruneState:
    """Tests for prune_state()."""

    def test_prune_message_history_within_limit(self):
        """Messages within limit should not be pruned."""
        messages = [_make_message("human", f"msg{i}") for i in range(5)]
        state = {"messages": messages}
        result = prune_state(state)
        assert len(result["messages"]) == 5

    def test_prune_message_history_over_limit(self):
        """Messages exceeding limit should be pruned to keep system + recent."""
        system_msg = _make_message("system", "system prompt")
        human_msgs = [_make_message("human", f"msg{i}") for i in range(30)]
        state = {"messages": [system_msg] + human_msgs}
        result = prune_state(state)
        # System message + last MAX_MESSAGE_HISTORY
        assert len(result["messages"]) <= MAX_MESSAGE_HISTORY + 1

    def test_prune_tool_results_within_limit(self):
        state = {"all_tool_results": [{"tool": f"t{i}"} for i in range(5)]}
        result = prune_state(state)
        assert len(result["all_tool_results"]) == 5

    def test_prune_tool_results_over_limit(self):
        state = {"all_tool_results": [{"tool": f"t{i}"} for i in range(25)]}
        result = prune_state(state)
        assert len(result["all_tool_results"]) == MAX_TOOL_RESULTS

    def test_removes_intermediate_fields(self):
        state = {
            "decomposed_queries": ["q1", "q2"],
            "rewritten_queries": ["rq1"],
            "expanded_queries": ["eq1"],
            "search_results": [{"doc": 1}],
        }
        result = prune_state(state)
        assert result["decomposed_queries"] == []
        assert result["rewritten_queries"] == []
        assert result["expanded_queries"] == []
        assert result["search_results"] == []

    def test_compresses_query_analysis(self):
        state = {
            "query_analysis": {
                "is_complex": True,
                "needs_internal_data": False,
                "intent": "search",
                "extra_data": "should be dropped",
                "verbose_info": {"detail": "lots of info"},
            }
        }
        result = prune_state(state)
        analysis = result["query_analysis"]
        assert analysis["is_complex"] is True
        assert analysis["needs_internal_data"] is False
        assert analysis["intent"] == "search"
        assert "extra_data" not in analysis
        assert "verbose_info" not in analysis

    def test_cleans_tool_execution_summary(self):
        state = {
            "tool_execution_summary": {"tool1": "success", "tool2": "error"},
        }
        result = prune_state(state)
        assert result["tool_execution_summary"] == {}

    def test_cleans_tool_data_available(self):
        state = {
            "tool_data_available": {"data": True, "results": [1, 2]},
        }
        result = prune_state(state)
        assert result["tool_data_available"] == {}

    def test_empty_state(self):
        state: dict = {}
        result = prune_state(state)
        assert result == {}

    def test_with_logger(self):
        log = _mock_log()
        messages = [_make_message("human", f"msg{i}") for i in range(30)]
        state = {"messages": messages}
        prune_state(state, logger=log)
        log.debug.assert_called()

    def test_intermediate_fields_empty_not_pruned(self):
        """Empty intermediate fields should not be set to []."""
        state = {
            "decomposed_queries": [],
            "rewritten_queries": [],
        }
        result = prune_state(state)
        # Already empty -- state[field] is falsy, so code skips
        assert result["decomposed_queries"] == []

    def test_intermediate_fields_none_not_pruned(self):
        """None intermediate fields should not be set to []."""
        state = {
            "decomposed_queries": None,
        }
        result = prune_state(state)
        # None is falsy, so the code doesn't touch it
        assert result["decomposed_queries"] is None

    def test_query_analysis_none_not_pruned(self):
        state = {"query_analysis": None}
        result = prune_state(state)
        assert result["query_analysis"] is None

    def test_query_analysis_empty_dict_not_pruned(self):
        """Empty dict is falsy, so it should not be pruned."""
        state = {"query_analysis": {}}
        result = prune_state(state)
        assert result["query_analysis"] == {}

    def test_preserves_system_messages_during_pruning(self):
        """System messages should always be kept."""
        system_msg = _make_message("system", "important system prompt")
        human_msgs = [_make_message("human", f"msg{i}") for i in range(30)]
        state = {"messages": [system_msg] + human_msgs}
        result = prune_state(state)
        # First message should be the system message
        system_msgs = [m for m in result["messages"] if m.type == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "important system prompt"


# ============================================================================
# 2. compress_documents
# ============================================================================

class TestCompressDocuments:
    """Tests for compress_documents()."""

    def test_empty_documents(self):
        assert compress_documents([]) == []

    def test_none_documents(self):
        assert compress_documents(None) is None

    def test_within_size_limit(self):
        docs = [
            {"page_content": "Short text", "metadata": {"source": "a.pdf", "title": "Doc A"}},
        ]
        result = compress_documents(docs)
        assert len(result) == 1
        assert result[0]["page_content"] == "Short text"
        assert result[0]["metadata"]["source"] == "a.pdf"

    def test_truncates_long_document(self):
        long_content = "X" * (MAX_DOCUMENT_SIZE + 1000)
        docs = [{"page_content": long_content, "metadata": {}}]
        result = compress_documents(docs)
        assert len(result[0]["page_content"]) <= MAX_DOCUMENT_SIZE + 20  # truncated + suffix
        assert "truncated" in result[0]["page_content"]

    def test_deduplicates_exact_matches(self):
        docs = [
            {"page_content": "Same text", "metadata": {}},
            {"page_content": "Same text", "metadata": {}},
            {"page_content": "Different text", "metadata": {}},
        ]
        result = compress_documents(docs)
        assert len(result) == 2

    def test_keeps_essential_metadata(self):
        docs = [
            {
                "page_content": "Text",
                "metadata": {
                    "source": "doc.pdf",
                    "title": "My Doc",
                    "type": "pdf",
                    "extra_field": "should be dropped",
                    "internal_id": "12345",
                },
            },
        ]
        result = compress_documents(docs)
        meta = result[0]["metadata"]
        assert meta["source"] == "doc.pdf"
        assert meta["title"] == "My Doc"
        assert meta["type"] == "pdf"
        assert "extra_field" not in meta
        assert "internal_id" not in meta

    def test_with_logger_logs_compression(self):
        log = _mock_log()
        docs = [
            {"page_content": "Same", "metadata": {}},
            {"page_content": "Same", "metadata": {}},
        ]
        compress_documents(docs, logger=log)
        log.debug.assert_called()

    def test_no_logger_no_crash(self):
        docs = [
            {"page_content": "Same", "metadata": {}},
            {"page_content": "Same", "metadata": {}},
        ]
        # Should not crash without logger
        result = compress_documents(docs)
        assert len(result) == 1

    def test_multiple_unique_documents_preserved(self):
        docs = [{"page_content": f"Doc {i}", "metadata": {}} for i in range(10)]
        result = compress_documents(docs)
        assert len(result) == 10

    def test_missing_page_content_key(self):
        docs = [{"metadata": {"source": "test"}}]
        result = compress_documents(docs)
        assert len(result) == 1
        assert result[0]["page_content"] == ""

    def test_missing_metadata_key(self):
        docs = [{"page_content": "text"}]
        result = compress_documents(docs)
        assert len(result) == 1
        assert "metadata" in result[0]


# ============================================================================
# 3. compress_context
# ============================================================================

class TestCompressContext:
    """Tests for compress_context()."""

    def test_within_limit(self):
        text = "Short context text."
        result = compress_context(text)
        assert result == text

    def test_over_limit(self):
        text = "A" * (MAX_TOTAL_CONTEXT_SIZE + 1000)
        result = compress_context(text)
        assert len(result) < len(text)
        assert "truncated" in result

    def test_custom_max_size(self):
        text = "A" * 200
        result = compress_context(text, max_size=100)
        assert len(result) < 200
        assert "truncated" in result

    def test_exactly_at_limit(self):
        text = "B" * MAX_TOTAL_CONTEXT_SIZE
        result = compress_context(text)
        assert result == text

    def test_preserves_beginning_and_end(self):
        """Should keep first 40% and last 40%."""
        # Create a distinctive text
        beginning = "START" * 100
        middle = "MIDDLE" * 100
        end = "END" * 100
        text = beginning + middle + end
        result = compress_context(text, max_size=len(text) // 2)
        assert result.startswith("START")
        assert result.endswith("END" * 100) or "END" in result[-50:]

    def test_empty_string(self):
        assert compress_context("") == ""

    def test_single_character(self):
        assert compress_context("X") == "X"

    def test_small_max_size(self):
        text = "A" * 100
        result = compress_context(text, max_size=10)
        assert "truncated" in result


# ============================================================================
# 4. optimize_messages
# ============================================================================

class TestOptimizeMessages:
    """Tests for optimize_messages()."""

    def test_empty_messages(self):
        assert optimize_messages([]) == []

    def test_none_messages(self):
        assert optimize_messages(None) is None

    def test_few_messages_not_optimized(self):
        """5 or fewer messages should not be touched."""
        msgs = [_make_message("human", f"msg{i}") for i in range(5)]
        result = optimize_messages(msgs)
        assert result == msgs

    def test_system_messages_always_kept(self):
        msgs = [_make_message("system", "System prompt")]
        msgs += [_make_message("human", f"msg{i}") for i in range(25)]
        result = optimize_messages(msgs)
        system_msgs = [m for m in result if m.type == "system"]
        assert len(system_msgs) == 1

    def test_limits_to_max_history(self):
        msgs = [_make_message("human", f"msg{i}") for i in range(50)]
        result = optimize_messages(msgs)
        non_system = [m for m in result if not (hasattr(m, 'type') and m.type == 'system')]
        assert len(non_system) <= MAX_MESSAGE_HISTORY

    def test_with_logger(self):
        log = _mock_log()
        msgs = [_make_message("human", f"msg{i}") for i in range(50)]
        optimize_messages(msgs, logger=log)
        log.debug.assert_called()

    def test_compresses_long_message_content(self):
        """Messages with content > COMPRESS_THRESHOLD should be compressed."""
        msgs = [_make_message("human", "short")] * 4
        long_msg = _make_message("human", "L" * (COMPRESS_THRESHOLD + 100))
        msgs.append(long_msg)
        msgs.append(_make_message("human", "another"))
        # Only runs optimization when > 5 messages
        result = optimize_messages(msgs)
        assert len(result) == 6

    def test_messages_without_type_attribute(self):
        """Messages without type attribute should be kept."""
        class SimpleMsg:
            def __init__(self, content):
                self.content = content

        msgs = [SimpleMsg(f"msg{i}") for i in range(10)]
        result = optimize_messages(msgs)
        assert len(result) == 10

    def test_messages_without_content_attribute(self):
        """Messages without content attribute should be kept."""
        msgs = [_make_message("human", f"msg{i}") for i in range(8)]
        # Add a message without string content
        msg_no_content = MagicMock()
        msg_no_content.type = "human"
        msg_no_content.content = 12345  # Not a string
        msgs.append(msg_no_content)
        result = optimize_messages(msgs)
        assert msg_no_content in result


# ============================================================================
# 5. get_state_memory_size
# ============================================================================

class TestGetStateMemorySize:
    """Tests for get_state_memory_size()."""

    def test_empty_state(self):
        result = get_state_memory_size({})
        assert result["total_bytes"] == 0
        assert result["total_kb"] == 0.0
        assert result["total_mb"] == 0.0
        assert result["by_field"] == {}

    def test_simple_state(self):
        state = {"query": "test", "messages": []}
        result = get_state_memory_size(state)
        assert result["total_bytes"] > 0
        assert "query" in result["by_field"]
        assert "messages" in result["by_field"]

    def test_state_with_list(self):
        state = {"messages": ["msg1", "msg2", "msg3"]}
        result = get_state_memory_size(state)
        assert result["total_bytes"] > 0

    def test_state_with_dict(self):
        state = {"config": {"key": "value", "num": 42}}
        result = get_state_memory_size(state)
        assert result["total_bytes"] > 0

    def test_state_with_none_values(self):
        state = {"field": None}
        result = get_state_memory_size(state)
        assert "field" not in result["by_field"] or result["by_field"]["field"] is not None

    def test_large_state(self):
        state = {"big": "X" * 100000}
        result = get_state_memory_size(state)
        assert result["total_kb"] > 90  # ~100KB

    def test_by_field_sorted_descending(self):
        state = {"small": "x", "big": "Y" * 10000}
        result = get_state_memory_size(state)
        fields = list(result["by_field"].keys())
        if len(fields) >= 2:
            # First field should be the bigger one
            assert fields[0] == "big"

    def test_kb_and_mb_calculations(self):
        state = {"data": "A" * 1024}
        result = get_state_memory_size(state)
        assert result["total_kb"] > 0
        assert result["total_mb"] >= 0


# ============================================================================
# 6. check_memory_health
# ============================================================================

class TestCheckMemoryHealth:
    """Tests for check_memory_health()."""

    def test_healthy_state(self):
        state = {
            "messages": [_make_message("human", "hi")],
            "all_tool_results": [],
            "final_results": [],
        }
        result = check_memory_health(state)
        assert result["status"] == "healthy"
        assert result["warnings"] == []
        assert result["recommendations"] == []

    def test_too_many_messages(self):
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [],
        }
        result = check_memory_health(state)
        assert result["status"] == "needs_optimization"
        assert any("messages" in w.lower() for w in result["warnings"])

    def test_too_many_tool_results(self):
        state = {
            "messages": [],
            "all_tool_results": [{"tool": f"t{i}"} for i in range(25)],
            "final_results": [],
        }
        result = check_memory_health(state)
        assert result["status"] == "needs_optimization"
        assert any("tool results" in w.lower() for w in result["warnings"])

    def test_too_many_documents(self):
        state = {
            "messages": [],
            "all_tool_results": [],
            "final_results": [{"doc": i} for i in range(60)],
        }
        result = check_memory_health(state)
        assert result["status"] == "needs_optimization"
        assert any("documents" in w.lower() for w in result["warnings"])

    def test_with_logger_logs_warnings(self):
        log = _mock_log()
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [],
        }
        check_memory_health(state, logger=log)
        log.warning.assert_called()

    def test_healthy_no_logger_warnings(self):
        log = _mock_log()
        state = {
            "messages": [],
            "all_tool_results": [],
            "final_results": [],
        }
        check_memory_health(state, logger=log)
        log.warning.assert_not_called()

    def test_empty_state_is_healthy(self):
        result = check_memory_health({})
        assert result["status"] == "healthy"

    def test_multiple_warnings(self):
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [{"tool": f"t{i}"} for i in range(25)],
            "final_results": [{"doc": i} for i in range(60)],
        }
        result = check_memory_health(state)
        assert len(result["warnings"]) >= 2
        assert len(result["recommendations"]) >= 2

    def test_memory_info_included(self):
        state = {"data": "some content"}
        result = check_memory_health(state)
        assert "memory_info" in result
        assert "total_bytes" in result["memory_info"]


# ============================================================================
# 7. auto_optimize_state
# ============================================================================

class TestAutoOptimizeState:
    """Tests for auto_optimize_state()."""

    def test_healthy_state_not_optimized(self):
        state = {
            "messages": [_make_message("human", "hi")],
            "all_tool_results": [],
            "final_results": [],
        }
        result = auto_optimize_state(state)
        # Should return state unchanged
        assert result is state

    def test_unhealthy_state_gets_optimized(self):
        """State that needs optimization should be pruned."""
        messages = [_make_message("human", f"msg{i}") for i in range(50)]
        state = {
            "messages": messages,
            "all_tool_results": [{"tool": f"t{i}"} for i in range(25)],
            "final_results": [
                {"page_content": "Same text", "metadata": {}},
                {"page_content": "Same text", "metadata": {}},
            ],
        }
        result = auto_optimize_state(state)
        # Messages should be pruned
        assert len(result.get("messages", [])) <= MAX_MESSAGE_HISTORY + 5

    def test_with_logger(self):
        log = _mock_log()
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [],
        }
        auto_optimize_state(state, logger=log)
        log.info.assert_called()

    def test_healthy_with_logger(self):
        log = _mock_log()
        state = {
            "messages": [],
            "all_tool_results": [],
            "final_results": [],
        }
        auto_optimize_state(state, logger=log)
        log.debug.assert_called()

    def test_compresses_documents_when_unhealthy(self):
        """final_results should be compressed during optimization."""
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [
                {"page_content": "Duplicate", "metadata": {}},
                {"page_content": "Duplicate", "metadata": {}},
                {"page_content": "Unique", "metadata": {}},
            ],
        }
        result = auto_optimize_state(state)
        # Duplicates should be compressed away
        assert len(result["final_results"]) == 2

    def test_optimizes_messages_when_unhealthy(self):
        """Messages should be optimized when state is unhealthy."""
        system_msg = _make_message("system", "sys")
        human_msgs = [_make_message("human", f"msg{i}") for i in range(50)]
        state = {
            "messages": [system_msg] + human_msgs,
            "all_tool_results": [],
            "final_results": [],
        }
        result = auto_optimize_state(state)
        assert len(result["messages"]) <= MAX_MESSAGE_HISTORY + 5

    def test_empty_state_stays_empty(self):
        state: dict = {}
        result = auto_optimize_state(state)
        assert result == {}

    def test_already_optimal_not_modified(self):
        """A well-sized state should pass through without modification."""
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(3)],
            "all_tool_results": [{"tool": "t1"}],
            "final_results": [{"page_content": "text", "metadata": {}}],
            "query": "test",
        }
        result = auto_optimize_state(state)
        assert result["query"] == "test"
        assert len(result["messages"]) == 3
