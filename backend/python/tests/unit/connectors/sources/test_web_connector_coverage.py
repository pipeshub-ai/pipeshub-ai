"""Extended coverage tests for Web connector - targets uncovered helper methods."""

import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

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
)
from app.connectors.sources.web.fetch_strategy import FetchResponse
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
    cs = AsyncMock()
    c = WebConnector(
        logger=logger, data_entities_processor=dep,
        data_store_provider=dsp, config_service=cs, connector_id="web-c-1"
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
        c = _make_connector()
        soup = BeautifulSoup("<html><title>My Page</title></html>", "html.parser")
        assert c._extract_title(soup, "https://example.com") == "My Page"

    def test_title_from_h1(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><h1>Heading</h1></html>", "html.parser")
        assert c._extract_title(soup, "https://example.com") == "Heading"

    def test_title_from_og_meta(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup(
            '<html><meta property="og:title" content="OG Title"></html>',
            "html.parser"
        )
        assert c._extract_title(soup, "https://example.com") == "OG Title"

    def test_title_fallback_to_url(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><body>No title</body></html>", "html.parser")
        result = c._extract_title(soup, "https://example.com/my-page")
        assert "My Page" in result

    def test_title_empty_title_tag(self):
        from bs4 import BeautifulSoup
        c = _make_connector()
        soup = BeautifulSoup("<html><title>  </title></html>", "html.parser")
        result = c._extract_title(soup, "https://example.com/fallback")
        assert "Fallback" in result


# ===========================================================================
# _extract_title_from_url
# ===========================================================================
class TestExtractTitleFromUrl:
    def test_with_extension(self):
        c = _make_connector()
        assert c._extract_title_from_url("https://example.com/path/my-file.pdf") == "My File"

    def test_with_hyphens_and_underscores(self):
        c = _make_connector()
        assert "Hello World" in c._extract_title_from_url("https://example.com/hello_world")

    def test_root_path(self):
        c = _make_connector()
        assert c._extract_title_from_url("https://example.com") == "example.com"

    def test_root_with_slash(self):
        c = _make_connector()
        assert c._extract_title_from_url("https://example.com/") == "example.com"


# ===========================================================================
# _get_parent_url
# ===========================================================================
class TestGetParentUrl:
    def test_root_returns_none(self):
        c = _make_connector()
        assert c._get_parent_url("https://example.com") is None
        assert c._get_parent_url("https://example.com/") is None

    def test_one_segment(self):
        c = _make_connector()
        # /docs -> parent path is "/" which is root -> None
        assert c._get_parent_url("https://example.com/docs") is None

    def test_two_segments(self):
        c = _make_connector()
        parent = c._get_parent_url("https://example.com/docs/page")
        assert parent == "https://example.com/docs/"

    def test_deep_path(self):
        c = _make_connector()
        parent = c._get_parent_url("https://example.com/a/b/c/d")
        assert parent == "https://example.com/a/b/c/"


# ===========================================================================
# _ensure_trailing_slash
# ===========================================================================
class TestEnsureTrailingSlash:
    def test_page_url_gets_slash(self):
        c = _make_connector()
        assert c._ensure_trailing_slash("https://example.com/page").endswith("/")

    def test_file_url_kept(self):
        c = _make_connector()
        result = c._ensure_trailing_slash("https://example.com/file.pdf")
        assert not result.endswith("/")

    def test_already_has_slash(self):
        c = _make_connector()
        result = c._ensure_trailing_slash("https://example.com/page/")
        assert result.endswith("/")

    def test_query_param_url_unchanged(self):
        c = _make_connector()
        url = "https://example.com/search?q=test"
        assert c._ensure_trailing_slash(url) == url


# ===========================================================================
# _determine_mime_type extended
# ===========================================================================
class TestDetermineMimeTypeExtended:
    def test_mdx_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "text/mdx")
        assert mime == MimeTypes.MDX

    def test_heic_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "image/heic")
        assert mime == MimeTypes.HEIC

    def test_heif_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "image/heif")
        assert mime == MimeTypes.HEIF

    def test_svg_content_type(self):
        c = _make_connector()
        # 'image/svg+xml' contains 'xml', which the code checks before 'svg',
        # so the XML branch takes precedence
        mime, ext = c._determine_mime_type("https://x.com/f", "image/svg+xml")
        assert mime == MimeTypes.XML

    def test_docx_content_type(self):
        c = _make_connector()
        # OOXML content types contain 'xml', which the code checks before
        # 'wordprocessingml', so the XML branch takes precedence
        mime, ext = c._determine_mime_type(
            "https://x.com/f",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert mime == MimeTypes.XML

    def test_doc_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/msword")
        assert mime == MimeTypes.DOC

    def test_xlsx_content_type(self):
        c = _make_connector()
        # OOXML content types contain 'xml', which the code checks before
        # 'spreadsheetml', so the XML branch takes precedence
        mime, ext = c._determine_mime_type(
            "https://x.com/f",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert mime == MimeTypes.XML

    def test_xls_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/vnd.ms-excel")
        assert mime == MimeTypes.XLS

    def test_pptx_content_type(self):
        c = _make_connector()
        # OOXML content types contain 'xml', which the code checks before
        # 'presentationml', so the XML branch takes precedence
        mime, ext = c._determine_mime_type(
            "https://x.com/f",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert mime == MimeTypes.XML

    def test_ppt_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/vnd.ms-powerpoint")
        assert mime == MimeTypes.PPT

    def test_zip_compressed_content_type(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/f", "application/x-compressed")
        assert mime == MimeTypes.ZIP

    def test_url_extension_fallback(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/file.json", "")
        assert mime == MimeTypes.JSON

    def test_txt_from_url(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/readme.txt", "")
        assert mime == MimeTypes.PLAIN_TEXT

    def test_csv_from_url(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/data.csv", "")
        assert mime == MimeTypes.CSV

    def test_webp_from_url(self):
        c = _make_connector()
        mime, ext = c._determine_mime_type("https://x.com/img.webp", "")
        assert mime == MimeTypes.WEBP


# ===========================================================================
# _pass_extension_filter extended
# ===========================================================================
class TestPassExtensionFilterExtended:
    def test_in_operator_allows_match(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf", "docx"]
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("pdf") is True

    def test_in_operator_rejects_non_match(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf", "docx"]
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("html") is False

    def test_not_in_operator_allows_non_match(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["exe", "bat"]
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("pdf") is True

    def test_not_in_operator_rejects_match(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["exe", "bat"]
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter("exe") is False

    def test_no_extension_with_in_operator_fails(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf"]
        filt.get_operator.return_value = MultiselectOperator.IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter(None) is False

    def test_no_extension_with_not_in_operator_passes(self):
        c = _make_connector()
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf"]
        filt.get_operator.return_value = MultiselectOperator.NOT_IN
        c.sync_filters = MagicMock()
        c.sync_filters.get.return_value = filt
        assert c._pass_extension_filter(None) is True

    def test_invalid_filter_value_passes(self):
        c = _make_connector()
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
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = "application/octet-stream"
        assert c._check_index_filter(record) is False

    def test_html_enabled(self):
        c = _make_connector()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.HTML.value
        assert c._check_index_filter(record) is False

    def test_image_enabled(self):
        c = _make_connector()
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
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = False
        assert c._is_valid_url("https://example.com/page", "https://example.com/") is True

    def test_rejects_css(self):
        c = _make_connector()
        assert c._is_valid_url("https://example.com/style.css", "https://example.com/") is False

    def test_rejects_js(self):
        c = _make_connector()
        assert c._is_valid_url("https://example.com/app.js", "https://example.com/") is False

    def test_rejects_woff(self):
        c = _make_connector()
        assert c._is_valid_url("https://example.com/font.woff2", "https://example.com/") is False

    def test_restrict_path_allows_subpath(self):
        c = _make_connector()
        c.follow_external = False
        c.restrict_to_start_path = True
        c.url = "https://example.com/docs/"
        c.start_path_prefix = "/docs/"
        assert c._is_valid_url("https://example.com/docs/sub/page", "https://example.com/docs/")

    def test_restrict_path_rejects_sibling(self):
        c = _make_connector()
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
        c = _make_connector()
        assert "#" not in c._normalize_url("https://example.com/page#section")

    def test_lowercases_netloc(self):
        c = _make_connector()
        result = c._normalize_url("https://EXAMPLE.COM/path")
        assert "example.com" in result

    def test_root_path_preserved(self):
        c = _make_connector()
        result = c._normalize_url("https://example.com/")
        assert result.endswith("/")


# ===========================================================================
# _ensure_parent_records_exist
# ===========================================================================
class TestEnsureParentRecordsExist:
    @pytest.mark.asyncio
    async def test_none_parent_returns_early(self):
        c = _make_connector()
        await c._ensure_parent_records_exist(None)
        c.data_entities_processor.get_record_by_external_id.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_missing_parents(self):
        c = _make_connector()
        c.url = "https://example.com"
        c.connector_name = Connectors.WEB
        c.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await c._ensure_parent_records_exist("https://example.com/a/b/")
        c.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_stops_when_parent_exists(self):
        c = _make_connector()
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
        c = _make_connector()
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
        c = _make_connector()
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
        c = _make_connector()
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
        c = _make_connector()
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
        c = _make_connector()
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
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={"other": {}})
        with pytest.raises(ValueError):
            await c._fetch_and_parse_config()


# ===========================================================================
# reload_config extended
# ===========================================================================
class TestReloadConfigExtended:
    @pytest.mark.asyncio
    async def test_updates_follow_external(self):
        c = _make_connector()
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
        c = _make_connector()
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
