"""RSS sync behaviour from the connector checklist audit.

Covers what a feed sync keeps, removes and reports: entry ids that stay apart
across feeds, a stored article kept while its page is down, failed feeds named
in a notification without losing their records, feeds taken out of the
settings removed, and the settings read back on every sync.

The fake store follows the real one where it matters here:
``get_record_by_external_id`` answers a plain ``Record`` and ``on_new_records``
keeps an existing record's id.
"""

import hashlib
import time
from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.sync.full_sync_coverage import FullSyncCoverage
from app.connectors.sources.rss.connector import (
    MAX_ARTICLES_PER_FEED_LIMIT,
    MAX_FETCH_SIZE_MB,
    FeedUnreadableError,
    RSSConnector,
)
from app.connectors.sources.web.fetch_strategy import FetchResponse
from app.models.entities import FileRecord, Record, RecordGroupType
from app.models.permission import Permission
from app.services.notification.types import NotificationSeverity, NotificationType

FEED_A = "https://a.example.com/rss"
FEED_B = "https://b.example.com/rss"
FETCH = "app.connectors.sources.rss.connector.fetch_url_with_fallback"


class FakeStore:
    """The slice of DataSourceEntitiesProcessor the RSS sync uses, over an in-memory graph."""

    org_id = "org-1"

    def __init__(self) -> None:
        self.records: dict[str, FileRecord] = {}
        self.groups: dict[str, str] = {}
        self.content_writes: list[str] = []
        self.deleted_groups: list[str] = []

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Record | None:
        stored = self.records.get(external_id)
        if stored is None:
            return None
        return Record(**{k: v for k, v in stored.model_dump().items() if k in Record.model_fields})

    async def on_new_records(self, batch: list[tuple[FileRecord, list[Permission]]]) -> None:
        for record, _ in batch:
            existing = self.records.get(record.external_record_id)
            if existing is not None:
                record.id = existing.id
                if existing.external_revision_id == record.external_revision_id:
                    continue
            self.content_writes.append(record.external_record_id)
            self.records[record.external_record_id] = record.model_copy()

    async def on_record_metadata_update(self, record: FileRecord) -> None:
        self.records[record.external_record_id] = record.model_copy()

    async def on_new_record_groups(self, groups: list) -> None:
        for group, _ in groups:
            self.groups[group.external_group_id] = group.name

    async def get_nodes_by_filters(
        self, collection: str, filters: dict, return_fields: list[str] | None = None, *, raise_on_error: bool = False
    ) -> list[dict]:
        assert collection == CollectionNames.RECORD_GROUPS.value
        assert filters["groupType"] == RecordGroupType.RSS_FEED.value
        return [{"externalGroupId": url} for url in self.groups]

    async def on_record_group_deleted(self, external_group_id: str, connector_id: str) -> bool:
        self.deleted_groups.append(external_group_id)
        self.groups.pop(external_group_id, None)
        for key in [k for k, r in self.records.items() if r.external_record_group_id == external_group_id]:
            del self.records[key]
        return True

    async def ensure_team_app_edge(self, connector_id: str) -> None:
        return None


def _connector(store: FakeStore, feeds: list[str], **sync: object) -> RSSConnector:
    conn = RSSConnector(
        logger=MagicMock(),
        data_entities_processor=store,
        data_store_provider=MagicMock(),
        config_service=AsyncMock(),
        connector_id="rss-conn-1",
        scope="team",
        created_by="user-1",
    )
    conn.config_service.get_config = AsyncMock(return_value={"sync": {
        "feed_urls": "\n".join(feeds), "fetch_full_content": False, **sync,
    }})
    conn.feed_urls = list(feeds)
    conn.fetch_full_content = bool(sync.get("fetch_full_content", False))
    conn.session = MagicMock()
    conn.notify = AsyncMock()
    return conn


def _entry(guid: str, title: str = "Post", link: str = "https://a.example.com/post",
           summary: str = "Feed summary.") -> dict:
    return {"id": guid, "title": title, "link": link, "summary": summary}


def _feed(*entries: dict, title: str = "Blog") -> MagicMock:
    feed = MagicMock()
    feed.entries = list(entries)
    feed.feed = {"title": title}
    return feed


def _feeds(by_url: dict[str, MagicMock | None]) -> Callable[..., Awaitable[MagicMock | None]]:
    """Answer _fetch_and_parse_feed per URL; None is a feed that could not be read."""

    async def fetch(
        feed_url: str, *, raise_on_error: bool = False, problems: list[str] | None = None
    ) -> MagicMock | None:
        feed = by_url[feed_url]
        if feed is None and problems is not None:
            problems.append("HTTP 503")
        return feed

    return fetch


