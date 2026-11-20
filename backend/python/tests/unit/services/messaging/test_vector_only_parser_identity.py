"""A vector-only rebuild re-embeds the stored blocks of the record's copy, so the copy's parser is unchanged: its COMPLETED write re-stamps the
identity the record already carries (rather than leaving it stale, which would make the next same-bytes update run in full)."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes, MimeTypes, OriginTypes, ProgressStatus, RecordTypes
from app.modules.pipeline import parser_identity as pi
from tests.support.record_handler import make_record_handler

DOC = {
    "_key": "rec-1", "id": "rec-1", "orgId": "org-1", "recordName": "doc.txt", "recordType": RecordTypes.FILE.value,
    "externalRecordId": "ext-1", "version": 1, "origin": OriginTypes.CONNECTOR.value, "connectorName": "KB", "connectorId": "conn-1",
    "virtualRecordId": "vr-1", "mimeType": MimeTypes.PLAIN_TEXT.value, "indexingStatus": ProgressStatus.NOT_STARTED.value,
    "extractionStatus": ProgressStatus.NOT_STARTED.value, "isFile": True, "lastIndexTimestamp": 500,
}
KNOWN = {
    pi.IDENTITY_FIELD: "inproc:txt@1", pi.IDENTITY_AT_FIELD: 500, pi.IDENTITY_VRID_FIELD: "vr-1", pi.IDENTITY_SOURCE_FIELD: pi.SOURCE_ADOPTED,
}


async def _rebuild(doc: dict) -> MagicMock:
    handler = make_record_handler()
    handler.event_processor.graph_provider.get_document = AsyncMock(return_value=doc)
    handler.event_processor.processor.indexing_pipeline.delete_points_for_virtual_record = AsyncMock()
    sink = MagicMock()
    sink.blob_storage.get_record_from_storage = AsyncMock(return_value={
        "block_containers": {"blocks": [{"index": 0, "type": "text", "data": "hello"}], "block_groups": []},
    })
    sink.index = AsyncMock()
    sink.reproject_named_entities = AsyncMock()
    handler.event_processor.sink_orchestrator = sink
    payload = {
        "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": "vr-1", "mimeType": MimeTypes.PLAIN_TEXT.value,
        "forceReindex": True, "vectorDbOnly": True,
    }
    async for _ in handler.process_event(EventTypes.REINDEX_RECORD.value, payload):
        pass
    return sink


@pytest.mark.asyncio
async def test_the_rebuild_restamps_the_known_identity_with_its_source() -> None:
    sink = await _rebuild({**DOC, **KNOWN})

    ctx = sink.index.await_args.args[0]
    assert ctx.parser_identity == "inproc:txt@1"
    assert ctx.parser_identity_source == pi.SOURCE_ADOPTED


@pytest.mark.asyncio
@pytest.mark.parametrize("extra", [{}, {**KNOWN, "lastIndexTimestamp": 501}], ids=["none", "stale"])
async def test_a_record_with_no_known_identity_restamps_nothing(extra: dict) -> None:
    sink = await _rebuild({**DOC, **extra})

    assert sink.index.await_args.args[0].parser_identity is None
