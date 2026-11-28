"""R15-MONEY (round 15 gate on be23cec0e3: WP-70's writing reserve x CF-13's unreadable ledger).

WP-70 reads the run's money for the writing reserve on the turn's ticks. When the ledger cannot be read, that read
logged a warning without a reason code, on every check, and only when a tick happened to fall inside the turn, so
CF-13's own fallback test (which reads every warning's reason code) failed now and then. The reserve's money read
says why, once per turn.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING

from harness_core.budget.run_budget import RunBudget
from harness_core.engines.controller import MONEY_CHECK_S
from harness_core.engines.types import RunLimits
from harness_core.testing.clock import ManualClock
from tests.unit.harness_core.engines.test_cf13_ledger_usage import (
    _Unreadable,  # pyright: ignore[reportPrivateUsage]
)
from tests.unit.harness_core.engines.test_controller import Ticks, World, task

if TYPE_CHECKING:
    import pytest


async def test_r15_money_1_an_unreadable_ledger_is_named_once_per_turn_by_the_writing_reserve(
    caplog: pytest.LogCaptureFixture,
) -> None:
    clock = ManualClock()
    ticks = Ticks(clock)
    world = World(clock=clock, sleep=ticks.sleep)
    run = world.run(usage=_Unreadable(), budget=RunBudget(5.0, None))
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=RunLimits(max_wall_s=3_600)))
    with caplog.at_level(logging.WARNING):
        runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, "hold\nsay hi")))
        await asyncio.wait_for(world.gateway.holding.wait(), 5)
        await ticks.parked()
        for _ in range(3):
            await ticks.advance(MONEY_CHECK_S + 1)
        world.gateway.release()
        outcome = await asyncio.wait_for(runner, 5)
    assert outcome.reason == "completed"
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert all("reasonCode" in r.__dict__["harness"] for r in warnings), [r.getMessage() for r in warnings]
    money = [r for r in warnings if getattr(r, "harness_event", "") == "engine.turn.money_unread"]
    assert len(money) == 1 and money[0].__dict__["harness"]["reasonCode"] == "usage_unreadable"
