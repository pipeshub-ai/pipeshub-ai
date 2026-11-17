"""The code sandbox yield as a policy input (``host_yield``)."""

from __future__ import annotations

import math

import pytest

from app.services.resource_governor import policy as policy_mod
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    LimitRule,
    Limits,
    Pool,
    PoolDemand,
    ResourceSnapshot,
)
from app.services.resource_governor.policy import (
    INCIDENT_COOLDOWN_SECONDS,
    SAMPLE_INTERVAL_SECONDS,
    SHRINK_COOLDOWN_SECONDS,
    YIELD_GROW_CONFIRM_SAMPLES,
    YIELD_HEAVY_MAX_HOLDOFF_SAMPLES,
    YIELD_REPEAT_WINDOW_SAMPLES,
    next_limits,
    pressure_floor,
)

INTERVAL = SAMPLE_INTERVAL_SECONDS
GIB = 1024 ** 3
CEILINGS = Ceilings(heavy=8, light=32, index_heavy=32, index_light=64)
YIELDING = (Pool.HEAVY_PARSE, Pool.INDEX_HEAVY, Pool.INDEX_LIGHT)


def _snap(pressure: float = 0.125) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=16.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
        mem_limit_bytes=64 * GIB, mem_working_set_bytes=int(pressure * 64 * GIB), source="test",
    )


def _busy(limits: Limits) -> dict[Pool, PoolDemand]:
    return {
        pool: PoolDemand(permit_seconds=limits.get(pool) * INTERVAL, blocked_acquires=3, completions=10)
        for pool in Pool
    }


def _limits(heavy: int = 6, light: int = 24, index_heavy: int = 32, index_light: int = 64) -> Limits:
    return Limits(values={
        Pool.HEAVY_PARSE: heavy, Pool.LIGHT_PARSE: light,
        Pool.INDEX_HEAVY: index_heavy, Pool.INDEX_LIGHT: index_light,
    })


def _floor(pool: Pool) -> int:
    return pressure_floor(pool, policy_mod.ceiling_for(pool, CEILINGS))


def _step(
    limits: Limits, state: ControllerState, now: float, *, host_yield: bool, pressure: float = 0.125,
) -> tuple[Limits, ControllerState]:
    return next_limits(limits, _snap(pressure), CEILINGS, state, _busy(limits), now=now, host_yield=host_yield)


class TestShrink:
    def test_index_pools_take_a_quarter_heavy_one_and_light_parse_nothing(self) -> None:
        limits, state = _step(_limits(), ControllerState.initial(), 1000.0, host_yield=True)
        assert limits.get(Pool.INDEX_HEAVY) == 24
        assert limits.get(Pool.INDEX_LIGHT) == 48
        assert limits.get(Pool.HEAVY_PARSE) == 5
        assert limits.get(Pool.LIGHT_PARSE) >= 24
        for pool in YIELDING:
            assert state.get(pool).cooldown_until == 1000.0 + SHRINK_COOLDOWN_SECONDS
            assert state.get(pool).yield_holdoff == YIELD_GROW_CONFIRM_SAMPLES
            assert not state.get(pool).in_slow_start
        assert state.get(Pool.LIGHT_PARSE).yield_holdoff == 0

    def test_repeated_samples_stop_at_the_pressure_floor(self) -> None:
        limits, state = _limits(), ControllerState.initial()
        now = 1000.0
        for _ in range(20):
            limits, state = _step(limits, state, now, host_yield=True)
            now += INTERVAL
        for pool in YIELDING:
            assert limits.get(pool) == _floor(pool)

    def test_a_pool_at_its_floor_holds(self) -> None:
        floors = _limits(
            heavy=_floor(Pool.HEAVY_PARSE), index_heavy=_floor(Pool.INDEX_HEAVY),
            index_light=_floor(Pool.INDEX_LIGHT),
        )
        limits, state = _step(floors, ControllerState.initial(), 1000.0, host_yield=True)
        for pool in YIELDING:
            assert limits.get(pool) == floors.get(pool)
            assert state.get(pool).cooldown_until == 1000.0 + SHRINK_COOLDOWN_SECONDS

    def test_the_tightest_rule_wins_with_a_hard_memory_brake(self) -> None:
        limits, state = _step(_limits(), ControllerState.initial(), 1000.0, host_yield=True, pressure=0.9)
        assert limits.get(Pool.INDEX_HEAVY) == 16
        assert limits.get(Pool.HEAVY_PARSE) == 3
        assert state.get(Pool.INDEX_HEAVY).cooldown_until == 1000.0 + INCIDENT_COOLDOWN_SECONDS

    def test_the_yield_is_the_rule_the_pools_report(self) -> None:
        _, state = _step(_limits(), ControllerState.initial(), 1000.0, host_yield=True)
        for pool in YIELDING:
            assert state.get(pool).rule is LimitRule.HOST_YIELD
            assert state.get(pool).active_cooldown_cause(1001.0) == "host_yield"
        assert state.get(Pool.LIGHT_PARSE).rule is not LimitRule.HOST_YIELD

    def test_a_tighter_memory_brake_is_the_rule_reported(self) -> None:
        _, state = _step(_limits(), ControllerState.initial(), 1000.0, host_yield=True, pressure=0.9)
        assert state.get(Pool.INDEX_HEAVY).rule is LimitRule.MEM_HARD

    def test_index_quarter_rounds_up(self) -> None:
        limits, _ = _step(_limits(index_heavy=7), ControllerState.initial(), 1000.0, host_yield=True)
        assert limits.get(Pool.INDEX_HEAVY) == max(_floor(Pool.INDEX_HEAVY), math.ceil(7 * 0.75))


