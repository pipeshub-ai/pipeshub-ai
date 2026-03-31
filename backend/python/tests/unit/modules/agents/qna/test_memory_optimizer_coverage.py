"""Additional unit tests for memory_optimizer.py to push coverage above 97%.

Targets missing lines/branches:
- Line 61: logger.debug when tool_results pruned (with logger)
- Lines 240->243, 245-246: get_state_memory_size getsizeof raises exception
- Lines 272-273: check_memory_health large state size warning (>10MB)
- Lines 338->341: auto_optimize_state with neither final_results nor messages
"""

import logging
import sys
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.qna.memory_optimizer import (
    MAX_MESSAGE_HISTORY,
    MAX_TOOL_RESULTS,
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
    return MagicMock(spec=logging.Logger)


def _make_message(msg_type: str = "human", content: str = "test") -> MagicMock:
    msg = MagicMock()
    msg.type = msg_type
    msg.content = content
    return msg


# ============================================================================
# prune_state - logger debug on tool results pruning
# ============================================================================


class TestPruneStateToolResultsLogger:
    """Cover line 61: logger.debug when tool_results are pruned."""

    def test_tool_results_pruned_with_logger_logs_debug(self):
        """When tool results are pruned AND logger is present, debug is called with pruning info."""
        log = _mock_log()
        state = {
            "all_tool_results": [{"tool": f"t{i}"} for i in range(25)],
        }
        result = prune_state(state, logger=log)

        assert len(result["all_tool_results"]) == MAX_TOOL_RESULTS
        # Check that debug was called with tool results pruning message
        debug_calls = [str(call) for call in log.debug.call_args_list]
        assert any("Pruned tool results" in str(call) for call in debug_calls)


# ============================================================================
# get_state_memory_size - getsizeof exception
# ============================================================================


class TestGetStateMemorySizeException:
    """Cover lines 245-246: when sys.getsizeof raises, sizes[key] = 0."""

    def test_getsizeof_raises_exception(self):
        """When sys.getsizeof raises, the field gets size 0."""

        class UnmeasurableObject:
            """Object that causes sys.getsizeof to fail."""

            def __sizeof__(self):
                raise TypeError("Cannot measure size")

        state = {
            "normal": "hello",
            "broken": UnmeasurableObject(),
        }

        result = get_state_memory_size(state)

        # 'broken' field should have size 0
        assert result["total_bytes"] >= 0
        # The 'broken' field should be excluded from by_field (since it's 0)
        assert "broken" not in result["by_field"]

    def test_list_with_none_items(self):
        """Lists with None items: None items are excluded from deep size calculation."""
        state = {"items": [None, "hello", None, "world"]}
        result = get_state_memory_size(state)
        assert result["total_bytes"] > 0

    def test_dict_value_deep_size(self):
        """Dict values are measured using deep size calculation."""
        state = {"config": {"key1": "value1", "key2": "value2"}}
        result = get_state_memory_size(state)
        assert result["total_bytes"] > 0
        assert "config" in result["by_field"]


# ============================================================================
# check_memory_health - large state size warning (>10MB)
# ============================================================================


class TestCheckMemoryHealthLargeState:
    """Cover lines 272-273: state size > 10MB warning."""

    def test_large_state_triggers_warning(self):
        """State larger than 10MB should trigger 'State size is large' warning."""
        # Create a state that's over 10MB
        state = {"big_data": "X" * (11 * 1024 * 1024)}  # ~11MB

        result = check_memory_health(state)

        assert result["status"] == "needs_optimization"
        assert any("State size is large" in w for w in result["warnings"])
        assert any("prune_state" in r for r in result["recommendations"])

    def test_large_state_with_logger(self):
        """Large state with logger should log warnings."""
        log = _mock_log()
        state = {"big_data": "X" * (11 * 1024 * 1024)}

        check_memory_health(state, logger=log)

        log.warning.assert_called()

    def test_multiple_issues_large_state_and_messages(self):
        """State with both large size and too many messages."""
        state = {
            "big_data": "X" * (11 * 1024 * 1024),
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [],
        }
        result = check_memory_health(state)

        assert result["status"] == "needs_optimization"
        assert len(result["warnings"]) >= 2


# ============================================================================
# auto_optimize_state - branch coverage for final_results and messages
# ============================================================================


class TestAutoOptimizeStateBranches:
    """Cover lines 338->341: auto_optimize with empty final_results/messages."""

    def test_unhealthy_with_empty_final_results(self):
        """When state needs optimization but final_results is empty, skip compress."""
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [],  # empty -> branch skipped
        }
        result = auto_optimize_state(state)

        # Messages should be pruned but final_results untouched
        assert result["final_results"] == []

    def test_unhealthy_with_no_final_results_key(self):
        """When state needs optimization but has no final_results key."""
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            # No final_results key at all
        }
        result = auto_optimize_state(state)

        assert "final_results" not in result or result.get("final_results") is None

    def test_unhealthy_with_empty_messages(self):
        """When state needs optimization but messages is empty, skip optimize_messages."""
        state = {
            "messages": [],  # empty -> branch skipped
            "all_tool_results": [{"tool": f"t{i}"} for i in range(25)],
            "final_results": [
                {"page_content": "Same text", "metadata": {}},
                {"page_content": "Same text", "metadata": {}},
            ],
        }
        result = auto_optimize_state(state)

        assert result["messages"] == []

    def test_unhealthy_with_no_messages_key(self):
        """When state needs optimization but has no messages key."""
        state = {
            "all_tool_results": [{"tool": f"t{i}"} for i in range(25)],
            "final_results": [],
        }
        result = auto_optimize_state(state)

        # Should not crash
        assert "messages" not in result or result.get("messages") is None

    def test_auto_optimize_with_logger_and_unhealthy(self):
        """Logger gets info calls during optimization."""
        log = _mock_log()
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [
                {"page_content": "text", "metadata": {}},
            ],
        }
        auto_optimize_state(state, logger=log)

        # Should call info for "Auto-optimizing state..." and "State optimization complete"
        info_calls = [str(call) for call in log.info.call_args_list]
        assert any("Auto-optimizing" in str(call) for call in info_calls)
        assert any("optimization complete" in str(call) for call in info_calls)

    def test_auto_optimize_no_logger_healthy(self):
        """Without logger, healthy state passes through."""
        state = {
            "messages": [],
            "all_tool_results": [],
            "final_results": [],
        }
        result = auto_optimize_state(state)
        assert result == state

    def test_auto_optimize_no_logger_unhealthy(self):
        """Without logger, unhealthy state is still optimized."""
        state = {
            "messages": [_make_message("human", f"msg{i}") for i in range(50)],
            "all_tool_results": [],
            "final_results": [],
        }
        result = auto_optimize_state(state)

        assert len(result["messages"]) <= MAX_MESSAGE_HISTORY + 1


