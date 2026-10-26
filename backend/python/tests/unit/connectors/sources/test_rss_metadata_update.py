"""An RSS entry's new title or link reaches its record on the next sync.

The fake store follows the real one where it matters here:
``get_record_by_external_id`` answers a plain ``Record`` (never a ``FileRecord``),
and ``on_new_records`` rewrites an existing record only when its revision changes.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.rss.connector import RSSConnector
from app.models.entities import FileRecord, Record
from app.models.permission import Permission

FEED = "https://blog.example.com/rss"


class FakeRecordStore:
    """The slice of DataSourceEntitiesProcessor the RSS sync uses, over an in-memory graph."""

    org_id = "org-1"

    def __init__(self) -> None:
        self.by_external_id: dict[str, FileRecord] = {}
        self.metadata_updates: list[str] = []
        self.content_writes: list[str] = []

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Record | None:
        stored = self.by_external_id.get(external_id)
        if stored is None:
            return None
        return Record(**{k: v for k, v in stored.model_dump().items() if k in Record.model_fields})

    async def get_file_record_by_id(self, record_id: str) -> FileRecord | None:
        return next((r for r in self.by_external_id.values() if r.id == record_id), None)

    async def on_new_records(self, batch: list[tuple[FileRecord, list[Permission]]]) -> None:
        for record, _ in batch:
            existing = self.by_external_id.get(record.external_record_id)
            if existing is not None:
                record.id = existing.id
                if existing.external_revision_id == record.external_revision_id:
                    continue
                self.content_writes.append(record.external_record_id)
            self.by_external_id[record.external_record_id] = record.model_copy()

    async def on_record_metadata_update(self, record: FileRecord) -> None:
        existing = self.by_external_id[record.external_record_id]
        self.metadata_updates.append(record.external_record_id)
        self.by_external_id[record.external_record_id] = record.model_copy(
            update={"id": existing.id, "indexing_status": existing.indexing_status}
        )

    def name_of(self, external_id: str) -> str:
        return self.by_external_id[external_id].record_name


def _connector(store: FakeRecordStore) -> RSSConnector:
    conn = RSSConnector(
        logger=MagicMock(),
        data_entities_processor=store,
        data_store_provider=MagicMock(),
        config_service=AsyncMock(),
        connector_id="rss-conn-1",
        scope="team",
        created_by="user-1",
    )
    conn.fetch_full_content = False
    conn.session = MagicMock()
    return conn


def _feed(*entries: dict) -> MagicMock:
    feed = MagicMock()
    feed.entries = list(entries)
    feed.feed = {"title": "Blog"}
    return feed


def _entry(title: str, summary: str = "The same body.", guid: str = "urn:post-1",
           link: str = "https://blog.example.com/post-1") -> dict:
    return {"title": title, "link": link, "id": guid, "summary": summary}


async def _sync(conn: RSSConnector, *entries: dict) -> None:
    conn.processed_urls.clear()
    with patch.object(conn, "create_record_group", new_callable=AsyncMock), \
         patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock,
                      return_value=_feed(*entries)):
        await conn._process_feed(FEED, [])


class TestRetitledEntry:
    @pytest.mark.asyncio
    async def test_a_new_title_on_the_same_text_renames_the_record(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Original title"))
        record_id = store.by_external_id["urn:post-1"].id

        await _sync(conn, _entry("Retitled"))

        assert store.name_of("urn:post-1") == "Retitled"
        assert store.by_external_id["urn:post-1"].id == record_id
        assert store.metadata_updates == ["urn:post-1"]
        # Same text, so nothing is re-indexed.
        assert store.content_writes == []

    @pytest.mark.asyncio
    async def test_a_new_link_on_the_same_text_updates_the_record(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Post"))

        await _sync(conn, _entry("Post", link="https://blog.example.com/posts/post-1"))

        assert store.by_external_id["urn:post-1"].weburl == "https://blog.example.com/posts/post-1"
        assert store.metadata_updates == ["urn:post-1"]

    @pytest.mark.asyncio
    async def test_an_unchanged_entry_writes_nothing(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Post"))

        await _sync(conn, _entry("Post"))

        assert store.metadata_updates == []
        assert store.content_writes == []

    @pytest.mark.asyncio
    async def test_new_text_and_title_go_through_the_content_update_alone(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Post"))

        await _sync(conn, _entry("Post, revised", summary="A different body."))

        assert store.name_of("urn:post-1") == "Post, revised"
        assert store.content_writes == ["urn:post-1"]
        assert store.metadata_updates == []

    @pytest.mark.asyncio
    async def test_a_failed_title_update_does_not_stop_the_feed(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("A", guid="urn:a", link="https://blog.example.com/a"),
                    _entry("B", guid="urn:b", link="https://blog.example.com/b"))
        real_update = store.on_record_metadata_update

        async def fail_first(record: FileRecord) -> None:
            if record.external_record_id == "urn:a":
                raise RuntimeError("graph unavailable")
            await real_update(record)

        store.on_record_metadata_update = fail_first  # type: ignore[method-assign]

        await _sync(conn, _entry("A2", guid="urn:a", link="https://blog.example.com/a"),
                    _entry("B2", guid="urn:b", link="https://blog.example.com/b"))

        assert store.name_of("urn:a") == "A"
        assert store.name_of("urn:b") == "B2"

    @pytest.mark.asyncio
    async def test_a_failed_read_of_the_stored_record_changes_nothing(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Post"))
        store.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("graph down"))  # type: ignore[method-assign]

        await _sync(conn, _entry("Retitled"))

        assert store.name_of("urn:post-1") == "Post"
        assert store.metadata_updates == []

    @pytest.mark.asyncio
    async def test_a_failed_title_update_is_retried_by_the_next_sync(self) -> None:
        # RSS keeps no sync checkpoint: every sync re-reads the feed and compares it
        # with the stored record, so an update that failed is found again next time.
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Post"))
        real_update = store.on_record_metadata_update
        store.on_record_metadata_update = AsyncMock(side_effect=RuntimeError("graph down"))  # type: ignore[method-assign]

        await _sync(conn, _entry("Retitled"))
        assert store.name_of("urn:post-1") == "Post"

        store.on_record_metadata_update = real_update  # type: ignore[method-assign]
        await _sync(conn, _entry("Retitled"))

        assert store.name_of("urn:post-1") == "Retitled"
        assert store.metadata_updates == ["urn:post-1"]

    @pytest.mark.asyncio
    async def test_a_failed_lookup_is_retried_by_the_next_sync(self) -> None:
        store = FakeRecordStore()
        conn = _connector(store)
        await _sync(conn, _entry("Post"))
        real_lookup = store.get_record_by_external_id
        store.get_record_by_external_id = AsyncMock(side_effect=RuntimeError("graph down"))  # type: ignore[method-assign]

        await _sync(conn, _entry("Retitled"))
        assert store.name_of("urn:post-1") == "Post"

        store.get_record_by_external_id = real_lookup  # type: ignore[method-assign]
        await _sync(conn, _entry("Retitled"))

        assert store.name_of("urn:post-1") == "Retitled"
