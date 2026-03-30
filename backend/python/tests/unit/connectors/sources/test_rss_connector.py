"""Tests for the RSS connector."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.sources.rss.connector import RSSApp, RSSConnector
from app.models.entities import (
    AppUser,
    RecordGroupType,
    RecordType,
    User,
)
from app.models.permission import EntityType, PermissionType
import asyncio
import hashlib
from io import BytesIO
from app.connectors.sources.rss.connector import RSSConnector
from app.models.entities import FileRecord, RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build an RSSConnector with all dependencies mocked."""
    from app.models.entities import AppMetadata
    
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    _creator = MagicMock()
    _creator.email = "user@test.com"
    data_entities_processor.get_user_by_user_id = AsyncMock(return_value=_creator)
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.get_app_by_id = AsyncMock(return_value=AppMetadata(
        connector_id="rss-conn-1",
        name="RSS Feed",
        type="rss",
        app_group="RSS",
        scope="PERSONAL",
        created_by="user-1",
        created_at_timestamp=1234567890,
        updated_at_timestamp=1234567890,
    ))
    
    data_store_provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    data_store_provider.transaction.return_value = mock_tx
    
    config_service = AsyncMock()
    connector_id = "rss-conn-1"
    connector = RSSConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
        scope="personal",
        created_by="test-user-id",
    )
    return connector


def _rss_config(feed_urls="https://blog.example.com/rss", max_articles=50,
                fetch_full=True):
    """Build a mock config dict for RSS connector."""
    return {
        "sync": {
            "feed_urls": feed_urls,
            "max_articles_per_feed": max_articles,
            "fetch_full_content": fetch_full,
        }
    }


def _make_feed_entry(title="Test Article", link="https://example.com/article-1",
                     guid=None, summary="Article summary", content=None,
                     published_parsed=None):
    """Build a mock feedparser entry."""
    entry = {
        "title": title,
        "link": link,
        "id": guid or link,
        "summary": summary,
    }
    if published_parsed:
        entry["published_parsed"] = published_parsed
    if content:
        entry["content"] = content
    return entry


# ===================================================================
# RSSApp tests
# ===================================================================

class TestRSSApp:
    def test_rss_app_creation(self):
        app = RSSApp("rss-1")
        assert app.app_name == Connectors.RSS


# ===================================================================
# RSSConnector - Initialization
# ===================================================================

class TestRSSConnectorInit:
    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_rss_config()
        )
        result = await connector.init()
        assert result is True
        assert len(connector.feed_urls) == 1
        assert connector.feed_urls[0] == "https://blog.example.com/rss"
        assert connector.max_articles_per_feed == 50
        assert connector.fetch_full_content is True
        assert connector.session is not None

    @pytest.mark.asyncio
    async def test_init_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_sync_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"sync": {}})
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_feed_urls(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"feed_urls": ""}}
        )
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_fetch_full_content_string_true(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_rss_config(fetch_full="true")
        )
        result = await connector.init()
        assert result is True
        assert connector.fetch_full_content is True

    @pytest.mark.asyncio
    async def test_init_fetch_full_content_string_false(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_rss_config(fetch_full="false")
        )
        result = await connector.init()
        assert result is True
        assert connector.fetch_full_content is False


# ===================================================================
# RSSConnector - URL Parsing
# ===================================================================

class TestRSSConnectorUrlParsing:
    def test_parse_feed_urls_comma_separated(self):
        connector = _make_connector()
        result = connector._parse_feed_urls(
            "https://a.com/rss, https://b.com/feed"
        )
        assert len(result) == 2
        assert "https://a.com/rss" in result
        assert "https://b.com/feed" in result

    def test_parse_feed_urls_newline_separated(self):
        connector = _make_connector()
        result = connector._parse_feed_urls(
            "https://a.com/rss\nhttps://b.com/feed"
        )
        assert len(result) == 2

    def test_parse_feed_urls_deduplication(self):
        connector = _make_connector()
        result = connector._parse_feed_urls(
            "https://a.com/rss, https://a.com/rss"
        )
        assert len(result) == 1

    def test_parse_feed_urls_invalid_urls_skipped(self):
        connector = _make_connector()
        result = connector._parse_feed_urls(
            "https://valid.com/rss, not-a-url, ftp://invalid.com"
        )
        assert len(result) == 1
        assert result[0] == "https://valid.com/rss"

    def test_parse_feed_urls_empty_string(self):
        connector = _make_connector()
        result = connector._parse_feed_urls("")
        assert result == []


