"""A stored paid result is bounded by the stage's start; the delayed re-purge chain is complete.

* an orphaned parse that keeps paying after its record was deleted leaves nothing once the chain ends, however long
  it runs (the write bound is measured from the stage's start, and the run manifest obeys it too);
* a cancel at every suspension point after a paid call returned keeps the result and releases the lock;
* the delete handler and both hop kinds (record, org) publish on the producer's own loop, and a failure there
  reaches the handler so the message is redelivered.
"""
from __future__ import annotations

import asyncio
import random
import threading
import warnings
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.core import envelope
from app.core.org_context import OrgContext, bind, bind_optional
from app.services.messaging.config import IndexingEvent, StreamMessage
from app.services.paid_cache import access, repurge
from app.services.paid_cache import cache as cache_module
from app.services.paid_cache import locks as locks_module
from app.services.paid_cache import run as run_module
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import (
    REPURGE_HOP_S,
    InProcessLocks,
    repurge_horizon_s,
)
from app.services.paid_cache.repurge import (
    org_repurge_event_payload,
    repurge_event_payload,
)
from app.services.paid_cache.run import RunScope, begin_run, clear_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler

ORG, REC = "org-1", "rec-1"
DELETE = {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"}
SEED = 424242


def _key(i: Any = 0, rec: str = REC) -> CacheKey:  # noqa: ANN401
    return CacheKey(ORG, rec, "ocr_page", 1, "m", "p", "q", f"p{i}")


def _live(name: str) -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == name and not t.done()]


async def _text() -> str:
    return "text"


class _YieldingStore(MemoryResultStore):
    """Every storage call suspends twice before it acts, like a presign + PUT over the network."""

    async def _hop(self) -> None:
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async def get(self, *a: Any) -> Any:  # noqa: ANN401
        await self._hop()
        return await super().get(*a)

    async def put(self, *a: Any) -> bool:  # noqa: ANN401
        await self._hop()
        return await super().put(*a)

    async def replace(self, *a: Any) -> None:  # noqa: ANN401
        await self._hop()
        await super().replace(*a)

    async def delete_files(self, *a: Any) -> None:  # noqa: ANN401
        await self._hop()
        await super().delete_files(*a)


class _YieldingLocks(InProcessLocks):
    async def acquire(self, *a: Any) -> Any:  # noqa: ANN401
        await asyncio.sleep(0)
        return await super().acquire(*a)

    async def owns(self, *a: Any) -> bool:  # noqa: ANN401
        await asyncio.sleep(0)
        return await super().owns(*a)

    async def release(self, *a: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0)
        await super().release(*a)

    async def is_marked(self, *a: Any) -> bool:  # noqa: ANN401
        await asyncio.sleep(0)
        return await super().is_marked(*a)


def _record_handler(producer: Any) -> Any:  # noqa: ANN401
    handler = make_record_handler(producer=producer)
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={"_key": REC, "orgId": ORG, "virtualRecordId": "vr"})
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()
    return handler


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    yield store, locks, cache
    clear_run()


# -- p2: cancel at every suspension point after the call returned ---------------------------------------------
async def _cancel_after(k: int, extra_cancels: int, *, with_run: bool) -> str:
    store, locks = _YieldingStore(), _YieldingLocks()
    cache = PaidResultCache(store, locks)
    returned = asyncio.Event()

    async def compute() -> str:
        returned.set()
        return "text"

    async def caller() -> str:
        if with_run:
            begin_run(ORG, REC, "rev1", "parse")
        return await cache.get_or_compute(_key(), compute)

    task = asyncio.ensure_future(caller())
    await returned.wait()
    for _ in range(k):
        await asyncio.sleep(0)
    if task.done():
        await task
        outcome = "finished-before-cancel"
    else:
        task.cancel()
        for _ in range(extra_cancels):
            for _ in range(3):
                await asyncio.sleep(0)
            task.cancel()
        try:
            await task
            outcome = "finished"
        except asyncio.CancelledError:
            outcome = "cancelled"
    for _ in range(20):
        await asyncio.sleep(0)
    assert len(store.results(ORG, REC)) == 1, f"k={k} extra={extra_cancels}: paid result not stored ({outcome})"
    assert _live("paid-cache-lock-renewal") == [], f"k={k}: heartbeat outlived the call"
    assert _live("paid-cache-settle") == [], f"k={k}: settle task left running"
    assert [n for n, h in locks._held.items() if h[0] != "marker"] == [], f"k={k}: lock not released"
    if with_run:
        assert any(p.startswith("manifests/") for p in store.paths(ORG, REC)), f"k={k}: manifest note lost"
    return outcome


