from __future__ import annotations

import importlib
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app.services.resource_governor import policy as policy_mod
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Limits,
    Pool,
    PoolDemand,
    ResourceSnapshot,
)
from app.services.resource_governor.policy import (
    EMBEDDING_CPU_RESERVATION,
    GROW_CONFIRM_SAMPLES,
    HEAVY_START_BUCKET_CAPACITY,
    HEAVY_START_INTERVAL_SECONDS,
    HEAVY_START_RATE_CEILING_DIVISOR,
    INCIDENT_COOLDOWN_SECONDS,
    LIGHT_GROW_CONFIRM_SAMPLES,
    MEM_SOFT,
    SAMPLE_INTERVAL_SECONDS,
    floor_for,
    next_limits,
    resolve_ceilings,
    start_rate_limiter_params,
    warm_start_limits,
)

if TYPE_CHECKING:
    from collections.abc import Generator

INTERVAL = SAMPLE_INTERVAL_SECONDS


def _snapshot(
    *,
    cpu_quota: float = 4.0,
    cpu_utilisation: float | None = 0.1,
    cpu_throttled_ratio: float | None = 0.0,
    cpu_pressure: float | None = 0.0,
    mem_limit_bytes: int | None = 4 * 1024 ** 3,
    mem_working_set_bytes: int | None = 1 * 1024 ** 3,
    mem_working_set_raw_bytes: int | None = None,
    mem_baseline_bytes: int | None = None,
    source: str = "test",
) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=cpu_quota,
        cpu_utilisation=cpu_utilisation,
        cpu_throttled_ratio=cpu_throttled_ratio,
        cpu_pressure=cpu_pressure,
        mem_limit_bytes=mem_limit_bytes,
        mem_working_set_bytes=mem_working_set_bytes,
        mem_working_set_raw_bytes=mem_working_set_raw_bytes,
        mem_baseline_bytes=mem_baseline_bytes,
        source=source,
    )


def _no_demand() -> dict[Pool, PoolDemand]:
    return {pool: PoolDemand.empty() for pool in Pool}


def _saturated_demand(limit: int, interval: float = INTERVAL) -> dict[Pool, PoolDemand]:
    """Demand that clearly indicates every pool is under contention."""
    return {
        pool: PoolDemand(permit_seconds=limit * interval, blocked_acquires=5, completions=100)
        for pool in Pool
    }