# ============================================================================
# optimize_messages - msg without .dict() method
# ============================================================================


class TestOptimizeMessagesEdge:
    """Cover the compress branch where msg has no .dict() method."""

    def test_message_without_dict_method(self):
        """Message with long content but no .dict() method."""
        from app.modules.agents.qna.memory_optimizer import COMPRESS_THRESHOLD

        class SimpleLongMsg:
            def __init__(self):
                self.type = "human"
                self.content = "L" * (COMPRESS_THRESHOLD + 100)

        msgs = [_make_message("human", "short")] * 4
        msgs.append(SimpleLongMsg())
        msgs.append(_make_message("human", "another"))

        result = optimize_messages(msgs)

        assert len(result) == 6

    def test_none_returns_none(self):
        """optimize_messages(None) returns None."""
        assert optimize_messages(None) is None

    def test_empty_returns_empty(self):
        """optimize_messages([]) returns []."""
        assert optimize_messages([]) == []


# ============================================================================
# compress_documents - logger called only when count differs
# ============================================================================


class TestCompressDocumentsLogger:
    """Cover the logger branch where document count doesn't change."""

    def test_no_compression_no_logger_call(self):
        """When documents aren't compressed (no dupes), logger.debug is NOT called."""
        log = _mock_log()
        docs = [
            {"page_content": "Unique 1", "metadata": {}},
            {"page_content": "Unique 2", "metadata": {}},
        ]
        compress_documents(docs, logger=log)

        # No compression happened, so debug should not be called
        log.debug.assert_not_called()
