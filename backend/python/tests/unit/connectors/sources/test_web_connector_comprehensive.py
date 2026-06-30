"""Comprehensive tests for Web connector - extended coverage for uncovered methods."""

import base64
import hashlib
from contextlib import asynccontextmanager
from io import BytesIO
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, FILE_MIME_TYPES, MimeTypes, OriginTypes, ProgressStatus
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import (
    FilterCollection,
    MultiselectOperator,
)
from app.connectors.sources.web.connector import (
    DOCUMENT_MIME_TYPES,
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
from app.connectors.sources.web.fetch_strategy import FetchResponse
from app.models.entities import RecordType
from app.models.permission import EntityType, Permission, PermissionType
from PIL import Image as PILImage

_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z5BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
_MINIMAL_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="red"/></svg>'
)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def _make_connector(scope: str = "personal", created_by: str = "test-user-id"):
    logger = MagicMock()
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_user_by_user_id = AsyncMock(return_value=None)
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    dsp = MagicMock()

    mock_tx = AsyncMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.ensure_team_app_edge = AsyncMock()

    @asynccontextmanager
    async def _transaction():
        yield mock_tx

    dsp.transaction = _transaction
    dsp._mock_tx = mock_tx

    cs = AsyncMock()
    c = WebConnector(
        logger=logger, data_entities_processor=dep,
        data_store_provider=dsp, config_service=cs, connector_id="web-comp-1",
        scope=scope, created_by=created_by,
    )
    c.record_sync_point.read_sync_point = AsyncMock(return_value={})
    c.record_sync_point.update_sync_point = AsyncMock(return_value={})
    return c


def _mock_config(**overrides):
    base = {
        "sync": {
            "url": "https://example.com",
            "type": "single",
            "depth": 3,
            "max_pages": 100,
            "max_size_mb": 10,
            "follow_external": False,
            "restrict_to_start_path": False,
            "url_should_contain": [],
        }
    }
    base["sync"].update(overrides)
    return base


# ===========================================================================
# WebApp
# ===========================================================================
class TestWebApp:
    def test_constructor(self):
        app = WebApp("conn-1")
        assert app.connector_id == "conn-1"


# ===========================================================================
# RecordUpdate
# ===========================================================================
class TestRecordUpdateComprehensive:
    def test_defaults(self):
        ru = RecordUpdate(
            record=None, is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False,
            content_changed=False, permissions_changed=False,
        )
        assert ru.html_bytes is None
        assert ru.external_record_id is None

    def test_with_html_bytes(self):
        ru = RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False,
            is_deleted=False, metadata_changed=False,
            content_changed=False, permissions_changed=False,
            html_bytes=b"<html>test</html>",
        )
        assert ru.html_bytes == b"<html>test</html>"


# ===========================================================================
# RetryUrl
# ===========================================================================
class TestRetryUrl:
    def test_construction(self):
        ru = RetryUrl(
            url="https://example.com",
            status="PENDING",
            status_code=429,
            retries=1,
            last_attempted=1000,
            depth=2,
            referer="https://example.com/parent"
        )
        assert ru.url == "https://example.com"
        assert ru.depth == 2
        assert ru.referer == "https://example.com/parent"

    def test_defaults(self):
        ru = RetryUrl(
            url="https://example.com",
            status="PENDING",
            status_code=500,
            retries=0,
            last_attempted=0,
        )
        assert ru.depth == 0
        assert ru.referer is None


# ===========================================================================
# Status enum
# ===========================================================================
class TestStatusEnum:
    def test_pending(self):
        assert Status.PENDING.value == "PENDING"


# ===========================================================================
# _bytes_async_gen
# ===========================================================================
class TestBytesAsyncGenComprehensive:
    @pytest.mark.asyncio
    async def test_yields_data(self):
        chunks = []
        async for chunk in _bytes_async_gen(b"hello world"):
            chunks.append(chunk)
        assert chunks == [b"hello world"]

    @pytest.mark.asyncio
    async def test_empty_bytes(self):
        chunks = []
        async for chunk in _bytes_async_gen(b""):
            chunks.append(chunk)
        assert chunks == [b""]


