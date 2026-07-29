"""Unit tests for app.utils.memory_monitor."""

import logging
from unittest.mock import MagicMock, patch

import pytest

import app.utils.memory_monitor as memory_monitor_module
from app.utils.memory_monitor import (
    MemoryMonitor,
    get_process_memory_mb,
    track_request_memory,
)


def _silent_logger() -> logging.Logger:
    log = logging.getLogger("test-memory-monitor")
    log.setLevel(logging.CRITICAL)
    return log


class TestGetProcessMemoryMb:
    def test_returns_rss_and_vms_in_mb(self):
        fake_info = MagicMock(rss=100 * 1024 * 1024, vms=200 * 1024 * 1024)
        fake_process = MagicMock()
        fake_process.memory_info.return_value = fake_info
        with patch.object(memory_monitor_module, "psutil", MagicMock(Process=MagicMock(return_value=fake_process))):
            result = get_process_memory_mb()
        assert result == (100.0, 200.0)

    def test_returns_none_when_psutil_unavailable(self):
        with patch.object(memory_monitor_module, "psutil", None):
            assert get_process_memory_mb() is None

    def test_returns_none_on_transient_read_error(self):
        fake_process = MagicMock()
        fake_process.memory_info.side_effect = OSError("no /proc")
        with patch.object(memory_monitor_module, "psutil", MagicMock(Process=MagicMock(return_value=fake_process))):
            assert get_process_memory_mb() is None


class TestMemoryMonitorStartStop:
    def test_start_is_a_no_op_when_psutil_unavailable(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service")
        with patch.object(memory_monitor_module, "psutil", None):
            monitor.start()
        assert monitor._thread is None

    def test_start_spawns_a_daemon_thread(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service", interval_s=60)
        with patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(50.0, 100.0)):
            monitor.start()
        try:
            assert monitor._thread is not None
            assert monitor._thread.daemon is True
            assert monitor._thread.is_alive()
        finally:
            monitor.stop()

    def test_start_twice_does_not_spawn_a_second_thread(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service", interval_s=60)
        with patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(50.0, 100.0)):
            monitor.start()
            first_thread = monitor._thread
            monitor.start()
        try:
            assert monitor._thread is first_thread
        finally:
            monitor.stop()

    def test_stop_joins_the_background_thread(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service", interval_s=60)
        with patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(50.0, 100.0)):
            monitor.start()
        thread = monitor._thread
        monitor.stop()
        assert thread is not None
        assert not thread.is_alive()

    def test_stop_before_start_does_not_raise(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service")
        monitor.stop()  # must not raise


class TestMemoryMonitorSampleOnce:
    def test_sample_once_returns_none_when_psutil_unavailable(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service")
        with patch.object(memory_monitor_module, "get_process_memory_mb", return_value=None):
            assert monitor.sample_once() is None

    def test_sample_once_publishes_gauges(self):
        monitor = MemoryMonitor(_silent_logger(), "query_service")
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(123.0, 456.0)),
            patch.object(memory_monitor_module, "set_process_memory") as mock_set,
        ):
            result = monitor.sample_once()
        assert result == (123.0, 456.0)
        mock_set.assert_called_once_with("query_service", 123.0, 456.0)

    def test_sample_once_logs_warning_when_over_threshold(self):
        log = MagicMock()
        monitor = MemoryMonitor(log, "query_service", warn_threshold_mb=100.0)
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(150.0, 300.0)),
            patch.object(memory_monitor_module, "set_process_memory"),
        ):
            monitor.sample_once()
        log.warning.assert_called_once()

    def test_sample_once_does_not_warn_when_under_threshold(self):
        log = MagicMock()
        monitor = MemoryMonitor(log, "query_service", warn_threshold_mb=1000.0)
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(150.0, 300.0)),
            patch.object(memory_monitor_module, "set_process_memory"),
        ):
            monitor.sample_once()
        log.warning.assert_not_called()

    def test_sample_once_never_warns_when_no_threshold_configured(self):
        log = MagicMock()
        monitor = MemoryMonitor(log, "query_service", warn_threshold_mb=None)
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", return_value=(999999.0, 999999.0)),
            patch.object(memory_monitor_module, "set_process_memory"),
        ):
            monitor.sample_once()
        log.warning.assert_not_called()


class TestMemoryMonitorRunLoop:
    def test_run_loop_survives_sample_exceptions(self):
        """A single failed sample must not kill the background thread — the
        next tick should still run."""
        log = MagicMock()
        monitor = MemoryMonitor(log, "query_service", interval_s=0.01)
        call_count = {"n": 0}

        def _flaky_sample():
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("boom")
            if call_count["n"] >= 2:
                monitor._stop.set()

        with patch.object(monitor, "sample_once", side_effect=_flaky_sample):
            monitor.start()
            monitor._thread.join(timeout=2)

        assert call_count["n"] >= 2
        log.warning.assert_called()


class TestTrackRequestMemory:
    def test_yields_through_when_psutil_unavailable(self):
        with patch.object(memory_monitor_module, "get_process_memory_mb", return_value=None):
            with track_request_memory(_silent_logger(), "query_service", "test_op"):
                did_run = True
        assert did_run

    def test_records_delta_on_normal_exit(self):
        samples = iter([(100.0, 200.0), (140.0, 240.0)])
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", side_effect=lambda: next(samples)),
            patch.object(memory_monitor_module, "observe_request_memory_delta") as mock_observe,
        ):
            with track_request_memory(_silent_logger(), "query_service", "test_op"):
                pass
        mock_observe.assert_called_once_with("query_service", "test_op", 40.0)

    def test_records_delta_even_when_block_raises(self):
        """The context manager's `finally` must still record the delta even
        when the wrapped code raises — a request that fails partway through
        can still be the one leaking memory."""
        samples = iter([(100.0, 200.0), (500.0, 900.0)])
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", side_effect=lambda: next(samples)),
            patch.object(memory_monitor_module, "observe_request_memory_delta") as mock_observe,
        ):
            with pytest.raises(ValueError):
                with track_request_memory(_silent_logger(), "query_service", "test_op"):
                    raise ValueError("boom")
        mock_observe.assert_called_once_with("query_service", "test_op", 400.0)

    def test_logs_warning_when_delta_exceeds_threshold(self):
        samples = iter([(100.0, 200.0), (300.0, 400.0)])
        log = MagicMock()
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", side_effect=lambda: next(samples)),
            patch.object(memory_monitor_module, "observe_request_memory_delta"),
        ):
            with track_request_memory(log, "query_service", "test_op", delta_warn_mb=50.0):
                pass
        log.warning.assert_called_once()

    def test_logs_debug_when_delta_under_threshold(self):
        samples = iter([(100.0, 200.0), (110.0, 210.0)])
        log = MagicMock()
        with (
            patch.object(memory_monitor_module, "get_process_memory_mb", side_effect=lambda: next(samples)),
            patch.object(memory_monitor_module, "observe_request_memory_delta"),
        ):
            with track_request_memory(log, "query_service", "test_op", delta_warn_mb=50.0):
                pass
        log.warning.assert_not_called()
        log.debug.assert_called_once()