async def _sync(conn: RSSConnector, by_url: dict[str, MagicMock | None]) -> None:
    with patch.object(conn, "_fetch_and_parse_feed", side_effect=_feeds(by_url)):
        await conn.run_sync()


def _html(text: str) -> FetchResponse:
    return FetchResponse(
        status_code=200, content_bytes=f"<html><body><article><p>{text}</p></article></body></html>".encode(),
        headers={"Content-Type": "text/html"}, final_url="https://a.example.com/post", strategy="aiohttp",
    )


def _failed(status: int) -> FetchResponse:
    return FetchResponse(status_code=status, content_bytes=b"", headers={}, final_url="", strategy="aiohttp")


class TestEntryIds:
    @pytest.mark.asyncio
    async def test_the_same_short_guid_in_two_feeds_makes_two_records(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])

        await _sync(conn, {
            FEED_A: _feed(_entry("1", "A one", "https://a.example.com/1", "Body of A.")),
            FEED_B: _feed(_entry("1", "B one", "https://b.example.com/1", "Body of B.")),
        })

        assert set(store.records) == {f"{FEED_A}#1", f"{FEED_B}#1"}
        assert store.records[f"{FEED_A}#1"].record_name == "A one"
        assert store.records[f"{FEED_B}#1"].record_name == "B one"

    @pytest.mark.asyncio
    async def test_url_and_urn_guids_are_kept_as_they_are(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])

        await _sync(conn, {FEED_A: _feed(
            _entry("https://a.example.com/?p=1", link="https://a.example.com/1"),
            _entry("urn:uuid:42", link="https://a.example.com/2"),
            _entry("tag:a.example.com,2025:3", link="https://a.example.com/3"),
        )})

        assert set(store.records) == {"https://a.example.com/?p=1", "urn:uuid:42", "tag:a.example.com,2025:3"}

    @pytest.mark.asyncio
    async def test_a_record_stored_under_the_bare_guid_keeps_its_id(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        await _sync(conn, {FEED_A: _feed(_entry("1"))})
        # As synced before ids carried the feed: the same record, under the bare guid.
        store.records["1"] = store.records.pop(f"{FEED_A}#1").model_copy(update={"external_record_id": "1"})
        store.content_writes.clear()

        await _sync(conn, {FEED_A: _feed(_entry("1", summary="Edited body."))})

        assert set(store.records) == {"1"}
        assert store.content_writes == ["1"]

    @pytest.mark.asyncio
    async def test_a_bare_guid_record_of_another_feed_is_not_taken_over(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])
        await _sync(conn, {FEED_A: _feed(_entry("1")), FEED_B: _feed()})
        store.records["1"] = store.records.pop(f"{FEED_A}#1").model_copy(update={"external_record_id": "1"})

        await _sync(conn, {FEED_A: _feed(_entry("1")),
                           FEED_B: _feed(_entry("1", "B one", "https://b.example.com/1", "Body of B."))})

        assert store.records["1"].external_record_group_id == FEED_A
        assert store.records[f"{FEED_B}#1"].record_name == "B one"

    @pytest.mark.asyncio
    async def test_stream_finds_the_entry_by_its_prefixed_or_bare_id(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        feed = _feed(_entry("1", summary="The entry text."))
        for external_id in (f"{FEED_A}#1", "1"):
            record = FileRecord(
                record_name="Post", external_record_id=external_id, external_record_group_id=FEED_A,
                weburl="https://a.example.com/moved", origin="CONNECTOR", connector_name="RSS",
                connector_id="rss-conn-1", record_type="FILE", version=0, is_file=True,
                mime_type="text/plain",
            )
            with patch.object(conn, "_fetch_and_parse_feed", new=AsyncMock(return_value=feed)):
                response = await conn.stream_record(record)
            body = b"".join([chunk async for chunk in response.body_iterator])
            assert body == b"The entry text."


class TestArticlePageDown:
    @pytest.mark.asyncio
    async def test_a_synced_article_keeps_its_text_while_its_page_fails(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A], fetch_full_content=True)
        with patch(FETCH, new=AsyncMock(return_value=_html("The full article."))):
            await _sync(conn, {FEED_A: _feed(_entry("1"))})
        kept = store.records[f"{FEED_A}#1"]
        store.content_writes.clear()

        with patch(FETCH, new=AsyncMock(return_value=_failed(503))):
            await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert store.content_writes == []
        assert store.records[f"{FEED_A}#1"].external_revision_id == kept.external_revision_id

    @pytest.mark.asyncio
    async def test_a_new_article_whose_page_fails_is_synced_from_its_summary(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A], fetch_full_content=True)

        with patch(FETCH, new=AsyncMock(return_value=_failed(503))):
            await _sync(conn, {FEED_A: _feed(_entry("1", summary="Only the summary."))})

        record = store.records[f"{FEED_A}#1"]
        assert record.external_revision_id == hashlib.md5(b"Only the summary.").hexdigest()

    @pytest.mark.asyncio
    async def test_streaming_serves_the_summary_only_if_the_sync_stored_it(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A], fetch_full_content=True)
        feed = _feed(_entry("1", summary="Only the summary."))

        def record_with(revision: str) -> FileRecord:
            return FileRecord(
                record_name="Post", external_record_id=f"{FEED_A}#1", external_record_group_id=FEED_A,
                weburl="https://a.example.com/post", origin="CONNECTOR", connector_name="RSS",
                connector_id="rss-conn-1", record_type="FILE", version=0, is_file=True,
                mime_type="text/plain", external_revision_id=revision,
            )

        with patch.object(conn, "_fetch_and_parse_feed", new=AsyncMock(return_value=feed)), \
             patch(FETCH, new=AsyncMock(return_value=_failed(503))):
            summary_record = record_with(hashlib.md5(b"Only the summary.").hexdigest())
            response = await conn.stream_record(summary_record)
            assert b"".join([c async for c in response.body_iterator]) == b"Only the summary."

            # The sync stored the article's text: the summary would be indexed under its revision.
            with pytest.raises(HTTPException) as raised:
                await conn.stream_record(record_with(hashlib.md5(b"The full article.").hexdigest()))
            assert raised.value.status_code >= 500


