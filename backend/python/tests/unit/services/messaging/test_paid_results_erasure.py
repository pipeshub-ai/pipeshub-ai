"""Deleting a record or an org deletes its stored model outputs; a storage failure is retried by the broker."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.services.messaging.config import IndexingEvent
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.run import clear_run, current_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler

ORG, REC = "org-1", "rec-1"
PAGE = f"ocr_page/{'a' * 64}.json"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryResultStore:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    yield memory
    clear_run()


def _seed(store: MemoryResultStore) -> None:
    for org, rec in ((ORG, REC), (ORG, "rec-2"), ("org-2", REC)):
        store.files[(org, rec, PAGE)] = (b"{}", 1)
        store.files[(org, rec, "manifests/rev/parse.json")] = (b"{}", 1)


async def _events(handler, event_type: str, payload: dict) -> list:
    return [e async for e in handler.process_event(event_type, payload) if e.event != IndexingEvent.START_DOWNLOAD]


def _handler(*, purged: bool = False) -> Any:  # noqa: ANN401 - a RecordEventHandler built over mocks
    """``purged``: the row a deleteRecord finds, in the trash as the purge leaves it (a live row is a record pushed again)."""
    handler = make_record_handler()
    row = {"_key": REC, "orgId": ORG, "virtualRecordId": "vr", **({"isDeleted": True} if purged else {})}
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value=row)
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()
    return handler


async def test_record_and_org_purge(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler(purged=True)

    events = await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})

    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    assert store.paths(ORG, REC) == []                       # the record's folder, manifests included
    assert store.paths(ORG, "rec-2") != []                   # another record of the org
    assert store.paths("org-2", REC) != []                   # the same record id in another org


async def test_a_record_delete_without_an_org_takes_it_from_the_record(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler(purged=True)

    await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "virtualRecordId": "vr"})

    assert store.paths(ORG, REC) == []
    assert store.paths("org-2", REC) != []


async def test_a_storage_failure_raises_so_the_broker_redelivers_until_it_succeeds(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler(purged=True)
    store.failing = {"purge"}

    with pytest.raises(ConnectionError):
        await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})
    assert store.paths(ORG, REC) != []                       # nothing was acknowledged as deleted

    store.failing = set()                                    # the broker redelivers
    events = await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    assert store.paths(ORG, REC) == []


async def test_a_record_with_nothing_stored_deletes_cleanly(store: MemoryResultStore) -> None:
    handler = _handler(purged=True)

    events = await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})

    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]


async def test_the_purge_happens_even_with_the_cache_switched_off(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_CACHE", "off")
    _seed(store)

    await _events(_handler(purged=True), EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})

    assert store.paths(ORG, REC) == []


async def test_an_org_delete_removes_the_orgs_whole_folder(store: MemoryResultStore) -> None:
    from app.services.messaging.kafka.handlers.entity import EntityEventService

    _seed(store)
    app_container = MagicMock()
    app_container.messaging_producer = AsyncMock()
    service = EntityEventService(MagicMock(), AsyncMock(), app_container)
    service._remove_mcp_data = AsyncMock()
    service._remove_personal_toolsets = AsyncMock()
    service._remove_api_toolset_data = AsyncMock()

    assert await service.process_event("orgDeleted", {"orgId": ORG}) is True

    assert store.paths(ORG, REC) == [] and store.paths(ORG, "rec-2") == []
    assert store.paths("org-2", REC) != []


async def test_an_org_delete_whose_purge_fails_is_redelivered(store: MemoryResultStore) -> None:
    from app.services.messaging.kafka.handlers.entity import EntityEventService

    _seed(store)
    store.failing = {"purge"}
    service = EntityEventService(MagicMock(), AsyncMock(), MagicMock(messaging_producer=AsyncMock()))
    service._remove_mcp_data = AsyncMock()
    service._remove_personal_toolsets = AsyncMock()
    service._remove_api_toolset_data = AsyncMock()

    with pytest.raises(ConnectionError):
        await service.process_event("orgDeleted", {"orgId": ORG})
    assert store.paths(ORG, REC) != []

    store.failing = set()
    assert await service.process_event("orgDeleted", {"orgId": ORG}) is True
    assert store.paths(ORG, REC) == []


async def _reindex(handler, payload: dict) -> None:
    """Runs a reindex event as far as it goes; what follows the refresh step is not under test.

    Only a failure after the refresh decision is tolerated: the decision (``refresh_requested``) must have been
    taken, so an error raised before it cannot hide behind the suppression.
    """
    import contextlib
    from unittest.mock import patch

    from app.services.paid_cache import run as paid_run

    decided: list[object] = []
    real = paid_run.refresh_requested

    def spy(event_payload: object) -> bool:
        decided.append(event_payload)
        return real(event_payload)

    with patch.object(paid_run, "refresh_requested", spy), contextlib.suppress(Exception):
        await _events(handler, EventTypes.REINDEX_RECORD.value, payload)
    assert decided, "the reindex failed before it decided whether to refresh"


async def test_refresh_paid_outputs_purges_the_set_first_and_binds_a_bypassing_run(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler()

    await _reindex(handler, {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True})

    assert store.paths(ORG, REC) == []                       # the record's set is gone before the run
    assert store.paths(ORG, "rec-2") != [] and store.paths("org-2", REC) != []
    run = current_run()
    assert run is not None and run.refresh and run.matches(ORG, REC)


async def test_refresh_paid_outputs_takes_the_org_from_the_record_when_the_event_has_none(store: MemoryResultStore) -> None:
    _seed(store)

    await _reindex(_handler(), {"recordId": REC, "refreshPaidOutputs": True})

    assert store.paths(ORG, REC) == [] and store.paths("org-2", REC) != []


async def test_a_refresh_whose_purge_fails_fails_the_event_so_it_is_retried(store: MemoryResultStore) -> None:
    _seed(store)
    store.failing = {"purge"}
    handler = _handler()

    with pytest.raises(ConnectionError):
        await _events(handler, EventTypes.REINDEX_RECORD.value, {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True})
    assert store.paths(ORG, REC) != []                       # a run never starts over a half-purged set


async def test_a_refresh_on_a_storage_without_the_cutoff_route_purges_without_a_cutoff_not_before_zero(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No purge-cutoff route (an earlier storage release), paid-result writes off: no ``before``, never ``before=0``."""
    monkeypatch.delenv("PAID_CACHE_WRITE", raising=False)
    async def no_route(org: str, rec: str) -> None:
        return None

    monkeypatch.setattr(store, "purge_cutoff", no_route)
    sent: list[int | None] = []
    real_purge = store.purge_record

    async def purge(org: str, rec: str, *, before_ms: int | None = None) -> None:
        sent.append(before_ms)
        await real_purge(org, rec, before_ms=before_ms)

    monkeypatch.setattr(store, "purge_record", purge)
    _seed(store)

    await _reindex(_handler(), {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True})

    assert sent == [None]
    assert store.paths(ORG, REC) == []


