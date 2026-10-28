"""An incremental sync reuses the page rows it has just listed (a book's pages, a user's own pages) and looks up by
id only a page no listing returned, so a change to a book of N pages costs no N extra requests."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector
from app.sources.client.bookstack.bookstack import BookStackResponse


def _pages(*rows: dict) -> BookStackResponse:
    return BookStackResponse(success=True, data={"content": json.dumps({"data": list(rows), "total": len(rows)})})


def _row(page_id: int) -> dict:
    return {"id": page_id, "name": f"Page {page_id}", "book_id": 1, "chapter_id": 0}


@pytest.fixture
def connector() -> BookStackConnector:
    processor = MagicMock(org_id="org")
    processor.on_new_records = AsyncMock()
    processor.on_new_record_groups = AsyncMock()
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        conn = BookStackConnector(logging.getLogger("bookstack-rows"), processor, AsyncMock(), AsyncMock(),
                                  "bs-rows", "team", "admin")
    conn.data_source = MagicMock()
    conn.data_source.get_book = AsyncMock(return_value=BookStackResponse(success=True, data={"id": 1, "name": "B"}))
    conn.data_source.list_chapters = AsyncMock(return_value=BookStackResponse(
        success=True, data={"data": [], "total": 0}))
    conn._access = AsyncMock(return_value=MagicMock(permissions=[]))
    conn._load_roles_for_access = AsyncMock(return_value=True)
    conn._get_book_id_filter = MagicMock(return_value=([], None))
    conn._get_date_filters = MagicMock(return_value=(None, None, None, None))
    conn._page_record = AsyncMock(side_effect=lambda page: (MagicMock(), []))
    return conn


def _looked_up(connector: BookStackConnector) -> list[str]:
    return [c.kwargs["filter"]["id"] for c in connector.data_source.list_pages.await_args_list
            if "id" in (c.kwargs.get("filter") or {})]


@pytest.mark.asyncio
async def test_a_book_change_reuses_the_listed_rows_and_looks_up_only_the_rest(connector) -> None:
    def answer(**kwargs: object) -> BookStackResponse:
        wanted = (kwargs.get("filter") or {})
        if wanted.get("book_id") == "1":
            return _pages(_row(10), _row(11), _row(12))
        return _pages(_row(int(wanted["id"])))

    connector.data_source.list_pages = AsyncMock(side_effect=answer)
    events = [{"id": 1, "type": "book_update", "loggable_type": "book", "loggable_id": 1},
              {"id": 2, "type": "page_update", "loggable_type": "page", "loggable_id": 20}]

    await connector._apply_events(events, [])

    assert _looked_up(connector) == ["20"]
    written = sorted(c.args[0]["id"] for c in connector._page_record.await_args_list)
    assert written == [10, 11, 12, 20]
    assert connector._health.ok
