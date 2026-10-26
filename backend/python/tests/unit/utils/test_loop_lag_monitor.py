"""Event-loop lag monitor (app.utils.loop_lag_monitor)."""

import asyncio
import logging
import threading
import time

import pytest

from app.utils.loop_lag_monitor import LoopLagMonitor, loop_lag_monitor_from_env


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def logger() -> logging.Logger:
    log = logging.getLogger("test_loop_lag_monitor")
    log.setLevel(logging.DEBUG)
    return log


def _warnings(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_lag_under_the_threshold_is_recorded_but_not_logged(logger, caplog) -> None:
    monitor = LoopLagMonitor(logger, "worker", warn_after=1.0, stack_after=None, clock=_Clock())
    with caplog.at_level(logging.WARNING, logger=logger.name):
        monitor.observe(0.2)
        monitor.observe(0.4)

    assert _warnings(caplog) == []
    stats = monitor.stats()
    assert stats["samples"] == 2
    assert stats["last_lag_s"] == 0.4
    assert stats["max_lag_s"] == 0.4
    assert stats["late_wakeups"] == 0


def test_late_wakeups_are_reported_at_most_once_per_period(logger, caplog) -> None:
    clock = _Clock()
    monitor = LoopLagMonitor(
        logger, "worker", warn_after=1.0, warn_every=10.0, stack_after=None, clock=clock,
    )
    with caplog.at_level(logging.WARNING, logger=logger.name):
        monitor.observe(1.5)
        clock.now += 2
        monitor.observe(4.0)
        clock.now += 2
        monitor.observe(2.0)
        clock.now += 10
        monitor.observe(1.2)

    warnings = _warnings(caplog)
    assert len(warnings) == 2
    assert "lagged 1.50s" in warnings[0]
    # The second line carries what the suppressed ones saw.
    assert "lagged 1.20s (worst 4.00s over 3 late wake-up(s)" in warnings[1]
    assert monitor.stats()["late_wakeups"] == 4
    assert monitor.stats()["max_lag_s"] == 4.0


def test_recent_maximum_resets_after_its_window(logger) -> None:
    clock = _Clock()
    monitor = LoopLagMonitor(logger, "worker", warn_after=100, stack_after=None, clock=clock)
    monitor.observe(3.0)
    clock.now += 61
    monitor.observe(0.1)

    assert monitor.stats()["max_lag_recent_s"] == 0.1
    assert monitor.stats()["max_lag_s"] == 3.0


def test_a_stalled_loop_logs_what_its_thread_is_executing_once(logger, caplog) -> None:
    clock = _Clock()
    monitor = LoopLagMonitor(logger, "worker", interval=0.5, stack_after=2.0, clock=clock)
    blocked = threading.Event()
    release = threading.Event()

    def stuck_in_a_blocking_call() -> None:
        blocked.set()
        release.wait(5)

    thread = threading.Thread(target=stuck_in_a_blocking_call)
    thread.start()
    try:
        assert blocked.wait(5)
        monitor._loop_thread_id = thread.ident
        monitor._last_tick = clock.now
        with caplog.at_level(logging.WARNING, logger=logger.name):
            clock.now += 1.0
            assert monitor.check_stalled() is False
            clock.now += 2.0
            assert monitor.check_stalled() is True
            clock.now += 5.0
            assert monitor.check_stalled() is False
    finally:
        release.set()
        thread.join()

    warnings = _warnings(caplog)
    assert len(warnings) == 1
    assert "has not run for 2.5s" in warnings[0]
    assert "stuck_in_a_blocking_call" in warnings[0]
    assert monitor.stats()["stalls_with_stack"] == 1


@pytest.mark.asyncio
async def test_monitor_measures_a_blocked_loop(logger, caplog) -> None:
    monitor = LoopLagMonitor(logger, "worker", interval=0.02, warn_after=0.1, stack_after=None)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        monitor.start()
        try:
            await asyncio.sleep(0.05)
            time.sleep(0.25)  # holds the loop, as a blocking parse would
            await asyncio.sleep(0.05)
        finally:
            monitor.stop()
            await asyncio.sleep(0)

    assert monitor.stats()["max_lag_s"] >= 0.15
    assert any("Event loop worker lagged" in w for w in _warnings(caplog))


@pytest.mark.asyncio
async def test_watchdog_thread_reports_a_blocked_loop(logger, caplog) -> None:
    monitor = LoopLagMonitor(logger, "worker", interval=0.02, warn_after=10, stack_after=0.1)
    with caplog.at_level(logging.WARNING, logger=logger.name):
        monitor.start()
        try:
            await asyncio.sleep(0.05)
            time.sleep(0.4)
            await asyncio.sleep(0.05)
        finally:
            monitor.stop()
            await asyncio.sleep(0)

    stall_lines = [w for w in _warnings(caplog) if "has not run for" in w]
    assert len(stall_lines) == 1
    assert "test_watchdog_thread_reports_a_blocked_loop" in stall_lines[0]


def test_interval_must_be_positive(logger) -> None:
    with pytest.raises(ValueError):
        LoopLagMonitor(logger, "worker", interval=0)


def test_env_can_switch_the_monitor_off_or_tune_it(monkeypatch, logger) -> None:
    monkeypatch.setenv("INDEXING_LOOP_LAG_WARN_SECONDS", "0")
    assert loop_lag_monitor_from_env(logger, "worker") is None

    monkeypatch.setenv("INDEXING_LOOP_LAG_WARN_SECONDS", "0.25")
    monkeypatch.setenv("INDEXING_LOOP_STALL_STACK_SECONDS", "0")
    monitor = loop_lag_monitor_from_env(logger, "worker")
    assert monitor is not None
    assert monitor.warn_after == 0.25
    assert monitor.stack_after is None

    monkeypatch.delenv("INDEXING_LOOP_LAG_WARN_SECONDS")
    monkeypatch.delenv("INDEXING_LOOP_STALL_STACK_SECONDS")
    monitor = loop_lag_monitor_from_env(logger, "worker")
    assert monitor is not None
    assert (monitor.warn_after, monitor.stack_after) == (1.0, 5.0)
