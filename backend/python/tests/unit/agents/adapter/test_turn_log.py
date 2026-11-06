"""`turn_log`: one `agent_turn.end` per chat turn, saying how it ended, at a level that never
repeats a failure another line already reported (`app/agents/agent_loop/turn_log.py`)."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agents.agent_loop.protocol.run_usage import UsageLedger
from app.agents.agent_loop.turn_log import AgentRunTiming, log_turn_end, stop_reason_of
from app.utils.logger import JsonFormatter, TextFormatter, log_record_fields
from app.utils.stage_timer import RunTimer

_LOG = logging.getLogger("tests.turn_log")
_GOAL = Goal(description="query-sentinel")


def _result(**fields: object) -> AgentResult:
    return AgentResult(goal=_GOAL, **fields)


def _end_lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "agent_turn.end"]


def _timing(*, enabled: bool = True) -> AgentRunTiming:
    return AgentRunTiming(RunTimer(enabled=enabled), run_id="run-1", model="gpt-4", mode="quick")


class TestStopReason:
    @pytest.mark.parametrize(("result", "awaiting", "reason"), [
        (_result(success=False, cancelled=True, end_event="agent_cancelled"), False, "cancelled"),
        (_result(needs_input="which account?", end_event="task_complete"), False, "awaiting_input"),
        (_result(end_event="task_complete"), True, "awaiting_input"),
        (_result(end_event="agent_complete_partial"), False, "max_turns"),
        (_result(end_event="agent_complete_after_max_turns"), False, "max_turns"),
        (_result(success=False, end_event="agent_failed"), False, "max_turns"),
        (_result(success=False, end_event="guardrail_blocked"), False, "guardrail"),
        (_result(success=False, end_event="turn_blocked"), False, "guardrail"),
        (_result(success=False, end_event="agent_blocked"), False, "guardrail"),
        (_result(success=False, end_event="llm_call_failed"), False, "error"),
        (_result(success=False, end_event="context_budget_exceeded"), False, "error"),
        (_result(end_event="agent_complete"), False, "done"),
        (_result(end_event="task_complete"), False, "done"),
    ])
    def test_each_exit(self, result: AgentResult, awaiting: bool, reason: str) -> None:
        assert stop_reason_of(result, awaiting_approval=awaiting) == reason


class TestLevels:
    @pytest.mark.parametrize(("stop_reason", "fields", "level"), [
        ("done", {}, logging.INFO),
        ("awaiting_input", {}, logging.INFO),
        ("client_disconnect", {}, logging.INFO),
        ("max_turns", {}, logging.WARNING),
        # Already reported by llm_call.failed.
        ("error", {"end_event": "llm_call_failed", "error_class": "misconfigured"}, logging.INFO),
        # The context guard logged ERROR before raising.
        ("error", {"end_event": "context_budget_exceeded"}, logging.INFO),
        # No other line says why: the end line is the report.
        ("error", {}, logging.WARNING),
        ("error", {"error_class": "forbidden"}, logging.WARNING),
        ("error", {"error_class": "misconfigured"}, logging.ERROR),
    ])
    def test_level(self, caplog: pytest.LogCaptureFixture, stop_reason: str, fields: dict, level: int) -> None:
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            log_turn_end(_LOG, stop_reason, **fields)

        [record] = _end_lines(caplog)
        assert record.levelno == level

    def test_a_crash_is_one_failure_line_then_an_info_end(self, caplog: pytest.LogCaptureFixture) -> None:
        try:
            raise KeyError("missing")
        except KeyError as exc:
            with caplog.at_level(logging.DEBUG, logger=_LOG.name):
                log_turn_end(_LOG, "error", exc=exc, run_id="run-1", code="unknown")

        failed, end = caplog.records
        assert (failed.getMessage(), failed.levelno) == ("agent_turn.failed", logging.ERROR)
        assert failed.exc_info is not None
        assert log_record_fields(failed)["run_id"] == "run-1"
        assert (end.getMessage(), end.levelno) == ("agent_turn.end", logging.INFO)
        assert log_record_fields(end)["error_class"] == "internal"


class TestEnd:
    def test_the_line_sums_the_ledger_and_counts_the_turn(self, caplog: pytest.LogCaptureFixture) -> None:
        ledger = UsageLedger()
        ledger.add("main", TokenUsage(input_tokens=100, output_tokens=10, cache_read_tokens=60))
        ledger.add("grep_generation", TokenUsage(input_tokens=5, output_tokens=1))
        timing = _timing(enabled=False)
        timing.tool_calls = 1
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            timing.end(_LOG, "done", result=_result(end_event="agent_complete"), ledger=ledger)

        fields = log_record_fields(_end_lines(caplog)[0])
        duration = fields.pop("duration_ms")
        assert isinstance(duration, int) and duration >= 0
        assert fields == {
            "run_id": "run-1", "model": "gpt-4", "mode": "quick", "turns": 0, "tool_calls": 1,
            "tool_errors": 0, "llm_calls": 2, "tokens_in": 105, "tokens_out": 11,
            "cache_read_tokens": 60, "end_event": "agent_complete", "stop_reason": "done",
        }

    def test_a_failed_result_carries_its_class_and_the_clients_code(self, caplog: pytest.LogCaptureFixture) -> None:
        result = _result(
            success=False, error="LLM call failed: 429 rate limit exceeded",
            end_event="llm_call_failed", error_class="rate_limited",
        )
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            _timing().end(_LOG, "error", result=result)

        fields = log_record_fields(_end_lines(caplog)[0])
        assert (fields["error_class"], fields["code"], fields["end_event"]) == (
            "rate_limited", "rate_limit", "llm_call_failed",
        )

    def test_the_stop_reason_is_derived_from_the_result_when_not_given(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            _timing().end(_LOG, None, result=_result(end_event="task_complete"), awaiting_approval=True)

        assert log_record_fields(_end_lines(caplog)[0])["stop_reason"] == "awaiting_input"

    def test_a_result_that_cannot_be_read_costs_the_line_not_the_caller(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG):
            _timing().end(_LOG, None, result=object())  # type: ignore[arg-type]

        assert _end_lines(caplog) == []
        assert "agent_turn.end failed" in [r.getMessage() for r in caplog.records]

    def test_only_the_first_end_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        timing = _timing()
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            timing.end(_LOG, "done")
            timing.end(_LOG, "client_disconnect")

        assert [log_record_fields(r)["stop_reason"] for r in _end_lines(caplog)] == ["done"]

    def test_a_logging_failure_never_raises(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("app.agents.agent_loop.turn_log.log_event", side_effect=RuntimeError("log bug")),
            caplog.at_level(logging.DEBUG, logger="app.agents.agent_loop.turn_log"),
        ):
            _timing().end(_LOG, "done")

        assert [r.getMessage() for r in caplog.records] == ["agent_turn.end failed"]

    def test_spans_only_with_timing_on(self, caplog: pytest.LogCaptureFixture) -> None:
        on, off = _timing(), _timing(enabled=False)
        for timing in (on, off):
            timing._timer.record("tool:retrieval", 12.0)
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            on.end(_LOG, "done")
            off.end(_LOG, "done")

        spans = [log_record_fields(r).get("spans") for r in _end_lines(caplog)]
        assert spans == ["tool:retrieval=12ms", None]

    def test_the_line_never_carries_the_query_or_the_answer(self, caplog: pytest.LogCaptureFixture) -> None:
        result = _result(output="answer-sentinel", end_event="agent_complete")
        with caplog.at_level(logging.DEBUG, logger=_LOG.name):
            _timing().end(_LOG, "done", result=result)

        [record] = _end_lines(caplog)
        for formatter in (TextFormatter(), JsonFormatter()):
            assert "sentinel" not in formatter.format(record)


class TestRunTimer:
    def test_summary_lists_finished_then_open_spans(self) -> None:
        timer = RunTimer(enabled=True)
        timer.start("t1", "llm_turn_1")
        timer.stop("t1")
        timer.record("pattern_match_wait", 5.0)
        timer.start("c1", "tool:retrieval")

        summary = timer.summary()

        assert summary is not None
        names = [part.split("=")[0] for part in summary.split()]
        assert names == ["llm_turn_1", "pattern_match_wait", "tool:retrieval"]
        assert summary.endswith("ms(open)")

    def test_summary_is_none_when_disabled_or_empty(self) -> None:
        disabled = RunTimer(enabled=False)
        disabled.record("a", 1.0)
        assert disabled.summary() is None
        assert RunTimer(enabled=True).summary() is None

    def test_total_ms_runs_with_timing_off(self) -> None:
        timer = RunTimer(enabled=False)
        with patch("app.utils.stage_timer.time.perf_counter", return_value=timer._t0 + 1.5):
            assert timer.total_ms == pytest.approx(1500.0)
