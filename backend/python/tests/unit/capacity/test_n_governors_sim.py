"""N cells on one cgroup behave like one governor with N times the capacity (real ``next_limits``, elected controller, ledger)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.capacity.member_budget import MemberBudget
from tests.support.n_governor_sim import Result, run

if TYPE_CHECKING:
    from pathlib import Path

TICKS = 120  # 30 minutes at the governor's 15 s sample


def _dir(tmp_path: Path, name: str) -> Path:
    d = tmp_path / name
    d.mkdir(mode=0o700)
    return d


@pytest.fixture(scope="module")
def single_cpu_bound(tmp_path_factory: pytest.TempPathFactory) -> Result:
    return run(1, 128, 1024, TICKS, use_link=False)


@pytest.mark.parametrize("cells", [2, 4, 8])
def test_cpu_bound(cells: int, tmp_path: Path, single_cpu_bound) -> None:
    """Time to the ceiling within 1.5x of one governor, the same utilisation within 10 %, and no wider a swing than 2 x N more."""
    many = run(cells, 128, 1024, TICKS, directory=_dir(tmp_path, "l"))
    level = 0.6 * single_cpu_bound.heavy_total()[-1]
    one, n = single_cpu_bound.first_at_or_above(level), many.first_at_or_above(level)
    assert one is not None and n is not None, "the ramp must reach the level inside 30 minutes"
    assert n <= 1.5 * one, f"{cells} cells reached {level:.0f} at tick {n}; one governor at {one}"
    assert abs(many.mean_util() - single_cpu_bound.mean_util()) <= 0.10
    assert many.amplitude() <= single_cpu_bound.amplitude() + 2 * cells


def test_memory_bound_gradual(tmp_path: Path) -> None:
    """128 CPU / 64 GB with the real working set at 2x the estimate and units that allocate gradually: peak raw
    memory is at most the single governor's peak plus 5 %, and never reaches 1.0 (the OOM kill of per-cell warm starts)."""
    single = run(1, 128, 64, TICKS, ws_real_gb=3.0, use_link=False)
    for cells in (4, 8):
        many = run(cells, 128, 64, TICKS, ws_real_gb=3.0, directory=_dir(tmp_path, f"m{cells}"))
        assert many.peak_raw() <= single.peak_raw() + 0.05
        assert many.peak_raw() < 1.0


def test_independent_governors_without_the_controller_reach_the_oom_line() -> None:
    """The control for the test above: N governors that each ramp on their own (no elected controller, no shared state)
    push raw memory to 1.0 and beyond at 128 CPU / 64 GB, which is what the container controller prevents."""
    single = run(1, 128, 64, TICKS, ws_real_gb=3.0, use_link=False)
    independent = run(8, 128, 64, TICKS, ws_real_gb=3.0, use_link=False)
    assert independent.peak_raw() >= 1.0 > single.peak_raw()


def test_controller_container_demand(tmp_path: Path) -> None:
    """The controller is an idle process while the other cells are loaded: the heavy limit still reaches 70 of 112
    inside 30 minutes, because the controller sums every process's demand."""
    r = run(8, 128, 1024, TICKS, directory=_dir(tmp_path, "i"), idle_controller=True)
    reached = r.first_at_or_above(70)
    assert reached is not None and r.heavy_total()[-1] >= 100


def test_a_controller_that_sees_only_its_own_demand_stalls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The negative control: feed the elected controller its own process's demand only and the heavy limit stays low."""
    real = MemberBudget.container_demand

    def own_only(self: MemberBudget, **kw: object) -> dict:
        everything = real(self, **kw)
        slot = self._read_slot(self._slot)["demand"]
        return {p: slot.get(p, (0.0, 0, 0, 0)) for p in everything}

    monkeypatch.setattr(MemberBudget, "container_demand", own_only)
    r = run(8, 128, 1024, TICKS, directory=_dir(tmp_path, "o"), idle_controller=True)
    assert (r.first_at_or_above(70) is None) or r.heavy_total()[-1] < 70
