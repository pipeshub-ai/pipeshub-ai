"""Several indexing workers hold heavy parse together while sandboxes wait.

Each worker's own heavy limit stops at its floor, so four workers would keep
four floors' worth of heavy parses running through a yield. While the yield
or this worker's holdoff lasts, the heavy-parse slots the workers share are
held to the floor one worker alone would have; afterwards they are back to
what free memory holds. One worker, or no shared slots, is unchanged.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.feedback import DownstreamFeedback
from app.services.resource_governor.models import Pool
from app.services.resource_governor.policy import floor_for
from app.services.resource_governor.yield_signal import YieldState
from tests.unit.services.resource_governor.test_controller import (
    FakeYieldSignal,
    ManualClock,
    ScriptedProbe,
    _snap,
)

if TYPE_CHECKING:
    from pathlib import Path


def _governor(tmp_path: Path, signal: FakeYieldSignal | None, clock: ManualClock, workers: int = 4) -> ResourceGovernor:
    return ResourceGovernor(
        logger=logging.getLogger("test.yield.heavy"),
        # Plenty of free memory in the indexing container: only the yield holds heavy parse back.
        probe=ScriptedProbe([_snap(mem_pressure_working_set_gb=2.0, mem_limit_gb=64.0, cpu_quota=16.0)]),
        feedback=DownstreamFeedback(),
        sample_interval=15.0,
        clock=clock,
        worker_count=workers,
        heavy_parse_slots_dir=tmp_path / "slots",
        yield_signal=signal,  # type: ignore[arg-type]
    )


def _take_all(governor: ResourceGovernor) -> int:
    holds = []
    while (hold := governor.try_acquire_heavy_parse_slots()) is not None and len(holds) < 64:
        holds.append(hold)
    for hold in holds:
        hold.release()
    return len(holds)


async def test_the_workers_hold_to_one_floor_while_yielding_then_recover(tmp_path: Path) -> None:
    clock = ManualClock()
    signal = FakeYieldSignal(active=False)
    governor = _governor(tmp_path, signal, clock)
    try:
        await governor._sample_once()
        fleet_heavy = governor.fleet_ceilings.heavy
        free = _take_all(governor)
        floor = floor_for(Pool.HEAVY_PARSE, fleet_heavy)
        assert free > floor

        signal.state = YieldState(active=True, since=0.0)
        clock.now += 15.0
        await governor._sample_once()
        assert _take_all(governor) == floor

        # The yield ends: the cap stays through the holdoff, then lifts.
        signal.state = YieldState()
        held_for = 0
        for _ in range(10):
            clock.now += 15.0
            await governor._sample_once()
            if _take_all(governor) == floor:
                held_for += 1
            else:
                break
        assert held_for == 3  # the 4-sample holdoff counts down from this sample
        assert _take_all(governor) == free
    finally:
        governor.close()


async def test_one_worker_is_unchanged(tmp_path: Path) -> None:
    clock = ManualClock()
    governor = _governor(tmp_path, FakeYieldSignal(active=True), clock, workers=1)
    try:
        await governor._sample_once()
        hold = governor.try_acquire_heavy_parse_slots()
        assert hold is not None and hold.held_before == 0
    finally:
        governor.close()


async def test_without_a_signal_the_shared_slots_are_unchanged(tmp_path: Path) -> None:
    clock = ManualClock()
    governor = _governor(tmp_path, None, clock)
    try:
        await governor._sample_once()
        assert _take_all(governor) > floor_for(Pool.HEAVY_PARSE, governor.fleet_ceilings.heavy)
    finally:
        governor.close()
