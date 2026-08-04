"""Tests for `events/emitters/logging.py::LoggingEventEmitter`."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agent_loop_lib.events.emitters.logging import LoggingEventEmitter


def _event() -> AgentEvent:
    return AgentEvent(
        event_type=EventType.TOOL_CALL,
        run_context=RunContext(
            run_id="run-1", agent_id="agent-1", trace_id="trace-1", role_name="researcher", model="gpt-5",
        ),
        payload={"tool": "web_search"},
    )


class TestLoggingEventEmitter:
    def test_defaults_to_info_level_and_the_agent_loop_events_logger(self) -> None:
        emitter = LoggingEventEmitter()
        assert emitter.level == logging.INFO
        assert emitter.logger.name == "agent_loop.events"

    def test_custom_level_is_respected(self) -> None:
        emitter = LoggingEventEmitter(level=logging.DEBUG)
        assert emitter.level == logging.DEBUG

    async def test_emit_logs_event_type_run_id_and_payload(self) -> None:
        emitter = LoggingEventEmitter()
        emitter.logger = MagicMock()
        event = _event()

        await emitter.emit(event)

        emitter.logger.log.assert_called_once_with(
            logging.INFO, "[%s] run_id=%s %s", "tool_call", "run-1", {"tool": "web_search"},
        )

    async def test_emit_uses_the_configured_level(self) -> None:
        emitter = LoggingEventEmitter(level=logging.WARNING)
        emitter.logger = MagicMock()

        await emitter.emit(_event())

        assert emitter.logger.log.call_args.args[0] == logging.WARNING

    async def test_emit_writes_to_the_real_logger_and_is_capturable(self, caplog) -> None:
        emitter = LoggingEventEmitter()
        with caplog.at_level(logging.INFO, logger="agent_loop.events"):
            await emitter.emit(_event())

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.INFO
        assert "tool_call" in record.getMessage()
        assert "run_id=run-1" in record.getMessage()
        assert "web_search" in record.getMessage()
