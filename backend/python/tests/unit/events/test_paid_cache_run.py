"""A record's paid model calls are listed in manifests, and the retention sweep runs when its revision completes."""
from __future__ import annotations

import json
import logging
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.pipeline.fingerprint import content_revision
from app.services.messaging.config import IndexingEvent
from app.services.paid_cache import access
from app.services.paid_cache.cache import PaidResultCache
from app.services.paid_cache.keys import CacheKey
from app.services.paid_cache.run import begin_run, clear_run, current_run
from app.services.parsing.interface import ParseResult, ParserProvider
from tests.support.paid_cache import MemoryResultStore, record_in_flight

ORG, REC = "org-1", "rec-1"
BODY = b"%PDF-1.4"


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> MemoryResultStore:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr(access, "paid_cache_for", lambda *_a, **_k: cache)

    # The sweep is queued detached; the test runs it where it stands.
    async def inline(coro: Any, *, name: str | None = None) -> None:  # noqa: ANN401 - a JSON value, model response or caller callable of any shape
        await coro

    spawned: list[Any] = []
    monkeypatch.setattr(access, "spawn_detached", lambda coro, name=None: spawned.append(coro))
    memory.spawned = spawned  # type: ignore[attr-defined]
    yield memory
    clear_run()


def _processor(sink: MagicMock) -> Any:  # noqa: ANN401 - an EventProcessor built over mocks
    from app.events.events import EventProcessor

    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={
        "_key": REC, "orgId": ORG, "recordName": "t.pdf", "recordType": "FILE", "indexingStatus": "NOT_STARTED",
        "mimeType": "application/pdf", "origin": "UPLOAD", "externalRecordId": "e", "connectorId": "c",
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    })
    graph.get_departments = AsyncMock(return_value=[])
    for name in ("batch_upsert_nodes", "batch_update_nodes", "update_node", "write_attempt_start"):
        setattr(graph, name, AsyncMock(return_value=True))
    graph.find_duplicate_records = AsyncMock(return_value=[])
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=[])
    stages = MagicMock()
    stages.on_indexed = AsyncMock(return_value=[])
    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="p")
    parsing = MagicMock()
    parsing.circuit_open = False
    block = Block(index=0, type=BlockType.TEXT, data="hello", format=DataFormat.TXT)
    parsing.parse = AsyncMock(return_value=ParseResult(
        block_container=BlocksContainer(blocks=[block], block_groups=[]), provider_used=ParserProvider.DEFAULT, metadata={},
    ))
    return EventProcessor(
        logger=logging.getLogger("test"), processor=processor, graph_provider=graph, config_service=MagicMock(),
        parsing_client=parsing, extraction_client=MagicMock(), sink_orchestrator=sink, stage_ingress=stages,
    )


def _event() -> dict[str, Any]:
    return {"eventType": "NEW_RECORD", "payload": {
        "recordId": REC, "orgId": ORG, "virtualRecordId": "vr-1", "extension": "pdf", "mimeType": "application/pdf",
        "recordName": "t.pdf", "buffer": BODY, "version": 1, "connectorName": "",
    }}


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_the_image_descriptions_of_a_revision_are_listed_in_its_manifest_and_the_sweep_is_queued(
    store: MemoryResultStore,
) -> None:
    key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", "i")
    sink = MagicMock()

    async def index(_ctx: object) -> None:
        # What the image describer does while the sink indexes: reads the record's cache inside the run.
        cache = access.paid_cache_for(MagicMock())
        await cache.get_or_compute(key, AsyncMock(return_value="a chart"))

    sink.index = AsyncMock(side_effect=index)
    ep = _processor(sink)

    with record_in_flight(ORG, REC):
        events = [e async for e in ep.on_event(_event())]

    assert IndexingEvent.INDEXING_COMPLETE in [e.event for e in events]
    rev = content_revision(BODY)
    final = store.files[(ORG, REC, f"manifests/{rev}/index.json")][0]
    assert json.loads(final)["keys"] == [key.relative_path()]
    assert (ORG, REC, f"manifests/{rev}/index.open.json") not in store.files
    assert len(store.spawned) == 1                                   # the sweep, queued off the index slot
    for coro in store.spawned:
        await coro


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_run_that_fails_keeps_its_open_manifest_and_queues_no_sweep(store: MemoryResultStore) -> None:
    key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", "i")
    sink = MagicMock()

    async def index(_ctx: object) -> None:
        cache = access.paid_cache_for(MagicMock())
        await cache.get_or_compute(key, AsyncMock(return_value="a chart"))
        raise ConnectionError("vector store down")

    sink.index = AsyncMock(side_effect=index)
    ep = _processor(sink)

    with record_in_flight(ORG, REC), pytest.raises(ConnectionError):
        async for _ in ep.on_event(_event()):
            pass

    rev = content_revision(BODY)
    assert (ORG, REC, f"manifests/{rev}/index.open.json") in store.files
    assert (ORG, REC, f"manifests/{rev}/index.json") not in store.files
    assert store.spawned == []
    # The paid object itself stays, for the retry.
    assert key.relative_path() in store.paths(ORG, REC)


