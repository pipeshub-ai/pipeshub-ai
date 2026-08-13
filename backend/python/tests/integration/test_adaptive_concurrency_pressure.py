"""Integration test: a real ``ResourceGovernor`` driving real ``AdmissionGate``
objects through a memory-pressure incident and recovery (plan section 9,
Phase 7 — "real governor plus a scripted probe").

Only the resource probe is faked (a scripted sequence of ``ResourceSnapshot``s)
and the clock is manually advanced so the test controls exactly when each
sample happens with no real-time sleeping or scheduling races — the governor,
registry, and gates are the real production classes. This is deliberately a
higher-level companion to ``tests/unit/services/resource_governor/
test_controller.py``: that file white-box-tests the control law in isolation;
this one exercises the same shrink/recover cycle through the public
gate-acquisition API two independent pools actually use.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import Pool, ResourceSnapshot
from app.services.resource_governor.policy import (
    INCIDENT_COOLDOWN_SECONDS,
    SAMPLE_INTERVAL_SECONDS,
    floor_for,
)
from tests.integration.resource_governor_helpers import (
    ManualClock,
    ScriptedProbe,
    cancel_all,
    make_snapshot,
)


async def _hold_gate(gate, cost: int = 1, timeout: float | None = 5.0) -> None:
    async with gate.slot(cost=cost, timeout=timeout):
        await asyncio.sleep(3600)  # always cancelled by the test well before this fires


@pytest.mark.asyncio
class TestAdaptiveConcurrencyPressure:
    async def test_hard_pressure_blocks_new_heavy_while_light_keeps_draining_then_recovers(self) -> None:
        clock = ManualClock()
        probe = ScriptedProbe([make_snapshot(mem_pressure=0.1)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.integration.pressure"),
            probe=probe,
            sample_interval=1.0,
            clock=clock,
        )
        heavy_gate = governor.gate(Pool.HEAVY_PARSE)
        light_gate = governor.gate(Pool.LIGHT_PARSE)
        heavy_floor = floor_for(Pool.HEAVY_PARSE, governor.ceilings.heavy)
        light_floor = floor_for(Pool.LIGHT_PARSE, governor.ceilings.light)
        assert heavy_gate.limit == heavy_floor  # warm-start floor (derived ceiling)
        assert light_gate.limit == light_floor

        # Saturate heavy at its current limit; light gets only one holder, so
        # it still has headroom under its own limit.
        heavy_holders = [
            asyncio.create_task(_hold_gate(heavy_gate)) for _ in range(heavy_floor)
        ]
        light_holder = asyncio.create_task(_hold_gate(light_gate))
        await asyncio.sleep(0)  # let the admitted holders actually acquire
        assert heavy_gate.in_use == heavy_floor
        assert light_gate.in_use == 1

        # ── Hard pressure hits ────────────────────────────────────────────
        probe.snapshots = [make_snapshot(mem_pressure=0.9)]  # >= MEM_HARD (0.85)
        clock.now += 1.0
        await governor._sample_once()

        # Both pools are independently re-evaluated against the same
        # pressure signal; already at the floor, they stay clamped there
        # rather than going lower.
        assert heavy_gate.limit == heavy_floor
        assert light_gate.limit == light_floor

        # New heavy admission blocks: in_use == limit, and
        # in_use != 0, so it must wait rather than being admitted alongside
        # the existing holders. AdmissionGate's own timeout deadline is
        # computed from the (manual) clock, which never advances on its own,
        # so waiting out a real timeout here would hang forever — instead,
        # prove non-admission by observing the acquire is still pending
        # after yielding once, then cancel it (equivalent, and exercised
        # more thoroughly for the manual-clock-safe case in
        # tests/unit/services/resource_governor/test_gate.py).
        blocked_task = asyncio.create_task(heavy_gate.acquire(timeout=5.0))
        await asyncio.sleep(0)
        assert not blocked_task.done(), "heavy_parse must not admit a new request while saturated"
        blocked_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await blocked_task

        # New light admission is NOT blocked: in_use(1) + cost(1) <= limit.
        admitted = await light_gate.acquire(timeout=0.1)
        assert admitted is True
        light_gate.release()

        # The pre-existing light holder was never revoked by the pressure
        # event (plan principle: shrinking/holding a limit never preempts
        # in-flight permits) — it "drains" normally on its own schedule.
        light_holder.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await light_holder
        assert light_gate.in_use == 0

        await cancel_all(heavy_holders)
        assert heavy_gate.in_use == 0

        # ── Pressure clears; limits recover (climb back above the floor) ──
        probe.snapshots = [make_snapshot(mem_pressure=0.1)]
        clock.now += INCIDENT_COOLDOWN_SECONDS + 1.0  # clear the incident cooldown

        for _ in range(6):
            holders = [asyncio.create_task(_hold_gate(heavy_gate)) for _ in range(8)]
            await asyncio.sleep(0)  # let holders that fit actually acquire
            clock.now += 1.0
            await governor._sample_once()
            await cancel_all(holders)

        assert heavy_gate.limit > heavy_floor, (
            "heavy_parse ceiling must recover once memory pressure clears"
        )

    async def test_exponential_recovery_reaches_near_ceiling_within_a_dozen_intervals(self) -> None:
        """Plan section 4 (TCP-slow-start growth): recovering from a
        MEM_HARD halve must reach back near a large explicit ceiling in
        O(log2(ceiling)) intervals, not O(ceiling) — the difference between
        ~60s and ~42 minutes for a 500-permit gap at 5s/interval (see plan
        "Expected Impact"). ``test_hard_pressure_blocks_new_heavy_while_
        light_keeps_draining_then_recovers`` above only proves recovery
        *happens*; this proves it's fast enough to matter operationally.
        """
        clock = ManualClock()
        # LIGHT_PARSE, because it is the widest *adapted* pool: the index
        # pools hold their ceiling for the life of the process, and heavy's
        # target is additionally clamped by heavy_memory_cap, which would cap
        # this at ~20 permits on any believable mem_limit. A 334-CPU host is
        # what a 1000-permit light ceiling (3/CPU, capped by
        # MAX_CONCURRENT_PARSING) implies — the cap can lower that
        # derivation, never raise it.
        def snapshot(mem_pressure: float) -> ResourceSnapshot:
            return make_snapshot(mem_pressure, cpu_quota=334.0)

        probe = ScriptedProbe([snapshot(0.1)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.integration.pressure.exponential_recovery"),
            env_parse=1000,
            probe=probe,
            sample_interval=SAMPLE_INTERVAL_SECONDS,
            clock=clock,
        )
        assert governor.ceilings.light == 1000
        light_gate = governor.gate(Pool.LIGHT_PARSE)
        # Warm start is the floor (half the ceiling for light), so put the
        # pool where a finished ramp would have left it — the halve below
        # needs a large limit to open the 500-permit gap this test measures.
        governor._registry.set(Pool.LIGHT_PARSE, 1000)
        assert light_gate.limit == 1000

        probe.snapshots = [snapshot(0.9)]  # >= MEM_HARD
        clock.now += SAMPLE_INTERVAL_SECONDS
        await governor._sample_once()
        assert light_gate.limit == 500

        probe.snapshots = [snapshot(0.1)]
        clock.now += INCIDENT_COOLDOWN_SECONDS + SAMPLE_INTERVAL_SECONDS  # clear the incident cooldown

        # timeout=None: a blocked holder's finite-timeout deadline is
        # measured against this same manual clock (see ManualClock's
        # docstring) — a single SAMPLE_INTERVAL_SECONDS jump would blow
        # past any finite deadline immediately and the holder would give up
        # for good instead of staying queued for the next growth step.
        holders = [asyncio.create_task(_hold_gate(light_gate, cost=1, timeout=None)) for _ in range(1000)]
        try:
            # 12 intervals (60s simulated) is 3 to confirm-healthy plus 9
            # doubling grows (+1,+2,+4,...,+256, clamped at the 1000
            # ceiling) — comfortably enough to fully recover. The old fixed
            # +1/interval step would need ~500 intervals (~42 minutes) to
            # close the same gap.
            for _ in range(12):
                await asyncio.sleep(0)  # let newly-freed room admit more holders
                clock.now += SAMPLE_INTERVAL_SECONDS
                await governor._sample_once()
        finally:
            await cancel_all(holders)

        assert light_gate.limit == 1000, (
            f"exponential recovery should fully close a 500-permit gap within "
            f"12 intervals (60s), got {light_gate.limit}"
        )

    async def test_limits_never_go_below_floor_under_repeated_hard_pressure(self) -> None:
        """Corner case: repeated hard-pressure samples halve toward zero but
        must clamp at the floor rather than starving the pool completely."""
        clock = ManualClock()
        probe = ScriptedProbe([make_snapshot(mem_pressure=0.1)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.integration.pressure.floor"),
            env_parse=8,
            env_index=24,
            probe=probe,
            sample_interval=1.0,
            clock=clock,
        )
        heavy_gate = governor.gate(Pool.HEAVY_PARSE)
        # Warm start is the floor, so lift the pool to its ceiling first —
        # otherwise the repeated halving below has nowhere to fall from and
        # the assertion passes vacuously.
        governor._registry.set(Pool.HEAVY_PARSE, 8)

        probe.snapshots = [make_snapshot(mem_pressure=0.95)]
        for _ in range(10):
            clock.now += 1.0
            await governor._sample_once()

        assert heavy_gate.limit == 2  # never drops below floor_for(HEAVY_PARSE, ceiling=8)