# ===================================================================
# RSSConnector - Title extraction
# ===================================================================

class TestRSSConnectorTitleExtraction:
    def test_extract_title_from_url_with_path(self):
        connector = _make_connector()
        result = connector._extract_title_from_url("https://example.com/blog/my-article")
        assert "my article" in result

    def test_extract_title_from_url_no_path(self):
        connector = _make_connector()
        result = connector._extract_title_from_url("https://example.com")
        assert result == "example.com"

    def test_extract_title_from_url_empty(self):
        connector = _make_connector()
        result = connector._extract_title_from_url("")
        assert result == "Untitled"


# ===================================================================
# RSSConnector - Timestamp parsing
# ===================================================================

class TestRSSConnectorTimestampParsing:
    def test_parse_feed_timestamp_valid(self):
        connector = _make_connector()
        ts = time.strptime("2024-01-15T10:30:00", "%Y-%m-%dT%H:%M:%S")
        result = connector._parse_feed_timestamp(ts)
        assert result is not None
        assert isinstance(result, int)
        assert result > 0

    def test_parse_feed_timestamp_none(self):
        connector = _make_connector()
        assert connector._parse_feed_timestamp(None) is None


# ===================================================================
# RSSConnector - Content extraction
# ===================================================================

class TestRSSConnectorContentExtraction:
    def test_extract_text_content_empty(self):
        connector = _make_connector()
        assert connector._extract_text_content("") == ""

    def test_extract_text_content_html_bytes(self):
        connector = _make_connector()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "Extracted text"
            result = connector._extract_text_content(b"<html><body>Content</body></html>")
            assert result == "Extracted text"

    def test_extract_text_content_html_string(self):
        connector = _make_connector()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "Extracted text"
            result = connector._extract_text_content("<html><body>Content</body></html>")
            assert result == "Extracted text"

    def test_extract_text_content_extraction_fails(self):
        connector = _make_connector()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.return_value = None
            result = connector._extract_text_content("<html><body>Content</body></html>")
            assert result == ""


# ===================================================================
# RSSConnector - Connection test
# ===================================================================

class TestRSSConnectorConnectionTest:
    @pytest.mark.asyncio
    async def test_connection_no_urls(self):
        connector = _make_connector()
        connector.feed_urls = []
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_connection_no_session(self):
        connector = _make_connector()
        connector.feed_urls = ["https://example.com/rss"]
        connector.session = None
        result = await connector.test_connection_and_access()
        assert result is False


# ===================================================================
# RSSConnector - Record group creation
# ===================================================================

class TestRSSConnectorRecordGroup:
    @pytest.mark.asyncio
    async def test_create_record_group(self):
        connector = _make_connector()
        await connector.create_record_group("https://blog.example.com/rss")
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        rg, perms = call_args[0]
        assert rg.group_type == RecordGroupType.RSS_FEED
        assert rg.external_group_id == "https://blog.example.com/rss"
        assert perms[0].entity_type == EntityType.ORG
        assert perms[0].type == PermissionType.READ


# ===================================================================
# RSSConnector - Entry processing
# ===================================================================

