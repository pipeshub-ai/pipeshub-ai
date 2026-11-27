"""WP-81: the writing reserve on the wall clock fits the model's own step time (WP81-SYN).

A research run: Codex on gpt-6.1-sol took up to 226 seconds a model step while it wrote its
report. The 30-minute turn kept 15% (270 s) back for writing; the window opened at 25.5 minutes, 47 seconds into a
318-second step, so the steer could reach the engine only after that step, which the wall limit cut first. The
engine never read "stop gathering", the step's cost was charged for nothing, and the turn ended mid-edit.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
from typing import TYPE_CHECKING

from harness_core.budget.run_budget import (
    MIN_SYNTHESIS_WALL_S,
    WALL_RESERVE_STEPS,
    Allowance,
)
from harness_core.engines.types import RunLimits
from harness_core.testing.clock import ManualClock
from tests.unit.harness_core.engines.test_controller import Ticks, World, task

if TYPE_CHECKING:
    import pytest


def test_wp81_syn_1_the_wall_reserve_is_at_least_two_of_the_longest_steps() -> None:
    allowance = Allowance(steps=500, wall_s=1800, usd=5.0)
    # Fast steps keep the 15% share (run 5's 270 s).
    assert allowance.synthesis_wall_s(longest_step_s=30) == 270
    # Run 5's 226-second step: the window opens 452 s before the limit, at 22.5 minutes, not 25.5.
    assert allowance.synthesis_wall_s(longest_step_s=226) == WALL_RESERVE_STEPS * 226
    assert allowance.synthesis_due(steps=10, elapsed_s=1347, longest_step_s=226) is None
    assert allowance.synthesis_due(steps=10, elapsed_s=1348, longest_step_s=226) == "wall_time"
    # Never more than half the turn: a step that long is the engine's whole pace.
    assert allowance.synthesis_wall_s(longest_step_s=2_000) == 900
    # Unchanged without a measured step, and for a small allowance.
    assert allowance.synthesis_wall_s() == 270
    assert Allowance(steps=10, wall_s=60, usd=None).synthesis_wall_s() == 30
    assert MIN_SYNTHESIS_WALL_S == 120


async def test_wp81_syn_2_a_slow_step_opens_the_window_earlier_on_a_running_turn(caplog: pytest.LogCaptureFixture) -> None:
    clock = ManualClock()
    ticks = Ticks(clock)
    world = World(clock=clock, sleep=ticks.sleep)
    run = world.run()
    run = dataclasses.replace(run, profile=dataclasses.replace(run.profile, limits=RunLimits(max_wall_s=1800)))
    with caplog.at_level(logging.WARNING):
        runner = asyncio.ensure_future(world.controller.run_turn(run, task(0, "hold\nwrite a.md x\nhold\nsay never")))
        # The first step takes 400 seconds of model time.
        await asyncio.wait_for(world.gateway.holding.wait(), 5)
        await ticks.parked()
        _ = clock.advance(400)
        world.gateway.release()
        await asyncio.sleep(0.05)
        await asyncio.wait_for(world.gateway.holding.wait(), 5)
        await ticks.parked()
        # 1,001 s in: past the 800-second reserve two such steps need, short of the 270 s the share keeps.
        await ticks.advance(601)
        synthesis = [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.turn.synthesis"]
        assert [r.harness["reasonCode"] for r in synthesis] == ["synthesis_wall_time"]  # type: ignore[attr-defined]
        _ = clock.advance(800)
        world.gateway.release()
        outcome = await asyncio.wait_for(runner, 5)
    assert outcome.reason in ("limit", "completed")
