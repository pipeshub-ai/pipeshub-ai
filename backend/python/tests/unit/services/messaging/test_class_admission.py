"""The consumers' admission points under ``PARSE_ADMISSION=classes`` ."""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from app.capacity.classes import CostClass
from app.modules.parse_engine import pools, wiring
from app.services.messaging import consumer_concurrency as cc
from app.services.resource_governor.models import ParseTier, Pool
from tests.support.fake_member_budget import FakeMemberBudget
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

MIB = 1 << 20
S, L, H = CostClass.SMALL, CostClass.LARGE, CostClass.HUGE


@pytest.fixture(autouse=True)
def classes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.delenv("PARSE_LANES", raising=False)
    monkeypatch.delenv("FAIR_SCHEDULING_PARALLEL_PARTITIONS", raising=False)
    wiring.reset_for_tests()


def host(**kw: object) -> SimpleNamespace:
    return SimpleNamespace(
        governor=make_test_governor(logger_name="test.cls"), logger=logging.getLogger("test.cls"),
        priority_lane=None, gate_waiters=cc.GateWaiters(), **kw,
    )


def msg(ext: str, size: int | None = None, org: str = "o") -> SimpleNamespace:
    payload = {"extension": ext, "mimeType": "", "orgId": org}
    if size:
        payload["sizeInBytes"] = size
    return SimpleNamespace(payload=payload)


def test_dispatch_levels() -> None:
    h = host()
    assert cc.dispatch_tier(h, msg("md")) is ParseTier.LIGHT
    assert cc.dispatch_tier(h, msg("pdf")) is S
    assert cc.dispatch_tier(h, msg("pdf", 150 * MIB)) is L
    assert cc.effective_index_tier(h, L) is ParseTier.HEAVY


def test_a_light_record_on_a_host_without_a_light_budget_joins_the_heavy_pool_as_small() -> None:
    h = host()
    h.governor = SimpleNamespace(ceilings=SimpleNamespace(index_light=0))
    assert cc.dispatch_tier(h, msg("md")) is S


def test_class_budget() -> None:
    """a small record is dispatchable while the large class is at its ceiling."""
    h = host()
    ceiling = cc.dispatch_budget(h).tiers[L].ceiling
    for _ in range(ceiling):
        h.gate_waiters.add(L)
    budget = cc.dispatch_budget(h)
    assert not budget.allows(L)
    assert budget.allows(S)
    assert budget.allows(ParseTier.LIGHT)
    assert set(budget.tiers) == {ParseTier.LIGHT, S, L, H}
    assert budget.tiers[L].ceiling < budget.total_ceiling
    assert budget.tiers[S].ceiling == budget.total_ceiling


def test_without_classes_the_budget_has_two_tiers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    assert set(cc.dispatch_budget(host()).tiers) == set(ParseTier)


def test_a_class_waiter_counts_in_its_own_bucket() -> None:
    h = host()
    token = cc.GateWaiterToken(h, L)
    assert h.gate_waiters.count(L) == 1 and h.gate_waiters.count(S) == 0 and h.gate_waiters.count() == 1
    token.admit()
    assert h.gate_waiters.count() == 0


async def _index(h: object, level: object, ctx: object = None) -> cc.Admission:
    return await cc.acquire_index_slot(h, level, **cc.class_kwargs(ctx))


def test_a_small_record_gets_an_index_permit_while_the_heavy_pool_is_full_of_large_ones() -> None:
    """An index permit is held by a small record while big work waits."""

    async def run() -> float:
        h = host()
        limit = h.governor.limit(Pool.INDEX_HEAVY)
        ctx = wiring.class_context(msg("pdf").payload)
        large = [asyncio.create_task(_index(h, L, ctx)) for _ in range(limit * 2)]
        await asyncio.sleep(0.2)
        held = [t.result() for t in large if t.done()]
        assert 0 < len(held) < limit  # the large class stops short of the whole pool
        t0 = time.monotonic()
        small = await asyncio.wait_for(_index(h, S, ctx), 1.0)
        waited = time.monotonic() - t0
        cc.release_admission(small)
        for a in held:
            cc.release_admission(a)
        for t in large:
            t.cancel()
        await asyncio.gather(*large, return_exceptions=True)
        return waited

    assert asyncio.run(run()) < 0.2


