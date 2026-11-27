"""Notion Personal: what a full sync leaves linked.

Each scenario runs the real connector against an in-memory Notion and store: a first sync stores the
workspace, then the source changes and a full sync runs as ``run_sync_task`` does (sync points cleared,
the processor's own seen bookkeeping, ``_unlink_what_full_sync_missed``). A node is removed when the
full sync deleted it or the unlink would drop its links. Still in Notion and removed: wrongly removed.
Gone from Notion and removed: really gone.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.core.sync import sync_runner
from app.connectors.sources.notion_personal.connector import NotionPersonalConnector
from app.connectors.utils.record_ids import new_record_id
from app.models.entities import (
    OriginTypes,
    Record,
    RecordGroup,
    RecordType,
    WebpageRecord,
)
from app.services.graph_db.common.full_sync_unlink import unseen_node_keys

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.connectors.core.sync.full_sync_coverage import FullSyncSeen

CONNECTOR_ID = "notion-personal-fs"
WORKSPACE = "ws-1"
T1 = "2026-01-01T10:00:00.000Z"
T2 = "2026-02-01T10:00:00.000Z"
RECORDS = CollectionNames.RECORDS.value
GROUPS = CollectionNames.RECORD_GROUPS.value


class _Body:
    def __init__(self, payload: object, status: int | None = None) -> None:
        self._payload = payload
        self.status = status

    def json(self) -> object:
        return self._payload


class _Response:
    def __init__(self, payload: object = None, status: int = 200) -> None:
        self.success = 200 <= status < 300
        self.status_code = status
        self.error = None if self.success else f"HTTP {status}"
        self.data = _Body(payload, status)


def _title(text: str) -> list[dict[str, Any]]:
    return [{"plain_text": text}]


class FakeNotion:
    """The pages, data sources, databases, blocks and comments one integration token reads."""

    def __init__(self) -> None:
        self.pages: dict[str, dict[str, Any]] = {}
        self.data_sources: dict[str, dict[str, Any]] = {}
        self.databases: dict[str, dict[str, Any]] = {}
        self.blocks: dict[str, list[dict[str, Any]]] = {}
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self.unlisted: set[str] = set()
        self.failures: dict[tuple[str, str], int] = {}

    # ---- building the workspace ----

    def page(self, page_id: str, parent: dict[str, Any] | None = None, edited: str = T1) -> None:
        self.pages[page_id] = {
            "object": "page", "id": page_id, "created_time": T1, "last_edited_time": edited,
            "parent": parent or {"type": "workspace", "workspace": True},
            "url": f"https://notion.so/{page_id}", "in_trash": False,
            "properties": {"title": {"type": "title", "title": _title(f"Page {page_id}")}},
        }
        self.blocks.setdefault(page_id, [])

    def database(self, database_id: str, data_source_id: str, parent_page: str) -> None:
        self.databases[database_id] = {
            "object": "database", "id": database_id, "in_trash": False,
            "parent": {"type": "page_id", "page_id": parent_page},
            "data_sources": [{"id": data_source_id}],
        }
        self.data_sources[data_source_id] = {
            "object": "data_source", "id": data_source_id, "created_time": T1, "last_edited_time": T1,
            "parent": {"type": "database_id", "database_id": database_id},
            "title": _title(f"Table {data_source_id}"), "url": f"https://notion.so/{data_source_id}",
            "in_trash": False,
        }

    def row(self, page_id: str, data_source_id: str) -> None:
        self.page(page_id, {"type": "data_source_id", "data_source_id": data_source_id})

    def attachment(self, page_id: str, block_id: str) -> None:
        self.blocks[page_id].append({
            "object": "block", "id": block_id, "type": "file", "has_children": False,
            "created_time": T1, "last_edited_time": T1,
            "file": {"type": "file", "name": f"{block_id}.pdf",
                     "file": {"url": f"https://files.notion.test/{block_id}.pdf"}},
        })

    def comment_file(self, page_id: str, comment_id: str, name: str) -> str:
        self.comments.setdefault(page_id, []).append({
            "object": "comment", "id": comment_id, "discussion_id": f"d-{comment_id}",
            "created_time": T1, "last_edited_time": T1, "created_by": {"id": "u-1"},
            "rich_text": _title("see attached"),
            "attachments": [{"category": "productivity", "file": {"url": f"https://files.notion.test/{name}"}}],
        })
        return f"ca_{comment_id}_{name}"

    def edit(self, object_id: str) -> None:
        target = self.pages.get(object_id) or self.data_sources[object_id]
        target["last_edited_time"] = T2

    # ---- what is still there ----

    def present(self, external_id: str) -> bool:
        for collection in (self.pages, self.data_sources, self.databases):
            if external_id in collection:
                return not collection[external_id].get("in_trash")
        for page_id, children in self.blocks.items():
            if page_id in self.pages and any(b["id"] == external_id for b in children):
                return True
        for page_id, comments in self.comments.items():
            for comment in comments:
                name = comment["attachments"][0]["file"]["url"].rsplit("/", 1)[-1]
                if page_id in self.pages and external_id == f"ca_{comment['id']}_{name}":
                    return True
        return external_id == WORKSPACE

    # ---- the API the connector calls ----

    def _fails(self, method: str, object_id: str) -> _Response | None:
        status = self.failures.get((method, object_id))
        return _Response(None, status) if status else None

    async def retrieve_bot_user(self) -> _Response:
        return _Response({"object": "user", "id": "bot-1", "type": "bot",
                          "bot": {"workspace_id": WORKSPACE, "workspace_name": "Mine"}})

    async def search(self, request_body: dict[str, Any]) -> _Response:
        kind = request_body["filter"]["value"]
        source = self.pages if kind == "page" else self.data_sources
        results = sorted(
            (o for i, o in source.items() if i not in self.unlisted),
            key=lambda o: o["last_edited_time"], reverse=True,
        )
        return _Response({"object": "list", "results": results, "has_more": False, "next_cursor": None})

    async def retrieve_page(self, page_id: str) -> _Response:
        return self._fails("page", page_id) or (
            _Response(self.pages[page_id]) if page_id in self.pages else _Response(None, 404))

    async def retrieve_data_source_by_id(self, data_source_id: str) -> _Response:
        return self._fails("data_source", data_source_id) or (
            _Response(self.data_sources[data_source_id]) if data_source_id in self.data_sources
            else _Response(None, 404))

    async def retrieve_database(self, database_id: str) -> _Response:
        return self._fails("database", database_id) or (
            _Response(self.databases[database_id]) if database_id in self.databases else _Response(None, 404))

    async def retrieve_block_children(self, block_id: str, start_cursor: str | None = None,
                                      page_size: int = 100) -> _Response:
        return self._fails("blocks", block_id) or _Response(
            {"object": "list", "results": self.blocks.get(block_id, []), "has_more": False, "next_cursor": None})

    async def retrieve_comments(self, block_id: str, start_cursor: str | None = None,
                                page_size: int = 100) -> _Response:
        return self._fails("comments", block_id) or _Response(
            {"object": "list", "results": self.comments.get(block_id, []), "has_more": False, "next_cursor": None})


class FakeStore(DataSourceEntitiesProcessor):
    """The processor's own full-sync bookkeeping over an in-memory graph of records and record groups."""

    def __init__(self) -> None:
        self.org_id = "org-1"
        self._full_sync_runs: dict[str, FullSyncSeen] = {}
        self.records: dict[str, Record] = {}
        self.groups: dict[str, RecordGroup] = {}
        self.deleted: list[Record] = []

    def by_id(self) -> dict[str, Record]:
        return {r.id: r for r in self.records.values()}

    async def get_record_by_external_id(self, connector_id: str, external_record_id: str) -> Record | None:
        return self.records.get(external_record_id)

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str,
                                    record_type: object = None, visibility: object = None) -> list[Record]:
        return [r for r in self.records.values() if r.parent_external_record_id == parent_external_record_id]

    async def get_records_by_record_type(self, connector_id: str, record_type: RecordType) -> list[Record]:
        return [r for r in self.records.values() if r.record_type == record_type]

    async def get_placeholder_records(self, connector_id: str) -> list[Record]:
        return [r for r in self.records.values() if r.is_placeholder]

    async def on_new_app_users(self, users: list[Any]) -> None:
        return None

    async def on_new_record_groups(self, groups: list[tuple[RecordGroup, list[Any]]]) -> None:
        for group, _ in groups:
            stored = self.groups.get(group.external_group_id)
            if stored is not None:
                group.id = stored.id
            self.groups[group.external_group_id] = group
            self._note_full_sync_seen(group.connector_id, GROUPS, group.id)

    async def on_new_records(self, rows: list[tuple[Record, list[Any]]]) -> None:
        for record, _ in rows:
            self._write(record)

    async def on_record_content_update(self, record: Record, permissions: list[Any] | None = None) -> None:
        self._write(record)

    async def on_record_deleted(self, record_id: str) -> None:
        for ext, record in list(self.records.items()):
            if record.id == record_id:
                self.deleted.append(self.records.pop(ext))

    async def on_records_detached_from_parent(self, record_ids: list[str]) -> None:
        for record in self.records.values():
            if record.id in record_ids:
                record.parent_external_record_id = None

    async def reindex_existing_records(self, records: list[Record]) -> None:
        return None

    def _write(self, record: Record) -> None:
        stored = self.records.get(record.external_record_id)
        if stored is not None:
            if record.is_placeholder and not stored.is_placeholder:
                return
            record.id = stored.id
        self.records[record.external_record_id] = record
        group = self.groups.get(record.external_record_group_id or "")
        self._note_full_sync_seen(record.connector_id, RECORDS, record.id)
        self._note_full_sync_seen(record.connector_id, GROUPS, group.id if group else None)
        parent_id = record.parent_external_record_id
        if not parent_id:
            return
        parent = self.records.get(parent_id)
        if parent is None and record.parent_record_type:
            parent = WebpageRecord(
                id=new_record_id(record.connector_id, parent_id), org_id=self.org_id, record_name=parent_id,
                record_type=record.parent_record_type, external_record_id=parent_id,
                external_record_group_id=record.external_record_group_id, version=0,
                connector_name=record.connector_name, connector_id=record.connector_id,
                mime_type=record.mime_type, is_placeholder=True, weburl="", origin=OriginTypes.CONNECTOR,
            )
            self.records[parent_id] = parent
        if parent is not None and parent.is_placeholder:
            self._note_full_sync_seen(record.connector_id, RECORDS, parent.id)


