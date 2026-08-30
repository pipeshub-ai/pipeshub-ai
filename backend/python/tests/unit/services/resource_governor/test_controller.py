from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import Pool, ResourceSnapshot
from app.services.resource_governor.policy import (
    HEAVY_START_INTERVAL_SECONDS,
    MEM_HARD,
    MEM_SOFT,
    floor_for,
    start_rate_limiter_params,
)


class ScriptedProbe:
    """Replays a fixed sequence of snapshots, one per call, holding the
    last one once exhausted."""

    def __init__(self, snapshots: list[ResourceSnapshot]) -> None:
        self._snapshots = snapshots
        self._index = 0

    def snapshot(self) -> ResourceSnapshot:
        snap = self._snapshots[min(self._index, len(self._snapshots) - 1)]
        self._index += 1
        return snap


def _snap(
    mem_pressure_working_set_gb: float,
    mem_limit_gb: float = 4.0,
    cpu_utilisation: float = 0.1,
    cpu_quota: float = 8.0,
) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=cpu_quota,
        cpu_utilisation=cpu_utilisation,
        cpu_throttled_ratio=0.0,
        cpu_pressure=0.0,
        mem_limit_bytes=int(mem_limit_gb * 1024 ** 3),
        mem_working_set_bytes=int(mem_pressure_working_set_gb * 1024 ** 3),
        source="scripted",
    )


class ManualClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


async def _hold_gate(gate) -> None:
    async with gate.slot(timeout=5.0):
        await asyncio.sleep(3600)  # always cancelled well before this fires


