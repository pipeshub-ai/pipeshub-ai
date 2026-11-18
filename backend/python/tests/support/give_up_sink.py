"""A real ``RecordEventHandler`` as the abandoned-message sink of a consumer test, over a COMPLETED record.

Shared from here so each broker's consumer test can prove the same thing: a same-bytes give-up makes the handler
queue exactly one membership repair, and any other give-up of the same message makes it queue none.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from tests.support.record_handler import make_record_handler

if TYPE_CHECKING:
    from app.services.messaging.kafka.handlers.record import RecordEventHandler

__all__ = ["completed_record_sink", "update_message_value", "repairs"]

RECORD = {
    "_key": "r1", "orgId": "org-1", "virtualRecordId": "vr1", "connectorId": "conn-1", "contentRev": "rev-1",
    "indexingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
}


def completed_record_sink() -> RecordEventHandler:
    handler = make_record_handler()
    graph = handler.event_processor.graph_provider
    graph.get_document = AsyncMock(return_value=dict(RECORD))
    graph.update_node_fields_if_match = AsyncMock(return_value=True)
    handler.producer = MagicMock()
    handler.producer.send_event = AsyncMock(return_value=True)
    return handler


def update_message_value() -> bytes:
    return json.dumps({"eventType": "updateRecord", "payload": {"recordId": "r1", "orgId": "org-1"}}).encode("utf-8")


def repairs(handler: RecordEventHandler) -> list[dict[str, Any]]:
    """The syncVectorMembership events the handler queued."""
    return [
        c.kwargs for c in handler.producer.send_event.await_args_list
        if c.kwargs.get("event_type") == "syncVectorMembership"
    ]
