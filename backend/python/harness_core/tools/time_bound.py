"""Bounds on one tool call, enforced by the loop that runs it (DUR2, DUR1-F7).

Two failure shapes, two bounds:

* **Wall clock** (``Tool.time_bound_s``): a call that keeps awaiting (a hung
  socket, a lost wake-up, an async busy loop) is cancelled at its bound, and
  never past its run's deadline (``ToolTimeBounds.wall_for``). The
  default is above every fixed-length tool the product has (``install_packages``
  waits 300 s, a sandbox provisions in 60 s); a tool whose length is its own
  argument (``run_code``'s ``timeout``) declares that plus headroom, and a
  composite whose every step is bounded on its own (a child agent loop, an
  engine task, a question to the user) declares ``None``.
* **Loop stall** (``stall_s``): a call that holds the event loop without
  yielding cannot be cancelled, because cancellation is delivered by the loop it
  blocks. A watchdog thread sees the loop stop ticking while the call's frame is
  on the loop thread's stack and raises ``ToolCallStalled`` in that thread. No
  legitimate tool holds the loop this long: at 30 s every other run, SSE
  heartbeat (15 s) and lease renewal (20 s) of the process has already missed
  its turn, and at the 60 s lease the attempt would be lost. CPython delivers
  the exception at the next bytecode, so a spin inside one C call (a regex) is
  stopped when that call returns.

Either way the call becomes a typed error result (``tool_timeout`` /
``tool_stalled``), or ``EffectUnknown`` for a caller that records effects: a
write stopped part-way may or may not have happened.
"""

from __future__ import annotations

import asyncio
import contextlib
import ctypes
import logging
import sys
import threading
import time
import weakref
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.budget.run_budget import scoped_bound_s

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from types import FrameType

__all__ = [
    "DEFAULT_STALL_S",
    "DEFAULT_WALL_S",
    "TOOL_STALLED",
    "TOOL_TIMEOUT",
    "BoundExceeded",
    "ToolCallStalled",
    "ToolTimeBounds",
    "run_bounded",
]

DEFAULT_WALL_S = 600.0
DEFAULT_STALL_S = 30.0

TOOL_TIMEOUT = "tool_timeout"
TOOL_STALLED = "tool_stalled"

BoundReason = Literal["tool_timeout", "tool_stalled"]

logger = logging.getLogger("harness_core.tools")


@dataclass(frozen=True)
class ToolTimeBounds:
    """The loop's policy: ``stall_s`` for every call, ``default_wall_s`` for a tool that declares none."""

    default_wall_s: float = DEFAULT_WALL_S
    stall_s: float = DEFAULT_STALL_S

    def wall_for(self, declared: float | None | Literal["default"]) -> float | None:
        """A call's wall bound: the tool's own, or the default for one that declares none, never past the deadline
        of the run it is for (ADR-RUN-01 D4, ``RunBudget.bound_s``). A tool that declares ``None`` (a question to the
        person, a child's run) stays unbounded: its own steps are."""
        return scoped_bound_s(self.default_wall_s if declared == "default" else declared)


@dataclass(frozen=True)
class BoundExceeded:
    """Why a call was stopped; ``message`` is what the model reads."""

    reason: BoundReason
    tool: str
    bound_s: float

    @property
    def message(self) -> str:
        if self.reason == TOOL_TIMEOUT:
            what = f"did not finish within {self.bound_s:g} s"
        else:
            what = f"held the agent's event loop for {self.bound_s:g} s without yielding"
        return f"[{self.reason}] {self.tool} {what} and was stopped. Do not repeat the same call unchanged."


class ToolCallStalled(BaseException):
    """Raised into the loop thread inside a call that stalled it.

    A ``BaseException`` so that a tool's own ``except Exception`` does not
    swallow it and keep spinning."""


async def run_bounded[T](
    call: Callable[[], Awaitable[T]],
    *,
    tool: str,
    wall_s: float | None,
    stall_s: float,
) -> T | BoundExceeded:
    """``call()``, or the bound it exceeded."""
    loop = asyncio.get_running_loop()
    frame = sys._getframe()  # pyright: ignore[reportPrivateUsage]
    try:
        with _watchdog(loop).guard(frame, stall_s):
            if wall_s is None:
                return await call()
            async with asyncio.timeout(wall_s):
                return await call()
    except TimeoutError:
        return BoundExceeded(TOOL_TIMEOUT, tool, wall_s or 0.0)
    except ToolCallStalled:
        return BoundExceeded(TOOL_STALLED, tool, stall_s)


@dataclass
class _Guarded:
    frame: FrameType
    stall_s: float
    interrupted: bool = False


class _LoopWatchdog:
    """Watches one event loop while at least one bounded call runs on it."""

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = weakref.ref(loop)
        self._thread_id = threading.get_ident()
        self._lock = threading.Lock()
        self._guarded: dict[int, _Guarded] = {}
        self._ticks = 0
        self._thread: threading.Thread | None = None

    @contextlib.contextmanager
    def guard(self, frame: FrameType, stall_s: float) -> Iterator[None]:
        entry = _Guarded(frame, stall_s)
        key = id(entry)
        with self._lock:
            self._guarded[key] = entry
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._watch, name="tool-stall-watchdog", daemon=True)
                self._thread.start()
        try:
            yield
        finally:
            with self._lock:
                del self._guarded[key]

    def _tick(self) -> None:
        self._ticks += 1

    def _watch(self) -> None:
        last_ticks = -1
        blocked_since = time.monotonic()
        while True:
            with self._lock:
                if not self._guarded:
                    self._thread = None
                    return
                interval = min(1.0, min(g.stall_s for g in self._guarded.values()) / 4)
            loop = self._loop()
            if loop is None or loop.is_closed():
                return
            with contextlib.suppress(RuntimeError):
                loop.call_soon_threadsafe(self._tick)
            time.sleep(interval)
            now = time.monotonic()
            if self._ticks != last_ticks:
                last_ticks, blocked_since = self._ticks, now
                continue
            self._interrupt_stalled(now - blocked_since)

    def _interrupt_stalled(self, blocked_s: float) -> None:
        current = sys._current_frames().get(self._thread_id)  # pyright: ignore[reportPrivateUsage]
        if current is None:
            return
        on_stack: set[int] = set()
        frame: FrameType | None = current
        while frame is not None:
            on_stack.add(id(frame))
            frame = frame.f_back
        with self._lock:
            # Innermost first: a bounded call nested in another is the one stuck.
            for entry in reversed(list(self._guarded.values())):
                if entry.interrupted or blocked_s < entry.stall_s or id(entry.frame) not in on_stack:
                    continue
                entry.interrupted = True
                _raise_in_thread(self._thread_id)
                return


def _raise_in_thread(thread_id: int) -> None:
    set_async_exc = getattr(ctypes.pythonapi, "PyThreadState_SetAsyncExc", None)
    if set_async_exc is None:
        logger.warning("tool.stall_unstoppable", extra={"reason": "no_async_exc"})
        return
    set_async_exc(ctypes.c_ulong(thread_id), ctypes.py_object(ToolCallStalled))


_watchdogs: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _LoopWatchdog] = weakref.WeakKeyDictionary()
_watchdogs_lock = threading.Lock()


def _watchdog(loop: asyncio.AbstractEventLoop) -> _LoopWatchdog:
    with _watchdogs_lock:
        watchdog = _watchdogs.get(loop)
        if watchdog is None:
            watchdog = _LoopWatchdog(loop)
            _watchdogs[loop] = watchdog
        return watchdog
