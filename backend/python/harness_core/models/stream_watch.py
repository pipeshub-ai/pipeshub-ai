"""How long a model call may go without progress (P4.9).

A stream's only progress signal is its events, so a stalled stream is one with no event for too
long, never one that has run long: a model that keeps streaming is never cut. The first event is
bounded separately and generously, since a model may be silent for minutes before it (queueing,
reasoning a Chat Completions stream does not show). A non-streamed call has no progress signal at
all, so it gets one bound on the whole response. A stall is ``ModelStalled``: a ``TransportError``
retryable in place only while nothing reached the caller (the rule every transport follows), and the
P4.7 trigger ``timeout`` for a kernel that retries it as a new model attempt.
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.kernel.core.exceptions import TransportError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Iterator

    from harness_core.kernel.core.streaming import StreamEvent

# Above the product's per-read client timeout (360 s), which a trickle never trips, so the provider's
# own error comes first whenever it can; the watch is the bound when it cannot.
DEFAULT_FIRST_EVENT_S = 600.0
DEFAULT_IDLE_S = 120.0
DEFAULT_RESPONSE_S = 900.0

# The bound the call being awaited runs under, and its length: set only while ``watched`` or ``bounded`` awaits it.
_WATCH: contextvars.ContextVar[tuple[asyncio.Timeout, float] | None] = contextvars.ContextVar(
    "harness_stream_watch", default=None
)

# ``ModelFailed.error_type`` and the P4.7 trigger a stall is.
TIMEOUT = "timeout"

StallPhase = Literal["first_event", "idle", "response"]


@dataclass(frozen=True)
class StreamTimeouts:
    first_event_s: float = DEFAULT_FIRST_EVENT_S
    idle_s: float = DEFAULT_IDLE_S
    response_s: float = DEFAULT_RESPONSE_S


@dataclass(frozen=True)
class CallTiming:
    """How one model call progressed (P4.10): the wait for its first event, the longest silence between two
    events after it, the whole call, and how many events it took. A non-streamed call is one event, at its end.
    What a slow step's log line needs to say whether the model was slow to start, stalled midway or simply long."""

    first_event_s: float
    max_gap_s: float
    duration_s: float
    events: int


class CallClock:
    """Times one call on an injected monotonic clock: created when the call starts, told of each event."""

    def __init__(self, clock: Callable[[], float]) -> None:
        super().__init__()
        self._clock = clock
        self._start = clock()
        self._first: float | None = None
        self._last = self._start
        self._max_gap = 0.0
        self._events = 0

    def event(self) -> None:
        now = self._clock()
        if self._first is None:
            self._first = now - self._start
        else:
            self._max_gap = max(self._max_gap, now - self._last)
        self._last = now
        self._events += 1

    def timing(self) -> CallTiming:
        duration = self._clock() - self._start
        first = duration if self._first is None else self._first
        return CallTiming(first, self._max_gap, duration, self._events)


class ModelStalled(TransportError):
    """No progress from the model within its bound."""

    error_type = TIMEOUT

    def __init__(self, phase: StallPhase, waited_s: float, events: int) -> None:
        what = {
            "first_event": "first stream event",
            "idle": "next stream event",
            "response": "response",
        }[phase]
        super().__init__(
            f"model {what} timed out after {waited_s:g}s", retryable=events == 0
        )
        self.phase: StallPhase = phase
        self.waited_s = waited_s
        self.events = events


async def watched(
    stream: AsyncIterator[StreamEvent], timeouts: StreamTimeouts
) -> AsyncIterator[StreamEvent]:
    """``stream``'s events, failing with ``ModelStalled`` when the next one is overdue. The inner
    stream is closed however this ends, so the provider stops (and a metered transport settles)."""
    events = 0
    try:
        while True:
            limit = timeouts.first_event_s if events == 0 else timeouts.idle_s
            bound = asyncio.timeout(limit)
            try:
                async with bound:
                    watching = _WATCH.set((bound, limit))
                    try:
                        event = await anext(stream)
                    finally:
                        _WATCH.reset(watching)
            except StopAsyncIteration:
                return
            except TimeoutError as exc:
                if not bound.expired():
                    raise
                raise ModelStalled(
                    "first_event" if events == 0 else "idle", limit, events
                ) from exc
            events += 1
            yield event
    finally:
        aclose = getattr(stream, "aclose", None)
        if aclose is not None:
            with contextlib.suppress(Exception):
                await aclose()


async def bounded[T](call: Awaitable[T], timeouts: StreamTimeouts) -> T:
    """``call``'s result, or ``ModelStalled`` when the whole response is overdue."""
    bound = asyncio.timeout(timeouts.response_s)
    try:
        async with bound:
            watching = _WATCH.set((bound, timeouts.response_s))
            try:
                return await call
            finally:
                _WATCH.reset(watching)
    except TimeoutError as exc:
        if not bound.expired():
            raise
        raise ModelStalled("response", timeouts.response_s, 0) from exc


@contextlib.contextmanager
def unwatched() -> Iterator[None]:
    """Time that is not the model's: the bound the call runs under is paused, and starts again in full when this
    ends. A call waiting for its provider slot (G-45) has not reached the model, so a long queue is never a stall."""
    current = _WATCH.get()
    if current is None:
        yield
        return
    bound, limit = current
    bound.reschedule(None)
    try:
        yield
    finally:
        if not bound.expired():
            bound.reschedule(asyncio.get_running_loop().time() + limit)


__all__ = [
    "DEFAULT_FIRST_EVENT_S",
    "CallClock",
    "CallTiming",
    "DEFAULT_IDLE_S",
    "DEFAULT_RESPONSE_S",
    "TIMEOUT",
    "ModelStalled",
    "StallPhase",
    "StreamTimeouts",
    "bounded",
    "unwatched",
    "watched",
]
