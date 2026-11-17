"""The real live limit, per-run check and yield in closed loop with indexing.

One simulated hour per scenario on ``live_sim`` (real ``LiveLimit``, real
``IndexingYield`` and ``YieldSignal``, real ``policy.next_limits``). The
bounds are those of ``test_sandbox_indexing_closed_loop`` where they apply;
the other figures were recorded from the first runs and guard regressions.
No start may leave the host below the hard floor once its memory is in.
"""
from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

import pytest

from tests.unit.services.resource_governor import sim_host
from tests.unit.services.resource_governor.qa import live_sim as sim

if TYPE_CHECKING:
    from pathlib import Path

TEN_MINUTES = 600 // sim.INTERVAL
GiB = sim.GiB


def _users(share: float, ceiling: int) -> int:
    return max(1, round(share * ceiling * (15.0 + 45.0) / 15.0))


STEADY = sim.Scenario(sim.HOST_16, users=_users(0.3, 6))
SCENARIOS = {
    "steady_16g": STEADY,
    # Twelve extra users for one minute in every ten.
    "bursty_16g": replace(STEADY, bursts=tuple((600.0 * i, 600.0 * i + 60, 12) for i in range(6))),
    # Something outside the app grows by 6 GiB over the hour.
    "leak_16g": replace(STEADY, leak_bytes=6 * GiB),
    "steady_4g": sim.Scenario(sim.HOST_4, users=4),
    "steady_192g": sim.Scenario(sim.HOST_192, users=_users(0.3, 72)),
    "four_indexing_workers_16g": replace(STEADY, index_workers=4),
    "chat_alone_16g": replace(STEADY, sync=False),
}


@pytest.fixture(scope="module")
def results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, sim.Result]:
    return {name: sim.run(scenario, tmp_path_factory.mktemp(name) / "state") for name, scenario in SCENARIOS.items()}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_no_start_leaves_the_host_below_the_floor(results: dict[str, sim.Result], name: str) -> None:
    result = results[name]
    assert result.overcommitted == 0
    assert all(result.floor <= limit <= result.ceiling for limit in result.live_limits)
    assert sim_host.reversals_per_window(result.live_limits, TEN_MINUTES) <= 10
    assert sim_host.reversals_per_window(result.index_limits, TEN_MINUTES) <= 12


@pytest.mark.parametrize("name", ["steady_16g", "bursty_16g", "four_indexing_workers_16g", "chat_alone_16g"])
def test_chat_is_served_within_the_slot_wait(results: dict[str, sim.Result], name: str) -> None:
    result = results[name]
    assert result.refused == 0 and result.p95_wait() < sim.SLOT_WAIT_S


def test_yield_episodes(results: dict[str, sim.Result]) -> None:
    assert 0 < results["steady_16g"].yield_episodes <= 14
    assert results["bursty_16g"].yield_episodes <= 20
    assert results["steady_192g"].yield_episodes == 0
    assert results["chat_alone_16g"].yield_episodes == 0


def test_a_large_host_never_binds(results: dict[str, sim.Result]) -> None:
    result = results["steady_192g"]
    assert set(result.live_limits) == {result.ceiling} and result.refused == 0


def test_a_leak_walks_the_limit_down_to_min_without_an_unsafe_start(results: dict[str, sim.Result]) -> None:
    result = results["leak_16g"]
    assert result.live_limits[-1] == result.floor
    assert result.overcommitted == 0


def test_a_4_gib_host_during_a_sync_starts_no_run_below_the_floor(results: dict[str, sim.Result]) -> None:
    # Recorded: the sync alone keeps free memory under the 1.5 GiB a run
    # needs, so every run waits out the slot wait. The per-run check is what
    # keeps it from starting anyway.
    result = results["steady_4g"]
    assert result.ceiling == result.floor == 2
    assert result.starts == 0 and result.refused > 0


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_bounds_hold_across_seeds(tmp_path: Path, seed: int) -> None:
    result = sim.run(replace(STEADY, seed=seed), tmp_path / "state")
    assert result.overcommitted == 0 and result.refused == 0
    assert result.yield_episodes <= 14
    assert sim_host.reversals_per_window(result.live_limits, TEN_MINUTES) <= 10


# Runs that use their full 512 MiB on a 16-CPU host (memory binds), with bursts.
FULL_USE = sim.Scenario(
    replace(sim.HOST_16, cpus=16), users=40, run_used=512 * sim.MiB, ramp_s=4.0,
    bursts=tuple((600.0 * i, 600.0 * i + 60, 30) for i in range(6)),
)


@pytest.mark.parametrize("k", [1.0, 2.0, 4.0])
def test_overcommit_never_takes_the_host_below_the_hard_floor(tmp_path: Path, k: float) -> None:
    # Recorded before runs were checked at full size: 83 starts over the floor
    # and -0.5 GiB free at 4x.
    result = sim.run(replace(FULL_USE, overcommit=k), tmp_path / "state")
    assert result.overcommitted == 0
    assert result.min_available >= GiB  # the hard floor on 16 GiB


@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_four_workers_holding_heavy_parse_together_yield_less(tmp_path: Path, seed: int) -> None:
    # Recorded, seeds 1/2/3/7: 36/31/25/23 yields an hour and p95 16/13/20/15 s
    # without the shared cap; 11/8/9/11 and 4/2/7/5 s with it.
    four = replace(STEADY, index_workers=4, seed=seed)
    before = sim.run(replace(four, yield_heavy_cap=False), tmp_path / "a")
    after = sim.run(four, tmp_path / "b")
    assert after.yield_episodes <= 14 < before.yield_episodes
    assert after.p95_wait() < before.p95_wait()
    assert after.overcommitted == 0 and after.refused == 0


@pytest.mark.parametrize("workers", [1, 2])
def test_the_shared_cap_changes_nothing_for_one_or_two_workers(tmp_path: Path, workers: int) -> None:
    scenario = replace(STEADY, index_workers=workers)
    capped = sim.run(scenario, tmp_path / "a")
    uncapped = sim.run(replace(scenario, yield_heavy_cap=False), tmp_path / "b")
    assert (capped.yield_episodes, capped.waits, capped.live_limits, capped.heavy_totals) == (
        uncapped.yield_episodes, uncapped.waits, uncapped.live_limits, uncapped.heavy_totals,
    )


@pytest.mark.parametrize("workers", [1, 4])
def test_indexing_recovers_once_chat_stops(tmp_path: Path, workers: int) -> None:
    # Chat for the first half hour only, then the sync alone.
    chat_then_none = replace(STEADY, users=0, bursts=((0.0, 1800.0, STEADY.users),), index_workers=workers)
    result = sim.run(chat_then_none, tmp_path / "a")
    alone = sim.run(replace(chat_then_none, bursts=()), tmp_path / "b")
    assert result.yield_episodes > 0
    tail = slice(-40, None)  # the last ten minutes
    assert max(result.index_limits[tail]) == max(alone.index_limits[tail])
    assert max(result.heavy_totals[tail]) == max(alone.heavy_totals[tail])
