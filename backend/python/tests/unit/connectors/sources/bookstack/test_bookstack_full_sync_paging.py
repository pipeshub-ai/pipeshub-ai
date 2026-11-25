"""A full sync writes each page of the page listing before it reads the next, and removes only after a complete
listing; a group removal the processor reports as failed keeps the run from succeeding."""

import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector
from app.sources.client.bookstack.bookstack import BookStackResponse

_NOT_FOUND = BookStackResponse(success=False, data={"error": {"code": 404, "message": "Not found"}}, status_code=404)


def _pages(rows: list[dict], total: int) -> BookStackResponse:
    return BookStackResponse(success=True, data={"content": json.dumps({"data": rows, "total": total})})


def _page(page_id: int) -> dict:
    return {"id": page_id, "name": f"Page {page_id}", "book_id": 1}


@pytest.fixture
def connector() -> BookStackConnector:
    processor = MagicMock(org_id="org")
    processor.on_new_records = AsyncMock()
    processor.on_record_group_deleted = AsyncMock(return_value=True)
    processor.get_records_by_status = AsyncMock(return_value=[])
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        conn = BookStackConnector(logging.getLogger("bookstack-paging"), processor, AsyncMock(), AsyncMock(),
                                  "bs-paging", "team", "admin")
    conn.batch_size = 2
    conn.data_source = MagicMock()
    conn.data_source.get_book = AsyncMock(return_value=_NOT_FOUND)
    conn.data_source.get_chapter = AsyncMock(return_value=_NOT_FOUND)
    conn._page_record = AsyncMock(side_effect=lambda page: (MagicMock(external_record_id=f"page/{page['id']}"), []))
    conn._get_book_id_filter = MagicMock(return_value=([], None))
    conn._get_date_filters = MagicMock(return_value=(None, None, None, None))
    conn._build_date_filter_params = MagicMock(return_value=None)
    conn._remove_pages_not_listed = AsyncMock(return_value=True)
    return conn


def _calls(connector: BookStackConnector, listings: list[BookStackResponse]) -> list[str]:
    """Answer the page listing with ``listings`` in turn, and record the order of listing reads and writes."""
    order: list[str] = []

    async def read(**kwargs: object) -> BookStackResponse:
        order.append(f"list@{kwargs['offset']}")
        return listings.pop(0)

    async def write(batch: list) -> None:
        order.append("write:" + ",".join(record.external_record_id for record, _ in batch))

    connector.data_source.list_pages = AsyncMock(side_effect=read)
    connector.data_entities_processor.on_new_records = AsyncMock(side_effect=write)
    return order


@pytest.mark.asyncio
async def test_each_listing_page_is_written_before_the_next_is_read(connector) -> None:
    order = _calls(connector, [_pages([_page(1), _page(2), _page(3)], 5), _pages([_page(4), _page(5)], 5)])

    await connector._sync_records_full()

    # A batch that is not full carries over to the next listing page: memory holds one listing page and one batch.
    assert order == ["list@0", "write:page/1,page/2", "list@3", "write:page/3,page/4", "write:page/5"]
    listed = connector._remove_pages_not_listed.await_args.args[0]
    assert listed == {f"page/{i}" for i in range(1, 6)}
    assert connector._health.ok


@pytest.mark.asyncio
@pytest.mark.parametrize("second", [
    BookStackResponse(success=False, data={"error": {"message": "An error occurred", "code": 503}}, status_code=503),
    _pages([], 5),
], ids=["error", "cut-off"])
async def test_a_listing_that_stops_early_removes_nothing_and_fails(connector, second) -> None:
    order = _calls(connector, [_pages([_page(1), _page(2), _page(3)], 5), second])

    await connector._sync_records_full()

    assert order == ["list@0", "write:page/1,page/2", "list@3", "write:page/3"]
    connector._remove_pages_not_listed.assert_not_awaited()
    assert not connector._health.ok


