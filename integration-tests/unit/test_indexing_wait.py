"""The connector suites' wait for one record to finish indexing, when it is queued behind a sync.

A record synced with a few hundred others waits its turn, and how long that takes depends
on the message broker. With ``queue_progress`` the wait must keep going while the records
ahead of it are still being indexed, and must still fail soon once indexing stops moving.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest
from helper import indexing_wait
from helper.indexing_wait import wait_until_record_indexing_completed

pytestmark = pytest.mark.unit

# A 0.3-second budget polled every 0.02 seconds, on a clock the test owns. The wait used to run on
# the real clock, so a runner that stalled the loop for more than the budget between two polls ended
# the wait before the record finished: the failure was the machine's load, not the helper.
_TIMEOUT = 0.3
_POLL = 0.02


class _Clock:
    """Virtual time: ``sleep`` advances it, so a wait takes no real time and cannot be stalled."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += seconds
        await asyncio.sleep(0)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    virtual = _Clock()
    monkeypatch.setattr(indexing_wait, "time", SimpleNamespace(time=virtual.time))
    monkeypatch.setattr(indexing_wait, "asyncio", SimpleNamespace(sleep=virtual.sleep))
    return virtual


class _Graph:
    """The record stays QUEUED until ``done_at`` seconds have passed (never, when None)."""

    def __init__(self, clock: _Clock, done_at: float | None) -> None:
        self.clock = clock
        self.start = clock.now
        self.done_at = done_at

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> SimpleNamespace:
        done = self.done_at is not None and self.clock.now - self.start >= self.done_at
        return SimpleNamespace(indexing_status="COMPLETED" if done else "QUEUED")


def _queue(clock: _Clock, rising_for: float):
    """Records ahead are indexed one per poll for ``rising_for`` seconds, then none."""
    start, count = clock.now, 0

    async def progress() -> int:
        nonlocal count
        if clock.now - start < rising_for:
            count += 1
        return count

    return progress


def _wait(graph: _Graph, **kwargs):
    return wait_until_record_indexing_completed(
        graph, "connector", "ext", timeout=_TIMEOUT, poll_interval=_POLL, **kwargs)


def test_without_queue_progress_the_budget_is_fixed(clock: _Clock) -> None:
    with pytest.raises(TimeoutError):
        asyncio.run(_wait(_Graph(clock, done_at=3 * _TIMEOUT)))


def test_a_moving_queue_keeps_the_wait_going_past_the_budget(clock: _Clock) -> None:
    record = asyncio.run(_wait(_Graph(clock, done_at=3 * _TIMEOUT), queue_progress=_queue(clock, rising_for=10)))
    assert record.indexing_status == "COMPLETED"


def test_a_stalled_queue_still_fails_after_the_budget(clock: _Clock) -> None:
    started = clock.now
    with pytest.raises(TimeoutError, match=r"none in the last"):
        asyncio.run(_wait(_Graph(clock, done_at=None), queue_progress=_queue(clock, rising_for=_TIMEOUT)))
    assert clock.now - started < 3 * _TIMEOUT, "the wait ran on after indexing stopped moving"


def test_a_queue_that_moves_forever_stops_at_the_cap(clock: _Clock) -> None:
    started = clock.now
    with pytest.raises(TimeoutError, match=r"cap"):
        asyncio.run(_wait(_Graph(clock, done_at=None), queue_progress=_queue(clock, rising_for=10), max_wait=2 * _TIMEOUT))
    assert clock.now - started < 3 * _TIMEOUT


def test_a_cap_below_the_budget_holds_when_nothing_moves(clock: _Clock) -> None:
    started = clock.now
    with pytest.raises(TimeoutError, match=r"cap"):
        asyncio.run(_wait(_Graph(clock, done_at=None), queue_progress=_queue(clock, rising_for=0), max_wait=_TIMEOUT / 3))
    assert clock.now - started < _TIMEOUT, "the wait ran past max_wait"
