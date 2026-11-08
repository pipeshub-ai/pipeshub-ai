"""Each byte class is charged by one gate; a permit holder never waits long for memory (PX-2e, T83, R6-1).

Under the measured permit base (0.25 GiB) a kind above it reserves the difference before its parse
permit, as one reservation with the file bytes (the larger of the two, not their sum). The tests run
the real helpers against a real governor, with the heavy working set set to the measured figure.
"""
# ruff: noqa: ANN201, ANN202
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from app.services.messaging import consumer_concurrency as cc
from app.services.resource_governor import policy
from app.services.resource_governor.kinds import ParseKind
from app.services.resource_governor.memory_budget import (
    kind_reservation_cost,
    record_memory_cost,
)
from app.services.resource_governor.memory_domain import permit_covers_here
from app.services.resource_governor.models import ParseTier, Pool
from app.utils.request_context import (
    get_admitted_in,
    reset_admitted_in,
    set_admitted_in,
)
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

if TYPE_CHECKING:
    from collections.abc import Iterator

GIB = 2**30
MIB = 2**20
MEASURED = policy.HeavyWorkingSet(0.25, "measured")


@pytest.fixture
def governor():
    gov = make_test_governor(logger_name="test.kinds", env_parse=4)
    gov.memory_budget.limit = 8 * GIB
    gov._heavy_ws = MEASURED
    yield gov
    gov.close()


@pytest.fixture
def host(governor):
    return SimpleNamespace(governor=governor, logger=logging.getLogger("test.kinds"))


class Spy:
    """Records the order of memory reservations and parse-permit acquisitions."""

    def __init__(self, governor) -> None:
        self.events: list[tuple[str, int]] = []
        budget = governor.memory_budget
        real_reserve, real_release = budget.reserve, budget.release

        async def reserve(n, timeout=None, *, held=0):
            self.events.append(("reserve", n))
            return await real_reserve(n, timeout, held=held)

        def release(n):
            self.events.append(("release", n))
            return real_release(n)

        budget.reserve, budget.release = reserve, release
        gate = governor.gate(Pool.HEAVY_PARSE)
        real_acquire = gate.acquire

        async def acquire(*a, **k):
            self.events.append(("permit", 0))
            return await real_acquire(*a, **k)

        gate.acquire = acquire  # type: ignore[method-assign]

    def names(self) -> list[str]:
        return [name for name, _ in self.events]


# -- the cost ----------------------------------------------------------------------------


def test_excess_is_max_not_sum_with_the_size_estimate() -> None:
    docx = ParseKind.DOCLING_DOCX
    # 100 MB file: the estimate (4 x) is 400 MB; the kind's excess is 1.25 GiB. Together: 1.25 GiB, not 1.65.
    assert record_memory_cost(ParseTier.HEAVY, 100 * MIB) == 400 * MIB
    assert kind_reservation_cost(ParseTier.HEAVY, 100 * MIB, docx, MEASURED) == int(1.25 * GIB)
    # 10 MB file: no estimate is reserved (under the index working set), the whole excess is.
    assert record_memory_cost(ParseTier.HEAVY, 10 * MIB) == 0
    assert kind_reservation_cost(ParseTier.HEAVY, 10 * MIB, docx, MEASURED) == int(1.25 * GIB)
    # A file whose estimate is above the excess keeps the estimate.
    assert kind_reservation_cost(ParseTier.HEAVY, 600 * MIB, docx, MEASURED) == 2400 * MIB


def test_a_small_text_record_reserves_only_the_file_bytes() -> None:
    for kind in (ParseKind.TEXT, ParseKind.CSV, ParseKind.CODE):
        assert kind_reservation_cost(ParseTier.LIGHT, 1000, kind, MEASURED) == 0
        assert kind_reservation_cost(ParseTier.HEAVY, 1000, kind, MEASURED) == 0
    assert kind_reservation_cost(ParseTier.HEAVY, 10 * MIB, ParseKind.PDF, MEASURED) == int(0.25 * GIB)


def test_a_light_text_record_reserves_its_parent_growth() -> None:
    size = 20 * MIB  # markdown of this size grew the parent by about 990 MiB
    cost = kind_reservation_cost(ParseTier.LIGHT, size, ParseKind.TEXT, MEASURED)
    assert cost > record_memory_cost(ParseTier.LIGHT, size) == 80 * MIB
    assert cost + int(policy.INDEX_LIGHT_WORKING_SET_GB * GIB) >= 991 * MIB
    heavy = kind_reservation_cost(ParseTier.HEAVY, size, ParseKind.TEXT, MEASURED)
    assert heavy + int(0.25 * GIB) >= 991 * MIB
    assert cost >= heavy, "no permit holds the base for a light record"


def test_a_light_text_record_is_unchanged_without_the_measured_base() -> None:
    for source in ("explicit", "fallback", "docling"):
        ws = policy.HeavyWorkingSet(1.5, source)
        for tier in ParseTier:
            assert kind_reservation_cost(tier, 20 * MIB, ParseKind.TEXT, ws) == record_memory_cost(tier, 20 * MIB)