class FakeSyncPoints:
    def __init__(self) -> None:
        self.points: dict[str, dict[str, Any]] = {}

    async def read_sync_point(self, key: str) -> dict[str, Any] | None:
        return self.points.get(key)

    async def update_sync_point(self, key: str, data: dict[str, Any]) -> None:
        self.points[key] = {**self.points.get(key, {}), **data}


class FakeTx:
    def __init__(self, store: FakeStore) -> None:
        self.store = store

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> RecordGroup | None:
        return self.store.groups.get(external_id)


def _data_store(store: FakeStore) -> MagicMock:
    provider = MagicMock()

    @asynccontextmanager
    async def transaction() -> AsyncIterator[FakeTx]:
        yield FakeTx(store)

    provider.transaction = transaction
    return provider


def _connector(notion: FakeNotion, store: FakeStore) -> NotionPersonalConnector:
    conn = NotionPersonalConnector(
        logging.getLogger("notion-personal-full-sync"), store, _data_store(store), AsyncMock(),
        CONNECTOR_ID, "personal", "user-1",
    )
    conn.creator_email = "me@example.com"
    conn._get_fresh_datasource = AsyncMock(return_value=notion)
    conn._assert_required_capabilities = AsyncMock()
    conn.pages_sync_point = FakeSyncPoints()
    conn.notify = AsyncMock()
    return conn


