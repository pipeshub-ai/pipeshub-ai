"""Deleted shelves, books and chapters leave the graph with everything below them.

BookStack logs no page_delete for the pages of a deleted chapter or book, so
only the group delete can take those pages away. A deleted item can come back
from the recycle bin under the same id, so the delete is applied only once
BookStack confirms the item is gone, and an unconfirmed delete holds the cursor.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector
from app.sources.client.bookstack.bookstack import BookStackResponse

CONNECTOR_ID = "bs-conn-1"


def _listing(*items: dict) -> BookStackResponse:
    return BookStackResponse(success=True, data={"data": list(items), "total": len(items)})


@pytest.fixture
def connector() -> BookStackConnector:
    processor = MagicMock()
    processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock(id="rg-1"))
    processor.on_record_group_deleted = AsyncMock(return_value=True)
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        conn = BookStackConnector(
            logger=logging.getLogger("bookstack-group-delete"),
            data_entities_processor=processor,
            data_store_provider=AsyncMock(),
            config_service=AsyncMock(),
            connector_id=CONNECTOR_ID,
            scope="team",
            created_by="admin",
        )
    conn.data_source = MagicMock()
    conn.data_source.list_chapters = AsyncMock(return_value=_listing())
    conn.data_source.list_books = AsyncMock(return_value=_listing())
    conn.data_source.list_shelves = AsyncMock(return_value=_listing())
    return conn


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type", ["chapter", "book", "bookshelf"])
async def test_a_confirmed_delete_removes_the_group_and_its_subtree(connector, content_type) -> None:
    done = await connector._handle_record_group_delete_event({"loggable_id": 7}, content_type)

    assert done is True
    connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with(
        f"{content_type}/7", CONNECTOR_ID
    )


@pytest.mark.asyncio
async def test_a_chapter_restored_before_the_sync_is_kept(connector) -> None:
    connector.data_source.list_chapters = AsyncMock(return_value=_listing({"id": 7}))

    assert await connector._handle_record_group_delete_event({"loggable_id": 7}, "chapter") is True
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_delete_bookstack_cannot_confirm_is_kept_and_retried(connector) -> None:
    connector.data_source.list_chapters = AsyncMock(
        return_value=BookStackResponse(success=True, data={"error": {"code": 429}})
    )

    assert await connector._handle_record_group_delete_event({"loggable_id": 7}, "chapter") is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_group_delete_is_retried(connector) -> None:
    connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=False)

    assert await connector._handle_record_group_delete_event({"loggable_id": 7}, "chapter") is False


@pytest.mark.asyncio
async def test_a_group_never_synced_needs_nothing(connector) -> None:
    connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=None)

    assert await connector._handle_record_group_delete_event({"loggable_id": 7}, "chapter") is True
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_purged_delete_without_an_id_does_not_block_the_cursor(connector) -> None:
    assert await connector._handle_record_group_delete_event({"loggable_id": None, "detail": "C2"}, "chapter") is True
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_group_cursor_holds_when_a_delete_is_not_applied(connector) -> None:
    connector.list_roles_with_details = AsyncMock(return_value={})
    connector.record_sync_point = MagicMock()
    connector.record_sync_point.read_sync_point = AsyncMock(return_value={"timestamp": "2026-10-01T00:00:00Z"})
    connector.record_sync_point.update_sync_point = AsyncMock()
    connector._sync_record_groups_incremental = AsyncMock(return_value=False)

    await connector._sync_record_groups()

    connector.record_sync_point.update_sync_point.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_group_cursor_moves_when_every_delete_is_applied(connector) -> None:
    connector.list_roles_with_details = AsyncMock(return_value={})
    connector.record_sync_point = MagicMock()
    connector.record_sync_point.read_sync_point = AsyncMock(return_value={"timestamp": "2026-10-01T00:00:00Z"})
    connector.record_sync_point.update_sync_point = AsyncMock()
    connector._sync_record_groups_incremental = AsyncMock(return_value=True)

    await connector._sync_record_groups()

    connector.record_sync_point.update_sync_point.assert_awaited_once()
