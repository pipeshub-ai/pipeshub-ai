"""Indexing and the code sandbox live limit adapting on one host do not fight.

Two simulated hours per scenario on ``sim_host``, which drives the real
indexing policy and the real live-limit step. The bounds were recorded from
the model's first passing runs, with some margin; the model's release lag is
a guess until measured on a real host, so they are a guard against
regressions, not a specification.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.resource_governor.models import Pool
from tests.unit.services.resource_governor import sim_host as sim

TEN_MINUTES = 600 // sim.INTERVAL
H16 = sim.HOST_16
STEADY = sim.Scenario(H16, users=sim.users_for(0.3, H16))
SCENARIOS = {
    "sync_and_steady_chat_16g": STEADY,
    "sync_and_steady_chat_192g": sim.Scenario(sim.HOST_192, users=sim.users_for(0.3, sim.HOST_192)),
    "chat_burst_during_sync": replace(
        STEADY, burst_users=2 * H16.sandbox_ceiling, burst_start=1800.0, burst_end=2100.0,
    ),
    "sync_alone": sim.Scenario(H16),
    "chat_alone": replace(STEADY, sync=False),
}
# Recorded 6-12 episodes an hour on the 16 GB host over seeds 1-30 (23-25
# with a fixed one-minute holdoff, when regrowing one heavy parse slot sent
# runs back to waiting every two and a half minutes). Fewer than that needs
# heavy parse held down for the whole chat session.
MAX_YIELDS_PER_HOUR = 14
MAX_INDEX_REVERSALS = 12
# Recorded 4-9 over seeds 1-30 (18-20 without the yield); the live limit
# still follows memory that the runs and heavy parse's own steps take.
MAX_LIVE_REVERSALS = 10


@pytest.fixture(scope="module")
def results() -> dict[str, sim.Result]:
    return {name: sim.run(scenario) for name, scenario in SCENARIOS.items()}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_bounded_oscillation_and_no_unsafe_start(results: dict[str, sim.Result], name: str) -> None:
    result = results[name]
    assert sim.reversals_per_window(result.index_limits, TEN_MINUTES) <= MAX_INDEX_REVERSALS
    assert sim.reversals_per_window(result.live_limits, TEN_MINUTES) <= MAX_LIVE_REVERSALS
    assert result.unsafe_starts == 0


def test_yield_episodes(results: dict[str, sim.Result]) -> None:
    hours = STEADY.hours
    assert 0 < results["sync_and_steady_chat_16g"].yield_episodes / hours <= MAX_YIELDS_PER_HOUR
    assert results["sync_and_steady_chat_192g"].yield_episodes == 0
    assert results["sync_alone"].yield_episodes == 0
    assert results["chat_alone"].yield_episodes == 0


@pytest.mark.parametrize("name", ["sync_and_steady_chat_16g", "chat_burst_during_sync"])
def test_chat_waits_stay_under_the_slot_wait(results: dict[str, sim.Result], name: str) -> None:
    result = results[name]
    assert result.refused == 0
    assert result.p95_wait() < sim.SLOT_WAIT_S


def test_a_sync_alone_reaches_the_same_limits_as_without_the_yield(results: dict[str, sim.Result]) -> None:
    without = sim.run(replace(SCENARIOS["sync_alone"], yield_enabled=False))
    assert results["sync_alone"].final_limits == without.final_limits
    assert results["sync_alone"].index_limits == without.index_limits
    assert without.final_limits[Pool.INDEX_HEAVY] == H16.ceilings.index_heavy


def test_the_yield_steadies_the_live_limit_and_shortens_waits(results: dict[str, sim.Result]) -> None:
    with_yield = results["sync_and_steady_chat_16g"]
    without = sim.run(replace(STEADY, yield_enabled=False))
    assert without.yield_episodes == 0
    assert sim.reversals_per_window(with_yield.live_limits, TEN_MINUTES) <= sim.reversals_per_window(
        without.live_limits, TEN_MINUTES,
    )
    assert with_yield.p95_wait() <= without.p95_wait()


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bounds_hold_across_seeds(seed: int) -> None:
    result = sim.run(replace(STEADY, seed=seed))
    assert result.yield_episodes / STEADY.hours <= MAX_YIELDS_PER_HOUR
    assert sim.reversals_per_window(result.live_limits, TEN_MINUTES) <= MAX_LIVE_REVERSALS
    assert sim.reversals_per_window(result.index_limits, TEN_MINUTES) <= MAX_INDEX_REVERSALS
    assert result.p95_wait() < sim.SLOT_WAIT_S


def test_reversal_counter() -> None:
    assert sim.reversals_per_window([1, 2, 3, 3, 2, 3, 2], window=10) == 3
    assert sim.reversals_per_window([1, 2, 1, 2, 1, 2], window=2) == 2
    assert sim.reversals_per_window([5, 5, 5], window=10) == 0
