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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build an RSSConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    connector_id = "rss-conn-1"
    connector = RSSConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
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
            )
            assert isinstance(connector, RSSConnector)
            mock_proc.initialize.assert_awaited_once()