def _txt_event(body: bytes = BODY) -> dict[str, Any]:
    return {"eventType": "NEW_RECORD", "payload": {
        "recordId": REC, "orgId": ORG, "virtualRecordId": "vr-1", "extension": "txt", "mimeType": "text/plain",
        "recordName": "t.txt", "buffer": body, "version": 1, "connectorName": "",
    }}


def _in_process(ep: Any, work: Any) -> None:  # noqa: ANN401 - a mock processor
    """The in-process ``process_txt_document``: reports parsing, pays through the record's cache (the transform
    pipeline: image descriptions, table summaries), then reports completion. That is the real processor's order."""

    async def process_txt_document(**_kwargs: Any) -> Any:  # noqa: ANN401
        yield MagicMock(event=IndexingEvent.PARSING_COMPLETE)
        await work()
        yield MagicMock(event=IndexingEvent.INDEXING_COMPLETE)

    ep.processor.process_txt_document = process_txt_document


async def _paid(key: CacheKey) -> None:
    await access.paid_cache_for(MagicMock()).get_or_compute(key, AsyncMock(return_value="a chart"))


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "false"})
async def test_the_default_in_process_pipeline_writes_the_same_manifest_and_queues_the_sweep(
    store: MemoryResultStore,
) -> None:
    """QA-S7a-6: the default deployment (``USE_PARSING_SERVICE`` off) stores paid results too."""
    key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", "i")
    ep = _processor(MagicMock())
    _in_process(ep, lambda: _paid(key))

    with record_in_flight(ORG, REC):
        events = [e async for e in ep.on_event(_txt_event())]

    assert IndexingEvent.INDEXING_COMPLETE in [e.event for e in events]
    rev = content_revision(BODY)
    final = json.loads(store.files[(ORG, REC, f"manifests/{rev}/index.json")][0])
    assert final["keys"] == [key.relative_path()]
    assert "completedMs" in final and "seq" in final
    assert (ORG, REC, f"manifests/{rev}/index.open.json") not in store.files
    assert len(store.spawned) == 1


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "false"})
async def test_the_in_process_pipeline_keeps_two_revisions_over_five(store: MemoryResultStore) -> None:
    """QA probe q8: five revisions through the default pipeline, then the sweep leaves the retained ones."""
    from app.services.paid_cache.retention import sweep_record

    ep = _processor(MagicMock())
    for rev in range(5):
        key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", f"rev{rev}")
        _in_process(ep, lambda key=key: _paid(key))
        with record_in_flight(ORG, REC):
            _ = [e async for e in ep.on_event(_txt_event(f"revision {rev}".encode()))]
        store.now_ms += 2 * 3600 * 1000
    for coro in store.spawned:
        coro.close()
    result = await sweep_record(store, ORG, REC, now_ms=store.now_ms)

    assert len(store.results(ORG, REC)) == 2
    assert result.deleted > 0


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "false"})
async def test_an_in_process_run_that_fails_keeps_its_open_manifest_and_queues_no_sweep(store: MemoryResultStore) -> None:
    key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", "i")
    ep = _processor(MagicMock())

    async def fail() -> None:
        await _paid(key)
        raise ConnectionError("vector store down")

    _in_process(ep, fail)
    with record_in_flight(ORG, REC), pytest.raises(ConnectionError):
        _ = [e async for e in ep.on_event(_txt_event())]

    rev = content_revision(BODY)
    assert (ORG, REC, f"manifests/{rev}/index.open.json") in store.files
    assert (ORG, REC, f"manifests/{rev}/index.json") not in store.files
    assert store.spawned == []


@pytest.mark.parametrize("service", ["true", "false"])
async def test_a_store_only_run_writes_its_manifest_but_queues_no_sweep(
    store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch, service: str,
) -> None:
    """``sweep=not store_only``: a record with automatic indexing off keeps what it paid, and sweeps nothing."""
    monkeypatch.setenv("USE_PARSING_SERVICE", service)
    monkeypatch.setattr("app.events.events.store_only_active", lambda: True)
    key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", "i")
    sink = MagicMock()

    async def index(_ctx: object) -> None:
        await _paid(key)

    sink.index = AsyncMock(side_effect=index)
    ep = _processor(sink)
    _in_process(ep, lambda: _paid(key))

    with record_in_flight(ORG, REC):
        event = _event() if service == "true" else _txt_event()
        _ = [e async for e in ep.on_event(event)]

    assert (ORG, REC, f"manifests/{content_revision(BODY)}/index.json") in store.files
    assert store.spawned == []