def test_a_large_record_still_gets_its_guaranteed_share() -> None:
    """The first slot a small record frees goes to the waiting large record, not to the 40 small ones queued before it."""

    async def run() -> str:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)
        small = [asyncio.create_task(_index(h, S, ctx)) for _ in range(40)]
        await asyncio.sleep(0.1)
        running = [t.result() for t in small if t.done()]
        big = asyncio.create_task(_index(h, L, ctx))
        await asyncio.sleep(0.05)
        assert not big.done()  # the pool is full
        cc.release_admission(running[0])
        await asyncio.wait_for(big, 1.0)  # large's guarantee: it is the next grant
        cc.release_admission(big.result())
        for t in small:
            t.cancel()
        results = await asyncio.gather(*small, return_exceptions=True)
        for r in results:
            if isinstance(r, cc.Admission):
                cc.release_admission(r)
        return "ok"

    assert asyncio.run(run()) == "ok"


def test_the_index_permit_moves_to_its_class_when_the_size_is_known() -> None:
    async def run() -> tuple[int, int, int, int]:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)
        a = await _index(h, S, ctx)
        gate = wiring.index_gate(h.governor)
        before = (gate.running(S), gate.running(L))
        assert cc.reclass_index_slot(a, ctx, 150 * MIB) is L
        after = (gate.running(S), gate.running(L))
        cc.release_admission(a)
        return (*before, *after)

    assert asyncio.run(run()) == (1, 0, 0, 1)


def test_reclass_is_a_no_op_for_a_legacy_permit() -> None:
    async def run() -> None:
        h = host()
        a = await cc.acquire_index_slot(h, ParseTier.HEAVY)
        assert cc.reclass_index_slot(a, None, 150 * MIB) is None
        cc.release_admission(a)

    asyncio.run(run())


def test_a_small_pdf_takes_a_docling_ticket_and_no_cpu_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """T3: a Docling-routed small PDF holds no HEAVY_PARSE permit and reserves no CPU token."""
    budget = FakeMemberBudget(4)
    monkeypatch.setattr("app.capacity.runtime.member_budget", lambda *_a, **_k: budget)

    async def run() -> tuple[int, int, int, int]:
        h = host()
        gov = h.governor
        ctx = wiring.class_context(msg("pdf").payload)
        # A large layout runs on the CPU parse slots (the governor's limit starts at 2, large's share of it is 1).
        large = [await asyncio.wait_for(
            cc.acquire_parsing_slot(h, ParseTier.HEAVY, 150 * MIB, timeout=1, ctx=wiring.class_context(msg("pdf", 150 * MIB).payload)), 2)]
        in_use = gov.gate(Pool.HEAVY_PARSE).in_use
        small = await asyncio.wait_for(cc.acquire_parsing_slot(h, ParseTier.HEAVY, 1 * MIB, timeout=1, ctx=ctx), 1)
        res = (in_use, gov.gate(Pool.HEAVY_PARSE).in_use, budget.snapshot()["used"], wiring.docling_gate(gov).running(S))
        cc.release_admission(small)
        for a in large:
            cc.release_admission(a)
        return res

    in_use_before, in_use_after, tokens, tickets = asyncio.run(run())
    assert in_use_after == in_use_before  # no HEAVY_PARSE permit for the small PDF
    assert tickets == 1
    assert tokens == 1  # the large layout's token; none for the PDF


def test_a_cpu_parse_reserves_once_and_the_lane_adopts_the_permit(monkeypatch: pytest.MonkeyPatch) -> None:
    """the gate's permit is the only reservation; the pool lane reserves nothing more."""
    budget = FakeMemberBudget(4)
    monkeypatch.setattr("app.capacity.runtime.member_budget", lambda *_a, **_k: budget)
    from app.modules.parsers import parse_pool

    async def run() -> tuple[int, int, int]:
        h = host()
        ctx = wiring.class_context(msg("xlsx").payload)
        admission = await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 1 * MIB, timeout=1, ctx=ctx)
        calls_after_gate = budget.reserve_calls
        used = budget.snapshot()["used"]
        held = pools.lane_permit(None)
        assert held is not None
        # the lane's own reservation helper, given the permit from the scope
        from app.capacity.admission import reserve_or_adopt

        adopted = await reserve_or_adopt(held, parse_pool._lane_demand(None), deadline_s=1)
        adopted.release()  # a lane releases what it adopted: nothing, the owner keeps it
        still = budget.snapshot()["used"]
        cc.release_admission(admission)
        return calls_after_gate, used, still

    calls, used, still = asyncio.run(run())
    assert calls == 1 and used == 1 and still == 1  # reserved once; the lane did not reserve or release


