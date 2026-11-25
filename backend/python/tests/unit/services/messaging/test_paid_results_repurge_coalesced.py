"""Coalesced re-purge: a bulk delete makes pages of hops, not a chain per record (G-32 part A)."""

from __future__ import annotations

import heapq
import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.core.org_context import OrgContext, bind
from app.exceptions.indexing_exceptions import ProcessingError
from app.plugins.telemetry.modules import envelope_metrics, paid_cache_metrics
from app.services.messaging.config import IndexingEvent
from app.services.paid_cache import access, repurge, repurge_window
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.locks import (
    REPURGE_HOP_S,
    InProcessLocks,
    repurge_horizon_s,
)
from app.services.paid_cache.run import clear_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler
from tests.support.repurge_window import (
    Broker,
    MemoryWindowStore,
    first_hop,
    redis_store,
)

ORG = "org-1"
BATCH = EventTypes.PURGE_PAID_RESULTS_BATCH.value
HOP = EventTypes.PURGE_PAID_RESULTS.value


@pytest.fixture(autouse=True)
def _flags(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_RESULT_REPURGE_COALESCE", "true")


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    now = [1_000_000.0]
    monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: now[0]))
    return now


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    store, window = MemoryResultStore(), redis_store()
    cache = PaidResultCache(store, InProcessLocks())
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    monkeypatch.setattr(repurge_window, "window_store_for", AsyncMock(return_value=window))
    yield SimpleNamespace(files=store, window=window, cache=cache)
    clear_run()


def _handler(producer: Any) -> Any:  # noqa: ANN401
    return make_record_handler(producer=producer)


async def _run(handler: Any, event_type: str, payload: dict) -> list:  # noqa: ANN401
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        return [e.event async for e in handler.process_event(event_type, payload) if e.event != IndexingEvent.START_DOWNLOAD]


async def test_by_default_a_first_hop_publishes_the_per_record_second_hop_and_touches_no_window(
    monkeypatch: pytest.MonkeyPatch, world: Any, clock: list[float],  # noqa: ANN401
) -> None:
    monkeypatch.delenv("PAID_RESULT_REPURGE_COALESCE")
    world.window.open_or_append = AsyncMock(side_effect=AssertionError("window touched"))
    broker = Broker(clock)
    await _run(_handler(broker), HOP, first_hop(ORG, "rec-1", clock[0]))
    assert [(m.type, m.payload["hop"], m.payload["recordId"]) for m in broker.sent] == [(HOP, 2, "rec-1")]


async def test_with_coalescing_on_a_first_hop_opens_the_window_publishes_one_seal_and_no_second_hop(
    world: Any, clock: list[float],  # noqa: ANN401
) -> None:
    broker = Broker(clock)
    handler = _handler(broker)
    for i in range(3):
        await _run(handler, HOP, first_hop(ORG, f"rec-{i}", clock[0]))
    assert [m.type for m in broker.sent] == [BATCH]
    seal = broker.sent[0].payload
    window = repurge_window.window_of(clock[0])
    assert (seal["phase"], seal["window"]) == ("seal", window)
    assert seal["_retry_not_before"] == pytest.approx((window + 1) * 300 + repurge_window.SEAL_GRACE_S)
    assert broker.sent[0].key == f"{ORG}:{window}"


async def test_with_the_chain_off_a_hop_already_queued_still_continues_per_record_and_never_coalesces(
    monkeypatch: pytest.MonkeyPatch, world: Any, clock: list[float],  # noqa: ANN401
) -> None:
    monkeypatch.delenv("PAID_RESULT_REPURGE_CHAIN")
    world.window.open_or_append = AsyncMock(side_effect=AssertionError("window touched"))
    broker = Broker(clock)
    await _run(_handler(broker), HOP, first_hop(ORG, "rec-1", clock[0]))
    assert [(m.type, m.payload["hop"]) for m in broker.sent] == [(HOP, 2)]


