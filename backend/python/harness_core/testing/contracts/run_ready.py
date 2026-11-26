"""Contract suite for ``RunReadySignal`` adapters (LAT1 NT-7).

Subclass ``RunReadySignalContract`` and provide a ``ready_harness`` fixture: the
signal plus a way to announce a run as Node does. No sleeps: a wait that must
end does so on a notice; one that must not is bounded by a short timeout.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from harness_core.journal.ready import RunReadySignal

LONG_S = 5.0
SHORT_S = 0.05


class ReadyHarness(Protocol):
    @property
    def signal(self) -> RunReadySignal: ...

    async def announce(self, org_id: str, run_id: str) -> None: ...


class RunReadySignalContract:
    @pytest.fixture
    def ready_harness(self) -> ReadyHarness:
        raise NotImplementedError("provide a ready_harness fixture")

    async def test_rr1_without_a_notice_the_wait_times_out(self, ready_harness: ReadyHarness) -> None:
        assert await ready_harness.signal.wait(SHORT_S) is False
        assert ready_harness.signal.healthy is True

    async def test_rr2_a_notice_ends_a_wait(self, ready_harness: ReadyHarness) -> None:
        _ = await ready_harness.signal.wait(0)
        waiting = asyncio.create_task(ready_harness.signal.wait(LONG_S))
        await ready_harness.announce("org-1", "run-1")
        assert await asyncio.wait_for(waiting, LONG_S) is True

    async def test_rr3_notices_between_waits_never_wake_more_often_than_sent(
        self, ready_harness: ReadyHarness
    ) -> None:
        # Those already delivered coalesce; one still in flight may wake once more, never a lost one.
        _ = await ready_harness.signal.wait(0)
        for n in range(3):
            await ready_harness.announce("org-1", f"run-{n}")
        assert await ready_harness.signal.wait(LONG_S) is True
        wakes = [await ready_harness.signal.wait(SHORT_S) for _ in range(3)]
        assert sum(wakes) <= 2
        assert wakes[-1] is False


__all__ = ["ReadyHarness", "RunReadySignalContract"]