class TestRecovery:
    def _yield_then_clear(self) -> tuple[Limits, ControllerState, float]:
        limits, state = _limits(), ControllerState.initial()
        now = 1000.0
        for _ in range(3):
            limits, state = _step(limits, state, now, host_yield=True)
            now += INTERVAL
        return limits, state, now

    def test_no_growth_for_four_samples_then_one_at_a_time(self) -> None:
        limits, state, now = self._yield_then_clear()
        held = limits
        for _ in range(YIELD_GROW_CONFIRM_SAMPLES):
            limits, state = _step(limits, state, now, host_yield=False)
            now += INTERVAL
            for pool in YIELDING:
                assert limits.get(pool) == held.get(pool)
        for _ in range(3):
            before = limits
            limits, state = _step(limits, state, now, host_yield=False)
            now += INTERVAL
            for pool in YIELDING:
                assert limits.get(pool) - before.get(pool) == 1, pool

    def test_the_holdoff_is_the_rule_reported_once_the_cooldown_ends(self) -> None:
        limits, state, now = self._yield_then_clear()
        now = max(state.get(pool).cooldown_until for pool in YIELDING)
        for _ in range(policy_mod.LIGHT_GROW_CONFIRM_SAMPLES):
            limits, state = _step(limits, state, now, host_yield=False)
            now += INTERVAL
        held = [pool for pool in YIELDING if state.get(pool).yield_holdoff > 0]
        assert held
        for pool in held:
            assert state.get(pool).rule in (LimitRule.YIELD_HOLDOFF, LimitRule.CONFIRMING)
        assert any(state.get(pool).rule is LimitRule.YIELD_HOLDOFF for pool in held)

    def test_a_later_memory_shrink_re_arms_slow_start(self) -> None:
        limits, state, now = self._yield_then_clear()
        limits, state = _step(limits, state, now, host_yield=False, pressure=0.75)
        assert state.get(Pool.INDEX_LIGHT).in_slow_start


