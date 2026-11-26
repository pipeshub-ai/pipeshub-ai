"""What a BookStack full sync leaves linked once the runner unlinks every node it did not see.

After a full sync the runner (``sync_runner._unlink_what_full_sync_missed``) removes every sync edge of each
stored node the run neither wrote nor noted as kept, unless ``full_sync_coverage()`` says the run did not read
everything. So a book, chapter, page or role still in BookStack must be written or noted kept, and one deleted
in BookStack must still go.

The connector's own sync code runs over an in-memory BookStack. Its listings page by offset, so an item deleted
while one is read can shift a live item past it: the listing misses it while a lookup by id still finds it. The
store keeps what the processor would and notes what a full sync wrote, as the processor does.
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.core.sync import sync_runner
from app.connectors.core.sync.full_sync_coverage import FullSyncSeen
from app.connectors.sources.bookstack.connector import BookStackConnector
from app.connectors.sources.bookstack.sync_health import BookStackSyncIncompleteError
from app.models.entities import Record
from app.services.graph_db.common.record_visibility import (
    RecordVisibility,
    matches_visibility,
)
from app.sources.client.bookstack.bookstack import BookStackResponse

CONNECTOR_ID = "bs-unlink"
VIEWER = 3
RECORDS, GROUPS, ROLES = (CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value,
                          CollectionNames.ROLES.value)


def _ok(body: dict[str, Any]) -> BookStackResponse:
    return BookStackResponse(success=True, data=body, status_code=200)


def _error(status: int) -> BookStackResponse:
    return BookStackResponse(success=False, data={"error": {"code": status, "message": "not now"}},
                             status_code=status, error="not now")


class FakeBookStack:
    """Books, chapters, pages, users and roles behind BookStack's API, as the data source returns them."""

    def __init__(self) -> None:
        self.users = {1: "u1@example.com"}
        self.roles: dict[int, dict[str, Any]] = {VIEWER: {
            "id": VIEWER, "display_name": "Viewer", "system_name": "",
            "permissions": ["book-view-all", "chapter-view-all", "page-view-all"], "users": [{"id": 1}],
        }}
        self.books: dict[int, dict[str, Any]] = {}
        self.chapters: dict[int, dict[str, Any]] = {}
        self.pages: dict[int, dict[str, Any]] = {}
        # "book/20", "page/2": left out of the listings (shifted past by a delete) while a lookup by id finds them.
        self.missed_by_listing: set[str] = set()
        # "page/2" -> the status the content permission read answers with.
        self.permissions_status: dict[str, int] = {}
        # Page ids whose lookup by id gets no listing back.
        self.lookup_fails: set[int] = set()
        # "book/20" -> what get_book or get_chapter answers in place of the item.
        self.get_answers: dict[str, BookStackResponse] = {}
        # "page/2" -> the role rows of its content permissions.
        self.permission_rows: dict[str, list[dict[str, Any]]] = {}

    def add_book(self, book_id: int) -> None:
        self.books[book_id] = {"id": book_id, "name": f"book-{book_id}", "slug": f"book-{book_id}"}

    def add_chapter(self, chapter_id: int, book_id: int) -> None:
        self.chapters[chapter_id] = {"id": chapter_id, "name": f"chapter-{chapter_id}", "book_id": book_id}

    def add_page(self, page_id: int, book_id: int, chapter_id: int | None = None) -> None:
        self.pages[page_id] = {
            "id": page_id, "name": f"page-{page_id}", "book_id": book_id, "chapter_id": chapter_id,
            "slug": f"page-{page_id}", "book_slug": f"book-{book_id}", "revision_count": 1, "draft": False,
            "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-01-02T00:00:00Z",
        }

    @staticmethod
    def _page(rows: list[dict[str, Any]], count: int | None, offset: int | None) -> BookStackResponse:
        start = offset or 0
        return _ok({"data": rows[start:start + (count or 100)], "total": len(rows)})

    def _listed(self, kind: str, items: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
        return [items[k] for k in sorted(items) if f"{kind}/{k}" not in self.missed_by_listing]

    async def list_audit_log(self, **_: object) -> BookStackResponse:
        return _ok({"data": [{"id": 7, "type": "page_update"}], "total": 7})

    async def list_users(self, count: int | None = None, offset: int | None = None) -> BookStackResponse:
        rows = [{"id": uid, "name": f"User {uid}", "email": email} for uid, email in sorted(self.users.items())]
        return self._page(rows, count, offset)

    async def get_user(self, user_id: int) -> BookStackResponse:
        if user_id not in self.users:
            return _error(404)
        return _ok({"id": user_id, "name": f"User {user_id}", "email": self.users[user_id]})

    async def list_roles(self, count: int | None = None, offset: int | None = None) -> BookStackResponse:
        rows = [{"id": rid, "display_name": r["display_name"]} for rid, r in sorted(self.roles.items())]
        return self._page(rows, count, offset)

    async def get_role(self, role_id: int) -> BookStackResponse:
        return _ok(dict(self.roles[role_id])) if role_id in self.roles else _error(404)

    async def list_books(self, count: int | None = None, offset: int | None = None,
                         filter: dict[str, str] | None = None, **_: object) -> BookStackResponse:  # noqa: A002
        return self._page(self._listed("book", self.books), count, offset)

    async def list_chapters(self, count: int | None = None, offset: int | None = None,
                            filter: dict[str, str] | None = None, **_: object) -> BookStackResponse:  # noqa: A002
        rows = self._listed("chapter", self.chapters)
        if filter and "book_id" in filter:
            rows = [c for c in rows if str(c["book_id"]) == filter["book_id"]]
        return self._page(rows, count, offset)

    async def get_book(self, book_id: int) -> BookStackResponse:
        if f"book/{book_id}" in self.get_answers:
            return self.get_answers[f"book/{book_id}"]
        return _ok(dict(self.books[book_id])) if book_id in self.books else _error(404)

    async def get_chapter(self, chapter_id: int) -> BookStackResponse:
        if f"chapter/{chapter_id}" in self.get_answers:
            return self.get_answers[f"chapter/{chapter_id}"]
        return _ok(dict(self.chapters[chapter_id])) if chapter_id in self.chapters else _error(404)

    async def list_pages(self, count: int | None = None, offset: int | None = None,
                         sort: str | None = None, filter: dict[str, str] | None = None) -> BookStackResponse:  # noqa: A002
        if filter and "id" in filter:
            page_id = int(filter["id"])
            if page_id in self.lookup_fails:
                return _error(503)
            found = [self.pages[page_id]] if page_id in self.pages else []
            body: dict[str, Any] = {"data": found, "total": len(found)}
        else:
            rows = self._listed("page", self.pages)
            start = offset or 0
            body = {"data": rows[start:start + (count or 100)], "total": len(rows)}
        return BookStackResponse(success=True, data={"content": json.dumps(body), "content_type": "application/json"})

    async def get_content_permissions(self, content_type: str, content_id: int) -> BookStackResponse:
        status = self.permissions_status.get(f"{content_type}/{content_id}")
        if status is not None:
            return _error(status)
        return _ok({"owner": {"id": 1}, "role_permissions": self.permission_rows.get(f"{content_type}/{content_id}", []),
                    "fallback_permissions": {"inheriting": True}})


class FakeGraph:
    """The nodes the processor stores for this connector, and what the full sync in progress wrote or kept."""

    async def skips_permission_writes(self, connector_id: str | None) -> bool:
        return False  # licensed: the access reads these tests check run

    def __init__(self) -> None:
        self.org_id = "org-1"
        self.records: dict[str, Record] = {}
        self.groups: dict[str, str | None] = {}  # external id -> parent external id
        self.group_names: dict[str, str] = {}
        self.roles: set[str] = set()
        self.users: list[Any] = []
        self.removed: set[str] = set()
        self.seen = FullSyncSeen()

    # -- writes, each noted as seen as the processor notes them --
    async def on_new_app_users(self, users: list[Any]) -> None:
        self.users = list(users)

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        for role, _ in roles:
            self.roles.add(role.source_role_id)
            self.seen.add(ROLES, role.source_role_id)

    async def on_new_record_groups(self, batch: list[tuple[Any, Any]]) -> None:
        for group, _ in batch:
            self.groups[group.external_group_id] = group.parent_external_group_id
            self.group_names[group.external_group_id] = group.name
            self.seen.add(GROUPS, group.external_group_id)

    async def on_new_records(self, batch: list[tuple[Any, Any]]) -> None:
        for record, _ in batch:
            self.records[record.external_record_id] = Record.model_validate(
                record.model_dump(include=set(Record.model_fields)))
            self.seen.add(RECORDS, record.id)
            self.seen.add(GROUPS, record.external_record_group_id)

    def note_kept_records(self, connector_id: str, record_ids: list[str]) -> None:
        for record_id in record_ids:
            self.seen.add(RECORDS, record_id)

    async def note_kept_record_group(self, connector_id: str, external_group_id: str) -> None:
        if external_group_id in self.groups:
            self.seen.add(GROUPS, external_group_id)

    async def has_stored_permissions(self, entity: str, connector_id: str, external_id: str, *,
                                     keep: bool = False) -> bool:
        if keep:
            self.seen.add(ROLES, external_id)
        return external_id in self.roles

    # -- removals the connector makes itself --
    async def on_record_deleted(self, record_id: str) -> None:
        for external_id, record in list(self.records.items()):
            if record.id == record_id:
                del self.records[external_id]
                self.removed.add(f"record:{external_id}")

    async def on_record_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        """With every group and record below it, as the processor deletes them."""
        if external_group_id not in self.groups:
            return False
        doomed = {external_group_id} | {g for g, parent in self.groups.items() if parent == external_group_id}
        for group in doomed:
            del self.groups[group]
            self.removed.add(f"recordGroup:{group}")
        for external_id, record in list(self.records.items()):
            if record.external_record_group_id in doomed:
                del self.records[external_id]
                self.removed.add(f"record:{external_id}")
        return True

    async def on_app_role_deleted(self, external_role_id: str, connector_id: str) -> bool:
        if external_role_id not in self.roles:
            return False
        self.roles.discard(external_role_id)
        self.removed.add(f"role:{external_role_id}")
        return True

    async def withdraw_app_user_access(self, connector_id: str, users: list[Any]) -> None:
        return None

    # -- reads --
    async def get_all_app_users(self, connector_id: str, *, raise_on_error: bool = False) -> list[Any]:
        return list(self.users)

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Record | None:
        record = self.records.get(external_id)
        return None if record is None else record.model_copy(deep=True)

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> str | None:
        return external_id if external_id in self.groups else None

    async def get_records_by_status(self, connector_id: str, status_filters: list[str] | None,
                                    limit: int | None = None, offset: int = 0, after_key: str | None = None,
                                    visibility: RecordVisibility = RecordVisibility.LIVE,
                                    **_: object) -> list[Record]:
        ordered = sorted((r for r in self.records.values() if matches_visibility(r, visibility)), key=lambda r: r.id)
        if after_key is not None:
            ordered = [r for r in ordered if r.id > after_key]
        return ordered[:limit] if limit else ordered

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any],
                                   return_fields: list[str] | None = None, *,
                                   raise_on_error: bool = False) -> list[dict[str, Any]]:
        if collection == ROLES:
            return [{"externalRoleId": role_id} for role_id in sorted(self.roles)]
        assert collection == GROUPS
        return [{"externalGroupId": external_id} for external_id in sorted(self.groups)]

    def stored(self) -> dict[str, dict[str, str]]:
        """collection -> {the node id a full sync notes: its name in these tests}."""
        return {
            RECORDS: {record.id: f"record:{external_id}" for external_id, record in self.records.items()},
            GROUPS: {external_id: f"recordGroup:{external_id}" for external_id in self.groups},
            ROLES: {role_id: f"role:{role_id}" for role_id in self.roles},
        }


