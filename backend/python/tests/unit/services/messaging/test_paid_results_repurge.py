"""A record delete is completed by a durable delayed re-purge, and the delete handler uses the fence."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import ProcessingError
from app.services.messaging.config import IndexingEvent
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.locks import (
    LOCK_TTL_MS,
    REPURGE_DELAY_S,
    WRITE_DEADLINE_S,
    InProcessLocks,
    RedisLocks,
)
from app.services.paid_cache.repurge import repurge_event_payload, schedule_repurge
from app.services.paid_cache.run import RunScope, clear_run
from tests.support.paid_cache import MemoryResultStore
from tests.support.record_handler import make_record_handler

ORG, REC = "org-1", "rec-1"
KEY = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")
DELETE = {"recordId": REC, "orgId": ORG, "virtualRecordId": "vr"}


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> tuple[MemoryResultStore, InProcessLocks, PaidResultCache]:
    store, locks = MemoryResultStore(), InProcessLocks()
    cache = PaidResultCache(store, locks)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)
    yield store, locks, cache
    clear_run()


def _handler(producer: Any = None) -> Any:  # noqa: ANN401 - a RecordEventHandler over mocks
    handler = make_record_handler(producer=producer)
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value={"_key": REC, "orgId": ORG, "virtualRecordId": "vr"})
    handler.event_processor.processor.indexing_pipeline.bulk_delete_embeddings = AsyncMock()
    handler._request_code_edge_build_for_deleted = AsyncMock()
    handler._hand_over_shared_copy = AsyncMock()
    return handler


async def _events(handler: Any, event_type: str, payload: dict) -> list:  # noqa: ANN401
    return [e async for e in handler.process_event(event_type, payload) if e.event != IndexingEvent.START_DOWNLOAD]


# -- the fence: the handler's delete refuses later writes (QA-S7a-16) ---------------------------------------------

async def test_a_deleted_record_takes_no_later_write_through_the_handler(world: Any) -> None:  # noqa: ANN401
    store, _locks, cache = world
    await _events(_handler(), EventTypes.DELETE_RECORD.value, DELETE)

    async def compute() -> str:
        return "late"

    # Every kind of late write: a result (put), a refresh run's overwrite, and a manifest.
    await cache.get_or_compute(KEY, compute)
    await cache.store.overwrite(ORG, REC, KEY.relative_path(), b"{}")
    scope = RunScope(ORG, REC, "rev", "parse")
    await scope.note(cache.store, KEY.relative_path(), opens=True)
    await scope.finish(cache.store)

    assert store.paths(ORG, REC) == []


# -- the durable delayed re-purge (D-155) ----------------------------------------------------------------------

async def test_the_delete_handler_publishes_a_delayed_repurge_after_purging(world: Any) -> None:  # noqa: ANN401
    store, _locks, _cache = world
    store.files[(ORG, REC, KEY.relative_path())] = (b"{}", 1)
    producer = AsyncMock()
    before = time.time()

    await _events(_handler(producer), EventTypes.DELETE_RECORD.value, DELETE)

    assert store.paths(ORG, REC) == []
    producer.send_event.assert_awaited_once()
    sent = producer.send_event.await_args.kwargs
    assert sent["topic"] == "record-events"
    assert sent["event_type"] == "purgePaidResults"
    assert sent["key"] == REC
    payload = sent["payload"]
    assert (payload["orgId"], payload["recordId"]) == (ORG, REC)
    # Due after the lock TTL plus the write deadline, with a margin: what a write that is in flight can outlast.
    assert payload["_retry_not_before"] >= before + LOCK_TTL_MS / 1000 + WRITE_DEADLINE_S
    assert payload["_retry_not_before"] - before == pytest.approx(REPURGE_DELAY_S, abs=5)
    assert REPURGE_DELAY_S > LOCK_TTL_MS / 1000 + WRITE_DEADLINE_S


async def test_a_delete_whose_repurge_cannot_be_published_is_redelivered(world: Any) -> None:  # noqa: ANN401
    producer = AsyncMock()
    producer.send_event.side_effect = ConnectionError("broker down")

    with pytest.raises(ConnectionError):
        await _events(_handler(producer), EventTypes.DELETE_RECORD.value, DELETE)


async def test_a_late_write_after_redis_loses_the_fence_is_removed_by_the_repurge(world: Any) -> None:  # noqa: ANN401
    """The whole path: delete, the marker is lost, an orphaned parse writes, the delayed event is delivered."""
    store, locks, cache = world
    producer = AsyncMock()
    await _events(_handler(producer), EventTypes.DELETE_RECORD.value, DELETE)
    locks._held.clear()                                   # Redis restarted: the fence is gone

    async def compute() -> str:
        return "late"

    scope = RunScope(ORG, REC, "rev1", "parse")
    for page in range(4):
        key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", f"p{page}")
        await scope.note(cache.store, key.relative_path(), opens=True)
        await cache.get_or_compute(key, compute)
    await scope.finish(cache.store)
    assert len(store.paths(ORG, REC)) == 5                # 4 pages and the manifest landed

    delivered = producer.send_event.await_args.kwargs
    events = await _events(_handler(), delivered["event_type"], delivered["payload"])

    assert [e.event for e in events] == ["parsing_complete", "indexing_complete"]
    assert store.paths(ORG, REC) == []


async def test_the_repurge_leaves_other_records_and_orgs_alone(world: Any) -> None:  # noqa: ANN401
    store, _locks, _cache = world
    for org, rec in ((ORG, "rec-2"), ("org-2", REC)):
        store.files[(org, rec, KEY.relative_path())] = (b"{}", 1)
    store.files[(ORG, REC, KEY.relative_path())] = (b"{}", 1)

    await _events(_handler(), "purgePaidResults", repurge_event_payload(ORG, REC))

    assert store.paths(ORG, REC) == []
    assert store.paths(ORG, "rec-2") != [] and store.paths("org-2", REC) != []


async def test_a_repurge_whose_storage_fails_raises_so_the_broker_redelivers(world: Any) -> None:  # noqa: ANN401
    store, _locks, _cache = world
    store.files[(ORG, REC, KEY.relative_path())] = (b"{}", 1)
    store.failing = {"purge"}

    with pytest.raises(ConnectionError):
        await _events(_handler(), "purgePaidResults", repurge_event_payload(ORG, REC))
    store.failing = set()
    await _events(_handler(), "purgePaidResults", repurge_event_payload(ORG, REC))
    assert store.paths(ORG, REC) == []


@pytest.mark.parametrize("payload", [{"recordId": REC}, {"orgId": ORG}, {}])
async def test_a_repurge_without_its_org_or_record_is_refused(world: Any, payload: dict) -> None:  # noqa: ANN401
    with pytest.raises(ProcessingError):
        await _events(_handler(), "purgePaidResults", payload)


async def test_no_producer_means_the_delete_is_not_acknowledged(world: Any) -> None:  # noqa: ANN401
    with pytest.raises(RuntimeError):
        await schedule_repurge(None, ORG, REC)


# -- the marker that reached this process only (QA-S7a-17) ----------------------------------------------------------

async def test_a_delete_whose_marker_reached_only_this_process_is_not_called_fenced_and_is_logged(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    async def broken(_cfg: object) -> object:
        raise ConnectionError("redis config unreadable")

    monkeypatch.setattr(access, "_redis_locks_of", broken)
    store = MemoryResultStore()
    store.files[(ORG, REC, KEY.relative_path())] = (b"{}", 1)
    backend = access._redis_locks(object(), logging.getLogger("paid-cache-test"))  # type: ignore[arg-type]
    cache = PaidResultCache(store, backend, logger=logging.getLogger("paid-cache-test"))

    with caplog.at_level(logging.WARNING, logger="paid-cache-test"):
        fenced = await cache.delete_record(ORG, REC)

    assert fenced is False
    assert store.paths(ORG, REC) == []                    # the purge still ran
    assert any("this process only" in r.getMessage() for r in caplog.records)


async def test_a_delete_with_a_shared_backend_is_fenced() -> None:
    class Shared(InProcessLocks):
        shared = True

    assert await PaidResultCache(MemoryResultStore(), Shared()).delete_record(ORG, REC) is True


# -- the lock keys carry the messaging client's namespace (QA-S7a-20, m17) ------------------------------------------

async def test_the_redis_locks_are_keyed_under_the_messaging_namespace(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.redis import connection_provider_factory as factory
    from app.services.redis.config import RedisConnectionConfig

    provider = MagicMock()
    provider.key_namespace = "tenant-ns"
    provider.create_client.return_value = MagicMock()
    monkeypatch.setattr(factory, "get_redis_provider", lambda *_a, **_k: provider)
    monkeypatch.setattr(RedisConnectionConfig, "from_redis_config", classmethod(lambda cls, _c: cls()))
    config_service = MagicMock()
    config_service.get_redis_config = AsyncMock(return_value={})

    locks = await access._redis_locks_of(config_service)

    assert isinstance(locks, RedisLocks)
    assert locks.key_of("pc:lock:x") == "tenant-ns:pc:lock:x"


# -- the final manifest records when the run completed (QA-S7a-20, m06) ----------------------------------------------

async def test_the_final_manifest_records_the_completion_time_not_the_start_time() -> None:
    now = [1_000.0]
    scope = RunScope(ORG, REC, "rev", "index", clock=lambda: now[0])
    store = MemoryResultStore()
    await scope.note(store, KEY.relative_path(), opens=True)
    now[0] += 90.0

    await scope.finish(store)

    body = json.loads(store.files[(ORG, REC, "manifests/rev/index.json")][0])
    assert body["startedMs"] == 1_000_000
    assert body["completedMs"] == 1_090_000


# -- concurrent flushes never leave a stale open manifest (QA-S7a-18) ------------------------------------------------

@pytest.mark.parametrize("seed", range(20))
async def test_concurrent_flushes_never_leave_a_stale_open_manifest(seed: int, monkeypatch: pytest.MonkeyPatch) -> None:
    import random

    from app.services.paid_cache import run as run_module

    monkeypatch.setattr(run_module, "FLUSH_KEYS", 3)
    rng = random.Random(seed)

    class Jitter(MemoryResultStore):
        async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
            await asyncio.sleep(rng.random() * 0.003)
            await super().replace(org_id, record_id, path, data)

    store = Jitter()
    scope = RunScope(ORG, REC, "rev", "parse")
    paths = [f"ocr_page/{i:064x}.json" for i in range(40)]

    async def note(path: str) -> None:
        await asyncio.sleep(rng.random() * 0.01)
        await scope.note(store, path, opens=True)

    await asyncio.gather(*(note(p) for p in paths))
    await scope.flush(store)                              # what a run that then fails leaves behind

    body = json.loads(store.files[(ORG, REC, "manifests/rev/parse.open.json")][0])
    assert set(paths) <= set(body["keys"])


async def test_a_flush_that_races_the_final_manifest_does_not_bring_the_open_one_back() -> None:
    gate = asyncio.Event()

    class Slow(MemoryResultStore):
        async def replace(self, org_id: str, record_id: str, path: str, data: bytes) -> None:
            if path.endswith(".open.json"):
                await gate.wait()
            await super().replace(org_id, record_id, path, data)

    store = Slow()
    scope = RunScope(ORG, REC, "rev", "parse")
    first = asyncio.create_task(scope.note(store, "ocr_page/a.json", opens=True))     # an open write in flight
    await asyncio.sleep(0.01)
    finishing = asyncio.create_task(scope.finish(store))
    await asyncio.sleep(0.01)
    gate.set()
    await asyncio.gather(first, finishing)

    assert (ORG, REC, "manifests/rev/parse.json") in store.files
    assert (ORG, REC, "manifests/rev/parse.open.json") not in store.files