async def _sync(conn: NotionPersonalConnector) -> None:
    with patch("app.connectors.sources.notion_personal.connector.load_connector_filters",
               AsyncMock(return_value=(FilterCollection(), FilterCollection()))):
        await conn.run_sync()


async def _full_sync(conn: NotionPersonalConnector, store: FakeStore) -> set[str]:
    """A full sync as run_sync_task runs one; returns the ids of the nodes whose links it removes."""
    conn.pages_sync_point.points.clear()
    sync_runner._INCOMPLETE_FULL_SYNC_RETRYING.discard(CONNECTOR_ID)
    _, seen = sync_runner._begin_full_sync_run(conn, CONNECTOR_ID)
    assert seen is not None
    unlinked: set[str] = set()

    async def unlink(connector_id: str, by_collection: dict[str, set[str]], **_: object) -> tuple[int, bool]:
        collected = {"record_keys": [r.id for r in store.records.values()],
                     "record_group_keys": [g.id for g in store.groups.values()]}
        unseen = unseen_node_keys(collected, by_collection)
        unlinked.update(unseen[RECORDS] + unseen[GROUPS])
        return 0, True

    graph = MagicMock()
    graph.unlink_nodes_missed_by_full_sync = AsyncMock(side_effect=unlink)
    try:
        await _sync(conn)
        await sync_runner._unlink_what_full_sync_missed(
            conn, CONNECTOR_ID, graph, logging.getLogger("notion-personal-full-sync"), seen)
    finally:
        store.end_full_sync_run(CONNECTOR_ID, seen)
        sync_runner._INCOMPLETE_FULL_SYNC_RETRYING.discard(CONNECTOR_ID)
    return unlinked