async def test_a_refresh_on_a_storage_without_the_cutoff_route_fails_with_writes_on_and_keeps_the_vectors(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With writes on, a purge without a cutoff could remove what a delivery that took over stored, so the refresh
    fails (retried, then terminal) before the vector store is touched, and nothing is purged."""
    from app.services.paid_cache.cache import PurgeCutoffUnavailable

    async def no_route(org: str, rec: str) -> None:
        return None

    monkeypatch.setenv("PAID_CACHE_WRITE", "on")
    monkeypatch.setattr(store, "purge_cutoff", no_route)
    _seed(store)
    handler = _handler()
    bulk = handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings

    with pytest.raises(PurgeCutoffUnavailable):
        await _events(handler, EventTypes.REINDEX_RECORD.value, {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True})

    assert store.paths(ORG, REC) != []                       # nothing purged
    assert "purge" not in store.ops
    bulk.assert_not_awaited()                                # the record's embeddings are still there


async def test_a_republished_refresh_keeps_its_identity_through_the_delivery_id_and_purges_once(store: MemoryResultStore) -> None:
    """A broker requeue gives the event a new envelope event id; the delivery id the consumers keep names the refresh."""
    _seed(store)
    base = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_delivery_id": "record-events-0-41"}

    await _events(_handler(), EventTypes.REINDEX_RECORD.value, {**base, "_event_id": "envelope-1"})
    first = store.ops["purge"]
    store.files[(ORG, REC, f"ocr_page/{'e' * 64}.json")] = (b"{}", store.now_ms + 5)      # the first delivery's run stored it
    await _events(_handler(), EventTypes.REINDEX_RECORD.value, {**base, "_event_id": "envelope-2"})

    assert first == 1 and store.ops["purge"] == 1                      # the second delivery reuses; it did not purge again
    assert (ORG, REC, f"ocr_page/{'e' * 64}.json") in store.files


@pytest.mark.parametrize("payload", [{"forceReindex": True}, {"refreshPaidOutputs": "true"}, {"refreshPaidOutputs": False}, {}])
async def test_anything_but_an_explicit_refresh_keeps_the_stored_set(store: MemoryResultStore, payload: dict) -> None:
    _seed(store)

    await _reindex(_handler(), {"recordId": REC, "orgId": ORG, **payload})

    assert store.paths(ORG, REC) != []
    run = current_run()
    assert run is None or not run.refresh


def test_only_an_explicit_true_is_a_refresh() -> None:
    from app.services.paid_cache.run import refresh_requested

    assert refresh_requested({"refreshPaidOutputs": True})
    for not_a_refresh in ({"refreshPaidOutputs": "true"}, {"refreshPaidOutputs": 1}, {"forceReindex": True}, {}, None, "x"):
        assert not refresh_requested(not_a_refresh)


async def test_force_reindex_alone_keeps_the_stored_set(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler()
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value=None)

    # A reindex carrying only forceReindex reaches the "record not found" drain without touching the set.
    await _events(handler, EventTypes.REINDEX_RECORD.value, {"recordId": REC, "orgId": ORG, "forceReindex": True})

    assert store.paths(ORG, REC) != []
    assert current_run() is None


async def test_a_delete_with_no_org_in_the_event_or_the_record_uses_the_bound_org(store: MemoryResultStore) -> None:
    from app.core.org_context import OrgContext, bind

    _seed(store)
    handler = _handler()
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value=None)      # the record is gone

    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")):
        await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "virtualRecordId": "vr"})

    assert store.paths(ORG, REC) == []                       # erased in the bound org
    assert store.paths("org-2", REC) != []


async def test_a_refresh_refused_for_a_user_without_access_purges_nothing(store: MemoryResultStore) -> None:
    from app.services.messaging.kafka.handlers import record as record_module

    _seed(store)
    handler = _handler()
    handler._parse_now_refusal = AsyncMock(return_value=(record_module._PARSE_NOW_NO_ACCESS, False))

    await _events(
        handler,
        EventTypes.REINDEX_RECORD.value,
        {"recordId": REC, "orgId": ORG, "parseNow": True, "requestedByUserId": "u1", "refreshPaidOutputs": True},
    )

    assert handler._parse_now_refusal.await_count == 1
    assert store.paths(ORG, REC) != []                       # the refused event wrote and erased nothing
    assert current_run() is None


async def test_a_failed_graph_read_on_a_delete_without_an_org_is_retried_not_fenced(store: MemoryResultStore) -> None:
    from app.core.org_context import OrgContext, bind

    _seed(store)
    handler = _handler()
    async def get_document(*_a: object, raise_on_error: bool = False, **_k: object) -> dict[str, None]:
        if raise_on_error:
            raise ConnectionError("graph unavailable")
        return {"orgId": None}                                # a non-raising read answers with no org on a blip

    handler.event_processor.graph_provider.get_document = get_document

    with bind(OrgContext(ORG, None, "service", "envelope", "req-1")), pytest.raises(ConnectionError):
        await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "virtualRecordId": "vr"})

    assert store.paths(ORG, REC) != []                       # nothing erased or fenced on a failed read


async def test_a_redelivered_refresh_event_reuses_what_its_first_delivery_stored(store: MemoryResultStore) -> None:
    """The first delivery purges and pays; a redelivery of the same event (an id the consumer stamps) must not purge
    again and pay for the same pages a second time."""
    _seed(store)
    handler = _handler()
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-1"}

    await _reindex(handler, dict(payload))
    first_run = current_run()
    assert first_run is not None and first_run.refresh and store.paths(ORG, REC) == []

    store.files[(ORG, REC, PAGE)] = (b"{}", 1)                          # a page the first delivery paid for and stored
    clear_run()
    await _reindex(handler, dict(payload))                              # the broker redelivers the same event

    assert store.paths(ORG, REC) == [PAGE]                              # not purged again
    run = current_run()
    assert run is not None and run.refresh and run.reuse_prior_attempt  # still a refresh: reuses only its own earlier pages

    clear_run()
    await _reindex(handler, {**payload, "_event_id": "evt-2"})          # a new request purges and bypasses again
    assert store.paths(ORG, REC) == [] and current_run() is not None and current_run().refresh


async def test_a_retried_refresh_request_pays_no_page_again_that_the_first_attempt_paid(store: MemoryResultStore) -> None:
    from app.services.paid_cache.keys import CacheKey
    from tests.support.paid_cache import record_in_flight

    handler = _handler()
    cache = access.paid_cache_for(MagicMock())
    paid: list[str] = []
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-9"}
    keys = [CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"page{n}") for n in range(3)]

    async def pay_pages() -> None:
        for key in keys:
            async def compute(page: str = key.input_digest) -> str:
                paid.append(page)
                return "text"

            await cache.get_or_compute(key, compute)

    with record_in_flight(ORG, REC):
        await _reindex(handler, dict(payload))                       # the first attempt: purge, then pay every page
        await pay_pages()
        assert len(paid) == 3
        await access.abandon_run(current_run(), MagicMock())         # it fails: its manifest is brought up to date
        clear_run()
        await _reindex(handler, dict(payload))                       # its retry (the same event, redelivered)
        await pay_pages()
    assert len(paid) == 3, f"the retry paid again for {len(paid) - 3} page(s)"


async def test_a_refresh_delivery_that_overlaps_a_purge_is_handed_back_without_an_attempt_and_runs_nothing(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
    from app.services.paid_cache import cache as cache_module
    from app.services.paid_cache.locks import refresh_name
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld

    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.05)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.01)
    _seed(store)
    handler = _handler()
    cache = access.paid_cache_for(MagicMock())
    backend = await cache._backend()
    assert await backend.acquire(refresh_name(ORG, REC, "evt-7") + ":lease", 60_000)      # another delivery's live lease

    with pytest.raises(RefreshPurgeHeld) as raised:
        await _events(handler, EventTypes.REINDEX_RECORD.value, {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-7"})

    assert isinstance(raised.value, RequeueWithoutAttempt) and raised.value.counts_toward_backstop is False
    assert raised.value.delay_s and raised.value.delay_s > 0                                # held back, then redelivered
    assert current_run() is None                                                        # no reuse run opened
    assert store.paths(ORG, REC) != []                                                  # nothing purged by this delivery


async def test_a_long_purge_and_its_duplicate_both_end_with_the_record_completed(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The holder purges for a long time (an event stands in for 180 s); the duplicate is handed back again and again
    without an attempt being spent, and once the holder has marked it reuses and completes."""
    import asyncio

    from app.services.paid_cache import cache as cache_module
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld

    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.02)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.005)
    _seed(store)
    holder_handler, duplicate_handler = _handler(), _handler()
    cache = access.paid_cache_for(MagicMock())
    release_purge = asyncio.Event()
    real_purge = cache.purge_record

    async def slow_purge(org: str, rec: str, *, before_ms: int | None = None) -> None:
        await release_purge.wait()                                 # the purge is still running
        await real_purge(org, rec, before_ms=before_ms)

    cache.purge_record = slow_purge  # type: ignore[method-assign]
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-180"}

    holder = asyncio.ensure_future(_events(holder_handler, EventTypes.REINDEX_RECORD.value, dict(payload)))
    await asyncio.sleep(0.05)                                      # the holder has its lease
    handed_back = 0
    for _ in range(8):                                             # far more than the 3-attempt budget
        with pytest.raises(RefreshPurgeHeld):
            await _events(duplicate_handler, EventTypes.REINDEX_RECORD.value, dict(payload))
        handed_back += 1
    release_purge.set()
    holder_events = await holder
    assert holder_events, "the holder did not run to its end"                                  # the holder completes
    await _reindex(duplicate_handler, dict(payload))               # the duplicate, delivered again, now completes
    run = current_run()
    assert handed_back == 8 and run is not None and run.reuse_prior_attempt