class TestRSSConnectorEntryProcessing:
    @pytest.mark.asyncio
    async def test_process_entry_basic(self):
        connector = _make_connector()
        connector.fetch_full_content = False
        entry = _make_feed_entry(
            title="My Article",
            link="https://example.com/article-1",
            summary="This is a test article summary",
        )
        result = await connector._process_entry(entry, "https://feed.example.com/rss")
        assert result is not None
        file_record, permissions = result
        assert file_record.record_name == "My Article"
        assert file_record.weburl == "https://example.com/article-1"
        assert file_record.mime_type == MimeTypes.HTML.value
        assert file_record.extension == "html"
        assert file_record.connector_name == Connectors.RSS
        assert len(permissions) == 1
        assert permissions[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_process_entry_no_link(self):
        connector = _make_connector()
        entry = {"title": "No Link"}
        result = await connector._process_entry(entry, "https://feed.example.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_process_entry_duplicate_skipped(self):
        connector = _make_connector()
        connector.fetch_full_content = False
        connector.processed_urls.add("https://example.com/article-1")
        entry = _make_feed_entry(link="https://example.com/article-1")
        result = await connector._process_entry(entry, "https://feed.example.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_process_entry_with_content_value(self):
        connector = _make_connector()
        connector.fetch_full_content = False
        entry = _make_feed_entry(
            link="https://example.com/article-2",
            content=[{"value": "<p>Full article content here</p>"}],
        )
        result = await connector._process_entry(entry, "https://feed.example.com/rss")
        assert result is not None
        file_record, _ = result
        assert file_record.size_in_bytes > 0

    @pytest.mark.asyncio
    async def test_process_entry_fallback_to_title(self):
        connector = _make_connector()
        connector.fetch_full_content = False
        entry = {
            "title": "Title Only",
            "link": "https://example.com/title-only",
            "id": "unique-guid",
        }
        result = await connector._process_entry(entry, "https://feed.example.com/rss")
        assert result is not None


# ===================================================================
# RSSConnector - App users
# ===================================================================

class TestRSSConnectorAppUsers:
    def test_get_app_users(self):
        connector = _make_connector()
        connector.connector_name = Connectors.RSS
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = connector.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].email == "a@test.com"


# ===================================================================
# RSSConnector - Sync flow
# ===================================================================

class TestRSSConnectorSync:
    @pytest.mark.asyncio
    async def test_run_sync_processes_feeds(self):
        connector = _make_connector()
        connector.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        connector.session = MagicMock()

        connector._process_feed = AsyncMock(return_value=5)
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[]
        )

        await connector.run_sync()
        assert connector._process_feed.await_count == 2

    @pytest.mark.asyncio
    async def test_run_sync_feed_error_continues(self):
        connector = _make_connector()
        connector.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        connector.session = MagicMock()

        connector._process_feed = AsyncMock(
            side_effect=[Exception("Network error"), 3]
        )
        connector.data_entities_processor.get_all_active_users = AsyncMock(
            return_value=[]
        )

        await connector.run_sync()
        # Should not raise, second feed still processed

    @pytest.mark.asyncio
    async def test_incremental_sync_delegates(self):
        connector = _make_connector()
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


# ===================================================================
# RSSConnector - Cleanup
# ===================================================================

class TestRSSConnectorCleanup:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        connector = _make_connector()
        mock_session = AsyncMock()
        connector.session = mock_session
        connector.processed_urls = {"https://example.com"}

        await connector.cleanup()
        mock_session.close.assert_awaited_once()
        assert connector.session is None
        assert len(connector.processed_urls) == 0


# ===================================================================
# RSSConnector - Unsupported operations
# ===================================================================

class TestRSSConnectorUnsupported:
    @pytest.mark.asyncio
    async def test_reindex_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_get_filter_options_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("any")

    @pytest.mark.asyncio
    async def test_handle_webhook_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_get_signed_url_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            from app.models.entities import FileRecord
            record = FileRecord(
                external_record_id="x",
                record_name="x",
                origin=OriginTypes.CONNECTOR,
                connector_name=Connectors.RSS,
                connector_id="rss-1",
                record_type=RecordType.FILE,
                version=1,
                is_file=True,
            )
            await connector.get_signed_url(record)


# ===================================================================
# RSSConnector - Factory
# ===================================================================

class TestRSSConnectorFactory:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        logger = MagicMock()
        data_store_provider = MagicMock()
        config_service = AsyncMock()

        with patch(
            "app.connectors.sources.rss.connector.DataSourceEntitiesProcessor"
        ) as MockProcessor:
            mock_proc = MagicMock()
            mock_proc.initialize = AsyncMock()
            MockProcessor.return_value = mock_proc
            connector = await RSSConnector.create_connector(
                logger=logger,
                data_store_provider=data_store_provider,
                config_service=config_service,
                connector_id="rss-conn-1",
                scope="personal",
                created_by="test-user-id",
            )
            assert isinstance(connector, RSSConnector)
            mock_proc.initialize.assert_awaited_once()

# =============================================================================
# Merged from test_rss_connector_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector_cov():
    """Build an RSSConnector with all dependencies mocked."""
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[])
    _creator = MagicMock()
    _creator.email = "user@test.com"
    dep.get_user_by_user_id = AsyncMock(return_value=_creator)
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
        scope="personal",
        created_by="test-user-id",
    )