def test_org_a_flooding_large_work_does_not_hold_org_b_back() -> None:
    """T6: the next large grant goes to the other org, not to the flooder's queue."""

    async def run() -> list[str]:
        h = host()
        order: list[str] = []
        flood_ctx = wiring.class_context(msg("pdf", org="A").payload)
        other_ctx = wiring.class_context(msg("pdf", org="B").payload)
        first = await _index(h, S, flood_ctx)  # a small head start so the large waiters queue
        pool_limit = h.governor.limit(Pool.INDEX_HEAVY)
        fill = [await _index(h, S, flood_ctx) for _ in range(pool_limit - 1)]

        async def large(org_ctx: object, name: str) -> cc.Admission:
            a = await _index(h, L, org_ctx)
            order.append(name)
            return a

        tasks = [asyncio.create_task(large(flood_ctx, f"A{i}")) for i in range(4)]
        await asyncio.sleep(0.05)
        tasks.append(asyncio.create_task(large(other_ctx, "B0")))
        await asyncio.sleep(0.05)
        for a in [first, *fill]:
            cc.release_admission(a)
        await asyncio.sleep(0.3)
        for t in tasks:
            if t.done():
                cc.release_admission(t.result())
            else:
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return order

    order = asyncio.run(run())
    assert "B0" in order[:2], order  # round-robin between orgs inside the big class


def test_a_big_wait_that_times_out_is_uncounted_and_carries_its_first_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> cc.BigAdmissionTimeout:
        h = host()
        ctx = wiring.class_context(msg("pdf", 150 * MIB).payload)
        # the governor's HEAVY_PARSE limit starts at 2 and large work's share of it is 1
        held = [await asyncio.wait_for(cc.acquire_parsing_slot(h, ParseTier.HEAVY, 150 * MIB, timeout=1, ctx=ctx), 2)]
        try:
            with pytest.raises(cc.BigAdmissionTimeout) as err:
                await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 150 * MIB, timeout=0.2, ctx=ctx)
        finally:
            for a in held:
                cc.release_admission(a)
        return err.value

    err = asyncio.run(run())
    assert err.counts_toward_backstop is False and err.pauses_consumer is False
    assert isinstance(err, cc.ParseAdmissionTimeout)
    assert err.parse_enqueued_at_ms is not None and err.parse_enqueued_at_ms <= time.time() * 1000


def _fill_the_governors_heavy_gate(h: object) -> list[object]:
    """Take every permit of the governor's HEAVY_PARSE gate directly, so a class-gate grant then waits behind it."""
    gate = h.governor.gate(Pool.HEAVY_PARSE)  # type: ignore[attr-defined]
    limit = h.governor.limit(Pool.HEAVY_PARSE)  # type: ignore[attr-defined]
    return [gate, limit]


def test_a_big_wait_at_the_governors_gate_is_also_handed_back_uncounted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> cc.ParseAdmissionTimeout:
        h = host()
        ctx = wiring.class_context(msg("pdf", 150 * MIB).payload)
        gate, limit = _fill_the_governors_heavy_gate(h)
        for _ in range(limit):
            assert await gate.acquire(1, 1.0, spaced=False)  # type: ignore[attr-defined]
        with pytest.raises(cc.ParseAdmissionTimeout) as err:
            await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 150 * MIB, timeout=0.3, ctx=ctx)
        return err.value

    err = asyncio.run(run())
    assert isinstance(err, cc.BigAdmissionTimeout)
    assert err.counts_toward_backstop is False and err.pauses_consumer is False
    assert err.parse_enqueued_at_ms is not None and err.parse_enqueued_at_ms <= time.time() * 1000


def test_a_big_wait_is_bounded_by_parse_big_wait_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PARSE_BIG_WAIT_SECONDS", "0.3")

    async def run() -> float:
        h = host()
        ctx = wiring.class_context(msg("pdf", 150 * MIB).payload)
        gate, limit = _fill_the_governors_heavy_gate(h)
        for _ in range(limit):
            assert await gate.acquire(1, 1.0, spaced=False)  # type: ignore[attr-defined]
        started = time.monotonic()
        with pytest.raises(cc.BigAdmissionTimeout):
            await cc.acquire_parsing_slot(h, ParseTier.HEAVY, 150 * MIB, timeout=30, ctx=ctx)  # the caller's own bound is far longer
        return time.monotonic() - started

    assert asyncio.run(run()) < 5.0


