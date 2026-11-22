"""A flip of the weight precision is never taken for a failed entity sync: it goes up to whoever hands the event back.

The real sink orchestrator runs over the real entity store's batch method; only the batch's own write is made to
meet an index of the other precision.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import EntityType
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.services.messaging.precision_flip import PrecisionFlip
from tests.unit.modules.transformers.test_entity_vectorstore_precision import (
    _Bundled,
    _store,
)

_DOC = {"_key": "rec-dup-1", "orgId": "org-9", "connectorId": "conn-b", "recordGroupId": "rg-2", "recordName": "Q3 Plan.pdf"}


def _orchestrator(upsert_error: Exception) -> SinkOrchestrator:
    store = _store(_Bundled("int8-w"))
    store.logger = MagicMock()
    store._ready_collections = {"c"}
    store._ensure_initialized = AsyncMock()
    store._coalesce_by_key = lambda entities: list(entities)
    store._collection_name_for = lambda org: "c"
    store._key_of = lambda e: "k"
    store._report = MagicMock()
    store._upsert_batch = AsyncMock(side_effect=upsert_error)
    graph = AsyncMock()
    graph.get_taxonomy_entities_for_record = AsyncMock(return_value=[{"entityId": "t1", "entityType": "category", "name": "Finance"}])
    graph.get_record_group_by_id = AsyncMock(return_value=None)
    return SinkOrchestrator(
        graphdb=AsyncMock(), blob_storage=AsyncMock(), vector_store=AsyncMock(), graph_provider=graph,
        logger=MagicMock(), config_service=MagicMock(), entity_vector_store=store,
    )


@pytest.mark.asyncio
async def test_a_flip_in_the_entity_batch_is_not_swallowed_by_the_duplicate_sync() -> None:
    orch = _orchestrator(PrecisionFlip("the index holds fp32"))
    with pytest.raises(PrecisionFlip):
        await orch.sync_entities_for_duplicate(_DOC)


@pytest.mark.asyncio
async def test_any_other_error_still_leaves_the_duplicate_sync_non_fatal() -> None:
    orch = _orchestrator(RuntimeError("store down"))
    assert await orch.sync_entities_for_duplicate(_DOC) is False
    assert EntityType.CATEGORY.value == "category"


@pytest.mark.asyncio
async def test_the_dedup_decision_hands_a_flip_back_instead_of_logging_it() -> None:
    from tests.unit.events.test_events import _make_multi_collection_event_processor

    ep, graph = _make_multi_collection_event_processor()
    ep.sink_orchestrator = AsyncMock()
    ep.sink_orchestrator.sync_entities_for_duplicate = AsyncMock(side_effect=PrecisionFlip("the index holds fp32"))
    graph.find_duplicate_records.return_value = [{
        "_key": "dup-1", "connectorName": "GOOGLE_DRIVE", "virtualRecordId": "vr-1",
        "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
    }]
    doc = {"_key": "r1", "connectorName": "GOOGLE_DRIVE", "md5Checksum": "abc", "recordType": "FILE", "sizeInBytes": 10}
    with pytest.raises(PrecisionFlip):
        await ep._check_duplicate_by_md5(b"x", doc)


@pytest.mark.asyncio
async def test_the_entities_stage_does_not_swallow_a_flip_while_copying_to_promoted_copies() -> None:
    from tests.unit.modules.pipeline.test_classify_io_writes import (
        _Blob,
        _entities_io,
        _job,
    )

    io = _entities_io(_Blob({}))
    io._graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1", "rec-2"])
    io._graph.copy_named_entity_mentions = AsyncMock()
    io._document = AsyncMock(return_value={"orgId": "org-1"})
    io._sink.sync_entities_for_duplicate = AsyncMock(side_effect=PrecisionFlip("the index holds fp32"))
    with pytest.raises(PrecisionFlip):
        await io._copy_to_promoted_copies(_job("entities"), "rec-1", {"rec-1"})

    io._sink.sync_entities_for_duplicate = AsyncMock(side_effect=RuntimeError("boom"))
    await io._copy_to_promoted_copies(_job("entities"), "rec-1", {"rec-1"})  # best effort, as before