@pytest.mark.parametrize("extra_cancels", [0, 1, 3])
@pytest.mark.parametrize("with_run", [False, True])
async def test_cancel_at_every_await_after_the_call_keeps_the_paid_result(extra_cancels: int, with_run: bool) -> None:
    outcomes = []
    for k in range(0, 120):
        outcomes.append(await _cancel_after(k, extra_cancels, with_run=with_run))
        clear_run()
        if outcomes[-1] == "finished-before-cancel":
            break
    assert outcomes[-1] == "finished-before-cancel", "the sweep never reached the end of the call"
    assert outcomes.count("cancelled") >= 5, outcomes                # the sweep covered several suspension points
    assert "finished" not in outcomes, "a cancel was swallowed"


async def _deliver_due(handler: Any, producer: Any, pending: list, now: float) -> int:  # noqa: ANN401
    hops = 0
    while pending and pending[0]["_retry_not_before"] <= now:
        due = pending.pop(0)
        producer.send_event.reset_mock()
        [e async for e in handler.process_event("purgePaidResults", due)]
        hops += 1
        if producer.send_event.await_args is not None:
            pending.append(producer.send_event.await_args.kwargs["payload"])
    return hops


@pytest.mark.parametrize("starts_after_s", [0, 1230, 1950, 4800])
@pytest.mark.parametrize("parse_runs_for_s", [2400, 3000, 3600, 7200])
async def test_an_orphan_that_keeps_paying_leaves_nothing_once_the_chain_ends(
    world: Any, monkeypatch: pytest.MonkeyPatch, parse_runs_for_s: int, starts_after_s: int,  # noqa: ANN401
) -> None:
    """A parse that keeps paying pages after its record was deleted, Redis having lost the delete marker, leaves
    nothing once the chain ends, however long it runs and however late it was admitted (the client's retries and the
    parsing route's gate can start a stage up to ``starts_after_s`` after the delete)."""
    store, locks, cache = world
    now = [1_000_000.0]
    monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    producer = AsyncMock()
    handler = _record_handler(producer)
    delete_at = now[0]
    with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
        [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]
        locks._held.clear()                                             # Redis lost the marker
        pending = [producer.send_event.await_args.kwargs["payload"]]
        while now[0] - delete_at < starts_after_s:
            now[0] += 30
            await _deliver_due(handler, producer, pending, now[0])
        scope = RunScope(ORG, REC, "rev1", "parse", clock=lambda: now[0])     # the stage begins here
        run_module._run.set(scope)
        stage_start, page = now[0], 0
        while now[0] - stage_start < parse_runs_for_s:
            now[0] += 30
            await scope.note(cache.store, _key(page).relative_path(), opens=True)
            await cache.get_or_compute(_key(page), _text)              # one more short paid call
            page += 1
            await _deliver_due(handler, producer, pending, now[0])
        await scope.finish(cache.store)
        run_module._run.set(None)
        while pending:
            now[0] = max(now[0], pending[0]["_retry_not_before"])
            await _deliver_due(handler, producer, pending, now[0])
    left = store.paths(ORG, REC)
    assert left == [], f"stage began {starts_after_s} s after the delete and ran {parse_runs_for_s} s: {len(left)} left"