class World:
    def __init__(self, source: FakeBookStack, graph: FakeGraph, connector: BookStackConnector) -> None:
        self.source, self.graph, self.connector = source, graph, connector

    async def full_sync(self) -> set[str]:
        """Run a full sync as the runner does (sync points reset first, then the unlink of what the run did not
        see), and return every node the run took away: removed by the connector, or unlinked after it."""
        self.connector.record_sync_point.points.clear()
        self.graph.seen = FullSyncSeen()
        self.graph.removed = set()
        failed = False
        with patch("app.connectors.sources.bookstack.connector.load_connector_filters",
                   AsyncMock(return_value=(FilterCollection(), FilterCollection()))):
            try:
                await self.connector.run_sync()
            except BookStackSyncIncompleteError:
                failed = True
        if failed:
            # A run that raised is a failed run: the runner unlinks nothing after it.
            return set(self.graph.removed)
        stored = self.graph.stored()
        unlinked: set[str] = set()

        async def unlink(_connector_id: str, seen_by_collection: dict[str, set[str]], **_: object) -> tuple[int, bool]:
            for collection, names in stored.items():
                seen_ids = seen_by_collection.get(collection) or set()
                unlinked.update(name for node_id, name in names.items() if node_id not in seen_ids)
            return len(unlinked), True

        graph_provider = AsyncMock()
        graph_provider.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=unlink)
        sync_runner._INCOMPLETE_FULL_SYNC_RETRYING.discard(CONNECTOR_ID)
        await sync_runner._unlink_what_full_sync_missed(
            self.connector, CONNECTOR_ID, graph_provider, logging.getLogger("bookstack-unlink"), self.graph.seen)
        sync_runner._INCOMPLETE_FULL_SYNC_RETRYING.discard(CONNECTOR_ID)
        return self.graph.removed | unlinked

    def at_source(self) -> set[str]:
        s = self.source
        return ({f"record:page/{p}" for p in s.pages} | {f"recordGroup:book/{b}" for b in s.books}
                | {f"recordGroup:chapter/{c}" for c in s.chapters} | {f"role:{r}" for r in s.roles})


