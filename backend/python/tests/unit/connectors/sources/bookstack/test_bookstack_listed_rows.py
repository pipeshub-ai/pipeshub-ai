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


def _row(page_id: int, chapter_id: int = 0, owner: int = 1) -> dict:
    return {"id": page_id, "name": f"Page {page_id}", "book_id": 1, "chapter_id": chapter_id, "owned_by": owner,
            "book_slug": "handbook", "slug": f"page-{page_id}", "revision_count": 1,
            "created_at": "2026-10-01T10:00:00.000000Z", "updated_at": "2026-10-02T10:00:00.000000Z"}


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
    conn.bookstack_base_url = "https://wiki.example.com"
    conn.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    return conn


def _looked_up(connector: BookStackConnector) -> list[str]:
    return [c.kwargs["filter"]["id"] for c in connector.data_source.list_pages.await_args_list
            if "id" in (c.kwargs.get("filter") or {})]


@pytest.mark.asyncio
async def test_a_book_change_reuses_the_listed_rows_and_looks_up_only_the_rest(connector) -> None:
    def answer(**kwargs: object) -> BookStackResponse:
        wanted = (kwargs.get("filter") or {})
        if wanted.get("book_id") == "1":
            return _pages(_row(10), _row(11, chapter_id=5, owner=7), _row(12, owner=8))
        return _pages(_row(int(wanted["id"])))

    connector.data_source.list_pages = AsyncMock(side_effect=answer)
    events = [{"id": 1, "type": "book_update", "loggable_type": "book", "loggable_id": 1},
              {"id": 2, "type": "page_update", "loggable_type": "page", "loggable_id": 20}]

    await connector._apply_events(events, [])

    assert _looked_up(connector) == ["20"]
    records = {r.external_record_id: r for c in connector.data_entities_processor.on_new_records.await_args_list
               for r, _ in c.args[0]}
    assert sorted(records) == ["page/10", "page/11", "page/12", "page/20"]
    # The listed row carries the page's parent and owner into its record and its access.
    assert records["page/10"].external_record_group_id == "book/1"
    assert records["page/11"].external_record_group_id == "chapter/5"
    owners = {c.args[1]["id"]: c.args[1]["owned_by"] for c in connector._access.await_args_list if c.args[0] == "page"}
    assert owners == {10: 1, 11: 7, 12: 8, 20: 1}
    assert connector._health.ok