class TestResolveCeilings:
    """Slot counts are a pure function of the CPU quota, capped by the two
    operator vars: heavy = 1/CPU, light = 10/CPU up to LIGHT_PARSE_MAX, and
    each index tier = 2x its own parse ceiling, clamped per tier. Memory is
    deliberately absent — it gates heavy and both index tiers per sample
    (``heavy_memory_cap`` / ``index_memory_cap``) rather than at startup."""

    def test_small_host_2cpu(self) -> None:
        snap = _snapshot(cpu_quota=2.0, mem_limit_bytes=4 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.heavy == 2
        assert ceilings.light == 20
        assert ceilings.index_heavy == 4  # ceil(2 * 2.0)
        assert ceilings.index_light == 40  # ceil(20 * 2.0)

    def test_medium_host_8cpu(self) -> None:
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=16 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.heavy == 8
        assert ceilings.light == 80
        assert ceilings.index_heavy == 16
        assert ceilings.index_light == 160

    def test_large_host_32cpu_scales_both_index_tiers(self) -> None:
        snap = _snapshot(cpu_quota=32.0, mem_limit_bytes=64 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.heavy == 32
        assert ceilings.light == policy_mod.LIGHT_PARSE_MAX  # 320 derived, capped
        assert ceilings.index_heavy == 64
        assert ceilings.index_light == policy_mod.INDEX_MAX_PER_TIER  # 512 derived, at cap

    def test_index_never_exceeds_its_absolute_maximum(self) -> None:
        """A very large host must not derive an in-flight budget nothing can
        drain: every admitted record holds a downloaded buffer, so the width
        is capped even when CPU says otherwise."""
        snap = _snapshot(cpu_quota=256.0, mem_limit_bytes=512 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.index_heavy == policy_mod.INDEX_MAX_PER_TIER
        assert ceilings.index_light == policy_mod.INDEX_MAX_PER_TIER

    def test_index_always_covers_both_parse_tiers(self) -> None:
        """A record holding a parse permit also holds an index permit, so an
        index ceiling below heavy+light would deadlock the parse pools
        against the pool above them."""
        for cpu_quota in (0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 48.0, 96.0):
            snap = _snapshot(cpu_quota=cpu_quota, mem_limit_bytes=16 * 1024 ** 3)
            ceilings = resolve_ceilings(snap, None, None, worker_count=1)
            assert ceilings.index_heavy >= ceilings.heavy, cpu_quota
            assert ceilings.index_light >= ceilings.light, cpu_quota

    def test_unknown_memory_changes_nothing(self) -> None:
        """Ceilings are CPU-only, so an unreadable cgroup memory limit is
        not a special case for them at all."""
        snap = _snapshot(cpu_quota=6.0, mem_limit_bytes=None, mem_working_set_bytes=None)
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.heavy == 6
        assert ceilings.light == 60

    def test_explicit_ceilings_honoured_including_one(self) -> None:
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=32 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, env_parse=1, env_index=1, worker_count=1)
        assert ceilings.heavy == 1
        assert ceilings.light == 1
        # A total of 1 is a hard aggregate, so the split collapses rather than
        # overshooting to 1+1. Light drops to zero and effective_index_tier
        # routes every record to heavy — at one record in flight there is no
        # tier fairness left to preserve anyway.
        assert ceilings.index_heavy == 1
        assert ceilings.index_light == 0
        assert ceilings.index == 1

    def test_an_index_max_below_the_tier_floor_is_still_honoured(self) -> None:
        """``_clamp(value, low, high)`` is ``max(low, min(high, value))``, so
        an explicit maximum below INDEX_MIN_PER_TIER would invert the bounds
        and return the *floor* — 8 per tier for an operator who asked for 1.
        The floor only exists to stop a tiny host serialising a tier; an
        explicit maximum outranks it."""
        snap = _snapshot(cpu_quota=16.0, mem_limit_bytes=64 * 1024 ** 3)
        with patch.object(policy_mod, "INDEX_MAX_PER_TIER", 1):
            ceilings = resolve_ceilings(snap, env_parse=None, env_index=None, worker_count=1)
        assert ceilings.index_heavy == 1
        assert ceilings.index_light == 1

    @pytest.mark.parametrize("index_max", [1, 3, 8, 64, 512])
    def test_no_tier_ever_exceeds_the_configured_index_max(self, index_max: int) -> None:
        snap = _snapshot(cpu_quota=16.0, mem_limit_bytes=64 * 1024 ** 3)
        with patch.object(policy_mod, "INDEX_MAX_PER_TIER", index_max):
            ceilings = resolve_ceilings(snap, env_parse=None, env_index=None, worker_count=1)
        assert ceilings.index_heavy <= index_max
        assert ceilings.index_light <= index_max

    def test_index_floors_are_equal_in_memory_not_in_count(self) -> None:
        """A flat floor of 2 is right for heavy records, which hold
        ~INDEX_HEAVY_WORKING_SET_GB each. Applying the same 2 to light records
        — already assumed to cost 7.5x less — throws away an order of
        magnitude of throughput while saving no memory at all. A memory-starved
        node must still turn over a useful number of the cheap ones.
        """
        heavy_floor = policy_mod.pressure_floor(Pool.INDEX_HEAVY, 32)
        light_floor = policy_mod.pressure_floor(Pool.INDEX_LIGHT, 512)

        assert light_floor > heavy_floor
        heavy_gb = heavy_floor * policy_mod.INDEX_HEAVY_WORKING_SET_GB
        light_gb = light_floor * policy_mod.INDEX_LIGHT_WORKING_SET_GB
        assert abs(heavy_gb - light_gb) < 0.05, (
            f"floors cost different memory: {heavy_gb:.2f} vs {light_gb:.2f} GB"
        )

    @pytest.mark.parametrize("ceiling", [1, 2, 4, 12, 46, 92, 512])
    def test_a_brake_floor_never_exceeds_the_warm_start_width(
        self, ceiling: int
    ) -> None:
        """Brakes only reduce. A floor above where the pool starts would let
        one *raise* a limit, turning the shrink path into a growth path."""
        for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
            assert policy_mod.pressure_floor(pool, ceiling) <= policy_mod.floor_for(
                pool, ceiling
            )
            assert policy_mod.pressure_floor(pool, ceiling) <= ceiling

    def test_a_collapsed_light_tier_survives_the_worker_division(self) -> None:
        """``max(1, x // workers)`` would resurrect a collapsed tier to 1 and
        put the total back over the cap on every multi-worker deployment."""
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=32 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, env_parse=1, env_index=1, worker_count=4)
        assert ceilings.index_light == 0
        assert ceilings.index == 1

    @pytest.mark.parametrize("env_index", [1, 2, 3, 4, 8, 16, 64])
    def test_the_explicit_total_is_never_exceeded(self, env_index: int) -> None:
        """The cap is an aggregate across tiers, at every value — the tier
        split decides who may claim the budget, never how large it is."""
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=32 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, env_parse=None, env_index=env_index, worker_count=1)
        assert ceilings.index <= env_index

    def test_explicit_parse_ceiling_caps_both_tiers(self) -> None:
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=32 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, env_parse=4, env_index=None, worker_count=1)
        assert ceilings.heavy == 4
        assert ceilings.light == 4  # 80 derived, capped by MAX_CONCURRENT_PARSING
        assert ceilings.index_heavy == 8  # ceil(4 * 2.0)
        assert ceilings.index_light == 8

    def test_explicit_ceilings_only_cap_and_never_raise(self) -> None:
        """MAX_CONCURRENT_* is a ``min`` against the derived value: a
        reckless 200 on a 4-CPU box must not admit 200 of anything."""
        snap = _snapshot(cpu_quota=4.0, mem_limit_bytes=8 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, env_parse=200, env_index=200, worker_count=1)
        assert ceilings.heavy == 4
        assert ceilings.light == 40
        assert ceilings.index == 88  # 8 + 80, well under the 200 asked for

    def test_sub_one_cpu_host_floors_at_one_not_zero(self) -> None:
        snap = _snapshot(cpu_quota=0.5, mem_limit_bytes=8 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.heavy == 1
        assert ceilings.light == 5  # floor(0.5 * 10) = 5
        assert ceilings.index_heavy == 2  # ceil(1 * 2.0)
        assert ceilings.index_light == 10  # ceil(5 * 2.0)

    def test_worker_count_divides_ceilings(self) -> None:
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=16 * 1024 ** 3)
        one_worker = resolve_ceilings(snap, None, None, worker_count=1)
        four_workers = resolve_ceilings(snap, None, None, worker_count=4)
        assert four_workers.heavy == max(1, one_worker.heavy // 4)
        assert four_workers.light == max(1, one_worker.light // 4)
        assert four_workers.index == max(1, one_worker.index // 4)

    def test_resident_memory_does_not_shrink_the_startup_ceiling(self) -> None:
        """The all-in-one container holds ~9 of 12 GiB before the first
        document arrives, but that is a startup condition, not a steady
        state: the ceiling stays CPU-sized and ``heavy_memory_cap`` holds
        the live limit down only while the memory really is unavailable."""
        snap = _snapshot(
            cpu_quota=16.0,
            mem_limit_bytes=12 * 1024 ** 3,
            mem_working_set_raw_bytes=9 * 1024 ** 3,
        )
        ceilings = resolve_ceilings(snap, None, None, worker_count=1)
        assert ceilings.heavy == 16
        assert policy_mod.heavy_memory_cap(snap) == 2  # (12 - 9) / 1.5


class TestEmbeddingCpuReservation:
    """``reserve_embedding_cpus`` holds CPU back for the co-located local
    embedding server before heavy-parse slots are derived, for the
    deployments that embed on local CPU (default / sentenceTransformers /
    huggingFace) in this same cgroup."""

    def test_reservation_comes_off_the_quota_before_heavy_slots_are_derived(self) -> None:
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=16 * 1024 ** 3)
        reserved = resolve_ceilings(snap, None, None, reserve_embedding_cpus=True)
        assert reserved.heavy == int(8.0 - EMBEDDING_CPU_RESERVATION)

    def test_off_by_default_so_a_hosted_embedding_api_keeps_every_core(self) -> None:
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=16 * 1024 ** 3)
        assert resolve_ceilings(snap, None, None).heavy == 8

    def test_light_budget_is_untouched(self) -> None:
        """A light parse is milliseconds of CPU on a few KB, so its ceiling
        is a runaway bound rather than a claim on cores — only heavy, which
        is CPU-bound end to end, gives ground to embedding. Index follows
        heavy down only through the headroom term, never on its own."""
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=16 * 1024 ** 3)
        derived = resolve_ceilings(snap, None, None)
        reserved = resolve_ceilings(snap, None, None, reserve_embedding_cpus=True)
        assert reserved.light == derived.light
        assert derived.index - reserved.index == int(
            (derived.heavy - reserved.heavy) * policy_mod.INDEX_HEADROOM
        )

    def test_reservation_cannot_flatten_heavy_on_a_small_host(self) -> None:
        """A flat 2-core reservation on a 4-core box left heavy with
        ceiling == floor == 2 — no range for the ramp, the demand check or
        the CPU brake to work in. Capping it as a share of the quota keeps
        the tier adaptive on small hosts."""
        snap = _snapshot(cpu_quota=4.0, mem_limit_bytes=8 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, None, None, reserve_embedding_cpus=True)
        assert ceilings.heavy == 3
        assert ceilings.heavy > floor_for(Pool.HEAVY_PARSE, ceilings.heavy)

    def test_reservation_is_taken_in_full_once_the_host_is_big_enough(self) -> None:
        snap = _snapshot(cpu_quota=16.0, mem_limit_bytes=32 * 1024 ** 3)
        reserved = resolve_ceilings(snap, None, None, reserve_embedding_cpus=True)
        assert reserved.heavy == int(16.0 - EMBEDDING_CPU_RESERVATION)

    def test_heavy_still_floors_at_one_when_the_reservation_covers_the_quota(self) -> None:
        """A 2-CPU container must still parse one document at a time,
        slowly, rather than stalling every PDF forever."""
        snap = _snapshot(cpu_quota=EMBEDDING_CPU_RESERVATION, mem_limit_bytes=4 * 1024 ** 3)
        assert resolve_ceilings(snap, None, None, reserve_embedding_cpus=True).heavy == 1

    def test_fully_ramped_heavy_never_climbs_past_the_reserved_ceiling(self) -> None:
        """The reservation has to hold at every point in the ramp, not just
        at startup: memory is generous and demand saturated here, so the
        only thing that can stop growth is the reserved ceiling itself."""
        snap = _snapshot(
            cpu_quota=8.0,
            mem_limit_bytes=64 * 1024 ** 3,
            mem_working_set_bytes=1 * 1024 ** 3,
        )
        ceilings = resolve_ceilings(snap, None, None, reserve_embedding_cpus=True)
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()

        now = 0.0
        for _ in range(40):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(
                limits, snap, ceilings, state, demand, now=now, interval=INTERVAL,
            )
            now += INTERVAL
            assert limits.get(Pool.HEAVY_PARSE) <= ceilings.heavy

        assert limits.get(Pool.HEAVY_PARSE) == ceilings.heavy  # ramp did reach it