class _Points:
    def __init__(self) -> None:
        self.points: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict:
        return dict(self.points.get(key, {}))

    async def update_sync_point(self, key: str, data: dict) -> None:
        self.points.setdefault(key, {}).update(data)


@pytest.fixture
def world() -> Iterator[World]:
    """Book 10 with pages 1-4; book 20 with chapter 21 and page 5 in it; book 30 with page 6; a Viewer role.
    Synced once in full, so everything is stored."""
    source, graph = FakeBookStack(), FakeGraph()
    for book_id in (10, 20, 30):
        source.add_book(book_id)
    source.add_chapter(21, 20)
    for page_id in (1, 2, 3, 4):
        source.add_page(page_id, 10)
    source.add_page(5, 20, 21)
    source.add_page(6, 30)
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"), \
         patch("app.connectors.sources.bookstack.connector.SyncPoint", side_effect=lambda **_: _Points()):
        connector = BookStackConnector(logging.getLogger("bookstack-unlink"), graph, AsyncMock(), AsyncMock(),
                                       CONNECTOR_ID, "team", "admin")
    connector.data_source = source
    connector.bookstack_base_url = "https://bookstack.example.com/"
    connector.notify = AsyncMock()
    yield World(source, graph, connector)


async def _stored(world: World) -> None:
    assert await world.full_sync() == set()
    assert set().union(*(names.values() for names in world.graph.stored().values())) == world.at_source()


