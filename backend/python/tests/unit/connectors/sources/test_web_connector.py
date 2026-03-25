"""Tests for the Web connector and fetch_strategy module."""

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.sources.web.connector import (
    DOCUMENT_MIME_TYPES,
    FILE_MIME_TYPES,
    IMAGE_MIME_TYPES,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
    RecordUpdate,
    RetryUrl,
    Status,
    WebApp,
    WebConnector,
)
from app.connectors.sources.web.fetch_strategy import (
    FetchResponse,
    build_stealth_headers,
)
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_connector():
    """Build a WebConnector with all dependencies mocked."""
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    connector_id = "web-conn-1"
    connector = WebConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id=connector_id,
    )
    return connector


def _mock_config(url="https://example.com", crawl_type="single", depth=3,
                 max_pages=100, max_size_mb=10, follow_external=False,
                 restrict_to_start_path=False, url_should_contain=None):
    """Build a mock config dict."""
    return {
        "sync": {
            "url": url,
            "type": crawl_type,
            "depth": depth,
            "max_pages": max_pages,
            "max_size_mb": max_size_mb,
            "follow_external": follow_external,
            "restrict_to_start_path": restrict_to_start_path,
            "url_should_contain": url_should_contain or [],
        }
    }


# ===================================================================
# WebApp tests
# ===================================================================

class TestWebApp:
    def test_web_app_creation(self):
        app = WebApp("web-1")
        assert app.connector_name == Connectors.WEB


# ===================================================================
# FetchStrategy tests
# ===================================================================

class TestFetchStrategy:
    def test_build_stealth_headers_basic(self):
        headers = build_stealth_headers("https://example.com")
        assert "Accept" in headers
        assert "Sec-Ch-Ua" in headers
        assert headers["Referer"] == "https://example.com/"

    def test_build_stealth_headers_with_referer(self):
        headers = build_stealth_headers(
            "https://example.com/page", referer="https://example.com/"
        )
        assert headers["Referer"] == "https://example.com/"

    def test_build_stealth_headers_with_extra(self):
        headers = build_stealth_headers(
            "https://example.com", extra={"X-Custom": "value"}
        )
        assert headers["X-Custom"] == "value"

    def test_fetch_response_dataclass(self):
        resp = FetchResponse(
            status_code=200,
            content_bytes=b"<html>test</html>",
            headers={"Content-Type": "text/html"},
            final_url="https://example.com",
            strategy="aiohttp",
        )
        assert resp.status_code == 200
        assert resp.strategy == "aiohttp"
        assert len(resp.content_bytes) > 0


# ===================================================================
# WebConnector - Configuration
# ===================================================================

