"""The behaviour tests' clock: virtual time only, and waits compared within rounding."""

from __future__ import annotations

import asyncio

import pytest

from tests.unit.connectors.sources.web.behaviour.web_behaviour_fakes import VirtualClock


@pytest.mark.asyncio
async def test_the_clock_moves_only_when_the_code_sleeps() -> None:
    clock = VirtualClock()
    start = clock.get_event_loop().time()
    await asyncio.sleep(0.01)
    assert clock.get_event_loop().time() == start
    await clock.sleep(2.5)
    assert clock.get_event_loop().time() == start + 2.5


@pytest.mark.asyncio
async def test_a_wait_computed_from_clock_readings_counts_as_that_wait() -> None:
    clock = VirtualClock()
    await clock.sleep(1.4331739070310885)
    now = clock.get_event_loop().time()
    await clock.sleep((now + 15.0) - clock.get_event_loop().time())
    assert clock.slept(15.0)
    assert not clock.slept(30.0)