def _split(world: World, taken: set[str], gone: set[str]) -> tuple[list[str], list[str]]:
    """(wrongly unlinked: still in BookStack, really gone: deleted in BookStack and taken away)."""
    return sorted(taken & world.at_source()), sorted(taken & gone)


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_but_bookstack_still_has_keeps_its_links(world: World) -> None:
    """A page the listing left out but a lookup by id still finds is written again, so the unlink after the full
    sync leaves it."""
    await _stored(world)
    world.source.pages.pop(4)
    world.source.missed_by_listing.add("page/2")

    taken = await world.full_sync()

    assert _split(world, taken, {"record:page/4"}) == ([], ["record:page/4"])


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_is_written_again_as_bookstack_has_it(world: World) -> None:
    await _stored(world)
    record_id = world.graph.records["page/2"].id
    world.source.pages[2].update(name="page-2-renamed", revision_count=2)
    world.source.missed_by_listing.add("page/2")

    taken = await world.full_sync()

    assert taken == set()
    stored = world.graph.records["page/2"]
    assert (stored.record_name, stored.id) == ("page-2-renamed", record_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [503, 404])
async def test_a_page_the_listing_missed_whose_access_cannot_be_read_keeps_its_links(
    world: World, status: int,
) -> None:
    """Missed by the listing, page 2's permissions are read only by the removal's rewrite."""
    await _stored(world)
    world.source.pages.pop(4)
    world.source.missed_by_listing.add("page/2")
    world.source.permissions_status["page/2"] = status

    taken = await world.full_sync()

    assert _split(world, taken, {"record:page/4"}) == ([], ["record:page/4"])
    assert "page/2" in world.graph.records
    if status == 503:
        assert not world.connector._health.ok
    else:
        assert not world.connector.full_sync_coverage().complete


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_whose_lookup_fails_keeps_its_links(world: World) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("page/2")
    world.source.lookup_fails.add(2)

    taken = await world.full_sync()

    assert world.connector._health.problems == ["removing pages BookStack no longer has"]
    assert _split(world, taken, set()) == ([], [])
    assert "page/2" in world.graph.records


