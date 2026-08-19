"""Integration test: 2,000 synthetic small records (the Jira/Confluence
``application/blocks`` shape — thousands of millisecond-scale parse calls)
driving a real ``ResourceGovernor`` + ``AdmissionGate`` for
``Pool.LIGHT_PARSE`` under an idle-CPU, ample-memory probe (plan section 9,
Phase 7).

This is the end-to-end companion to the two regression defects named in plan
section 1 and exercised in isolation by
``tests/unit/services/resource_governor/test_policy.py`` and
``tests/unit/services/resource_governor/test_demand_accounting.py``:

- **Aliasing:** demand must be proven by accumulated ``permit_seconds`` /
  ``blocked_acquires`` across the interval, not a point-sampled ``in_use``
  that would read near-zero between millisecond-scale holds.
- **CPU-derived cap:** the growth target is the pool's ceiling, not a
  ``cpu_quota`` expression, so an idle-CPU host must still ramp concurrency
  up when the real work is I/O-bound.

``Pool.INDEX`` is asserted here too but only to pin the opposite property:
pipeline width is fixed at its ceiling and never ramps.

Only the probe and the controller's clock are faked; the 2,000 tasks
actually acquire/hold/release a real ``AdmissionGate`` and really
``asyncio.sleep`` for their simulated per-record cost, so throughput moves
only because concurrency actually increased.
"""
from __future__ import annotations

import asyncio
import logging

import pytest

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import Pool
from app.services.resource_governor.policy import (
    LIGHT_GROW_CONFIRM_SAMPLES,
    SAMPLE_INTERVAL_SECONDS,
    floor_for,
)
from tests.integration.resource_governor_helpers import (
    ManualClock,
    ScriptedProbe,
    cancel_all,
    make_snapshot,
)

NUM_RECORDS = 2000
RECORD_COST_SECONDS = 0.02  # a "thousands of milliseconds" block-shaped parse call
NUM_SAMPLES = 25
REAL_SECONDS_BETWEEN_SAMPLES = 0.1
# Total simulated work (NUM_RECORDS * RECORD_COST_SECONDS = 40s) comfortably
# exceeds what even LIGHT_CEILING concurrency could drain within this test's
# real wall-clock budget (NUM_SAMPLES * REAL_SECONDS_BETWEEN_SAMPLES = 2.5s
# real time) — so the backlog never runs dry mid-test and every sampled
# interval stays demand-saturated, regardless of how fast the limit ramps.
CPU_QUOTA = 2.0


async def _parse_one_record(gate, cost_seconds: float) -> None:
    # timeout=None: this test only cares about the admission/throughput
    # curve, not the gate's own timeout mechanics (see
    # resource_governor_helpers.ManualClock's docstring for why a finite
    # timeout is unsafe to hold across a manually-advanced clock).
    async with gate.slot(cost=1, timeout=None) as admitted:
        assert admitted
        await asyncio.sleep(cost_seconds)


@pytest.mark.asyncio
class TestSmallRecordScaling:
    async def test_light_parse_limit_climbs_and_throughput_rises_under_idle_cpu_ample_memory(self) -> None:
        clock = ManualClock()
        # A small (2 vCPU) but idle host: cpu_quota is deliberately low so
        # a target still derived from cpu_quota (the regression being
        # guarded against) would cap growth at ~2 — proving the pool grows
        # past that is proof the cap is gone, not just that growth happens.
        probe = ScriptedProbe([make_snapshot(mem_pressure=0.05, cpu_quota=CPU_QUOTA, cpu_utilisation=0.02)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.integration.small_record_scaling"),
            probe=probe,
            sample_interval=SAMPLE_INTERVAL_SECONDS,
            clock=clock,
        )
        light_gate = governor.gate(Pool.LIGHT_PARSE)
        floor_limit = floor_for(Pool.LIGHT_PARSE, governor.ceilings.light)
        light_ceiling = governor.ceilings.light
        assert light_gate.limit == floor_limit  # warm-start floor (derived ceiling)
        # Pipeline width does not ramp at all — it is at its ceiling from
        # the first acquire, before any sample has run.
        assert governor.gate(Pool.INDEX).limit == governor.ceilings.index

        records = [
            asyncio.create_task(_parse_one_record(light_gate, RECORD_COST_SECONDS))
            for _ in range(NUM_RECORDS)
        ]

        # (limit that was active during the interval, completions drained
        # from that same interval) — paired per-iteration so there is no
        # off-by-one ambiguity about which limit produced which throughput.
        history: list[tuple[int, int]] = []
        try:
            for _ in range(NUM_SAMPLES):
                limit_during_interval = light_gate.limit
                # Real wall-clock sleep: lets the currently-admitted batch of
                # records actually run and release, generating the
                # permit_seconds/completions the next sample will drain.
                await asyncio.sleep(REAL_SECONDS_BETWEEN_SAMPLES)
                clock.now += SAMPLE_INTERVAL_SECONDS
                await governor._sample_once()
                completions = governor.stats()["demand"]["light_parse"]["completions"]
                history.append((limit_during_interval, completions))
        finally:
            await cancel_all(records)

        # Growth cannot start before LIGHT_GROW_CONFIRM_SAMPLES consecutive
        # healthy+demanding samples (plan section 4), regardless of demand.
        assert all(limit == floor_limit for limit, _ in history[:LIGHT_GROW_CONFIRM_SAMPLES])

        peak_limit = max(limit for limit, _ in history)
        assert peak_limit > floor_limit, "the pool must climb from its floor once demand is proven"
        assert peak_limit > probe.snapshots[-1].cpu_quota, (
            "growth must pass cpu_quota — the target is the ceiling, not a CPU-derived expression"
        )
        assert peak_limit <= light_ceiling, "growth must not pass the derived ceiling"

        # Throughput rose alongside concurrency: mean completions/interval
        # once the limit had grown must exceed the floor-limit baseline,
        # even though every individual record costs the same fixed amount.
        floor_completions = [c for limit, c in history if limit == floor_limit]
        grown_completions = [c for limit, c in history if limit > floor_limit]
        assert grown_completions, "limit must have grown for at least one sampled interval"
        floor_mean = sum(floor_completions) / len(floor_completions)
        grown_mean = sum(grown_completions) / len(grown_completions)
        assert grown_mean > floor_mean, (
            "mean completions/interval after growth must exceed the floor-limit baseline "
            f"(floor_mean={floor_mean:.1f}, grown_mean={grown_mean:.1f})"
        )
