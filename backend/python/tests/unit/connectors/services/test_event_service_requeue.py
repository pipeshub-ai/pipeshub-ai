"""A reindex event that names records hands the connector those still where the re-queue left them."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.sync.task_manager import reindex_task_manager
from app.connectors.services.event_service import MAX_REQUEUE_IDS, EventService
from app.models.entities import FileRecord, RecordType


def _record(key: str, status: str, *, reason: str | None = None, connector: str = "c1", org: str = "org1",
            **flags: bool) -> FileRecord:
    return FileRecord(
        id=key, org_id=org, record_name=f"{key}.docm", record_type=RecordType.FILE, external_record_id=f"x-{key}",
        version=0, origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=connector,
        mime_type="application/octet-stream", is_file=True, extension="docm", indexing_status=status, reason=reason,
        **flags,
    )


class _Connector:
    REQUEUE_PARKED = False

    def __init__(self) -> None:
        self.app = MagicMock()
        self.app.get_app_name.return_value = MagicMock(name="DRIVE")
        self.app.get_app_name.return_value.name = "DRIVE"
        self.requeue_records = AsyncMock()
        self.reindex_records = AsyncMock()


class _ShareConnector(_Connector):
    REQUEUE_PARKED = True


def _service(records: list[FileRecord]) -> EventService:
    graph = AsyncMock()
    graph.get_typed_records_batch = AsyncMock(return_value={r.id: r for r in records})
    return EventService(MagicMock(spec=logging.Logger), MagicMock(), graph)


async def _handle(svc: EventService, connector: _Connector, payload: dict) -> bool:
    with patch.object(svc, "_ensure_connector", new_callable=AsyncMock, return_value=connector), \
         patch("app.connectors.services.event_service.Connectors") as known:
        known.DRIVE = MagicMock()
        handled = await svc._handle_reindex("drive", {"orgId": "org1", "connectorId": "c1", **payload})
        for prefix in ("requeue:c1:", "reindex:c1:"):
            for task in list(reindex_task_manager.tasks_with_prefix(prefix)):
                await task
    return handled


async def test_the_named_records_still_queued_are_handed_to_the_connector() -> None:
    queued = _record("r1", ProgressStatus.QUEUED.value)
    records = [
        queued,
        _record("r2", ProgressStatus.IN_PROGRESS.value),
        _record("r3", ProgressStatus.QUEUED.value, is_placeholder=True),
        _record("r4", ProgressStatus.QUEUED.value, connector="c-other"),
        _record("r5", ProgressStatus.QUEUED.value, is_deleted=True),
    ]
    svc = _service(records)
    connector = _Connector()

    assert await _handle(svc, connector, {"recordIds": ["r1", "r2", "r3", "r4", "r5", "gone"]})

    connector.requeue_records.assert_awaited_once()
    assert [r.id for r in connector.requeue_records.await_args.args[0]] == [queued.id]
    connector.reindex_records.assert_not_awaited()


async def test_a_network_share_takes_only_records_parked_without_a_reason() -> None:
    records = [
        _record("r1", ProgressStatus.AUTO_INDEX_OFF.value),
        _record("r2", ProgressStatus.AUTO_INDEX_OFF.value, reason="The connector was turned off"),
        _record("r3", ProgressStatus.QUEUED.value),
    ]
    svc = _service(records)
    connector = _ShareConnector()

    assert await _handle(svc, connector, {"recordIds": ["r1", "r2", "r3"]})

    assert [r.id for r in connector.requeue_records.await_args.args[0]] == ["r1"]


async def test_nothing_left_where_the_re_queue_left_it_calls_nothing() -> None:
    svc = _service([_record("r1", ProgressStatus.COMPLETED.value)])
    connector = _Connector()

    assert await _handle(svc, connector, {"recordIds": ["r1"]})

    connector.requeue_records.assert_not_awaited()


@pytest.mark.parametrize("payload", [
    {"recordIds": []},
    {"recordIds": "r1"},
    {"recordIds": ["r1", 7]},
    {"recordIds": [""]},
    {"recordIds": [f"r{i}" for i in range(MAX_REQUEUE_IDS + 1)]},
    {"recordIds": ["r1"], "recordId": "r1"},
    {"recordIds": ["r1"], "recordGroupId": "g1"},
])
async def test_a_malformed_list_of_records_is_refused(payload: dict) -> None:
    svc = _service([_record("r1", ProgressStatus.QUEUED.value)])
    connector = _Connector()

    assert await _handle(svc, connector, payload) is False

    connector.requeue_records.assert_not_awaited()
    svc.graph_provider.get_typed_records_batch.assert_not_awaited()


async def test_an_event_without_record_ids_still_reindexes_by_status() -> None:
    svc = _service([])
    svc.graph_provider.get_records_by_status = AsyncMock(return_value=[])
    connector = _Connector()

    assert await _handle(svc, connector, {})

    svc.graph_provider.get_records_by_status.assert_awaited()
    svc.graph_provider.get_typed_records_batch.assert_not_awaited()


async def test_the_default_re_queue_publishes_the_records_as_stored() -> None:
    connector = MagicMock(spec=BaseConnector)
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.reindex_existing_records = AsyncMock(return_value=["r1"])
    records = [_record("r1", ProgressStatus.QUEUED.value)]

    await BaseConnector.requeue_records(connector, records)

    connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)
    assert BaseConnector.REQUEUE_PARKED is False