class TestFailedFeeds:
    @pytest.mark.asyncio
    async def test_a_failed_feed_is_named_in_a_warning_and_the_others_sync(self) -> None:
        store = FakeStore()
        token_feed = "https://b.example.com/rss?token=s3cret"
        conn = _connector(store, [FEED_A, token_feed])

        await _sync(conn, {FEED_A: _feed(_entry("1")), token_feed: None})

        assert f"{FEED_A}#1" in store.records
        conn.notify.assert_awaited_once()
        sent = conn.notify.await_args.kwargs
        assert sent["type"] == NotificationType.CONNECTOR_RECORD_GROUP_SYNC_ERROR
        assert sent["severity"] == NotificationSeverity.WARNING
        assert "1 of 2 feed(s)" in sent["message"]
        assert "https://b.example.com/rss (HTTP 503)" in sent["message"]
        assert "s3cret" not in sent["message"]

    @pytest.mark.asyncio
    async def test_a_failed_feed_keeps_its_records(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])
        await _sync(conn, {FEED_A: _feed(_entry("1")), FEED_B: _feed(_entry("2", link="https://b.example.com/2"))})

        await _sync(conn, {FEED_A: None, FEED_B: _feed()})

        assert f"{FEED_A}#1" in store.records
        assert store.deleted_groups == []

    @pytest.mark.asyncio
    async def test_a_sync_that_reads_no_feed_fails_and_says_so(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])

        with pytest.raises(RuntimeError, match="None of the 2 feed"):
            await _sync(conn, {FEED_A: None, FEED_B: None})

        sent = conn.notify.await_args.kwargs
        assert sent["type"] == NotificationType.CONNECTOR_SYNC_ERROR
        assert sent["severity"] == NotificationSeverity.ERROR

    @pytest.mark.asyncio
    async def test_an_empty_feed_is_not_a_failure(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])

        await _sync(conn, {FEED_A: _feed(title="Quiet blog")})

        conn.notify.assert_not_called()
        assert store.groups == {FEED_A: "Quiet blog"}

    @pytest.mark.asyncio
    async def test_a_failed_notification_does_not_fail_the_sync(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])
        conn.notify = AsyncMock(side_effect=RuntimeError("broker down"))

        await _sync(conn, {FEED_A: _feed(_entry("1")), FEED_B: None})

        assert f"{FEED_A}#1" in store.records

    @pytest.mark.asyncio
    async def test_process_feed_raises_for_an_unreadable_feed(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        with patch.object(conn, "_fetch_and_parse_feed", side_effect=_feeds({FEED_A: None})):
            with pytest.raises(FeedUnreadableError, match="HTTP 503"):
                await conn._process_feed(FEED_A, [])
        assert store.groups == {}


class TestRemovedFeeds:
    @pytest.mark.asyncio
    async def test_a_feed_taken_out_of_the_settings_is_removed_with_its_articles(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])
        await _sync(conn, {FEED_A: _feed(_entry("1")), FEED_B: _feed(_entry("2", link="https://b.example.com/2"))})

        conn.config_service.get_config = AsyncMock(return_value={"sync": {"feed_urls": FEED_A}})
        await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert store.deleted_groups == [FEED_B]
        assert set(store.records) == {f"{FEED_A}#1"}
        assert conn.feed_urls == [FEED_A]

    @pytest.mark.asyncio
    async def test_an_entry_that_leaves_its_feed_is_kept(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        await _sync(conn, {FEED_A: _feed(_entry("1"), _entry("2", link="https://a.example.com/2"))})

        await _sync(conn, {FEED_A: _feed(_entry("2", link="https://a.example.com/2"))})

        assert set(store.records) == {f"{FEED_A}#1", f"{FEED_A}#2"}

    @pytest.mark.asyncio
    async def test_unreadable_stored_groups_delete_nothing(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        store.groups[FEED_B] = "Old feed"
        store.get_nodes_by_filters = AsyncMock(side_effect=RuntimeError("graph down"))  # type: ignore[method-assign]

        await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert store.deleted_groups == []

    @pytest.mark.asyncio
    async def test_a_failed_group_delete_does_not_fail_the_sync(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        store.groups[FEED_B] = "Old feed"
        store.on_record_group_deleted = AsyncMock(side_effect=RuntimeError("graph down"))  # type: ignore[method-assign]

        await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert f"{FEED_A}#1" in store.records

    def test_a_full_sync_never_unlinks_what_it_did_not_see(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        assert conn.full_sync_coverage() == FullSyncCoverage(complete=False)

    @pytest.mark.asyncio
    async def test_each_sync_reads_the_saved_settings(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])

        await _sync(conn, {FEED_A: _feed()})

        conn.config_service.get_config.assert_any_await(
            "/services/connectors/rss-conn-1/config", use_cache=False
        )


class TestSettings:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("saved", "expected"), [
        (None, 50), ("", 50), ("0", 1), (-3, 1), ("20", 20), (10_000, MAX_ARTICLES_PER_FEED_LIMIT),
    ])
    async def test_max_articles_is_kept_in_range(self, saved: object, expected: int) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        conn.config_service.get_config = AsyncMock(return_value={"sync": {
            "feed_urls": FEED_A, "max_articles_per_feed": saved,
        }})
        await conn._load_config()
        assert conn.max_articles_per_feed == expected

    @pytest.mark.asyncio
    async def test_a_max_articles_that_is_not_a_number_is_named(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        conn.config_service.get_config = AsyncMock(return_value={"sync": {
            "feed_urls": FEED_A, "max_articles_per_feed": "lots",
        }})
        with pytest.raises(ConnectorInitError, match="Max Articles Per Feed"):
            await conn._load_config()

    @pytest.mark.asyncio
    async def test_init_shows_a_settings_problem_to_the_user(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        conn.config_service.get_config = AsyncMock(return_value={"sync": {"feed_urls": "ftp://x, not a url"}})
        with pytest.raises(ConnectorInitError, match="No feed URLs"):
            await conn.init()

    def test_feed_urls_saved_as_a_list_are_read(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        assert conn._parse_feed_urls([FEED_A, " ", FEED_B, FEED_A]) == [FEED_A, FEED_B]

    def test_the_article_limit_field_declares_its_range(self) -> None:
        fields = RSSConnector._connector_metadata["config"]["sync"]["customFields"]
        limit = next(f for f in fields if f["name"] == "max_articles_per_feed")
        assert limit["validation"] == {"min": 1, "max": MAX_ARTICLES_PER_FEED_LIMIT}

    def test_the_documentation_link_is_the_rss_page(self) -> None:
        links = RSSConnector._connector_metadata["config"]["documentationLinks"]
        assert any(link["url"].endswith("/connectors/rss/rss") for link in links)


class TestRecordFields:
    @pytest.mark.asyncio
    async def test_updated_time_comes_from_the_entry_update(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        entry = _entry("1")
        entry["published_parsed"] = time.strptime("2025-09-01", "%Y-%m-%d")
        entry["updated_parsed"] = time.strptime("2025-09-03", "%Y-%m-%d")

        await _sync(conn, {FEED_A: _feed(entry)})

        record = store.records[f"{FEED_A}#1"]
        assert record.source_created_at == 1756684800000
        assert record.source_updated_at == 1756857600000

    @pytest.mark.asyncio
    async def test_an_empty_title_falls_back_to_the_link(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])

        await _sync(conn, {FEED_A: _feed(_entry("1", title="", link="https://a.example.com/hello-world"))})

        assert store.records[f"{FEED_A}#1"].record_name == "hello world"


class TestFetchLimitsAndLogs:
    @pytest.mark.asyncio
    async def test_feed_and_article_fetches_are_size_capped(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        fetch = AsyncMock(return_value=_failed(404))
        with patch(FETCH, new=fetch):
            await conn._fetch_and_parse_feed(FEED_A)
            await conn._fetch_article_content("https://a.example.com/post")
        assert [c.kwargs["max_size_mb"] for c in fetch.await_args_list] == [MAX_FETCH_SIZE_MB] * 2

    @pytest.mark.asyncio
    async def test_a_feed_url_token_never_reaches_the_log(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        feed_with_userinfo = "https://user:pw@a.example.com/rss?token=s3cret"
        with patch(FETCH, new=AsyncMock(side_effect=RuntimeError(f"cannot reach {feed_with_userinfo}"))):
            assert await conn._fetch_and_parse_feed(feed_with_userinfo) is None
        with patch(FETCH, new=AsyncMock(return_value=_failed(500))):
            assert await conn._fetch_and_parse_feed(feed_with_userinfo) is None
        logged = " ".join(str(call) for method in ("info", "warning", "error", "debug")
                          for call in getattr(conn.logger, method).call_args_list)
        assert "a.example.com/rss" in logged
        assert "s3cret" not in logged and "pw@" not in logged


class TestReindex:
    @pytest.mark.asyncio
    async def test_reindex_requeues_every_record_it_is_given(self) -> None:
        store = FakeStore()
        store.reindex_existing_records = AsyncMock(return_value=["r1", "r2"])  # type: ignore[attr-defined]
        conn = _connector(store, [FEED_A])
        records = [MagicMock(id="r1"), MagicMock(id="r2")]

        await conn.reindex_records(records)

        store.reindex_existing_records.assert_awaited_once_with(records)  # type: ignore[attr-defined]


MANUAL_INDEXING = {"indexing": {"values": {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}}}}


class TestManualIndexing:
    def test_the_manual_indexing_switch_is_declared(self) -> None:
        filters = RSSConnector._connector_metadata["config"]["filters"]["indexing"]["schema"]["fields"]
        assert [f["name"] for f in filters] == ["enable_manual_sync"]

    @pytest.mark.asyncio
    async def test_with_manual_indexing_on_new_items_are_synced_but_not_indexed(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        conn.config_service.get_config = AsyncMock(return_value={
            "sync": {"feed_urls": FEED_A, "fetch_full_content": False}, "filters": MANUAL_INDEXING,
        })

        await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert store.records[f"{FEED_A}#1"].indexing_status == "AUTO_INDEX_OFF"

    @pytest.mark.asyncio
    async def test_with_manual_indexing_off_items_are_left_to_index(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])

        await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert store.records[f"{FEED_A}#1"].indexing_status != "AUTO_INDEX_OFF"

    @pytest.mark.asyncio
    async def test_turning_manual_indexing_on_applies_from_the_next_sync(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        await _sync(conn, {FEED_A: _feed(_entry("1"))})

        conn.config_service.get_config = AsyncMock(return_value={
            "sync": {"feed_urls": FEED_A, "fetch_full_content": False}, "filters": MANUAL_INDEXING,
        })
        await _sync(conn, {FEED_A: _feed(_entry("1"), _entry("2", link="https://a.example.com/2"))})

        assert store.records[f"{FEED_A}#2"].indexing_status == "AUTO_INDEX_OFF"
        # Unchanged text: the indexed item is not rewritten, so it stays indexed.
        assert store.records[f"{FEED_A}#1"].indexing_status != "AUTO_INDEX_OFF"


def _page(entries: list[dict], next_href: str | None = None) -> bytes:
    items = "".join(
        f"<item><guid>{e['id']}</guid><title>{e['title']}</title><link>{e['link']}</link>"
        f"<description>{e['summary']}</description></item>"
        for e in entries
    )
    nxt = f'<atom:link rel="next" href="{next_href}"/>' if next_href else ""
    return (
        '<?xml version="1.0"?><rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom">'
        f"<channel><title>Paged</title>{nxt}{items}</channel></rss>"
    ).encode()


def _xml(body: bytes, status: int = 200) -> FetchResponse:
    return FetchResponse(status_code=status, content_bytes=body, headers={"Content-Type": "application/rss+xml"},
                         final_url="", strategy="aiohttp")


def _entries(*numbers: int) -> list[dict]:
    return [_entry(f"urn:p:{n}", f"Post {n}", f"https://a.example.com/{n}") for n in numbers]


class TestPagedFeeds:
    @pytest.mark.asyncio
    async def test_next_pages_are_read_up_to_the_article_limit(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        conn.max_articles_per_feed = 5
        pages = {
            FEED_A: _xml(_page(_entries(1, 2), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page(_entries(3, 4), "/page3.xml")),
            "https://a.example.com/page3.xml": _xml(_page(_entries(5, 6), "page4.xml")),
        }
        fetch = AsyncMock(side_effect=lambda url, **_: pages[url])
        with patch(FETCH, new=fetch):
            feed = await conn._fetch_and_parse_feed(FEED_A)
        assert [e["id"] for e in feed.entries] == [f"urn:p:{n}" for n in range(1, 7)]
        # The limit was reached on the third page, so the fourth was never asked for.
        assert [c.kwargs["url"] for c in fetch.await_args_list] == list(pages)

    @pytest.mark.asyncio
    async def test_a_page_that_links_back_ends_the_walk(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        pages = {
            FEED_A: _xml(_page(_entries(1), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page(_entries(2), FEED_A)),
        }
        fetch = AsyncMock(side_effect=lambda url, **_: pages[url])
        with patch(FETCH, new=fetch):
            feed = await conn._fetch_and_parse_feed(FEED_A)
        assert len(feed.entries) == 2
        assert fetch.await_count == 2

    @pytest.mark.asyncio
    async def test_an_empty_page_in_the_middle_does_not_end_the_walk(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        pages = {
            FEED_A: _xml(_page(_entries(1), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page([], "page3.xml")),
            "https://a.example.com/page3.xml": _xml(_page(_entries(3))),
        }
        with patch(FETCH, new=AsyncMock(side_effect=lambda url, **_: pages[url])):
            feed = await conn._fetch_and_parse_feed(FEED_A)
        assert [e["id"] for e in feed.entries] == ["urn:p:1", "urn:p:3"]

    @pytest.mark.asyncio
    async def test_a_failing_page_keeps_what_was_read(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        pages = {
            FEED_A: _xml(_page(_entries(1, 2), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(b"", status=503),
        }
        with patch(FETCH, new=AsyncMock(side_effect=lambda url, **_: pages[url])):
            feed = await conn._fetch_and_parse_feed(FEED_A)
        assert [e["id"] for e in feed.entries] == ["urn:p:1", "urn:p:2"]

    @pytest.mark.asyncio
    async def test_the_sync_takes_entries_from_every_page(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A])
        pages = {
            FEED_A: _xml(_page(_entries(1), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page(_entries(2))),
        }
        with patch(FETCH, new=AsyncMock(side_effect=lambda url, **_: pages[url])):
            await conn.run_sync()
        assert set(store.records) == {"urn:p:1", "urn:p:2"}


class TestReviewFindings:
    @pytest.mark.asyncio
    async def test_well_formed_xml_that_is_not_a_feed_is_refused(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        sitemap = b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><url><loc>https://a.example.com/</loc></url></urlset>'
        problems: list[str] = []
        with patch(FETCH, new=AsyncMock(return_value=_xml(sitemap))):
            assert await conn._fetch_and_parse_feed(FEED_A, problems=problems) is None
        assert problems == ["not an RSS or Atom feed: no RSS or Atom root element"]

    @pytest.mark.asyncio
    async def test_streaming_reads_more_pages_only_when_the_entry_is_not_on_the_first(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        pages = {
            FEED_A: _xml(_page(_entries(1), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page(_entries(2))),
        }
        fetch = AsyncMock(side_effect=lambda url, **_: pages[url])

        def record(external_id: str) -> FileRecord:
            return FileRecord(
                record_name="Post", external_record_id=external_id, external_record_group_id=FEED_A,
                weburl="https://a.example.com/x", origin="CONNECTOR", connector_name="RSS",
                connector_id="rss-conn-1", record_type="FILE", version=0, is_file=True, mime_type="text/plain",
            )

        with patch(FETCH, new=fetch):
            await conn.stream_record(record("urn:p:1"))
            assert fetch.await_count == 1
            response = await conn.stream_record(record("urn:p:2"))
        assert b"".join([c async for c in response.body_iterator]) == b"Feed summary."
        assert fetch.await_count == 3

    @pytest.mark.asyncio
    async def test_a_page_that_yields_no_text_keeps_the_stored_article(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A], fetch_full_content=True)
        with patch(FETCH, new=AsyncMock(return_value=_html("The full article."))):
            await _sync(conn, {FEED_A: _feed(_entry("1"))})
        kept = store.records[f"{FEED_A}#1"].external_revision_id
        store.content_writes.clear()

        script_shell = FetchResponse(status_code=200, content_bytes=b"<html><body><div id='app'></div></body></html>",
                                     headers={"Content-Type": "text/html"}, final_url="", strategy="aiohttp")
        with patch(FETCH, new=AsyncMock(return_value=script_shell)):
            await _sync(conn, {FEED_A: _feed(_entry("1"))})

        assert store.content_writes == []
        assert store.records[f"{FEED_A}#1"].external_revision_id == kept

    @pytest.mark.asyncio
    async def test_a_failed_batch_write_fails_the_feed_once(self) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A, FEED_B])
        conn.batch_size = 2
        writes = AsyncMock(side_effect=RuntimeError("graph down"))
        store.on_new_records = writes  # type: ignore[method-assign]

        await _sync(conn, {
            FEED_A: _feed(*[_entry(str(n), link=f"https://a.example.com/{n}") for n in range(6)]),
            FEED_B: _feed(),
        })

        assert writes.await_count == 1
        sent = conn.notify.await_args.kwargs
        assert "1 of 2 feed(s)" in sent["message"] and "it could not be saved" in sent["message"]

    @pytest.mark.asyncio
    async def test_streaming_finds_an_entry_by_id_when_several_share_its_link(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        show = "https://a.example.com/show"
        episodes = [_entry(f"urn:ep:{n}", f"Episode {n}", show, f"Notes for episode {n}.") for n in (1, 2, 3)]
        pages = {
            FEED_A: _xml(_page(episodes[:2], "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page(episodes[2:])),
        }

        def record(external_id: str) -> FileRecord:
            return FileRecord(
                record_name="Episode", external_record_id=external_id, external_record_group_id=FEED_A,
                weburl=show, origin="CONNECTOR", connector_name="RSS", connector_id="rss-conn-1",
                record_type="FILE", version=0, is_file=True, mime_type="text/plain",
            )

        with patch(FETCH, new=AsyncMock(side_effect=lambda url, **_: pages[url])):
            for n in (2, 3):
                response = await conn.stream_record(record(f"urn:ep:{n}"))
                assert b"".join([c async for c in response.body_iterator]) == f"Notes for episode {n}.".encode()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("headers", [{"Content-Type": "application/pdf"}, {}])
    async def test_a_page_that_is_not_html_keeps_the_stored_article(self, headers: dict[str, str]) -> None:
        store = FakeStore()
        conn = _connector(store, [FEED_A], fetch_full_content=True)
        feed = _feed(_entry("1", summary="Only the summary."))
        with patch(FETCH, new=AsyncMock(return_value=_html("The full article."))):
            await _sync(conn, {FEED_A: feed})
        stored = store.records[f"{FEED_A}#1"]
        store.content_writes.clear()

        not_html = FetchResponse(status_code=200, content_bytes=b"%PDF-1.7", headers=headers,
                                 final_url="", strategy="aiohttp")
        with patch(FETCH, new=AsyncMock(return_value=not_html)):
            await _sync(conn, {FEED_A: feed})
            assert store.content_writes == []
            assert store.records[f"{FEED_A}#1"].external_revision_id == stored.external_revision_id

            with patch.object(conn, "_fetch_and_parse_feed", new=AsyncMock(return_value=feed)):
                with pytest.raises(HTTPException) as raised:
                    await conn.stream_record(stored)
                assert raised.value.status_code == 502, "indexing retries a 502, not a 404"
                stored.external_revision_id = hashlib.md5(b"Only the summary.").hexdigest()
                response = await conn.stream_record(stored)
        assert b"".join([c async for c in response.body_iterator]) == b"Only the summary."

    @pytest.mark.asyncio
    async def test_an_episode_gone_from_the_feed_is_not_served_as_another_with_its_link(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        show = "https://a.example.com/show"
        pages = {FEED_A: _xml(_page([_entry("urn:ep:2", "Episode 2", show, "Notes for episode 2.")]))}
        fetch = AsyncMock(side_effect=lambda url, **_: pages[url])
        gone = FileRecord(
            record_name="Episode 1", external_record_id="urn:ep:1", external_record_group_id=FEED_A,
            weburl=show, origin="CONNECTOR", connector_name="RSS", connector_id="rss-conn-1",
            record_type="FILE", version=0, is_file=True, mime_type="text/plain",
        )
        with patch(FETCH, new=fetch), pytest.raises(HTTPException) as raised:
            await conn.stream_record(gone)
        assert raised.value.status_code == 404
        # The show page is no episode's text, so it is not crawled in the episode's place.
        assert [c.kwargs["url"] for c in fetch.await_args_list] == [FEED_A]

    @pytest.mark.asyncio
    async def test_an_entry_behind_a_failed_page_is_retried(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        show = "https://a.example.com/show"
        pages = {
            FEED_A: _xml(_page([_entry("urn:ep:1", "Episode 1", show)], "page2.xml")),
            "https://a.example.com/page2.xml": _xml(b"", status=503),
        }
        on_page_two = FileRecord(
            record_name="Episode 2", external_record_id="urn:ep:2", external_record_group_id=FEED_A,
            weburl=show, origin="CONNECTOR", connector_name="RSS", connector_id="rss-conn-1",
            record_type="FILE", version=0, is_file=True, mime_type="text/plain",
        )
        fetch = AsyncMock(side_effect=lambda url, **_: pages[url])
        with patch(FETCH, new=fetch), pytest.raises(HTTPException) as raised:
            await conn.stream_record(on_page_two)
        assert raised.value.status_code == 502

        # With a link of its own, the entry's page is still not crawled in its place.
        on_page_two.weburl = "https://a.example.com/episode-2"
        fetch.reset_mock()
        with patch(FETCH, new=fetch), pytest.raises(HTTPException) as raised:
            await conn.stream_record(on_page_two)
        assert raised.value.status_code == 502
        assert "https://a.example.com/episode-2" not in [c.kwargs["url"] for c in fetch.await_args_list]

    @pytest.mark.asyncio
    async def test_streaming_follows_an_empty_first_page_to_the_entry(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        pages = {
            FEED_A: _xml(_page([], "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page([_entry("urn:p:2", "Post 2", FEED_A, "Text from page two.")])),
        }
        linkless = FileRecord(
            record_name="Post 2", external_record_id="urn:p:2", external_record_group_id=FEED_A,
            weburl=FEED_A, origin="CONNECTOR", connector_name="RSS", connector_id="rss-conn-1",
            record_type="FILE", version=0, is_file=True, mime_type="text/plain",
        )
        with patch(FETCH, new=AsyncMock(side_effect=lambda url, **_: pages[url])):
            response = await conn.stream_record(linkless)
        assert b"".join([c async for c in response.body_iterator]) == b"Text from page two."

    @pytest.mark.asyncio
    async def test_streaming_serves_an_entry_read_before_a_later_page_failed(self) -> None:
        conn = _connector(FakeStore(), [FEED_A])
        pages = {
            FEED_A: _xml(_page(_entries(1), "page2.xml")),
            "https://a.example.com/page2.xml": _xml(_page(
                [_entry("urn:p:2", "Post 2", "https://a.example.com/2", "Text from page two.")], "page3.xml",
            )),
            "https://a.example.com/page3.xml": _xml(b"", status=503),
        }
        on_page_two = FileRecord(
            record_name="Post 2", external_record_id="urn:p:2", external_record_group_id=FEED_A,
            weburl="https://a.example.com/2", origin="CONNECTOR", connector_name="RSS", connector_id="rss-conn-1",
            record_type="FILE", version=0, is_file=True, mime_type="text/plain",
        )
        fetch = AsyncMock(side_effect=lambda url, **_: pages[url])
        with patch(FETCH, new=fetch):
            response = await conn.stream_record(on_page_two)
        assert b"".join([c async for c in response.body_iterator]) == b"Text from page two."
        assert "https://a.example.com/2" not in [c.kwargs["url"] for c in fetch.await_args_list]
