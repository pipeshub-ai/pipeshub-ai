"""`tool.blocked`: one WARNING when a tool's failure streak reaches the threshold
(`app/agents/agent_loop/hooks/tool_blocking.py`)."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.executor import MAX_LOGGED_TOOL_NAME
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


def test_a_model_made_tool_path_is_capped(caplog: pytest.LogCaptureFixture) -> None:
    """An unknown tool's path carries the model's own name: the event logs at most MAX_LOGGED_TOOL_NAME characters of it."""
    caplog.set_level(logging.DEBUG)
    path = "/unresolved/" + "x" * 1000
    tracker = ToolErrorTracker()
    for turn in range(3):
        tracker.record(path, is_error=True, turn_index=turn)

    (record,) = _blocked(caplog)
    assert log_record_fields(record)["tool_path"] == path[:MAX_LOGGED_TOOL_NAME]
    assert tracker.is_blocked(path)          # the cap is the log's only: blocking still keys on the full path


async def test_a_call_refused_after_the_streak_names_why() -> None:
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext

    tracker = ToolErrorTracker()
    for turn in range(3):
        tracker.record(_PATH, is_error=True, turn_index=turn)
    ctx = ToolCallContext(tool_path=_PATH, tool_input={})

    async def _next() -> None:
        raise AssertionError("a blocked tool must not run")

    await tracker.pre_tool_use(ctx, _next)
    assert ctx.decision_code == "consecutive_failures"