async def _held_handler(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    from app.services.paid_cache import cache as cache_module
    from app.services.paid_cache.locks import refresh_name

    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.02)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.005)
    _seed(store)
    handler = _handler()
    handler._trigger_next_queued_duplicate = AsyncMock()
    # A record whose release path would trigger the next queued duplicate, were the bookkeeping to run.
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={
        "_key": REC, "orgId": ORG, "virtualRecordId": "vr", "indexingStatus": "ENABLE_MULTIMODAL_MODELS",
    })
    cache = access.paid_cache_for(MagicMock())
    backend = await cache._backend()
    assert await backend.acquire(refresh_name(ORG, REC, "evt-f") + ":lease", 600_000)         # a live holder's lease
    return handler


async def test_a_handed_back_refresh_on_its_last_counted_delivery_does_not_fail_the_record(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld

    handler = await _held_handler(store, monkeypatch)
    updates = handler.event_processor.graph_provider.update_node
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-f", "is_final_failure": True}

    with pytest.raises(RefreshPurgeHeld):
        await _events(handler, EventTypes.REINDEX_RECORD.value, payload)

    assert not [c for c in updates.await_args_list if "FAILED" in str(c)]              # the record is not marked FAILED
    handler._trigger_next_queued_duplicate.assert_not_awaited()                          # no queued duplicate triggered
    updates.assert_not_awaited()                                                         # no status rewrite at all


async def test_a_duplicate_is_handed_back_for_as_long_as_the_holder_lives_and_never_fails_the_record(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past what used to be the 20th hand-back, on a delivery the consumer marks final: no FAILED, no triggered duplicate."""
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld

    handler = await _held_handler(store, monkeypatch)
    updates = handler.event_processor.graph_provider.update_node
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-f", "is_final_failure": True}

    for _ in range(25):
        with pytest.raises(RefreshPurgeHeld):                                          # never the failure path
            await _events(handler, EventTypes.REINDEX_RECORD.value, dict(payload))

    assert not [c for c in updates.await_args_list if "FAILED" in str(c)]
    handler._trigger_next_queued_duplicate.assert_not_awaited()
    updates.assert_not_awaited()


async def test_a_holder_that_renews_for_ever_is_cut_off_and_when_it_resumes_it_removes_nothing_the_duplicate_stored(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio
    import time

    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
    from app.services.paid_cache import cache as cache_module
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld

    monkeypatch.setattr(cache_module, "LOCK_TTL_MS", 150)                  # renewals every 50 ms
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_MAX_AGE_S", 0.3)      # the holder's hard deadline
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.02)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.005)
    _seed(store)
    holder_handler, duplicate_handler = _handler(), _handler()
    cache = access.paid_cache_for(MagicMock())
    resume = asyncio.Event()
    real_purge = cache.purge_record
    calls: list[str] = []

    async def purge(org: str, rec: str, *, before_ms: int | None = None) -> None:
        calls.append("holder" if not calls else "duplicate")
        if len(calls) == 1:
            await resume.wait()                                             # the holder's DELETE is slow
        await real_purge(org, rec, before_ms=before_ms)

    cache.purge_record = purge  # type: ignore[method-assign]
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-9"}

    holder = asyncio.ensure_future(_events(holder_handler, EventTypes.REINDEX_RECORD.value, dict(payload)))
    await asyncio.sleep(0.05)
    with pytest.raises(RefreshPurgeHeld):                                   # inside the deadline the duplicate waits
        await _events(duplicate_handler, EventTypes.REINDEX_RECORD.value, dict(payload))

    with caplog.at_level("WARNING"):
        for _ in range(100):                                               # delivered again until the holder is cut off
            try:
                await _events(duplicate_handler, EventTypes.REINDEX_RECORD.value, dict(payload))
                break
            except RefreshPurgeHeld:
                await asyncio.sleep(0.02)
        else:
            raise AssertionError("the duplicate never ran")
    assert calls == ["holder", "duplicate"]
    assert any("no longer renewed" in r.getMessage() for r in caplog.records)   # a visible reason

    store.now_ms = int(time.time() * 1000) + 60_000                         # the duplicate's run stores a result since
    stored = f"ocr_page/{'d' * 64}.json"
    store.files[(ORG, REC, stored)] = (b"{}", store.now_ms)
    clear_run()

    resume.set()                                                           # the holder's DELETE now lands, late
    with pytest.raises(RequeueWithoutAttempt):                              # lost its lease: handed back, nothing more
        await holder

    assert stored in store.paths(ORG, REC), "the late DELETE removed an object the duplicate stored"
    assert current_run() is None, "the lost-lease holder opened a run"
    async with cache.refresh_purge(ORG, REC, "evt-9") as redelivery:        # and it wrote no marker: the duplicate's stands
        assert redelivery is False


async def test_a_delete_in_flight_longer_than_the_lease_keeps_it_renewed_so_the_duplicate_waits_for_it(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One storage DELETE can take as long as the lease (120 s read limit): the heartbeat renews through the call."""
    import asyncio

    from app.services.paid_cache import cache as cache_module
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld

    monkeypatch.setattr(cache_module, "LOCK_TTL_MS", 150)                  # the lease is shorter than the call
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.02)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.005)
    _seed(store)
    holder_handler, duplicate_handler = _handler(), _handler()
    cache = access.paid_cache_for(MagicMock())
    real_purge = cache.purge_record
    calls: list[str] = []

    async def purge(org: str, rec: str, *, before_ms: int | None = None) -> None:
        calls.append("purge")
        await asyncio.sleep(0.6)                                           # a call far longer than the lease, under the deadline
        await real_purge(org, rec, before_ms=before_ms)

    cache.purge_record = purge  # type: ignore[method-assign]
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-slow"}
    holder = asyncio.ensure_future(_events(holder_handler, EventTypes.REINDEX_RECORD.value, dict(payload)))
    await asyncio.sleep(0.05)
    for _ in range(5):                                                     # while the call is in flight
        with pytest.raises(RefreshPurgeHeld):
            await _events(duplicate_handler, EventTypes.REINDEX_RECORD.value, dict(payload))
        await asyncio.sleep(0.04)
    await holder
    await _reindex(duplicate_handler, dict(payload))                       # the marker stands: the duplicate reuses
    assert calls == ["purge"]                                              # one purge only


