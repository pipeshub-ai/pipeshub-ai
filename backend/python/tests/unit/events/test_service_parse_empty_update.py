"""The parsing-service path drops an emptied record's old content too, and fails loudly.

When the parsing service returns no blocks for an update, the record is marked
EMPTY and leaves search. Its previous vectors must go with it (unless another
record shares them), and an EMPTY write that didn't persist must not let the
event complete with ``hasIndexedCopy`` still set.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError
from tests.unit.events.test_events_blob_storage_ordering import (
    _collect_events,
    _make_event_processor,
)


def _empty_parse(ep) -> None:
    container = MagicMock()
    container.blocks = []
    container.block_groups = []
    ep.parsing_client.parse = AsyncMock(
        return_value=MagicMock(block_container=container, provider_used=MagicMock(value="test")),
    )


def _with_pipeline(ep) -> MagicMock:
    pipeline = MagicMock()
    pipeline.delete_points_for_virtual_record = AsyncMock()
    ep.processor.indexing_pipeline = pipeline
    ep.sink_orchestrator.blob_storage.save_reconciliation_metadata = AsyncMock()
    ep.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="path/vrid-1")
    return pipeline


@pytest.mark.asyncio
async def test_an_empty_parse_of_an_update_drops_the_old_vectors() -> None:
    ep = _make_event_processor()
    _empty_parse(ep)
    pipeline = _with_pipeline(ep)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])

    await _collect_events(ep, event_type=EventTypes.UPDATE_RECORD.value)

    pipeline.delete_points_for_virtual_record.assert_awaited_once()
    assert pipeline.delete_points_for_virtual_record.await_args.args[0] == "vrid-1"
    ep.sink_orchestrator.blob_storage.save_reconciliation_metadata.assert_awaited_once()
    written = [c.args[2] for c in ep.graph_provider.update_node.await_args_list if "indexingStatus" in c.args[2]]
    assert written[-1]["indexingStatus"] == "EMPTY" and written[-1]["hasIndexedCopy"] is False


@pytest.mark.asyncio
async def test_a_failed_empty_write_does_not_complete_the_event() -> None:
    ep = _make_event_processor()
    _empty_parse(ep)
    _with_pipeline(ep)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])

    async def update_node(_key, _collection, fields) -> bool:
        return fields.get("indexingStatus") != "EMPTY"

    ep.graph_provider.update_node = AsyncMock(side_effect=update_node)

    with pytest.raises(IndexingError):
        await _collect_events(ep, event_type=EventTypes.UPDATE_RECORD.value)


@pytest.mark.asyncio
async def test_no_pipeline_to_delete_with_fails_the_event() -> None:
    ep = _make_event_processor()
    _empty_parse(ep)
    ep.processor.indexing_pipeline = None
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])

    with pytest.raises(IndexingError):
        await _collect_events(ep, event_type=EventTypes.UPDATE_RECORD.value)