class TestHeavyParseBacksOffOnRepeats:
    def _run(
        self, limits: Limits, state: ControllerState, now: float, samples: int, *, host_yield: bool,
    ) -> tuple[Limits, ControllerState, float]:
        for _ in range(samples):
            limits, state = _step(limits, state, now, host_yield=host_yield)
            now += INTERVAL
        return limits, state, now

    def _episode(
        self, limits: Limits, state: ControllerState, now: float, *, quiet: int,
    ) -> tuple[Limits, ControllerState, float]:
        """One yielding sample, the holdoff it set run out, then *quiet* more clear samples."""
        limits, state, now = self._run(limits, state, now, 1, host_yield=True)
        limits, state, now = self._run(
            limits, state, now, state.get(Pool.HEAVY_PARSE).yield_holdoff + quiet, host_yield=False,
        )
        return limits, state, now

    def test_a_yield_soon_after_growth_resumed_doubles_heavy_only(self) -> None:
        limits, state, now = self._episode(_limits(), ControllerState.initial(), 1000.0, quiet=2)
        limits, state, now = self._run(limits, state, now, 1, host_yield=True)
        assert state.get(Pool.HEAVY_PARSE).yield_holdoff == 2 * YIELD_GROW_CONFIRM_SAMPLES
        assert state.get(Pool.INDEX_HEAVY).yield_holdoff == YIELD_GROW_CONFIRM_SAMPLES
        assert state.get(Pool.INDEX_LIGHT).yield_holdoff == YIELD_GROW_CONFIRM_SAMPLES

    def test_the_longer_holdoff_holds_heavy_while_the_index_pools_grow(self) -> None:
        limits, state, now = self._episode(_limits(), ControllerState.initial(), 1000.0, quiet=2)
        limits, state, now = self._run(limits, state, now, 1, host_yield=True)
        held = limits
        limits, state, now = self._run(limits, state, now, YIELD_GROW_CONFIRM_SAMPLES + 1, host_yield=False)
        assert limits.get(Pool.INDEX_HEAVY) > held.get(Pool.INDEX_HEAVY)
        assert limits.get(Pool.HEAVY_PARSE) == held.get(Pool.HEAVY_PARSE)
        limits, state, now = self._run(limits, state, now, YIELD_GROW_CONFIRM_SAMPLES, host_yield=False)
        assert limits.get(Pool.HEAVY_PARSE) == held.get(Pool.HEAVY_PARSE) + 1

    def test_repeats_double_up_to_the_cap(self) -> None:
        limits, state, now = _limits(), ControllerState.initial(), 1000.0
        seen = []
        for _ in range(8):
            limits, state, now = self._episode(limits, state, now, quiet=1)
            seen.append(state.get(Pool.HEAVY_PARSE).yield_holdoff_set)
        assert seen[:4] == [4, 8, 16, 32]
        assert max(seen) == seen[-1] == YIELD_HEAVY_MAX_HOLDOFF_SAMPLES

    def test_a_yield_after_a_quiet_spell_starts_again_from_the_base(self) -> None:
        limits, state, now = _limits(), ControllerState.initial(), 1000.0
        for _ in range(3):
            limits, state, now = self._episode(limits, state, now, quiet=1)
        assert state.get(Pool.HEAVY_PARSE).yield_holdoff_set == 4 * YIELD_GROW_CONFIRM_SAMPLES
        limits, state, now = self._run(limits, state, now, YIELD_REPEAT_WINDOW_SAMPLES, host_yield=False)
        limits, state, now = self._run(limits, state, now, 1, host_yield=True)
        assert state.get(Pool.HEAVY_PARSE).yield_holdoff == YIELD_GROW_CONFIRM_SAMPLES

    def test_a_yield_back_before_growth_resumed_does_not_lengthen_it(self) -> None:
        limits, state, now = self._run(_limits(), ControllerState.initial(), 1000.0, 1, host_yield=True)
        limits, state, now = self._run(limits, state, now, YIELD_GROW_CONFIRM_SAMPLES - 1, host_yield=False)
        limits, state, now = self._run(limits, state, now, 1, host_yield=True)
        assert state.get(Pool.HEAVY_PARSE).yield_holdoff == YIELD_GROW_CONFIRM_SAMPLES

    def test_a_long_episode_counts_once(self) -> None:
        limits, state, now = self._run(_limits(), ControllerState.initial(), 1000.0, 6, host_yield=True)
        assert state.get(Pool.HEAVY_PARSE).yield_holdoff == YIELD_GROW_CONFIRM_SAMPLES


@pytest.mark.parametrize("pressure", [0.125, 0.75, 0.9])
def test_host_yield_false_is_the_default(pressure: float) -> None:
    limits, state = _limits(), ControllerState.initial()
    explicit = next_limits(limits, _snap(pressure), CEILINGS, state, _busy(limits), now=1000.0, host_yield=False)
    default = next_limits(limits, _snap(pressure), CEILINGS, state, _busy(limits), now=1000.0)
    assert explicit == default
