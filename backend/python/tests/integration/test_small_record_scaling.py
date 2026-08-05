"""Integration test: 2,000 synthetic small records (the Jira/Confluence
``application/blocks`` shape — thousands of millisecond-scale index calls)
driving a real ``ResourceGovernor`` + ``AdmissionGate`` for ``Pool.INDEX``
under an idle-CPU, ample-memory probe (plan section 9, Phase 7).

This is the end-to-end companion to the two regression defects named in plan
section 1 and exercised in isolation by
``tests/unit/services/resource_governor/test_policy.py``
(``test_index_grows_on_idle_cpu_high_demand_confluence_case``) and
``tests/unit/services/resource_governor/test_demand_accounting.py``:

- **Aliasing:** demand must be proven by accumulated ``permit_seconds`` /
  ``blocked_acquires`` across the interval, not a point-sampled ``in_use``
  that would read near-zero between millisecond-scale holds.
- **CPU-derived cap:** ``INDEX``'s growth target is the operator ceiling,
  not a ``cpu_quota`` expression, so an idle-CPU host must still ramp INDEX
  concurrency up when downstream (real) work is I/O-bound.

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
    GROW_CONFIRM_SAMPLES,
    SAMPLE_INTERVAL_SECONDS,
)
from tests.integration.resource_governor_helpers import (
    ManualClock,
    ScriptedProbe,
    cancel_all,
    make_snapshot,
)

NUM_RECORDS = 2000
RECORD_COST_SECONDS = 0.02  # a "thousands of milliseconds" block-shaped index call
NUM_SAMPLES = 25
REAL_SECONDS_BETWEEN_SAMPLES = 0.1
FLOOR_LIMIT = 2
# Total simulated work (NUM_RECORDS * RECORD_COST_SECONDS = 40s) comfortably
# exceeds what even INDEX_CEILING concurrency could drain within this test's
# real wall-clock budget (NUM_SAMPLES * REAL_SECONDS_BETWEEN_SAMPLES = 2.5s
# real time, ceiling * that = 20 CPU-seconds worth) — so the backlog never
# runs dry mid-test and every sampled interval stays demand-saturated,
# regardless of how fast the limit ramps. Without this margin, throughput
# comparisons below flake under CI load once growth drains the backlog
# faster than real time advances.
INDEX_CEILING = 6  # derived from cpu_quota=2.0 → parse_ceiling=2 → index=2*3=6


async def _index_one_record(gate, cost_seconds: float) -> None:
    # timeout=None: this test only cares about the admission/throughput
    # curve, not the gate's own timeout mechanics (see
    # resource_governor_helpers.ManualClock's docstring for why a finite
    # timeout is unsafe to hold across a manually-advanced clock).
    async with gate.slot(cost=1, timeout=None) as admitted:
        assert admitted
        await asyncio.sleep(cost_seconds)


@pytest.mark.asyncio
class TestSmallRecordScaling:
    async def test_index_limit_climbs_and_throughput_rises_under_idle_cpu_ample_memory(self) -> None:
        clock = ManualClock()
        # A small (2 vCPU) but idle host: cpu_quota is deliberately low so
        # a target still derived from cpu_quota (the regression being
        # guarded against) would cap growth at ~2 — proving INDEX grows
        # past that is proof the cap is gone, not just that growth happens.
        probe = ScriptedProbe([make_snapshot(mem_pressure=0.05, cpu_quota=2.0, cpu_utilisation=0.02)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.integration.small_record_scaling"),
            probe=probe,
            sample_interval=SAMPLE_INTERVAL_SECONDS,
            clock=clock,
        )
        index_gate = governor.gate(Pool.INDEX)
        assert index_gate.limit == FLOOR_LIMIT  # warm-start floor (derived ceiling)

        records = [
            asyncio.create_task(_index_one_record(index_gate, RECORD_COST_SECONDS))
            for _ in range(NUM_RECORDS)
        ]

        # (limit that was active during the interval, completions drained
        # from that same interval) — paired per-iteration so there is no
        # off-by-one ambiguity about which limit produced which throughput.
        history: list[tuple[int, int]] = []
        try:
            for _ in range(NUM_SAMPLES):
                limit_during_interval = index_gate.limit
                # Real wall-clock sleep: lets the currently-admitted batch of
                # records actually run and release, generating the
                # permit_seconds/completions the next sample will drain.
                await asyncio.sleep(REAL_SECONDS_BETWEEN_SAMPLES)
                clock.now += SAMPLE_INTERVAL_SECONDS
                await governor._sample_once()
                completions = governor.stats()["demand"]["index"]["completions"]
                history.append((limit_during_interval, completions))
        finally:
            await cancel_all(records)

        # Growth cannot start before GROW_CONFIRM_SAMPLES consecutive
        # healthy+demanding samples (plan section 4), regardless of demand.
        assert all(limit == FLOOR_LIMIT for limit, _ in history[:GROW_CONFIRM_SAMPLES])

        peak_limit = max(limit for limit, _ in history)
        assert peak_limit > FLOOR_LIMIT, "INDEX must climb from the floor once demand is proven over several intervals"
        assert peak_limit > probe.snapshots[-1].cpu_quota, (
            "INDEX must grow past cpu_quota — its target is the ceiling, not a CPU-derived expression"
        )

        # Throughput rose alongside concurrency: mean completions/interval
        # once the limit had grown must exceed the floor-limit baseline,
        # even though every individual record costs the same fixed amount.
        floor_completions = [c for limit, c in history if limit == FLOOR_LIMIT]
        grown_completions = [c for limit, c in history if limit > FLOOR_LIMIT]
        assert grown_completions, "limit must have grown for at least one sampled interval"
        floor_mean = sum(floor_completions) / len(floor_completions)
        grown_mean = sum(grown_completions) / len(grown_completions)
        assert grown_mean > floor_mean, (
            "mean completions/interval after growth must exceed the floor-limit baseline "
            f"(floor_mean={floor_mean:.1f}, grown_mean={grown_mean:.1f})"
        )
