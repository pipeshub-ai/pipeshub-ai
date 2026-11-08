"""``Processor.load_checkpoint`` and ``resume_from_checkpoint``: the stored blocks of an interrupted run."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.events.processor import Processor
from app.exceptions.indexing_exceptions import RecordDeletedError
from app.models.blocks import BlocksContainer
from app.modules.parsers.json.json_parser import JSONParser
from app.services.messaging.config import IndexingEvent


def _processor(blob: object = None, *, error: Exception | None = None) -> Processor:
    with patch("app.events.processor.DoclingClient"), patch("app.events.processor.DoclingProcessor"):
        processor = Processor(
            logger=MagicMock(), config_service=MagicMock(), indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(), parsers={}, document_extractor=MagicMock(), sink_orchestrator=MagicMock(),
        )
    processor.sink_orchestrator.blob_storage.get_record_from_storage = AsyncMock(return_value=blob, side_effect=error)
    return processor


async def _stored() -> dict:
    container = (await JSONParser().parse(b'[{"a": 1}, {"a": 2}]', "d.json")).block_container
    return {"block_containers": container.model_dump(mode="json")}


@pytest.mark.asyncio
class TestLoadCheckpoint:
    async def test_the_stored_blocks_come_back_equal_to_what_was_stored(self) -> None:
        blob = await _stored()
        loaded = await _processor(blob).load_checkpoint("v", "o")
        assert loaded is not None
        container, semantic = loaded
        assert container.model_dump(mode="json") == blob["block_containers"] and semantic is None

    @pytest.mark.parametrize("blob", [None, {}, {"block_containers": {}}, {"block_containers": {"blocks": [], "block_groups": []}},
                                      {"block_containers": {"blocks": [{"not": "a block"}]}}])
    async def test_nothing_usable_stored_means_parse_again(self, blob: object) -> None:
        assert await _processor(blob).load_checkpoint("v", "o") is None

    async def test_a_storage_error_a_retry_could_clear_is_raised_not_read_as_nothing_stored(self) -> None:
        # Kills: a timeout that throws an attempt's progress away.
        with pytest.raises(TimeoutError):
            await _processor(error=TimeoutError("storage")).load_checkpoint("v", "o")

    async def test_the_storage_is_asked_for_the_marker_vrid_and_the_events_org(self) -> None:
        processor = _processor(await _stored())
        await processor.load_checkpoint("v-1", "org-1")
        processor.sink_orchestrator.blob_storage.get_record_from_storage.assert_awaited_once_with("v-1", "org-1")


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