class TestHeavyMemoryCap:
    def test_free_memory_uses_the_raw_working_set_not_the_adjusted_one(self) -> None:
        """A baseline-credited working set says nothing about how much of
        the cgroup is physically free — memory held by a co-located service
        is unavailable to a parse slot whoever it is attributed to."""
        snap = _snapshot(
            cpu_quota=16.0,
            mem_limit_bytes=12 * 1024 ** 3,
            mem_working_set_bytes=0,
            mem_working_set_raw_bytes=9 * 1024 ** 3,
            mem_baseline_bytes=9 * 1024 ** 3,
        )
        assert policy_mod.heavy_memory_cap(snap) == 2

    def test_unknown_memory_limit_yields_no_cap(self) -> None:
        snap = _snapshot(mem_limit_bytes=None, mem_working_set_bytes=None)
        assert policy_mod.heavy_memory_cap(snap) is None

    def test_exhausted_memory_still_permits_one_parse(self) -> None:
        """A cap of 0 would stall every PDF forever rather than slowly."""
        snap = _snapshot(
            mem_limit_bytes=4 * 1024 ** 3, mem_working_set_raw_bytes=4 * 1024 ** 3,
        )
        assert policy_mod.heavy_memory_cap(snap) == 1


class TestFloorFor:
    def test_count_pool_floor_is_two_unless_ceiling_smaller(self) -> None:
        assert floor_for(Pool.HEAVY_PARSE, 12) == 2
        assert floor_for(Pool.HEAVY_PARSE, 1) == 1

    def test_light_parse_starts_at_half_its_ceiling(self) -> None:
        assert floor_for(Pool.LIGHT_PARSE, 24) == 12

    def test_index_pool_floors_at_half_its_ceiling(self) -> None:
        """Half, not the full ceiling: a floor equal to the ceiling would
        leave the memory brake nothing to shrink into, and a floor of 2
        would reintroduce the near-serial startup that pinning this pool at
        its ceiling was originally meant to avoid."""
        for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
            assert floor_for(pool, 48) == 24
            assert floor_for(pool, 1) == 1
            assert floor_for(pool, 3) == 2

    def test_light_floor_never_exceeds_a_tiny_ceiling(self) -> None:
        assert floor_for(Pool.LIGHT_PARSE, 1) == 1
        assert floor_for(Pool.LIGHT_PARSE, 3) == 2


class TestWarmStartLimits:
    def test_adapted_pools_start_at_their_floor(self) -> None:
        ceilings = Ceilings(heavy=12, light=64, index_heavy=24, index_light=24)
        limits = warm_start_limits(ceilings)

        assert limits.get(Pool.HEAVY_PARSE) == floor_for(Pool.HEAVY_PARSE, ceilings.heavy)
        assert limits.get(Pool.LIGHT_PARSE) == floor_for(Pool.LIGHT_PARSE, ceilings.light)

    def test_index_pool_starts_wide_enough_to_fill_the_pipeline(self) -> None:
        """Half its ceiling, not the count-pool floor of 2: the pipeline has
        to be full from the first message, so this pool must never spend the
        first samples ramping the way heavy does."""
        ceilings = Ceilings(heavy=12, light=64, index_heavy=24, index_light=24)
        limits = warm_start_limits(ceilings)

        assert limits.get(Pool.INDEX_HEAVY) == 12   # half of 24
        assert limits.get(Pool.INDEX_LIGHT) == 12
        assert limits.get(Pool.INDEX_LIGHT) > limits.get(Pool.HEAVY_PARSE)

    def test_explicit_high_ceiling_does_not_start_wide_open(self) -> None:
        """The OOM this fixes: MAX_CONCURRENT_PARSING/INDEXING=1000 used to
        hand out 1000 permits before the first sample ran, and a limit only
        bounds new admissions — permits already granted cannot be revoked, so
        the halving that follows arrives far too late."""
        snap = _snapshot(cpu_quota=8.0, mem_limit_bytes=12 * 1024 ** 3)
        ceilings = resolve_ceilings(snap, env_parse=1000, env_index=1000, worker_count=1)
        limits = warm_start_limits(ceilings)

        assert ceilings.heavy == 8
        assert ceilings.light == 80
        # MAX_CONCURRENT_INDEXING only ever caps: the derived 132 is already
        # far below the 1000 asked for, so the reckless value changes nothing.
        assert ceilings.index == 176  # 16 heavy + 160 light
        assert limits.get(Pool.HEAVY_PARSE) == 2
        assert limits.get(Pool.INDEX_HEAVY) == ceilings.index_heavy // 2
        assert limits.get(Pool.INDEX_LIGHT) == ceilings.index_light // 2

    def test_ceiling_below_the_floor_is_honoured_exactly(self) -> None:
        ceilings = Ceilings(heavy=1, light=1, index_heavy=1, index_light=1)
        limits = warm_start_limits(ceilings)
        assert limits.get(Pool.HEAVY_PARSE) == 1
        assert limits.get(Pool.INDEX_HEAVY) == 1
        assert limits.get(Pool.INDEX_LIGHT) == 1