def test_the_big_wait_setting_defaults_to_ten_minutes_and_rejects_nonsense() -> None:
    assert wiring.big_wait_seconds({}) == 600.0
    assert wiring.big_wait_seconds({"PARSE_BIG_WAIT_SECONDS": "90"}) == 90.0
    for bad in ("0", "-5", "abc", "nan", "inf"):
        assert wiring.big_wait_seconds({"PARSE_BIG_WAIT_SECONDS": bad}) == 600.0


def test_download_ahead_has_places_per_class(monkeypatch: pytest.MonkeyPatch) -> None:
    """small 4 places, big 2; a full big set does not stop a small record's download."""
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "2")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "4")

    async def run() -> tuple[int, int, bool]:
        h = host()
        big_ctx = wiring.class_context(msg("pdf").payload)
        big = [await cc.acquire_download_slot(h, L, timeout=0.2, ctx=big_ctx, size_bytes=150 * MIB) for _ in range(2)]
        with pytest.raises(cc.ParseAdmissionTimeout):
            await cc.acquire_download_slot(h, L, timeout=0.1, ctx=big_ctx, size_bytes=150 * MIB)
        smalls = [await cc.acquire_download_slot(h, S, timeout=0.2, ctx=big_ctx, size_bytes=1 * MIB) for _ in range(4)]
        refused = False
        try:
            await cc.acquire_download_slot(h, S, timeout=0.1, ctx=big_ctx, size_bytes=1 * MIB)
        except cc.ParseAdmissionTimeout:
            refused = True
        for a in [*big, *smalls]:
            cc.release_admission(a)
        return len(big), len(smalls), refused

    assert asyncio.run(run()) == (2, 4, True)


def test_a_small_memory_reservation_goes_ahead_of_queued_big_ones() -> None:
    """with the budget full and big requests queued, a small one (<= 1 GiB) is served first."""

    async def run() -> list[str]:
        h = host()
        budget = h.governor.memory_budget
        budget.limit = 4 << 30
        order: list[str] = []
        ctx = wiring.class_context(msg("pdf").payload)
        first = await budget.reserve(4 << 30, 1)
        assert first

        async def want(name: str, nbytes: int, ahead: bool) -> None:
            if await budget.reserve(nbytes, 3, ahead=ahead):
                order.append(name)
                budget.release(nbytes)

        big = [asyncio.create_task(want(f"big{i}", 3 << 30, False)) for i in range(2)]
        await asyncio.sleep(0.05)
        small = asyncio.create_task(want("small", 1 << 30, wiring.memory_goes_ahead(wiring.class_of(ctx, 1 * MIB), 1 << 30)))
        await asyncio.sleep(0.05)
        budget.release(4 << 30)
        await asyncio.gather(*big, small)
        return order

    assert asyncio.run(run())[0] == "small"


def test_memory_goes_ahead_only_for_small_requests_up_to_a_gib() -> None:
    assert wiring.memory_goes_ahead(S, 1 << 30)
    assert not wiring.memory_goes_ahead(S, (1 << 30) + 1)
    assert not wiring.memory_goes_ahead(L, 1 << 20)
    assert not wiring.memory_goes_ahead(None, 1 << 20)
    assert not wiring.memory_goes_ahead(S, 0)


