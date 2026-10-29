"""The run's cursor (the last audit event applied) and when a run may report success (``run_sync``)."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import (
    _ACCESS_MODEL,
    _CURSOR_KEY,
    BookStackConnector,
    ReadOutcome,
)
from app.connectors.sources.bookstack.sync_health import BookStackSyncIncompleteError
from app.models.entities import AppUser
from app.sources.client.bookstack.bookstack import BookStackResponse


class Points:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict) -> None:
        self.points.setdefault(key, {}).update(data)


def listing(rows: list[dict], total: int | None = None, status: int = 200) -> BookStackResponse:
    return BookStackResponse(success=True, data={"data": rows, "total": len(rows) if total is None else total},
                             status_code=status)


def refused(status: int, message: str = "nope") -> BookStackResponse:
    return BookStackResponse(success=False, data={"error": {"message": message, "code": status}},
                             status_code=status, error=message)


@pytest.fixture
def connector() -> BookStackConnector:
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"), \
         patch("app.connectors.sources.bookstack.connector.SyncPoint", side_effect=lambda **_: Points()):
        c = BookStackConnector(logging.getLogger("bs-run"), MagicMock(org_id="org"), MagicMock(), MagicMock(),
                               "bs-1", "team", "admin")
    c.data_source = MagicMock()
    c.notify = AsyncMock()
    c.get_all_users = AsyncMock(return_value=[AppUser(app_name="BOOKSTACK", connector_id="bs-1", source_user_id="1",
                                                      email="a@example.com", full_name="A")])
    c._user_list_read = ReadOutcome.COMPLETE
    c._sync_everything = AsyncMock()
    c._apply_events = AsyncMock()
    return c


def events(*ids: int) -> list[dict]:
    return [{"id": i, "type": "page_update", "loggable_type": "page", "loggable_id": i} for i in ids]


async def run(c: BookStackConnector) -> None:
    with patch("app.connectors.sources.bookstack.connector.load_connector_filters",
               AsyncMock(return_value=(MagicMock(), MagicMock()))):
        await c.run_sync()


@pytest.mark.asyncio
async def test_a_first_run_reads_everything_and_saves_the_newest_event(connector) -> None:
    connector.data_source.list_audit_log = AsyncMock(return_value=listing(events(41), total=41))
    await run(connector)
    connector._sync_everything.assert_awaited_once()
    assert connector.record_sync_point.points[_CURSOR_KEY]["lastEventId"] == 41


@pytest.mark.asyncio
async def test_a_cursor_from_the_old_code_reads_everything_again(connector) -> None:
    """The old code left timestamps and no access model: its access must be worked out again."""
    connector.record_sync_point.points[_CURSOR_KEY] = {"lastEventId": 5}
    connector.data_source.list_audit_log = AsyncMock(return_value=listing(events(41), total=41))
    await run(connector)
    connector._sync_everything.assert_awaited_once()
    connector._apply_events.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_later_run_applies_only_the_events_after_the_cursor(connector) -> None:
    connector.record_sync_point.points[_CURSOR_KEY] = {"lastEventId": 40, "accessModel": _ACCESS_MODEL}
    connector.data_source.list_audit_log = AsyncMock(side_effect=[listing(events(43), 43),
                                                                  listing(events(41, 42, 43))])
    await run(connector)
    connector._apply_events.assert_awaited_once()
    assert [e["id"] for e in connector._apply_events.await_args.args[0]] == [41, 42, 43]
    assert connector.record_sync_point.points[_CURSOR_KEY]["lastEventId"] == 43


@pytest.mark.asyncio
async def test_a_failed_run_keeps_the_cursor_and_does_not_report_success(connector) -> None:
    connector.record_sync_point.points[_CURSOR_KEY] = {"lastEventId": 40, "accessModel": _ACCESS_MODEL}
    connector.data_source.list_audit_log = AsyncMock(side_effect=[listing(events(43), 43),
                                                                  listing(events(41, 42, 43))])

    async def apply(events: list[dict], users: list[AppUser]) -> None:
        connector._health.fail("page 42")
    connector._apply_events = AsyncMock(side_effect=apply)
    with pytest.raises(BookStackSyncIncompleteError):
        await run(connector)
    assert connector.record_sync_point.points[_CURSOR_KEY]["lastEventId"] == 40


@pytest.mark.asyncio
async def test_an_unreadable_audit_log_fails_the_run_and_moves_nothing(connector) -> None:
    connector.record_sync_point.points[_CURSOR_KEY] = {"lastEventId": 40, "accessModel": _ACCESS_MODEL}
    connector.data_source.list_audit_log = AsyncMock(side_effect=[listing(events(43), 43), refused(502)])
    with pytest.raises(BookStackSyncIncompleteError):
        await run(connector)
    connector._apply_events.assert_not_awaited()
    assert connector.record_sync_point.points[_CURSOR_KEY]["lastEventId"] == 40
    connector.notify.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_token_stops_the_run_fails_it_and_tells_the_admin(connector) -> None:
    connector.data_source.list_audit_log = AsyncMock(return_value=refused(401, "No matching API token was found"))
    with pytest.raises(BookStackSyncIncompleteError):
        await run(connector)
    connector._sync_everything.assert_not_awaited()
    connector.notify.assert_awaited_once()
    assert _CURSOR_KEY not in connector.record_sync_point.points