class TestStartRateLimiterParams:
    """Regression coverage for the "5-10 docs forever regardless of
    MAX_CONCURRENT_*" bug: StartRateLimiter's sustained rate is exactly
    1/interval (capacity only bounds burst — see gate.py), so a fixed
    interval caps admissions independent of the pool's ceiling unless this
    scales with it."""

    def test_small_derived_ceiling_keeps_original_conservative_rate(self) -> None:
        interval, capacity = start_rate_limiter_params(2)
        assert interval == HEAVY_START_INTERVAL_SECONDS
        assert capacity == HEAVY_START_BUCKET_CAPACITY

    def test_large_explicit_ceiling_yields_much_higher_sustained_rate(self) -> None:
        interval, capacity = start_rate_limiter_params(1000)
        sustained_rate = 1.0 / interval
        assert sustained_rate == 1000 / HEAVY_START_RATE_CEILING_DIVISOR
        assert sustained_rate > 1.0 / HEAVY_START_INTERVAL_SECONDS
        assert capacity == 1000 // HEAVY_START_RATE_CEILING_DIVISOR

    def test_rate_never_falls_below_the_original_default(self) -> None:
        for ceiling in (0, 1, 2, 5, 10):
            interval, _ = start_rate_limiter_params(ceiling)
            assert 1.0 / interval >= 1.0 / HEAVY_START_INTERVAL_SECONDS

    def test_capacity_never_falls_below_the_original_default(self) -> None:
        for ceiling in (0, 1, 2, 5, 10):
            _, capacity = start_rate_limiter_params(ceiling)
            assert capacity >= HEAVY_START_BUCKET_CAPACITY

    def test_rate_scales_monotonically_with_ceiling(self) -> None:
        small_interval, _ = start_rate_limiter_params(100)
        large_interval, _ = start_rate_limiter_params(1000)
        assert large_interval < small_interval


class TestNextLimitsPressure:
    def _ceilings(self) -> Ceilings:
        return Ceilings(heavy=8, light=32, index_heavy=24, index_light=24)

    def test_hard_pressure_halves_and_starts_cooldown(self) -> None:
        ceilings = self._ceilings()
        current = Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 32, Pool.INDEX_HEAVY: 16, Pool.INDEX_LIGHT: 16})
        snap = _snapshot(mem_working_set_bytes=int(0.9 * 4 * 1024 ** 3), mem_limit_bytes=4 * 1024 ** 3)
        state = ControllerState.initial()

        new_limits, new_state = next_limits(current, snap, ceilings, state, _no_demand(), now=0.0, interval=INTERVAL)

        assert new_limits.get(Pool.HEAVY_PARSE) == 4
        assert new_limits.get(Pool.LIGHT_PARSE) == 16
        assert new_state.get(Pool.HEAVY_PARSE).cooldown_until > 0.0
        # Index halves too: every in-flight record holds a downloaded buffer,
        # so an emergency memory brake has to narrow the pipeline as well.
        assert new_limits.get(Pool.INDEX_HEAVY) == 8
        # Light stops at its own floor rather than halving to heavy's. The
        # floors are equal *in memory*, not in count, so the cheap tier keeps
        # turning records over during an incident instead of throwing away an
        # order of magnitude of throughput to save nothing.
        assert new_limits.get(Pool.INDEX_LIGHT) == policy_mod.pressure_floor(
            Pool.INDEX_LIGHT, ceilings.index_light
        )

    def test_soft_pressure_decrements_by_one(self) -> None:
        ceilings = self._ceilings()
        current = Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 32, Pool.INDEX_HEAVY: 16, Pool.INDEX_LIGHT: 16})
        snap = _snapshot(mem_working_set_bytes=int((MEM_SOFT + 0.02) * 4 * 1024 ** 3), mem_limit_bytes=4 * 1024 ** 3)
        state = ControllerState.initial()

        new_limits, _ = next_limits(current, snap, ceilings, state, _no_demand(), now=0.0, interval=INTERVAL)

        assert new_limits.get(Pool.HEAVY_PARSE) == 7
        assert new_limits.get(Pool.LIGHT_PARSE) == 31
        # The index tiers shrink harder than -1 here, and correctly so: at
        # soft pressure there is by definition little free memory left, so
        # index_memory_cap is already below the current limit and the
        # walk-down toward it dominates. index_heavy converges further than
        # index_light because its assumed per-record working set is ~7x
        # larger, so the same free memory holds far fewer of them.
        assert new_limits.get(Pool.INDEX_HEAVY) < new_limits.get(Pool.INDEX_LIGHT) < 16

    def test_cpu_brake_decrements_even_when_memory_is_fine(self) -> None:
        ceilings = self._ceilings()
        current = Limits(values={Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 32, Pool.INDEX_HEAVY: 16, Pool.INDEX_LIGHT: 16})
        snap = _snapshot(cpu_utilisation=0.95, mem_working_set_bytes=int(0.1 * 4 * 1024 ** 3))
        state = ControllerState.initial()

        new_limits, _ = next_limits(current, snap, ceilings, state, _no_demand(), now=0.0, interval=INTERVAL)

        assert new_limits.get(Pool.HEAVY_PARSE) == 7
        # Neither light parses nor in-flight records are CPU-bound — a
        # saturated Docling host must not queue Jira/Slack, or narrow the
        # pipeline, behind a brake for pressure they are not causing.
        assert new_limits.get(Pool.LIGHT_PARSE) == 32
        assert new_limits.get(Pool.INDEX_HEAVY) == 16
        assert new_limits.get(Pool.INDEX_LIGHT) == 16

    def test_memory_unknown_freezes_at_current(self) -> None:
        ceilings = self._ceilings()
        current = Limits(values={
            Pool.HEAVY_PARSE: 3, Pool.LIGHT_PARSE: 10,
            Pool.INDEX_HEAVY: ceilings.index_heavy,
            Pool.INDEX_LIGHT: ceilings.index_light,
        })
        snap = _snapshot(mem_limit_bytes=None, mem_working_set_bytes=None, cpu_utilisation=0.1)
        state = ControllerState.initial()

        new_limits, _ = next_limits(current, snap, ceilings, state, _saturated_demand(3), now=0.0, interval=INTERVAL)

        for pool in Pool:
            assert new_limits.get(pool) == current.get(pool)


class TestBrakeUsesRawPressure:
    """The all-in-one container OOM: crediting a co-located service's idle
    footprint to ``mem_pressure`` also pushed the shrink brake's trip points
    up by the baseline's share of the limit, so the governor kept admitting
    heavy parses while the cgroup was already within one Docling page batch
    of its cap. Shrink reads the uncredited occupancy; growth still reads the
    credited one.
    """

    LIMIT = 12 * 1024 ** 3
    BASELINE = 3 * 1024 ** 3

    def _ceilings(self) -> Ceilings:
        return Ceilings(heavy=8, light=32, index_heavy=12, index_light=12)

    def _limits(self) -> Limits:
        return Limits(values={
            Pool.HEAVY_PARSE: 8, Pool.LIGHT_PARSE: 32, Pool.INDEX_HEAVY: 16, Pool.INDEX_LIGHT: 16,
        })

    def _snap_at_raw_pressure(self, raw_pressure: float) -> ResourceSnapshot:
        raw_working_set = int(raw_pressure * self.LIMIT)
        return _snapshot(
            mem_limit_bytes=self.LIMIT,
            mem_working_set_bytes=raw_working_set - self.BASELINE,
            mem_working_set_raw_bytes=raw_working_set,
            mem_baseline_bytes=self.BASELINE,
        )

    def test_soft_brake_trips_on_occupancy_the_baseline_hides(self) -> None:
        snap = self._snap_at_raw_pressure(MEM_SOFT + 0.06)
        assert snap.mem_pressure < MEM_SOFT, "precondition: credited reading looks healthy"

        new_limits, _ = next_limits(
            self._limits(), snap, self._ceilings(), ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )

        assert new_limits.get(Pool.HEAVY_PARSE) == 7

    def test_hard_brake_trips_on_occupancy_the_baseline_hides(self) -> None:
        snap = self._snap_at_raw_pressure(policy_mod.MEM_HARD + 0.02)
        assert snap.mem_pressure < policy_mod.MEM_HARD, "precondition: credited reading is below MEM_HARD"

        new_limits, _ = next_limits(
            self._limits(), snap, self._ceilings(), ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )

        assert new_limits.get(Pool.HEAVY_PARSE) == 4

    def test_growth_still_credits_the_baseline(self) -> None:
        """The baseline's actual purpose: a container holding a large but idle
        co-located footprint must still be able to grow, rather than sitting
        pinned at its floor."""
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = self._snap_at_raw_pressure(MEM_SOFT - 0.1)
        assert snap.mem_pressure_raw < MEM_SOFT, "precondition: brake must stay off"

        for i in range(GROW_CONFIRM_SAMPLES):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(
                limits, snap, ceilings, state, demand, now=float(i) * INTERVAL, interval=INTERVAL,
            )

        assert limits.get(Pool.HEAVY_PARSE) == floor_for(Pool.HEAVY_PARSE, ceilings.heavy) + 1