@pytest.mark.asyncio
async def test_a_group_removal_the_processor_reports_as_failed_fails_the_run(connector) -> None:
    connector.data_entities_processor.get_nodes_by_filters = AsyncMock(return_value=[
        {"externalGroupId": "book/9"}, {"externalGroupId": "book/1"}])
    connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=False)
    connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(return_value=MagicMock())

    assert await connector._remove_groups_not_listed({"book/1"}) is False
    connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with("book/9", "bs-paging")


@pytest.mark.asyncio
@pytest.mark.parametrize("lookup", [AsyncMock(return_value=MagicMock()), AsyncMock(side_effect=RuntimeError("down"))],
                         ids=["still-stored", "lookup-fails"])
async def test_a_group_that_could_not_be_removed_fails_the_run(connector, lookup) -> None:
    connector.data_entities_processor.get_nodes_by_filters = AsyncMock(return_value=[{"externalGroupId": "book/9"}])
    connector.data_entities_processor.on_record_group_deleted = AsyncMock(return_value=False)
    connector.data_entities_processor.get_record_group_by_external_id = lookup

    assert await connector._remove_groups_not_listed(set()) is False


@pytest.mark.asyncio
async def test_a_chapter_already_removed_with_its_book_is_not_a_failure(connector) -> None:
    connector.data_entities_processor.get_nodes_by_filters = AsyncMock(return_value=[
        {"externalGroupId": "book/9"}, {"externalGroupId": "chapter/13"}])
    gone: set[str] = set()

    async def delete(external_id: str, connector_id: str) -> bool:
        if external_id in gone:
            return False
        gone.update({"book/9", "chapter/13"})
        return True

    async def lookup(connector_id: str, external_id: str) -> object:
        return None if external_id in gone else MagicMock()

    connector.data_entities_processor.on_record_group_deleted = AsyncMock(side_effect=delete)
    connector.data_entities_processor.get_record_group_by_external_id = AsyncMock(side_effect=lookup)

    assert await connector._remove_groups_not_listed(set()) is True


@pytest.mark.asyncio
async def test_a_group_still_in_bookstack_is_not_removed(connector) -> None:
    processor = connector.data_entities_processor
    processor.get_nodes_by_filters = AsyncMock(return_value=[
        {"externalGroupId": "book/9"}, {"externalGroupId": "chapter/13"}])
    processor.on_new_record_groups = AsyncMock()
    connector.data_source.get_book = AsyncMock(
        return_value=BookStackResponse(success=True, data={"id": 9, "name": "b"}, status_code=200))
    connector._access = AsyncMock(return_value=MagicMock(permissions=[]))

    assert await connector._remove_groups_not_listed(set()) is True
    processor.on_record_group_deleted.assert_awaited_once_with("chapter/13", "bs-paging")
    ((batch,), _), = processor.on_new_record_groups.await_args_list
    assert [group.external_group_id for group, _ in batch] == ["book/9"]


@pytest.mark.asyncio
async def test_a_chapter_moved_out_of_a_deleted_book_keeps_its_records(connector) -> None:
    """Book 9 is deleted at the source after its chapter 13 moved to book 2, and the listing missed the chapter.
    The chapter is written under book 2 before book 9 goes, so the book's removal no longer takes it."""
    parents = {"book/9": None, "chapter/13": "book/9"}
    records = {"page/5": ("chapter/13", "record-5")}
    processor = connector.data_entities_processor
    processor.get_nodes_by_filters = AsyncMock(return_value=[
        {"externalGroupId": "book/9"}, {"externalGroupId": "chapter/13"}])

    async def write(batch: list) -> None:
        for group, _ in batch:
            parents[group.external_group_id] = group.parent_external_group_id

    async def delete(external_id: str, connector_id: str) -> bool:
        if external_id not in parents:
            return False
        subtree = {external_id} | {gid for gid, parent in parents.items() if parent == external_id}
        for gid in subtree:
            parents.pop(gid)
        for page in [page for page, (group, _) in records.items() if group in subtree]:
            records.pop(page)
        return True

    processor.on_new_record_groups = AsyncMock(side_effect=write)
    processor.on_record_group_deleted = AsyncMock(side_effect=delete)
    connector.data_source.get_chapter = AsyncMock(return_value=BookStackResponse(
        success=True, data={"id": 13, "name": "c", "book_id": 2}, status_code=200))
    connector._access = AsyncMock(return_value=MagicMock(permissions=[]))

    assert await connector._remove_groups_not_listed({"book/2"}) is True
    assert parents == {"chapter/13": "book/2"}
    assert records == {"page/5": ("chapter/13", "record-5")}
    processor.on_record_group_deleted.assert_awaited_once_with("book/9", "bs-paging")