class TestWebConnectorConfig:
    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config()
        )
        result = await connector.init()
        assert result is True
        assert connector.url == "https://example.com"
        assert connector.crawl_type == "single"
        assert connector.max_depth == 3

    @pytest.mark.asyncio
    async def test_init_missing_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_missing_url(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value={"sync": {"type": "single"}}
        )
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_pages_clamp(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(max_pages=99999)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_pages"] == 10000

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_depth_clamp(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(depth=50)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_depth"] == 10

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_restrict_overrides_follow(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(
                follow_external=True, restrict_to_start_path=True
            )
        )
        result = await connector._fetch_and_parse_config()
        assert result["follow_external"] is False
        assert result["restrict_to_start_path"] is True


# ===================================================================
# WebConnector - URL processing utilities
# ===================================================================

class TestWebConnectorUrlProcessing:
    def test_extract_title_from_url(self):
        connector = _make_connector()
        title = connector._extract_title_from_url("https://example.com/blog/my-post")
        assert "my post" in title.lower()

    def test_extract_title_from_url_empty(self):
        connector = _make_connector()
        title = connector._extract_title_from_url("")
        assert title == "Untitled"

    def test_normalize_url_strips_fragment(self):
        connector = _make_connector()
        result = connector._normalize_url("https://example.com/page#section")
        assert "#" not in result

    def test_normalize_url_strips_trailing_slash(self):
        connector = _make_connector()
        result1 = connector._normalize_url("https://example.com/page/")
        result2 = connector._normalize_url("https://example.com/page")
        assert result1 == result2

    def test_ensure_trailing_slash_adds_slash(self):
        connector = _make_connector()
        result = connector._ensure_trailing_slash("https://example.com/path")
        assert result.endswith("/")

    def test_ensure_trailing_slash_keeps_extension(self):
        connector = _make_connector()
        result = connector._ensure_trailing_slash("https://example.com/file.pdf")
        assert not result.endswith("/")
        assert result == "https://example.com/file.pdf"


# ===================================================================
# WebConnector - Connection test
# ===================================================================

class TestWebConnectorConnection:
    @pytest.mark.asyncio
    async def test_test_connection_no_url(self):
        connector = _make_connector()
        connector.url = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_no_session(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = None
        result = await connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()

        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"OK",
                headers={},
                final_url="https://example.com",
                strategy="aiohttp",
            )
            result = await connector.test_connection_and_access()
            assert result is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()

        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = None
            result = await connector.test_connection_and_access()
            assert result is False


# ===================================================================
# WebConnector - Record group creation
# ===================================================================

class TestWebConnectorRecordGroup:
    @pytest.mark.asyncio
    async def test_create_record_group(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        from app.models.entities import AppUser
        app_users = [
            AppUser(
                app_name=Connectors.WEB,
                connector_id="web-conn-1",
                source_user_id="u1",
                org_id="org-1",
                email="test@example.com",
                full_name="Test User",
                is_active=True,
            )
        ]
        await connector.create_record_group(app_users)
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_create_record_group_no_url(self):
        connector = _make_connector()
        connector.url = None
        await connector.create_record_group([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()


# ===================================================================
# WebConnector - Crawl logic
# ===================================================================

class TestWebConnectorCrawl:
    @pytest.mark.asyncio
    async def test_crawl_single_page(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.connector_id = "web-conn-1"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.sync_filters = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_record.indexing_status = "QUEUED"
        mock_permissions = []

        mock_update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=mock_permissions,
        )
        connector._fetch_and_process_url = AsyncMock(return_value=mock_update)
        connector._check_index_filter = MagicMock(return_value=False)
        connector._normalize_url = MagicMock(return_value="https://example.com/")

        await connector._crawl_single_page("https://example.com")
        connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crawl_single_page_none_result(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        connector._fetch_and_process_url = AsyncMock(return_value=None)
        connector._normalize_url = MagicMock(return_value="https://example.com/")

        await connector._crawl_single_page("https://example.com")
        connector.data_entities_processor.on_new_records.assert_not_awaited()


# ===================================================================
# WebConnector - MIME type detection
# ===================================================================

class TestWebConnectorMimeType:
    def test_determine_mime_type_html(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/page", "text/html")
        assert mime == MimeTypes.HTML
        assert ext == "html"

    def test_determine_mime_type_pdf(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.pdf", "application/pdf")
        assert mime == MimeTypes.PDF
        assert ext == "pdf"


# ===================================================================
# WebConnector - Cleanup
# ===================================================================

class TestWebConnectorCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_session(self):
        connector = _make_connector()
        mock_session = AsyncMock()
        connector.session = mock_session
        connector.visited_urls = {"https://example.com"}
        connector.retry_urls = {"https://example.com/404": RetryUrl(url="url", status="PENDING", status_code=404, retries=0, last_attempted=0)}

        await connector.cleanup()
        mock_session.close.assert_awaited_once()
        assert connector.session is None

    @pytest.mark.asyncio
    async def test_cleanup_without_session(self):
        connector = _make_connector()
        connector.session = None
        await connector.cleanup()


# ===================================================================
# WebConnector - Constants / data structures
# ===================================================================

class TestWebConnectorConstants:
    def test_retryable_status_codes(self):
        assert 429 in RETRYABLE_STATUS_CODES
        assert 403 in RETRYABLE_STATUS_CODES
        assert 503 in RETRYABLE_STATUS_CODES

    def test_max_retries(self):
        assert MAX_RETRIES == 2

    def test_retry_url_dataclass(self):
        retry = RetryUrl(
            url="https://example.com/page",
            status=Status.PENDING,
            status_code=429,
            retries=1,
            last_attempted=1000,
            depth=2,
            referer="https://example.com",
        )
        assert retry.retries == 1
        assert retry.depth == 2

    def test_record_update_dataclass(self):
        update = RecordUpdate(
            record=None,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        assert update.is_new is True
        assert update.record is None

    def test_file_mime_types_mapping(self):
        assert FILE_MIME_TYPES[".pdf"] == MimeTypes.PDF
        assert FILE_MIME_TYPES[".docx"] == MimeTypes.DOCX
        assert FILE_MIME_TYPES[".html"] == MimeTypes.HTML

    def test_document_mime_types_set(self):
        assert MimeTypes.PDF.value in DOCUMENT_MIME_TYPES
        assert MimeTypes.DOCX.value in DOCUMENT_MIME_TYPES

    def test_image_mime_types_set(self):
        assert MimeTypes.PNG.value in IMAGE_MIME_TYPES
        assert MimeTypes.JPEG.value in IMAGE_MIME_TYPES


# ===================================================================
# WebConnector - get_app_users
# ===================================================================

class TestWebConnectorAppUsers:
    def test_get_app_users(self):
        connector = _make_connector()
        connector.connector_name = Connectors.WEB
        from app.models.entities import User
        users = [
            User(
                email="a@test.com",
                full_name="Alice",
                is_active=True,
                org_id="org-1",
            ),
            User(
                email="",
                full_name="NoEmail",
                is_active=True,
            ),
        ]
        app_users = connector.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].email == "a@test.com"