def test_the_admission_window_is_tied_to_the_consumer_and_the_route() -> None:
    """The constants the horizon mirrors are the ones the consumer, the client and the route really use."""
    from app.api.routes.parsing import PARSE_GATE_TIMEOUT_SECONDS
    from app.services.messaging.consumer_concurrency import parse_admission_wait_seconds
    from app.services.resource_governor.kinds import EXCESS_WAIT_SECONDS

    assert locks_module.PARSE_GATE_WAIT_S == PARSE_GATE_TIMEOUT_SECONDS
    assert locks_module.PARSE_MEMORY_WAIT_S == EXCESS_WAIT_SECONDS
    assert locks_module.PARSE_SLOT_WAITS * locks_module._budgets()[0] == 2 * parse_admission_wait_seconds()


@pytest.mark.parametrize("pdf_budget", [900, 5400, 9000, 21600])
def test_the_horizon_follows_the_configured_budgets(monkeypatch: pytest.MonkeyPatch, pdf_budget: int) -> None:
    """Computed at the hop from the live settings: a raised budget lengthens the chain, a fixed constant would not."""
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", str(pdf_budget))
    record = float(locks_module._budgets()[0])
    latest_start = 2 * record + max(record, pdf_budget) + 120 + 30
    assert locks_module.admission_window_s() >= latest_start
    assert locks_module.stage_deadline_s() >= max(record, pdf_budget)
    assert repurge_horizon_s() >= latest_start + max(record, pdf_budget) + locks_module.WRITE_DEADLINE_S + 120


@pytest.mark.parametrize("pdf_budget", [9000, 21600])
async def test_a_stage_that_starts_late_under_a_raised_budget_leaves_nothing(
    world: Any, monkeypatch: pytest.MonkeyPatch, pdf_budget: int,  # noqa: ANN401
) -> None:
    monkeypatch.setenv("PDF_RECORD_PROCESSING_TIMEOUT", str(pdf_budget))
    store, locks, cache = world
    now = [1_000_000.0]
    monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    producer = AsyncMock()
    handler = _record_handler(producer)
    delete_at = now[0]
    late = int(locks_module.admission_window_s()) - 60                  # as late as the configuration allows
    with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
        [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]
        locks._held.clear()
        pending = [producer.send_event.await_args.kwargs["payload"]]
        while now[0] - delete_at < late:
            now[0] += 60
            await _deliver_due(handler, producer, pending, now[0])
        scope = RunScope(ORG, REC, "rev1", "parse", clock=lambda: now[0])
        run_module._run.set(scope)
        stage_start, page = now[0], 0
        while now[0] - stage_start < locks_module.stage_deadline_s() + 600:    # pays on past the deadline
            now[0] += 120
            await scope.note(cache.store, _key(page).relative_path(), opens=True)
            await cache.get_or_compute(_key(page), _text)
            page += 1
            await _deliver_due(handler, producer, pending, now[0])
        await scope.finish(cache.store)
        run_module._run.set(None)
        while pending:
            now[0] = max(now[0], pending[0]["_retry_not_before"])
            await _deliver_due(handler, producer, pending, now[0])
    assert store.paths(ORG, REC) == []


async def test_a_call_started_long_after_its_stage_began_is_still_stored(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1_000_000.0]
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(monotonic=lambda: now[0]))
    store = MemoryResultStore()
    cache = PaidResultCache(store, InProcessLocks())
    scope = begin_run(ORG, REC, "rev1", "parse")
    scope.started_ms -= int((locks_module.stage_deadline_s() + 600) * 1000)   # the stage began 3000 s ago
    try:
        await cache.get_or_compute(_key(), _text)
    finally:
        clear_run()
    assert store.results(ORG, REC) == [], "a result of a stage that began more than STAGE_DEADLINE_S ago was stored"


class _Wire:
    def __init__(self, clock: list[float]) -> None:
        self.clock = clock
        self.sent: list[tuple[str, dict, float]] = []

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        from app.utils.request_context import inject_envelope

        message = {"eventType": event_type, "payload": payload, "timestamp": int(self.clock[0] * 1000)}
        self.sent.append((topic, envelope.sign_message(topic, inject_envelope(message)), self.clock[0]))
        return True