@pytest.mark.asyncio
async def test_a_failed_group_lookup_logs_its_status_and_error(connector, caplog) -> None:
    connector.data_source.get_book = AsyncMock(return_value=BookStackResponse(
        success=False, data={"error": {"code": 503, "message": "down"}}, error="Down at https://bs.example/api/books/9",
        status_code=503))

    with caplog.at_level(logging.WARNING, logger="bookstack-paging"):
        assert await connector._fetch_group("book", 9) == (False, None)

    message = caplog.records[-1].getMessage()
    assert "book ID 9 (HTTP 503): Down at [url]" in message
    assert "bs.example" not in message


_UNAVAILABLE = BookStackResponse(success=False, data={"error": {"code": 503, "message": "down"}}, status_code=503)


def _store(connector: BookStackConnector, parents: dict[str, str | None],
           records: dict[str, tuple[str, str]]) -> None:
    """Back the processor with stored groups (id -> parent) and page records (id -> (group, record id)); a group
    removal takes its stored child groups with every record in them (enough for two-level trees), as with soft delete off."""
    processor = connector.data_entities_processor
    processor.get_nodes_by_filters = AsyncMock(side_effect=lambda *a, **k: [
        {"externalGroupId": gid, "parentExternalGroupId": parent} for gid, parent in parents.items()])

    async def write_groups(batch: list) -> None:
        for group, _ in batch:
            parents[group.external_group_id] = group.parent_external_group_id

    async def delete(external_id: str, connector_id: str) -> bool:
        if external_id not in parents:
            return False
        subtree = {external_id} | {gid for gid, parent in parents.items() if parent == external_id}
        for gid in subtree:
            parents.pop(gid)
        for page in [page for page, (group, _) in records.items() if group in subtree]:
            records.pop(page)
        return True

    async def scan(*args: object, **kwargs: object) -> list:
        return [MagicMock(id=rid, external_record_id=page, external_record_group_id=group)
                for page, (group, rid) in records.items()]

    async def write_pages(batch: list) -> None:
        # The processor keeps the id of a stored record; a page with none gets a new one.
        for record, _ in batch:
            records[record.external_record_id] = (
                record.external_record_group_id, records.get(record.external_record_id, (None, "new-id"))[1])

    processor.on_new_record_groups = AsyncMock(side_effect=write_groups)
    processor.on_record_group_deleted = AsyncMock(side_effect=delete)
    processor.get_records_by_status = AsyncMock(side_effect=scan)
    async def lookup(connector_id: str, external_id: str, **kwargs: object) -> object:
        stored = records.get(external_id)
        return MagicMock(external_record_group_id=stored[0]) if stored else None

    processor.on_new_records = AsyncMock(side_effect=write_pages)
    processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
    connector._page_record = AsyncMock(side_effect=lambda page: (MagicMock(
        external_record_id=f"page/{page['id']}", external_record_group_id=f"book/{page['book_id']}"), []))


@pytest.mark.asyncio
async def test_a_deleted_book_whose_chapter_lookup_fails_is_kept_with_the_chapter(connector) -> None:
    """Book 9 answers 404 and its stored chapter 13 answers 503: removing the book would take the chapter and its
    pages, so the book is kept too and the run is incomplete."""
    parents = {"book/9": None, "chapter/13": "book/9"}
    records = {"page/5": ("chapter/13", "record-5")}
    _store(connector, parents, records)
    connector.data_source.get_chapter = AsyncMock(return_value=_UNAVAILABLE)

    assert await connector._remove_groups_not_listed(set()) is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/9": None, "chapter/13": "book/9"}
    assert records == {"page/5": ("chapter/13", "record-5")}


