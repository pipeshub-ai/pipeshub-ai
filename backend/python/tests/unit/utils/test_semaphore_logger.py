"""Unit tests for app.utils.semaphore_logger."""

import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from app.utils.semaphore_logger import SemaphoreLogger, get_timestamp


@pytest.fixture(autouse=True)
def reset_logger():
    """Reset SemaphoreLogger state before each test."""
    SemaphoreLogger._logger = None
    SemaphoreLogger._enabled = True
    yield
    SemaphoreLogger._logger = None
    SemaphoreLogger._enabled = True


class TestGetTimestamp:
    def test_returns_float(self):
        ts = get_timestamp()
        assert isinstance(ts, float)

    def test_close_to_current_time(self):
        before = time.time()
        ts = get_timestamp()
        after = time.time()
        assert before <= ts <= after


class TestSemaphoreLoggerGetLogger:
    def test_creates_logger_with_correct_name(self):
        logger = SemaphoreLogger._get_logger()
        assert logger.name == "semaphore_debug"

    def test_returns_same_logger(self):
        logger1 = SemaphoreLogger._get_logger()
        logger2 = SemaphoreLogger._get_logger()
        assert logger1 is logger2

    def test_logger_has_handler(self):
        logger = SemaphoreLogger._get_logger()
        assert len(logger.handlers) >= 1

    def test_disabled_via_env(self, monkeypatch):
        monkeypatch.setenv("SEMAPHORE_DEBUG_ENABLED", "false")
        SemaphoreLogger._logger = None
        SemaphoreLogger._get_logger()
        assert SemaphoreLogger._enabled is False


class TestSemaphoreLoggerWhenDisabled:
    def test_acquire_attempt_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_acquire_attempt("parse", "msg1", 2, 3, 4, 5)
        mock_logger.debug.assert_not_called()

    def test_acquired_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_acquired("msg1", 2, 3, 4, 5)
        mock_logger.debug.assert_not_called()

    def test_release_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_release("parse", "msg1", 2, 3)
        mock_logger.debug.assert_not_called()

    def test_phase_transition_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_phase_transition("msg1", "parsing_complete")
        mock_logger.debug.assert_not_called()

    def test_semaphore_state_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_state(2, 3, 4, 5, 6)
        mock_logger.debug.assert_not_called()

    def test_message_start_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_message_start("msg1", "topic", 0, 1)
        mock_logger.debug.assert_not_called()

    def test_message_error_noop(self):
        SemaphoreLogger._enabled = False
        mock_logger = MagicMock()
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_message_error("msg1", "boom")
        mock_logger.error.assert_not_called()


class TestSemaphoreLoggerWhenEnabled:
    def test_acquire_attempt_logs(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_acquire_attempt("parse", "msg1", 2, 3, 4, 5)
        mock_logger.debug.assert_called_once()
        msg = mock_logger.debug.call_args[0][0]
        assert "msg1" in msg
        assert "ACQUIRE_ATTEMPT" in msg

    def test_acquired_logs_with_wait_time(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_acquired("msg1", 2, 3, 4, 5, wait_time_ms=42.5)
        mock_logger.debug.assert_called_once()
        msg = mock_logger.debug.call_args[0][0]
        assert "ACQUIRED" in msg
        assert "42.50" in msg

    def test_acquired_logs_without_wait_time(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_acquired("msg1", 2, 3, 4, 5)
        msg = mock_logger.debug.call_args[0][0]
        assert "wait_time" not in msg

    def test_release_logs_with_duration_and_reason(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_release("parse", "msg1", 2, 3, duration=1.5, reason="done")
        msg = mock_logger.debug.call_args[0][0]
        assert "RELEASE" in msg
        assert "1.50" in msg
        assert "done" in msg

    def test_phase_transition_logs(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_phase_transition("msg1", "indexing_complete", record_id="r1", duration=2.0)
        msg = mock_logger.debug.call_args[0][0]
        assert "PHASE_COMPLETE" in msg
        assert "r1" in msg

    def test_semaphore_state_with_message_id(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_state(2, 3, 4, 5, 6, message_id="msg1")
        msg = mock_logger.debug.call_args[0][0]
        assert "STATE" in msg
        assert "msg1" in msg

    def test_semaphore_state_without_message_id(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_semaphore_state(2, 3, 4, 5, 6)
        msg = mock_logger.debug.call_args[0][0]
        assert "STATE" in msg

    def test_message_start_logs(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_message_start("msg1", "my-topic", 2, 100)
        msg = mock_logger.debug.call_args[0][0]
        assert "MESSAGE_START" in msg
        assert "my-topic" in msg

    def test_message_error_logs(self):
        mock_logger = MagicMock(spec=logging.Logger)
        mock_logger.handlers = [MagicMock()]
        SemaphoreLogger._logger = mock_logger
        SemaphoreLogger.log_message_error("msg1", "something broke")
        mock_logger.error.assert_called_once()
        msg = mock_logger.error.call_args[0][0]
        assert "ERROR" in msg
        assert "something broke" in msg