class TestHeavyMemoryGate:
    """The heavy ceiling is CPU-sized, so free memory — not the ceiling —
    decides how many Docling working sets can actually be resident. This
    gate walks heavy down to that number without waiting for the pressure
    brake, which by design only trips once headroom is nearly gone."""

    def _ceilings(self) -> Ceilings:
        return Ceilings(heavy=8, light=24, index_heavy=24, index_light=24)

    def _limits(self, heavy: int) -> Limits:
        return Limits(values={
            Pool.HEAVY_PARSE: heavy, Pool.LIGHT_PARSE: 12, Pool.INDEX_HEAVY: 4, Pool.INDEX_LIGHT: 4,
        })

    def _snap_with_free_gb(self, free_gb: float) -> ResourceSnapshot:
        """A snapshot with *free_gb* absolute headroom at 50% occupancy —
        comfortably below every brake, so only the memory gate can act.
        Absolute free memory and pressure are independent: a small cgroup
        can be half empty and still not hold two Docling working sets."""
        limit = int(free_gb * 2 * 1024 ** 3)
        return _snapshot(
            cpu_quota=8.0, cpu_utilisation=0.1,
            mem_limit_bytes=limit,
            mem_working_set_bytes=limit - int(free_gb * 1024 ** 3),
            mem_working_set_raw_bytes=limit - int(free_gb * 1024 ** 3),
        )

    def test_heavy_shrinks_toward_what_free_memory_can_hold(self) -> None:
        # 4.5GiB free / 1.5GiB per parse = 3 slots, while the ceiling is 8.
        snap = self._snap_with_free_gb(4.5)
        assert snap.mem_pressure_raw < MEM_SOFT, "precondition: no brake, only the memory gate"

        new_limits, _ = next_limits(
            self._limits(heavy=8), snap, self._ceilings(), ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )

        assert new_limits.get(Pool.HEAVY_PARSE) == 7  # one step per sample, toward 3

    def test_heavy_stops_shrinking_once_it_reaches_the_memory_cap(self) -> None:
        snap = self._snap_with_free_gb(4.5)
        new_limits, _ = next_limits(
            self._limits(heavy=3), snap, self._ceilings(), ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )
        assert new_limits.get(Pool.HEAVY_PARSE) == 3

    def test_memory_gate_can_hold_heavy_below_its_warm_start_floor(self) -> None:
        """Scarce memory must be able to pull heavy under floor_for's 2 —
        clamping at the floor would keep admitting a second Docling working
        set the cgroup has no room for."""
        ceilings = self._ceilings()
        snap = self._snap_with_free_gb(1.0)
        assert snap.mem_pressure_raw < MEM_SOFT

        new_limits, _ = next_limits(
            self._limits(heavy=2), snap, ceilings, ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )

        assert floor_for(Pool.HEAVY_PARSE, ceilings.heavy) == 2
        assert new_limits.get(Pool.HEAVY_PARSE) == 1

    def test_light_is_untouched_by_the_heavy_memory_gate(self) -> None:
        snap = self._snap_with_free_gb(1.0)
        new_limits, _ = next_limits(
            self._limits(heavy=8), snap, self._ceilings(), ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )
        assert new_limits.get(Pool.LIGHT_PARSE) == 12

    def test_heavy_grows_back_no_further_than_the_memory_cap(self) -> None:
        ceilings = self._ceilings()
        snap = self._snap_with_free_gb(4.5)  # cap of 3
        limits = self._limits(heavy=2)
        state = ControllerState.initial()

        now = 0.0
        for _ in range(GROW_CONFIRM_SAMPLES * 4):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(limits, snap, ceilings, state, demand, now=now, interval=INTERVAL)
            now += INTERVAL

        assert limits.get(Pool.HEAVY_PARSE) == 3

    def test_unknown_memory_limit_leaves_heavy_at_its_ceiling(self) -> None:
        snap = _snapshot(
            cpu_quota=8.0, cpu_utilisation=0.1,
            mem_limit_bytes=None, mem_working_set_bytes=None,
        )
        new_limits, _ = next_limits(
            self._limits(heavy=8), snap, self._ceilings(), ControllerState.initial(),
            _no_demand(), now=0.0, interval=INTERVAL,
        )
        assert new_limits.get(Pool.HEAVY_PARSE) == 8


