"""`tool.blocked`: one WARNING when a tool's failure streak reaches the threshold
(`app/agents/agent_loop/hooks/tool_blocking.py`)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agents.agent_loop.hooks.tool_blocking import ToolErrorTracker
from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    import pytest

_PATH = "/mcp/inst-1/search"


def _blocked(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == "tool.blocked"]


def test_the_third_failed_turn_logs_once(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    tracker = ToolErrorTracker()
    for turn in range(4):
        tracker.record(_PATH, is_error=True, turn_index=turn)

    (record,) = _blocked(caplog)
    assert record.levelno == logging.WARNING
    assert log_record_fields(record) == {"tool_path": _PATH, "consecutive_failures": 3}


def test_a_success_starts_a_new_streak_that_logs_again(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    tracker = ToolErrorTracker()
    for turn in range(3):
        tracker.record(_PATH, is_error=True, turn_index=turn)
    tracker.record(_PATH, is_error=False, turn_index=3)
    for turn in range(4, 7):
        tracker.record(_PATH, is_error=True, turn_index=turn)

    assert len(_blocked(caplog)) == 2


def test_parallel_failures_in_one_turn_are_one_strike(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    tracker = ToolErrorTracker()
    for _ in range(3):
        tracker.record(_PATH, is_error=True, turn_index=0)

    assert _blocked(caplog) == []
    assert not tracker.is_blocked(_PATH)
