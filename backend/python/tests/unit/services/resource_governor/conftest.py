from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor.lanes import LaneBudget, plan_lanes
from tests.unit.lane_gauge import FakeClock, Mem

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator
    from pathlib import Path

GIB = 2**30


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def mem() -> Mem:
    return Mem(limit=10 * GIB, raw=1 * GIB)


@pytest.fixture
def lane_dir(tmp_path: Path) -> Path:
    return tmp_path / "lanes"


@pytest.fixture
def make_budget(
    lane_dir: Path, clock: FakeClock, mem: Mem
) -> Iterator[Callable[..., LaneBudget]]:
    """Budgets on one directory; each stands in for a process (own descriptors)."""
    made: list[LaneBudget] = []

    def factory(lanes: int = 8, **options: object) -> LaneBudget:
        options.setdefault("clock", clock)
        options.setdefault("memory_reader", mem)
        options.setdefault("view_ttl", 0.0)
        options.setdefault("alive", lambda pid: True)
        plan = plan_lanes(cpus=lanes * 2, heavy=lanes * 2)
        assert plan.lanes == lanes
        budget = LaneBudget.open(plan, lane_dir, **options)
        made.append(budget)
        return budget

    yield factory
    for budget in made:
        budget.close()