def test_a_small_cpu_parse_never_waits_behind_a_large_one_waiting_for_a_start_token() -> None:
    """With the governor's own start-rate limiter, large parses queue at the heavy gate for start tokens; a small
    workbook (a CPU parse that skips the limiter) is admitted at once, not behind them."""

    async def record(h: SimpleNamespace, payload: dict, hold: float, out: list, kind: str) -> None:
        level = cc.dispatch_tier(h, SimpleNamespace(payload=payload))
        ctx = cc.class_context(h, payload)
        started = time.monotonic()
        index = await cc.acquire_index_slot(h, level, **cc.class_kwargs(ctx))
        cc.reclass_index_slot(index, ctx, payload["sizeInBytes"])
        try:
            parse = await cc.acquire_parsing_slot(h, ParseTier.HEAVY, payload["sizeInBytes"], timeout=60.0, **cc.class_kwargs(ctx))
            out.append((kind, time.monotonic() - started))
            await asyncio.sleep(hold)
            cc.release_admission(parse)
        finally:
            cc.release_admission(index)

    async def run() -> list[float]:
        h = host()
        h.governor._registry.set(Pool.HEAVY_PARSE, 8)
        h.governor._registry.set(Pool.INDEX_HEAVY, 16)
        out: list = []
        tasks = [
            asyncio.create_task(record(
                h, {"extension": "pdf", "mimeType": "application/pdf", "orgId": "o", "recordId": f"l{i}", "sizeInBytes": 150 * MIB},
                3.0, out, "large",
            ))
            for i in range(8)
        ]
        await asyncio.sleep(0.1)
        for i in range(20):
            tasks.append(asyncio.create_task(record(
                h, {"extension": "xlsx", "mimeType": "", "orgId": "o", "recordId": f"s{i}", "sizeInBytes": 1 * MIB}, 0.02, out, "small",
            )))
            await asyncio.sleep(0.05)
        await asyncio.gather(*tasks)
        return sorted(w for kind, w in out if kind == "small")

    waits = asyncio.run(run())
    assert waits[-1] < 0.5, waits  # without the fix the slowest small waits 1.5-2 s for a start token


def test_the_admission_backlog_counts_a_cost_class_record_in_the_heavy_slots() -> None:
    """Every waiting stage is entered and left with the dispatch level, which is a cost class under classes."""
    backlog = cc.AdmissionBacklog()
    for level in (S, L, H):
        for phase in (cc.PHASE_GATE, cc.PHASE_LEASE, cc.PHASE_DOWNLOAD, cc.PHASE_PARSE):
            backlog.add(level, phase)  # a KeyError here left the waiter counted for ever
    totals = backlog.totals()[ParseTier.HEAVY]
    assert totals == {cc.PHASE_GATE: 3, cc.PHASE_LEASE: 3, cc.PHASE_DOWNLOAD: 3, cc.PHASE_PARSE: 3}
    for level in (S, L, H):
        for phase in (cc.PHASE_GATE, cc.PHASE_LEASE, cc.PHASE_DOWNLOAD, cc.PHASE_PARSE):
            backlog.remove(level, phase)
    assert all(v == 0 for v in backlog.totals()[ParseTier.HEAVY].values())


def test_a_gate_waiter_of_a_cost_class_leaves_the_backlog_clean() -> None:
    async def run() -> int:
        h = host()
        h.admission_backlog = cc.AdmissionBacklog()
        ctx = wiring.class_context(msg("pdf", 150 * MIB).payload)
        level = cc.dispatch_tier(h, msg("pdf", 150 * MIB))
        token = cc.GateWaiterToken(h, level)
        token.enter_gate()
        admission = await cc.acquire_index_slot(h, level, **cc.class_kwargs(ctx))
        token.admit()
        cc.release_admission(admission)
        token.release()
        return sum(h.admission_backlog.totals()[ParseTier.HEAVY].values())

    assert asyncio.run(run()) == 0


def test_inspection_moves_the_download_place_to_the_new_class_so_the_next_small_download_is_not_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Four small places by default: a PDF that inspection turns into a huge one must stop holding one of them."""
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "2")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "1")

    async def run() -> tuple[bool, bool]:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)  # no size in the event: it starts as small
        first = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=1 * MIB)
        blocked = False
        try:
            await cc.acquire_download_slot(h, L, timeout=0.1, ctx=ctx, size_bytes=1 * MIB)  # the one small place is taken
        except cc.ParseAdmissionTimeout:
            blocked = True
        inspected = ctx.inspected(1000, scanned=False)  # a thousand pages: huge
        moved = await cc.reclass_download_slot(first, inspected, 80 * MIB, 0.5)
        second = await cc.acquire_download_slot(h, L, timeout=0.3, ctx=ctx, size_bytes=1 * MIB)  # a small place is free again
        cc.release_admission(second)
        cc.release_admission(moved)
        return blocked, moved is not first

    blocked, moved = asyncio.run(run())
    assert blocked and moved


def test_a_download_place_whose_class_did_not_change_stays_where_it_is() -> None:
    async def run() -> bool:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)
        first = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=1 * MIB)
        same = await cc.reclass_download_slot(first, ctx, 1 * MIB, 0.5)
        cc.release_admission(same)
        return same is first

    assert asyncio.run(run())


def test_with_a_remote_parsing_service_a_light_format_record_takes_its_place_in_the_same_remote_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One owner for the configured concurrency: heavy and light remote parses together never exceed it."""
    from app.services.parsing import remote_concurrency as rc

    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "2")

    async def run() -> tuple[int, int]:
        rc.reset_remote_parse_slots()
        h = host()
        in_flight = 0
        peak = 0

        async def one(payload: dict, tier: ParseTier, size: int) -> None:
            nonlocal in_flight, peak
            ctx = wiring.class_context(payload)
            admission = await cc.acquire_parsing_slot(h, tier, size, timeout=5, ctx=ctx)
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0.05)
            in_flight -= 1
            cc.release_admission(admission)

        light = {"extension": "txt", "mimeType": "text/plain", "orgId": "o"}
        heavy = {"extension": "pdf", "mimeType": "application/pdf", "orgId": "o"}
        await asyncio.gather(
            *(one(light, ParseTier.LIGHT, 1 * MIB) for _ in range(4)), *(one(heavy, ParseTier.HEAVY, 150 * MIB) for _ in range(4))
        )
        return peak, rc.remote_parse_slots(2)._free

    peak, fifo_free = asyncio.run(run())
    assert peak <= 2  # the configured concurrency, held once
    assert fifo_free == 2  # the FIFO pool was never entered with classes on