def _make_mock_response(status=200, content=b"<html>body</html>", headers=None):
    """Build a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.read = AsyncMock(return_value=content)
    resp.headers = headers or {"Content-Type": "text/html"}
    return resp


def _make_async_context_manager(return_value):
    """Create an async context manager mock."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def _make_session(response):
    """Create a mock session with get() returning a context manager."""
    session = MagicMock()
    session.get = MagicMock(return_value=_make_async_context_manager(response))
    return session


def _make_record(**overrides):
    """Build a minimal Record for testing."""
    defaults = {
        "id": "rec-1",
        "record_name": "Test Article",
        "record_type": RecordType.FILE,
        "external_record_id": "ext-1",
        "origin": OriginTypes.CONNECTOR,
        "connector_name": Connectors.RSS,
        "connector_id": "rss-conn-1",
        "version": 1,
        "is_file": True,
        "weburl": "https://example.com/article-1",
        "mime_type": "text/html",
    }
    defaults.update(overrides)
    return FileRecord(**defaults)


# ===================================================================
# _fetch_and_parse_feed
# ===================================================================

class TestFetchAndParseFeed:

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=404)
        conn.session = _make_session(resp)
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_success_returns_feed(self):
        conn = _make_connector_cov()
        feed_xml = b"""<?xml version="1.0"?>
        <rss version="2.0">
            <channel>
                <title>Test Feed</title>
                <item>
                    <title>Article 1</title>
                    <link>https://example.com/1</link>
                </item>
            </channel>
        </rss>"""
        resp = _make_mock_response(status=200, content=feed_xml)
        conn.session = _make_session(resp)
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is not None
        assert len(result.entries) == 1

    @pytest.mark.asyncio
    async def test_bozo_feed_with_no_entries_returns_none(self):
        conn = _make_connector_cov()
        # Return content that feedparser can parse but marks as bozo
        resp = _make_mock_response(status=200, content=b"not-valid-xml-at-all")
        conn.session = _make_session(resp)
        with patch("app.connectors.sources.rss.connector.feedparser") as mock_fp:
            mock_feed = MagicMock()
            mock_feed.bozo = True
            mock_feed.entries = []
            mock_feed.bozo_exception = Exception("parse error")
            mock_fp.parse.return_value = mock_feed
            result = await conn._fetch_and_parse_feed("https://feed.com/rss")
            assert result is None

    @pytest.mark.asyncio
    async def test_bozo_feed_with_entries_returns_feed(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=200, content=b"<xml>something</xml>")
        conn.session = _make_session(resp)
        with patch("app.connectors.sources.rss.connector.feedparser") as mock_fp:
            mock_feed = MagicMock()
            mock_feed.bozo = True
            mock_feed.entries = [{"title": "Article"}]
            mock_fp.parse.return_value = mock_feed
            result = await conn._fetch_and_parse_feed("https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        conn = _make_connector_cov()
        session = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=cm)
        conn.session = session
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_cov()
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("network error"))
        conn.session = session
        result = await conn._fetch_and_parse_feed("https://feed.com/rss")
        assert result is None


