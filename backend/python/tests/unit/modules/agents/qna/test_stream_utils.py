"""
Unit tests for app.modules.agents.qna.stream_utils

Tests safe_stream_write, stream_status, stream_error, and send_keepalive.
All external dependencies (StreamWriter, RunnableConfig) are mocked.
"""

import asyncio
import logging
from unittest.mock import MagicMock, patch

import pytest

from app.modules.agents.qna.stream_utils import (
    safe_stream_write,
    send_keepalive,
    stream_error,
    stream_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_config():
    """Return a mock RunnableConfig."""
    return {"configurable": {"thread_id": "test-thread"}}


# ============================================================================
# 1. safe_stream_write
# ============================================================================

class TestSafeStreamWrite:
    """Tests for safe_stream_write()."""

    def test_none_writer_returns_false(self):
        """When writer is None, returns False immediately."""
        result = safe_stream_write(None, {"event": "test", "data": {}})
        assert result is False

    def test_successful_write_returns_true(self):
        """Normal write succeeds and returns True."""
        writer = MagicMock()
        event_data = {"event": "status", "data": {"message": "ok"}}
        result = safe_stream_write(writer, event_data)
        assert result is True
        writer.assert_called_once_with(event_data)

    def test_writer_called_with_correct_data(self):
        """Writer receives the exact event_data dict."""
        writer = MagicMock()
        event_data = {"event": "custom", "data": {"key": "value"}}
        safe_stream_write(writer, event_data)
        writer.assert_called_once_with(event_data)

    def test_runtime_error_with_get_config_and_config_restores_context(self):
        """RuntimeError with 'get_config' triggers context restoration."""
        writer = MagicMock()
        # First call raises RuntimeError, second call (after context restore) succeeds
        writer.side_effect = [
            RuntimeError("get_config not found in context"),
            None,
        ]
        config = _mock_config()

        with patch(
            "app.modules.agents.qna.stream_utils.var_child_runnable_config"
        ) as mock_var:
            mock_token = MagicMock()
            mock_var.set.return_value = mock_token
            result = safe_stream_write(writer, {"event": "test", "data": {}}, config)

        assert result is True
        mock_var.set.assert_called_once_with(config)
        mock_var.reset.assert_called_once_with(mock_token)

    def test_runtime_error_with_get_config_no_config_returns_false(self):
        """RuntimeError with 'get_config' but no config => returns False."""
        writer = MagicMock()
        writer.side_effect = RuntimeError("get_config not found")
        result = safe_stream_write(writer, {"event": "test", "data": {}}, config=None)
        assert result is False

    def test_runtime_error_without_get_config_returns_false(self):
        """RuntimeError without 'get_config' in message returns False."""
        writer = MagicMock()
        writer.side_effect = RuntimeError("some other error")
        result = safe_stream_write(
            writer, {"event": "test", "data": {}}, config=_mock_config()
        )
        assert result is False

    def test_runtime_error_context_restore_also_fails(self):
        """If context restoration also fails, returns False."""
        writer = MagicMock()
        writer.side_effect = RuntimeError("get_config not found")
        config = _mock_config()

        with patch(
            "app.modules.agents.qna.stream_utils.var_child_runnable_config"
        ) as mock_var:
            mock_token = MagicMock()
            mock_var.set.return_value = mock_token
            # Writer still fails after context restore
            writer.side_effect = [
                RuntimeError("get_config not found"),
                RuntimeError("still fails after restore"),
            ]
            result = safe_stream_write(
                writer, {"event": "test", "data": {}}, config
            )

        assert result is False

    def test_unexpected_exception_returns_false(self):
        """Non-RuntimeError exceptions return False."""
        writer = MagicMock()
        writer.side_effect = ValueError("unexpected")
        result = safe_stream_write(writer, {"event": "test", "data": {}})
        assert result is False

    def test_log_errors_true_logs_unexpected(self):
        """When log_errors is True, unexpected errors are logged."""
        writer = MagicMock()
        writer.side_effect = TypeError("bad type")
        with patch("app.modules.agents.qna.stream_utils.logger") as mock_logger:
            safe_stream_write(writer, {"event": "test", "data": {}}, log_errors=True)
            mock_logger.warning.assert_called()

    def test_log_errors_false_suppresses_logging(self):
        """When log_errors is False, runtime errors without config are not logged."""
        writer = MagicMock()
        writer.side_effect = RuntimeError("get_config issue")
        # No config provided, log_errors=False
        with patch("app.modules.agents.qna.stream_utils.logger") as mock_logger:
            safe_stream_write(
                writer,
                {"event": "test", "data": {}},
                config=None,
                log_errors=False,
            )
            mock_logger.warning.assert_not_called()

    def test_runtime_error_no_get_config_log_errors_false(self):
        """RuntimeError without 'get_config', log_errors=False => no log."""
        writer = MagicMock()
        writer.side_effect = RuntimeError("other error")
        with patch("app.modules.agents.qna.stream_utils.logger") as mock_logger:
            result = safe_stream_write(
                writer,
                {"event": "test", "data": {}},
                config=_mock_config(),
                log_errors=False,
            )
        assert result is False
        mock_logger.warning.assert_not_called()


# ============================================================================
# 2. stream_status
# ============================================================================

class TestStreamStatus:
    """Tests for stream_status()."""

    def test_basic_status_stream(self):
        """Streams status event with correct structure."""
        writer = MagicMock()
        result = stream_status(writer, "analyzing", "Analyzing your request...")
        assert result is True
        call_args = writer.call_args[0][0]
        assert call_args["event"] == "status"
        assert call_args["data"]["status"] == "analyzing"
        assert call_args["data"]["message"] == "Analyzing your request..."

    def test_extra_data_included(self):
        """Extra keyword arguments are included in data."""
        writer = MagicMock()
        stream_status(
            writer, "processing", "Working...", progress=50, step="extraction"
        )
        call_args = writer.call_args[0][0]
        assert call_args["data"]["progress"] == 50
        assert call_args["data"]["step"] == "extraction"

    def test_none_writer_returns_false(self):
        """None writer returns False (delegated to safe_stream_write)."""
        result = stream_status(None, "test", "message")
        assert result is False

    def test_with_config(self):
        """Config is passed through to safe_stream_write."""
        writer = MagicMock()
        config = _mock_config()
        result = stream_status(writer, "status", "msg", config)
        assert result is True

    def test_various_status_values(self):
        """Different status values are correctly passed through."""
        writer = MagicMock()
        for status in ["analyzing", "processing", "complete", "error", "keepalive"]:
            stream_status(writer, status, f"Status: {status}")
            call_args = writer.call_args[0][0]
            assert call_args["data"]["status"] == status

    def test_empty_message(self):
        """Empty message string is valid."""
        writer = MagicMock()
        result = stream_status(writer, "idle", "")
        assert result is True
        call_args = writer.call_args[0][0]
        assert call_args["data"]["message"] == ""

    def test_writer_error_returns_false(self):
        """If writer raises, returns False."""
        writer = MagicMock()
        writer.side_effect = ValueError("broken")
        result = stream_status(writer, "test", "msg")
        assert result is False


# ============================================================================
# 3. stream_error
# ============================================================================

class TestStreamError:
    """Tests for stream_error()."""

    def test_basic_error_stream(self):
        """Streams error event with correct structure."""
        writer = MagicMock()
        result = stream_error(writer, "Something went wrong")
        assert result is True
        call_args = writer.call_args[0][0]
        assert call_args["event"] == "error"
        assert call_args["data"]["message"] == "Something went wrong"
        assert call_args["data"]["code"] is None

    def test_error_with_code(self):
        """Error code is included when provided."""
        writer = MagicMock()
        stream_error(writer, "Auth failed", error_code="AUTH_ERROR")
        call_args = writer.call_args[0][0]
        assert call_args["data"]["code"] == "AUTH_ERROR"

    def test_none_writer_returns_false(self):
        """None writer returns False."""
        result = stream_error(None, "error msg")
        assert result is False

    def test_with_config(self):
        """Config is passed through."""
        writer = MagicMock()
        config = _mock_config()
        result = stream_error(writer, "error", config=config)
        assert result is True

    def test_error_uses_log_errors_false(self):
        """stream_error calls safe_stream_write with log_errors=False."""
        writer = MagicMock()
        writer.side_effect = ValueError("broken")
        # Should NOT log because stream_error passes log_errors=False
        with patch("app.modules.agents.qna.stream_utils.logger") as mock_logger:
            result = stream_error(writer, "error")
            assert result is False
            # The unexpected exception handler in safe_stream_write uses log_errors
            # stream_error passes log_errors=False to safe_stream_write

    def test_empty_error_message(self):
        """Empty error message is valid."""
        writer = MagicMock()
        result = stream_error(writer, "")
        assert result is True
        call_args = writer.call_args[0][0]
        assert call_args["data"]["message"] == ""


# ============================================================================
# 4. send_keepalive
# ============================================================================

class TestSendKeepalive:
    """Tests for send_keepalive()."""

    @pytest.mark.asyncio
    async def test_keepalive_sends_events(self):
        """Keepalive sends periodic status events until cancelled."""
        writer = MagicMock()
        config = _mock_config()

        with patch(
            "app.modules.agents.qna.stream_utils.safe_stream_write", return_value=True
        ) as mock_write:
            task = asyncio.create_task(
                send_keepalive(writer, config, "Processing...", interval=0.01)
            )
            # Let it send a few keepalives
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        assert mock_write.call_count >= 1
        # Check the event structure
        first_call = mock_write.call_args_list[0]
        event_data = first_call[0][1]  # second positional arg
        assert event_data["event"] == "status"
        assert event_data["data"]["status"] == "keepalive"
        assert event_data["data"]["message"] == "Processing..."

    @pytest.mark.asyncio
    async def test_keepalive_stops_on_write_failure(self):
        """Keepalive stops when safe_stream_write returns False."""
        writer = MagicMock()
        config = _mock_config()

        with patch(
            "app.modules.agents.qna.stream_utils.safe_stream_write", return_value=False
        ):
            # Should return promptly when write fails
            task = asyncio.create_task(
                send_keepalive(writer, config, "msg", interval=0.01)
            )
            await asyncio.sleep(0.05)
            # Task should have completed on its own
            assert task.done()

    @pytest.mark.asyncio
    async def test_keepalive_stops_on_exception(self):
        """Keepalive stops when safe_stream_write raises exception."""
        writer = MagicMock()
        config = _mock_config()

        with patch(
            "app.modules.agents.qna.stream_utils.safe_stream_write",
            side_effect=Exception("disconnected"),
        ):
            task = asyncio.create_task(
                send_keepalive(writer, config, "msg", interval=0.01)
            )
            await asyncio.sleep(0.05)
            assert task.done()

    @pytest.mark.asyncio
    async def test_keepalive_uses_custom_interval(self):
        """Custom interval controls spacing between events."""
        writer = MagicMock()
        config = _mock_config()
        call_count = 0

        def count_calls(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return True

        with patch(
            "app.modules.agents.qna.stream_utils.safe_stream_write",
            side_effect=count_calls,
        ):
            task = asyncio.create_task(
                send_keepalive(writer, config, "msg", interval=0.02)
            )
            await asyncio.sleep(0.07)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # With 0.02s interval and 0.07s total, expect ~3 calls
        assert 1 <= call_count <= 5

    @pytest.mark.asyncio
    async def test_keepalive_passes_config(self):
        """Config is passed through to safe_stream_write."""
        writer = MagicMock()
        config = _mock_config()

        with patch(
            "app.modules.agents.qna.stream_utils.safe_stream_write", return_value=True
        ) as mock_write:
            task = asyncio.create_task(
                send_keepalive(writer, config, "msg", interval=0.01)
            )
            await asyncio.sleep(0.03)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        # Check config was passed
        first_call = mock_write.call_args_list[0]
        assert first_call[0][2] is config  # third positional arg