def _removed(store: FakeStore, notion: FakeNotion, before: dict[str, Record], unlinked: set[str]
             ) -> tuple[list[str], list[str]]:
    """(wrongly removed, really gone and removed): stored before, not placeholders, by external id."""
    wrongly, gone = [], []
    deleted = {r.id for r in store.deleted}
    for record in before.values():
        if record.is_placeholder or (record.id not in unlinked and record.id not in deleted):
            continue
        (wrongly if notion.present(record.external_record_id) else gone).append(record.external_record_id)
    for group in store.groups.values():
        if group.id in unlinked:
            (wrongly if notion.present(group.external_group_id) else gone).append(group.external_group_id)
    return sorted(wrongly), sorted(gone)


def _workspace() -> FakeNotion:
    """Home (with a file and a comment file), a table under it with two rows, and Notes."""
    notion = FakeNotion()
    notion.page("home")
    notion.attachment("home", "blk-home")
    notion.comment_file("home", "c-home", "home.pdf")
    notion.database("db-1", "ds-1", "home")
    notion.row("row-1", "ds-1")
    notion.row("row-2", "ds-1")
    notion.page("notes")
    notion.page("gone-page")
    return notion


def _store_legacy_container(conn: NotionPersonalConnector, store: FakeStore, before: dict[str, Record]) -> None:
    """A DATABASE container for db-1, as stored before data sources existed."""
    legacy = WebpageRecord(
        id=new_record_id(CONNECTOR_ID, "db-1"), org_id="org-1", record_name="Table",
        record_type=RecordType.DATABASE, external_record_id="db-1", external_record_group_id=WORKSPACE,
        version=0, connector_name=conn.connector_name, connector_id=CONNECTOR_ID,
        mime_type="text/html", weburl="https://notion.so/db-1", origin=OriginTypes.CONNECTOR,
        parent_external_record_id="home", parent_record_type=RecordType.WEBPAGE,
    )
    store.records["db-1"] = legacy
    before["db-1"] = legacy


async def _first_sync(notion: FakeNotion) -> tuple[NotionPersonalConnector, FakeStore, dict[str, Record]]:
    store = FakeStore()
    conn = _connector(notion, store)
    await _sync(conn)
    assert {"home", "ds-1", "row-1", "row-2", "notes", "blk-home", "ca_c-home_home.pdf"} <= set(store.records)
    assert not any(r.is_placeholder for r in store.records.values())
    return conn, store, dict(store.records)