class TestExplicitIndexCapReporting:
    """An operator-set MAX_CONCURRENT_INDEXING that the per-tier split cannot
    express exactly must say so, not deviate in silence."""

    def _governor(self, caplog: pytest.LogCaptureFixture, env_index: int) -> ResourceGovernor:
        with caplog.at_level(logging.WARNING, logger="test.index_cap"):
            return ResourceGovernor(
                logger=logging.getLogger("test.index_cap"),
                probe=ScriptedProbe([_snap(mem_pressure_working_set_gb=0.5)]),
                sample_interval=1.0,
                clock=ManualClock(),
                env_index=env_index,
            )

    def test_a_total_of_one_is_honoured_exactly_by_collapsing_the_split(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The cap is a hard aggregate: one record in flight means one, not
        one per tier. Two tiers each floored at 1 cannot express that, so the
        light tier collapses to zero and everything routes to heavy."""
        governor = self._governor(caplog, 1)

        assert governor.ceilings.index == 1
        assert governor.ceilings.index_heavy == 1
        assert governor.ceilings.index_light == 0
        assert "leaves no room to split the in-flight budget by tier" in caplog.text

    def test_a_total_the_split_can_express_keeps_both_tiers_and_is_silent(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        governor = self._governor(caplog, 2)

        assert governor.ceilings.index == 2
        assert governor.ceilings.index_light > 0
        assert "MAX_CONCURRENT_INDEXING" not in caplog.text


@pytest.mark.asyncio
class TestResourceGovernorController:
    async def test_shrink_then_ramp_with_hysteresis(self) -> None:
        """Drives ``_sample_once`` directly (rather than the background
        ``run()`` loop) so the test controls exactly when each sample
        happens, with no real-time sleeping or scheduling races."""
        clock = ManualClock()
        probe = ScriptedProbe([_snap(mem_pressure_working_set_gb=0.5)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.governor"),
            probe=probe,
            sample_interval=1.0,
            clock=clock,
        )
        # LIGHT_PARSE has no start-rate limiter (plan section 4, "Light
        # parses are never rate-limited"), which keeps this test's demand
        # accounting simple: every holder that fits under the limit is
        # admitted immediately, and the rest count as blocked_acquires.
        # env_parse/env_index are left as None (derived) so the ceiling
        # comes from the CPU quota, leaving room above the warm-start floor
        # for the ramp to demonstrate.
        gate = governor.gate(Pool.LIGHT_PARSE)
        light_floor = floor_for(Pool.LIGHT_PARSE, governor.ceilings.light)
        assert gate.limit == light_floor
        assert light_floor < governor.ceilings.light

        # Hard pressure -> halve (already at floor, stays clamped at floor).
        probe._snapshots = [_snap(mem_pressure_working_set_gb=3.6)]  # 0.9 of 4GiB
        clock.now += 1.0
        await governor._sample_once()
        assert gate.limit == light_floor

        # Healthy again, with proven demand each interval -> ramps up by
        # exactly one permit per sample once past the confirm window. Clear
        # the incident cooldown from the hard-pressure sample above first
        # (growth is deliberately frozen for INCIDENT_COOLDOWN_SECONDS).
        probe._snapshots = [_snap(mem_pressure_working_set_gb=0.5)]
        clock.now += 61.0
        for _ in range(6):
            holders = [
                asyncio.create_task(_hold_gate(gate))
                for _ in range(governor.ceilings.light + 4)
            ]
            await asyncio.sleep(0)  # let holders that fit actually acquire
            clock.now += 1.0
            await governor._sample_once()
            for holder in holders:
                holder.cancel()
            for holder in holders:
                with contextlib.suppress(asyncio.CancelledError):
                    await holder

        assert gate.limit > light_floor

    async def test_report_memory_incident_halves_immediately_without_waiting_for_sample(self) -> None:
        clock = ManualClock()
        probe = ScriptedProbe([_snap(mem_pressure_working_set_gb=0.5)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.governor"),
            env_parse=8,
            env_index=24,
            probe=probe,
            sample_interval=100.0,  # long enough that the sample loop can't be what caused the halving
            jitter=0.0,
            clock=clock,
        )
        heavy_gate = governor.gate(Pool.HEAVY_PARSE)

        # Grow heavy_parse's limit directly via the registry to simulate an
        # earlier ramp-up, so halving is observable.
        governor._registry.set(Pool.HEAVY_PARSE, 8)
        before_heavy = heavy_gate.limit

        governor.report_memory_incident("synthetic OOM-adjacent event")

        assert heavy_gate.limit == max(
            floor_for(Pool.HEAVY_PARSE, governor.ceilings.heavy), before_heavy // 2
        )

    async def test_start_rate_limiters_scale_with_a_high_ceiling(self) -> None:
        """Regression guard: a large resolved ceiling must raise how fast
        HEAVY_PARSE admits new work, not leave it throttled at the fixed
        ~0.5/s default forever (the root cause of "5-10 docs at a time
        regardless of MAX_CONCURRENT_*"). Driven by a CPU-rich host rather
        than by MAX_CONCURRENT_PARSING, which can only cap the CPU-derived
        ceiling, never raise it."""
        probe = ScriptedProbe([_snap(mem_pressure_working_set_gb=0.5, cpu_quota=64.0)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.governor"),
            probe=probe,
        )
        heavy_gate = governor.gate(Pool.HEAVY_PARSE)

        expected_heavy_interval, expected_heavy_capacity = start_rate_limiter_params(
            governor.ceilings.heavy
        )

        assert heavy_gate._rate_limiter._interval == expected_heavy_interval
        assert heavy_gate._rate_limiter._capacity == expected_heavy_capacity
        # Sustained rate must be well above the old fixed default now.
        assert 1.0 / heavy_gate._rate_limiter._interval > 1.0 / HEAVY_START_INTERVAL_SECONDS

    async def test_start_rate_limiters_keep_conservative_default_for_small_derived_ceiling(self) -> None:
        """Small/derived ceilings (no explicit env) must not regress — the
        burst smoother stays at its original conservative rate."""
        probe = ScriptedProbe([_snap(mem_pressure_working_set_gb=0.5, cpu_utilisation=0.1)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.governor"),
            probe=probe,
        )
        heavy_gate = governor.gate(Pool.HEAVY_PARSE)
        assert heavy_gate._rate_limiter._interval == HEAVY_START_INTERVAL_SECONDS

    async def test_rate_limited_acquires_are_tracked_and_reset_by_drain_demand(self) -> None:
        """Denials purely from the start-rate limiter must be counted
        separately from ordinary capacity-blocked acquires, and reset each
        interval (like the other PoolDemand accumulators)."""
        probe = ScriptedProbe([_snap(mem_pressure_working_set_gb=0.5)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.governor"),
            probe=probe,
        )
        gate = governor.gate(Pool.HEAVY_PARSE)
        # Exhaust the rate limiter's burst capacity, then force a denial
        # that has nothing to do with the concurrency limit (limit is high).
        governor._registry.set(Pool.HEAVY_PARSE, 100)
        while gate._rate_limiter.try_consume():
            pass
        assert gate._try_admit(1) is False

        demand = gate.drain_demand()
        assert demand.rate_limited_acquires >= 1

        # A second drain with no further denials reports zero.
        demand_again = gate.drain_demand()
        assert demand_again.rate_limited_acquires == 0

    async def test_stats_shape(self) -> None:
        probe = ScriptedProbe([_snap(mem_pressure_working_set_gb=1.0)])
        governor = ResourceGovernor(
            logger=logging.getLogger("test.governor"),
            env_parse=4,
            env_index=8,
            probe=probe,
        )
        stats = governor.stats()

        assert set(stats.keys()) == {
            "probe_source", "cpu_quota", "cpu_utilisation", "cpu_pressure",
            "cpu_throttled_ratio", "mem_pressure", "mem_limit_bytes",
            "mem_usable_bytes", "mem_working_set_raw_bytes", "mem_baseline_bytes",
            "worker_count", "ceilings", "limits", "in_use", "demand",
            "mem_pressure_raw",
        }
        assert set(stats["limits"].keys()) == {pool.value for pool in Pool}
        assert set(stats["ceilings"].keys()) == {
            "heavy_parse", "light_parse", "index", "index_heavy", "index_light",
        }
        # "index" stays in the payload as the total operators reason about
        # (and what MAX_CONCURRENT_INDEXING caps); the per-tier figures say
        # which budget a record can actually draw on.
        assert stats["ceilings"]["index"] == (
            stats["ceilings"]["index_heavy"] + stats["ceilings"]["index_light"]
        )
        assert set(stats["demand"][Pool.HEAVY_PARSE.value].keys()) == {
            "utilisation", "blocked_acquires", "completions", "rate_limited_acquires",
        }


class TestControllerConstants:
    def test_mem_soft_below_mem_hard(self) -> None:
        assert MEM_SOFT < MEM_HARD
