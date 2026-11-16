"""A loop clock the test controls, for timing assertions that a busy runner cannot move."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def virtual_loop_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the running loop's clock jump to its next timer whenever it would sleep.

    Every ``asyncio.sleep``, ``asyncio.timeout`` and ``wait_for`` then takes no wall time, and loop time moves
    only by the waits the code makes, so an elapsed ``loop.time()`` is exact: a garbage-collection pause or a
    descheduled process adds nothing to it. Not for code that waits on a thread while a timer is pending: the
    clock would jump past the thread's work.
    """
    loop = asyncio.get_running_loop()
    selector = loop._selector  # type: ignore[attr-defined]
    if getattr(selector.select, "virtual", False):
        return  # one clock per loop: a second would run beside the first instead of replacing it
    real_select = selector.select
    now = loop.time()

    def select(timeout: float | None = None) -> list:
        nonlocal now
        events = real_select(0)
        if events or timeout == 0:
            return events
        if timeout is None:  # no timer: only a thread or a socket can wake the loop
            return real_select(None)
        now += timeout
        return []

    select.virtual = True  # type: ignore[attr-defined]
    monkeypatch.setattr(loop, "time", lambda: now)
    monkeypatch.setattr(selector, "select", select)
