"""Event-loop lag monitor.

Every in-flight record shares one worker event loop, so a step that holds the
loop does not look slow itself: every other record's graph, vector and
embedding calls look slow instead. This tells the two apart.

A task on the monitored loop sleeps ``interval`` seconds at a time and
measures how late it wakes. Lateness over ``warn_after`` is logged at WARNING
(at most once per ``warn_every`` seconds, with the count and worst value since
the last line), and the figures are kept for ``/health``.

A sleeping task cannot report a loop that never comes back, so a watchdog
thread also checks how long ago the task last ran. Past ``stack_after`` it
logs what the loop thread is executing at that moment, once per stall: the
frame that is holding the loop.

Overhead: one wake-up per interval on the loop and one on the thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
import threading
import time
import traceback
from typing import TYPE_CHECKING, Any

from app.core.org_context import spawn_detached

if TYPE_CHECKING:
    import logging
    from collections.abc import Callable

__all__ = ["LoopLagMonitor", "loop_lag_monitor_from_env"]

_DEFAULT_INTERVAL_SECONDS = 0.5
_DEFAULT_WARN_AFTER_SECONDS = 1.0
_DEFAULT_STACK_AFTER_SECONDS = 5.0
_DEFAULT_WARN_EVERY_SECONDS = 10.0
_RECENT_WINDOW_SECONDS = 60.0
_STACK_FRAMES = 12


def _env_seconds(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


class LoopLagMonitor:
    """Measures scheduling delay on one event loop."""

    def __init__(
        self,
        logger: logging.Logger,
        name: str,
        *,
        interval: float = _DEFAULT_INTERVAL_SECONDS,
        warn_after: float = _DEFAULT_WARN_AFTER_SECONDS,
        stack_after: float | None = _DEFAULT_STACK_AFTER_SECONDS,
        warn_every: float = _DEFAULT_WARN_EVERY_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval <= 0:
            raise ValueError(f"interval must be > 0, got {interval}")
        self.logger = logger
        self.name = name
        self.interval = interval
        self.warn_after = warn_after
        self.stack_after = stack_after
        self.warn_every = warn_every
        self._clock = clock

        self._task: asyncio.Task[None] | None = None
        self._loop_thread_id: int | None = None
        self._watchdog: threading.Thread | None = None
        self._stop = threading.Event()

        # Written by the loop task, read by the watchdog and /health. Plain
        # float/int attribute writes are atomic under the GIL.
        self._last_tick: float | None = None
        self.samples = 0
        self.last_lag = 0.0
        self.max_lag = 0.0
        self.lag_events = 0
        self.stalls = 0
        self._recent_max = 0.0
        self._recent_since = clock()
        self._pending_count = 0
        self._pending_max = 0.0
        self._last_warned: float | None = None
        self._stall_reported = False

    # -- lifecycle -------------------------------------------------------

    def start(self) -> asyncio.Task[None]:
        """Start on the running loop; call from that loop (e.g. ``call_soon``)."""
        self._loop_thread_id = threading.get_ident()
        self._stop.clear()
        self._last_tick = self._clock()
        self._task = spawn_detached(self._run(), name=f"loop-lag-monitor:{self.name}")
        if self.stack_after is not None and self.stack_after > 0:
            self._watchdog = threading.Thread(
                target=self._watch,
                name=f"loop-lag-watchdog:{self.name}",
                daemon=True,
            )
            self._watchdog.start()
        return self._task

    def stop(self) -> None:
        """Stop the watchdog and cancel the task. Safe from any thread."""
        self._stop.set()
        task = self._task
        self._task = None
        if task is not None and not task.done():
            # RuntimeError: the loop is already closed and the task went with it.
            with contextlib.suppress(RuntimeError):
                task.get_loop().call_soon_threadsafe(task.cancel)

    # -- measurement -----------------------------------------------------

    async def _run(self) -> None:
        while not self._stop.is_set():
            before = self._clock()
            await asyncio.sleep(self.interval)
            now = self._clock()
            self._last_tick = now
            # The loop ran again, so the next stall gets its own stack.
            self._stall_reported = False
            self.observe(max(0.0, now - before - self.interval), now)

    def observe(self, lag: float, now: float | None = None) -> None:
        """Record one lateness sample (seconds the loop woke us late)."""
        now = self._clock() if now is None else now
        self.samples += 1
        self.last_lag = lag
        if lag > self.max_lag:
            self.max_lag = lag
        if now - self._recent_since >= _RECENT_WINDOW_SECONDS:
            self._recent_max = 0.0
            self._recent_since = now
        if lag > self._recent_max:
            self._recent_max = lag
        if lag < self.warn_after:
            return
        self.lag_events += 1
        self._pending_count += 1
        if lag > self._pending_max:
            self._pending_max = lag
        if self._last_warned is not None and now - self._last_warned < self.warn_every:
            return
        self.logger.warning(
            "⏱ Event loop %s lagged %.2fs (worst %.2fs over %d late wake-up(s) "
            "since the last report; threshold %.2fs). Everything on this loop "
            "was stalled that long; look for blocking work, not slow I/O.",
            self.name,
            lag,
            self._pending_max,
            self._pending_count,
            self.warn_after,
        )
        self._last_warned = now
        self._pending_count = 0
        self._pending_max = 0.0

    def check_stalled(self, now: float | None = None) -> bool:
        """Log the loop thread's stack if the loop has not run for ``stack_after``.

        Returns True when a stack was logged. Called by the watchdog thread.
        """
        if self.stack_after is None or self._last_tick is None:
            return False
        now = self._clock() if now is None else now
        silent_for = now - self._last_tick - self.interval
        if silent_for < self.stack_after or self._stall_reported:
            return False
        self._stall_reported = True
        self.stalls += 1
        frame = sys._current_frames().get(self._loop_thread_id) if self._loop_thread_id else None
        stack = (
            "".join(traceback.format_stack(frame, limit=_STACK_FRAMES)).rstrip()
            if frame is not None
            else "<loop thread not found>"
        )
        self.logger.warning(
            "⏱ Event loop %s has not run for %.1fs; it is executing:\n%s",
            self.name,
            silent_for,
            stack,
        )
        return True

    def _watch(self) -> None:
        while not self._stop.wait(self.interval):
            with contextlib.suppress(Exception):
                self.check_stalled()

    def stats(self) -> dict[str, Any]:
        return {
            "interval_s": self.interval,
            "warn_after_s": self.warn_after,
            "samples": self.samples,
            "last_lag_s": round(self.last_lag, 3),
            "max_lag_recent_s": round(self._recent_max, 3),
            "max_lag_s": round(self.max_lag, 3),
            "late_wakeups": self.lag_events,
            "stalls_with_stack": self.stalls,
        }


def loop_lag_monitor_from_env(logger: logging.Logger, name: str) -> LoopLagMonitor | None:
    """Monitor configured from env; ``None`` when ``INDEXING_LOOP_LAG_WARN_SECONDS=0``."""
    warn_after = _env_seconds("INDEXING_LOOP_LAG_WARN_SECONDS", _DEFAULT_WARN_AFTER_SECONDS)
    if warn_after == 0:
        return None
    stack_after = _env_seconds("INDEXING_LOOP_STALL_STACK_SECONDS", _DEFAULT_STACK_AFTER_SECONDS)
    return LoopLagMonitor(
        logger,
        name,
        warn_after=warn_after,
        stack_after=stack_after or None,
    )
