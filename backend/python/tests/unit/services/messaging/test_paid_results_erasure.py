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


def _handler() -> Any:  # noqa: ANN401 - a RecordEventHandler built over mocks
    handler = make_record_handler()
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={"_key": REC, "orgId": ORG, "virtualRecordId": "vr"})
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()
    return handler


async def test_record_and_org_purge(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler()

    events = await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})

    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    assert store.paths(ORG, REC) == []                       # the record's folder, manifests included
    assert store.paths(ORG, "rec-2") != []                   # another record of the org
    assert store.paths("org-2", REC) != []                   # the same record id in another org


async def test_a_record_delete_without_an_org_takes_it_from_the_record(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler()

    await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "virtualRecordId": "vr"})

    assert store.paths(ORG, REC) == []
    assert store.paths("org-2", REC) != []


async def test_a_storage_failure_raises_so_the_broker_redelivers_until_it_succeeds(store: MemoryResultStore) -> None:
    _seed(store)
    handler = _handler()
    store.failing = {"purge"}

    with pytest.raises(ConnectionError):
        await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})
    assert store.paths(ORG, REC) != []                       # nothing was acknowledged as deleted

    store.failing = set()                                    # the broker redelivers
    events = await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})
    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    assert store.paths(ORG, REC) == []


async def test_a_record_with_nothing_stored_deletes_cleanly(store: MemoryResultStore) -> None:
    handler = _handler()

    events = await _events(handler, EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})

    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]


async def test_the_purge_happens_even_with_the_cache_switched_off(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAID_CACHE", "off")
    _seed(store)

    await _events(_handler(), EventTypes.DELETE_RECORD.value, {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"})

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
    assert run is None or not run.refresh                               # reused by an ordinary run

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
        clear_run()
        await _reindex(handler, dict(payload))                       # its retry (the same event, redelivered)
        await pay_pages()
    assert len(paid) == 3, f"the retry paid again for {len(paid) - 3} page(s)"
