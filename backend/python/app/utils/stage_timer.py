"""One-line-per-request stage timings for the chat/agent streaming paths.

Answers "where did the seconds go" without a profiler: each boundary on the
pre-first-token path calls `mark()`, and the whole request is emitted as a
single log line. Deltas are between marks, so the numbers sum to the total.

Overhead is a `perf_counter()` call and a tuple append per stage. Enabled by
default; set ``PIPESHUB_CHAT_TIMING=false`` to compile it down to no-ops.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging

__all__ = ["StageTimer", "timing_enabled"]


def timing_enabled() -> bool:
    return os.getenv("PIPESHUB_CHAT_TIMING", "true").strip().lower() not in ("false", "0", "no")


class StageTimer:
    """Monotonic stage timings for one request.

    `mark()` records the elapsed time since the previous mark. `emit()` writes
    the whole sequence as one line and is safe to call more than once (later
    calls are ignored) so a path with several exits cannot double-log.
    """

    __slots__ = ("_enabled", "_t0", "_last", "_marks", "_emitted")

    def __init__(self, enabled: bool | None = None) -> None:
        self._enabled = timing_enabled() if enabled is None else enabled
        now = time.perf_counter()
        self._t0 = now
        self._last = now
        self._marks: list[tuple[str, float]] = []
        self._emitted = False

    def mark(self, stage: str) -> None:
        if not self._enabled:
            return
        now = time.perf_counter()
        self._marks.append((stage, (now - self._last) * 1000.0))
        self._last = now

    @property
    def total_ms(self) -> float:
        return (time.perf_counter() - self._t0) * 1000.0

    def emit(self, logger: "logging.Logger", label: str, **extra: object) -> None:
        if not self._enabled or self._emitted:
            return
        self._emitted = True
        parts = " ".join(f"{name}={ms:.0f}ms" for name, ms in self._marks)
        suffix = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
        with contextlib.suppress(Exception):
            logger.info(
                "⏱ %s total=%.0fms | %s%s",
                label, self.total_ms, parts, f" | {suffix}" if suffix else "",
            )