def test_a_large_record_that_inspection_makes_huge_keeps_its_place_in_a_full_big_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    """Large and huge share the big download pool: with both places held, the move must not wait for a third."""
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "2")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "1")

    async def run() -> tuple[bool, float]:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)
        first = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=150 * MIB)  # large by size
        second = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=150 * MIB)  # the big pool is full
        started = asyncio.get_running_loop().time()
        moved = await cc.reclass_download_slot(first, ctx.inspected(1000, scanned=False), 150 * MIB, 0.3)  # now huge
        waited = asyncio.get_running_loop().time() - started
        same = moved is first and moved.download_class is H
        cc.release_admission(moved)
        cc.release_admission(second)
        return same, waited

    same, waited = asyncio.run(run())
    assert same and waited < 0.1  # kept, no timeout, no wait for a third place


def _use() -> dict[str, int]:
    return {n: a.stats()["in_use"] for n, a in wiring._Registry.ahead.get(asyncio.get_running_loop(), {}).items()}


def test_two_records_crossing_between_full_download_pools_never_wait_on_each_other(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small pool 1/1 and big pool 1/1, both held. One record moves small->huge, the other large->small: each holds
    the pool the other needs. Neither may wait while holding its old place."""
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "1")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "1")

    async def run() -> tuple[float, list[object], dict[str, int], dict[str, int]]:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)
        a = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=1 * MIB)  # small
        b = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=150 * MIB)  # large (big pool)
        before = _use()
        started = asyncio.get_running_loop().time()
        results = await asyncio.gather(
            cc.reclass_download_slot(a, ctx.inspected(1000, scanned=False), 80 * MIB, 2.0),  # small -> huge
            cc.reclass_download_slot(b, ctx.inspected(1, scanned=False), 1 * MIB, 2.0),  # large -> small (measured: 1 page)
            return_exceptions=True,
        )
        took = asyncio.get_running_loop().time() - started
        after = _use()
        for r in results:
            if not isinstance(r, BaseException):
                cc.release_admission(r)
        return took, results, before, after

    took, results, before, after = asyncio.run(run())
    assert not any(isinstance(r, BaseException) for r in results), results  # no timeout
    assert took < 0.5  # one service time, not the admission wait
    assert before == after  # one place in each pool, none leaked or doubled


def test_a_free_place_in_the_new_pool_is_taken_without_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD", "2")
    monkeypatch.setenv("INDEX_HEAVY_DOWNLOAD_AHEAD_SMALL", "1")

    async def run() -> tuple[dict[str, int], dict[str, int]]:
        h = host()
        ctx = wiring.class_context(msg("pdf").payload)
        a = await cc.acquire_download_slot(h, L, timeout=0.2, ctx=ctx, size_bytes=1 * MIB)
        before = _use()
        moved = await cc.reclass_download_slot(a, ctx.inspected(1000, scanned=False), 80 * MIB, 0.0)  # no wait allowed at all
        after = _use()
        cc.release_admission(moved)
        return before, after

    before, after = asyncio.run(run())
    assert before == {"small": 1, "big": 0} or sum(before.values()) == 1
    assert sum(after.values()) == 1 and after.get("big") == 1 and after.get("small", 0) == 0
