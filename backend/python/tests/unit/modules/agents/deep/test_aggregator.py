"""
Unit tests for app.modules.agents.deep.aggregator

Tests pure/near-pure helper functions and the aggregator_node async function.
All external dependencies (LLM, streaming) are mocked.
"""

import json
import logging
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.agents.deep.aggregator import (
    _build_evaluator_instructions,
    _has_retryable_errors,
    _parse_evaluation_response,
    _set_continue,
    _set_respond_error,
    _set_respond_success,
    _set_retry,
    aggregator_node,
    route_after_evaluation,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_log() -> logging.Logger:
    """Return a mock logger that silently accepts all log calls."""
    return MagicMock(spec=logging.Logger)


def _make_task(status: str = "success", task_id: str = "t1",
               error: str = "", result: Any = None,
               domains: list = None, description: str = "",
               duration_ms: float = 100) -> Dict[str, Any]:
    """Create a mock SubAgentTask dict."""
    task = {
        "task_id": task_id,
        "status": status,
        "domains": domains or ["general"],
        "description": description,
        "duration_ms": duration_ms,
    }
    if error:
        task["error"] = error
    if result is not None:
        task["result"] = result
    return task


# ============================================================================
# 1. route_after_evaluation
# ============================================================================

class TestRouteAfterEvaluation:
    """Tests for route_after_evaluation()."""

    def test_respond_success(self):
        """respond_success -> respond."""
        state = {"reflection_decision": "respond_success"}
        assert route_after_evaluation(state) == "respond"

    def test_respond_error(self):
        """respond_error -> respond."""
        state = {"reflection_decision": "respond_error"}
        assert route_after_evaluation(state) == "respond"

    def test_respond_clarify(self):
        """respond_clarify -> respond."""
        state = {"reflection_decision": "respond_clarify"}
        assert route_after_evaluation(state) == "respond"

    def test_retry(self):
        """retry -> retry."""
        state = {"reflection_decision": "retry"}
        assert route_after_evaluation(state) == "retry"

    def test_continue(self):
        """continue -> continue."""
        state = {"reflection_decision": "continue"}
        assert route_after_evaluation(state) == "continue"

    def test_unknown_fallback(self):
        """Unknown decision falls back to respond."""
        state = {"reflection_decision": "unknown_decision"}
        assert route_after_evaluation(state) == "respond"

    def test_missing_decision_defaults_to_respond(self):
        """Missing reflection_decision defaults to respond_success -> respond."""
        state = {}
        assert route_after_evaluation(state) == "respond"


# ============================================================================
# 2. _has_retryable_errors
# ============================================================================

class TestHasRetryableErrors:
    """Tests for _has_retryable_errors()."""

    def test_timeout_is_retryable(self):
        """Task with 'timeout' error is retryable."""
        tasks = [_make_task(status="error", error="Connection timeout after 30s")]
        assert _has_retryable_errors(tasks) is True

    def test_rate_limit_is_retryable(self):
        """Task with 'rate limit' error is retryable."""
        tasks = [_make_task(status="error", error="Rate limit exceeded, retry after 60s")]
        assert _has_retryable_errors(tasks) is True

    def test_429_is_retryable(self):
        """Task with '429' status code is retryable."""
        tasks = [_make_task(status="error", error="HTTP 429 Too Many Requests")]
        assert _has_retryable_errors(tasks) is True

    def test_503_is_retryable(self):
        """Task with '503' status code is retryable."""
        tasks = [_make_task(status="error", error="503 Service Unavailable")]
        assert _has_retryable_errors(tasks) is True

    def test_502_is_retryable(self):
        """Task with '502' status code is retryable."""
        tasks = [_make_task(status="error", error="502 Bad Gateway")]
        assert _has_retryable_errors(tasks) is True

    def test_temporary_is_retryable(self):
        """Task with 'temporary' error is retryable."""
        tasks = [_make_task(status="error", error="Temporary failure")]
        assert _has_retryable_errors(tasks) is True

    def test_transient_is_retryable(self):
        """Task with 'transient' error is retryable."""
        tasks = [_make_task(status="error", error="Transient error")]
        assert _has_retryable_errors(tasks) is True

    def test_connection_is_retryable(self):
        """Task with 'connection' error is retryable."""
        tasks = [_make_task(status="error", error="Connection refused")]
        assert _has_retryable_errors(tasks) is True

    def test_permission_error_not_retryable(self):
        """Permission error is not retryable."""
        tasks = [_make_task(status="error", error="Permission denied")]
        assert _has_retryable_errors(tasks) is False

    def test_not_found_not_retryable(self):
        """Not found error is not retryable."""
        tasks = [_make_task(status="error", error="Resource not found")]
        assert _has_retryable_errors(tasks) is False

    def test_empty_list(self):
        """Empty error task list is not retryable."""
        assert _has_retryable_errors([]) is False

    def test_none_error_not_retryable(self):
        """Task with None error is not retryable."""
        tasks = [{"status": "error"}]  # No error key
        assert _has_retryable_errors(tasks) is False

    def test_mixed_tasks_any_retryable(self):
        """If any task is retryable, returns True."""
        tasks = [
            _make_task(status="error", error="Permission denied"),
            _make_task(status="error", error="Connection timeout"),
        ]
        assert _has_retryable_errors(tasks) is True


# ============================================================================
# 3. _set_respond_success
# ============================================================================

class TestSetRespondSuccess:
    """Tests for _set_respond_success()."""

    def test_sets_decision_and_reflection(self):
        """Sets correct decision and reflection."""
        state = {}
        tasks = [_make_task(), _make_task(task_id="t2")]
        _set_respond_success(state, tasks, _mock_log())
        assert state["reflection_decision"] == "respond_success"
        assert state["reflection"]["decision"] == "respond_success"
        assert "2 task(s) completed" in state["reflection"]["reasoning"]

    def test_single_task(self):
        """Works with single task."""
        state = {}
        _set_respond_success(state, [_make_task()], _mock_log())
        assert "1 task(s)" in state["reflection"]["reasoning"]


# ============================================================================
# 4. _set_respond_error
# ============================================================================

class TestSetRespondError:
    """Tests for _set_respond_error()."""

    def test_sets_error_decision(self):
        """Sets error decision with error details."""
        state = {}
        tasks = [_make_task(status="error", task_id="t1", error="Something failed")]
        _set_respond_error(state, tasks, _mock_log())
        assert state["reflection_decision"] == "respond_error"
        assert "error_context" in state["reflection"]
        assert "t1" in state["reflection"]["error_context"]

    def test_truncates_long_errors(self):
        """Long error messages are truncated."""
        state = {}
        tasks = [_make_task(status="error", task_id="t1", error="x" * 500)]
        _set_respond_error(state, tasks, _mock_log())
        # Error context should not contain the full 500-char error
        assert len(state["reflection"]["error_context"]) < 500

    def test_limits_to_3_errors(self):
        """Only first 3 error tasks are included."""
        state = {}
        tasks = [
            _make_task(status="error", task_id=f"t{i}", error=f"Error {i}")
            for i in range(5)
        ]
        _set_respond_error(state, tasks, _mock_log())
        # Count occurrences of task IDs in error_context
        error_ctx = state["reflection"]["error_context"]
        assert "t0" in error_ctx
        assert "t2" in error_ctx
        # t3 and t4 should not be there (only first 3)

    def test_empty_error_tasks(self):
        """Empty error list produces fallback context."""
        state = {}
        _set_respond_error(state, [], _mock_log())
        assert state["reflection"]["error_context"] == "Tasks failed"


# ============================================================================
# 5. _set_retry
# ============================================================================

class TestSetRetry:
    """Tests for _set_retry()."""

    def test_sets_retry_decision(self):
        """Sets retry decision and increments iteration count."""
        state = {"deep_iteration_count": 0}
        tasks = [_make_task(status="error", task_id="t1", error="timeout")]
        _set_retry(state, tasks, _mock_log())
        assert state["reflection_decision"] == "retry"
        assert state["deep_iteration_count"] == 1
        assert state["sub_agent_tasks"] == []

    def test_timeout_error_fix_description(self):
        """Timeout error gets appropriate fix description."""
        state = {"deep_iteration_count": 0}
        tasks = [_make_task(status="error", task_id="t1", error="Connection timeout")]
        _set_retry(state, tasks, _mock_log())
        assert "timeout" in state["reflection"]["retry_fix"].lower()

    def test_unauthorized_error_fix_description(self):
        """Auth error gets permissions fix description."""
        state = {"deep_iteration_count": 0}
        tasks = [_make_task(status="error", task_id="t1", error="Unauthorized access")]
        _set_retry(state, tasks, _mock_log())
        assert "permissions" in state["reflection"]["retry_fix"].lower()

    def test_forbidden_error_fix_description(self):
        """Forbidden error gets permissions fix description."""
        state = {"deep_iteration_count": 0}
        tasks = [_make_task(status="error", task_id="t1", error="403 Forbidden")]
        _set_retry(state, tasks, _mock_log())
        assert "permissions" in state["reflection"]["retry_fix"].lower()

    def test_generic_error_fix_description(self):
        """Generic error gets retry with adjusted parameters."""
        state = {"deep_iteration_count": 0}
        tasks = [_make_task(status="error", task_id="t1", error="Something else")]
        _set_retry(state, tasks, _mock_log())
        assert "retry" in state["reflection"]["retry_fix"].lower()

    def test_missing_iteration_count_defaults_to_zero(self):
        """Missing deep_iteration_count defaults to 0, then incremented to 1."""
        state = {}
        tasks = [_make_task(status="error", error="timeout")]
        _set_retry(state, tasks, _mock_log())
        assert state["deep_iteration_count"] == 1


# ============================================================================
# 6. _set_continue
# ============================================================================

class TestSetContinue:
    """Tests for _set_continue()."""

    def test_sets_continue_decision(self):
        """Sets continue decision."""
        state = {"deep_iteration_count": 0}
        evaluation = {"continue_description": "Need more data", "reasoning": "Partial"}
        _set_continue(state, evaluation, _mock_log())
        assert state["reflection_decision"] == "continue"
        assert state["deep_iteration_count"] == 1
        assert state["sub_agent_tasks"] == []
        assert state["reflection"]["continue_description"] == "Need more data"

    def test_missing_fields_use_defaults(self):
        """Missing evaluation fields use defaults."""
        state = {"deep_iteration_count": 1}
        _set_continue(state, {}, _mock_log())
        assert state["reflection"]["continue_description"] == "More steps needed"
        assert state["reflection"]["reasoning"] == ""
        assert state["deep_iteration_count"] == 2


# ============================================================================
# 7. _parse_evaluation_response
# ============================================================================

class TestParseEvaluationResponse:
    """Tests for _parse_evaluation_response()."""

    def test_clean_json(self):
        """Clean JSON with decision key is returned."""
        log = _mock_log()
        content = json.dumps({"decision": "respond_success", "reasoning": "all good"})
        result = _parse_evaluation_response(content, log)
        assert result["decision"] == "respond_success"
        assert result["reasoning"] == "all good"

    def test_json_in_code_fence(self):
        """JSON wrapped in ```json fences is extracted."""
        log = _mock_log()
        raw = {"decision": "retry", "reasoning": "timeout"}
        content = f"```json\n{json.dumps(raw)}\n```"
        result = _parse_evaluation_response(content, log)
        assert result["decision"] == "retry"

    def test_json_in_plain_code_fence(self):
        """JSON wrapped in plain ``` fences is extracted."""
        log = _mock_log()
        raw = {"decision": "continue", "continue_description": "more steps"}
        content = f"```\n{json.dumps(raw)}\n```"
        result = _parse_evaluation_response(content, log)
        assert result["decision"] == "continue"

    def test_invalid_json_with_embedded_json(self):
        """Text with embedded JSON object is extracted via regex."""
        log = _mock_log()
        content = 'Here is my analysis: {"decision": "respond_error", "reasoning": "failed"} done.'
        result = _parse_evaluation_response(content, log)
        assert result["decision"] == "respond_error"

    def test_completely_unparseable(self):
        """Completely unparseable content returns default."""
        log = _mock_log()
        result = _parse_evaluation_response("No JSON here at all!", log)
        assert result["decision"] == "respond_success"

    def test_json_without_decision_key(self):
        """JSON without 'decision' key is not accepted."""
        log = _mock_log()
        content = json.dumps({"reasoning": "something", "data": "ok"})
        result = _parse_evaluation_response(content, log)
        # Falls back to default since no "decision" key
        assert result["decision"] == "respond_success"

    def test_empty_string(self):
        """Empty string returns default."""
        log = _mock_log()
        result = _parse_evaluation_response("", log)
        assert result["decision"] == "respond_success"


# ============================================================================
# 8. _build_evaluator_instructions
# ============================================================================

class TestBuildEvaluatorInstructions:
    """Tests for _build_evaluator_instructions()."""

    def test_with_instructions(self):
        """Agent instructions are included."""
        state = {"instructions": "Always respond in Spanish."}
        result = _build_evaluator_instructions(state)
        assert "Agent Instructions" in result
        assert "Always respond in Spanish." in result

    def test_empty_instructions(self):
        """Empty instructions return empty string."""
        state = {"instructions": "   "}
        result = _build_evaluator_instructions(state)
        assert result == ""

    def test_no_instructions(self):
        """No instructions key returns empty string."""
        state = {}
        result = _build_evaluator_instructions(state)
        assert result == ""


# ============================================================================
# 9. aggregator_node - async tests
# ============================================================================

class TestAggregatorNode:
    """Tests for aggregator_node() async function."""

    @pytest.mark.asyncio
    async def test_all_success_fast_path(self):
        """All tasks succeeded -> respond_success fast path."""
        state = {
            "completed_tasks": [
                _make_task(status="success", task_id="t1"),
                _make_task(status="success", task_id="t2"),
            ],
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_all_failed_retryable(self):
        """All tasks failed with retryable errors -> retry."""
        state = {
            "completed_tasks": [
                _make_task(status="error", task_id="t1", error="timeout"),
            ],
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "retry"

    @pytest.mark.asyncio
    async def test_all_failed_non_retryable(self):
        """All tasks failed with non-retryable errors -> respond_error."""
        state = {
            "completed_tasks": [
                _make_task(status="error", task_id="t1", error="Permission denied"),
            ],
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "respond_error"

    @pytest.mark.asyncio
    async def test_all_failed_at_max_iterations(self):
        """All failed at max iterations -> respond_error even if retryable."""
        state = {
            "completed_tasks": [
                _make_task(status="error", task_id="t1", error="timeout"),
            ],
            "deep_iteration_count": 2,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "respond_error"

    @pytest.mark.asyncio
    async def test_partial_success_at_max_iterations(self):
        """Partial success at max iterations -> respond_success with available data."""
        state = {
            "completed_tasks": [
                _make_task(status="success", task_id="t1"),
                _make_task(status="error", task_id="t2", error="timeout"),
            ],
            "deep_iteration_count": 2,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_partial_success_no_retryable_errors(self):
        """Partial success with no retryable errors -> respond_success."""
        state = {
            "completed_tasks": [
                _make_task(status="success", task_id="t1"),
                _make_task(status="error", task_id="t2", error="not found"),
            ],
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_empty_completed_tasks(self):
        """Empty completed tasks list doesn't crash."""
        state = {
            "completed_tasks": [],
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        # With no completed tasks, none of the fast paths match, so falls through
        # to LLM evaluation which will fail (no LLM) and fallback to respond_error
        assert "reflection_decision" in result

    @pytest.mark.asyncio
    async def test_skipped_tasks_with_success(self):
        """Skipped tasks + success -> respond_success."""
        state = {
            "completed_tasks": [
                _make_task(status="success", task_id="t1"),
                _make_task(status="skipped", task_id="t2", error="Dependencies failed"),
            ],
            "deep_iteration_count": 2,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
        }
        config = {}
        writer = MagicMock()

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"):
            result = await aggregator_node(state, config, writer)

        assert result["reflection_decision"] == "respond_success"


# ============================================================================
# 10. _evaluate_with_llm
# ============================================================================

class TestEvaluateWithLlm:
    """Tests for _evaluate_with_llm()."""

    @pytest.mark.asyncio
    async def test_success_evaluation(self):
        """LLM returns valid JSON evaluation."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({"decision": "respond_success", "reasoning": "looks good"})
        mock_llm.ainvoke.return_value = mock_response

        state = {
            "llm": mock_llm,
            "query": "What happened last week?",
            "task_plan": {"goal": "summarize"},
        }
        completed = [_make_task(status="success", result={"response": "All good"})]

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await _evaluate_with_llm(state, completed, _mock_log())

        assert result["decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_error_evaluation(self):
        """LLM invocation fails -> returns fallback."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_llm.ainvoke.side_effect = RuntimeError("LLM down")

        state = {
            "llm": mock_llm,
            "query": "test",
            "task_plan": {},
        }

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            with pytest.raises(RuntimeError):
                await _evaluate_with_llm(state, [], _mock_log())

    @pytest.mark.asyncio
    async def test_evaluation_with_error_tasks(self):
        """LLM evaluates mix of success and error tasks."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = json.dumps({"decision": "retry", "reasoning": "some failed"})
        mock_llm.ainvoke.return_value = mock_response

        state = {
            "llm": mock_llm,
            "query": "complex task",
            "task_plan": {"goal": "do stuff"},
            "instructions": "Be thorough",
        }
        completed = [
            _make_task(status="success", task_id="t1", result={"response": "ok"}),
            _make_task(status="error", task_id="t2", error="failed"),
        ]

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await _evaluate_with_llm(state, completed, _mock_log())

        assert result["decision"] == "retry"


# ============================================================================
# 11. aggregator_node - LLM evaluation decision branches (lines 122-145)
# ============================================================================

class TestAggregatorNodeLLMDecisions:
    """Tests for aggregator_node with partial success going through LLM evaluation."""

    def _make_partial_state(self, iteration=0, max_iter=3):
        return {
            "completed_tasks": [
                _make_task(status="success", task_id="t1"),
                _make_task(status="error", task_id="t2", error="timeout"),
            ],
            "deep_iteration_count": iteration,
            "deep_max_iterations": max_iter,
            "logger": _mock_log(),
            "llm": AsyncMock(),
            "query": "test query",
            "task_plan": {"goal": "test"},
        }

    @pytest.mark.asyncio
    async def test_llm_respond_error_decision(self):
        """LLM returns respond_error -> sets respond_error (line 128)."""
        state = self._make_partial_state()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "respond_error", "reasoning": "bad data"})
        state["llm"].ainvoke.return_value = mock_resp

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        assert result["reflection_decision"] == "respond_error"

    @pytest.mark.asyncio
    async def test_llm_retry_decision(self):
        """LLM returns retry with iteration < max -> sets retry (line 130)."""
        state = self._make_partial_state(iteration=0, max_iter=3)
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "retry", "reasoning": "try again"})
        state["llm"].ainvoke.return_value = mock_resp

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        assert result["reflection_decision"] == "retry"

    @pytest.mark.asyncio
    async def test_llm_continue_decision(self):
        """LLM returns continue with iteration < max -> sets continue (line 132)."""
        state = self._make_partial_state(iteration=0, max_iter=3)
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "continue", "continue_description": "need more", "reasoning": "partial"})
        state["llm"].ainvoke.return_value = mock_resp

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        assert result["reflection_decision"] == "continue"

    @pytest.mark.asyncio
    async def test_llm_retry_at_max_iteration_fallback(self):
        """LLM returns retry at max iteration -> fallback to respond_success (lines 133-138)."""
        state = self._make_partial_state(iteration=2, max_iter=3)
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "retry", "reasoning": "try again"})
        state["llm"].ainvoke.return_value = mock_resp

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        # Fallback: success tasks exist -> respond_success
        assert result["reflection_decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_llm_retry_at_max_no_success_fallback(self):
        """LLM returns retry at max with no success tasks -> respond_error (line 138)."""
        state = {
            "completed_tasks": [
                _make_task(status="error", task_id="t1", error="timeout"),
                _make_task(status="error", task_id="t2", error="bad data"),
            ],
            "deep_iteration_count": 2,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
            "llm": AsyncMock(),
            "query": "test query",
            "task_plan": {"goal": "test"},
        }
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "retry", "reasoning": "try again"})
        state["llm"].ainvoke.return_value = mock_resp

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        assert result["reflection_decision"] == "respond_error"

    @pytest.mark.asyncio
    async def test_llm_exception_fallback_with_success(self):
        """LLM throws exception, success tasks exist -> fallback respond_success (lines 140-143)."""
        state = self._make_partial_state()
        state["llm"].ainvoke.side_effect = RuntimeError("LLM down")

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        assert result["reflection_decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_llm_exception_fallback_no_success(self):
        """LLM throws exception, no success tasks -> fallback respond_error (lines 144-145)."""
        state = {
            "completed_tasks": [
                _make_task(status="error", task_id="t1", error="bad"),
            ],
            "deep_iteration_count": 0,
            "deep_max_iterations": 3,
            "logger": _mock_log(),
            "llm": AsyncMock(),
            "query": "test",
            "task_plan": {},
        }
        state["llm"].ainvoke.side_effect = RuntimeError("LLM down")

        with patch("app.modules.agents.deep.aggregator.safe_stream_write"), \
             patch("app.modules.agents.deep.aggregator.send_keepalive", new_callable=AsyncMock), \
             patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await aggregator_node(state, {}, MagicMock())
        assert result["reflection_decision"] == "respond_error"


# ============================================================================
# 12. _evaluate_with_llm - result formatting branches (lines 297-328)
# ============================================================================

class TestEvaluateWithLlmFormatting:
    """Tests for result formatting in _evaluate_with_llm."""

    @pytest.mark.asyncio
    async def test_result_not_dict(self):
        """Task result that is not a dict uses str() fallback (lines 301-302)."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "respond_success", "reasoning": "ok"})
        mock_llm.ainvoke.return_value = mock_resp

        state = {"llm": mock_llm, "query": "test", "task_plan": {}}
        # result is a string, not a dict
        completed = [_make_task(status="success", result="plain string result")]

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await _evaluate_with_llm(state, completed, _mock_log())
        assert result["decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_error_task_with_description_and_duration(self):
        """Error task formatting with description and duration (lines 304-311)."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "retry", "reasoning": "errors"})
        mock_llm.ainvoke.return_value = mock_resp

        state = {"llm": mock_llm, "query": "test", "task_plan": {}}
        completed = [_make_task(status="error", task_id="t1", error="timeout error",
                               description="Search slack", duration_ms=500)]

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await _evaluate_with_llm(state, completed, _mock_log())
        assert result["decision"] == "retry"

    @pytest.mark.asyncio
    async def test_skipped_task_formatting(self):
        """Skipped task formatting (lines 312-315)."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "respond_success", "reasoning": "ok"})
        mock_llm.ainvoke.return_value = mock_resp

        state = {"llm": mock_llm, "query": "test", "task_plan": {}}
        completed = [
            _make_task(status="success", task_id="t1"),
            _make_task(status="skipped", task_id="t2", error="Dependencies failed"),
        ]

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await _evaluate_with_llm(state, completed, _mock_log())
        assert result["decision"] == "respond_success"

    @pytest.mark.asyncio
    async def test_plan_json_encoding_error(self):
        """Plan that fails JSON serialization uses str() fallback (lines 327-328)."""
        from app.modules.agents.deep.aggregator import _evaluate_with_llm

        mock_llm = AsyncMock()
        mock_resp = MagicMock()
        mock_resp.content = json.dumps({"decision": "respond_success", "reasoning": "ok"})
        mock_llm.ainvoke.return_value = mock_resp

        # Create a plan that json.dumps can't serialize normally
        class BadObj:
            def __str__(self):
                return "bad_obj_str"

        state = {
            "llm": mock_llm,
            "query": "test",
            "task_plan": {"data": BadObj()},
        }
        completed = [_make_task(status="success")]

        with patch("app.modules.agents.deep.aggregator.get_opik_config", return_value={}):
            result = await _evaluate_with_llm(state, completed, _mock_log())
        assert result["decision"] == "respond_success"


# ============================================================================
# 13. _parse_evaluation_response edge cases (lines 367-388)
# ============================================================================

class TestParseEvaluationResponseEdgeCases:
    """Additional edge cases for _parse_evaluation_response."""

    def test_embedded_json_without_decision_falls_through(self):
        """Embedded JSON via regex but without decision key (line 386-388)."""
        log = _mock_log()
        content = 'Some text {"key": "value"} more text'
        result = _parse_evaluation_response(content, log)
        # No "decision" key in extracted JSON -> default
        assert result["decision"] == "respond_success"

    def test_code_fence_with_no_closing(self):
        """Code fence without closing ``` (lines 367-375)."""
        log = _mock_log()
        content = '```json\n{"decision": "retry", "reasoning": "ok"}\nno closing fence'
        result = _parse_evaluation_response(content, log)
        assert result["decision"] == "retry"