def test_a_huge_text_record_still_reserves_at_least_its_estimate() -> None:
    assert kind_reservation_cost(ParseTier.HEAVY, 100 * MIB, ParseKind.CODE, MEASURED) >= 400 * MIB
    assert kind_reservation_cost(ParseTier.HEAVY, 10 * MIB, ParseKind.PDF, MEASURED) == int(0.25 * GIB)


def test_no_extra_cost_without_the_measured_base() -> None:
    for source in ("explicit", "fallback", "docling"):
        ws = policy.HeavyWorkingSet(1.5, source)
        for kind in ParseKind:
            assert kind_reservation_cost(ParseTier.HEAVY, 50 * MIB, kind, ws) == record_memory_cost(ParseTier.HEAVY, 50 * MIB)


def test_no_kind_is_the_cost_of_today() -> None:
    assert kind_reservation_cost(ParseTier.HEAVY, 10 * MIB, None, MEASURED) == 0
    assert kind_reservation_cost(ParseTier.HEAVY, 200 * MIB, None, None) == 800 * MIB


# -- one gate per byte class -----------------------------------------------------------------


async def test_excess_is_reserved_before_the_permit(host, governor) -> None:
    spy = Spy(governor)
    memory = await cc.reserve_record_memory(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX)
    assert memory is not None and memory.cost == int(1.25 * GIB)
    slot = await cc.acquire_parsing_slot(
        host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX, reserved_bytes=memory.cost
    )
    assert spy.names() == ["reserve", "permit"], "the permit takes no memory of its own, and none is awaited behind it"
    cc.release_admission(slot)
    cc.release_admission(memory)


async def test_each_memory_class_is_charged_by_one_gate(host, governor) -> None:
    budget = governor.memory_budget
    memory = await cc.reserve_record_memory(host, ParseTier.HEAVY, 100 * MIB, kind=ParseKind.DOCLING_DOCX)
    assert budget.stats()["reserved_bytes"] == int(1.25 * GIB)
    again = await cc.reserve_record_memory(
        host, ParseTier.HEAVY, 100 * MIB, downloaded=True, current=memory, kind=ParseKind.DOCLING_DOCX
    )
    assert again is memory, "the reservation covers the download: kept, not taken twice"
    slot = await cc.acquire_parsing_slot(
        host, ParseTier.HEAVY, 100 * MIB, kind=ParseKind.DOCLING_DOCX, reserved_bytes=again.cost
    )
    assert budget.stats()["reserved_bytes"] == int(1.25 * GIB), "the permit added nothing"
    cc.release_admission(slot)
    assert budget.stats()["reserved_bytes"] == int(1.25 * GIB), "the permit held no bytes"
    cc.release_admission(again)
    assert budget.stats()["reserved_bytes"] == 0


async def test_excess_is_released_with_the_admission(host, governor) -> None:
    budget = governor.memory_budget
    # A caller that reserved nothing (the parsing route's shape): the permit takes the excess.
    slot = await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX)
    assert budget.stats()["reserved_bytes"] == int(1.25 * GIB)
    cc.release_admission(slot)
    assert budget.stats()["reserved_bytes"] == 0
    cc.release_admission(slot)  # idempotent: never released twice
    assert budget.stats()["reserved_bytes"] == 0


async def test_a_reservation_that_covers_the_excess_makes_the_permit_reserve_nothing(host, governor) -> None:
    spy = Spy(governor)
    slot = await cc.acquire_parsing_slot(
        host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.PDF, reserved_bytes=int(0.25 * GIB)
    )
    assert "reserve" not in spy.names()
    cc.release_admission(slot)
    # Reserved less than the excess: the permit takes only the missing part.
    spy.events.clear()
    slot = await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.PDF, reserved_bytes=100 * MIB)
    assert spy.events[1] == ("reserve", int(0.25 * GIB) - 100 * MIB)
    cc.release_admission(slot)


async def test_a_stale_size_reserves_the_larger_one_and_returns_the_smaller(host, governor) -> None:
    first = await cc.reserve_record_memory(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.PDF)
    assert first is not None and first.cost == int(0.25 * GIB)
    second = await cc.reserve_record_memory(
        host, ParseTier.HEAVY, 400 * MIB, downloaded=True, current=first, kind=ParseKind.PDF
    )
    assert second is not None and second.cost == 1600 * MIB and first.released
    assert governor.memory_budget.stats()["reserved_bytes"] == 1600 * MIB
    cc.release_admission(second)


async def test_a_light_tier_record_reserves_no_excess(host, governor) -> None:
    memory = await cc.reserve_record_memory(host, ParseTier.LIGHT, 1000, kind=ParseKind.TEXT)
    assert memory is None
    slot = await cc.acquire_parsing_slot(host, ParseTier.LIGHT, 1000, kind=ParseKind.TEXT)
    assert governor.memory_budget.stats()["reserved_bytes"] == 0
    cc.release_admission(slot)


