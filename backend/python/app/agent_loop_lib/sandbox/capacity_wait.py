"""A sandbox call's wait for capacity the governor cannot see.

The governor's slot is this process's share; the broker's ceiling counts every sandbox of the install, engine
sandboxes too, so a call holding a slot can still find the broker full. ``SandboxManager.slot`` sets the call's
wait here (what is left of ``SANDBOX_SLOT_WAIT_S`` and its "waiting in queue" note) and the backend spends it
on the refusals that mean "full", so a busy install queues a code run instead of failing it at once.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import time
from collections.abc import Awaitable, Callable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TypeVar

__all__ = ["CapacityWait", "capacity_wait", "retry_while_full"]

T = TypeVar("T")

_FIRST_POLL_S = 0.5
_MAX_POLL_S = 5.0


@dataclass(frozen=True)
class CapacityWait:
    # On `clock`; None waits as long as it takes (a negative SANDBOX_SLOT_WAIT_S).
    deadline: float | None
    # Shows (True) or clears (False) the call's queued note.
    note: Callable[[bool], Awaitable[None]]
    clock: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep


_current: ContextVar[CapacityWait | None] = ContextVar("sandbox_capacity_wait", default=None)


def _left(wait: CapacityWait) -> float:
    return math.inf if wait.deadline is None else wait.deadline - wait.clock()


@contextlib.contextmanager
def capacity_wait(wait: CapacityWait) -> Iterator[None]:
    token = _current.set(wait)
    try:
        yield
    finally:
        _current.reset(token)


async def retry_while_full(
    attempt: Callable[[], Awaitable[T]],
    is_full: Callable[[BaseException], bool],
    *,
    release: Callable[[T], Awaitable[None]] | None = None,
) -> T:
    """``attempt()``, tried again while it fails with a "full" refusal and the call's wait lasts; then that
    refusal. Outside a slot (no wait set) the first refusal is the answer, as before.

    *release* gives back what an attempt got when a cancel lands while the queued note is cleared, after the
    attempt succeeded and before the caller holds the result: otherwise nothing would ever release it."""
    wait = _current.get()
    delay = _FIRST_POLL_S
    shown = False
    got: list[T] = []
    try:
        while True:
            try:
                got.append(await attempt())
                return got[0]
            except Exception as exc:
                if wait is None or not is_full(exc) or _left(wait) <= 0:
                    raise
                if not shown:
                    shown = True
                    await wait.note(True)
                # Read again after the note, which may itself take a while: no retry starts past the deadline.
                left = _left(wait)
                if left <= 0:
                    raise
                await wait.sleep(min(delay, left))
                if _left(wait) <= 0:
                    raise
                delay = min(delay * 2, _MAX_POLL_S)
    finally:
        if shown:
            try:
                await asyncio.shield(wait.note(False))
            except asyncio.CancelledError:
                if got and release is not None:
                    await asyncio.shield(release(got[0]))
                raise