async def test_after_the_holders_deadline_the_pages_are_paid_once_not_twice(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    import time

    from app.services.messaging.consumer_concurrency import RequeueWithoutAttempt
    from app.services.paid_cache import cache as cache_module
    from app.services.paid_cache.keys import CacheKey
    from app.services.paid_cache.refresh_handback import RefreshPurgeHeld
    from tests.support.paid_cache import record_in_flight

    monkeypatch.setattr(cache_module, "LOCK_TTL_MS", 150)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_MAX_AGE_S", 0.3)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_WAIT_S", 0.02)
    monkeypatch.setattr(cache_module, "REFRESH_PURGE_POLL_S", 0.005)
    _seed(store)
    holder_handler, duplicate_handler = _handler(), _handler()
    cache = access.paid_cache_for(MagicMock())
    resume = asyncio.Event()
    real_purge = cache.purge_record
    n = 0

    async def purge(org: str, rec: str, *, before_ms: int | None = None) -> None:
        nonlocal n
        n += 1
        if n == 1:
            await resume.wait()                                            # the holder's DELETE runs on and on
        await real_purge(org, rec, before_ms=before_ms)

    cache.purge_record = purge  # type: ignore[method-assign]
    payload = {"recordId": REC, "orgId": ORG, "refreshPaidOutputs": True, "_event_id": "evt-20m"}
    paid: list[str] = []
    keys = [CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"page{i}") for i in range(3)]

    async def pay_pages() -> None:
        for key in keys:
            async def compute(page: str = key.input_digest) -> str:
                paid.append(page)
                return "text"

            await cache.get_or_compute(key, compute)

    holder = asyncio.ensure_future(_events(holder_handler, EventTypes.REINDEX_RECORD.value, dict(payload)))
    await asyncio.sleep(0.05)
    with record_in_flight(ORG, REC):
        for _ in range(200):                                              # until the holder is cut off: the duplicate runs
            try:
                await _events(duplicate_handler, EventTypes.REINDEX_RECORD.value, dict(payload))
                break
            except RefreshPurgeHeld:
                await asyncio.sleep(0.02)
        run = current_run()
        assert run is not None and run.refresh
        await pay_pages()                                                 # the duplicate's run pays the three pages
        store.now_ms = int(time.time() * 1000) + 60_000
        resume.set()                                                      # the holder's late DELETE lands
        with pytest.raises(RequeueWithoutAttempt):                         # it lost its lease: no marker, no paid run
            await holder
    assert len(paid) == 3, f"{len(paid)} paid calls for 3 pages"


def test_a_failed_force_reindex_says_the_record_may_have_no_vectors_and_a_plain_failure_does_not() -> None:
    from app.services.messaging.kafka.handlers.record import (
        FORCE_REINDEX_FAILED_NOTE,
        with_force_reindex_note,
    )

    forced = {"recordId": REC, "forceReindex": True, "refreshPaidOutputs": True}
    assert with_force_reindex_note("The file could not be read.", forced) == f"The file could not be read. {FORCE_REINDEX_FAILED_NOTE}"
    assert with_force_reindex_note(None, forced) == FORCE_REINDEX_FAILED_NOTE
    assert with_force_reindex_note("The file could not be read.", {"recordId": REC, "forceReindex": True}) == "The file could not be read."
    assert with_force_reindex_note("The file could not be read.", {"recordId": REC, "refreshPaidOutputs": "true"}) == "The file could not be read."
