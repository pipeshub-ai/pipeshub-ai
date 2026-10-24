"""An update that leaves a record with no content must leave search with its old copy.

The empty-bytes branch of ``EventProcessor.on_event`` marks the record EMPTY through
``mark_record_status``. That write clears ``hasIndexedCopy``, so the record leaves
the search gate, and the previous vectors of a type reconciled block by block, which
nothing else deleted, are dropped unless another record still holds the VRID.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import EventTypes
from app.exceptions.indexing_exceptions import IndexingError
from tests.unit.events.test_orchestrator_flow import (
    _make_event_data,
    _make_event_processor,
)

if TYPE_CHECKING:
    from app.events.events import EventProcessor


def _empty_update(event_type: str = EventTypes.UPDATE_RECORD.value) -> dict[str, Any]:
    event = _make_event_data()
    event["eventType"] = event_type
    event["payload"]["buffer"] = b""
    return event


def _processor() -> MagicMock:
    processor = MagicMock()
    processor.indexing_pipeline = AsyncMock()
    processor.sink_orchestrator.blob_storage.save_reconciliation_metadata = AsyncMock()
    processor.sink_orchestrator.blob_storage.get_actual_content_path = AsyncMock(return_value="path/vr-1")
    return processor


def _status_writes(ep: EventProcessor) -> list[dict]:
    return [
        call.args[2] for call in ep.graph_provider.update_node.await_args_list
        if len(call.args) > 2 and isinstance(call.args[2], dict) and "indexingStatus" in call.args[2]
    ]


async def _drain(ep: EventProcessor, event: dict[str, Any]) -> None:
    async for _ in ep.on_event(event):
        pass


@pytest.mark.asyncio
async def test_an_update_to_empty_content_drops_the_old_vectors_and_the_indexed_copy() -> None:
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])

    await _drain(ep, _empty_update())

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_awaited_once()
    assert processor.indexing_pipeline.delete_points_for_virtual_record.await_args.args[0] == "vr-1"
    assert ep.graph_provider.get_records_by_virtual_record_id.await_args.kwargs["raise_on_error"] is True
    # The VRID is kept, so the stored block list must be emptied with the vectors.
    saved = processor.sink_orchestrator.blob_storage.save_reconciliation_metadata.await_args
    assert saved.args[2] == "vr-1" and not any(saved.args[3].values())
    assert _status_writes(ep)[-1] == {
        "indexingStatus": "EMPTY", "hasIndexedCopy": False, "processingStartedAt": None,
    }


@pytest.mark.asyncio
async def test_vectors_another_record_still_holds_are_kept() -> None:
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1", "rec-2"])

    await _drain(ep, _empty_update())

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert _status_writes(ep)[-1]["hasIndexedCopy"] is False


@pytest.mark.asyncio
async def test_a_new_empty_record_has_no_old_vectors_to_drop() -> None:
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])

    await _drain(ep, _empty_update(EventTypes.NEW_RECORD.value))

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert _status_writes(ep)[-1]["indexingStatus"] == "EMPTY"


@pytest.mark.asyncio
async def test_a_failed_holder_lookup_retries_instead_of_deleting() -> None:
    # An unreadable graph must not pass for "nobody else holds this content".
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(side_effect=TimeoutError("graph"))

    with pytest.raises(IndexingError):
        await _drain(ep, _empty_update())

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert all(w["indexingStatus"] != "EMPTY" for w in _status_writes(ep))


@pytest.mark.asyncio
async def test_no_holder_at_all_deletes_nothing() -> None:
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])

    await _drain(ep, _empty_update())

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_block_list_reset_retries_the_event() -> None:
    processor = _processor()
    processor.sink_orchestrator.blob_storage.save_reconciliation_metadata = AsyncMock(side_effect=OSError("blob"))
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])

    with pytest.raises(IndexingError):
        await _drain(ep, _empty_update())

    assert all(w["indexingStatus"] != "EMPTY" for w in _status_writes(ep))