@pytest.mark.asyncio
async def test_a_stored_page_record_with_an_unreadable_id_is_kept_and_noted(world: World) -> None:
    await _stored(world)
    stray = world.graph.records["page/1"].model_copy(update={"id": "stray-record", "external_record_id": "page/abc"})
    world.graph.records["page/abc"] = stray

    taken = await world.full_sync()

    assert taken == set()
    assert "page/abc" in world.graph.records


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_that_bookstack_deleted_still_goes(world: World) -> None:
    await _stored(world)
    world.source.pages.pop(2)
    world.source.missed_by_listing.add("page/2")

    assert await world.full_sync() == {"record:page/2"}


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_now_outside_the_book_filter_goes(world: World) -> None:
    """Page 2 moved to book 40, outside the books synced; the lookup by id finds it there."""
    await _stored(world)
    world.source.pages[2]["book_id"] = 40
    world.source.missed_by_listing.add("page/2")

    with patch.object(world.connector, "_get_book_id_filter",
                      return_value=({10, 20, 30}, SimpleNamespace(value="in"))):
        taken = await world.full_sync()

    assert taken == {"record:page/2"}


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_keeps_access_that_follows_memberships(world: World) -> None:
    """Page 2 denies a role no one is in yet: its access holds only while memberships stay as they are, so it
    must stay among the pages a membership change works out again."""
    world.source.roles[4] = {"id": 4, "display_name": "Blocked", "system_name": "",
                             "permissions": ["page-view-all"], "users": []}
    world.source.permission_rows["page/2"] = [{"role_id": 4, "view": False}]
    await _stored(world)
    assert 2 in world.connector._dependent_pages
    world.source.missed_by_listing.add("page/2")

    assert await world.full_sync() == set()
    assert 2 in world.connector._dependent_pages
    (state,) = world.connector.record_sync_point.points.values()
    assert 2 in state["dependentPages"]


@pytest.mark.asyncio
async def test_a_page_the_listing_missed_now_outside_the_date_filter_goes(world: World) -> None:
    await _stored(world)
    for page_id, page in world.source.pages.items():
        if page_id != 2:
            page["updated_at"] = "2026-01-10T00:00:00Z"
    world.source.missed_by_listing.add("page/2")

    with patch.object(world.connector, "_get_date_filters",
                      return_value=(datetime(2026, 1, 5, tzinfo=timezone.utc), None, None, None)):
        taken = await world.full_sync()

    assert taken == {"record:page/2"}


@pytest.mark.asyncio
async def test_missed_pages_are_written_again_even_when_another_lookup_fails(world: World) -> None:
    await _stored(world)
    record_id = world.graph.records["page/2"].id
    world.source.pages.pop(4)
    world.source.pages[2]["name"] = "page-2-renamed"
    world.source.missed_by_listing |= {"page/2", "page/3"}
    world.source.lookup_fails.add(3)

    taken = await world.full_sync()

    assert world.connector._health.problems == ["removing pages BookStack no longer has"]
    assert taken == {"record:page/4"}
    stored = world.graph.records["page/2"]
    assert (stored.record_name, stored.id) == ("page-2-renamed", record_id)
    assert "page/3" in world.graph.records