class TestNextLimitsGrowth:
    def _ceilings(self) -> Ceilings:
        return Ceilings(heavy=8, light=32, index_heavy=12, index_light=12)

    def _healthy_snapshot(self) -> ResourceSnapshot:
        return _snapshot(
            cpu_quota=8.0, cpu_utilisation=0.1,
            mem_limit_bytes=16 * 1024 ** 3, mem_working_set_bytes=1 * 1024 ** 3,
        )

    def _light_demand(self, limits: Limits) -> dict[Pool, PoolDemand]:
        return {
            Pool.LIGHT_PARSE: PoolDemand(
                permit_seconds=limits.get(Pool.LIGHT_PARSE) * INTERVAL,
                blocked_acquires=5,
                completions=100,
            ),
            Pool.HEAVY_PARSE: PoolDemand.empty(),
            Pool.INDEX_HEAVY: PoolDemand.empty(),
            Pool.INDEX_LIGHT: PoolDemand.empty(),
        }

    def test_no_growth_without_demand(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = self._healthy_snapshot()

        for _ in range(GROW_CONFIRM_SAMPLES + 2):
            limits, state = next_limits(limits, snap, ceilings, state, _no_demand(), now=0.0, interval=INTERVAL)

        assert limits.get(Pool.HEAVY_PARSE) == floor_for(Pool.HEAVY_PARSE, ceilings.heavy)

    def test_growth_after_confirm_window_with_demand(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = self._healthy_snapshot()

        for _ in range(GROW_CONFIRM_SAMPLES):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(limits, snap, ceilings, state, demand, now=float(_) * INTERVAL, interval=INTERVAL)

        assert limits.get(Pool.HEAVY_PARSE) == floor_for(Pool.HEAVY_PARSE, ceilings.heavy) + 1

    def test_growth_capped_at_one_step_per_interval(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = self._healthy_snapshot()

        before = limits.get(Pool.HEAVY_PARSE)
        for i in range(GROW_CONFIRM_SAMPLES):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(limits, snap, ceilings, state, demand, now=float(i) * INTERVAL, interval=INTERVAL)

        assert limits.get(Pool.HEAVY_PARSE) - before <= 1

    def test_growth_cooldown_blocks_regrowth_after_shrink(self) -> None:
        ceilings = self._ceilings()
        limits = Limits(values={Pool.HEAVY_PARSE: 4, Pool.LIGHT_PARSE: 10, Pool.INDEX_HEAVY: 8, Pool.INDEX_LIGHT: 8})
        state = ControllerState.initial()

        hard_snap = _snapshot(mem_working_set_bytes=int(0.9 * 4 * 1024 ** 3), mem_limit_bytes=4 * 1024 ** 3)
        limits, state = next_limits(limits, hard_snap, ceilings, state, _no_demand(), now=0.0, interval=INTERVAL)
        assert limits.get(Pool.HEAVY_PARSE) == 2

        healthy_snap = self._healthy_snapshot()
        demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
        for i in range(1, GROW_CONFIRM_SAMPLES + 1):
            limits, state = next_limits(limits, healthy_snap, ceilings, state, demand, now=float(i) * 1.0, interval=INTERVAL)

        # Still inside the 60s incident cooldown at these small `now` values.
        assert limits.get(Pool.HEAVY_PARSE) == 2

    def test_index_pool_stays_within_its_floor_and_ceiling_under_any_sample(self) -> None:
        """Index is adapted like the parse pools now, but it is the pool the
        whole pipeline's width depends on: no sequence of pressure, CPU
        saturation or idleness may drive it below its floor or above its
        ceiling."""
        ceilings = Ceilings(heavy=2, light=16, index_heavy=12, index_light=12)
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        # pressure_floor, not floor_for: the warm-start width is a CPU-derived
        # starting point that a brake is allowed to shrink past.
        floor = policy_mod.pressure_floor(Pool.INDEX_LIGHT, ceilings.index_light)
        mem_limit = 4 * 1024 ** 3
        samples = [
            _snapshot(cpu_quota=2.0, cpu_utilisation=0.02,
                      mem_limit_bytes=16 * 1024 ** 3, mem_working_set_bytes=1024 ** 3),
            _snapshot(cpu_quota=2.0, cpu_utilisation=0.99,
                      mem_limit_bytes=mem_limit, mem_working_set_bytes=int(0.95 * mem_limit)),
        ]

        now = 0.0
        for snap in samples * 4:
            for demand in (_no_demand(), _saturated_demand(ceilings.index)):
                limits, state = next_limits(
                    limits, snap, ceilings, state, demand, now=now, interval=INTERVAL,
                )
                now += INTERVAL
                assert floor <= limits.get(Pool.INDEX_LIGHT) <= ceilings.index_light

    def test_index_grows_back_to_its_ceiling_when_memory_is_plentiful(self) -> None:
        """The 96 GiB case: a big host must actually reach its full in-flight
        width rather than sitting at the warm-start floor forever."""
        ceilings = Ceilings(heavy=8, light=64, index_heavy=64, index_light=64)
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = _snapshot(
            cpu_quota=16.0, cpu_utilisation=0.2,
            mem_limit_bytes=96 * 1024 ** 3, mem_working_set_bytes=8 * 1024 ** 3,
        )

        now = 0.0
        for _ in range(20):
            limits, state = next_limits(
                limits, snap, ceilings, state,
                _saturated_demand(limits.get(Pool.INDEX_LIGHT)),
                now=now, interval=INTERVAL,
            )
            now += INTERVAL

        assert limits.get(Pool.INDEX_LIGHT) == ceilings.index_light
        assert limits.get(Pool.INDEX_HEAVY) == ceilings.index_heavy

    def test_index_is_held_down_by_a_small_memory_budget(self) -> None:
        """The 8 GiB case: the same image on a small host must hold the
        pipeline to what free memory can actually buffer, without the
        operator setting anything."""
        ceilings = Ceilings(heavy=3, light=40, index_heavy=100, index_light=100)
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        mem_limit = 8 * 1024 ** 3
        snap = _snapshot(
            cpu_quota=4.0, cpu_utilisation=0.2,
            mem_limit_bytes=mem_limit, mem_working_set_bytes=int(6 * 1024 ** 3),
            mem_working_set_raw_bytes=int(6 * 1024 ** 3),
        )
        cap = policy_mod.index_memory_cap(snap, Pool.INDEX_LIGHT)
        assert cap is not None and cap < ceilings.index

        now = 0.0
        for _ in range(8):
            limits, state = next_limits(
                limits, snap, ceilings, state,
                _saturated_demand(limits.get(Pool.INDEX_LIGHT)),
                now=now, interval=INTERVAL,
            )
            now += INTERVAL

        # Within a handful of samples, not the ~60 a one-permit-per-sample
        # walk-down would need: while it is converging the container is
        # over-committed by exactly the memory this cap exists to protect.
        assert limits.get(Pool.INDEX_LIGHT) <= cap

    def test_light_grows_after_one_healthy_sample(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = self._healthy_snapshot()

        for _ in range(LIGHT_GROW_CONFIRM_SAMPLES):
            limits, state = next_limits(
                limits, snap, ceilings, state, self._light_demand(limits), now=0.0, interval=INTERVAL,
            )

        assert limits.get(Pool.LIGHT_PARSE) == floor_for(Pool.LIGHT_PARSE, ceilings.light) + 1
        assert limits.get(Pool.HEAVY_PARSE) == floor_for(Pool.HEAVY_PARSE, ceilings.heavy)

    def test_heavy_grows_just_below_mem_soft(self) -> None:
        """Heavy no longer needs an extra GROW_BAND of headroom — the
        memory cap is the per-slot gate — so a cgroup sitting just under
        MEM_SOFT still ramps."""
        ceilings = self._ceilings()
        mem_limit = 16 * 1024 ** 3
        pressure = MEM_SOFT - 0.02
        snap = _snapshot(
            cpu_quota=8.0, cpu_utilisation=0.1,
            mem_limit_bytes=mem_limit, mem_working_set_bytes=int(pressure * mem_limit),
        )
        assert snap.mem_pressure_raw is not None
        assert snap.mem_pressure_raw < MEM_SOFT

        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        now = 0.0
        for _ in range(GROW_CONFIRM_SAMPLES):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(limits, snap, ceilings, state, demand, now=now, interval=INTERVAL)
            now += INTERVAL

        assert limits.get(Pool.HEAVY_PARSE) == floor_for(Pool.HEAVY_PARSE, ceilings.heavy) + 1

    def test_light_grows_despite_cpu_brake(self) -> None:
        ceilings = self._ceilings()
        limits = Limits(values={
            Pool.HEAVY_PARSE: 8,
            Pool.LIGHT_PARSE: floor_for(Pool.LIGHT_PARSE, ceilings.light),
            Pool.INDEX_HEAVY: ceilings.index_heavy,
            Pool.INDEX_LIGHT: ceilings.index_light,
        })
        state = ControllerState.initial()
        snap = _snapshot(
            cpu_quota=8.0, cpu_utilisation=0.95,
            mem_limit_bytes=16 * 1024 ** 3, mem_working_set_bytes=1 * 1024 ** 3,
        )

        new_limits, _ = next_limits(
            limits, snap, ceilings, state, self._light_demand(limits), now=0.0, interval=INTERVAL,
        )

        assert new_limits.get(Pool.LIGHT_PARSE) == limits.get(Pool.LIGHT_PARSE) + 1
        assert new_limits.get(Pool.HEAVY_PARSE) == 7

    def test_light_parse_keeps_doubling_while_throughput_stays_flat(self) -> None:
        ceilings = Ceilings(heavy=2, light=24, index_heavy=12, index_light=12)
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = _snapshot(
            cpu_quota=2.0, cpu_utilisation=0.02,
            mem_limit_bytes=16 * 1024 ** 3, mem_working_set_bytes=1 * 1024 ** 3,
        )

        def demand_at(limit: int) -> dict[Pool, PoolDemand]:
            return {
                Pool.LIGHT_PARSE: PoolDemand(
                    permit_seconds=limit * INTERVAL, blocked_acquires=50, completions=100,
                ),
                Pool.INDEX_HEAVY: PoolDemand.empty(),
            Pool.INDEX_LIGHT: PoolDemand.empty(),
                Pool.HEAVY_PARSE: PoolDemand.empty(),
            }

        now = 0.0
        floor = floor_for(Pool.LIGHT_PARSE, ceilings.light)
        for _ in range(4):
            limits, state = next_limits(
                limits, snap, ceilings, state, demand_at(limits.get(Pool.LIGHT_PARSE)),
                now=now, interval=INTERVAL,
            )
            now += INTERVAL

        # Light skips the resource-delta check entirely, so it keeps
        # doubling (1+2+4 after the first three samples, then +8).
        assert limits.get(Pool.LIGHT_PARSE) > floor + 1


class TestExponentialGrowth:
    """TCP-slow-start-inspired growth (plan section 4, Phase A/C).

    Uses HEAVY_PARSE throughout to isolate the resource-delta step-sizing
    mechanism from the light tier's looser gates.
    """

    def _ceilings(self) -> Ceilings:
        return Ceilings(heavy=1000, light=32, index_heavy=12, index_light=12)

    def _snap_with_working_set(self, working_set_gb: float) -> ResourceSnapshot:
        # cpu_quota/mem_limit fixed and generous throughout so `_target_for`
        # never caps growth before ~17 permits — only the resource-delta
        # step logic under test can hold or throttle it.
        return _snapshot(
            cpu_quota=64.0, cpu_utilisation=0.1,
            mem_limit_bytes=64 * 1024 ** 3, mem_working_set_bytes=int(working_set_gb * 1024 ** 3),
        )

    def _confirm_and_grow_once(
        self, limits: Limits, state: ControllerState, ceilings: Ceilings, snap: ResourceSnapshot, start_now: float,
    ) -> tuple[Limits, ControllerState, float]:
        """Run GROW_CONFIRM_SAMPLES healthy+demand samples, triggering
        exactly one grow (the first, always step=1 since there is no prior
        baseline). Returns the updated state and the next `now` to use."""
        now = start_now
        for _ in range(GROW_CONFIRM_SAMPLES):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(limits, snap, ceilings, state, demand, now=now, interval=INTERVAL)
            now += INTERVAL
        return limits, state, now

    def test_step_doubles_each_successful_grow_with_stable_resources(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        snap = self._snap_with_working_set(1.0)
        floor = floor_for(Pool.HEAVY_PARSE, ceilings.heavy)

        limits, state, now = self._confirm_and_grow_once(limits, state, ceilings, snap, start_now=0.0)
        assert limits.get(Pool.HEAVY_PARSE) == floor + 1  # first grow: no baseline yet, step=1

        # Every following healthy+demand sample keeps growing (healthy_streak
        # stays >= GROW_CONFIRM_SAMPLES) with the SAME stable snapshot, so
        # each step should double: +2, +4, +8.
        expected_steps = [2, 4, 8]
        running_total = floor + 1
        for step in expected_steps:
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = next_limits(limits, snap, ceilings, state, demand, now=now, interval=INTERVAL)
            now += INTERVAL
            running_total += step
            assert limits.get(Pool.HEAVY_PARSE) == running_total

        assert state.get(Pool.HEAVY_PARSE).in_slow_start is True

    def test_moderate_resource_delta_switches_to_linear_permanently(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        baseline_snap = self._snap_with_working_set(1.0)  # mem_pressure ~= 0.0156

        limits, state, now = self._confirm_and_grow_once(limits, state, ceilings, baseline_snap, start_now=0.0)
        after_first_grow = limits.get(Pool.HEAVY_PARSE)

        # ~11% delta vs. the recorded baseline: moderate impact.
        moderate_snap = self._snap_with_working_set(8.0)  # mem_pressure = 0.125
        demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
        limits, state = next_limits(limits, moderate_snap, ceilings, state, demand, now=now, interval=INTERVAL)
        now += INTERVAL

        assert limits.get(Pool.HEAVY_PARSE) == after_first_grow + 1  # switched to linear, not doubled
        assert state.get(Pool.HEAVY_PARSE).in_slow_start is False

        # Even back on the calm baseline snapshot, growth stays linear —
        # exiting slow start is permanent until a shrink re-arms it.
        demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
        limits, state = next_limits(limits, baseline_snap, ceilings, state, demand, now=now, interval=INTERVAL)
        assert limits.get(Pool.HEAVY_PARSE) == after_first_grow + 2

    def test_large_resource_delta_holds_without_abandoning_slow_start(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        baseline_snap = self._snap_with_working_set(1.0)  # mem_pressure ~= 0.0156

        limits, state, now = self._confirm_and_grow_once(limits, state, ceilings, baseline_snap, start_now=0.0)
        held_at = limits.get(Pool.HEAVY_PARSE)

        # ~30% delta vs. the recorded baseline: large impact -> hold.
        spike_snap = self._snap_with_working_set(20.0)  # mem_pressure = 0.3125
        demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
        limits, state = next_limits(limits, spike_snap, ceilings, state, demand, now=now, interval=INTERVAL)
        now += INTERVAL

        assert limits.get(Pool.HEAVY_PARSE) == held_at  # unchanged this round
        assert state.get(Pool.HEAVY_PARSE).in_slow_start is True  # still armed

        # Back to the calm baseline: doubling resumes from the step size
        # that had already been earned (2), not reset to 1.
        demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
        limits, state = next_limits(limits, baseline_snap, ceilings, state, demand, now=now, interval=INTERVAL)
        assert limits.get(Pool.HEAVY_PARSE) == held_at + 2

    def test_recovery_after_shrink_reenters_slow_start(self) -> None:
        ceilings = self._ceilings()
        limits = warm_start_limits(ceilings)
        state = ControllerState.initial()
        baseline_snap = self._snap_with_working_set(1.0)

        # Grow twice under stable resources: +1, then +2 (slow_start_step
        # has doubled to 4 for the *next* grow at this point).
        limits, state, now = self._confirm_and_grow_once(limits, state, ceilings, baseline_snap, start_now=0.0)
        demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
        limits, state = next_limits(limits, baseline_snap, ceilings, state, demand, now=now, interval=INTERVAL)
        now += INTERVAL
        grown_limit = limits.get(Pool.HEAVY_PARSE)
        assert state.get(Pool.HEAVY_PARSE).slow_start_step == 4

        # A hard-pressure incident halves the limit and must reset slow
        # start entirely (plan: "Reset to slow-start").
        hard_snap = _snapshot(mem_working_set_bytes=int(0.9 * 4 * 1024 ** 3), mem_limit_bytes=4 * 1024 ** 3)
        limits, state = next_limits(limits, hard_snap, ceilings, state, demand, now=now, interval=INTERVAL)
        now += INTERVAL
        assert limits.get(Pool.HEAVY_PARSE) == grown_limit // 2
        halved_state = state.get(Pool.HEAVY_PARSE)
        assert halved_state.in_slow_start is True
        assert halved_state.slow_start_step == 1
        assert halved_state.prev_grow_mem_pressure is None

        # After the incident cooldown clears and resources are healthy
        # again, the first grow back must be +1 (fresh slow start), not a
        # jump that continues the pre-shrink step size.
        now += INCIDENT_COOLDOWN_SECONDS + 1.0
        before_recovery = limits.get(Pool.HEAVY_PARSE)
        limits, state, _ = self._confirm_and_grow_once(limits, state, ceilings, baseline_snap, start_now=now)
        assert limits.get(Pool.HEAVY_PARSE) == before_recovery + 1


class TestEnvFloatHelper:
    """``_env_float`` backs GOVERNOR_MEM_SOFT/HARD/GROW_BAND (plan: "Fix 3 —
    Make MEM_SOFT/MEM_HARD/GROW_BAND configurable") — a typo'd or absent
    override must never crash startup or push the governor into a
    degenerate state."""

    def test_returns_default_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TEST_ENV_FLOAT_VAR", raising=False)
        assert policy_mod._env_float("TEST_ENV_FLOAT_VAR", 0.5, low=0.0, high=1.0) == 0.5

    def test_returns_default_on_malformed_value(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_ENV_FLOAT_VAR", "not-a-number")
        assert policy_mod._env_float("TEST_ENV_FLOAT_VAR", 0.5, low=0.0, high=1.0) == 0.5

    def test_clamps_value_above_high(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_ENV_FLOAT_VAR", "5.0")
        assert policy_mod._env_float("TEST_ENV_FLOAT_VAR", 0.5, low=0.0, high=1.0) == 1.0

    def test_clamps_value_below_low(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_ENV_FLOAT_VAR", "-5.0")
        assert policy_mod._env_float("TEST_ENV_FLOAT_VAR", 0.5, low=0.0, high=1.0) == 0.0

    def test_valid_value_within_range_passes_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TEST_ENV_FLOAT_VAR", "0.42")
        assert policy_mod._env_float("TEST_ENV_FLOAT_VAR", 0.5, low=0.0, high=1.0) == 0.42


class TestConfigurableThresholds:
    """MEM_SOFT/MEM_HARD/GROW_BAND are read from the environment once at
    module import (plan: "Fix 3"). These tests reload the module with the
    env var set, then reload it again on teardown so every other test in
    the suite keeps seeing the original defaults — module globals are
    shared by every function in ``policy.py`` via ``__globals__``, so a
    leaked override would silently change behaviour for unrelated tests.
    """

    @pytest.fixture(autouse=True)
    def _restore_module_after(self) -> "Generator[None, None, None]":
        yield
        for var in ("GOVERNOR_MEM_SOFT", "GOVERNOR_MEM_HARD", "GOVERNOR_GROW_BAND"):
            import os
            os.environ.pop(var, None)
        importlib.reload(policy_mod)

    def test_mem_soft_override_takes_effect_after_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOVERNOR_MEM_SOFT", "0.90")
        importlib.reload(policy_mod)

        assert policy_mod.MEM_SOFT == 0.90

    def test_mem_hard_stays_strictly_above_overridden_mem_soft_even_when_unset(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # GOVERNOR_MEM_HARD deliberately left unset: its own default (0.85)
        # would sit *below* an overridden MEM_SOFT of 0.90 without the
        # invariant enforcement in policy.py.
        monkeypatch.setenv("GOVERNOR_MEM_SOFT", "0.90")
        importlib.reload(policy_mod)

        assert policy_mod.MEM_HARD > policy_mod.MEM_SOFT

    def test_grow_band_never_pushes_growth_threshold_negative(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("GOVERNOR_MEM_SOFT", "0.10")
        monkeypatch.setenv("GOVERNOR_GROW_BAND", "0.50")
        importlib.reload(policy_mod)

        assert policy_mod.MEM_SOFT - policy_mod.GROW_BAND >= 0.0

    def test_module_reload_without_overrides_restores_original_defaults(self) -> None:
        importlib.reload(policy_mod)

        assert policy_mod.MEM_SOFT == 0.70
        assert policy_mod.MEM_HARD == 0.80
        assert policy_mod.GROW_BAND == 0.0

    def test_raised_mem_soft_permits_growth_at_a_pressure_that_would_otherwise_shrink_forever(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End-to-end: a deployment with a persistent ~78% baseline (e.g. a
        co-located idle Docling process) can raise GOVERNOR_MEM_SOFT so the
        governor stops shrinking every interval despite idle CPU."""
        monkeypatch.setenv("GOVERNOR_MEM_SOFT", "0.85")
        monkeypatch.setenv("GOVERNOR_MEM_HARD", "0.95")
        importlib.reload(policy_mod)

        ceilings = Ceilings(heavy=8, light=32, index_heavy=12, index_light=12)
        limits = policy_mod.warm_start_limits(ceilings)
        state = ControllerState.initial()
        # 78% raw pressure on a large-enough container that plenty of
        # *absolute* free memory remains for HEAVY_PARSE's own per-slot
        # budget (_target_for) — isolating the MEM_SOFT/GROW_BAND effect
        # from that separate, unrelated constraint. With the original
        # MEM_SOFT=0.70 this pressure would have forced a shrink every
        # interval; with the overridden 0.85 it reads as healthy.
        snap = _snapshot(
            cpu_quota=8.0, cpu_utilisation=0.1,
            mem_limit_bytes=64 * 1024 ** 3, mem_working_set_bytes=int(0.78 * 64 * 1024 ** 3),
        )
        before = limits.get(Pool.HEAVY_PARSE)

        now = 0.0
        for _ in range(GROW_CONFIRM_SAMPLES):
            demand = _saturated_demand(limits.get(Pool.HEAVY_PARSE))
            limits, state = policy_mod.next_limits(limits, snap, ceilings, state, demand, now=now, interval=INTERVAL)
            now += INTERVAL

        assert limits.get(Pool.HEAVY_PARSE) > before
