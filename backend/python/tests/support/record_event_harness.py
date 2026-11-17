"""A record event handler over a fake graph, with the event processor's runs recorded."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import CollectionNames, EventTypes
from app.modules.indexing.parse_now_marker import current_run, store_only_active
from app.services.messaging.config import (
    IndexingEvent,
    PipelineEvent,
    PipelineEventData,
)
from app.services.messaging.kafka.handlers import record as record_module
from app.services.messaging.kafka.handlers.record import RecordEventHandler

RECORDS = CollectionNames.RECORDS.value
APPS = CollectionNames.APPS.value
REV = "rev-1"
PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def make_record(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    record = {
        "_key": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "origin": "CONNECTOR", "recordName": "deck.pptx",
        "mimeType": PPTX, "extension": "pptx", "indexingStatus": "AUTO_INDEX_OFF", "isFile": True,
        "virtualRecordId": "vr-1", "contentRev": REV, "version": 3,
    }
    record.update(overrides)
    return record


def make_payload(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    payload = {
        "recordId": "rec-1", "orgId": "org-1", "connectorId": "conn-1", "connectorName": "SLACK", "mimeType": PPTX,
        "virtualRecordId": "vr-1", "parseNow": True, "requestedByUserId": "user-1", "contentRev": REV,
    }
    payload.update(overrides)
    return payload


class Harness:
    """A handler over a fake graph, with the event processor's run recorded."""

    def __init__(self, record: dict[str, Any] | None, *, connector: dict[str, Any] | None = None, access: bool = True) -> None:
        self.record = record
        self.connector = {"_key": "conn-1", "isActive": True, "name": "Slack"} if connector is None else connector
        self.runs: list[dict[str, Any]] = []
        self.fail_with: Exception | None = None
        event_processor = MagicMock()
        graph = AsyncMock()
        graph.get_document = AsyncMock(side_effect=self._get)
        graph.update_node = AsyncMock(return_value=True)
        graph.check_record_access_with_details = AsyncMock(return_value={"record": {}} if access else None)
        event_processor.graph_provider = graph
        event_processor.stage_ingress = None
        event_processor.on_event = self._on_event
        pipeline = AsyncMock()
        pipeline.bulk_delete_embeddings = AsyncMock(return_value={"success": True})
        event_processor.processor.indexing_pipeline = pipeline
        config = AsyncMock()
        config.get_config = AsyncMock(return_value={"connectors": {"endpoint": "http://connectors"}})
        self.handler = RecordEventHandler(logger=MagicMock(), config_service=config, event_processor=event_processor, producer=AsyncMock())
        for name in ("_release_queued_copies", "_trigger_next_queued_duplicate", "_propagate_primary_failure_to_queued_duplicates",
                     "_request_code_edge_build_if_repo_drained"):
            setattr(self.handler, name, AsyncMock())
        self.graph = graph
        self.pipeline = pipeline

    async def _get(self, key: str, collection: str, **_: object) -> dict[str, Any] | None:
        if collection == APPS:
            return self.connector
        return None if self.record is None else dict(self.record)

    async def _on_event(self, event_data: dict[str, Any]):  # noqa: ANN202
        run = current_run()
        self.runs.append({"payload": dict(event_data["payload"]), "store_only": store_only_active(), "run": run})
        if self.fail_with is not None:
            raise self.fail_with
        yield PipelineEvent(event=IndexingEvent.PARSING_COMPLETE, data=PipelineEventData(record_id="rec-1"))
        yield PipelineEvent(event=IndexingEvent.INDEXING_COMPLETE, data=PipelineEventData(record_id="rec-1"))

    async def run(
        self, payload: dict[str, Any] | None = None, event_type: str = EventTypes.REINDEX_RECORD.value,
        *, download_error: Exception | None = None,
    ) -> list[str]:
        download = AsyncMock(return_value={"data": b"hello"}, side_effect=download_error)
        with (
            patch.object(record_module, "generate_jwt", AsyncMock(return_value="token")),
            patch.object(record_module, "make_api_call", download),
        ):
            self.download = download
            return [e.event async for e in self.handler.process_event(event_type, payload or make_payload())]

    def writes(self) -> list[dict[str, Any]]:
        return [call.args[2] for call in self.graph.update_node.await_args_list]