@pytest.mark.asyncio
async def test_a_purge_writes_again_a_page_the_id_listing_missed(world: World) -> None:
    """The incremental removal after a purge shares the page removal: a page its id listing missed is written
    again, not deleted."""
    await _stored(world)
    record_id = world.graph.records["page/2"].id
    world.connector._rows_cache = {}
    world.source.pages.pop(4)
    world.source.pages[2]["name"] = "page-2-renamed"
    world.source.missed_by_listing.add("page/2")

    assert await world.connector._remove_pages_no_longer_in_bookstack() is True

    assert "page/4" not in world.graph.records
    stored = world.graph.records["page/2"]
    assert (stored.record_name, stored.id) == ("page-2-renamed", record_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("missed", ["book/20", "chapter/21"])
async def test_a_book_or_chapter_the_listing_missed_but_bookstack_still_has_keeps_its_links(
    world: World, missed: str,
) -> None:
    """A book or chapter missed by a shifted listing is looked up by id and written again with what is under it;
    a book BookStack deleted still goes."""
    await _stored(world)
    world.source.books.pop(30)
    world.source.pages.pop(6)
    world.source.missed_by_listing.add(missed)

    record_id = world.graph.records["page/5"].id

    taken = await world.full_sync()

    assert _split(world, taken, {"recordGroup:book/30", "record:page/6"}) == (
        [], ["record:page/6", "recordGroup:book/30"])
    assert world.graph.records["page/5"].id == record_id


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_is_written_again_as_bookstack_has_it(world: World) -> None:
    """Chapter 21 is dropped from the listing with its book and is written again under it."""
    await _stored(world)
    world.source.books[20]["name"] = "book-20-renamed"
    world.source.missed_by_listing.add("book/20")

    assert await world.full_sync() == set()
    assert world.graph.group_names["book/20"] == "book-20-renamed"
    assert world.graph.groups["chapter/21"] == "book/20"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [
    _error(503),
    BookStackResponse(success=True, data={"error": {"code": 429, "message": "Too many attempts"}}, status_code=429),
], ids=["503", "error-body"])
async def test_a_book_the_listing_missed_whose_lookup_fails_keeps_its_links(
    world: World, answer: BookStackResponse,
) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("book/20")
    world.source.get_answers["book/20"] = answer

    taken = await world.full_sync()

    assert world.connector._health.problems == ["removing books and chapters BookStack no longer has"]
    assert taken == set()
    assert {"book/20", "chapter/21"} <= set(world.graph.groups)
    assert "page/5" in world.graph.records


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_whose_access_cannot_be_read_keeps_its_links(world: World) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("book/20")
    world.source.permissions_status["book/20"] = 404

    taken = await world.full_sync()

    assert not world.connector.full_sync_coverage().complete
    assert taken == set()
    assert {"book/20", "chapter/21"} <= set(world.graph.groups)
    assert "page/5" in world.graph.records


@pytest.mark.asyncio
async def test_an_empty_book_the_listing_missed_whose_access_cannot_be_read_keeps_its_links(world: World) -> None:
    """Book 40 has nothing under it, so only its own rewrite reads its access."""
    world.source.add_book(40)
    await _stored(world)
    world.source.missed_by_listing.add("book/40")
    world.source.permissions_status["book/40"] = 404

    taken = await world.full_sync()

    assert not world.connector.full_sync_coverage().complete
    assert taken == set()
    assert "book/40" in world.graph.groups


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_that_bookstack_deleted_still_goes(world: World) -> None:
    await _stored(world)
    world.source.books.pop(30)
    world.source.pages.pop(6)
    world.source.missed_by_listing.add("book/30")

    assert await world.full_sync() == {"recordGroup:book/30", "record:page/6"}


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_now_outside_the_book_filter_goes(world: World) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("book/20")

    with patch.object(world.connector, "_get_book_id_filter",
                      return_value=({10, 30}, SimpleNamespace(value="in"))):
        taken = await world.full_sync()

    assert taken == {"recordGroup:book/20", "recordGroup:chapter/21", "record:page/5"}


@pytest.mark.asyncio
async def test_a_chapter_of_a_deleted_book_goes_with_it(world: World) -> None:
    await _stored(world)
    world.source.books.pop(20)
    world.source.chapters.pop(21)
    world.source.pages.pop(5)

    taken = await world.full_sync()

    assert taken == {"recordGroup:book/20", "recordGroup:chapter/21", "record:page/5"}
    assert world.connector._health.ok


@pytest.mark.asyncio
async def test_a_book_and_its_chapter_both_missed_by_the_listings_keep_their_links(world: World) -> None:
    await _stored(world)
    record_id = world.graph.records["page/5"].id
    world.source.missed_by_listing |= {"book/20", "chapter/21"}

    assert await world.full_sync() == set()
    assert world.graph.groups["chapter/21"] == "book/20"
    assert world.graph.records["page/5"].id == record_id


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_whose_access_read_fails_fails_the_run(world: World) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("book/20")
    world.source.permissions_status["book/20"] = 503

    taken = await world.full_sync()

    assert not world.connector._health.ok
    assert taken == set()
    assert {"book/20", "chapter/21"} <= set(world.graph.groups)
    assert "page/5" in world.graph.records


@pytest.mark.asyncio
async def test_a_chapter_the_listing_missed_whose_lookup_is_refused_keeps_its_links(world: World) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("chapter/21")
    world.source.get_answers["chapter/21"] = _error(403)

    taken = await world.full_sync()

    assert world.connector._health.problems == ["removing books and chapters BookStack no longer has"]
    assert taken == set()
    assert "chapter/21" in world.graph.groups
    assert "page/5" in world.graph.records


