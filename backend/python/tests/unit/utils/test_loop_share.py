"""LoopShare: background sweeps take at most a share of a busy event loop."""

from __future__ import annotations

import asyncio
import time

import pytest

from app.utils.loop_share import LoopShare


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []
        # How long the loop takes to hand control back after a yield.
        self.yield_takes = 0.0

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += self.yield_takes if seconds == 0 else seconds


def _share(clock: _Clock, **kwargs) -> LoopShare:
    kwargs.setdefault("max_pause_seconds", 60.0)
    return LoopShare(clock=clock, sleep=clock.sleep, **kwargs)


class TestPacing:
    async def test_an_idle_loop_only_yields(self) -> None:
        clock = _Clock()
        pacer = _share(clock)
        clock.now += 1.0  # a unit of work

        assert await pacer.pause() == 0.0
        assert clock.slept == [0]

    async def test_a_busy_loop_gets_the_rest_of_the_time(self) -> None:
        clock = _Clock()
        clock.yield_takes = 0.05
        pacer = _share(clock, share=0.25)
        clock.now += 1.0

        # 1 s of work at a quarter share is followed by 3 s for everything else.
        assert await pacer.pause() == pytest.approx(3.0)
        assert clock.slept == [0, pytest.approx(3.0)]

    async def test_a_pause_is_capped(self) -> None:
        clock = _Clock()
        clock.yield_takes = 0.05
        pacer = _share(clock, share=0.25, max_pause_seconds=5.0)
        clock.now += 100.0

        assert await pacer.pause() == 5.0

    async def test_time_before_begin_is_not_counted(self) -> None:
        clock = _Clock()
        clock.yield_takes = 0.05
        pacer = _share(clock, share=0.5)
        clock.now += 600.0  # the sweep's own idle wait between pages
        pacer.begin()
        clock.now += 0.2

        assert await pacer.pause() == pytest.approx(0.2)

    async def test_each_pause_starts_the_next_unit(self) -> None:
        clock = _Clock()
        clock.yield_takes = 0.05
        pacer = _share(clock, share=0.5)
        clock.now += 1.0
        await pacer.pause()
        clock.now += 0.1

        assert await pacer.pause() == pytest.approx(0.1)

    def test_a_share_outside_zero_to_one_is_refused(self) -> None:
        for share in (0, -0.1, 1.5):
            with pytest.raises(ValueError):
                LoopShare(share, max_pause_seconds=1.0)


class TestOnARealLoop:
    async def test_it_waits_while_other_work_is_ready_and_not_otherwise(self) -> None:
        pacer = LoopShare(0.25, max_pause_seconds=0.2)
        pacer.begin()
        time.sleep(0.02)  # the sweep's own unit of work
        assert await pacer.pause() == 0.0

        stop = asyncio.Event()

        async def indexing() -> None:
            while not stop.is_set():
                time.sleep(0.005)  # a record's CPU between awaits
                await asyncio.sleep(0)

        workers = [asyncio.create_task(indexing()) for _ in range(3)]
        try:
            await asyncio.sleep(0)
            pacer.begin()
            time.sleep(0.02)
            assert await pacer.pause() > 0.05
        finally:
            stop.set()
            await asyncio.gather(*workers)