async def test_a_run_that_does_not_index_writes_its_manifest_but_queues_no_sweep(store: MemoryResultStore) -> None:
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")
    with record_in_flight(ORG, REC):
        run = access.start_run("rev", "parse")
        await access.paid_cache_for(MagicMock()).get_or_compute(key, AsyncMock(return_value="text"))
        await access.complete_run(run, MagicMock(), sweep=False)

    assert (ORG, REC, "manifests/rev/parse.json") in store.files
    assert store.spawned == []


async def test_a_run_needs_a_record_in_flight_and_the_cache_on(store: MemoryResultStore, monkeypatch: pytest.MonkeyPatch) -> None:
    assert access.start_run("rev", "index") is None                  # no record bound
    with record_in_flight(ORG, REC):
        assert access.start_run("rev", "index") is not None
        monkeypatch.setenv("PAID_CACHE", "off")
        assert access.start_run("rev", "index") is None


async def test_a_run_started_after_a_refresh_request_keeps_the_refresh(store: MemoryResultStore) -> None:
    with record_in_flight(ORG, REC):
        begin_run(ORG, REC, None, "index", refresh=True)
        run = access.start_run("rev", "index")
        assert run is not None and run.refresh and run.content_rev == "rev"
    with record_in_flight(ORG, "rec-2"):
        run = access.start_run("rev", "index")                       # another record: the refresh does not leak
        assert run is not None and not run.refresh
    assert current_run() is run


async def test_a_manifest_that_cannot_be_written_never_fails_the_record(store: MemoryResultStore) -> None:
    store.failing = {"replace"}
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")
    with record_in_flight(ORG, REC):
        run = access.start_run("rev", "parse")
        cache = access.paid_cache_for(MagicMock())
        assert await cache.get_or_compute(key, AsyncMock(return_value="text")) == "text"   # the open manifest fails: swallowed
        await access.complete_run(run, MagicMock(), sweep=True)                              # so does the final one

    assert store.spawned == []                                                               # no sweep over a record with no manifest


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "false"})
async def test_a_failure_after_parsing_complete_leaves_no_final_manifest_and_queues_no_sweep(
    store: MemoryResultStore,
) -> None:
    """The run ends at INDEXING_COMPLETE, not PARSING_COMPLETE: a record that failed in between is not complete."""
    key = CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", "after-parse")
    ep = _processor(MagicMock())

    async def fail() -> None:
        await _paid(key)
        raise ConnectionError("vector store down")

    _in_process(ep, fail)

    with record_in_flight(ORG, REC), pytest.raises(ConnectionError):
        _ = [e async for e in ep.on_event(_txt_event())]

    rev = content_revision(BODY)
    assert (ORG, REC, f"manifests/{rev}/index.json") not in store.files
    assert (ORG, REC, f"manifests/{rev}/index.open.json") in store.files
    assert store.spawned == []


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_document_that_parses_to_nothing_still_completes_its_run(store: MemoryResultStore) -> None:
    """A scan whose pages were paid for but yielded no blocks ends EMPTY: its manifest is final, not left open."""
    key = CacheKey(ORG, REC, "ocr_page", 1, "m", "p", "q", "i")
    ep = _processor(MagicMock())

    async def parse(**_k: object) -> ParseResult:
        await access.paid_cache_for(MagicMock()).get_or_compute(key, AsyncMock(return_value="page"))
        return ParseResult(block_container=BlocksContainer(blocks=[], block_groups=[]), provider_used=ParserProvider.DEFAULT, metadata={})

    ep.parsing_client.parse = AsyncMock(side_effect=parse)

    with record_in_flight(ORG, REC):
        events = [e async for e in ep.on_event(_event())]

    assert IndexingEvent.INDEXING_COMPLETE in [e.event for e in events]
    rev = content_revision(BODY)
    assert (ORG, REC, f"manifests/{rev}/index.json") in store.files
    assert (ORG, REC, f"manifests/{rev}/index.open.json") not in store.files
    assert len(store.spawned) == 1                                   # the retention sweep is queued for an emptied record too


@patch.dict(os.environ, {"USE_PARSING_SERVICE": "true"})
async def test_a_run_that_fails_lists_every_key_it_noted_without_anyone_flushing(store: MemoryResultStore) -> None:
    """The failure path flushes: keys noted since the last flush are protected too, not only the first one."""
    keys = [CacheKey(ORG, REC, "image_description", 1, "m", "p", "q", f"i{n}") for n in range(4)]
    sink = MagicMock()

    async def index(_ctx: object) -> None:
        cache = access.paid_cache_for(MagicMock())
        for key in keys:
            await cache.get_or_compute(key, AsyncMock(return_value="a chart"))
        raise ConnectionError("vector store down")

    sink.index = AsyncMock(side_effect=index)
    ep = _processor(sink)

    with record_in_flight(ORG, REC), pytest.raises(ConnectionError):
        async for _ in ep.on_event(_event()):
            pass

    listed = json.loads(store.files[(ORG, REC, f"manifests/{content_revision(BODY)}/index.open.json")][0])["keys"]
    assert sorted(listed) == sorted(k.relative_path() for k in keys)