@pytest.mark.asyncio
async def test_a_chapter_the_listing_missed_in_a_book_the_filter_includes_keeps_its_links(world: World) -> None:
    await _stored(world)
    record_id = world.graph.records["page/5"].id
    world.source.missed_by_listing.add("chapter/21")

    with patch.object(world.connector, "_get_book_id_filter",
                      return_value=({10, 20, 30}, SimpleNamespace(value="in"))):
        taken = await world.full_sync()

    assert taken == set()
    assert world.graph.groups["chapter/21"] == "book/20"
    assert world.graph.records["page/5"].id == record_id


@pytest.mark.asyncio
async def test_a_chapter_the_listing_missed_now_in_a_book_outside_the_filter_goes(world: World) -> None:
    await _stored(world)
    world.source.chapters[21]["book_id"] = 40
    world.source.missed_by_listing.add("chapter/21")

    with patch.object(world.connector, "_get_book_id_filter",
                      return_value=({10, 20, 30}, SimpleNamespace(value="in"))):
        taken = await world.full_sync()

    assert "recordGroup:chapter/21" in taken
    assert "book/20" in world.graph.groups


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_whose_lookup_raises_removes_nothing(world: World) -> None:
    await _stored(world)
    world.source.missed_by_listing.add("book/20")
    world.connector.data_source = SimpleNamespace(
        **{name: getattr(world.source, name) for name in dir(world.source) if not name.startswith("_")})
    world.connector.data_source.get_book = AsyncMock(side_effect=ConnectionError("reset"))

    with pytest.raises(ConnectionError):
        await world.full_sync()

    assert world.graph.removed == set()
    assert {"book/20", "chapter/21"} <= set(world.graph.groups)


@pytest.mark.asyncio
async def test_a_book_the_listing_missed_keeps_access_that_follows_memberships(world: World) -> None:
    world.source.roles[4] = {"id": 4, "display_name": "Blocked", "system_name": "",
                             "permissions": ["book-view-all"], "users": []}
    world.source.permission_rows["book/20"] = [{"role_id": 4, "view": False}]
    await _stored(world)
    assert "book/20" in world.connector._dependent_groups
    world.source.missed_by_listing.add("book/20")

    assert await world.full_sync() == set()
    assert "book/20" in world.connector._dependent_groups
    (state,) = world.connector.record_sync_point.points.values()
    assert "book/20" in state["dependentGroups"]


@pytest.mark.asyncio
async def test_a_purge_writes_again_a_book_the_listing_missed(world: World) -> None:
    await _stored(world)
    world.connector._rows_cache = {}
    world.source.books.pop(30)
    world.source.pages.pop(6)
    world.source.books[20]["name"] = "book-20-renamed"
    world.source.missed_by_listing.add("book/20")

    assert await world.connector._remove_groups_no_longer_in_bookstack() is True

    assert world.graph.removed == {"recordGroup:book/30", "record:page/6"}
    assert world.graph.group_names["book/20"] == "book-20-renamed"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [503, 404])
async def test_a_page_whose_access_cannot_be_read_keeps_its_links(world: World, status: int) -> None:
    """Control: a failed permission read leaves the page as stored and the run does not unlink what it did not
    see (503 fails the run; 404 marks it incomplete). A page deleted in BookStack still goes."""
    await _stored(world)
    world.source.pages.pop(4)
    world.source.permissions_status["page/2"] = status

    taken = await world.full_sync()

    assert _split(world, taken, {"record:page/4"}) == ([], ["record:page/4"])


@pytest.mark.asyncio
async def test_what_bookstack_deleted_goes_and_nothing_else(world: World) -> None:
    """Control: a full sync with every read answered takes away a deleted page, book and role, and nothing else."""
    world.source.roles[5] = {"id": 5, "display_name": "Old", "system_name": "", "permissions": [], "users": []}
    await _stored(world)
    world.source.pages.pop(4)
    world.source.books.pop(30)
    world.source.pages.pop(6)
    world.source.roles.pop(5)

    taken = await world.full_sync()

    assert _split(world, taken, {"record:page/4", "recordGroup:book/30", "record:page/6", "role:5"}) == (
        [], ["record:page/4", "record:page/6", "recordGroup:book/30", "role:5"])