# ===================================================================
# _fetch_article_content
# ===================================================================

class TestFetchArticleContent:

    @pytest.mark.asyncio
    async def test_success_returns_text(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=200, content=b"<html><body>Hello world</body></html>")
        resp.headers = {"Content-Type": "text/html; charset=utf-8"}
        conn.session = _make_session(resp)
        with patch.object(conn, "_extract_text_content", return_value="Hello world"):
            result = await conn._fetch_article_content("https://example.com/article")
            assert result == "Hello world"

    @pytest.mark.asyncio
    async def test_http_error_returns_empty(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=500)
        conn.session = _make_session(resp)
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""

    @pytest.mark.asyncio
    async def test_non_html_content_type_returns_empty(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=200, content=b"binary data")
        resp.headers = {"Content-Type": "application/pdf"}
        conn.session = _make_session(resp)
        result = await conn._fetch_article_content("https://example.com/file.pdf")
        assert result == ""

    @pytest.mark.asyncio
    async def test_xml_content_type_succeeds(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=200, content=b"<xml>data</xml>")
        resp.headers = {"Content-Type": "application/xml"}
        conn.session = _make_session(resp)
        with patch.object(conn, "_extract_text_content", return_value="data"):
            result = await conn._fetch_article_content("https://example.com/feed.xml")
            assert result == "data"

    @pytest.mark.asyncio
    async def test_timeout_returns_empty(self):
        conn = _make_connector_cov()
        session = MagicMock()
        cm = MagicMock()
        cm.__aenter__ = AsyncMock(side_effect=asyncio.TimeoutError())
        cm.__aexit__ = AsyncMock(return_value=None)
        session.get = MagicMock(return_value=cm)
        conn.session = session
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        conn = _make_connector_cov()
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("connection error"))
        conn.session = session
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""

    @pytest.mark.asyncio
    async def test_missing_content_type_header(self):
        conn = _make_connector_cov()
        resp = _make_mock_response(status=200, content=b"<html>body</html>")
        mock_headers = MagicMock()
        mock_headers.get = MagicMock(return_value="")
        resp.headers = mock_headers
        conn.session = _make_session(resp)
        result = await conn._fetch_article_content("https://example.com/article")
        assert result == ""  # Empty content-type doesn't contain 'html' or 'xml'


# ===================================================================
# _process_feed
# ===================================================================

