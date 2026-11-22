"""Shelves are not synced: only books, chapters and pages.

A shelf holds no content and gives no access to its books, so the connector neither lists shelves nor writes
them, ignores the audit events that only change one, and removes the shelves older versions stored, once a
complete listing of books and chapters says what does belong.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector
from app.models.entities import RecordGroupType
from app.sources.client.bookstack.bookstack import BookStackResponse

CONNECTOR_ID = "bs-shelves"


def _listing(*items: dict) -> BookStackResponse:
    return BookStackResponse(success=True, data={"data": list(items), "total": len(items)})


@pytest.fixture
def connector() -> BookStackConnector:
    processor = MagicMock(org_id="org")
    processor.on_new_record_groups = AsyncMock()
    processor.on_record_group_deleted = AsyncMock(return_value=True)
    processor.get_record_group_by_external_id = AsyncMock(return_value=None)
    processor.get_nodes_by_filters = AsyncMock(return_value=[
        {"externalGroupId": "bookshelf/3"}, {"externalGroupId": "book/1"}, {"externalGroupId": "book/9"},
        {"externalGroupId": "chapter/2"},
    ])
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        conn = BookStackConnector(logging.getLogger("bookstack-shelves"), processor, AsyncMock(), AsyncMock(),
                                  CONNECTOR_ID, "team", "admin")
    conn.data_source = MagicMock()
    conn.data_source.list_books = AsyncMock(return_value=_listing({"id": 1, "name": "Handbook"}))
    conn.data_source.list_chapters = AsyncMock(return_value=_listing({"id": 2, "name": "Policies", "book_id": 1}))
    conn.data_source.list_shelves = AsyncMock(return_value=_listing({"id": 3, "name": "Company"}))
    conn.data_source.get_shelf = AsyncMock()
    conn.data_source.get_content_permissions = AsyncMock()
    conn._access = AsyncMock(return_value=MagicMock(permissions=[]))
    conn._load_roles_for_access = AsyncMock(return_value=True)
    conn._get_book_id_filter = MagicMock(return_value=([], None))
    return conn


def _deleted(connector: BookStackConnector) -> list[str]:
    return [c.args[0] for c in connector.data_entities_processor.on_record_group_deleted.await_args_list]


@pytest.mark.asyncio
async def test_a_full_read_writes_books_and_chapters_and_no_shelf(connector) -> None:
    await connector._sync_groups_full()

    connector.data_source.list_shelves.assert_not_awaited()
    written = [g for c in connector.data_entities_processor.on_new_record_groups.await_args_list for g, _ in c.args[0]]
    assert sorted(g.external_group_id for g in written) == ["book/1", "chapter/2"]
    assert RecordGroupType.SHELF not in {g.group_type for g in written}


@pytest.mark.asyncio
async def test_a_complete_listing_removes_a_shelf_an_older_version_stored(connector) -> None:
    await connector._sync_groups_full()

    assert sorted(_deleted(connector)) == ["book/9", "bookshelf/3"]
    assert connector._health.ok


@pytest.mark.asyncio
async def test_an_incomplete_listing_removes_no_shelf(connector) -> None:
    connector.data_source.list_chapters = AsyncMock(return_value=BookStackResponse(
        success=False, data={"error": {"message": "An error occurred", "code": 500}}, status_code=500))

    await connector._sync_groups_full()

    assert _deleted(connector) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    {"id": 1, "type": "bookshelf_create", "loggable_type": "bookshelf", "loggable_id": 3},
    {"id": 2, "type": "bookshelf_update", "loggable_type": "bookshelf", "loggable_id": 3},
    {"id": 3, "type": "permissions_update", "loggable_type": "bookshelf", "loggable_id": 3},
    {"id": 4, "type": "bookshelf_delete", "loggable_type": "bookshelf", "loggable_id": 3},
    {"id": 5, "type": "bookshelf_delete", "loggable_type": None, "loggable_id": None, "detail": "Company"},
    {"id": 6, "type": "recycle_bin_restore", "detail": "Deletion (8) for bookshelf (3)"},
], ids=["create", "update", "permissions", "delete", "purged-delete", "restore"])
async def test_an_event_that_only_changes_a_shelf_reads_and_writes_nothing(connector, event) -> None:
    await connector._apply_events([event], [])

    connector._load_roles_for_access.assert_not_awaited()
    for method in ("list_shelves", "get_shelf", "list_books", "list_chapters", "get_content_permissions"):
        getattr(connector.data_source, method).assert_not_awaited()
    connector.data_entities_processor.on_new_record_groups.assert_not_awaited()
    assert _deleted(connector) == []
    assert connector._health.ok


@pytest.mark.asyncio
async def test_turning_a_book_into_a_shelf_still_reads_everything(connector) -> None:
    connector._sync_records_full = AsyncMock()
    event = {"id": 7, "type": "bookshelf_create_from_book", "loggable_type": "bookshelf", "loggable_id": 3}

    await connector._apply_events([event], [])

    connector._sync_records_full.assert_awaited_once()
    assert sorted(_deleted(connector)) == ["book/9", "bookshelf/3"]
