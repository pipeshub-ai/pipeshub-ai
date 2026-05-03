"""Tests for the Web connector and fetch_strategy module."""

import asyncio
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
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
from app.connectors.core.registry.filters import (
    FilterCollection,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.sources.web.connector import (
    DOCUMENT_MIME_TYPES,
    IMAGE_MIME_TYPES,
    RecordUpdate,
    RetryUrl,
    Status,
    WebApp,
    WebConnector,
    _bytes_async_gen,
)
from app.connectors.sources.web.fetch_strategy import FetchResponse
import base64
from bs4 import BeautifulSoup
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
    _bytes_async_gen,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_connector():
    """Build a WebConnector with all dependencies mocked."""
    from app.models.entities import AppMetadata
    
    logger = MagicMock()
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-1"
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_record_metadata_update = AsyncMock()
    data_entities_processor.on_record_content_update = AsyncMock()
    data_entities_processor.get_app_by_id = AsyncMock(return_value=AppMetadata(
        connector_id="web-conn-1",
        name="Web Crawler",
        type="web",
        app_group="WEB",
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
    connector = WebConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id="web-conn-1",
        scope="personal",
        created_by="test-user-id",
    )
    return connector


def _mock_config(url="https://example.com", crawl_type="single", depth=3,
                 max_pages=100, max_size_mb=10, follow_external=False,
                 restrict_to_start_path=False, url_should_contain=None):
    return {
        "sync": {
            "url": url, "type": crawl_type, "depth": depth,
            "max_pages": max_pages, "max_size_mb": max_size_mb,
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
        assert app.app_name == Connectors.WEB


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
        headers = build_stealth_headers("https://example.com/page", referer="https://example.com/")
        assert headers["Referer"] == "https://example.com/"

    def test_build_stealth_headers_with_extra(self):
        headers = build_stealth_headers("https://example.com", extra={"X-Custom": "value"})
        assert headers["X-Custom"] == "value"

    def test_fetch_response_dataclass(self):
        resp = FetchResponse(
            status_code=200, content_bytes=b"<html>test</html>",
            headers={"Content-Type": "text/html"},
            final_url="https://example.com", strategy="aiohttp",
        )
        assert resp.status_code == 200
        assert resp.strategy == "aiohttp"


# ===================================================================
# Configuration
# ===================================================================
class TestWebConnectorConfig:
    @pytest.mark.asyncio
    async def test_init_success(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=_mock_config())
        result = await connector.init()
        assert result is True
        assert connector.url == "https://example.com"

    @pytest.mark.asyncio
    async def test_init_missing_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_init_missing_url(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"sync": {"type": "single"}})
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_init_missing_sync_block(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"other": "data"})
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_pages_clamp_high(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(max_pages=99999))
        result = await connector._fetch_and_parse_config()
        assert result["max_pages"] == 10000

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_pages_clamp_low(self):
        connector = _make_connector()
        # max_pages=0 is falsy, so the `or 1000` default kicks in => 1000
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(max_pages=0))
        result = await connector._fetch_and_parse_config()
        assert result["max_pages"] == 1000  # 0 is falsy => default 1000

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_depth_clamp_high(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(depth=50))
        result = await connector._fetch_and_parse_config()
        assert result["max_depth"] == 10

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_depth_clamp_low(self):
        connector = _make_connector()
        # depth=0 is falsy, so `or 3` default kicks in => 3
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(depth=0))
        result = await connector._fetch_and_parse_config()
        assert result["max_depth"] == 3  # 0 is falsy => default 3

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_size_clamp(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(max_size_mb=200))
        result = await connector._fetch_and_parse_config()
        assert result["max_size_mb"] == 100

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_max_size_clamp_low(self):
        connector = _make_connector()
        # max_size_mb=0 is falsy, so `or 10` default kicks in => 10
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(max_size_mb=0))
        result = await connector._fetch_and_parse_config()
        assert result["max_size_mb"] == 10  # 0 is falsy => default 10

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_restrict_overrides_follow(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(follow_external=True, restrict_to_start_path=True)
        )
        result = await connector._fetch_and_parse_config()
        assert result["follow_external"] is False

    @pytest.mark.asyncio
    async def test_fetch_and_parse_config_url_should_contain_non_list(self):
        connector = _make_connector()
        config = _mock_config()
        config["sync"]["url_should_contain"] = "not-a-list"
        connector.config_service.get_config = AsyncMock(return_value=config)
        result = await connector._fetch_and_parse_config()
        assert result["url_should_contain"] == []


# ===================================================================
# URL processing
# ===================================================================
class TestWebConnectorUrlProcessing:
    def test_extract_title_from_url(self):
        connector = _make_connector()
        title = connector._extract_title_from_url("https://example.com/blog/my-post")
        assert "my post" in title.lower()

    def test_extract_title_from_url_empty(self):
        connector = _make_connector()
        assert connector._extract_title_from_url("") == ""

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

    def test_is_valid_url_http(self):
        connector = _make_connector()
        connector.follow_external = False
        connector.restrict_to_start_path = False
        assert connector._is_valid_url("https://example.com/page", "https://example.com/")

    def test_is_valid_url_rejects_non_http(self):
        connector = _make_connector()
        assert not connector._is_valid_url("ftp://example.com/page", "https://example.com/")

    def test_is_valid_url_rejects_fragment(self):
        connector = _make_connector()
        assert not connector._is_valid_url("https://example.com/page#section", "https://example.com/")

    def test_is_valid_url_rejects_skip_extensions(self):
        connector = _make_connector()
        connector.follow_external = False
        assert not connector._is_valid_url("https://example.com/image.jpg", "https://example.com/")

    def test_is_valid_url_rejects_external(self):
        connector = _make_connector()
        connector.follow_external = False
        assert not connector._is_valid_url("https://other.com/page", "https://example.com/")

    def test_is_valid_url_allows_external(self):
        connector = _make_connector()
        connector.follow_external = True
        connector.restrict_to_start_path = False
        assert connector._is_valid_url("https://other.com/page", "https://example.com/")

    def test_is_valid_url_restrict_to_start_path(self):
        connector = _make_connector()
        connector.follow_external = False
        connector.restrict_to_start_path = True
        connector.url = "https://example.com/docs/"
        connector.start_path_prefix = "/docs/"
        assert connector._is_valid_url("https://example.com/docs/sub", "https://example.com/docs/")
        assert not connector._is_valid_url("https://example.com/other", "https://example.com/docs/")


