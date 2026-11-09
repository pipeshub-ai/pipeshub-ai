"""``Processor.load_checkpoint`` and ``resume_from_checkpoint``: the stored blocks of an interrupted run."""

from __future__ import annotations

import gzip
import json
import zlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.processor import Processor
from app.exceptions.indexing_exceptions import RecordDeletedError
from app.models.blocks import BlocksContainer
from app.modules.parsers.json.json_parser import JSONParser
from app.modules.transformers.blob_storage import (
    BlobStorage,
    StorageDocumentNotFoundError,
    StoredRecordTooLargeError,
)
from app.services.messaging.config import IndexingEvent

REV = "rev-1"


def _processor(blob: object = None, *, error: Exception | None = None, stored_rev: str | None = REV, mapped: bool = True) -> Processor:
    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        processor = Processor(
            logger=MagicMock(), config_service=MagicMock(), indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(), parsers={}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )
    lookup = {"record_doc_id": "doc-1", "storedRev": stored_rev} if mapped else None
    storage = processor.sink_orchestrator.blob_storage
    storage.get_document_id_by_virtual_record_id = AsyncMock(return_value=lookup)
    storage.get_record_from_storage = AsyncMock(return_value=blob, side_effect=error)
    return processor


async def _stored() -> dict:
    container = (await JSONParser().parse(b'[{"a": 1}, {"a": 2}]', "d.json")).block_container
    return {"block_containers": container.model_dump(mode="json")}


@pytest.mark.asyncio
class TestLoadCheckpoint:
    async def test_the_stored_blocks_come_back_equal_to_what_was_stored(self) -> None:
        blob = await _stored()
        loaded = await _processor(blob).load_checkpoint("v", "o", REV)
        assert loaded is not None
        container, semantic = loaded
        assert container.model_dump(mode="json") == blob["block_containers"] and semantic is None

    @pytest.mark.parametrize("blob", [None, {}, {"block_containers": {}}, {"block_containers": {"blocks": [], "block_groups": []}},
                                      {"block_containers": {"blocks": [{"not": "a block"}]}}, {"block_containers": "zz"},
                                      {"block_containers": {"blocks": [1, 2, 3], "block_groups": []}}])
    async def test_nothing_usable_stored_means_parse_again(self, blob: object) -> None:
        assert await _processor(blob).load_checkpoint("v", "o", REV) is None

    @pytest.mark.parametrize(
        "error",
        [
            StorageDocumentNotFoundError("404: the mapping exists, the document is gone"),
            StoredRecordTooLargeError("too large"),
            EOFError("Compressed file ended before the end-of-stream marker"),
            zlib.error("incomplete"),
            gzip.BadGzipFile("crc"),
            json.JSONDecodeError("x", "a", 0),
            TimeoutError("storage"),
            ConnectionError("down"),
            RuntimeError("unknown"),
        ],
        ids=lambda e: type(e).__name__,
    )
    async def test_any_failure_to_load_falls_back_to_the_parse_and_never_raises(self, error: Exception) -> None:
        # Kills: re-raising what looks transient. A 404 or a corrupt document repeats on every retry, so the
        # record would fail where the parse would have finished it.
        processor = _processor(error=error)
        assert await processor.load_checkpoint("v", "o", REV) is None
        (message, *args), _ = processor.logger.warning.call_args
        assert type(error).__name__ in args

    async def test_a_failing_lookup_falls_back_too(self) -> None:
        processor = _processor()
        processor.sink_orchestrator.blob_storage.get_document_id_by_virtual_record_id = AsyncMock(side_effect=RuntimeError("graph"))
        assert await processor.load_checkpoint("v", "o", REV) is None

    @pytest.mark.parametrize("stored_rev", ["rev-2", None, ""])
    async def test_a_blob_of_another_revision_or_of_none_is_not_used(self, stored_rev: str | None) -> None:
        # Kills: embedding another run's blocks under this run's revision.
        processor = _processor(await _stored(), stored_rev=stored_rev)
        assert await processor.load_checkpoint("v", "o", REV) is None
        processor.sink_orchestrator.blob_storage.get_record_from_storage.assert_not_awaited()

    async def test_no_mapping_means_nothing_stored(self) -> None:
        assert await _processor(await _stored(), mapped=False).load_checkpoint("v", "o", REV) is None

    async def test_the_storage_is_asked_for_the_marker_vrid_with_the_lookup_that_proved_the_revision(self) -> None:
        processor = _processor(await _stored())
        await processor.load_checkpoint("v-1", "org-1", REV)
        storage = processor.sink_orchestrator.blob_storage
        storage.get_document_id_by_virtual_record_id.assert_awaited_once_with("v-1")
        storage.get_record_from_storage.assert_awaited_once_with(
            "v-1", "org-1", lookup_result={"record_doc_id": "doc-1", "storedRev": REV}, relookup=False
        )

    async def test_a_document_gone_with_the_mapping_moved_on_is_not_read_from_the_new_document(self) -> None:
        # Kills: the reader re-resolving the mapping after a 404 and returning another revision's blocks.
        blob_storage = BlobStorage(logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock())
        blob_storage._get_auth_and_config = AsyncMock(return_value=({}, "http://node", "azureBlob"))
        old = {"record_doc_id": "doc-old", "storedRev": REV}
        newer = {"record_doc_id": "doc-new", "storedRev": "rev-2"}
        blob_storage.get_document_id_by_virtual_record_id = AsyncMock(side_effect=[old, newer])
        read = AsyncMock(side_effect=[StorageDocumentNotFoundError("gone"), await _stored()])
        blob_storage._read_stored_document = read
        processor = _processor()
        processor.sink_orchestrator.blob_storage = blob_storage

        assert await processor.load_checkpoint("v", "o", REV) is None
        assert read.await_count == 1
        assert blob_storage.get_document_id_by_virtual_record_id.await_count == 1


@pytest.mark.asyncio
class TestResumeFromCheckpoint:
    async def test_the_pipeline_runs_once_on_the_loaded_blocks_between_the_two_completion_events(self) -> None:
        processor = _processor()
        container = BlocksContainer()
        processor.graph_provider.get_document.return_value = {"_key": "rec-1", "recordType": "FILE"}
        record = MagicMock()
        processor._convert_record = MagicMock(return_value=record)
        pipeline = MagicMock(apply=AsyncMock())
        processor._pipeline = MagicMock(return_value=pipeline)
        processor._create_transform_context = MagicMock(return_value="ctx")

        events = [e.event async for e in processor.resume_from_checkpoint("rec-1", (container, None), "v", "newRecord", None)]

        assert events == [IndexingEvent.PARSING_COMPLETE, IndexingEvent.INDEXING_COMPLETE]
        assert record.block_containers is container and record.virtual_record_id == "v"
        processor._create_transform_context.assert_called_once_with(record, "newRecord", None)
        pipeline.apply.assert_awaited_once_with("ctx")

    async def test_a_record_deleted_meanwhile_ends_the_attempt_as_a_deletion(self) -> None:
        processor = _processor()
        processor.graph_provider.get_document.return_value = None
        with pytest.raises(RecordDeletedError):
            async for _ in processor.resume_from_checkpoint("rec-1", (BlocksContainer(), None), "v"):
                pass
