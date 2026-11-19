"""``submit(..., charge_bytes=)``: the memory a job is charged to lane admission, in place of the kind-and-size figure."""
from __future__ import annotations

import pytest

from app.modules.parsers import parse_pool
from app.services.resource_governor.kinds import ParseKind, lane_charge_bytes

MIB = 1 << 20


def test_without_a_stated_charge_the_demand_is_the_kind_and_size_figure() -> None:
    demand = parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB)
    assert demand.mem_bytes == lane_charge_bytes(ParseKind.PDF_LAYOUT, 5 * MIB)
    assert parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB, None) == demand


def test_a_stated_charge_replaces_it_even_for_a_job_of_no_known_kind() -> None:
    assert parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB, 7 * MIB).mem_bytes == 7 * MIB
    assert parse_pool._lane_demand(None, 0, 3 * MIB).mem_bytes == 3 * MIB


def test_a_negative_charge_reserves_nothing_extra() -> None:
    assert parse_pool._lane_demand(None, 0, -1).mem_bytes == 0


@pytest.mark.asyncio
async def test_submit_reserves_the_stated_charge_and_carries_it_on_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_reserve(permit: object, demand: object, deadline_s: float) -> None:
        seen["mem"] = demand.mem_bytes

    class _Pool:
        async def submit(self, job: object) -> str:
            seen["job_charge"] = job.charge_bytes
            return "ok"

    monkeypatch.setattr(parse_pool, "reserve_or_adopt", fake_reserve)
    monkeypatch.setattr(parse_pool, "_pool", lambda **kw: _Pool())
    monkeypatch.setattr(parse_pool.wiring, "classes_enabled", lambda: False)

    assert await parse_pool.submit(len, [], kind=ParseKind.PDF_LAYOUT, size=MIB, charge_bytes=9 * MIB) == "ok"
    assert seen == {"mem": 9 * MIB, "job_charge": 9 * MIB}


def test_a_job_built_without_a_charge_carries_none() -> None:
    job = parse_pool._Job(len, (), "x", 0)
    assert job.charge_bytes is None


@pytest.mark.parametrize("kind", [*ParseKind, None])
@pytest.mark.parametrize("size", [0, 1, 5 * MIB, 600 * MIB])
def test_with_no_charge_the_off_path_demand_is_exactly_the_legacy_one(kind: object, size: int) -> None:
    """``charge_bytes=None`` (the default, with ``PARSE_LANES`` off) must charge what the code charged before the
    parameter existed: one parse token and the kind-and-size memory figure, or nothing for a job of no known kind."""
    import dataclasses

    from app.capacity.budget_policy import Demand, Family

    legacy_memory = lane_charge_bytes(kind, size) if isinstance(kind, ParseKind) else 0
    legacy = Demand(Family.PARSE, 1)
    if legacy_memory:
        legacy = dataclasses.replace(legacy, mem_bytes=legacy_memory)
    assert parse_pool._lane_demand(kind, size) == legacy
    assert parse_pool._lane_demand(kind, size, None) == legacy


class TestPrecedenceWithADemandThatCarriesItsOwnMemory:
    """With ``PARSE_ADMISSION=classes`` a demand may already hold a memory figure chosen for it (an inspection's 0.1 GiB).
    The kind's lane charge never replaces it, but a stated ``charge_bytes`` does; a stated charge of nothing leaves it."""

    OWN = 100 * MIB // 1000

    @staticmethod
    def _demand_with_memory(monkeypatch: pytest.MonkeyPatch, memory: int) -> None:
        import dataclasses

        from app.capacity.budget_policy import Demand, Family

        own = dataclasses.replace(Demand(Family.PARSE, 1), mem_bytes=memory)
        monkeypatch.setattr(parse_pool.wiring, "classes_enabled", lambda: True)
        monkeypatch.setattr(parse_pool.wiring, "lane_demand", lambda kind, cls: own)

    def test_the_kind_charge_does_not_replace_a_figure_the_demand_carries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._demand_with_memory(monkeypatch, self.OWN)
        assert parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB).mem_bytes == self.OWN

    def test_a_stated_charge_replaces_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._demand_with_memory(monkeypatch, self.OWN)
        assert parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB, 7 * MIB).mem_bytes == 7 * MIB

    @pytest.mark.parametrize("charge", [0, -1])
    def test_a_stated_charge_of_nothing_leaves_it(self, monkeypatch: pytest.MonkeyPatch, charge: int) -> None:
        self._demand_with_memory(monkeypatch, self.OWN)
        assert parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB, charge).mem_bytes == self.OWN

    def test_with_no_memory_of_its_own_the_kind_charge_applies_as_before(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._demand_with_memory(monkeypatch, 0)
        assert parse_pool._lane_demand(ParseKind.PDF_LAYOUT, 5 * MIB).mem_bytes == lane_charge_bytes(ParseKind.PDF_LAYOUT, 5 * MIB)