class TestReallyGoneFromNotion:
    @pytest.mark.asyncio
    async def test_trashed_and_deleted_pages_and_a_removed_file_lose_their_links(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        notion.pages["notes"]["in_trash"] = True
        del notion.pages["gone-page"]
        notion.blocks["home"].clear()
        notion.edit("home")
        unlinked = await _full_sync(conn, store)

        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["blk-home", "gone-page", "notes"]
        assert wrongly == []


class TestFailedReadsKeepWhatIsStored:
    @pytest.mark.asyncio
    async def test_a_page_whose_blocks_cannot_be_read_unlinks_nothing(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        notion.edit("home")
        notion.failures[("blocks", "home")] = 500
        del notion.pages["gone-page"]
        unlinked = await _full_sync(conn, store)

        assert conn.full_sync_coverage().complete is False
        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["gone-page"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_data_source_whose_database_read_fails_unlinks_nothing(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        notion.failures[("database", "db-1")] = 503
        del notion.pages["gone-page"]
        unlinked = await _full_sync(conn, store)

        assert conn.full_sync_coverage().complete is False
        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["gone-page"]
        assert wrongly == []


class TestStoredButNotRewritten:
    @pytest.mark.asyncio
    async def test_the_files_of_an_unchanged_page_stay_linked(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        del notion.pages["gone-page"]
        unlinked = await _full_sync(conn, store)

        assert conn.full_sync_coverage().complete is True
        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["gone-page"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_page_search_does_not_list_yet_stays_linked(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        notion.unlisted.add("notes")
        del notion.pages["gone-page"]
        unlinked = await _full_sync(conn, store)

        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["gone-page"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_listed_data_source_whose_database_answers_404_stays_linked(self) -> None:
        """Search lists ds-1 and it reads by id, but retrieve_database answers 404 (an unshared
        database reads the same way): the stored data source is noted kept, so it and the rows that
        inherit through it stay linked."""
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        notion.failures[("database", "db-1")] = 404
        del notion.pages["gone-page"]
        unlinked = await _full_sync(conn, store)

        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["gone-page"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_listed_data_source_whose_database_answers_404_keeps_the_run_complete(self) -> None:
        notion = _workspace()
        conn, store, _ = await _first_sync(notion)

        notion.failures[("database", "db-1")] = 404
        await _full_sync(conn, store)

        assert conn.full_sync_coverage().complete is True

    @pytest.mark.asyncio
    async def test_a_legacy_database_container_whose_retire_fails_stays_linked(self) -> None:
        """A DATABASE container from before data sources: a 429 on its database read in the leftover
        pass leaves it for the next retire, noted kept, so it stays linked and the run stays complete."""
        notion = _workspace()
        conn, store, before = await _first_sync(notion)
        _store_legacy_container(conn, store, before)

        notion.failures[("database", "db-1")] = 429
        del notion.pages["gone-page"]
        # The data source walk resolves db-1 as well; only the leftover pass should meet the 429.
        parent_ref = AsyncMock(return_value=("home", RecordType.WEBPAGE))
        with patch.object(type(conn), "_get_database_parent_ref", parent_ref):
            unlinked = await _full_sync(conn, store)

        assert conn.full_sync_coverage().complete is True
        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["gone-page"]
        assert wrongly == []


class TestDataSourcesReallyGone:
    @pytest.mark.asyncio
    async def test_a_deleted_data_source_and_its_rows_lose_their_links(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        for page_id in ("row-1", "row-2", "gone-page"):
            del notion.pages[page_id]
        del notion.data_sources["ds-1"]
        del notion.databases["db-1"]
        unlinked = await _full_sync(conn, store)

        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["ds-1", "gone-page", "row-1", "row-2"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_trashed_data_source_loses_its_links(self) -> None:
        notion = _workspace()
        conn, store, before = await _first_sync(notion)

        notion.data_sources["ds-1"]["in_trash"] = True
        notion.databases["db-1"]["in_trash"] = True
        unlinked = await _full_sync(conn, store)

        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["ds-1"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_legacy_database_container_whose_database_is_gone_is_retired(self) -> None:
        """The database answers 404 while search still lists its data source: the container is
        retired, and the data source stays linked."""
        notion = _workspace()
        conn, store, before = await _first_sync(notion)
        _store_legacy_container(conn, store, before)

        del notion.databases["db-1"]
        del notion.pages["gone-page"]
        unlinked = await _full_sync(conn, store)

        assert "db-1" in {r.external_record_id for r in store.deleted}
        assert conn.full_sync_coverage().complete is True
        wrongly, gone = _removed(store, notion, before, unlinked)
        assert gone == ["db-1", "gone-page"]
        assert wrongly == []

    @pytest.mark.asyncio
    async def test_a_listed_data_source_whose_database_is_archived_loses_its_links(self) -> None:
        """Notion says the database is in the trash, so its data source is gone even while search
        still lists it (FakeNotion would count the listed data source as present)."""
        notion = _workspace()
        conn, store, _ = await _first_sync(notion)

        notion.databases["db-1"]["in_trash"] = True
        unlinked = await _full_sync(conn, store)

        assert store.records["ds-1"].id in unlinked
        assert conn.full_sync_coverage().complete is True