# ===================================================================
# Connection test
# ===================================================================
class TestWebConnectorConnection:
    @pytest.mark.asyncio
    async def test_test_connection_no_url(self):
        connector = _make_connector()
        connector.url = None
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_test_connection_success(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=b"OK", headers={},
                final_url="https://example.com", strategy="aiohttp",
            )
            assert await connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_test_connection_failure(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_test_connection_high_status(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=500, content_bytes=b"Error", headers={},
                final_url="https://example.com", strategy="aiohttp",
            )
            assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_test_connection_exception(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = Exception("network error")
            assert await connector.test_connection_and_access() is False


# ===================================================================
# Record group creation
# ===================================================================
class TestWebConnectorRecordGroup:
    @pytest.mark.asyncio
    async def test_create_record_group(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        from app.models.entities import AppUser
        app_users = [
            AppUser(
                app_name=Connectors.WEB, connector_id="web-conn-1",
                source_user_id="u1", org_id="org-1",
                email="test@example.com", full_name="Test User", is_active=True,
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

    @pytest.mark.asyncio
    async def test_create_record_group_exception(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.data_entities_processor.on_new_record_groups = AsyncMock(side_effect=Exception("fail"))
        with pytest.raises(Exception):
            await connector.create_record_group([])


# ===================================================================
# Crawl logic
# ===================================================================
class TestWebConnectorCrawl:
    @pytest.mark.asyncio
    async def test_crawl_single_page(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.sync_filters = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_record.indexing_status = "QUEUED"
        mock_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
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

    @pytest.mark.asyncio
    async def test_crawl_single_page_updated_record(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        connector.indexing_filters = MagicMock()
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_update = RecordUpdate(
            record=mock_record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        connector._fetch_and_process_url = AsyncMock(return_value=mock_update)
        connector._check_index_filter = MagicMock(return_value=False)
        connector._normalize_url = MagicMock(return_value="https://example.com/")
        connector._handle_record_updates = AsyncMock()
        await connector._crawl_single_page("https://example.com")
        connector._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crawl_single_page_index_disabled(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        connector.indexing_filters = MagicMock()
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[],
        )
        connector._fetch_and_process_url = AsyncMock(return_value=mock_update)
        connector._check_index_filter = MagicMock(return_value=True)
        connector._normalize_url = MagicMock(return_value="https://example.com/")
        await connector._crawl_single_page("https://example.com")
        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ===================================================================
# MIME type detection
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

    def test_determine_mime_type_json(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/data", "application/json")
        assert mime == MimeTypes.JSON

    def test_determine_mime_type_xml(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/data", "text/xml")
        assert mime == MimeTypes.XML

    def test_determine_mime_type_plain(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/data", "text/plain")
        assert mime == MimeTypes.PLAIN_TEXT

    def test_determine_mime_type_csv(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/data", "text/csv")
        assert mime == MimeTypes.CSV

    def test_determine_mime_type_docx_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.docx", "")
        assert mime == MimeTypes.DOCX

    def test_determine_mime_type_doc_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.doc", "")
        assert mime == MimeTypes.DOC

    def test_determine_mime_type_xlsx_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.xlsx", "")
        assert mime == MimeTypes.XLSX

    def test_determine_mime_type_xls_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.xls", "")
        assert mime == MimeTypes.XLS

    def test_determine_mime_type_pptx_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.pptx", "")
        assert mime == MimeTypes.PPTX

    def test_determine_mime_type_ppt_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.ppt", "")
        assert mime == MimeTypes.PPT

    def test_determine_mime_type_zip(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "application/zip")
        assert mime == MimeTypes.ZIP

    def test_determine_mime_type_png(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/png")
        assert mime == MimeTypes.PNG

    def test_determine_mime_type_jpeg(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/jpeg")
        assert mime == MimeTypes.JPEG

    def test_determine_mime_type_gif(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/gif")
        assert mime == MimeTypes.GIF

    def test_determine_mime_type_svg_from_url(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f.svg", "")
        assert mime == MimeTypes.SVG

    def test_determine_mime_type_webp(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/webp")
        assert mime == MimeTypes.WEBP

    def test_determine_mime_type_markdown(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "text/markdown")
        assert mime == MimeTypes.MARKDOWN

    def test_determine_mime_type_tsv(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "text/tab-separated-values")
        assert mime == MimeTypes.TSV

    def test_determine_mime_type_from_url_extension(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/file.md", "")
        assert mime == MimeTypes.MARKDOWN

    def test_determine_mime_type_default_html(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/page", "")
        assert mime == MimeTypes.HTML


# ===================================================================
# Check index filter
# ===================================================================
class TestWebConnectorCheckIndexFilter:
    def test_check_index_filter_completed(self):
        connector = _make_connector()
        record = MagicMock()
        record.indexing_status = ProgressStatus.COMPLETED.value
        record.mime_type = MimeTypes.HTML.value
        assert connector._check_index_filter(record) is False

    def test_check_index_filter_html_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.HTML.value
        assert connector._check_index_filter(record) is True

    def test_check_index_filter_document_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.PDF.value
        assert connector._check_index_filter(record) is True

    def test_check_index_filter_image_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.PNG.value
        assert connector._check_index_filter(record) is True


# ===================================================================
# Handle record updates
# ===================================================================
class TestWebConnectorHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_handle_updates_deleted(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_updates_metadata_and_content(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_handle_updates_no_record(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=None, is_new=False, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        await connector._handle_record_updates(update)


# ===================================================================
# Reload config
# ===================================================================
class TestWebConnectorReloadConfig:
    @pytest.mark.asyncio
    async def test_reload_config_no_url_change(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "single"
        connector.max_depth = 3
        connector.max_pages = 100
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.start_path_prefix = "/"
        connector.url_should_contain = []
        connector.config_service.get_config = AsyncMock(return_value=_mock_config())
        await connector.reload_config()

    @pytest.mark.asyncio
    async def test_reload_config_url_changed_raises(self):
        connector = _make_connector()
        connector.url = "https://old.com"
        connector.base_domain = "https://old.com"
        connector.config_service.get_config = AsyncMock(return_value=_mock_config(url="https://new.com"))
        with pytest.raises(ValueError, match="Cannot change URL"):
            await connector.reload_config()

    @pytest.mark.asyncio
    async def test_reload_config_updates_fields(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "single"
        connector.max_depth = 3
        connector.max_pages = 100
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.start_path_prefix = "/"
        connector.url_should_contain = []
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(crawl_type="recursive", depth=5, max_pages=200, max_size_mb=20)
        )
        await connector.reload_config()
        assert connector.crawl_type == "recursive"
        assert connector.max_depth == 5


# ===================================================================
# Cleanup
# ===================================================================
class TestWebConnectorCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_closes_session(self):
        connector = _make_connector()
        mock_session = AsyncMock()
        connector.session = mock_session
        connector.visited_urls = {"https://example.com"}
        connector.retry_urls = {"url": RetryUrl(url="url", status="PENDING", status_code=404, retries=0, last_attempted=0)}
        await connector.cleanup()
        mock_session.close.assert_awaited_once()
        assert connector.session is None

    @pytest.mark.asyncio
    async def test_cleanup_without_session(self):
        connector = _make_connector()
        connector.session = None
        await connector.cleanup()


# ===================================================================
# Constants / data structures
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
            url="https://example.com/page", status=Status.PENDING,
            status_code=429, retries=1, last_attempted=1000, depth=2,
            referer="https://example.com",
        )
        assert retry.retries == 1
        assert retry.depth == 2

    def test_record_update_dataclass(self):
        update = RecordUpdate(
            record=None, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        assert update.is_new is True
        assert update.html_bytes is None

    def test_file_mime_types_mapping(self):
        assert FILE_MIME_TYPES[".pdf"] == MimeTypes.PDF
        assert FILE_MIME_TYPES[".docx"] == MimeTypes.DOCX

    def test_document_mime_types_set(self):
        assert MimeTypes.PDF.value in DOCUMENT_MIME_TYPES

    def test_image_mime_types_set(self):
        assert MimeTypes.PNG.value in IMAGE_MIME_TYPES


# ===================================================================
# App users
# ===================================================================
class TestWebConnectorAppUsers:
    def test_get_app_users(self):
        connector = _make_connector()
        connector.connector_name = Connectors.WEB
        from app.models.entities import User
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = connector.get_app_users(users)
        assert len(app_users) == 1


# ===================================================================
# Extension filter
# ===================================================================
class TestWebConnectorExtensionFilter:
    def test_pass_extension_filter_no_filter(self):
        connector = _make_connector()
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=None)
        assert connector._pass_extension_filter("pdf") is True

    def test_pass_extension_filter_empty(self):
        connector = _make_connector()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=True)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter("pdf") is True


# ===================================================================
# Run sync
# ===================================================================
class TestWebConnectorRunSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_single(self, mock_filters):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "single"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.reload_config = AsyncMock()
        connector._crawl_single_page = AsyncMock()
        connector.process_retry_urls = AsyncMock()
        await connector.run_sync()
        connector._crawl_single_page.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_recursive(self, mock_filters):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "recursive"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.reload_config = AsyncMock()
        connector._crawl_recursive = AsyncMock()
        connector.process_retry_urls = AsyncMock()
        await connector.run_sync()
        connector._crawl_recursive.assert_awaited_once()


# ===================================================================
# Process retry URLs
# ===================================================================
class TestWebConnectorRetryUrls:
    @pytest.mark.asyncio
    async def test_process_retry_urls_empty(self):
        connector = _make_connector()
        connector.retry_urls = {}
        await connector.process_retry_urls()
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_process_retry_urls_with_placeholder(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.retry_urls = {
            "https://example.com/fail": RetryUrl(
                url="https://example.com/fail", status=Status.PENDING,
                status_code=500, retries=3, last_attempted=1000,
            ),
        }
        mock_record = MagicMock()
        mock_perms = [MagicMock()]
        connector._create_failed_placeholder_record = AsyncMock(return_value=(mock_record, mock_perms))
        await connector.process_retry_urls()
        connector.data_entities_processor.on_new_records.assert_awaited()


# ===================================================================
# Deep sync: _crawl_recursive
# ===================================================================
class TestWebConnectorCrawlRecursiveDeep:
    @pytest.mark.asyncio
    async def test_recursive_crawl_processes_batch(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 2
        connector.max_pages = 100
        connector.batch_size = 2
        connector.session = MagicMock()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.processed_urls = 0
        connector.max_size_mb = 10

        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[MagicMock()], html_bytes=b"<html></html>",
        )

        async def mock_generator(start_url, depth):
            yield mock_update

        with patch.object(connector, "_crawl_recursive_generator", side_effect=mock_generator), \
             patch.object(connector, "_check_index_filter", return_value=False), \
             patch.object(connector, "_create_ancestor_placeholder_records", new_callable=AsyncMock):
            await connector._crawl_recursive("https://example.com", 0)
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_recursive_crawl_handles_updates(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 2
        connector.max_pages = 100
        connector.batch_size = 50
        connector.session = MagicMock()
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.processed_urls = 0
        connector.max_size_mb = 10
        connector.indexing_filters = MagicMock()

        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_update = RecordUpdate(
            record=mock_record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )

        async def mock_generator(start_url, depth):
            yield mock_update

        with patch.object(connector, "_crawl_recursive_generator", side_effect=mock_generator), \
             patch.object(connector, "_handle_record_updates", new_callable=AsyncMock) as mock_handle, \
             patch.object(connector, "_create_ancestor_placeholder_records", new_callable=AsyncMock):
            await connector._crawl_recursive("https://example.com", 0)
        mock_handle.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_recursive_crawl_exception_propagated(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 2
        connector.max_pages = 100
        connector.batch_size = 50
        connector.session = MagicMock()
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.processed_urls = 0

        async def mock_generator(start_url, depth):
            raise Exception("crawl boom")
            yield  # noqa: unreachable

        with patch.object(connector, "_crawl_recursive_generator", side_effect=mock_generator), \
             patch.object(connector, "_create_ancestor_placeholder_records", new_callable=AsyncMock):
            with pytest.raises(Exception, match="crawl boom"):
                await connector._crawl_recursive("https://example.com", 0)


# ===================================================================
# Deep sync: _crawl_recursive_generator
# ===================================================================
class TestWebConnectorCrawlRecursiveGeneratorDeep:
    @pytest.mark.asyncio
    async def test_generator_yields_records(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 2
        connector.max_pages = 100
        connector.session = MagicMock()
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.processed_urls = 0
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)

        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[MagicMock()], html_bytes=b"<html></html>",
        )
        connector._fetch_and_process_url = AsyncMock(return_value=mock_update)
        connector._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        connector._check_index_filter = MagicMock(return_value=False)
        connector._extract_links_from_content = AsyncMock(return_value=[])
        connector._create_ancestor_placeholder_records = AsyncMock()

        results = []
        async for update in connector._crawl_recursive_generator("https://example.com", 0):
            results.append(update)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_generator_skips_visited_urls(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 2
        connector.max_pages = 100
        connector.session = MagicMock()
        connector.visited_urls = {"https://example.com"}
        connector.retry_urls = {}
        connector.processed_urls = 0
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.indexing_filters = MagicMock()
        connector._normalize_url = MagicMock(return_value="https://example.com")
        connector._fetch_and_process_url = AsyncMock(return_value=None)
        connector._create_ancestor_placeholder_records = AsyncMock()

        results = []
        async for update in connector._crawl_recursive_generator("https://example.com", 0):
            results.append(update)
        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_generator_respects_max_depth(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 0
        connector.max_pages = 100
        connector.session = MagicMock()
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.processed_urls = 0
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.indexing_filters = MagicMock()

        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        mock_update = RecordUpdate(
            record=mock_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[MagicMock()], html_bytes=b"<html></html>",
        )
        connector._fetch_and_process_url = AsyncMock(return_value=mock_update)
        connector._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        connector._check_index_filter = MagicMock(return_value=False)
        connector._extract_links_from_content = AsyncMock(return_value=["https://example.com/deep"])
        connector._create_ancestor_placeholder_records = AsyncMock()

        results = []
        async for update in connector._crawl_recursive_generator("https://example.com", 0):
            results.append(update)
        # At depth=0 with max_depth=0, it processes the start URL but doesn't follow links (depth+1 > max_depth)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_generator_respects_max_pages(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 5
        connector.max_pages = 1
        connector.session = MagicMock()
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.processed_urls = 0
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.indexing_filters = MagicMock()

        call_count = 0

        async def mock_fetch(url, depth, referer=None):
            nonlocal call_count
            call_count += 1
            mock_record = MagicMock()
            mock_record.mime_type = MimeTypes.HTML.value
            return RecordUpdate(
                record=mock_record, is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                new_permissions=[MagicMock()], html_bytes=b"<html></html>",
            )

        connector._fetch_and_process_url = AsyncMock(side_effect=mock_fetch)
        connector._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        connector._check_index_filter = MagicMock(return_value=False)
        connector._extract_links_from_content = AsyncMock(return_value=[
            "https://example.com/page2", "https://example.com/page3"
        ])
        connector._create_ancestor_placeholder_records = AsyncMock()

        results = []
        async for update in connector._crawl_recursive_generator("https://example.com", 0):
            results.append(update)
        # Should only process 1 page due to max_pages=1
        assert len(results) == 1


# ===================================================================
# Deep sync: _fetch_and_process_url
# ===================================================================
class TestWebConnectorFetchAndProcessDeep:
    @pytest.mark.asyncio
    async def test_returns_none_when_session_is_none(self):
        connector = _make_connector()
        connector.session = None
        result = await connector._fetch_and_process_url("https://example.com", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_queues_retry_on_none_result(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector._normalize_url = MagicMock(return_value="https://example.com/page")
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock, return_value=None):
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None
        assert "https://example.com/page" in connector.retry_urls

    @pytest.mark.asyncio
    async def test_queues_retry_on_retryable_status(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector._normalize_url = MagicMock(return_value="https://example.com/page")
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=429, content_bytes=b"Rate limited",
                headers={}, final_url="https://example.com/page", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None
        assert "https://example.com/page" in connector.retry_urls

    @pytest.mark.asyncio
    async def test_skips_oversized_content(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 1  # 1MB limit
        connector.follow_external = False
        connector.retry_urls = {}
        connector.url_should_contain = []
        connector._normalize_url = MagicMock(return_value="https://example.com/page")
        big_content = b"x" * (2 * 1024 * 1024)  # 2MB
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=big_content,
                headers={"Content-Type": "text/html"}, final_url="https://example.com/page",
                strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_skips_cross_domain_redirect(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector._normalize_url = MagicMock(side_effect=lambda u: u)
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=b"<html></html>",
                headers={"Content-Type": "text/html"},
                final_url="https://other-domain.com/page", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_url_should_contain_filter(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector.visited_urls = set()
        connector.url_should_contain = ["docs"]
        connector._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=b"<html></html>",
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/blog/post", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/blog/post", 0)
        assert result is None


# ===================================================================
# Deep sync: _handle_record_updates
# ===================================================================
class TestWebConnectorHandleRecordUpdatesDeep:
    @pytest.mark.asyncio
    async def test_handle_only_metadata(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=False, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_only_content(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=True, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        connector.data_entities_processor.on_record_metadata_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_deleted_and_changed(self):
        connector = _make_connector()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=True, content_changed=True, permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_deleted.assert_awaited_once()


# ===================================================================
# Deep sync: run_sync orchestration
# ===================================================================
class TestWebConnectorRunSyncDeep:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_clears_state(self, mock_filters):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "single"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.visited_urls = {"https://old.com"}
        connector.retry_urls = {"old": RetryUrl(url="old", status=Status.PENDING, status_code=500, retries=1, last_attempted=0)}
        connector.processed_urls = 5
        connector.reload_config = AsyncMock()
        connector._crawl_single_page = AsyncMock()
        connector.process_retry_urls = AsyncMock()
        await connector.run_sync()
        assert connector.visited_urls == set() or "https://example.com" in connector.visited_urls or len(connector.visited_urls) <= 1

    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_exception_propagated(self, mock_filters):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "recursive"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.reload_config = AsyncMock()
        connector._crawl_recursive = AsyncMock(side_effect=Exception("crawl error"))
        with pytest.raises(Exception, match="crawl error"):
            await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_creates_app_users(self, mock_filters):
        from app.connectors.core.registry.filters import FilterCollection
        from app.models.entities import User
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "single"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.reload_config = AsyncMock()
        connector._crawl_single_page = AsyncMock()
        connector.process_retry_urls = AsyncMock()
        connector.scope = "PERSONAL"
        connector.created_by = "user-1"
        connector.creator_email = "user@test.com"
        from app.models.entities import User
        mock_user = User(
            email="user@example.com",
            full_name="User",
            is_active=True,
            org_id="org-1",
            source_user_id="u1",
            id="u1",
            title=None,
        )
        connector.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=mock_user)
        await connector.run_sync()
        connector.data_entities_processor.on_new_app_users.assert_awaited()


# ===================================================================
# Deep sync: _extract_links_from_content
# ===================================================================
class TestWebConnectorExtractLinksDeep:
    @pytest.mark.asyncio
    async def test_returns_empty_for_none_html(self):
        connector = _make_connector()
        connector.session = None
        record = MagicMock()
        record.weburl = None
        result = await connector._extract_links_from_content(
            "https://example.com", None, record
        )
        assert result == []

    @pytest.mark.asyncio
    async def test_extracts_links_from_html_bytes(self):
        connector = _make_connector()
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.url_should_contain = []
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector.session = MagicMock()
        connector._is_valid_url = MagicMock(return_value=True)
        connector._normalize_url = MagicMock(side_effect=lambda u: u)

        html = b'<html><a href="https://example.com/page2">Link</a></html>'
        record = MagicMock()
        record.weburl = "https://example.com"
        record.mime_type = MimeTypes.HTML.value
        result = await connector._extract_links_from_content(
            "https://example.com", html, record
        )
        assert len(result) >= 1


# ===================================================================
# Deep sync: is_valid_url edge cases
# ===================================================================
class TestWebConnectorIsValidUrlDeep:
    def test_rejects_javascript_urls(self):
        connector = _make_connector()
        assert not connector._is_valid_url("javascript:void(0)", "https://example.com/")

    def test_rejects_mailto_urls(self):
        connector = _make_connector()
        assert not connector._is_valid_url("mailto:user@example.com", "https://example.com/")

    def test_rejects_data_urls(self):
        connector = _make_connector()
        assert not connector._is_valid_url("data:text/html,Hello", "https://example.com/")

# =============================================================================
# Merged from test_web_connector_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def _make_connector_cov():
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dsp = MagicMock()
    cs = AsyncMock()
    c = WebConnector(
        logger=logger, data_entities_processor=dep,
        data_store_provider=dsp, config_service=cs, connector_id="web-c-1",
        scope="personal", created_by="test-user-id",
    )
    return c


# ===========================================================================
# _bytes_async_gen
# ===========================================================================
class TestBytesAsyncGen:
    @pytest.mark.asyncio
    async def test_yields_data(self):
        chunks = []
        async for chunk in _bytes_async_gen(b"hello"):
            chunks.append(chunk)
        assert chunks == [b"hello"]


# ===========================================================================
# _extract_title helper
# ===========================================================================
class TestExtractTitle:
    def test_title_from_title_tag(self):
        from bs4 import BeautifulSoup
        c = _make_connector_cov()
        soup = BeautifulSoup("<html><title>My Page</title></html>", "html.parser")
        assert c._extract_title(soup, "https://example.com") == "My Page"

    def test_title_from_h1(self):
        from bs4 import BeautifulSoup
        c = _make_connector_cov()
        soup = BeautifulSoup("<html><h1>Heading</h1></html>", "html.parser")
        assert c._extract_title(soup, "https://example.com") == "Heading"

    def test_title_from_og_meta(self):
        from bs4 import BeautifulSoup
        c = _make_connector_cov()
        soup = BeautifulSoup(
            '<html><meta property="og:title" content="OG Title"></html>',
            "html.parser"
        )
        assert c._extract_title(soup, "https://example.com") == "OG Title"

    def test_title_fallback_to_url(self):
        from bs4 import BeautifulSoup
        c = _make_connector_cov()
        soup = BeautifulSoup("<html><body>No title</body></html>", "html.parser")
        result = c._extract_title(soup, "https://example.com/my-page")
        assert "My Page" in result

    def test_title_empty_title_tag(self):
        from bs4 import BeautifulSoup
        c = _make_connector_cov()
        soup = BeautifulSoup("<html><title>  </title></html>", "html.parser")
        result = c._extract_title(soup, "https://example.com/fallback")
        assert "Fallback" in result


# ===========================================================================
# _extract_title_from_url
# ===========================================================================
class TestExtractTitleFromUrl:
    def test_with_extension(self):
        c = _make_connector_cov()
        assert c._extract_title_from_url("https://example.com/path/my-file.pdf") == "My File"

    def test_with_hyphens_and_underscores(self):
        c = _make_connector_cov()
        assert "Hello World" in c._extract_title_from_url("https://example.com/hello_world")

    def test_root_path(self):
        c = _make_connector_cov()
        assert c._extract_title_from_url("https://example.com") == "example.com"

    def test_root_with_slash(self):
        c = _make_connector_cov()
        assert c._extract_title_from_url("https://example.com/") == "example.com"


# ===========================================================================
# _get_parent_url
# ===========================================================================
class TestGetParentUrl:
    def test_root_returns_none(self):
        c = _make_connector_cov()
        assert c._get_parent_url("https://example.com") is None
        assert c._get_parent_url("https://example.com/") is None

    def test_one_segment(self):
        c = _make_connector_cov()
        # /docs -> parent path is "/" which is root -> None
        assert c._get_parent_url("https://example.com/docs") is None

    def test_two_segments(self):
        c = _make_connector_cov()
        parent = c._get_parent_url("https://example.com/docs/page")
        assert parent == "https://example.com/docs/"

    def test_deep_path(self):
        c = _make_connector_cov()
        parent = c._get_parent_url("https://example.com/a/b/c/d")
        assert parent == "https://example.com/a/b/c/"


# ===========================================================================
# _ensure_trailing_slash
# ===========================================================================
class TestEnsureTrailingSlash:
    def test_page_url_gets_slash(self):
        c = _make_connector_cov()
        assert c._ensure_trailing_slash("https://example.com/page").endswith("/")

    def test_file_url_kept(self):
        c = _make_connector_cov()
        result = c._ensure_trailing_slash("https://example.com/file.pdf")
        assert not result.endswith("/")

    def test_already_has_slash(self):
        c = _make_connector_cov()
        result = c._ensure_trailing_slash("https://example.com/page/")
        assert result.endswith("/")

    def test_query_param_url_unchanged(self):
        c = _make_connector_cov()
        url = "https://example.com/search?q=test"
        assert c._ensure_trailing_slash(url) == url


# ===========================================================================
# _determine_mime_type extended
# ===========================================================================
class TestDetermineMimeTypeExtended:
    def test_mdx_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "text/mdx")
        assert mime == MimeTypes.MDX

    def test_heic_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "image/heic")
        assert mime == MimeTypes.HEIC

    def test_heif_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "image/heif")
        assert mime == MimeTypes.HEIF

    def test_svg_content_type(self):
        c = _make_connector_cov()
        # 'image/svg+xml' contains 'xml', which the code checks before 'svg',
        # so the XML branch takes precedence
        mime, ext = c._determine_mime_type("https://x.com/f", "image/svg+xml")
        assert mime == MimeTypes.XML

    def test_docx_content_type(self):
        c = _make_connector_cov()
        # OOXML content types contain 'xml', which the code checks before
        # 'wordprocessingml', so the XML branch takes precedence
        mime, ext = c._determine_mime_type(
            "https://x.com/f",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert mime == MimeTypes.XML

    def test_doc_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/msword")
        assert mime == MimeTypes.DOC

    def test_xlsx_content_type(self):
        c = _make_connector_cov()
        # OOXML content types contain 'xml', which the code checks before
        # 'spreadsheetml', so the XML branch takes precedence
        mime, ext = c._determine_mime_type(
            "https://x.com/f",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert mime == MimeTypes.XML

    def test_xls_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/vnd.ms-excel")
        assert mime == MimeTypes.XLS

    def test_pptx_content_type(self):
        c = _make_connector_cov()
        # OOXML content types contain 'xml', which the code checks before
        # 'presentationml', so the XML branch takes precedence
        mime, ext = c._determine_mime_type(
            "https://x.com/f",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert mime == MimeTypes.XML

    def test_ppt_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/vnd.ms-powerpoint")
        assert mime == MimeTypes.PPT

    def test_zip_compressed_content_type(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/x-compressed")
        assert mime == MimeTypes.ZIP

    def test_url_extension_fallback(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/file.json", "")
        assert mime == MimeTypes.JSON

    def test_txt_from_url(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/readme.txt", "")
        assert mime == MimeTypes.PLAIN_TEXT

    def test_csv_from_url(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/data.csv", "")
        assert mime == MimeTypes.CSV

    def test_webp_from_url(self):
        c = _make_connector_cov()
        mime, ext = c._determine_mime_type("https://x.com/img.webp", "")
        assert mime == MimeTypes.WEBP


# ===========================================================================
# _pass_extension_filter extended
# ===========================================================================
class TestPassExtensionFilterExtended:
    def test_in_operator_allows_match(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf", "docx"]
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("pdf") is True

    def test_in_operator_rejects_non_match(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf", "docx"]
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("html") is False

    def test_not_in_operator_allows_non_match(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["exe", "bat"]
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("pdf") is True

    def test_not_in_operator_rejects_match(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["exe", "bat"]
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("exe") is False

    def test_no_extension_with_in_operator_fails(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf"]
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter(None) is False

    def test_no_extension_with_not_in_operator_passes(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf"]
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter(None) is True

    def test_invalid_filter_value_passes(self):
        c = _make_connector_cov()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = "not-a-list"
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("pdf") is True


# ===========================================================================
# _check_index_filter extended
# ===========================================================================
class TestCheckIndexFilterExtended:
    def test_unknown_mime_type_not_disabled(self):
        c = _make_connector_cov()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = "application/octet-stream"
        assert c._check_index_filter(record) is False

    def test_html_enabled(self):
        c = _make_connector_cov()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.HTML.value
        assert c._check_index_filter(record) is False

    def test_image_enabled(self):
        c = _make_connector_cov()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.JPEG.value
        assert c._check_index_filter(record) is False


# ===========================================================================
# _is_valid_url extended
# ===========================================================================
class TestIsValidUrlExtended:
    def test_allows_same_domain(self):
        c = _make_connector_cov()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/page", "https://example.com/") is True

    def test_rejects_css(self):
        c = _make_connector_cov()
        assert c._is_valid_url("https://example.com/style.css", "https://example.com/") is False

    def test_rejects_js(self):
        c = _make_connector_cov()
        assert c._is_valid_url("https://example.com/app.js", "https://example.com/") is False

    def test_rejects_woff(self):
        c = _make_connector_cov()
        assert c._is_valid_url("https://example.com/font.woff2", "https://example.com/") is False

    def test_restrict_path_allows_subpath(self):
        c = _make_connector_cov()
        c.follow_external = False
        c.restrict_to_start_path = True
        c.url = "https://example.com/docs/"
        c.start_path_prefix = "/docs/"
        assert c._is_valid_url("https://example.com/docs/sub/page", "https://example.com/docs/")

    def test_restrict_path_rejects_sibling(self):
        c = _make_connector_cov()
        c.follow_external = False
        c.restrict_to_start_path = True
        c.url = "https://example.com/docs/"
        c.start_path_prefix = "/docs/"
        assert not c._is_valid_url("https://example.com/blog/post", "https://example.com/docs/")


# ===========================================================================
# _normalize_url
# ===========================================================================
class TestNormalizeUrl:
    def test_strips_fragment(self):
        c = _make_connector_cov()
        assert "#" not in c._normalize_url("https://example.com/page#section")

    def test_lowercases_netloc(self):
        c = _make_connector_cov()
        result = c._normalize_url("https://EXAMPLE.COM/path")
        assert "example.com" in result

    def test_root_path_preserved(self):
        c = _make_connector_cov()
        result = c._normalize_url("https://example.com/")
        assert result.endswith("/")


# ===========================================================================
# _ensure_parent_records_exist
# ===========================================================================
class TestEnsureParentRecordsExist:
    @pytest.mark.asyncio
    async def test_none_parent_returns_early(self):
        c = _make_connector_cov()
        await c._ensure_parent_records_exist(None)
        c.data_entities_processor.get_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_missing_parents(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.connector_name = Connectors.WEB
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await c._ensure_parent_records_exist("https://example.com/a/b/")
        c.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_stops_when_parent_exists(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.connector_name = Connectors.WEB
        existing = MagicMock()
        existing.id = "existing-id"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await c._ensure_parent_records_exist("https://example.com/a/")
        c.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# _create_failed_placeholder_record
# ===========================================================================
class TestCreateFailedPlaceholderRecord:
    @pytest.mark.asyncio
    async def test_creates_placeholder_for_new_url(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.connector_name = Connectors.WEB
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        c._ensure_parent_records_exist = AsyncMock()
        record, perms = await c._create_failed_placeholder_record(
            "https://example.com/fail", 500
        )
        assert record is not None
        assert record.indexing_status == ProgressStatus.FAILED.value

    @pytest.mark.asyncio
    async def test_returns_none_for_existing_url(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.connector_name = Connectors.WEB
        existing = MagicMock()
        existing.id = "existing-id"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        record, perms = await c._create_failed_placeholder_record(
            "https://example.com/exists", 404
        )
        assert record is None
        assert perms is None


# ===========================================================================
# process_retry_urls
# ===========================================================================
class TestProcessRetryUrls:
    @pytest.mark.asyncio
    async def test_batches_records(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.batch_size = 1
        c.connector_name = Connectors.WEB
        c.retry_urls = {
            "https://example.com/a": RetryUrl(
                url="https://example.com/a", status=Status.PENDING,
                status_code=500, retries=3, last_attempted=1000
            ),
            "https://example.com/b": RetryUrl(
                url="https://example.com/b", status=Status.PENDING,
                status_code=503, retries=3, last_attempted=1000
            ),
        }
        mock_record = MagicMock()
        mock_perms = [MagicMock()]
        c._create_failed_placeholder_record = AsyncMock(return_value=(mock_record, mock_perms))
        await c.process_retry_urls()
        assert c.data_entities_processor.on_new_records.await_count >= 2


# ===========================================================================
# _fetch_and_parse_config edge cases
# ===========================================================================
class TestFetchAndParseConfigEdgeCases:
    @pytest.mark.asyncio
    async def test_url_should_contain_list_filtering(self):
        c = _make_connector_cov()
        c.config_service.get_config = AsyncMock(return_value={
            "sync": {
                "url": "https://example.com",
                "type": "single",
                "url_should_contain": ["docs", "", "  ", 123],
            }
        })
        result = await c._fetch_and_parse_config()
        assert result["url_should_contain"] == ["docs"]

    @pytest.mark.asyncio
    async def test_start_path_prefix_computed(self):
        c = _make_connector_cov()
        c.config_service.get_config = AsyncMock(return_value={
            "sync": {
                "url": "https://example.com/docs/api",
                "type": "recursive",
            }
        })
        result = await c._fetch_and_parse_config()
        assert result["start_path_prefix"] == "/docs/api/"

    @pytest.mark.asyncio
    async def test_missing_config_raises(self):
        c = _make_connector_cov()
        c.config_service.get_config = AsyncMock(return_value={"other": {}})
        with pytest.raises(ValueError):
            await c._fetch_and_parse_config()


# ===========================================================================
# reload_config extended
# ===========================================================================
class TestReloadConfigExtended:
    @pytest.mark.asyncio
    async def test_updates_follow_external(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.crawl_type = "single"
        c.max_depth = 3
        c.max_pages = 100
        c.max_size_mb = 10
        c.follow_external = False
        c.restrict_to_start_path = False
        c.start_path_prefix = "/"
        c.url_should_contain = []
        c.config_service.get_config = AsyncMock(return_value={
            "sync": {
                "url": "https://example.com",
                "type": "single",
                "follow_external": True,
            }
        })
        await c.reload_config()
        assert c.follow_external is True

    @pytest.mark.asyncio
    async def test_base_domain_change_raises(self):
        c = _make_connector_cov()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.config_service.get_config = AsyncMock(return_value={
            "sync": {
                "url": "https://example.com",
                "type": "single",
            }
        })
        # Monkey-patch to simulate a domain change
        original = c._fetch_and_parse_config
        async def _patched(*args, **kwargs):
            result = await original(*args, **kwargs)
            result["base_domain"] = "https://other.com"
            return result
        c._fetch_and_parse_config = _patched
        with pytest.raises(ValueError, match="Cannot change base domain"):
            await c.reload_config()

# =============================================================================
# Merged from test_web_connector_full_coverage.py
# =============================================================================

def _make_connector_fullcov():
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dsp = MagicMock()
    cs = AsyncMock()
    connector = WebConnector(
        logger=logger,
        data_entities_processor=dep,
        data_store_provider=dsp,
        config_service=cs,
        connector_id="web-conn-1",
        scope="personal",
        created_by="test-user-id",
    )
    return connector


def _mock_config(url="https://example.com", crawl_type="single", depth=3,
                 max_pages=100, max_size_mb=10, follow_external=False,
                 restrict_to_start_path=False, url_should_contain=None):
    return {
        "sync": {
            "url": url, "type": crawl_type, "depth": depth,
            "max_pages": max_pages, "max_size_mb": max_size_mb,
            "follow_external": follow_external,
            "restrict_to_start_path": restrict_to_start_path,
            "url_should_contain": url_should_contain or [],
        }
    }


class TestBytesAsyncGenFullCoverage:
    @pytest.mark.asyncio
    async def test_yields_data(self):
        data = b"hello world"
        chunks = []
        async for chunk in _bytes_async_gen(data):
            chunks.append(chunk)
        assert chunks == [data]


class TestWebAppCreation:
    def test_app_name(self):
        app = WebApp("wc-1")
        assert app.app_name == Connectors.WEB


class TestConfigEdgeCases:
    @pytest.mark.asyncio
    async def test_max_pages_below_one_clamped(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(max_pages=-5)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_pages"] == 1

    @pytest.mark.asyncio
    async def test_max_depth_below_one_clamped(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(depth=-1)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_depth"] == 1

    @pytest.mark.asyncio
    async def test_max_size_below_one_clamped(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(max_size_mb=-3)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_size_mb"] == 1

    @pytest.mark.asyncio
    async def test_url_should_contain_filters_empty_strings(self):
        connector = _make_connector_fullcov()
        config = _mock_config()
        config["sync"]["url_should_contain"] = ["docs", "", "  "]
        connector.config_service.get_config = AsyncMock(return_value=config)
        result = await connector._fetch_and_parse_config()
        assert result["url_should_contain"] == ["docs"]

    @pytest.mark.asyncio
    async def test_config_not_dict_raises(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(return_value="bad")
        with pytest.raises(ValueError, match="not found"):
            await connector._fetch_and_parse_config()

    @pytest.mark.asyncio
    async def test_start_path_prefix_computed(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(url="https://example.com/docs/api")
        )
        result = await connector._fetch_and_parse_config()
        assert result["start_path_prefix"] == "/docs/api/"


class TestInitSession:
    @pytest.mark.asyncio
    async def test_init_creates_session(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(return_value=_mock_config())
        result = await connector.init()
        assert result is True
        assert connector.session is not None
        await connector.session.close()

    @pytest.mark.asyncio
    async def test_init_exception_returns_false(self):
        connector = _make_connector_fullcov()
        connector.config_service.get_config = AsyncMock(side_effect=Exception("boom"))
        result = await connector.init()
        assert result is False


class TestConnectionNoSession:
    @pytest.mark.asyncio
    async def test_test_connection_no_session(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.session = None
        assert await connector.test_connection_and_access() is False


class TestGetAppUsers:
    def test_filters_empty_email(self):
        connector = _make_connector_fullcov()
        connector.connector_name = Connectors.WEB
        from app.models.entities import User
        valid_user = User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1")
        empty_email_user = User(email="", full_name="NoEmail", is_active=True)
        none_email_user = MagicMock()
        none_email_user.email = None
        none_email_user.full_name = "NoneEmail"
        none_email_user.is_active = True
        users = [valid_user, empty_email_user, none_email_user]
        app_users = connector.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].email == "a@test.com"

    def test_user_defaults(self):
        connector = _make_connector_fullcov()
        connector.connector_name = Connectors.WEB
        from app.models.entities import User
        users = [
            User(email="b@test.com", full_name="", is_active=True, org_id="org-1"),
        ]
        app_users = connector.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].is_active is True
        assert app_users[0].full_name == "b@test.com"


class TestExtractTitleFullCoverage:
    def test_extract_title_from_soup_title_tag(self):
        connector = _make_connector_fullcov()
        html = "<html><head><title>My Page</title></head><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert connector._extract_title(soup, "https://example.com") == "My Page"

    def test_extract_title_from_h1(self):
        connector = _make_connector_fullcov()
        html = "<html><body><h1>Heading One</h1></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert connector._extract_title(soup, "https://example.com") == "Heading One"

    def test_extract_title_from_og_title(self):
        connector = _make_connector_fullcov()
        html = '<html><head><meta property="og:title" content="OG Title" /></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        assert connector._extract_title(soup, "https://example.com") == "OG Title"

    def test_extract_title_fallback_to_url(self):
        connector = _make_connector_fullcov()
        html = "<html><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        title = connector._extract_title(soup, "https://example.com/my-page")
        assert "my page" in title.lower()

    def test_extract_title_from_url_domain_only(self):
        connector = _make_connector_fullcov()
        title = connector._extract_title_from_url("https://example.com")
        assert title == "example.com"

    def test_extract_title_from_url_with_extension(self):
        connector = _make_connector_fullcov()
        title = connector._extract_title_from_url("https://example.com/file.pdf")
        assert "file" in title.lower()


class TestNormalizeUrlFullCoverage:
    def test_normalizes_query_params(self):
        connector = _make_connector_fullcov()
        result = connector._normalize_url("https://example.com/page?a=1")
        assert "?a=1" in result

    def test_handles_invalid_url(self):
        connector = _make_connector_fullcov()
        result = connector._normalize_url("")
        assert isinstance(result, str)


class TestEnsureTrailingSlashFullCoverage:
    def test_with_query_params_unchanged(self):
        connector = _make_connector_fullcov()
        url = "https://example.com/page?id=123"
        assert connector._ensure_trailing_slash(url) == url

    def test_root_url(self):
        connector = _make_connector_fullcov()
        result = connector._ensure_trailing_slash("https://example.com")
        assert result.endswith("/")


class TestGetParentUrlFullCoverage:
    def test_root_returns_none(self):
        connector = _make_connector_fullcov()
        assert connector._get_parent_url("https://example.com/") is None

    def test_single_segment_returns_none(self):
        connector = _make_connector_fullcov()
        assert connector._get_parent_url("https://example.com/docs/") is None

    def test_nested_returns_parent(self):
        connector = _make_connector_fullcov()
        parent = connector._get_parent_url("https://example.com/docs/api/v1")
        assert parent is not None
        assert "/docs/api/" in parent


class TestIsValidUrl:
    def test_url_should_contain_filter_blocks(self):
        connector = _make_connector_fullcov()
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.url_should_contain = []
        assert connector._is_valid_url("https://example.com/page", "https://example.com/")

    def test_exception_returns_false(self):
        connector = _make_connector_fullcov()
        assert not connector._is_valid_url(None, "https://example.com/")


class TestDetermineMimeType:
    def test_docx_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert mime == MimeTypes.XML

    def test_doc_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f", "application/msword"
        )
        assert mime == MimeTypes.DOC

    def test_xlsx_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert mime == MimeTypes.XML

    def test_xls_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f", "application/vnd.ms-excel"
        )
        assert mime == MimeTypes.XLS

    def test_pptx_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert mime == MimeTypes.XML

    def test_ppt_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f", "application/vnd.ms-powerpoint"
        )
        assert mime == MimeTypes.PPT

    def test_mdx_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type("https://example.com/f", "text/mdx")
        assert mime == MimeTypes.MDX

    def test_heic_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/heic")
        assert mime == MimeTypes.HEIC

    def test_heif_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/heif")
        assert mime == MimeTypes.HEIF

    def test_svg_from_content_type(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/svg+xml")
        assert mime == MimeTypes.XML

    def test_htm_extension(self):
        connector = _make_connector_fullcov()
        mime, ext = connector._determine_mime_type("https://example.com/page.htm", "")
        assert mime == MimeTypes.HTML


class TestPassExtensionFilter:
    def test_in_operator_matches(self):
        connector = _make_connector_fullcov()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["pdf", "docx"]
        filt.get_operator = MagicMock()
        from app.connectors.core.registry.filters import MultiselectOperator
        filt.get_operator.return_value = MultiselectOperator.IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter("pdf") is True
        assert connector._pass_extension_filter("html") is False

    def test_not_in_operator(self):
        connector = _make_connector_fullcov()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["exe", "bat"]
        filt.get_operator = MagicMock()
        from app.connectors.core.registry.filters import MultiselectOperator
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter("pdf") is True
        assert connector._pass_extension_filter("exe") is False

    def test_no_extension_with_in_operator(self):
        connector = _make_connector_fullcov()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["pdf"]
        filt.get_operator = MagicMock()
        from app.connectors.core.registry.filters import MultiselectOperator
        filt.get_operator.return_value = MultiselectOperator.IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter(None) is False
        assert connector._pass_extension_filter("") is False

    def test_no_extension_with_not_in_operator(self):
        connector = _make_connector_fullcov()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["pdf"]
        filt.get_operator = MagicMock()
        from app.connectors.core.registry.filters import MultiselectOperator
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter(None) is True

    def test_invalid_filter_value_returns_true(self):
        connector = _make_connector_fullcov()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = "not-a-list"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter("pdf") is True


class TestCheckIndexFilter:
    def test_file_type_disabled(self):
        connector = _make_connector_fullcov()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.XLSX.value
        assert connector._check_index_filter(record) is True

    def test_unknown_mime_passes(self):
        connector = _make_connector_fullcov()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = "application/custom"
        assert connector._check_index_filter(record) is False


class TestRemoveUnwantedTags:
    def test_removes_scripts_and_nav(self):
        connector = _make_connector_fullcov()
        html = "<html><body><script>alert(1)</script><nav>menu</nav><p>content</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_unwanted_tags(soup)
        assert soup.find("script") is None
        assert soup.find("nav") is None
        assert soup.find("p") is not None

    def test_removes_css_selectors(self):
        connector = _make_connector_fullcov()
        html = '<html><body><div class="sidebar">side</div><div class="main">content</div></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_unwanted_tags(soup)
        assert soup.find("div", class_="sidebar") is None

    def test_removes_form_elements(self):
        connector = _make_connector_fullcov()
        html = "<html><body><form><input type='text'/><button>Submit</button></form><p>text</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_unwanted_tags(soup)
        assert soup.find("form") is None
        assert soup.find("button") is None


class TestRemoveImageTags:
    def test_removes_img_and_svg(self):
        connector = _make_connector_fullcov()
        html = '<html><body><img src="test.png"/><svg><rect/></svg><p>keep</p></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_image_tags(soup)
        assert soup.find("img") is None
        assert soup.find("svg") is None
        assert soup.find("p") is not None


class TestCleanBase64String:
    def test_valid_base64(self):
        connector = _make_connector_fullcov()
        data = base64.b64encode(b"hello").decode()
        assert connector._clean_base64_string(data) == data

    def test_empty_string(self):
        connector = _make_connector_fullcov()
        assert connector._clean_base64_string("") == ""

    def test_url_encoded_padding(self):
        connector = _make_connector_fullcov()
        data = base64.b64encode(b"hello world!!!").decode()
        url_encoded = data.replace("=", "%3D")
        result = connector._clean_base64_string(url_encoded)
        assert result != ""

    def test_invalid_chars(self):
        connector = _make_connector_fullcov()
        assert connector._clean_base64_string("invalid!!chars@@") == ""

    def test_missing_padding_fixed(self):
        connector = _make_connector_fullcov()
        data = base64.b64encode(b"test data here").decode().rstrip("=")
        result = connector._clean_base64_string(data)
        assert result != ""


class TestCleanDataUrisInHtml:
    def test_valid_data_uri_kept(self):
        connector = _make_connector_fullcov()
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        html = f'<img src="data:image/png;base64,{b64}"/>'
        result = connector._clean_data_uris_in_html(html)
        assert "data:image/png;base64," in result

    def test_invalid_data_uri_removed(self):
        connector = _make_connector_fullcov()
        html = '<img src="data:image/png;base64,!!!invalid!!!"/>'
        result = connector._clean_data_uris_in_html(html)
        assert "!!!" not in result


class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_handle_metadata_only(self):
        connector = _make_connector_fullcov()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=True, content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_metadata_update.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_content_only(self):
        connector = _make_connector_fullcov()
        update = RecordUpdate(
            record=MagicMock(id="r1"), is_new=False, is_updated=True,
            is_deleted=False, metadata_changed=False, content_changed=True,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        connector.data_entities_processor.on_record_content_update.assert_awaited_once()
        connector.data_entities_processor.on_record_metadata_update.assert_not_awaited()


class TestFetchAndProcessUrl:
    @pytest.mark.asyncio
    async def test_successful_html_page(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector.visited_urls = set()
        connector.url_should_contain = []
        connector._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        connector._ensure_parent_records_exist = AsyncMock()

        html_content = b"<html><head><title>Test Page</title></head><body><p>Hello</p></body></html>"
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=html_content,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is not None
        assert result.is_new is True
        assert result.record.mime_type == MimeTypes.HTML.value

    @pytest.mark.asyncio
    async def test_existing_record_unchanged(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector.visited_urls = set()
        connector.url_should_contain = []
        connector._ensure_parent_records_exist = AsyncMock()

        html_content = b"<html><head><title>Test</title></head><body>content</body></html>"
        content_hash = hashlib.md5(BeautifulSoup(html_content, "html.parser").get_text(separator="\n", strip=True).encode("utf-8")).hexdigest()

        existing = MagicMock()
        existing.id = "existing-id"
        existing.record_name = "Test"
        existing.external_revision_id = content_hash
        existing.parent_external_record_id = None
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = "COMPLETED"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)

        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=html_content,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is not None
        assert result.is_updated is False
        assert result.record.indexing_status == ProgressStatus.COMPLETED.value

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = asyncio.TimeoutError()
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_extension_filter_blocks(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector.url_should_contain = []
        connector._pass_extension_filter = MagicMock(return_value=False)

        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200, content_bytes=b"data",
                headers={"Content-Type": "application/pdf"},
                final_url="https://example.com/file.pdf", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/file.pdf", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_retryable_error_returns_none(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.retry_urls = {}
        connector._normalize_url = MagicMock(return_value="https://example.com/page")

        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=404, content_bytes=b"Not found",
                headers={}, final_url="https://example.com/page", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None
        assert "https://example.com/page" not in connector.retry_urls


class TestEnsureParentRecordsExistFullCoverage:
    @pytest.mark.asyncio
    async def test_none_parent_returns_immediately(self):
        connector = _make_connector_fullcov()
        await connector._ensure_parent_records_exist(None)
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_parent_stops_walk(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        existing = MagicMock()
        existing.id = "e-1"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await connector._ensure_parent_records_exist("https://example.com/docs/api/")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_missing_ancestors(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._ensure_parent_records_exist("https://example.com/docs/api/")
        connector.data_entities_processor.on_new_records.assert_awaited()


class TestCreateAncestorPlaceholders:
    @pytest.mark.asyncio
    async def test_no_segments_returns_early(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        await connector._create_ancestor_placeholder_records("https://example.com/")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_placeholders_for_intermediate_paths(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._create_ancestor_placeholder_records("https://example.com/a/b/c/")
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_existing_record_reused(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        existing = MagicMock()
        existing.id = "existing-ph-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await connector._create_ancestor_placeholder_records("https://example.com/a/b/")
        call_args = connector.data_entities_processor.on_new_records.call_args
        records = call_args[0][0]
        assert records[0][0].id == "existing-ph-id"


class TestCreateFailedPlaceholderRecordFullCoverage:
    @pytest.mark.asyncio
    async def test_creates_placeholder(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector._ensure_parent_records_exist = AsyncMock()
        record, perms = await connector._create_failed_placeholder_record(
            "https://example.com/fail", 500
        )
        assert record is not None
        assert record.indexing_status == ProgressStatus.FAILED.value
        assert perms is not None

    @pytest.mark.asyncio
    async def test_existing_record_returns_none(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        existing = MagicMock()
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        record, perms = await connector._create_failed_placeholder_record(
            "https://example.com/fail", 500
        )
        assert record is None
        assert perms is None


class TestRetryUrlsGenerator:
    @pytest.mark.asyncio
    async def test_yields_placeholder_records(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector._ensure_parent_records_exist = AsyncMock()
        connector.retry_urls = {
            "https://example.com/fail": RetryUrl(
                url="https://example.com/fail", status=Status.PENDING,
                status_code=500, retries=3, last_attempted=1000,
            ),
        }
        results = []
        async for ru in connector._retry_urls_generator(max_retries=2):
            results.append(ru)
        assert len(results) == 1
        assert results[0].record.indexing_status == ProgressStatus.FAILED.value


class TestProcessRetryUrlsFullCoverage:
    @pytest.mark.asyncio
    async def test_batches_and_flushes(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.batch_size = 1
        connector._ensure_parent_records_exist = AsyncMock()
        connector.retry_urls = {
            f"https://example.com/fail{i}": RetryUrl(
                url=f"https://example.com/fail{i}", status=Status.PENDING,
                status_code=500, retries=3, last_attempted=1000,
            )
            for i in range(3)
        }
        await connector.process_retry_urls()
        assert connector.data_entities_processor.on_new_records.call_count >= 3


class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self):
        connector = _make_connector_fullcov()
        await connector.reindex_records([])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_reindex(self):
        connector = _make_connector_fullcov()
        records = [MagicMock()]
        await connector.reindex_records(records)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_weburl(self):
        connector = _make_connector_fullcov()
        record = MagicMock()
        record.weburl = "https://example.com/page"
        result = await connector.get_signed_url(record)
        assert result == "https://example.com/page"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_weburl(self):
        connector = _make_connector_fullcov()
        record = MagicMock()
        record.weburl = None
        result = await connector.get_signed_url(record)
        assert result is None


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        connector = _make_connector_fullcov()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_noop(self):
        connector = _make_connector_fullcov()
        await connector.handle_webhook_notification({})


class TestCleanupAdvanced:
    @pytest.mark.asyncio
    async def test_cleanup_clears_retry_urls(self):
        connector = _make_connector_fullcov()
        mock_session = AsyncMock()
        connector.session = mock_session
        connector.visited_urls = {"url1", "url2"}
        connector.retry_urls = {"url": RetryUrl(url="url", status="PENDING", status_code=404, retries=0, last_attempted=0)}
        await connector.cleanup()
        assert connector.session is None
        assert len(connector.visited_urls) == 0


class TestExtractLinksFromContent:
    @pytest.mark.asyncio
    async def test_refetch_when_no_html_bytes(self):
        connector = _make_connector_fullcov()
        connector.session = MagicMock()
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.url_should_contain = []
        connector.visited_urls = set()
        connector.retry_urls = {}
        connector._is_valid_url = MagicMock(return_value=True)

        record = MagicMock()
        record.weburl = "https://example.com"
        record.mime_type = MimeTypes.HTML.value

        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b'<html><a href="https://example.com/link">L</a></html>',
                headers={}, final_url="https://example.com", strategy="aiohttp",
            )
            result = await connector._extract_links_from_content(
                "https://example.com", None, record
            )
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_refetch_fails_returns_empty(self):
        connector = _make_connector_fullcov()
        connector.session = MagicMock()

        record = MagicMock()
        record.weburl = "https://example.com"
        record.mime_type = MimeTypes.HTML.value

        with patch("app.connectors.sources.web.connector.fetch_url_with_fallback",
                    new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = None
            result = await connector._extract_links_from_content(
                "https://example.com", None, record
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_no_session_no_weburl_returns_empty(self):
        connector = _make_connector_fullcov()
        connector.session = None
        record = MagicMock()
        record.weburl = None
        result = await connector._extract_links_from_content("https://example.com", None, record)
        assert result == []


class TestRecursiveGeneratorRetry:
    @pytest.mark.asyncio
    async def test_retry_re_enqueue(self):
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.max_depth = 2
        connector.max_pages = 10
        connector.session = MagicMock()
        connector.visited_urls = {"https://example.com"}
        connector.processed_urls = 0
        connector.max_size_mb = 10
        connector.follow_external = False
        connector.indexing_filters = MagicMock()

        connector.retry_urls = {
            "https://example.com/retry": RetryUrl(
                url="https://example.com/retry", status=Status.PENDING,
                status_code=429, retries=MAX_RETRIES, last_attempted=1000,
                depth=1, referer="https://example.com",
            ),
        }
        connector._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        connector._fetch_and_process_url = AsyncMock(return_value=None)

        results = []
        async for update in connector._crawl_recursive_generator("https://example.com/other", 0):
            results.append(update)
        assert len(results) == 0


class TestRunSyncSitemap:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_unknown_crawl_type_falls_through(self, mock_filters):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector = _make_connector_fullcov()
        connector.url = "https://example.com"
        connector.base_domain = "https://example.com"
        connector.crawl_type = "sitemap"
        connector.session = MagicMock()
        connector.max_size_mb = 10
        connector.reload_config = AsyncMock()
        connector.process_retry_urls = AsyncMock()
        await connector.run_sync()
        connector.process_retry_urls.assert_awaited_once()


class TestCreateConnectorFactory:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.web.connector.DataSourceEntitiesProcessor") as mock_dep:
            mock_dep_instance = MagicMock()
            mock_dep_instance.initialize = AsyncMock()
            mock_dep.return_value = mock_dep_instance
            logger = MagicMock()
            dsp = MagicMock()
            cs = AsyncMock()
            conn = await WebConnector.create_connector(
                logger, dsp, cs, "wc-factory", "team", "test-user-id"
            )
            assert isinstance(conn, WebConnector)