@pytest.mark.asyncio
@pytest.mark.parametrize(("listed", "unwritten"), [(set(), ()), ({"chapter/13"}, {"chapter/13"})],
                         ids=["missed-by-listing", "listed"])
async def test_a_deleted_book_is_kept_while_a_chapter_moved_out_of_it_could_not_be_written(
        connector, listed, unwritten) -> None:
    """Chapter 13 moved from deleted book 9 to book 2, but its access cannot be read, so it is not written and
    is still stored under book 9: the book is kept with it."""
    parents = {"book/9": None, "chapter/13": "book/9"}
    records = {"page/5": ("chapter/13", "record-5")}
    _store(connector, parents, records)
    connector.data_source.get_chapter = AsyncMock(return_value=BookStackResponse(
        success=True, data={"id": 13, "name": "c", "book_id": 2}, status_code=200))
    connector._access = AsyncMock(return_value=None)

    assert await connector._remove_groups_not_listed(listed, unwritten) is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/9": None, "chapter/13": "book/9"}
    assert records == {"page/5": ("chapter/13", "record-5")}


@pytest.mark.asyncio
async def test_a_page_moved_out_of_a_deleted_book_keeps_its_record(connector) -> None:
    """Page 5 moved from book 9 to book 2 before book 9 was deleted: it is written under book 2 before the book
    goes, so it keeps its record id; page 6, gone with the book, is removed with it."""
    parents = {"book/9": None, "book/2": None}
    records = {"page/5": ("book/9", "record-5"), "page/6": ("book/9", "record-6")}
    _store(connector, parents, records)
    rows = {"5": [{"id": 5, "name": "p", "book_id": 2}], "6": []}
    connector.data_source.list_pages = AsyncMock(side_effect=lambda **kw: _pages(rows[kw["filter"]["id"]], 1))

    assert await connector._remove_groups_not_listed({"book/2"}) is True
    connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with("book/9", "bs-paging")
    assert parents == {"book/2": None}
    assert records == {"page/5": ("book/2", "record-5")}


@pytest.mark.asyncio
async def test_a_deleted_book_is_kept_while_a_page_moved_out_of_it_was_not_stored(connector) -> None:
    """Page 5 moved from deleted book 9 to book 2, but the processor skipped its write without raising (a schema
    rejection): it is still under book 9, so the book is kept with it."""
    parents = {"book/9": None, "book/2": None}
    records = {"page/5": ("book/9", "record-5")}
    _store(connector, parents, records)
    connector.data_entities_processor.on_new_records = AsyncMock()
    connector.data_source.list_pages = AsyncMock(return_value=_pages([{"id": 5, "name": "p", "book_id": 2}], 1))

    assert await connector._remove_groups_not_listed({"book/2"}) is False
    connector.data_entities_processor.on_new_records.assert_awaited_once()
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/9": None, "book/2": None}
    assert records == {"page/5": ("book/9", "record-5")}


@pytest.mark.asyncio
async def test_a_deleted_chapter_and_its_book_are_kept_while_a_page_id_is_unreadable(connector) -> None:
    """A stored page with a non-numeric id cannot be looked up, and the page removal keeps it: its chapter and
    the chapter's book are kept with it, while page 6, gone from BookStack, does not hold unrelated book 8."""
    parents = {"book/9": None, "chapter/13": "book/9", "book/8": None}
    records = {"page/legacy": ("chapter/13", "record-legacy"), "page/6": ("book/8", "record-6")}
    _store(connector, parents, records)
    connector.data_source.list_pages = AsyncMock(return_value=_pages([], 0))

    assert await connector._remove_groups_not_listed(set()) is False
    connector.data_entities_processor.on_record_group_deleted.assert_awaited_once_with("book/8", "bs-paging")
    assert parents == {"book/9": None, "chapter/13": "book/9"}
    assert records == {"page/legacy": ("chapter/13", "record-legacy")}