async def test_download_ahead_bound_unchanged_by_kind_excess(host, governor) -> None:
    ahead = governor.download_ahead
    assert ahead.stats()["limit"] if "limit" in ahead.stats() else True
    places = []
    memories = []
    for _ in range(2):
        places.append(await cc.acquire_download_slot(host, ParseTier.HEAVY, timeout=0.5))
        memories.append(await cc.reserve_record_memory(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX))
    with pytest.raises(cc.ParseAdmissionTimeout):
        await cc.acquire_download_slot(host, ParseTier.HEAVY, timeout=0.1)
    # The bound is a count of places; the bytes are the reservations, 1.25 GiB each, and nothing more.
    assert governor.memory_budget.stats()["reserved_bytes"] == 2 * int(1.25 * GIB)
    for item in (*places, *memories):
        cc.release_admission(item)
    assert governor.memory_budget.stats()["reserved_bytes"] == 0


# -- R6-1: no hold-and-wait ---------------------------------------------------------------------


async def test_permit_holder_memory_wait_is_bounded(host, governor, monkeypatch) -> None:
    """A caller that reserved nothing and finds memory spoken for gives its permit back after the bound."""
    monkeypatch.setattr(cc, "EXCESS_WAIT_SECONDS", 0.3)
    budget = governor.memory_budget
    budget.limit = 2 * GIB
    assert await budget.reserve(int(1.5 * GIB))  # someone else holds most of it
    gate = governor.gate(Pool.HEAVY_PARSE)
    started = time.monotonic()
    with pytest.raises(cc.ParseAdmissionTimeout):
        await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, timeout=30.0, kind=ParseKind.DOCLING_DOCX)
    assert time.monotonic() - started < 3.0
    assert gate.in_use == 0, "the permit went back"
    assert budget.stats()["reserved_bytes"] == int(1.5 * GIB), "and took nothing"


async def test_permit_holder_never_waits_for_memory_beyond_the_bound(host, governor, monkeypatch) -> None:
    """The cycle of the review's probe: X reserved at download and waits for a permit; A holds the permit."""
    monkeypatch.setattr(cc, "EXCESS_WAIT_SECONDS", 0.3)
    budget = governor.memory_budget
    budget.limit = int(2.5 * GIB)
    registry = governor._registry
    registry.set(Pool.HEAVY_PARSE, 1)
    x = await cc.reserve_record_memory(host, ParseTier.HEAVY, 400 * MIB, kind=ParseKind.PDF)  # 1.6 GiB, waits for a permit
    assert x is not None
    started = time.monotonic()
    with pytest.raises(cc.ParseAdmissionTimeout):
        await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, timeout=30.0, kind=ParseKind.DOCLING_DOCX)
    assert time.monotonic() - started < 3.0
    cc.release_admission(x)


async def test_requeued_record_gets_the_permit_after_the_reservation_frees(host, governor, monkeypatch) -> None:
    monkeypatch.setattr(cc, "EXCESS_WAIT_SECONDS", 0.2)
    budget = governor.memory_budget
    budget.limit = 2 * GIB
    assert await budget.reserve(int(1.5 * GIB))
    with pytest.raises(cc.ParseAdmissionTimeout):
        await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, timeout=30.0, kind=ParseKind.DOCLING_DOCX)
    budget.release(int(1.5 * GIB))  # the holder finished
    slot = await asyncio.wait_for(
        cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, timeout=30.0, kind=ParseKind.DOCLING_DOCX), 3.0
    )
    cc.release_admission(slot)


async def test_a_cancelled_excess_wait_gives_the_permit_back(host, governor) -> None:
    budget = governor.memory_budget
    budget.limit = 2 * GIB
    assert await budget.reserve(int(1.5 * GIB))
    gate = governor.gate(Pool.HEAVY_PARSE)
    task = asyncio.create_task(
        cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX)
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert gate.in_use == 0


# -- what "covered by a permit" means --------------------------------------------------------------


async def test_covered_means_a_heavy_permit_with_the_kind_excess(host, governor) -> None:
    assert not permit_covers_here()
    stamp = set_admitted_in("a-memory-domain")  # what a light permit, or a request from another service, leaves behind
    assert get_admitted_in() == "a-memory-domain"
    assert not permit_covers_here(), "an admitted-in stamp covers nothing"
    reset_admitted_in(stamp)
    light = await cc.acquire_parsing_slot(host, ParseTier.LIGHT, 1000, kind=ParseKind.TEXT)
    assert not permit_covers_here(), "a light permit covers nothing"
    cc.release_admission(light)
    heavy = await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.EXCEL_LOAD)
    assert permit_covers_here()
    cc.release_admission(heavy)
    assert not permit_covers_here()


# -- off ----------------------------------------------------------------------------------------------


async def test_off_reserves_no_excess_and_makes_no_budget_call(governor, host) -> None:
    governor._heavy_ws = policy.HeavyWorkingSet(1.5, "fallback")
    spy = Spy(governor)
    memory = await cc.reserve_record_memory(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX)
    assert memory is None
    slot = await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 10 * MIB, kind=ParseKind.DOCLING_DOCX)
    assert spy.names() == ["permit"], "no MemoryBudget call at all"
    cc.release_admission(slot)
    assert spy.names() == ["permit"]


@pytest.fixture(autouse=True)
def _reset_context() -> Iterator[None]:
    yield
