from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness_core.clock import Clock, SystemClock
from harness_core.testing import ManualClock
from tests.support.settle import settle


class TestK5Timers:
    def test_advance_fires_due_timers_in_deadline_order(self) -> None:
        clock = ManualClock()
        seen: list[tuple[str, float]] = []
        clock.call_later(3, lambda: seen.append(("c", clock.monotonic())))
        clock.call_later(1, lambda: seen.append(("a", clock.monotonic())))
        clock.call_later(1, lambda: seen.append(("b", clock.monotonic())))
        clock.call_later(10, lambda: seen.append(("late", clock.monotonic())))

        assert clock.advance(0.5) == 0
        assert clock.advance(2.5) == 3
        assert seen == [("a", 1.0), ("b", 1.0), ("c", 3.0)]
        assert clock.monotonic() == 3.0
        assert clock.pending_timers() == 1

    def test_cancelled_timer_does_not_fire(self) -> None:
        clock = ManualClock()
        fired: list[int] = []
        handle = clock.call_later(1, lambda: fired.append(1))
        handle.cancel()
        clock.advance(5)
        assert fired == []

    def test_timer_scheduled_from_a_callback_fires_in_the_same_advance(self) -> None:
        clock = ManualClock()
        fired: list[float] = []
        clock.call_later(1, lambda: clock.call_later(1, lambda: fired.append(clock.monotonic())))
        clock.advance(2)
        assert fired == [2.0]

    async def test_sleep_completes_only_when_advanced(self) -> None:
        clock = ManualClock()
        task = asyncio.create_task(clock.sleep(2))
        await asyncio.sleep(0)
        clock.advance(1.9)
        await asyncio.sleep(0)
        assert not task.done()
        clock.advance(0.1)
        await settle(task)

    def test_rejects_backwards_time(self) -> None:
        with pytest.raises(ValueError):
            ManualClock().advance(-1)

    @settings(max_examples=50, deadline=None)
    @given(
        delays=st.lists(st.floats(0, 100), min_size=1, max_size=20),
        steps=st.lists(st.floats(0, 30), min_size=1, max_size=10),
    )
    def test_firing_order_is_deterministic(self, delays: list[float], steps: list[float]) -> None:
        def run() -> list[int]:
            clock = ManualClock()
            order: list[int] = []
            for i, d in enumerate(delays):
                clock.call_later(d, lambda i=i: order.append(i))
            for s in steps:
                clock.advance(s)
            return order

        order = run()
        assert order == run()
        assert order == sorted(order, key=lambda i: (delays[i], i))


class TestSuspendAware:
    def test_suspend_moves_wall_and_suspend_aware_but_not_monotonic(self) -> None:
        clock = ManualClock()
        start = clock.now()
        lease_expired: list[bool] = []
        clock.call_later(30, lambda: lease_expired.append(True))

        assert clock.suspend(60) == 1
        assert lease_expired == [True]
        assert clock.monotonic() == 0.0
        assert clock.suspend_aware() == 60.0
        assert clock.now() - start == timedelta(seconds=60)


def test_both_clocks_satisfy_the_port() -> None:
    clocks: list[Clock] = [ManualClock(), SystemClock()]
    for clock in clocks:
        assert clock.now().tzinfo is not None
        assert clock.suspend_aware() >= 0
        assert clock.monotonic() >= 0