@pytest.mark.asyncio
async def test_a_deleted_book_is_kept_while_a_page_lookup_fails(connector) -> None:
    parents = {"book/9": None}
    records = {"page/5": ("book/9", "record-5")}
    _store(connector, parents, records)
    connector.data_source.list_pages = AsyncMock(return_value=_UNAVAILABLE)

    assert await connector._remove_groups_not_listed(set()) is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert records == {"page/5": ("book/9", "record-5")}


@pytest.mark.asyncio
async def test_a_deleted_book_is_kept_while_a_page_lookup_fails_in_its_deleted_chapter(connector) -> None:
    """Book 9 and its chapter 13 both answer 404, and the lookup of page 5 in the chapter fails: removing the book
    would take the chapter and the page, so the book is kept too."""
    parents = {"book/9": None, "chapter/13": "book/9"}
    records = {"page/5": ("chapter/13", "record-5")}
    _store(connector, parents, records)
    connector.data_source.list_pages = AsyncMock(return_value=_UNAVAILABLE)

    assert await connector._remove_groups_not_listed(set()) is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/9": None, "chapter/13": "book/9"}
    assert records == {"page/5": ("chapter/13", "record-5")}


@pytest.mark.asyncio
async def test_a_full_sync_keeps_a_deleted_book_while_a_chapter_listed_under_another_book_is_not_written(
        connector) -> None:
    """The listings return book 2 and chapter 13 under it, but the chapter's access cannot be read, so it stays
    stored under deleted book 9: the book is kept with it and the run fails."""
    parents = {"book/2": None, "book/9": None, "chapter/13": "book/9"}
    records = {"page/5": ("chapter/13", "record-5")}
    _store(connector, parents, records)
    connector.data_source.list_books = AsyncMock(return_value=BookStackResponse(
        success=True, data={"data": [{"id": 2, "name": "b"}], "total": 1}))
    connector.data_source.list_chapters = AsyncMock(return_value=BookStackResponse(
        success=True, data={"data": [{"id": 13, "name": "c", "book_id": 2}], "total": 1}))
    connector._access = AsyncMock(side_effect=lambda kind, item: None if kind == "chapter" else MagicMock(permissions=[]))

    await connector._sync_groups_full()

    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/2": None, "book/9": None, "chapter/13": "book/9"}
    assert records == {"page/5": ("chapter/13", "record-5")}
    assert not connector._health.ok


@pytest.mark.asyncio
async def test_a_deleted_book_is_kept_while_the_page_records_cannot_be_read(connector) -> None:
    parents = {"book/9": None}
    records = {"page/5": ("book/9", "record-5")}
    _store(connector, parents, records)
    connector.data_entities_processor.get_records_by_status = AsyncMock(side_effect=RuntimeError("db down"))

    assert await connector._remove_groups_not_listed(set()) is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/9": None}
    assert records == {"page/5": ("book/9", "record-5")}


@pytest.mark.asyncio
async def test_a_deleted_book_is_kept_while_a_page_moved_out_of_it_has_no_readable_access(connector) -> None:
    parents = {"book/9": None, "book/2": None}
    records = {"page/5": ("book/9", "record-5")}
    _store(connector, parents, records)
    connector.data_source.list_pages = AsyncMock(return_value=_pages([{"id": 5, "name": "p", "book_id": 2}], 1))
    connector._page_record = AsyncMock(return_value=None)

    assert await connector._remove_groups_not_listed({"book/2"}) is False
    connector.data_entities_processor.on_record_group_deleted.assert_not_awaited()
    assert parents == {"book/9": None, "book/2": None}
    assert records == {"page/5": ("book/9", "record-5")}
    assert connector._kept_unnoted_access
