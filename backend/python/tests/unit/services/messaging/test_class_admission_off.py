"""With ``PARSE_ADMISSION`` unset or ``legacy`` the admission functions behave as they did."""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest

from app.modules.parse_engine import wiring
from app.services.messaging import consumer_concurrency as cc
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

MIB = 1 << 20


@pytest.fixture(autouse=True)
def _off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PARSE_ADMISSION", raising=False)
    wiring.reset_for_tests()


def host() -> SimpleNamespace:
    return SimpleNamespace(
        governor=make_test_governor(logger_name="test.off"), logger=logging.getLogger("test.off"),
        priority_lane=None, gate_waiters=cc.GateWaiters(),
    )


def test_dispatch_tiers_are_tiers_whatever_the_size() -> None:
    h = host()
    for ext, size in (("pdf", None), ("pdf", 500 * MIB), ("xlsx", 1), ("md", 5 * MIB)):
        payload = {"extension": ext, "mimeType": "", "sizeInBytes": size}
        assert isinstance(cc.dispatch_tier(h, SimpleNamespace(payload=payload)), ParseTier)
    assert cc.dispatch_tier(h, SimpleNamespace(payload={"extension": "pdf"})) is ParseTier.HEAVY


def test_no_class_context_and_no_extra_arguments() -> None:
    assert cc.class_context(host(), {"orgId": "o", "extension": "pdf"}) is None
    assert cc.class_kwargs(None, size_bytes=5) == {}


def test_an_index_permit_has_no_class_gate_and_reclass_is_inert() -> None:
    async def run() -> None:
        h = host()
        a = await cc.acquire_index_slot(h, ParseTier.HEAVY)
        assert getattr(a, "ordered", None) is None
        assert h.governor.gate(Pool.INDEX_HEAVY).in_use == 1
        assert cc.reclass_index_slot(a, None, 500 * MIB) is None
        cc.release_admission(a)
        assert h.governor.gate(Pool.INDEX_HEAVY).in_use == 0

    asyncio.run(run())


def test_the_dispatch_budget_keeps_its_two_tiers_and_counts_by_tier() -> None:
    h = host()
    cc.GateWaiterToken(h, ParseTier.HEAVY)
    budget = cc.dispatch_budget(h)
    assert set(budget.tiers) == set(ParseTier)
    assert h.gate_waiters.snapshot() == {ParseTier.HEAVY: 1, ParseTier.LIGHT: 0}


def test_the_memory_queue_order_is_unchanged_without_ahead() -> None:
    async def run() -> list[str]:
        budget = make_test_governor(logger_name="test.off.mem").memory_budget
        budget.limit = 4 << 30
        order: list[str] = []
        await budget.reserve(4 << 30, 1)

        async def want(name: str, n: int) -> None:
            if await budget.reserve(n, 3):
                order.append(name)
                budget.release(n)

        tasks = [asyncio.create_task(want("big", 3 << 30))]
        await asyncio.sleep(0.05)
        tasks.append(asyncio.create_task(want("small", 1 << 30)))
        await asyncio.sleep(0.05)
        budget.release(4 << 30)
        await asyncio.gather(*tasks)
        return order

    assert asyncio.run(run()) == ["big", "small"]  # first come, first served, as before


def test_a_pool_is_built_the_legacy_way(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.modules.parsers import parse_pool

    monkeypatch.setenv("PARSE_POOL_WORKERS", "1")
    parse_pool.set_resource_governor(make_test_governor(logger_name="test.off.pool"))
    try:
        pool = parse_pool._pool()
        assert type(pool) is parse_pool._ParsePool
        assert parse_pool.pool_stats() == {}
    finally:
        parse_pool.shutdown_parse_pool()
        parse_pool.set_resource_governor(None)


def test_every_parse_start_is_still_spaced_by_the_rate_limiter() -> None:
    """Off: no start skips the heavy start-rate limiter (only the class path passes ``spaced=False``)."""
    from app.services.resource_governor import StartRateLimiter

    async def run() -> int:
        h = host()
        h.governor._registry.set(Pool.HEAVY_PARSE, 8)
        h.governor.gate(Pool.HEAVY_PARSE)._rate_limiter = StartRateLimiter(1000.0, 1)
        first = await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 1 * MIB, timeout=1)
        timed_out = 0
        try:
            await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 1 * MIB, timeout=0.2)
        except cc.ParseAdmissionTimeout:
            timed_out = 1
        cc.release_admission(first)
        return timed_out

    assert asyncio.run(run()) == 1


def test_the_requeue_stamps_nothing_without_classes() -> None:
    assert cc.first_admission_kwargs(cc.ParseAdmissionTimeout("heavy_parse", 5.0)) == {}
