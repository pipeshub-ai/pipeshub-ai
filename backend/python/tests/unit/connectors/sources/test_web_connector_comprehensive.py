"""Comprehensive tests for Web connector - extended coverage for uncovered methods."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch
from contextlib import asynccontextmanager

import pytest

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
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
    FILE_MIME_TYPES,
)
from app.models.entities import RecordType


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------
def _make_connector():
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

    mock_tx = AsyncMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)

    @asynccontextmanager
    async def _transaction():
        yield mock_tx

    dsp.transaction = _transaction
    dsp._mock_tx = mock_tx

    cs = AsyncMock()
    c = WebConnector(
        logger=logger, data_entities_processor=dep,
        data_store_provider=dsp, config_service=cs, connector_id="web-comp-1",
        scope="personal", created_by="test-user-id",
    )
    return c


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