@pytest.mark.parametrize("scope", ["record", "org"])
@pytest.mark.parametrize("trial", range(6))
async def test_every_hop_verifies_under_random_lag_and_the_chain_ends_after_the_horizon(
    world: Any, monkeypatch: pytest.MonkeyPatch, trial: int, scope: str,  # noqa: ANN401
) -> None:
    rng = random.Random(SEED * 100 + trial)
    now = [2_000_000.0 + rng.uniform(0, 1000)]
    monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: now[0]))
    monkeypatch.setattr(envelope, "time", SimpleNamespace(time=lambda: now[0]))      # issuedAt at the fake clock
    monkeypatch.setenv("ENVELOPE_SIGN", "true")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    monkeypatch.setattr(envelope, "_sign_key", b"k" * 32)
    monkeypatch.setattr(envelope, "_keys", AsyncMock(return_value=[b"k" * 32]))
    store, _locks, cache = world
    wire = _Wire(now)
    handler = _record_handler(wire)
    with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
        if scope == "record":
            await repurge.schedule_repurge(wire, ORG, REC)
        else:
            await repurge.schedule_org_repurge(wire, ORG)
    delete_at, consumed, purge_times = now[0], 0, []
    while consumed < len(wire.sent) and consumed < 60:
        topic, signed, published = wire.sent[consumed]
        lag = rng.uniform(0, 590)
        now[0] = max(published + lag, published + REPURGE_HOP_S)          # delivered after its hold and its lag
        keys = await envelope._keys()
        opened = await envelope.open_envelope(topic, signed, keys=keys, now=lambda: now[0])
        msg = StreamMessage(**opened.message, org_ctx=opened.ctx)
        await cache.get_or_compute(_key(f"late{consumed}"), _text)       # something to purge each time
        with bind_optional(envelope.ctx_of(msg)):
            events = [e.event async for e in handler.process_event(msg.eventType, msg.payload) if e.event != IndexingEvent.START_DOWNLOAD]
        assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
        assert store.paths(ORG, REC) == []
        purge_times.append(now[0] - delete_at)
        consumed += 1
    assert consumed < 60, "the chain did not end"
    assert purge_times[-1] > repurge_horizon_s(), f"last purge at {purge_times[-1]:.0f} s"
    assert all(b - a <= REPURGE_HOP_S + 590 + 1e-6 for a, b in zip([0.0, *purge_times], purge_times)), purge_times


# -- p8: run_on_loop ------------------------------------------------------------------------------------------
class _LoopThread:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="qa-producer-loop", daemon=True)
        self.thread.start()

    def run(self, coro: Any) -> Any:  # noqa: ANN401
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    def stop(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)


class _LoopBoundProducer:
    """A producer whose client belongs to ``loop``: awaited from another loop it fails as aiokafka would."""

    def __init__(self, loop: asyncio.AbstractEventLoop, fail: int = 0) -> None:
        self.loop = loop
        self.fail = fail
        self.calls: list[tuple[str, dict, str]] = []
        self.log: list[str] = []

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("Future attached to a different loop")
        await asyncio.sleep(0.01)
        self.log.append(event_type)
        if event_type != EventTypes.PURGE_PAID_RESULTS.value:
            return True                                              # the handler's other publishes (mocked graph)
        if self.fail:
            self.fail -= 1
            raise ConnectionError("broker down")
        self.calls.append((threading.current_thread().name, payload, key or ""))
        return True


def _handler_on(lt: _LoopThread, producer: Any) -> Any:  # noqa: ANN401
    async def build() -> Any:  # noqa: ANN401
        return _record_handler(producer)

    handler = lt.run(build())
    assert handler._producer_loop is lt.loop
    return handler


