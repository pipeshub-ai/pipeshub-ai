"""Extended tests for RSSConnector covering more uncovered code paths."""

import time
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.sources.rss.connector import RSSApp, RSSConnector
from app.models.entities import AppUser, RecordGroupType, RecordType, User
from app.models.permission import EntityType, PermissionType


def _make_connector():
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    ds_provider = MagicMock()
    config_service = AsyncMock()
    return RSSConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=ds_provider,
        config_service=config_service,
        connector_id="rss-conn-1",
    )


def _make_entry(title="Article", link="https://example.com/article-1",
                guid=None, summary="Summary", content=None,
                published_parsed=None, summary_detail=None):
    entry = {"title": title, "link": link, "id": guid or link, "summary": summary}
    if published_parsed:
        entry["published_parsed"] = published_parsed
    if content:
        entry["content"] = content
    if summary_detail:
        entry["summary_detail"] = summary_detail
    return entry


# ===========================================================================
# Init
# ===========================================================================

class TestRSSInit:
    async def test_init_no_config(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value=None)
        # init catches the ValueError and returns False
        result = await conn.init()
        assert result is False

    async def test_init_no_sync_config(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={"sync": {}})
        result = await conn.init()
        assert result is False

    async def test_init_no_feed_urls(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={"sync": {"feed_urls": ""}})
        result = await conn.init()
        assert result is False

    async def test_init_string_fetch_full_content(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(return_value={
            "sync": {"feed_urls": "https://blog.example.com/rss", "fetch_full_content": "false"},
        })
        result = await conn.init()
        assert result is True
        assert conn.fetch_full_content is False
        # Cleanup
        if conn.session:
            await conn.session.close()

    async def test_init_exception(self):
        conn = _make_connector()
        conn.config_service.get_config = AsyncMock(side_effect=Exception("error"))
        result = await conn.init()
        assert result is False


# ===========================================================================
# _parse_feed_urls
# ===========================================================================

class TestParseFeedUrls:
    def test_comma_separated(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss, https://b.com/rss")
        assert len(result) == 2

    def test_newline_separated(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss\nhttps://b.com/rss")
        assert len(result) == 2

    def test_mixed_separators(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss,\nhttps://b.com/rss\nhttps://c.com/rss")
        assert len(result) == 3

    def test_deduplication(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss, https://a.com/rss")
        assert len(result) == 1

    def test_invalid_urls_filtered(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("not-a-url, https://valid.com/rss, ftp://bad.com")
        assert len(result) == 1

    def test_empty_items_filtered(self):
        conn = _make_connector()
        result = conn._parse_feed_urls("https://a.com/rss,,  ,https://b.com/rss")
        assert len(result) == 2


# ===========================================================================
# test_connection_and_access
# ===========================================================================

class TestTestConnection:
    async def test_no_feed_urls(self):
        conn = _make_connector()
        conn.feed_urls = []
        assert await conn.test_connection_and_access() is False

    async def test_no_session(self):
        conn = _make_connector()
        conn.feed_urls = ["https://example.com/rss"]
        conn.session = None
        assert await conn.test_connection_and_access() is False

    async def test_success(self):
        conn = _make_connector()
        conn.feed_urls = ["https://example.com/rss"]
        mock_response = MagicMock()
        mock_response.status = 200
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        conn.session = mock_session
        assert await conn.test_connection_and_access() is True

    async def test_bad_status(self):
        conn = _make_connector()
        conn.feed_urls = ["https://example.com/rss"]
        mock_response = MagicMock()
        mock_response.status = 404
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=None),
        ))
        conn.session = mock_session
        assert await conn.test_connection_and_access() is False

    async def test_exception(self):
        conn = _make_connector()
        conn.feed_urls = ["https://example.com/rss"]
        mock_session = MagicMock()
        mock_session.get = MagicMock(side_effect=Exception("error"))
        conn.session = mock_session
        assert await conn.test_connection_and_access() is False


# ===========================================================================
# get_app_users
# ===========================================================================

class TestGetAppUsers:
    def test_basic_conversion(self):
        conn = _make_connector()
        user = MagicMock()
        user.email = "test@example.com"
        user.source_user_id = "su-1"
        user.id = "u-1"
        user.org_id = "org-1"
        user.full_name = "Test User"
        user.is_active = True
        user.title = "Engineer"
        result = conn.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "test@example.com"

    def test_user_without_email_filtered(self):
        conn = _make_connector()
        user = MagicMock()
        user.email = ""
        result = conn.get_app_users([user])
        assert len(result) == 0


# ===========================================================================
# _extract_title_from_url
# ===========================================================================

class TestExtractTitleFromUrl:
    def test_empty_url(self):
        conn = _make_connector()
        assert conn._extract_title_from_url("") == "Untitled"

    def test_url_with_path(self):
        conn = _make_connector()
        assert conn._extract_title_from_url("https://example.com/my-article") == "my article"

    def test_url_with_underscores(self):
        conn = _make_connector()
        assert conn._extract_title_from_url("https://example.com/my_article") == "my article"

    def test_url_no_path(self):
        conn = _make_connector()
        assert conn._extract_title_from_url("https://example.com") == "example.com"

    def test_url_with_trailing_slash(self):
        conn = _make_connector()
        result = conn._extract_title_from_url("https://example.com/blog/")
        assert result == "example.com" or "blog" in result


# ===========================================================================
# _parse_feed_timestamp
# ===========================================================================

class TestParseFeedTimestamp:
    def test_none(self):
        conn = _make_connector()
        assert conn._parse_feed_timestamp(None) is None

    def test_valid_struct(self):
        conn = _make_connector()
        ts = time.strptime("2025-01-15", "%Y-%m-%d")
        result = conn._parse_feed_timestamp(ts)
        assert isinstance(result, int)
        assert result > 0

    def test_invalid_struct(self):
        conn = _make_connector()
        assert conn._parse_feed_timestamp("not-a-struct") is None


# ===========================================================================
# _extract_text_content
# ===========================================================================

class TestExtractTextContent:
    def test_empty_html(self):
        conn = _make_connector()
        assert conn._extract_text_content("") == ""

    def test_none_html(self):
        conn = _make_connector()
        assert conn._extract_text_content(None) == ""

    @patch("app.connectors.sources.rss.connector.trafilatura.extract")
    def test_bytes_input(self, mock_extract):
        mock_extract.return_value = "Extracted text"
        conn = _make_connector()
        result = conn._extract_text_content(b"<html><body>Content</body></html>")
        assert result == "Extracted text"

    @patch("app.connectors.sources.rss.connector.trafilatura.extract")
    def test_string_input(self, mock_extract):
        mock_extract.return_value = "Text"
        conn = _make_connector()
        result = conn._extract_text_content("<html><body>Content</body></html>")
        assert result == "Text"

    @patch("app.connectors.sources.rss.connector.trafilatura.extract")
    def test_extract_returns_none(self, mock_extract):
        mock_extract.return_value = None
        conn = _make_connector()
        assert conn._extract_text_content("<html></html>") == ""


# ===========================================================================
# _process_entry
# ===========================================================================

class TestProcessEntry:
    async def test_no_link_returns_none(self):
        conn = _make_connector()
        entry = {"title": "No Link"}
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is None

    async def test_duplicate_url_skipped(self):
        conn = _make_connector()
        conn.processed_urls.add("https://example.com/article-1")
        entry = _make_entry()
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is None

    async def test_entry_with_content_value(self):
        conn = _make_connector()
        entry = _make_entry(content=[{"value": "<p>Full content</p>"}])
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
        record, perms = result
        assert record.record_name == "Article"
        assert record.mime_type == MimeTypes.HTML.value
        assert len(perms) == 1

    async def test_entry_with_html_summary(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = _make_entry(
            summary="<p>Summary content</p>",
            summary_detail={"type": "text/html"},
        )
        with patch.object(conn, "_extract_text_content", return_value="Summary content"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None

    async def test_entry_fallback_to_title(self):
        conn = _make_connector()
        conn.fetch_full_content = False
        entry = _make_entry(summary="")
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
        record, _ = result
        assert record.record_name == "Article"

    async def test_entry_with_published_parsed(self):
        conn = _make_connector()
        ts = time.strptime("2025-06-15", "%Y-%m-%d")
        entry = _make_entry(published_parsed=ts, content=[{"value": "Content"}])
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
        record, _ = result
        assert record.source_created_at > 0


# ===========================================================================
# run_sync
# ===========================================================================

class TestRunSync:
    async def test_run_sync_processes_feeds(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed1.com/rss"]
        with patch.object(conn, "_process_feed", new_callable=AsyncMock, return_value=5):
            await conn.run_sync()
        conn.data_entities_processor.on_new_app_users.assert_called_once()

    async def test_run_sync_feed_error_continues(self):
        conn = _make_connector()
        conn.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        with patch.object(conn, "_process_feed", new_callable=AsyncMock,
                         side_effect=[Exception("error"), 3]):
            await conn.run_sync()

    async def test_run_sync_exception_raises(self):
        conn = _make_connector()
        conn.data_entities_processor.get_all_active_users = AsyncMock(
            side_effect=Exception("error")
        )
        with pytest.raises(Exception, match="error"):
            await conn.run_sync()


# ===========================================================================
# create_record_group
# ===========================================================================

class TestCreateRecordGroup:
    async def test_creates_group(self):
        conn = _make_connector()
        await conn.create_record_group("https://blog.example.com/rss")
        conn.data_entities_processor.on_new_record_groups.assert_called_once()
        call_args = conn.data_entities_processor.on_new_record_groups.call_args[0][0]
        rg, perms = call_args[0]
        assert rg.group_type == RecordGroupType.RSS_FEED
        assert perms[0].entity_type == EntityType.ORG

    async def test_error_raises(self):
        conn = _make_connector()
        conn.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=Exception("error")
        )
        with pytest.raises(Exception):
            await conn.create_record_group("https://blog.example.com/rss")


# ===========================================================================
# cleanup & reindex
# ===========================================================================

class TestCleanup:
    async def test_cleanup(self):
        conn = _make_connector()
        conn.session = MagicMock()
        conn.session.close = AsyncMock()
        conn.processed_urls.add("url")
        await conn.cleanup()
        assert conn.session is None
        assert len(conn.processed_urls) == 0

    async def test_cleanup_no_session(self):
        conn = _make_connector()
        conn.session = None
        await conn.cleanup()  # should not raise

    async def test_reindex_not_implemented(self):
        conn = _make_connector()
        with pytest.raises(NotImplementedError):
            await conn.reindex_records([])

    async def test_incremental_sync(self):
        conn = _make_connector()
        with patch.object(conn, "run_sync", new_callable=AsyncMock):
            await conn.run_incremental_sync()
            conn.run_sync.assert_called_once()