# ===========================================================================
# _extract_title
# ===========================================================================
class TestExtractTitleComprehensive:
    def test_from_title_tag(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><title>My Page</title></html>", "html.parser")
        assert c._extract_title(soup, "https://example.com") == "My Page"

    def test_from_h1(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><h1>Heading</h1></html>", "html.parser")
        assert c._extract_title(soup, "https://example.com") == "Heading"

    def test_from_og_title(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup(
            '<html><meta property="og:title" content="OG Title"></html>',
            "html.parser"
        )
        assert c._extract_title(soup, "https://example.com") == "OG Title"

    def test_fallback_to_url(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><body>No title</body></html>", "html.parser")
        result = c._extract_title(soup, "https://example.com/my-page")
        assert "My Page" in result

    def test_empty_title_tag_falls_through(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><title>  </title><h1>H1 Title</h1></html>", "html.parser")
        result = c._extract_title(soup, "https://example.com")
        assert result == "H1 Title"


# ===========================================================================
# _extract_title_from_url
# ===========================================================================
class TestExtractTitleFromUrlComprehensive:
    def test_normal_path(self):
        c = _make_connector()
        result = c._extract_title_from_url("https://example.com/my-page")
        assert result == "My Page"

    def test_with_extension(self):
        c = _make_connector()
        result = c._extract_title_from_url("https://example.com/report.pdf")
        assert result == "Report"

    def test_with_underscores(self):
        c = _make_connector()
        result = c._extract_title_from_url("https://example.com/my_great_page")
        assert result == "My Great Page"

    def test_root_path(self):
        c = _make_connector()
        result = c._extract_title_from_url("https://example.com")
        assert result == "example.com"

    def test_deep_path(self):
        c = _make_connector()
        result = c._extract_title_from_url("https://example.com/a/b/c/my-page")
        assert result == "My Page"


# ===========================================================================
# _get_parent_url
# ===========================================================================
class TestGetParentUrlComprehensive:
    def test_root_returns_none(self):
        c = _make_connector()
        assert c._get_parent_url("https://example.com") is None

    def test_root_with_slash_returns_none(self):
        c = _make_connector()
        assert c._get_parent_url("https://example.com/") is None

    def test_one_segment_returns_none(self):
        c = _make_connector()
        result = c._get_parent_url("https://example.com/page/")
        # Parent would be "/" which is None
        assert result is None

    def test_two_segments(self):
        c = _make_connector()
        result = c._get_parent_url("https://example.com/docs/page/")
        assert result is not None
        assert "/docs/" in result

    def test_deep_path(self):
        c = _make_connector()
        result = c._get_parent_url("https://example.com/a/b/c/page/")
        assert result is not None
        assert "/a/b/c/" in result


# ===========================================================================
# _ensure_trailing_slash
# ===========================================================================
class TestEnsureTrailingSlashComprehensive:
    def test_page_url_gets_slash(self):
        c = _make_connector()
        result = c._ensure_trailing_slash("https://example.com/page")
        assert result.endswith("/")

    def test_file_url_unchanged(self):
        c = _make_connector()
        result = c._ensure_trailing_slash("https://example.com/doc.pdf")
        assert result == "https://example.com/doc.pdf"

    def test_already_has_slash(self):
        c = _make_connector()
        result = c._ensure_trailing_slash("https://example.com/page/")
        assert result.endswith("/")

    def test_query_param_url_unchanged(self):
        c = _make_connector()
        url = "https://example.com/page?id=123"
        result = c._ensure_trailing_slash(url)
        assert result == url


# ===========================================================================
# _normalize_url
# ===========================================================================
class TestNormalizeUrlComprehensive:
    def test_strips_fragment(self):
        c = _make_connector()
        result = c._normalize_url("https://example.com/page#section")
        assert "#" not in result

    def test_lowercases_netloc(self):
        c = _make_connector()
        result = c._normalize_url("https://EXAMPLE.COM/page")
        assert "example.com" in result

    def test_strips_trailing_slash(self):
        c = _make_connector()
        result = c._normalize_url("https://example.com/page/")
        assert result.endswith("/page")

    def test_root_path_preserved(self):
        c = _make_connector()
        result = c._normalize_url("https://example.com/")
        assert result.endswith("/")

    def test_preserves_query_params(self):
        c = _make_connector()
        result = c._normalize_url("https://example.com/page?key=value")
        assert "key=value" in result


# ===========================================================================
# _determine_mime_type
# ===========================================================================
class TestDetermineMimeTypeComprehensive:
    def test_html_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com", "text/html")
        assert mime == MimeTypes.HTML
        assert ext == "html"

    def test_pdf_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/doc.pdf", "application/pdf")
        assert mime == MimeTypes.PDF
        assert ext == "pdf"

    def test_json_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/data.json", "application/json")
        assert mime == MimeTypes.JSON

    def test_xml_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/data.xml", "text/xml")
        assert mime == MimeTypes.XML

    def test_plain_text_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/text.txt", "text/plain")
        assert mime == MimeTypes.PLAIN_TEXT

    def test_csv_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/data.csv", "text/csv")
        assert mime == MimeTypes.CSV

    def test_tsv_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/data.tsv", "text/tab-separated-values")
        assert mime == MimeTypes.TSV

    def test_markdown_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/file.md", "text/markdown")
        assert mime == MimeTypes.MARKDOWN

    def test_png_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/img.png", "image/png")
        assert mime == MimeTypes.PNG

    def test_jpeg_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/img.jpg", "image/jpeg")
        assert mime == MimeTypes.JPEG

    def test_gif_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/img.gif", "image/gif")
        assert mime == MimeTypes.GIF

    def test_svg_content_type(self):
        c = _make_connector()
        # "image/svg" matches the svg check in the code
        mime, ext = c._determine_mime_type("https://example.com/img.svg", "image/svg")
        assert mime == MimeTypes.SVG

    def test_webp_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/img.webp", "image/webp")
        assert mime == MimeTypes.WEBP

    def test_heic_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/img.heic", "image/heic")
        assert mime == MimeTypes.HEIC

    def test_heif_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/img.heif", "image/heif")
        assert mime == MimeTypes.HEIF

    def test_docx_content_type_via_url(self):
        # Note: The openxml content-type contains 'xml' which matches earlier in the code.
        # DOCX is detected via URL extension fallback or via msword content-type.
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/doc.docx", "")
        assert mime == MimeTypes.DOCX

    def test_doc_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("", "application/msword")
        assert mime == MimeTypes.DOC

    def test_xlsx_content_type_via_url(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/data.xlsx", "")
        assert mime == MimeTypes.XLSX

    def test_xls_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("", "application/vnd.ms-excel")
        assert mime == MimeTypes.XLS

    def test_pptx_content_type_via_url(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/slides.pptx", "")
        assert mime == MimeTypes.PPTX

    def test_ppt_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("", "application/vnd.ms-powerpoint")
        assert mime == MimeTypes.PPT

    def test_zip_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("", "application/zip")
        assert mime == MimeTypes.ZIP

    def test_url_extension_fallback_pdf(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/report.pdf", "")
        assert mime == MimeTypes.PDF
        assert ext == "pdf"

    def test_url_extension_fallback_md(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/README.md", "")
        assert mime == MimeTypes.MARKDOWN

    def test_default_to_html(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://example.com/page", "")
        assert mime == MimeTypes.HTML

    def test_mdx_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("", "text/mdx")
        assert mime == MimeTypes.MDX


# ===========================================================================
# _pass_extension_filter
# ===========================================================================
class TestPassExtensionFilterComprehensive:
    def test_no_filter_passes(self):
        c = _make_connector()
        c.sync_filters = FilterCollection()
        assert c._pass_extension_filter("pdf") is True

    def test_in_operator_match(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "docx"]
        mock_filter.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter("pdf") is True

    def test_in_operator_no_match(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter("xlsx") is False

    def test_not_in_operator(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe"]
        mock_filter.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter("pdf") is True
        assert c._pass_extension_filter("exe") is False

    def test_no_extension_with_in_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter(None) is False

    def test_no_extension_with_not_in_passes(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter(None) is True

    def test_empty_extension_with_in_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter("") is False

    def test_invalid_filter_value(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not-a-list"
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = mock_filter
        assert c._pass_extension_filter("pdf") is True


# ===========================================================================
# _check_index_filter
# ===========================================================================
class TestCheckIndexFilterComprehensive:
    def test_html_enabled(self):
        c = _make_connector()
        c.indexing_filters = FilterCollection()
        record = MagicMock()
        record.mime_type = MimeTypes.HTML.value
        record.indexing_status = None
        result = c._check_index_filter(record)
        assert result is False

    def test_already_completed(self):
        c = _make_connector()
        record = MagicMock()
        record.mime_type = MimeTypes.HTML.value
        record.indexing_status = ProgressStatus.COMPLETED.value
        result = c._check_index_filter(record)
        assert result is False

    def test_document_type(self):
        c = _make_connector()
        c.indexing_filters = FilterCollection()
        record = MagicMock()
        record.mime_type = MimeTypes.PDF.value
        record.indexing_status = None
        result = c._check_index_filter(record)
        assert result is False

    def test_image_type(self):
        c = _make_connector()
        c.indexing_filters = FilterCollection()
        record = MagicMock()
        record.mime_type = MimeTypes.PNG.value
        record.indexing_status = None
        result = c._check_index_filter(record)
        assert result is False


# ===========================================================================
# _is_valid_url
# ===========================================================================
class TestIsValidUrlComprehensive:
    def test_same_domain(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/page", "https://example.com") is True

    def test_external_domain_blocked(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://other.com/page", "https://example.com") is False

    def test_external_domain_allowed(self):
        c = _make_connector()
        c.follow_external = True
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://other.com/page", "https://example.com") is True

    def test_rejects_non_http(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("ftp://example.com/file", "https://example.com") is False

    def test_rejects_fragment(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/page#section", "https://example.com") is False

    def test_rejects_css(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/style.css", "https://example.com") is False

    def test_rejects_js(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/app.js", "https://example.com") is False

    def test_rejects_image_extensions(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/img.png", "https://example.com") is False
        assert c._is_valid_url("https://example.com/img.jpg", "https://example.com") is False

    def test_restrict_path_allows_subpath(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = True
        c.url = "https://example.com/docs/"
        c.start_path_prefix = "/docs/"
        assert c._is_valid_url("https://example.com/docs/page", "https://example.com/docs/") is True

    def test_restrict_path_rejects_sibling(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = True
        c.url = "https://example.com/docs/"
        c.start_path_prefix = "/docs/"
        assert c._is_valid_url("https://example.com/other/page", "https://example.com/docs/") is False

    def test_invalid_url_returns_false(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        # Should not raise
        result = c._is_valid_url("not-a-url", "https://example.com")
        assert result is False


# ===========================================================================
# _ensure_parent_records_exist
# ===========================================================================
class TestEnsureParentRecordsExist:
    @pytest.mark.asyncio
    async def test_none_parent_returns_early(self):
        c = _make_connector()
        await c._ensure_parent_records_exist(None)
        c.data_entities_processor.on_new_records.assert_not_awaited()


# ===========================================================================
# Misc
# ===========================================================================
class TestMiscComprehensive:
    @pytest.mark.asyncio
    async def test_cleanup(self):
        c = _make_connector()
        await c.cleanup()

    @pytest.mark.asyncio
    async def test_reindex_records_empty(self):
        c = _make_connector()
        await c.reindex_records([])

    @pytest.mark.asyncio
    async def test_handle_webhook_notification(self):
        c = _make_connector()
        await c.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_get_signed_url_returns_none(self):
        c = _make_connector()
        record = MagicMock(weburl=None)
        result = await c.get_signed_url(record)
        assert result is None

    def test_clean_base64_string(self):
        c = _make_connector()
        # Valid base64
        result = c._clean_base64_string("SGVsbG8=")
        assert result == "SGVsbG8="

    def test_clean_base64_string_with_padding(self):
        c = _make_connector()
        result = c._clean_base64_string("SGVsbG8")
        # Should add padding
        assert len(result) % 4 == 0

    def test_file_mime_types_mapping(self):
        assert '.pdf' in FILE_MIME_TYPES
        assert '.docx' in FILE_MIME_TYPES
        assert '.xlsx' in FILE_MIME_TYPES
        assert '.pptx' in FILE_MIME_TYPES
        assert '.txt' in FILE_MIME_TYPES
        assert '.csv' in FILE_MIME_TYPES
        assert '.html' in FILE_MIME_TYPES
        assert '.md' in FILE_MIME_TYPES
        assert '.json' in FILE_MIME_TYPES

    def test_document_mime_types(self):
        assert MimeTypes.PDF.value in DOCUMENT_MIME_TYPES
        assert MimeTypes.DOCX.value in DOCUMENT_MIME_TYPES

    def test_image_mime_types(self):
        assert MimeTypes.PNG.value in IMAGE_MIME_TYPES
        assert MimeTypes.JPEG.value in IMAGE_MIME_TYPES

    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        c = _make_connector()
        c.run_sync = AsyncMock()
        await c.run_incremental_sync()
        c.run_sync.assert_awaited_once()


# ===========================================================================
# _remove_unwanted_tags
# ===========================================================================
class TestRemoveUnwantedTags:
    def test_removes_script_and_style(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        html = "<html><script>alert('hi')</script><style>.a{}</style><p>text</p></html>"
        soup = BeautifulSoup(html, "html.parser")
        c._remove_unwanted_tags(soup)
        assert soup.find("script") is None
        assert soup.find("style") is None
        assert soup.find("p") is not None


# ===========================================================================
# _remove_image_tags
# ===========================================================================
class TestRemoveImageTags:
    def test_removes_img_tags(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        html = '<html><img src="test.png"><p>text</p></html>'
        soup = BeautifulSoup(html, "html.parser")
        c._remove_image_tags(soup)
        assert soup.find("img") is None


# ===========================================================================
# _clean_data_uris_in_html
# ===========================================================================
class TestCleanDataUris:
    def test_truncates_long_base64(self):
        c = _make_connector()
        # Use invalid base64 characters so the data URI is removed entirely
        invalid_data = "!" * 100
        html = f'<img src="data:image/png;base64,{invalid_data}">'
        result = c._clean_data_uris_in_html(html)
        assert len(result) < len(html)

    def test_short_data_unchanged(self):
        c = _make_connector()
        # Use already-padded valid base64 so no padding is added by the cleaner
        html = '<img src="data:image/png;base64,abc=">'
        result = c._clean_data_uris_in_html(html)
        assert result == html


# ===========================================================================
# Permissions, sync orchestration, crawl/fetch (from gaps coverage merge)
# ===========================================================================
class TestCreateWebPermissions:
    @pytest.mark.asyncio
    async def test_team_scope_org_read(self):
        c = _make_connector(scope=ConnectorScope.TEAM.value)
        c.url = "https://example.com"
        await c.create_record_group([])
        _, perms = c.data_entities_processor.on_new_record_groups.call_args[0][0][0]
        assert len(perms) == 1
        assert perms[0].type == PermissionType.READ
        assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_personal_with_app_users_grants_user_read(self):
        c = _make_connector()
        c.url = "https://example.com"
        app_user = MagicMock()
        app_user.email = "owner@example.com"
        await c.create_record_group([app_user])
        _, perms = c.data_entities_processor.on_new_record_groups.call_args[0][0][0]
        assert len(perms) == 1
        assert perms[0].type == PermissionType.READ
        assert perms[0].entity_type == EntityType.USER
        assert perms[0].email == "owner@example.com"

    @pytest.mark.asyncio
    async def test_personal_without_creator_yields_no_permissions(self):
        c = _make_connector()
        c.url = "https://example.com"
        await c.create_record_group([])
        _, perms = c.data_entities_processor.on_new_record_groups.call_args[0][0][0]
        assert perms == []

    @pytest.mark.asyncio
    async def test_exception_propagates_from_create_record_group(self):
        c = _make_connector(scope=ConnectorScope.TEAM.value)
        c.url = "https://example.com"
        c.data_entities_processor.on_new_record_groups = AsyncMock(
            side_effect=RuntimeError("perm fail")
        )
        with pytest.raises(RuntimeError, match="perm fail"):
            await c.create_record_group([])


class TestCreateRecordGroupTeam:
    @pytest.mark.asyncio
    async def test_team_without_app_users_uses_org_permission(self):
        c = _make_connector(scope=ConnectorScope.TEAM.value)
        c.url = "https://example.com"
        c.connector_name = Connectors.WEB
        await c.create_record_group([])
        _, permissions = c.data_entities_processor.on_new_record_groups.call_args[0][0][0]
        assert permissions[0].entity_type == EntityType.ORG


class TestReloadConfigOrchestration:
    @pytest.mark.asyncio
    async def test_updates_restrict_to_start_path_and_url_should_contain(self):
        c = _make_connector()
        c.url = "https://example.com/docs"
        c.base_domain = "https://example.com"
        c.crawl_type = "recursive"
        c.max_depth = 3
        c.max_pages = 100
        c.max_size_mb = 10
        c.follow_external = False
        c.restrict_to_start_path = False
        c.start_path_prefix = "/"
        c.url_should_contain = []
        c.config_service.get_config = AsyncMock(
            return_value=_mock_config(
                url="https://example.com/docs",
                restrict_to_start_path=True,
                url_should_contain=["api"],
            )
        )
        await c.reload_config()
        assert c.restrict_to_start_path is True
        assert c.url_should_contain == ["api"]


class TestRunSyncOrchestration:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_team_scope_ensures_team_app_edge(self, mock_filters):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        c = _make_connector(scope=ConnectorScope.TEAM.value)
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.crawl_type = "single"
        c.session = MagicMock()
        c.reload_config = AsyncMock()
        c._crawl_single_page = AsyncMock()
        c.process_retry_urls = AsyncMock()
        await c.run_sync()
        c.data_store_provider._mock_tx.ensure_team_app_edge.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_personal_no_created_by_skips_app_users(self, mock_filters):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        c = _make_connector(created_by="")
        c.created_by = None
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.crawl_type = "single"
        c.session = MagicMock()
        c.reload_config = AsyncMock()
        c._crawl_single_page = AsyncMock()
        c.process_retry_urls = AsyncMock()
        await c.run_sync()
        c.data_entities_processor.on_new_app_users.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.web.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_personal_creator_missing_email_skips_app_users(self, mock_filters):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.crawl_type = "single"
        c.session = MagicMock()
        c.created_by = "user-1"
        c.data_entities_processor.get_user_by_user_id = AsyncMock(return_value=None)
        c.reload_config = AsyncMock()
        c._crawl_single_page = AsyncMock()
        c.process_retry_urls = AsyncMock()
        await c.run_sync()
        c.data_entities_processor.on_new_app_users.assert_not_awaited()


class TestCrawlSinglePageOrchestration:
    @pytest.mark.asyncio
    async def test_logs_error_on_exception(self):
        c = _make_connector()
        c.url = "https://example.com"
        c._fetch_and_process_url = AsyncMock(side_effect=RuntimeError("boom"))
        c._normalize_url = MagicMock(return_value="https://example.com/")
        await c._crawl_single_page("https://example.com")
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_skips_on_new_without_permissions(self):
        c = _make_connector()
        c.url = "https://example.com"
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=None,
        )
        c._fetch_and_process_url = AsyncMock(return_value=update)
        c._normalize_url = MagicMock(return_value="https://example.com/")
        c._check_index_filter = MagicMock(return_value=False)
        await c._crawl_single_page("https://example.com")
        c.data_entities_processor.on_new_records.assert_not_awaited()


class TestAncestorPlaceholderOrchestration:
    @pytest.mark.asyncio
    async def test_legacy_record_without_trailing_slash(self):
        c = _make_connector()
        c.url = "https://example.com"
        existing = MagicMock()
        existing.id = "legacy-id"

        async def lookup(connector_id, external_record_id):
            if external_record_id.endswith("/"):
                return None
            return existing

        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
        await c._create_ancestor_placeholder_records("https://example.com/a/b/c/")
        c.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_persistence_error_logged(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=Exception("db down"))
        await c._create_ancestor_placeholder_records("https://example.com/a/b/")
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_value_error_logged(self):
        c = _make_connector()
        c.url = "https://example.com"
        with patch(
            "app.connectors.sources.web.connector.urlparse",
            side_effect=ValueError("bad"),
        ):
            await c._create_ancestor_placeholder_records("https://example.com/a/b/")
        c.logger.error.assert_called()


class TestRecursiveCrawlOrchestration:
    @pytest.mark.asyncio
    async def test_final_batch_flush(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.batch_size = 10
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        updates = [
            RecordUpdate(
                record=mock_record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=[MagicMock()],
            )
            for _ in range(2)
        ]

        async def gen(*_a, **_k):
            for u in updates:
                yield u

        c._create_ancestor_placeholder_records = AsyncMock()
        c._crawl_recursive_generator = gen
        c.processed_urls = 0
        await c._crawl_recursive("https://example.com", 0)
        assert c.data_entities_processor.on_new_records.await_count >= 1

    @pytest.mark.asyncio
    async def test_re_enqueues_retry_below_max(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.max_depth = 2
        c.max_pages = 10
        c.session = MagicMock()
        c.visited_urls = {c._normalize_url("https://example.com/start")}
        c.retry_urls = {
            c._normalize_url("https://example.com/retry"): RetryUrl(
                url="https://example.com/retry",
                status=Status.PENDING,
                status_code=429,
                retries=0,
                last_attempted=1000,
                depth=1,
            ),
        }
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)

        async def _fetch(url, depth, referer=None):
            norm = c._normalize_url(url)
            c.visited_urls.add(norm)
            if norm in c.retry_urls:
                c.retry_urls[norm].retries = MAX_RETRIES
            return None

        c._fetch_and_process_url = _fetch

        with patch("app.connectors.sources.web.connector.asyncio.sleep", new_callable=AsyncMock):
            results = []
            async for _ in c._crawl_recursive_generator("https://example.com/start", 0):
                results.append(_)
        assert results == []
        assert any("Re-enqueued" in str(call) for call in c.logger.debug.call_args_list)

    @pytest.mark.asyncio
    async def test_enqueues_discovered_links(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.max_depth = 2
        c.max_pages = 10
        c.session = MagicMock()
        c.visited_urls = set()
        c.retry_urls = {}
        c._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        html = b'<html><body><a href="/child">child</a></body></html>'
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[],
            html_bytes=html,
        )
        c._fetch_and_process_url = AsyncMock(return_value=update)
        c._extract_links_from_content = AsyncMock(return_value=["https://example.com/child"])
        c._check_index_filter = MagicMock(return_value=False)

        with patch("app.connectors.sources.web.connector.asyncio.sleep", new_callable=AsyncMock):
            results = []
            async for ru in c._crawl_recursive_generator("https://example.com", 0):
                results.append(ru)
        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_skips_updated_records(self):
        c = _make_connector()
        c.url = "https://example.com"
        updated = RecordUpdate(
            record=MagicMock(),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
        )

        async def gen(*_a, **_k):
            yield updated

        c._create_ancestor_placeholder_records = AsyncMock()
        c._crawl_recursive_generator = gen
        c._handle_record_updates = AsyncMock()
        await c._crawl_recursive("https://example.com", 0)
        c._handle_record_updates.assert_awaited_once()


class TestFetchAndProcessUrlOrchestration:
    @pytest.mark.asyncio
    async def test_connection_failure_queues_retry(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.retry_urls = {}
        c._normalize_url = MagicMock(return_value="https://example.com/page")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is None
        assert "https://example.com/page" in c.retry_urls

    @pytest.mark.asyncio
    async def test_redirect_cross_domain_skipped(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.follow_external = False
        c.session = MagicMock()
        c.max_size_mb = 10
        c.retry_urls = {}
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"ok",
                headers={"Content-Type": "text/html"},
                final_url="https://other.com/page",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_url_should_contain_filter_skips(self):
        c = _make_connector()
        c.url = "https://example.com/start"
        c.base_domain = "https://example.com"
        c.url_should_contain = ["docs"]
        c.session = MagicMock()
        c.max_size_mb = 10
        c.visited_urls = set()
        c._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"<html></html>",
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/other",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/other", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_url_should_contain_allows_start_url(self):
        c = _make_connector()
        c.url = "https://example.com/start"
        c.base_domain = "https://example.com"
        c.url_should_contain = ["docs"]
        c.session = MagicMock()
        c.max_size_mb = 10
        c.retry_urls = {}
        c._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        html = b"<html><head><title>Start</title></head><body>x</body></html>"
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/start",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/start", 0)
        assert result is not None

    @pytest.mark.asyncio
    async def test_retryable_status_queues_retry(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.retry_urls = {}
        code = next(iter(RETRYABLE_STATUS_CODES))
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=code,
                content_bytes=b"",
                headers={},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is None
        assert c.retry_urls

    @pytest.mark.asyncio
    async def test_success_clears_pending_retry(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.follow_external = False
        c.retry_urls = {
            "https://example.com/page": RetryUrl(
                url="https://example.com/page",
                status=Status.PENDING,
                status_code=503,
                retries=1,
                last_attempted=0,
            )
        }
        c.url_should_contain = []
        c._normalize_url = MagicMock(return_value="https://example.com/page")
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        html = b"<html><head><title>T</title></head><body>x</body></html>"
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            await c._fetch_and_process_url("https://example.com/page", 0)
        assert "https://example.com/page" not in c.retry_urls

    @pytest.mark.asyncio
    async def test_oversized_content_skipped(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 0
        c.retry_urls = {}
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"x" * 1024,
                headers={"Content-Type": "text/plain"},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is None

    @pytest.mark.asyncio
    async def test_legacy_record_triggers_migration(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.follow_external = False
        c.retry_urls = {}
        c.url_should_contain = []
        c._normalize_url = MagicMock(return_value="https://example.com/page")
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        html = b"<html><head><title>New Title</title></head><body>changed</body></html>"
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Old"
        existing.external_revision_id = "oldhash"
        existing.parent_external_record_id = None
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = "COMPLETED"

        async def lookup(connector_id, external_record_id):
            if external_record_id.endswith("/"):
                return None
            return existing

        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is not None
        assert result.is_new is True

    @pytest.mark.asyncio
    async def test_existing_record_metadata_and_content_changes(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.follow_external = False
        c.retry_urls = {}
        c.url_should_contain = []
        c._normalize_url = MagicMock(return_value="https://example.com/page/")
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        html = b"<html><head><title>New</title></head><body>body</body></html>"
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Old"
        existing.external_revision_id = "stale"
        existing.parent_external_record_id = "https://example.com/"
        existing.indexing_status = ProgressStatus.QUEUED.value
        existing.extraction_status = "QUEUED"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result.is_updated is True
        assert result.metadata_changed is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_generic_exception_returns_none(self):
        c = _make_connector()
        c.session = MagicMock()
        c.max_size_mb = 10
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network"),
        ):
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is None


class TestRetryAndOrchestrationHelpers:
    @pytest.mark.asyncio
    async def test_retry_generator_skips_when_placeholder_none(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.retry_urls = {
            "https://example.com/fail": RetryUrl(
                url="https://example.com/fail",
                status=Status.PENDING,
                status_code=500,
                retries=3,
                last_attempted=0,
            )
        }
        c._create_failed_placeholder_record = AsyncMock(return_value=(None, None))
        results = []
        async for ru in c._retry_urls_generator():
            results.append(ru)
        assert results == []

    def test_restrict_to_start_path_rejects_outside_path(self):
        c = _make_connector()
        c.restrict_to_start_path = True
        c.url = "https://example.com/docs/start"
        c.start_path_prefix = "/docs/start/"
        assert not c._is_valid_url(
            "https://example.com/other/page", "https://example.com/docs/start"
        )

    def test_normalize_url_on_parse_error(self):
        c = _make_connector()
        with patch(
            "app.connectors.sources.web.connector.urlparse",
            side_effect=ValueError("bad url"),
        ):
            assert c._normalize_url("not-valid") == "not-valid"

    def test_mime_legacy_office_formats(self):
        c = _make_connector()
        _, ext = c._determine_mime_type("https://x.com/f", "application/msword")
        assert ext == "doc"
        _, ext = c._determine_mime_type("https://x.com/f", "application/vnd.ms-excel")
        assert ext == "xls"
        _, ext = c._determine_mime_type("https://x.com/f", "application/vnd.ms-powerpoint")
        assert ext == "ppt"

    def test_pass_extension_unknown_operator(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = ["pdf"]
        filt.get_operator = MagicMock(return_value="UNKNOWN")
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=filt)
        assert c._pass_extension_filter("pdf") is True

    def test_extract_title_prefers_h1_over_og(self):
        c = _make_connector()
        soup = BeautifulSoup(
            '<html><meta property="og:title" content="OG"/><h1>H1</h1></html>',
            "html.parser",
        )
        assert c._extract_title(soup, "https://example.com") == "H1"

    @pytest.mark.asyncio
    async def test_handle_record_updates_deleted(self):
        c = _make_connector()
        update = RecordUpdate(
            record=MagicMock(id="r-del"),
            is_new=False,
            is_updated=True,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await c._handle_record_updates(update)
        c.data_entities_processor.on_record_deleted.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_extract_links_exception_logged(self):
        c = _make_connector()
        c.session = MagicMock()
        record = MagicMock()
        record.weburl = "https://example.com"
        record.mime_type = MimeTypes.HTML.value
        with patch(
            "app.connectors.sources.web.connector.BeautifulSoup",
            side_effect=RuntimeError("parse fail"),
        ):
            result = await c._extract_links_from_content(
                "https://example.com", b"<html></html>", record
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_failed_placeholder_legacy_lookup(self):
        c = _make_connector()
        c.url = "https://example.com"
        existing = MagicMock()

        async def lookup(connector_id, external_record_id):
            if external_record_id.endswith("/"):
                return None
            return existing

        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
        record, perms = await c._create_failed_placeholder_record(
            "https://example.com/missing", 404
        )
        assert record is None
        assert perms is None


# ===========================================================================
# Image / SVG / HTML processing
# ===========================================================================
class TestImageProcessingComprehensive:
    def test_convert_svg_tag_success(self):
        c = _make_connector()
        soup = BeautifulSoup(f"<html><body>{_MINIMAL_SVG.decode()}</body></html>", "html.parser")
        svg = soup.find("svg")
        fake_png = base64.b64encode(_TINY_PNG).decode()
        with patch(
            "app.connectors.sources.web.connector.ImageParser.svg_base64_to_png_base64",
            return_value=fake_png,
        ):
            assert c._convert_svg_tag_to_png(soup, svg) is True
        assert soup.find("img") is not None

    def test_convert_svg_tag_empty_png_decomposes(self):
        c = _make_connector()
        soup = BeautifulSoup(f"<html><body>{_MINIMAL_SVG.decode()}</body></html>", "html.parser")
        svg = soup.find("svg")
        with patch(
            "app.connectors.sources.web.connector.ImageParser.svg_base64_to_png_base64",
            return_value="valid",
        ), patch.object(c, "_clean_base64_string", return_value=""):
            assert c._convert_svg_tag_to_png(soup, svg) is False
        assert soup.find("svg") is None

    def test_convert_svg_tag_failure_decomposes(self):
        c = _make_connector()
        soup = BeautifulSoup(f"<html><body>{_MINIMAL_SVG.decode()}</body></html>", "html.parser")
        svg = soup.find("svg")
        with patch(
            "app.connectors.sources.web.connector.ImageParser.svg_base64_to_png_base64",
            side_effect=RuntimeError("convert fail"),
        ):
            assert c._convert_svg_tag_to_png(soup, svg) is False

    def test_process_svg_tags_iterates(self):
        c = _make_connector()
        soup = BeautifulSoup(f"<html><body>{_MINIMAL_SVG.decode()}</body></html>", "html.parser")
        with patch.object(c, "_convert_svg_tag_to_png", return_value=True):
            c._process_svg_tags(soup)

    @pytest.mark.asyncio
    async def test_inline_svg_data_uri_base64_converted(self):
        c = _make_connector()
        svg_b64 = base64.b64encode(_MINIMAL_SVG).decode()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/svg+xml;base64,{svg_b64}"
        soup.body.append(img)
        png_b64 = base64.b64encode(_TINY_PNG).decode()
        with patch.object(c, "_convert_svg_bytes_to_png_base64", return_value=png_b64):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert "image/png" in img["src"]

    @pytest.mark.asyncio
    async def test_inline_svg_url_encoded_converted(self):
        c = _make_connector()
        encoded = _MINIMAL_SVG.decode().replace("<", "%3C")
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/svg+xml,{encoded}"
        soup.body.append(img)
        png_b64 = base64.b64encode(_TINY_PNG).decode()
        with patch.object(c, "_convert_svg_bytes_to_png_base64", return_value=png_b64):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert "image/png" in img["src"]

    @pytest.mark.asyncio
    async def test_inline_svg_invalid_base64_removed(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = "data:image/svg+xml;base64,!!!invalid!!!"
        soup.body.append(img)
        with patch.object(c, "_clean_base64_string", return_value=""):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_inline_svg_conversion_exception_removed(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/svg+xml;base64,{base64.b64encode(_MINIMAL_SVG).decode()}"
        soup.body.append(img)
        with patch.object(
            c, "_convert_svg_bytes_to_png_base64", side_effect=RuntimeError("svg fail")
        ):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_inline_avif_converted(self):
        c = _make_connector()

        buf = BytesIO()
        PILImage.new("RGB", (2, 2), color="blue").save(buf, format="PNG")
        avif_b64 = base64.b64encode(buf.getvalue()).decode()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/avif;base64,{avif_b64}"
        soup.body.append(img)
        png_b64 = base64.b64encode(_TINY_PNG).decode()
        with patch.object(c, "_convert_avif_bytes_to_png_base64", return_value=png_b64):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert "image/png" in img["src"]

    @pytest.mark.asyncio
    async def test_inline_avif_invalid_base64_removed(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = "data:image/avif;base64,!!!"
        soup.body.append(img)
        with patch.object(c, "_clean_base64_string", return_value=""):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_inline_avif_conversion_error_removed(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/avif;base64,{base64.b64encode(b'x').decode()}"
        soup.body.append(img)
        with patch.object(
            c, "_convert_avif_bytes_to_png_base64", side_effect=RuntimeError("avif fail")
        ):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_inline_jpeg_cleaned(self):
        c = _make_connector()
        jpeg_b64 = base64.b64encode(_TINY_PNG).decode()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/jpeg;base64,{jpeg_b64}"
        soup.body.append(img)
        await c._process_single_image(img, soup, "https://example.com", {})
        assert img["src"].startswith("data:image/jpeg;base64,")

    @pytest.mark.asyncio
    async def test_unsupported_data_uri_removed(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = "data:image/bmp;base64,AAAA"
        soup.body.append(img)
        await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_download_png_image(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/pic.png"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=_TINY_PNG,
                headers={"Content-Type": "image/png"},
                final_url="https://example.com/pic.png",
                strategy="aiohttp",
            )
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img["src"].startswith("data:image/png;base64,")

    @pytest.mark.asyncio
    async def test_download_failure_returns_early(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/missing.png"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img["src"] == "/missing.png"

    @pytest.mark.asyncio
    async def test_download_empty_bytes_returns_early(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/empty.png"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"",
                headers={"Content-Type": "image/png"},
                final_url="https://example.com/empty.png",
                strategy="aiohttp",
            )
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img["src"] == "/empty.png"

    @pytest.mark.asyncio
    async def test_download_external_svg_converted(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/icon.svg"/></body></html>', "html.parser")
        img = soup.find("img")
        png_b64 = base64.b64encode(_TINY_PNG).decode()
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=_MINIMAL_SVG,
                headers={"Content-Type": "image/svg+xml"},
                final_url="https://example.com/icon.svg",
                strategy="aiohttp",
            )
            with patch.object(c, "_convert_svg_bytes_to_png_base64", return_value=png_b64):
                await c._process_single_image(img, soup, "https://example.com", {})
        assert "image/png" in img["src"]

    @pytest.mark.asyncio
    async def test_download_external_avif_converted(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/photo.avif"/></body></html>', "html.parser")
        img = soup.find("img")
        png_b64 = base64.b64encode(_TINY_PNG).decode()
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"fake-avif",
                headers={"content-type": "image/avif"},
                final_url="https://example.com/photo.avif",
                strategy="aiohttp",
            )
            with patch.object(c, "_convert_avif_bytes_to_png_base64", return_value=png_b64):
                await c._process_single_image(img, soup, "https://example.com", {})
        assert "image/png" in img["src"]

    @pytest.mark.asyncio
    async def test_download_unsupported_type_removed(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/file.bmp"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"bmpdata",
                headers={"Content-Type": "image/bmp"},
                final_url="https://example.com/file.bmp",
                strategy="aiohttp",
            )
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_download_invalid_base64_removed(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/bad.png"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=_TINY_PNG,
                headers={"Content-Type": "image/png"},
                final_url="https://example.com/bad.png",
                strategy="aiohttp",
            )
            with patch.object(c, "_clean_base64_string", return_value=""):
                await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_process_image_exception_logged(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/x.png"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("network"),
        ):
            await c._process_single_image(img, soup, "https://example.com", {})
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_no_src_returns_immediately(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body><img/></body></html>", "html.parser")
        await c._process_single_image(soup.find("img"), soup, "https://example.com", {})
        assert soup.find("img").get("src") is None

    @pytest.mark.asyncio
    async def test_session_missing_skips_download(self):
        c = _make_connector()
        c.session = None
        soup = BeautifulSoup('<html><body><img src="/x.png"/></body></html>', "html.parser")
        img = soup.find("img")
        await c._process_single_image(img, soup, "https://example.com", {})
        assert img["src"] == "/x.png"

    def test_determine_image_content_type_from_extension(self):
        c = _make_connector()
        for path, expected in [
            (".png", "image/png"),
            (".gif", "image/gif"),
            (".webp", "image/webp"),
            (".svg", "image/svg+xml"),
            (".avif", "image/avif"),
        ]:
            resp = MagicMock()
            resp.headers = {"content-type": "application/octet-stream"}
            assert c._determine_image_content_type(resp, f"https://x.com/a{path}") == expected

    def test_convert_svg_bytes_returns_none_on_failure(self):
        c = _make_connector()
        with patch(
            "app.connectors.sources.web.connector.ImageParser.svg_base64_to_png_base64",
            side_effect=RuntimeError("fail"),
        ):
            assert c._convert_svg_bytes_to_png_base64(_MINIMAL_SVG, "url") is None

    def test_convert_svg_bytes_empty_after_clean(self):
        c = _make_connector()
        with patch(
            "app.connectors.sources.web.connector.ImageParser.svg_base64_to_png_base64",
            return_value="x",
        ), patch.object(c, "_clean_base64_string", return_value=""):
            assert c._convert_svg_bytes_to_png_base64(_MINIMAL_SVG, "url") is None

    def test_convert_avif_bytes_via_pillow(self):
        c = _make_connector()

        buf = BytesIO()
        PILImage.new("RGB", (2, 2), color="red").save(buf, format="PNG")
        with patch.object(
            c, "_clean_base64_string", return_value=base64.b64encode(buf.getvalue()).decode()
        ):
            assert c._convert_avif_bytes_to_png_base64(buf.getvalue(), "https://x.com/a.avif")

    def test_convert_avif_pillow_failure(self):
        c = _make_connector()
        with patch("app.connectors.sources.web.connector.Image.open", side_effect=OSError("bad")):
            assert c._convert_avif_bytes_to_png_base64(b"not-image", "url") is None

    @pytest.mark.asyncio
    async def test_process_all_images(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/a.png"/></body></html>', "html.parser")
        with patch.object(c, "_process_single_image", new_callable=AsyncMock) as mock_proc:
            await c._process_all_images(soup, "https://example.com", {})
        mock_proc.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_process_html_with_images_disabled(self):
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.get_value = MagicMock(return_value=False)
        record = MagicMock()
        record.weburl = "https://example.com"
        html = b'<html><body><img src="x.png"/><svg></svg></body></html>'
        result = await c._process_html_content(html, record, {})
        assert "<img" not in result

    @pytest.mark.asyncio
    async def test_process_html_with_images_enabled(self):
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.get_value = MagicMock(return_value=True)
        record = MagicMock()
        record.weburl = "https://example.com"
        with patch.object(c, "_process_svg_tags"), patch.object(
            c, "_process_all_images", new_callable=AsyncMock
        ):
            result = await c._process_html_content(
                b"<html><body><p>hi</p></body></html>", record, {}
            )
        assert result is not None

    @pytest.mark.asyncio
    async def test_process_html_raises_on_parse_error(self):
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.get_value = MagicMock(return_value=True)
        record = MagicMock()
        record.weburl = "https://example.com"
        with patch(
            "app.connectors.sources.web.connector.BeautifulSoup",
            side_effect=RuntimeError("bad html"),
        ):
            with pytest.raises(RuntimeError):
                await c._process_html_content(b"<html>", record, {})


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecordComprehensive:
    @pytest.mark.asyncio
    async def test_missing_weburl_raises_404(self):
        c = _make_connector()
        record = MagicMock(weburl=None, record_name="Page", id="r1")
        with pytest.raises(HTTPException) as exc:
            await c.stream_record(record)
        assert exc.value.status_code == HttpStatusCode.NOT_FOUND.value

    @pytest.mark.asyncio
    async def test_no_session_raises_500(self):
        c = _make_connector()
        c.session = None
        record = MagicMock(weburl="https://example.com", record_name="Page", id="r1")
        with pytest.raises(HTTPException) as exc:
            await c.stream_record(record)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_streams_html_after_processing(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.session = MagicMock()
        record = MagicMock(
            weburl="https://example.com/page",
            record_name="Page",
            id="r1",
            mime_type="text/html",
        )
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "app.connectors.sources.web.connector.create_stream_record_response",
            return_value="stream-response",
        ) as mock_stream:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"<html><body>hi</body></html>",
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            c._process_html_content = AsyncMock(return_value="<html>clean</html>")
            result = await c.stream_record(record)
        assert result == "stream-response"
        mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_fetch_failure_raises_http_exception(self):
        c = _make_connector()
        c.session = MagicMock()
        record = MagicMock(
            weburl="https://example.com/page",
            record_name="Page",
            id="r1",
            mime_type="text/html",
        )
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
            return_value=None,
        ):
            with pytest.raises(HTTPException):
                await c.stream_record(record)

    @pytest.mark.asyncio
    async def test_generic_error_reraises(self):
        c = _make_connector()
        c.session = MagicMock()
        record = MagicMock(
            weburl="https://example.com/page",
            record_name="Page",
            id="r1",
            mime_type="text/html",
        )
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ):
            with pytest.raises(RuntimeError):
                await c.stream_record(record)


# ===========================================================================
# Remaining branch coverage
# ===========================================================================
class TestWebConnectorRemainingCoverageGaps:
    @pytest.mark.asyncio
    async def test_crawl_single_page_updated_with_index_filter_off(self):
        c = _make_connector()
        c.url = "https://example.com"
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[MagicMock()],
        )
        c._fetch_and_process_url = AsyncMock(return_value=update)
        c._normalize_url = MagicMock(return_value="https://example.com/")
        c._check_index_filter = MagicMock(return_value=True)
        c._handle_record_updates = AsyncMock()
        await c._crawl_single_page("https://example.com")
        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        c._handle_record_updates.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crawl_single_page_new_record_with_index_filter_off(self):
        c = _make_connector()
        c.url = "https://example.com"
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        perms = [MagicMock()]
        update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=perms,
        )
        c._fetch_and_process_url = AsyncMock(return_value=update)
        c._normalize_url = MagicMock(return_value="https://example.com/")
        c._check_index_filter = MagicMock(return_value=True)
        await c._crawl_single_page("https://example.com")
        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value
        c.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_crawl_recursive_flushes_mid_batch(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.batch_size = 1
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        updates = [
            RecordUpdate(
                record=mock_record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=[MagicMock()],
            )
            for _ in range(2)
        ]

        async def gen(*_a, **_k):
            for u in updates:
                yield u

        c._create_ancestor_placeholder_records = AsyncMock()
        c._crawl_recursive_generator = gen
        c.processed_urls = 0
        await c._crawl_recursive("https://example.com", 0)
        assert c.data_entities_processor.on_new_records.await_count == 2

    @pytest.mark.asyncio
    async def test_generator_skips_url_at_max_retries(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.max_depth = 2
        c.max_pages = 10
        url = "https://example.com/stuck"
        norm = c._normalize_url(url)
        c.visited_urls = set()
        c.retry_urls = {
            norm: RetryUrl(
                url=url,
                status=Status.PENDING,
                status_code=503,
                retries=MAX_RETRIES,
                last_attempted=0,
                depth=0,
            )
        }
        c._fetch_and_process_url = AsyncMock(return_value=None)

        results = []
        async for ru in c._crawl_recursive_generator(url, 0):
            results.append(ru)
        assert results == []
        c._fetch_and_process_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_generator_skips_depth_exceeded_links(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.max_depth = 0
        c.max_pages = 10
        c.session = MagicMock()
        c.visited_urls = set()
        c.retry_urls = {}
        html = b'<html><body><a href="/child">child</a></body></html>'
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[MagicMock()],
            html_bytes=html,
        )
        c._fetch_and_process_url = AsyncMock(return_value=update)
        c._check_index_filter = MagicMock(return_value=False)

        with patch("app.connectors.sources.web.connector.asyncio.sleep", new_callable=AsyncMock):
            results = []
            async for ru in c._crawl_recursive_generator("https://example.com", 0):
                results.append(ru)
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_generator_sets_auto_index_off(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.max_depth = 0
        c.max_pages = 10
        c.session = MagicMock()
        c.visited_urls = set()
        c.retry_urls = {}
        mock_record = MagicMock()
        mock_record.mime_type = MimeTypes.HTML.value
        update = RecordUpdate(
            record=mock_record,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[MagicMock()],
            html_bytes=b"<html></html>",
        )
        c._fetch_and_process_url = AsyncMock(return_value=update)
        c._check_index_filter = MagicMock(return_value=True)

        with patch("app.connectors.sources.web.connector.asyncio.sleep", new_callable=AsyncMock):
            async for _ in c._crawl_recursive_generator("https://example.com", 0):
                pass
        assert mock_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_generator_logs_process_exception(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.max_depth = 1
        c.max_pages = 10
        c.visited_urls = set()
        c.retry_urls = {}
        c._fetch_and_process_url = AsyncMock(side_effect=RuntimeError("fetch boom"))

        with patch("app.connectors.sources.web.connector.asyncio.sleep", new_callable=AsyncMock):
            results = []
            async for ru in c._crawl_recursive_generator("https://example.com", 0):
                results.append(ru)
        assert results == []
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_url_filter_marks_redirect_as_visited(self):
        c = _make_connector()
        c.url = "https://example.com/start"
        c.base_domain = "https://example.com"
        c.url_should_contain = ["docs"]
        c.session = MagicMock()
        c.max_size_mb = 10
        c.visited_urls = set()
        c._normalize_url = MagicMock(side_effect=lambda u: u.rstrip("/"))
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"<html></html>",
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/other",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url(
                "https://example.com/redirect", 0
            )
        assert result is None
        assert "https://example.com/other" in c.visited_urls

    @pytest.mark.asyncio
    async def test_html_parse_failure_still_returns_record(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.retry_urls = {}
        c.url_should_contain = []
        c._normalize_url = MagicMock(return_value="https://example.com/page/")
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        html = b"<html><head><title>T</title></head><body>x</body></html>"
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "app.connectors.sources.web.connector.BeautifulSoup",
            side_effect=RuntimeError("parse fail"),
        ):
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page", 0)
        assert result is not None
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_empty_title_falls_back_to_netloc(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.retry_urls = {}
        c.url_should_contain = []
        c._normalize_url = MagicMock(return_value="https://example.com/")
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        html = b"<html><body>content only</body></html>"
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch, patch.object(
            c, "_extract_title", return_value="  "
        ), patch.object(c, "_extract_title_from_url", return_value="  "):
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/", 0)
        assert result is not None
        assert result.record.record_name == "example.com"

    @pytest.mark.asyncio
    async def test_parent_external_record_id_change_only(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.base_domain = "https://example.com"
        c.session = MagicMock()
        c.max_size_mb = 10
        c.follow_external = False
        c.retry_urls = {}
        c.url_should_contain = []
        html = b"<html><head><title>Same</title></head><body>same</body></html>"
        content_hash = hashlib.md5(
            BeautifulSoup(html, "html.parser")
            .get_text(separator="\n", strip=True)
            .encode("utf-8")
        ).hexdigest()
        c._normalize_url = MagicMock(return_value="https://example.com/page/")
        c._ensure_parent_records_exist = AsyncMock()
        c._pass_extension_filter = MagicMock(return_value=True)
        existing = MagicMock()
        existing.id = "rec-1"
        existing.record_name = "Same"
        existing.external_revision_id = content_hash
        existing.parent_external_record_id = "https://example.com/wrong-parent/"
        existing.indexing_status = ProgressStatus.COMPLETED.value
        existing.extraction_status = "COMPLETED"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=html,
                headers={"Content-Type": "text/html"},
                final_url="https://example.com/page/sub/",
                strategy="aiohttp",
            )
            result = await c._fetch_and_process_url("https://example.com/page/sub/", 0)
        assert result.is_updated is True
        assert result.metadata_changed is True
        assert result.content_changed is False

    @pytest.mark.asyncio
    async def test_extract_links_collects_valid_same_domain_links(self):
        c = _make_connector()
        c.follow_external = False
        record = MagicMock()
        record.weburl = "https://example.com/parent/"
        record.mime_type = MimeTypes.HTML.value
        html = b'<html><body><a href="/child">c</a><a href="https://other.com/x">x</a></body></html>'
        links = await c._extract_links_from_content(
            "https://example.com/parent/", html, record
        )
        assert links == ["https://example.com/child"]

    @pytest.mark.asyncio
    async def test_process_retry_urls_mid_batch_flush(self):
        c = _make_connector()
        c.batch_size = 1
        r1, r2 = MagicMock(), MagicMock()
        perms = [MagicMock()]

        async def gen(*_a, **_k):
            yield RecordUpdate(
                record=r1,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=perms,
            )
            yield RecordUpdate(
                record=r2,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
                new_permissions=perms,
            )

        c._retry_urls_generator = gen
        await c.process_retry_urls()
        assert c.data_entities_processor.on_new_records.await_count == 2

    def test_is_valid_url_exception_returns_false(self):
        c = _make_connector()
        with patch(
            "app.connectors.sources.web.connector.urlparse",
            side_effect=RuntimeError("bad parse"),
        ):
            assert c._is_valid_url("https://example.com/x", "https://example.com") is False

    def test_zip_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/archive", "application/zip")
        assert mime == MimeTypes.ZIP
        assert ext == "zip"

    def test_extract_title_og_when_h1_whitespace_only(self):
        c = _make_connector()
        soup = BeautifulSoup(
            '<html><h1>   </h1><meta property="og:title" content="OG Only"/></html>',
            "html.parser",
        )
        assert c._extract_title(soup, "https://example.com") == "OG Only"

    def test_ensure_trailing_slash_logs_on_error(self):
        c = _make_connector()
        with patch(
            "app.connectors.sources.web.connector.urlparse",
            side_effect=ValueError("bad"),
        ):
            assert c._ensure_trailing_slash("https://example.com/page") == "https://example.com/page"
        c.logger.warning.assert_called()

    @pytest.mark.asyncio
    async def test_ensure_parent_records_legacy_lookup(self):
        c = _make_connector()
        c.url = "https://example.com"
        existing = MagicMock()

        async def lookup(connector_id, external_record_id):
            if external_record_id.endswith("/"):
                return None
            return existing

        c.data_entities_processor.get_record_by_external_id = AsyncMock(side_effect=lookup)
        await c._ensure_parent_records_exist("https://example.com/a/b/")
        c.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ensure_parent_records_exception_logged(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.data_entities_processor.get_record_by_external_id = AsyncMock(
            side_effect=RuntimeError("db fail")
        )
        await c._ensure_parent_records_exist("https://example.com/a/b/")
        c.logger.error.assert_called()

    @pytest.mark.asyncio
    async def test_reindex_records_raises_on_error(self):
        c = _make_connector()
        c.data_entities_processor.reindex_existing_records = AsyncMock(
            side_effect=RuntimeError("reindex fail")
        )
        with pytest.raises(RuntimeError, match="reindex fail"):
            await c.reindex_records([MagicMock()])

    def test_clean_base64_decode_failure(self):
        c = _make_connector()
        with patch(
            "app.connectors.sources.web.connector.base64.b64decode",
            side_effect=ValueError("bad decode"),
        ):
            assert c._clean_base64_string("SGVsbG8=") == ""

    def test_clean_data_uri_decode_failure_removes_uri(self):
        c = _make_connector()
        html = '<img src="data:image/png;base64,SGVsbG8=">'
        with patch(
            "app.connectors.sources.web.connector.base64.b64decode",
            side_effect=ValueError("bad decode"),
        ):
            result = c._clean_data_uris_in_html(html)
        assert "data:image/png" not in result

    def test_clean_data_uri_fixes_padding(self):
        c = _make_connector()
        # Valid payload without padding — cleaner should add '=' and keep URI
        html = '<img src="data:image/png;base64,SGVsbG8">'
        result = c._clean_data_uris_in_html(html)
        assert "data:image/png;base64,SGVsbG8=" in result

    @pytest.mark.asyncio
    async def test_inline_svg_conversion_none_removes_image(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/svg+xml;base64,{base64.b64encode(_MINIMAL_SVG).decode()}"
        soup.body.append(img)
        with patch.object(c, "_convert_svg_bytes_to_png_base64", return_value=None):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_inline_avif_conversion_none_removes_image(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = f"data:image/avif;base64,{base64.b64encode(b'avif').decode()}"
        soup.body.append(img)
        with patch.object(c, "_convert_avif_bytes_to_png_base64", return_value=None):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_inline_jpeg_invalid_base64_removed(self):
        c = _make_connector()
        soup = BeautifulSoup("<html><body></body></html>", "html.parser")
        img = soup.new_tag("img")
        img["src"] = "data:image/jpeg;base64,SGVsbG8="
        soup.body.append(img)
        with patch.object(c, "_clean_base64_string", return_value=""):
            await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_external_svg_conversion_none_decomposes(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/icon.svg"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=_MINIMAL_SVG,
                headers={"Content-Type": "image/svg+xml"},
                final_url="https://example.com/icon.svg",
                strategy="aiohttp",
            )
            with patch.object(c, "_convert_svg_bytes_to_png_base64", return_value=None):
                await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    @pytest.mark.asyncio
    async def test_external_avif_conversion_none_decomposes(self):
        c = _make_connector()
        c.session = MagicMock()
        soup = BeautifulSoup('<html><body><img src="/photo.avif"/></body></html>', "html.parser")
        img = soup.find("img")
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=b"fake-avif",
                headers={"content-type": "image/avif"},
                final_url="https://example.com/photo.avif",
                strategy="aiohttp",
            )
            with patch.object(c, "_convert_avif_bytes_to_png_base64", return_value=None):
                await c._process_single_image(img, soup, "https://example.com", {})
        assert img.parent is None

    def test_image_content_type_defaults_to_jpeg(self):
        c = _make_connector()
        resp = MagicMock()
        resp.headers = {"content-type": "application/octet-stream"}
        assert c._determine_image_content_type(resp, "https://x.com/file.unknown") == "image/jpeg"

    def test_convert_svg_bytes_success(self):
        c = _make_connector()
        fake_png = base64.b64encode(_TINY_PNG).decode()
        with patch(
            "app.connectors.sources.web.connector.ImageParser.svg_base64_to_png_base64",
            return_value=fake_png,
        ):
            result = c._convert_svg_bytes_to_png_base64(_MINIMAL_SVG, "https://example.com/icon.svg")
        assert result == fake_png

    def test_convert_avif_bytes_empty_after_clean(self):
        c = _make_connector()

        buf = BytesIO()
        PILImage.new("RGB", (2, 2), color="green").save(buf, format="PNG")
        with patch.object(c, "_clean_base64_string", return_value=""):
            assert c._convert_avif_bytes_to_png_base64(buf.getvalue(), "url") is None

    @pytest.mark.asyncio
    async def test_stream_record_non_html_skips_html_processing(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.session = MagicMock()
        record = MagicMock(
            weburl="https://example.com/doc.pdf",
            record_name="Doc",
            id="r1",
            mime_type="application/pdf",
        )
        pdf_bytes = b"%PDF-1.4"
        with patch(
            "app.connectors.sources.web.connector.fetch_url_with_fallback",
            new_callable=AsyncMock,
        ) as mock_fetch, patch(
            "app.connectors.sources.web.connector.create_stream_record_response",
            return_value="stream-response",
        ), patch.object(c, "_process_html_content", new_callable=AsyncMock) as mock_html:
            mock_fetch.return_value = FetchResponse(
                status_code=200,
                content_bytes=pdf_bytes,
                headers={"Content-Type": "application/pdf"},
                final_url="https://example.com/doc.pdf",
                strategy="aiohttp",
            )
            result = await c.stream_record(record)
        assert result == "stream-response"
        mock_html.assert_not_called()
