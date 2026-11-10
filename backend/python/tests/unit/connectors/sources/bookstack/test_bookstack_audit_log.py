"""Reading BookStack's audit log by event id (``audit_log.py``)."""

from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.bookstack.audit_log import (
    AuditLogReader,
    entity_ref,
    named_id,
    restored_ref,
)
from app.sources.client.bookstack.bookstack import BookStackResponse


def listing(rows: list[dict], total: int) -> BookStackResponse:
    return BookStackResponse(success=True, data={"data": rows, "total": total}, status_code=200)


def events(*ids: int) -> list[dict]:
    return [{"id": i, "type": "page_update", "loggable_id": i, "loggable_type": "page", "detail": f"({i}) P"}
            for i in ids]


@pytest.mark.asyncio
async def test_reads_every_page_after_the_cursor_in_id_order() -> None:
    list_log = AsyncMock(side_effect=[listing(events(11, 12), 3), listing(events(13), 3)])
    read = await AuditLogReader(list_log, page_size=2).read_after(10)
    assert read.complete and [e["id"] for e in read.events] == [11, 12, 13] and read.last_id == 13
    first = list_log.await_args_list[0].kwargs
    assert first["filter"] == {"id:gt": "10"} and first["sort"] == "+id" and first["offset"] == 0
    assert list_log.await_args_list[1].kwargs["offset"] == 2


@pytest.mark.asyncio
async def test_an_error_body_is_not_the_end_of_the_log() -> None:
    refused = BookStackResponse(success=False, data={"error": {"message": "x", "code": 403}}, status_code=403,
                                error="You do not have permission to perform the requested action.")
    read = await AuditLogReader(AsyncMock(side_effect=[listing(events(11), 5), refused]), page_size=1).read_after(10)
    assert not read.complete and read.status == 403 and read.message.startswith("You do not")


@pytest.mark.asyncio
async def test_an_empty_page_short_of_the_total_is_cut_off() -> None:
    read = await AuditLogReader(AsyncMock(side_effect=[listing(events(11), 4), listing([], 4)]),
                                page_size=1).read_after(10)
    assert not read.complete


@pytest.mark.asyncio
async def test_nothing_new_is_a_complete_empty_read() -> None:
    read = await AuditLogReader(AsyncMock(return_value=listing([], 0))).read_after(99)
    assert read.complete and read.events == [] and read.last_id is None


@pytest.mark.asyncio
async def test_latest_id_is_the_newest_event_or_zero() -> None:
    newest = await AuditLogReader(AsyncMock(return_value=listing(events(42), 42))).latest_id()
    assert newest.complete and newest.last_id == 42
    empty = await AuditLogReader(AsyncMock(return_value=listing([], 0))).latest_id()
    assert empty.complete and (empty.last_id or 0) == 0
    failed = await AuditLogReader(AsyncMock(return_value=BookStackResponse(success=False, status_code=502))).latest_id()
    assert not failed.complete and failed.status == 502


def test_event_references() -> None:
    assert entity_ref({"loggable_type": "chapter", "loggable_id": 7}) == ("chapter", 7)
    assert entity_ref({"loggable_type": "page", "loggable_id": None, "detail": "(7) Purged page"}) is None
    assert entity_ref({"loggable_type": "page", "loggable_id": True}) is None
    assert named_id({"type": "user_update", "detail": "(12) Ana (Ops)"}) == 12
    assert named_id({"type": "user_update", "detail": "Ana"}) is None
    assert restored_ref({"detail": "Deletion (3) for chapter (6) Policies"}) == ("chapter", 6)
    assert restored_ref({"detail": "Deletion (3)"}) is None
