"""A reindex of a whole connector asks the installed record view decorator for records its own listing does not reach, and counts them."""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import ProgressStatus
from app.connectors.record_views import DefaultRecordViewDecorator, set_decorator
from app.connectors.services.event_service import EventService
from tests.unit.connectors.services.test_event_service_requeue import _Connector


class Extra(DefaultRecordViewDecorator):
    def __init__(self, queued: int = 2) -> None:
        self.queued, self.calls = queued, []

    async def reindex_connector_extra(
        self, connector: object, graph: object, *, org_id: str, connector_id: str, statuses: list[str], user_key: str | None
    ) -> int:
        self.calls.append({"org_id": org_id, "connector_id": connector_id, "statuses": statuses, "user_key": user_key})
        return self.queued


async def _reindex(payload: dict) -> tuple[EventService, _Connector]:
    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=[])
    svc = EventService(MagicMock(spec=logging.Logger), MagicMock(), graph)
    connector = _Connector()
    with patch.object(svc, "_ensure_connector", new_callable=AsyncMock, return_value=connector), \
         patch("app.connectors.services.event_service.Connectors") as known:
        known.DRIVE = MagicMock()
        await svc._reindex_batches(
            connector, "DRIVE", "c1", "org1", None, None, 0, "u1", payload.get("statuses"), None,
        )
    return svc, connector


async def test_a_whole_connector_reindex_asks_for_the_extra_records_with_the_statuses_it_was_given() -> None:
    extra = Extra()
    set_decorator(extra)

    await _reindex({"statuses": [ProgressStatus.FAILED.value]})

    assert extra.calls == [
        {"org_id": "org1", "connector_id": "c1", "statuses": [ProgressStatus.FAILED.value], "user_key": "u1"}
    ]


async def test_without_statuses_it_asks_for_the_failed_ones() -> None:
    extra = Extra()
    set_decorator(extra)

    await _reindex({})

    assert extra.calls[0]["statuses"] == [ProgressStatus.FAILED.value]


async def test_a_reindex_of_one_record_or_one_group_never_asks() -> None:
    extra = Extra()
    set_decorator(extra)
    graph = AsyncMock()
    graph.get_records_by_parent_record = AsyncMock(return_value=[])
    graph.get_records_by_record_group = AsyncMock(return_value=[])
    svc = EventService(MagicMock(spec=logging.Logger), MagicMock(), graph)
    connector = _Connector()

    await svc._reindex_batches(connector, "DRIVE", "c1", "org1", "rec-1", None, 0, "u1", None, None)
    await svc._reindex_batches(connector, "DRIVE", "c1", "org1", None, "grp-1", 0, "u1", None, None)

    assert extra.calls == []


async def test_the_extra_records_are_counted_in_the_total_and_the_default_adds_none() -> None:
    set_decorator(Extra(queued=2))
    svc, _connector = await _reindex({})
    assert "Total records processed: 2" in svc.logger.info.call_args.args[0]

    record_views_default()
    svc, _connector = await _reindex({})
    assert "Total records processed: 0" in svc.logger.info.call_args.args[0]


def record_views_default() -> None:
    set_decorator(DefaultRecordViewDecorator())