class TestProcessFeed:

    @pytest.mark.asyncio
    async def test_no_entries_returns_zero(self):
        conn = _make_connector_cov()
        conn.session = MagicMock()
        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=None):
                result = await conn._process_feed("https://feed.com/rss", [])
                assert result == 0

    @pytest.mark.asyncio
    async def test_empty_entries_returns_zero(self):
        conn = _make_connector_cov()
        conn.session = MagicMock()
        mock_feed = MagicMock()
        mock_feed.entries = []
        mock_feed.feed = {"title": "Test Feed"}
        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                result = await conn._process_feed("https://feed.com/rss", [])
                assert result == 0

    @pytest.mark.asyncio
    async def test_processes_entries_and_flushes_batch(self):
        conn = _make_connector_cov()
        conn.batch_size = 2  # Small batch for testing
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [
            {"title": f"Article {i}", "link": f"https://example.com/{i}", "id": f"id-{i}"}
            for i in range(5)
        ]
        mock_feed.feed = {"title": "Test Feed"}

        mock_record = MagicMock()
        mock_perms = [MagicMock()]

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(conn, "_process_entry", new_callable=AsyncMock, return_value=(mock_record, mock_perms)):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 5
                    # Should have flushed: 2 batches of 2 + 1 final batch of 1
                    assert conn.data_entities_processor.on_new_records.await_count == 3

    @pytest.mark.asyncio
    async def test_entry_processing_error_continues(self):
        conn = _make_connector_cov()
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [
            {"title": "Article 1", "link": "https://example.com/1", "id": "id-1"},
            {"title": "Article 2", "link": "https://example.com/2", "id": "id-2"},
        ]
        mock_feed.feed = {"title": "Test Feed"}

        mock_record = MagicMock()
        mock_perms = [MagicMock()]

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(
                    conn, "_process_entry", new_callable=AsyncMock,
                    side_effect=[Exception("parse error"), (mock_record, mock_perms)]
                ):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 1  # Only second entry succeeded

    @pytest.mark.asyncio
    async def test_process_entry_returns_none_skipped(self):
        conn = _make_connector_cov()
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [{"title": "Article", "link": "https://ex.com/1", "id": "1"}]
        mock_feed.feed = {"title": "Feed"}

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(conn, "_process_entry", new_callable=AsyncMock, return_value=None):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 0

    @pytest.mark.asyncio
    async def test_max_articles_limit_applied(self):
        conn = _make_connector_cov()
        conn.max_articles_per_feed = 2
        conn.session = MagicMock()

        mock_feed = MagicMock()
        mock_feed.entries = [
            {"title": f"Art {i}", "link": f"https://ex.com/{i}", "id": f"id-{i}"}
            for i in range(10)
        ]
        mock_feed.feed = {"title": "Feed"}

        mock_record = MagicMock()
        mock_perms = [MagicMock()]

        with patch.object(conn, "create_record_group", new_callable=AsyncMock):
            with patch.object(conn, "_fetch_and_parse_feed", new_callable=AsyncMock, return_value=mock_feed):
                with patch.object(conn, "_process_entry", new_callable=AsyncMock, return_value=(mock_record, mock_perms)):
                    result = await conn._process_feed("https://feed.com/rss", [])
                    assert result == 2  # Limited to max_articles_per_feed


# ===================================================================
# _process_entry - fetch_full_content path
# ===================================================================

