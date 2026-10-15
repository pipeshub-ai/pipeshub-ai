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
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])

    await _drain(ep, _empty_update())

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_awaited_once()
    assert processor.indexing_pipeline.delete_points_for_virtual_record.await_args.args[0] == "vr-1"
    assert _status_writes(ep)[-1] == {
        "indexingStatus": "EMPTY", "hasIndexedCopy": False, "processingStartedAt": None,
    }


@pytest.mark.asyncio
async def test_vectors_another_record_still_holds_are_kept() -> None:
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(
        return_value=[{"_key": "rec-1"}, {"_key": "rec-2"}]
    )

    await _drain(ep, _empty_update())

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert _status_writes(ep)[-1]["hasIndexedCopy"] is False


@pytest.mark.asyncio
async def test_a_new_empty_record_has_no_old_vectors_to_drop() -> None:
    processor = _processor()
    ep = _make_event_processor(processor=processor)
    ep.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[{"_key": "rec-1"}])

    await _drain(ep, _empty_update(EventTypes.NEW_RECORD.value))

    processor.indexing_pipeline.delete_points_for_virtual_record.assert_not_awaited()
    assert _status_writes(ep)[-1]["indexingStatus"] == "EMPTY"
