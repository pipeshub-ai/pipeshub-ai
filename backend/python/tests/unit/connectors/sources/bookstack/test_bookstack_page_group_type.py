"""A page record names the type of the group it sits in, so a page written before its chapter or book (a group missed
by the listing, or a page event applied first) is placed in a group of the right type instead of one with no type,
which no graph store can write."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector
from app.models.entities import RecordGroupType
from tests.unit.connectors.core.test_data_processor import (
    _make_processor,
    _make_tx_store,
)


def _row(chapter_id: int) -> dict:
    return {"id": 10, "name": "Page 10", "book_id": 1, "chapter_id": chapter_id, "owned_by": 1,
            "book_slug": "handbook", "slug": "page-10", "revision_count": 1,
            "created_at": "2026-10-01T10:00:00.000000Z", "updated_at": "2026-10-02T10:00:00.000000Z"}


@pytest.fixture
def connector() -> BookStackConnector:
    processor = MagicMock(org_id="org-1")
    processor.get_record_by_external_id = AsyncMock(return_value=None)
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        conn = BookStackConnector(logging.getLogger("bookstack-group-type"), processor, AsyncMock(), AsyncMock(),
                                  "bs-group-type", "team", "admin")
    conn._access = AsyncMock(return_value=MagicMock(permissions=[]))
    conn.bookstack_base_url = "https://wiki.example.com"
    return conn


_CASES = [(5, "chapter/5", RecordGroupType.CHAPTER), (0, "book/1", RecordGroupType.BOOK)]


@pytest.mark.asyncio
@pytest.mark.parametrize(("chapter_id", "group", "group_type"), _CASES)
async def test_a_page_record_names_its_group_type(connector, chapter_id, group, group_type) -> None:
    record, _ = await connector._page_record(_row(chapter_id))

    assert record.external_record_group_id == group
    assert record.record_group_type == group_type


@pytest.mark.asyncio
@pytest.mark.parametrize(("chapter_id", "group", "group_type"), _CASES)
async def test_a_page_before_its_group_is_placed_in_a_group_of_that_type(
    connector, chapter_id, group, group_type
) -> None:
    record, _ = await connector._page_record(_row(chapter_id))
    proc = _make_processor()
    tx_store = _make_tx_store()
    written: list = []

    async def _write(groups: list) -> None:
        for g in groups:
            # What every graph store does with a group; a group with no type fails here.
            g.to_arango_base_record_group()
        written.extend(groups)

    tx_store.batch_upsert_record_groups = AsyncMock(side_effect=_write)

    group_id = await proc._handle_record_group(record, tx_store)

    assert [(g.external_group_id, g.group_type) for g in written] == [(group, group_type)]
    assert group_id == written[0].id == record.record_group_id