class TestProcessEntryFetchFullContent:

    @pytest.mark.asyncio
    async def test_fetch_full_content_enabled_fetches_article(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = True
        entry = {
            "title": "Test",
            "link": "https://example.com/article-new",
            "id": "guid-new",
        }
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="Full article text"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None
            record, _ = result
            # Content should include "Full article text"
            assert record.size_in_bytes > 0

    @pytest.mark.asyncio
    async def test_fetch_full_content_falls_back_to_summary(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = True
        entry = {
            "title": "Test",
            "link": "https://example.com/article-fb",
            "id": "guid-fb",
            "summary": "Fallback summary text",
        }
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value=""):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None
            record, _ = result
            assert record.size_in_bytes > 0

    @pytest.mark.asyncio
    async def test_html_summary_with_xhtml_type(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article-xhtml",
            "id": "guid-xhtml",
            "summary": "<p>XHTML summary</p>",
            "summary_detail": {"type": "application/xhtml+xml"},
        }
        with patch.object(conn, "_extract_text_content", return_value="XHTML summary"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_plain_text_summary_used_directly(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article-plain",
            "id": "guid-plain",
            "summary": "Plain text summary",
            "summary_detail": {"type": "text/plain"},
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
        record, _ = result
        expected_bytes = "Plain text summary".encode("utf-8")
        assert record.size_in_bytes == len(expected_bytes)


# ===================================================================
# stream_record
# ===================================================================

class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_no_weburl_returns_none(self):
        conn = _make_connector_cov()
        record = _make_record(weburl="")
        result = await conn.stream_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        conn = _make_connector_cov()
        record = _make_record()
        resp = _make_mock_response(status=500)
        conn.session = _make_session(resp)
        result = await conn.stream_record(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_success_html_content_cleaned(self):
        conn = _make_connector_cov()
        record = _make_record(mime_type="text/html")
        resp = _make_mock_response(status=200, content=b"<html><body>Hello</body></html>")
        conn.session = _make_session(resp)
        with patch.object(conn, "_extract_text_content", return_value="Hello"):
            with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
                mock_stream.return_value = MagicMock()
                result = await conn.stream_record(record)
                assert result is not None
                mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_non_html_content_not_cleaned(self):
        conn = _make_connector_cov()
        record = _make_record(mime_type="application/pdf")
        resp = _make_mock_response(status=200, content=b"pdf binary data")
        conn.session = _make_session(resp)
        with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await conn.stream_record(record)
            assert result is not None
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_success_html_no_cleaned_text(self):
        conn = _make_connector_cov()
        record = _make_record(mime_type="text/html")
        resp = _make_mock_response(status=200, content=b"<html></html>")
        conn.session = _make_session(resp)
        with patch.object(conn, "_extract_text_content", return_value=""):
            with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
                mock_stream.return_value = MagicMock()
                result = await conn.stream_record(record)
                assert result is not None

    @pytest.mark.asyncio
    async def test_success_no_mime_type_defaults(self):
        conn = _make_connector_cov()
        record = _make_record()
        record.mime_type = None  # Set to None after creation to bypass validation
        resp = _make_mock_response(status=200, content=b"data")
        conn.session = _make_session(resp)
        with patch("app.connectors.sources.rss.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            result = await conn.stream_record(record)
            assert result is not None
            # Should use "text/html" as default
            call_kwargs = mock_stream.call_args
            assert call_kwargs[1]["mime_type"] == "text/html"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        conn = _make_connector_cov()
        record = _make_record()
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("network error"))
        conn.session = session
        result = await conn.stream_record(record)
        assert result is None


# ===================================================================
# _extract_text_content edge cases
# ===================================================================

class TestExtractTextContentEdgeCases:

    def test_exception_in_trafilatura_returns_empty(self):
        conn = _make_connector_cov()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.side_effect = Exception("parse error")
            result = conn._extract_text_content("<html>bad</html>")
            assert result == ""

    def test_bytes_with_bad_encoding(self):
        conn = _make_connector_cov()
        with patch("app.connectors.sources.rss.connector.trafilatura") as mock_traf:
            mock_traf.extract.return_value = "text"
            # bytes with replacement characters
            result = conn._extract_text_content(b"\xff\xfe<html>test</html>")
            assert result == "text"


# ===================================================================
# run_sync
# ===================================================================

class TestRunSync:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_cov()
        conn.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        with patch.object(conn, "_process_feed", new_callable=AsyncMock, return_value=10):
            await conn.run_sync()
            assert conn.data_entities_processor.on_new_app_users.await_count == 1

    @pytest.mark.asyncio
    async def test_feed_error_continues(self):
        conn = _make_connector_cov()
        conn.feed_urls = ["https://feed1.com/rss", "https://feed2.com/rss"]
        call_count = 0

        async def process_side_effect(url, users):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Feed 1 failed")
            return 5

        with patch.object(conn, "_process_feed", new_callable=AsyncMock, side_effect=process_side_effect):
            await conn.run_sync()
            # Both feeds should have been attempted

    @pytest.mark.asyncio
    async def test_sync_raises_on_general_error(self):
        conn = _make_connector_cov()
        conn.feed_urls = ["https://feed.com/rss"]
        conn.data_entities_processor.get_all_active_users = AsyncMock(side_effect=Exception("db error"))
        with pytest.raises(Exception, match="db error"):
            await conn.run_sync()


# ===================================================================
# _parse_feed_urls
# ===================================================================

class TestParseFeedUrls:
    def test_comma_separated(self):
        conn = _make_connector_cov()
        result = conn._parse_feed_urls("https://a.com/rss,https://b.com/rss")
        assert result == ["https://a.com/rss", "https://b.com/rss"]

    def test_newline_separated(self):
        conn = _make_connector_cov()
        result = conn._parse_feed_urls("https://a.com/rss\nhttps://b.com/rss")
        assert result == ["https://a.com/rss", "https://b.com/rss"]

    def test_filters_invalid_urls(self):
        conn = _make_connector_cov()
        result = conn._parse_feed_urls("https://valid.com/rss,not-a-url,ftp://bad.com")
        assert result == ["https://valid.com/rss"]

    def test_deduplication(self):
        conn = _make_connector_cov()
        result = conn._parse_feed_urls("https://a.com/rss,https://a.com/rss")
        assert result == ["https://a.com/rss"]


# ===================================================================
# test_connection_and_access
# ===================================================================

class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_no_session(self):
        conn = _make_connector_cov()
        conn.session = None
        conn.feed_urls = ["https://feed.com/rss"]
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_no_feed_urls(self):
        conn = _make_connector_cov()
        conn.feed_urls = []
        conn.session = MagicMock()
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_cov()
        conn.feed_urls = ["https://feed.com/rss"]
        resp = _make_mock_response(status=200)
        conn.session = _make_session(resp)
        result = await conn.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_bad_status(self):
        conn = _make_connector_cov()
        conn.feed_urls = ["https://feed.com/rss"]
        resp = _make_mock_response(status=404)
        conn.session = _make_session(resp)
        result = await conn.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self):
        conn = _make_connector_cov()
        conn.feed_urls = ["https://feed.com/rss"]
        session = MagicMock()
        session.get = MagicMock(side_effect=Exception("network error"))
        conn.session = session
        result = await conn.test_connection_and_access()
        assert result is False


# ===================================================================
# get_app_users
# ===================================================================

class TestGetAppUsers:
    def test_converts_users(self):
        from app.models.entities import User
        conn = _make_connector_cov()
        user = MagicMock(spec=User)
        user.source_user_id = "src1"
        user.id = "u1"
        user.email = "test@example.com"
        user.full_name = "Test User"
        user.is_active = True
        user.title = "Engineer"
        user.org_id = "org-1"
        result = conn.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "test@example.com"

    def test_skips_users_without_email(self):
        conn = _make_connector_cov()
        user = MagicMock()
        user.email = None
        result = conn.get_app_users([user])
        assert len(result) == 0


# ===================================================================
# create_record_group
# ===================================================================

class TestCreateRecordGroup:
    @pytest.mark.asyncio
    async def test_success(self):
        conn = _make_connector_cov()
        await conn.create_record_group("https://blog.example.com/rss")
        conn.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        conn = _make_connector_cov()
        conn.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=Exception("db error"))
        with pytest.raises(Exception, match="db error"):
            await conn.create_record_group("https://blog.example.com/rss")


# ===================================================================
# _process_entry
# ===================================================================

class TestProcessEntryExtended:
    @pytest.mark.asyncio
    async def test_no_link_returns_none(self):
        conn = _make_connector_cov()
        entry = {"title": "Test", "id": "1"}  # no link
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_duplicate_url_skipped(self):
        conn = _make_connector_cov()
        conn.processed_urls = {"https://example.com/article-1"}
        entry = {"title": "Test", "link": "https://example.com/article-1", "id": "1"}
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is None

    @pytest.mark.asyncio
    async def test_content_from_entry_content(self):
        conn = _make_connector_cov()
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "content": [{"value": "<p>Full article content</p>"}],
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None

    @pytest.mark.asyncio
    async def test_content_from_fetch_full(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = True
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "summary": "",
        }
        with patch.object(conn, "_fetch_article_content", new_callable=AsyncMock, return_value="Full crawled content"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_content_from_html_summary(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "summary": "<p>HTML summary</p>",
            "summary_detail": {"type": "text/html"},
        }
        with patch.object(conn, "_extract_text_content", return_value="HTML summary"):
            result = await conn._process_entry(entry, "https://feed.com/rss")
            assert result is not None

    @pytest.mark.asyncio
    async def test_content_from_plain_summary(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = False
        entry = {
            "title": "Test",
            "link": "https://example.com/article",
            "id": "1",
            "summary": "Plain text summary",
            "summary_detail": {"type": "text/plain"},
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None

    @pytest.mark.asyncio
    async def test_fallback_to_title(self):
        conn = _make_connector_cov()
        conn.fetch_full_content = False
        entry = {
            "title": "Only Title Here",
            "link": "https://example.com/article",
            "id": "1",
        }
        result = await conn._process_entry(entry, "https://feed.com/rss")
        assert result is not None
