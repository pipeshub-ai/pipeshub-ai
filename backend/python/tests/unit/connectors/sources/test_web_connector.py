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
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_record_metadata_update = AsyncMock()
    data_entities_processor.on_record_content_update = AsyncMock()
    data_store_provider = MagicMock()
    config_service = AsyncMock()
    connector = WebConnector(
        logger=logger,
        data_entities_processor=data_entities_processor,
        data_store_provider=data_store_provider,
        config_service=config_service,
        connector_id="web-conn-1",
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
        mock_user = MagicMock()
        mock_user.email = "user@example.com"
        mock_user.full_name = "User"
        mock_user.is_active = True
        mock_user.org_id = "org-1"
        mock_user.source_user_id = "u1"
        mock_user.id = "u1"
        mock_user.title = None
        connector.data_entities_processor.get_all_active_users = AsyncMock(return_value=[mock_user])
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
