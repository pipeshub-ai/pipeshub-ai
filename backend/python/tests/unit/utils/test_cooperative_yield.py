"""The cooperative yield helper: counts work in units, yields on elapsed time, never per row."""
from __future__ import annotations

import asyncio

import pytest

from app.utils.cooperative_yield import CooperativeYield


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_it_does_not_read_the_clock_until_check_every_units_have_passed() -> None:
    reads = 0

    def clock() -> float:
        nonlocal reads
        reads += 1
        return 0.0

    pacer = CooperativeYield(budget_seconds=0.01, check_every=100, clock=clock)
    reads = 0
    for _ in range(99):
        assert pacer.due() is False
    assert reads == 0
    pacer.due()
    assert reads == 1


def test_it_is_due_only_after_the_budget_has_elapsed() -> None:
    clock = FakeClock()
    pacer = CooperativeYield(budget_seconds=0.010, check_every=10, clock=clock)

    clock.now = 0.009
    assert pacer.due(10) is False
    clock.now = 0.010
    assert pacer.due(10) is True


def test_work_is_counted_in_units_not_in_calls() -> None:
    """One call that stands for a 16,000-cell row counts as 16,000, not as one."""
    clock = FakeClock()
    pacer = CooperativeYield(budget_seconds=0.0, check_every=1000, clock=clock)

    assert pacer.due(1) is False
    assert pacer.due(16_000) is True


async def test_pause_gives_other_tasks_a_turn_and_restarts_the_budget() -> None:
    clock = FakeClock()
    pacer = CooperativeYield(budget_seconds=0.010, check_every=1, clock=clock)
    ran = False

    async def other() -> None:
        nonlocal ran
        ran = True

    task = asyncio.create_task(other())
    clock.now = 0.5
    assert pacer.due() is True
    await pacer.pause()

    assert ran
    await task
    assert pacer.due() is False  # the budget restarted at the pause


async def test_tick_pauses_only_when_due() -> None:
    clock = FakeClock()
    pacer = CooperativeYield(budget_seconds=0.010, check_every=1, clock=clock)
    ran = []

    async def other() -> None:
        ran.append(1)

    asyncio.create_task(other())
    await pacer.tick()
    assert ran == []  # not due: no yield, the other task has not run

    clock.now = 1.0
    await pacer.tick()
    assert ran == [1]


@pytest.mark.parametrize("check_every", [0, -1])
def test_a_zero_check_interval_is_refused(check_every: int) -> None:
    with pytest.raises(ValueError):
        CooperativeYield(check_every=check_every)


def test_a_negative_budget_is_refused() -> None:
    with pytest.raises(ValueError):
        CooperativeYield(budget_seconds=-1)
