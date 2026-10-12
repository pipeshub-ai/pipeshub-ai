"""One-line-per-request stage timings for the chat/agent streaming paths.

Answers "where did the seconds go" without a profiler: each boundary on the
pre-first-token path calls `mark()`, and the whole request is emitted as a
single log line. Deltas are between marks, so the numbers sum to the total.

Overhead is a `perf_counter()` call and a tuple append per stage. Enabled by
default; set ``PIPESHUB_CHAT_TIMING=false`` to compile it down to no-ops.

`StageTimer` stops at the first agent event. `RunTimer` covers the rest of an
agent run — each model turn, each tool call, the pattern-match wait inside a
search, and the finish step — under the same flag.
"""

from __future__ import annotations

import contextlib
import os
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import logging
    from collections.abc import Hashable

__all__ = ["RunTimer", "StageTimer", "timing_enabled"]


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

    @property
    def enabled(self) -> bool:
        return self._enabled

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


class RunTimer:
    """Span timings for one agent run, written as one line when the run ends.

    Unlike `StageTimer`'s marks, spans are measured independently: tool calls
    can run in parallel and the pattern-match wait happens inside a tool call,
    so spans may overlap and do not sum to the total. Each span is logged
    under its name, in the order it finished; a name can repeat (two
    ``tool:retrieval`` spans, say).

    `start(key, name)` / `stop(key)` time a span between two events that share
    a key (a turn number, a tool-call id); `stop` without a matching `start` is
    ignored. `record(name, ms)` adds a span measured elsewhere. A span still
    open when `emit()` runs (a model call that raised, a tool cut off by a
    client disconnect) is logged too, timed up to that moment and marked
    ``(open)``. `emit()` is safe to call more than once; only the first call
    logs.
    """

    __slots__ = ("_enabled", "_t0", "_open", "_spans", "_emitted")

    def __init__(self, *, enabled: bool | None = None) -> None:
        self._enabled = timing_enabled() if enabled is None else enabled
        self._t0 = time.perf_counter()
        self._open: dict[Hashable, tuple[str, float]] = {}
        self._spans: list[tuple[str, float]] = []
        self._emitted = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def spans(self) -> list[tuple[str, float]]:
        """Finished spans as ``(name, ms)``, in the order they finished."""
        return list(self._spans)

    def start(self, key: Hashable, name: str) -> None:
        if not self._enabled:
            return
        self._open[key] = (name, time.perf_counter())

    def stop(self, key: Hashable) -> None:
        if not self._enabled:
            return
        opened = self._open.pop(key, None)
        if opened is not None:
            name, started = opened
            self._spans.append((name, (time.perf_counter() - started) * 1000.0))

    def record(self, name: str, ms: float) -> None:
        if not self._enabled:
            return
        self._spans.append((name, ms))

    def emit(self, logger: "logging.Logger", label: str, **extra: object) -> None:
        if not self._enabled or self._emitted:
            return
        self._emitted = True
        now = time.perf_counter()
        total_ms = (now - self._t0) * 1000.0
        finished = [f"{name}={ms:.0f}ms" for name, ms in self._spans]
        unfinished = [
            f"{name}={(now - started) * 1000.0:.0f}ms(open)"
            for name, started in self._open.values()
        ]
        parts = " ".join(finished + unfinished)
        suffix = " ".join(f"{k}={v}" for k, v in extra.items() if v is not None)
        with contextlib.suppress(Exception):
            logger.info(
                "⏱ %s total=%.0fms | %s%s",
                label, total_ms, parts, f" | {suffix}" if suffix else "",
            )