async def test_the_seal_pages_the_ids_and_every_page_hop_purges_them_then_publishes_its_successor(world: Any, clock: list[float], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    monkeypatch.setenv("PAID_RESULT_REPURGE_PAGE_SIZE", "3")
    broker = Broker(clock)
    handler = _handler(broker)
    for rid in ("a", "b", "c", "d", "e"):
        await _run(handler, HOP, first_hop(ORG, rid, clock[0]))
    for rid in ("a", "b", "c", "d", "e"):                  # a late write by an orphan, after each record's first hop
        world.files.files[(ORG, rid, "ocr/x.json")] = (b"1", 0)
    seal = broker.sent[0].payload
    clock[0] = seal["_retry_not_before"]
    broker.sent.clear()
    await _run(handler, BATCH, seal)
    pages = [m.payload for m in broker.sent]
    assert [p["recordIds"] for p in pages] == [["a", "b", "c"], ["d", "e"]]
    assert all(p["hop"] == 1 and p["deletedAt"] == clock[0] and p["horizonS"] == repurge_horizon_s() for p in pages)
    assert [p["_retry_not_before"] - clock[0] for p in pages] == [REPURGE_HOP_S, REPURGE_HOP_S + repurge_window.SPREAD_S // 2]
    due = pages[0]
    clock[0] = due["_retry_not_before"]
    broker.sent.clear()
    await _run(handler, BATCH, due)
    assert world.files.paths(ORG, "a") == [] and world.files.paths(ORG, "d") != []       # only its own page was purged
    nxt = broker.sent[0].payload
    assert (nxt["hop"], nxt["recordIds"], nxt["page"], nxt["window"]) == (2, ["a", "b", "c"], 0, due["window"])
    assert nxt["deletedAt"] == due["deletedAt"] and nxt["horizonS"] == due["horizonS"]


async def test_the_hop_of_a_record_that_took_a_window_goes_on_only_through_the_pages(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    broker = Broker(clock)
    await _run(_handler(broker), HOP, first_hop(ORG, "rec-1", clock[0]))
    assert not [m for m in broker.sent if m.type == HOP]


async def test_a_batch_whose_org_is_forged_in_the_body_purges_the_envelopes_org(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    world.files.files[(ORG, "rec-1", "p")] = (b"1", 0)
    world.files.files[("org-evil", "rec-1", "p")] = (b"1", 0)
    hop = repurge_window.page_hop_payload("org-evil", 1, 0, ["rec-1"], now=clock[0], spread_s=0)
    await _run(_handler(Broker(clock)), BATCH, hop)
    assert world.files.paths(ORG, "rec-1") == [] and world.files.paths("org-evil", "rec-1") != []


@pytest.mark.parametrize(
    "body",
    [{"phase": "hop", "recordIds": "rec-1"}, {"phase": "hop", "recordIds": ["a"] * 1001}, {"phase": "hop", "recordIds": [1]},
     {"phase": "seal"}, {"phase": "seal", "window": True, "epoch": "e"}, {"phase": "weird"}, {}],
)
async def test_a_batch_of_a_shape_no_retry_can_fix_is_terminal_and_purges_nothing(world: Any, clock: list[float], body: dict) -> None:  # noqa: ANN401
    from app.exceptions.indexing_exceptions import ProcessingError

    world.files.files[(ORG, "a", "p")] = (b"1", 0)
    with pytest.raises(ProcessingError):
        await _run(_handler(Broker(clock)), BATCH, {**body, "deletedAt": clock[0], "hop": 1})
    assert world.files.paths(ORG, "a") != []


async def test_one_invalid_id_in_a_page_is_dropped_and_the_rest_are_purged(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    for rid in ("good-1", "good-2"):
        world.files.files[(ORG, rid, "p")] = (b"1", 0)
    broker = Broker(clock)
    hop = repurge_window.page_hop_payload(ORG, 1, 0, ["good-1", "bad id!", "org", "good-2"], now=clock[0], spread_s=0)
    await _run(_handler(broker), BATCH, hop)
    assert world.files.paths(ORG, "good-1") == [] and world.files.paths(ORG, "good-2") == []
    assert broker.sent[0].payload["recordIds"] == ["good-1", "good-2"]


# -- failures ----------------------------------------------------------------------------------------------------


def _hop(clock: list[float], ids: list[str], *, hop: int = 1, deleted_at: float | None = None) -> dict:
    payload = repurge_window.page_hop_payload(ORG, 1, 0, ids, now=clock[0] if deleted_at is None else deleted_at, spread_s=0)
    payload["hop"] = hop
    return payload


def _flaky(bad: set[str]) -> Any:  # noqa: ANN401
    calls: list[str] = []

    async def purge(org: str, rid: str) -> None:
        calls.append(rid)
        if rid in bad:
            raise ConnectionError("storage down")
    purge.calls = calls  # type: ignore[attr-defined]
    return purge


async def test_a_partial_failure_does_not_fail_the_page_and_the_next_hop_carries_every_id(clock: list[float]) -> None:
    broker = Broker(clock)
    ids = [f"r{i}" for i in range(500)]
    purge = _flaky({"r3", "r4", "r5"})
    await repurge_window.handle_page_hop(broker, ORG, _hop(clock, ids), purge=purge)
    assert [m.type for m in broker.sent] == [BATCH] and broker.sent[0].payload["recordIds"] == ids      # no retry message
    assert broker.sent[0].payload["hop"] == 2


def _many(bad: set[str], *, leave_out: set[str] = frozenset()) -> Any:  # noqa: ANN401
    calls: list[list[str]] = []

    async def purge_many(org: str, ids: Any) -> dict[str, BaseException | None]:  # noqa: ANN401
        calls.append(list(ids))
        return {i: (ConnectionError("down") if i in bad else None) for i in ids if i not in leave_out}
    purge_many.calls = calls  # type: ignore[attr-defined]
    return purge_many


async def test_a_page_hop_with_a_batch_purge_purges_the_page_in_one_call_and_carries_every_id_on(clock: list[float]) -> None:
    broker = Broker(clock)
    ids = [f"r{i}" for i in range(500)]
    per_id, many = _flaky(set()), _many({"r3"})
    await repurge_window.handle_page_hop(broker, ORG, _hop(clock, ids), purge=per_id, purge_many=many)
    assert many.calls == [ids] and per_id.calls == []                       # one batch call, no per-id purge
    assert [m.type for m in broker.sent] == [BATCH] and broker.sent[0].payload["recordIds"] == ids


async def test_a_page_hop_with_a_batch_purge_retries_failed_and_unanswered_ids_in_the_tail(clock: list[float]) -> None:
    broker = Broker(clock)
    ids = ["a", "b", "c"]
    await repurge_window.handle_page_hop(
        broker, ORG, _hop(clock, ids, hop=50, deleted_at=clock[0] - repurge_horizon_s() - 1), purge=_flaky(set()), purge_many=_many({"b"}, leave_out={"c"}),
    )
    tails = [m.payload for m in broker.sent if m.payload.get("tail")]
    assert len(tails) == 1 and tails[0]["recordIds"] == ["b", "c"]


async def test_a_page_hop_whose_batch_purge_fails_for_every_id_raises_so_the_page_is_redelivered(clock: list[float]) -> None:
    with pytest.raises(ConnectionError):
        await repurge_window.handle_page_hop(
            Broker(clock), ORG, _hop(clock, ["a", "b"]), purge=_flaky(set()), purge_many=_many({"a", "b"}),
        )


async def test_every_id_failing_or_the_successor_not_publishing_raises_so_the_page_is_redelivered(clock: list[float]) -> None:
    ids = ["a", "b"]
    with pytest.raises(ConnectionError):
        await repurge_window.handle_page_hop(Broker(clock), ORG, _hop(clock, ids), purge=_flaky({"a", "b"}))
    broker = Broker(clock)
    broker.down = True
    with pytest.raises(ConnectionError):
        await repurge_window.handle_page_hop(broker, ORG, _hop(clock, ids), purge=_flaky(set()))


async def test_at_the_final_hop_the_failed_ids_get_a_tail_that_backs_off_and_then_dead_letters_with_the_ids(clock: list[float]) -> None:
    deleted = clock[0] - repurge_horizon_s() - 1                                      # past the horizon: no successor
    broker = Broker(clock)
    await repurge_window.handle_page_hop(broker, ORG, _hop(clock, ["a", "b", "c"], hop=50, deleted_at=deleted), purge=_flaky({"b"}))
    tail = broker.sent[0].payload
    assert (tail["tail"], tail["recordIds"], tail["attempt"]) == (True, ["b"], 1)
    delays = []
    attempts = 0
    while True:
        delays.append(tail["_retry_not_before"] - clock[0])
        broker.sent.clear()
        try:
            await repurge_window.handle_page_hop(broker, ORG, tail, purge=_flaky({"b"}))
        except ProcessingError:                  # terminal: it dead-letters at once, not after a redelivery budget
            break
        attempts += 1
        tail = broker.sent[0].payload
    assert attempts == repurge_window.MAX_TAIL_ATTEMPTS - 1
    assert delays == [60, 120, 240, 480]
    from app.core.envelope import max_age_seconds

    assert max(delays) <= max_age_seconds() - 300
    # a tail that succeeds publishes nothing more
    broker.sent.clear()
    await repurge_window.handle_page_hop(broker, ORG, {**tail, "recordIds": ["b"]}, purge=_flaky(set()))
    assert broker.sent == []


# -- flags, rolling deploy ---------------------------------------------------------------------------------------


async def test_a_seal_and_a_page_hop_already_queued_still_run_and_continue_after_the_flags_go_off(
    monkeypatch: pytest.MonkeyPatch, world: Any, clock: list[float],  # noqa: ANN401
) -> None:
    broker = Broker(clock)
    handler = _handler(broker)
    for rid in ("a", "b"):
        await _run(handler, HOP, first_hop(ORG, rid, clock[0]))
    seal = broker.sent[0].payload
    monkeypatch.delenv("PAID_RESULT_REPURGE_COALESCE")
    monkeypatch.delenv("PAID_RESULT_REPURGE_CHAIN")
    clock[0] = seal["_retry_not_before"]
    broker.sent.clear()
    await _run(handler, BATCH, seal)
    page = broker.sent[0].payload
    assert page["recordIds"] == ["a", "b"]
    broker.sent.clear()
    clock[0] = page["_retry_not_before"]
    await _run(handler, BATCH, page)
    assert broker.sent[0].payload["hop"] == 2


async def test_the_batch_type_is_new_and_never_looks_like_the_per_record_hop_an_older_consumer_understands(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    from app.exceptions.indexing_exceptions import ProcessingError

    assert BATCH != HOP
    broker = Broker(clock)
    await _run(_handler(broker), HOP, first_hop(ORG, "a", clock[0]))
    seal = broker.sent[0].payload
    assert "recordId" not in seal and "recordIds" not in seal
    # An older consumer routes an unknown type to its record path, which refuses a message with no recordId in one attempt
    # (record.py "has no recordId") and publishes nothing: no silent loss of the ids a newer one would carry.
    with pytest.raises(ProcessingError, match="no recordId"):
        await _run(_handler(Broker(clock)), "someTypeThisConsumerDoesNotKnow", seal)


async def test_a_per_record_chain_started_before_the_flag_continues_per_record_after_it(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    broker = Broker(clock)
    second = repurge.next_hop_payload(ORG, first_hop(ORG, "rec-1", clock[0]), now=clock[0] + REPURGE_HOP_S)
    assert second is not None
    clock[0] += REPURGE_HOP_S
    await _run(_handler(broker), HOP, second)
    assert [(m.type, m.payload["hop"]) for m in broker.sent] == [(HOP, 3)]                 # hop 2 never coalesces


# -- the message count -------------------------------------------------------------------------------------------


class _Sim:
    """The broker's delayed queue: events run when due, in order, between the deletes (a seal runs before the next window)."""

    def __init__(self, broker: Broker, store: Any, clock: list[float], monkeypatch: Any) -> None:  # noqa: ANN401
        monkeypatch.setattr(repurge_window, "_purge_all", AsyncMock(return_value=([], None)))
        self.broker, self.store, self.clock = broker, store, clock
        self.heap: list[tuple[float, int, dict]] = []
        self.n = 0
        self.hops = self.seals = 0

    def _take(self) -> None:
        for m in self.broker.sent:
            self.n += 1
            heapq.heappush(self.heap, (float(m.payload["_retry_not_before"]), self.n, m.payload))
        self.broker.sent.clear()

    async def advance(self, until: float) -> None:
        self._take()
        while self.heap and self.heap[0][0] <= until:
            due, _n, payload = heapq.heappop(self.heap)
            self.clock[0] = max(self.clock[0], due)
            if payload["phase"] == "seal":
                self.seals += 1
                await repurge_window.handle_seal(self.broker, ORG, payload, store=self.store)
            else:
                self.hops += 1
                await repurge_window.handle_page_hop(self.broker, ORG, payload, purge=None)  # type: ignore[arg-type]
            self._take()
        self.clock[0] = max(self.clock[0], until)

    async def finish(self) -> None:
        await self.advance(float("inf") if not self.heap else 10**12)


def _chain_length() -> int:
    """The hops after the first that one record's own chain publishes, from the real hop code."""
    count, probe = 0, first_hop(ORG, "x", 0.0)
    while (probe := repurge.next_hop_payload(ORG, probe, now=probe["_retry_not_before"])) is not None:
        count += 1
    return count


# The message ratio does not depend on the delete count once a few windows and pages have formed: at 20,000 it is 46x, at
# 100,000 it is 46x. The work does: every record runs the real hop code (about 30 us a step, a few hundred thousand env
# reads and millions of id checks at 100,000), 3 s on an idle core but over a minute on a CI runner that is shared, so the
# per-PR job runs the 20,000 case and the 100,000 case runs with the stress tests (``-m stress`` or RUN_STRESS_TESTS=1).
@pytest.mark.parametrize("n", [20_000, pytest.param(100_000, marks=pytest.mark.stress)], ids=["20k", "100k"])
async def test_a_bulk_delete_makes_pages_of_hops_not_a_chain_per_record(
    monkeypatch: pytest.MonkeyPatch, clock: list[float], n: int,
) -> None:
    store, broker = MemoryWindowStore(), Broker(clock)
    sim = _Sim(broker, store, clock, monkeypatch)
    start = clock[0]
    for i in range(n):
        await sim.advance(start + i * 0.01)                                                # 100 deletes a second
        hop = first_hop(ORG, f"rec-{i}", clock[0] - REPURGE_HOP_S)
        assert await repurge_window.coalesce_successor(broker, ORG, hop, store=store) is True
    await sim.finish()
    per_record = _chain_length()
    windows = math.ceil(n * 0.01 / 300) + 1
    pages_max = math.ceil(n / repurge_window.page_size()) + windows
    total = n + sim.seals + sim.hops                                                       # hop 1 per record + seals + page hops
    assert sim.seals <= windows
    assert sim.hops <= pages_max * (per_record + 1)
    today = n * per_record                                                                 # hop 1 is the same in both shapes
    assert (n * (per_record + 1)) / total >= 40, (today, total)                            # the reviewer measured 45.5x
    assert total <= 0.025 * n * (per_record + 1)


async def test_a_sparse_org_pays_at_most_a_few_percent_more_than_per_record_chains(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> None:
    store, broker = MemoryWindowStore(), Broker(clock)
    sim = _Sim(broker, store, clock, monkeypatch)
    start, ids = clock[0], 20
    for i in range(ids):
        await sim.advance(start + i * 3600.0)                                              # one delete an hour: a window each
        assert await repurge_window.coalesce_successor(broker, ORG, first_hop(ORG, f"r{i}", clock[0] - 300), store=store)
    await sim.finish()
    assert (sim.hops + sim.seals) / (ids * _chain_length()) <= 1.08


async def test_a_windows_pages_are_spread_over_the_spread_and_each_page_is_within_the_message_bound(monkeypatch: pytest.MonkeyPatch, clock: list[float]) -> None:
    store, broker = MemoryWindowStore(), Broker(clock)
    for i in range(2000):
        await repurge_window.coalesce_successor(broker, ORG, first_hop(ORG, f"rec-{i:040d}", clock[0] - 300), store=store)
    seal = broker.sent[0].payload
    broker.sent.clear()
    clock[0] = seal["_retry_not_before"]
    await repurge_window.handle_seal(broker, ORG, seal, store=store)
    pages = [m.payload for m in broker.sent]
    assert len(pages) == 4 and all(len(p["recordIds"]) <= repurge_window.page_size() for p in pages)
    offsets = sorted(p["_retry_not_before"] - clock[0] - REPURGE_HOP_S for p in pages)
    assert offsets == [0, 30, 60, 90]
    longest_wait = REPURGE_HOP_S + repurge_window.SPREAD_S
    from app.core.envelope import max_age_seconds

    assert longest_wait <= max_age_seconds() - 300


async def test_the_coverage_never_ends_before_a_per_record_chain_would_even_with_clock_skew(
    monkeypatch: pytest.MonkeyPatch, clock: list[float],
) -> None:
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", "9000")                            # not the default budget
    store, broker = MemoryWindowStore(), Broker(clock)
    for skew in (-120.0, 0.0, 120.0):
        broker.sent.clear()
        base = 5_000_000.0 + skew * 1000
        clock[0] = base
        deleted_at = base - REPURGE_HOP_S                                                   # the record's own delete
        await repurge_window.coalesce_successor(broker, ORG, first_hop(ORG, f"s{int(skew)}", deleted_at), store=store)
        seal = broker.sent[0].payload
        clock[0] = max(clock[0], seal["_retry_not_before"])
        broker.sent.clear()
        await repurge_window.handle_seal(broker, ORG, seal, store=store)
        page = broker.sent[0].payload
        monkeypatch.setattr(repurge_window, "_purge_all", AsyncMock(return_value=([], None)))
        last = page
        while True:
            clock[0] = max(clock[0], last["_retry_not_before"])
            broker.sent.clear()
            await repurge_window.handle_page_hop(broker, ORG, last, purge=None)  # type: ignore[arg-type]
            if not broker.sent:
                break
            last = broker.sent[0].payload
        assert clock[0] >= deleted_at + repurge_horizon_s(), (skew, clock[0] - deleted_at)


async def test_the_memory_store_agrees_with_the_redis_scripts() -> None:
    a, b = MemoryWindowStore(), redis_store()
    ops = [
        ("open_or_append", ("o", 1, "t1", "e1", "r1", 1000)), ("open_or_append", ("o", 1, "t2", "e2", "r2", 1001)),
        ("mark_published", ("o", 1, "t1", "e1", 9000, 1001)), ("open_or_append", ("o", 1, "t2", "e2", "r2", 1002)),
        ("seal_read", ("o", 1, "e1", 9500)), ("seal_read", ("o", 1, "x", 9500)), ("open_or_append", ("o", 1, "t3", "e3", "r3", 9600)),
        ("mark_done", ("o", 1, "e1")), ("seal_read", ("o", 1, "e1", 9700)),
    ]
    for name, args in ops:
        assert await getattr(a, name)(*args) == await getattr(b, name)(*args), (name, args)


async def test_a_stale_message_is_counted_by_its_event_type(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core import envelope

    seen: list[tuple[str, ...]] = []
    monkeypatch.setattr(envelope_metrics.STALE_BY_TYPE, "inc", SimpleNamespace(inc=lambda *labels, **_k: seen.append(labels)).inc)
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    monkeypatch.setattr(envelope, "_sign_key", b"k" * 32)
    monkeypatch.setattr(envelope, "_keys", AsyncMock(return_value=[b"k" * 32]))
    from app.utils.request_context import inject_envelope

    message = {"eventType": BATCH, "payload": {"phase": "seal"}, "timestamp": 1}
    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        wire = envelope.sign_message("record-events", inject_envelope(message))
    keys = await envelope._keys()
    with pytest.raises(envelope.EnvelopeRejected):
        await envelope.open_envelope("record-events", wire, keys=keys, now=lambda: wire["ctx"]["issuedAt"] + 100_000)
    assert ("record-events", BATCH) in seen


async def test_a_page_whose_ids_are_all_invalid_keeps_no_chain_going(clock: list[float]) -> None:
    broker = Broker(clock)
    await repurge_window.handle_page_hop(broker, ORG, _hop(clock, ["bad id!", "org"]), purge=_flaky(set()))
    assert broker.sent == []


async def test_an_org_hop_never_counts_as_an_invalid_id_fallback(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    seen: list[str] = []
    real = paid_cache_metrics.REPURGE_FALLBACK
    paid_cache_metrics.REPURGE_FALLBACK = SimpleNamespace(inc=lambda *labels, **_k: seen.append(labels[0]))  # type: ignore[assignment]
    try:
        org_hop = repurge.org_repurge_event_payload(ORG, now=clock[0])
        assert await repurge_window.coalesce_successor(Broker(clock), ORG, org_hop, store=world.window, now=clock[0] + 300) is False
    finally:
        paid_cache_metrics.REPURGE_FALLBACK = real
    assert seen == []


async def test_a_burst_of_failing_first_hops_logs_one_warning_and_counts_every_fallback(
    clock: list[float], caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """QA-G32a-5: during a Redis outage a 100k delete would otherwise log 100k lines."""
    counted: list[str] = []
    monkeypatch.setattr(paid_cache_metrics.REPURGE_FALLBACK, "inc", SimpleNamespace(inc=lambda *labels, **_k: counted.append(labels[0])).inc)
    monkeypatch.setattr(repurge_window, "_last_warned", {})
    store = AsyncMock()
    store.open_or_append.side_effect = ConnectionError("redis down")
    with caplog.at_level("WARNING", logger=repurge_window.log.name):
        for i in range(500):
            assert await repurge_window.coalesce_successor(
                Broker(clock), ORG, first_hop(ORG, f"rec-{i}", clock[0] - 300), store=store, now=clock[0],
            ) is False
    warnings = [r for r in caplog.records if "Re-purge window unavailable" in r.getMessage()]
    assert len(warnings) == 1 and counted == ["error"] * 500


async def test_a_record_whose_chain_carries_a_longer_horizon_than_the_live_one_keeps_its_own_chain(world: Any, clock: list[float]) -> None:  # noqa: ANN401
    """A page chain ends at the live horizon from its seal; a record started under a longer one must not be cut short."""
    hop = {**first_hop(ORG, "rec-1", clock[0]), "horizonS": repurge_horizon_s() + 3600}
    world.window.open_or_append = AsyncMock(return_value=repurge_window.Opened(repurge_window.APPENDED))   # would take the id
    assert await repurge_window.coalesce_successor(Broker(clock), ORG, hop, store=world.window, now=clock[0] + 300) is False
    world.window.open_or_append.assert_not_awaited()


async def test_a_seal_published_while_the_claim_lapses_is_not_marked_published_and_the_record_keeps_its_chain(
    world: Any, clock: list[float],  # noqa: ANN401
) -> None:
    class Stalling(Broker):
        async def send_event(self, *a: Any, **k: Any) -> bool:  # noqa: ANN401
            clock[0] += repurge_window.CLAIM_TTL_MS / 1000 + 5                       # the worker pauses past its claim
            return await super().send_event(*a, **k)

    broker = Stalling(clock)
    hop = first_hop(ORG, "rec-1", clock[0] - 300)
    assert await repurge_window.coalesce_successor(broker, ORG, hop, store=world.window, now=clock[0]) is False
    window = repurge_window.window_of(clock[0] - repurge_window.CLAIM_TTL_MS / 1000 - 5)
    own = await world.window._client.inner.get(world.window._keys(ORG, window)[0])
    assert own is None or own.startswith("claim:")                                     # never "published": nothing was appended
    assert await world.window._client.inner.llen(world.window._keys(ORG, window)[1]) == 0


async def test_a_seal_counts_the_ids_a_lost_list_no_longer_holds(world: Any, clock: list[float], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    counted: list[str] = []
    monkeypatch.setattr(paid_cache_metrics.REPURGE_WINDOW_LOST, "inc", SimpleNamespace(inc=lambda *labels, **_k: counted.append(labels[0])).inc)
    broker = Broker(clock)
    for i in range(3):
        assert await repurge_window.coalesce_successor(broker, ORG, first_hop(ORG, f"rec-{i}", clock[0] - 300), store=world.window, now=clock[0])
    seal = broker.sent[0].payload
    window = seal["window"]
    await world.window._client.inner.ltrim(world.window._keys(ORG, window)[1], 2, -1)  # Redis evicted part of the list
    broker.sent.clear()
    await repurge_window.handle_seal(broker, ORG, seal, store=world.window, now=seal["_retry_not_before"])
    assert counted == ["ids"] and broker.sent[0].payload["recordIds"] == ["rec-2"]    # what is left is still paged


async def test_a_window_that_never_accepted_an_id_finishes_without_a_loss(world: Any, clock: list[float], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN401
    counted: list[str] = []
    monkeypatch.setattr(paid_cache_metrics.REPURGE_WINDOW_LOST, "inc", SimpleNamespace(inc=lambda *labels, **_k: counted.append(labels[0])).inc)
    store = world.window
    assert (await store.open_or_append(ORG, 5, "aaaa", "e1", "seed", 1000)).state == repurge_window.CLAIMED
    assert await store.mark_published(ORG, 5, "aaaa", "e1", 9000, 1001)
    await repurge_window.handle_seal(Broker(clock), ORG, repurge_window.seal_payload(ORG, 5, "e1", due_s=0.0), store=store, now=clock[0])
    assert counted == []


def test_the_window_never_outlives_the_envelope_age_a_seal_must_survive(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.envelope import max_age_seconds

    monkeypatch.setenv("PAID_RESULT_REPURGE_WINDOW_S", "900")
    assert repurge_window.window_s() + repurge_window.SEAL_GRACE_S <= max_age_seconds() - 300
    monkeypatch.setenv("PAID_RESULT_REPURGE_WINDOW_S", "10")
    assert repurge_window.window_s() == 60
    monkeypatch.setenv("PAID_RESULT_REPURGE_WINDOW_S", "300")
    assert repurge_window.window_s() == 300