async def test_producer_loop_a_delete_and_both_hop_kinds_publish_on_the_producer_loop(world: Any) -> None:  # noqa: ANN401
    store, _locks, cache = world
    lt = _LoopThread()
    try:
        producer = _LoopBoundProducer(lt.loop)
        handler = _handler_on(lt, producer)
        with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
            [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]
            [e async for e in handler.process_event("purgePaidResults", repurge_event_payload(ORG, REC))]
            [e async for e in handler.process_event("purgePaidResults", org_repurge_event_payload(ORG))]
        assert [c[0] for c in producer.calls] == ["qa-producer-loop"] * 3
        assert [c[1]["hop"] for c in producer.calls] == [1, 2, 2]
        assert [c[2] for c in producer.calls] == [REC, REC, ORG]
    finally:
        lt.stop()


async def test_producer_loop_b_a_publish_failure_on_the_producer_loop_fails_the_delete_and_the_redelivery_completes(world: Any) -> None:  # noqa: ANN401
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )

    store, _locks, cache = world
    lt = _LoopThread()
    try:
        producer = _LoopBoundProducer(lt.loop, fail=1)
        handler = _handler_on(lt, producer)
        await cache.get_or_compute(_key(), _text)
        with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
            with pytest.raises(ConnectionError) as err:
                [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]
            assert MessageErrorClassifier.classify_by_exception(err.value) == MessageErrorType.TRANSIENT
            assert store.paths(ORG, REC) == [] and producer.calls == []
            [e async for e in handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)]      # redelivered
        assert len(producer.calls) == 1 and producer.calls[0][1]["hop"] == 1
        with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
            producer.fail = 1
            with pytest.raises(ConnectionError):
                [e async for e in handler.process_event("purgePaidResults", producer.calls[0][1])]
    finally:
        lt.stop()


async def test_producer_loop_c_a_stopped_producer_loop_raises_promptly_and_leaks_no_coroutine(world: Any) -> None:  # noqa: ANN401
    from app.services.messaging.error_classifier import (
        MessageErrorClassifier,
        MessageErrorType,
    )

    lt = _LoopThread()
    producer = _LoopBoundProducer(lt.loop)
    handler = _handler_on(lt, producer)
    lt.stop()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with bind(OrgContext(ORG, None, "service", "envelope", "qa")):
            with pytest.raises(RuntimeError) as err:
                await asyncio.wait_for(_drain(handler.process_event(EventTypes.DELETE_RECORD.value, DELETE)), 5)
        import gc
        gc.collect()
    assert "not running" in str(err.value)
    assert MessageErrorClassifier.classify_by_exception(err.value) == MessageErrorType.TRANSIENT
    # (record.py's own mocked graph calls warn too; only the producer's coroutine matters here)
    assert not [w for w in caught if "never awaited" in str(w.message) and "send_event" in str(w.message)]
    lt.loop.close()


async def _drain(gen: Any) -> list:  # noqa: ANN401
    return [e async for e in gen]


async def test_the_run_manifest_is_not_written_past_the_stage_deadline() -> None:
    """The open and the final manifest are objects too: none may land after the chain's last purge."""
    store = MemoryResultStore()
    scope = RunScope(ORG, REC, "rev1", "parse")
    scope.started_ms -= int((locks_module.stage_deadline_s() + 1) * 1000)
    await scope.note(store, _key().relative_path(), opens=True)
    await scope.finish(store)
    assert store.paths(ORG, REC) == []


async def test_a_manifest_write_slower_than_the_write_deadline_is_abandoned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(locks_module, "WRITE_DEADLINE_S", 0.05)
    store = MemoryResultStore()

    async def slow(*_a: Any) -> None:  # noqa: ANN401
        await asyncio.sleep(0.3)

    store.replace = slow  # type: ignore[method-assign]
    scope = RunScope(ORG, REC, "rev1", "parse")
    with pytest.raises(TimeoutError):
        await scope.note(store, _key().relative_path(), opens=True)
    with pytest.raises(TimeoutError):
        await scope.finish(store)
