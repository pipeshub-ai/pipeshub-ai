"""Comprehensive tests for the Web connector – targets uncovered lines."""

import asyncio
import base64
import hashlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from bs4 import BeautifulSoup

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
    _bytes_async_gen,
)
from app.connectors.sources.web.fetch_strategy import FetchResponse
from app.models.entities import RecordType


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


class TestBytesAsyncGen:
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
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(max_pages=-5)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_pages"] == 1

    @pytest.mark.asyncio
    async def test_max_depth_below_one_clamped(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(depth=-1)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_depth"] == 1

    @pytest.mark.asyncio
    async def test_max_size_below_one_clamped(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(max_size_mb=-3)
        )
        result = await connector._fetch_and_parse_config()
        assert result["max_size_mb"] == 1

    @pytest.mark.asyncio
    async def test_url_should_contain_filters_empty_strings(self):
        connector = _make_connector()
        config = _mock_config()
        config["sync"]["url_should_contain"] = ["docs", "", "  "]
        connector.config_service.get_config = AsyncMock(return_value=config)
        result = await connector._fetch_and_parse_config()
        assert result["url_should_contain"] == ["docs"]

    @pytest.mark.asyncio
    async def test_config_not_dict_raises(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value="bad")
        with pytest.raises(ValueError, match="not found"):
            await connector._fetch_and_parse_config()

    @pytest.mark.asyncio
    async def test_start_path_prefix_computed(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(
            return_value=_mock_config(url="https://example.com/docs/api")
        )
        result = await connector._fetch_and_parse_config()
        assert result["start_path_prefix"] == "/docs/api/"


class TestInitSession:
    @pytest.mark.asyncio
    async def test_init_creates_session(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=_mock_config())
        result = await connector.init()
        assert result is True
        assert connector.session is not None
        await connector.session.close()

    @pytest.mark.asyncio
    async def test_init_exception_returns_false(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(side_effect=Exception("boom"))
        result = await connector.init()
        assert result is False


class TestConnectionNoSession:
    @pytest.mark.asyncio
    async def test_test_connection_no_session(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.session = None
        assert await connector.test_connection_and_access() is False


class TestGetAppUsers:
    def test_filters_empty_email(self):
        connector = _make_connector()
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
        connector = _make_connector()
        connector.connector_name = Connectors.WEB
        from app.models.entities import User
        users = [
            User(email="b@test.com", full_name="", is_active=True, org_id="org-1"),
        ]
        app_users = connector.get_app_users(users)
        assert len(app_users) == 1
        assert app_users[0].is_active is True
        assert app_users[0].full_name == "b@test.com"


class TestExtractTitle:
    def test_extract_title_from_soup_title_tag(self):
        connector = _make_connector()
        html = "<html><head><title>My Page</title></head><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert connector._extract_title(soup, "https://example.com") == "My Page"

    def test_extract_title_from_h1(self):
        connector = _make_connector()
        html = "<html><body><h1>Heading One</h1></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        assert connector._extract_title(soup, "https://example.com") == "Heading One"

    def test_extract_title_from_og_title(self):
        connector = _make_connector()
        html = '<html><head><meta property="og:title" content="OG Title" /></head><body></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        assert connector._extract_title(soup, "https://example.com") == "OG Title"

    def test_extract_title_fallback_to_url(self):
        connector = _make_connector()
        html = "<html><body></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        title = connector._extract_title(soup, "https://example.com/my-page")
        assert "my page" in title.lower()

    def test_extract_title_from_url_domain_only(self):
        connector = _make_connector()
        title = connector._extract_title_from_url("https://example.com")
        assert title == "example.com"

    def test_extract_title_from_url_with_extension(self):
        connector = _make_connector()
        title = connector._extract_title_from_url("https://example.com/file.pdf")
        assert "file" in title.lower()


class TestNormalizeUrl:
    def test_normalizes_query_params(self):
        connector = _make_connector()
        result = connector._normalize_url("https://example.com/page?a=1")
        assert "?a=1" in result

    def test_handles_invalid_url(self):
        connector = _make_connector()
        result = connector._normalize_url("")
        assert isinstance(result, str)


class TestEnsureTrailingSlash:
    def test_with_query_params_unchanged(self):
        connector = _make_connector()
        url = "https://example.com/page?id=123"
        assert connector._ensure_trailing_slash(url) == url

    def test_root_url(self):
        connector = _make_connector()
        result = connector._ensure_trailing_slash("https://example.com")
        assert result.endswith("/")


class TestGetParentUrl:
    def test_root_returns_none(self):
        connector = _make_connector()
        assert connector._get_parent_url("https://example.com/") is None

    def test_single_segment_returns_none(self):
        connector = _make_connector()
        assert connector._get_parent_url("https://example.com/docs/") is None

    def test_nested_returns_parent(self):
        connector = _make_connector()
        parent = connector._get_parent_url("https://example.com/docs/api/v1")
        assert parent is not None
        assert "/docs/api/" in parent


class TestIsValidUrl:
    def test_url_should_contain_filter_blocks(self):
        connector = _make_connector()
        connector.follow_external = False
        connector.restrict_to_start_path = False
        connector.url_should_contain = []
        assert connector._is_valid_url("https://example.com/page", "https://example.com/")

    def test_exception_returns_false(self):
        connector = _make_connector()
        assert not connector._is_valid_url(None, "https://example.com/")


class TestDetermineMimeType:
    def test_docx_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        assert mime == MimeTypes.XML

    def test_doc_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f", "application/msword"
        )
        assert mime == MimeTypes.DOC

    def test_xlsx_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )
        assert mime == MimeTypes.XML

    def test_xls_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f", "application/vnd.ms-excel"
        )
        assert mime == MimeTypes.XLS

    def test_pptx_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f",
            "application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )
        assert mime == MimeTypes.XML

    def test_ppt_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type(
            "https://example.com/f", "application/vnd.ms-powerpoint"
        )
        assert mime == MimeTypes.PPT

    def test_mdx_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "text/mdx")
        assert mime == MimeTypes.MDX

    def test_heic_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/heic")
        assert mime == MimeTypes.HEIC

    def test_heif_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/heif")
        assert mime == MimeTypes.HEIF

    def test_svg_from_content_type(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/f", "image/svg+xml")
        assert mime == MimeTypes.XML

    def test_htm_extension(self):
        connector = _make_connector()
        mime, ext = connector._determine_mime_type("https://example.com/page.htm", "")
        assert mime == MimeTypes.HTML


class TestPassExtensionFilter:
    def test_in_operator_matches(self):
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
        filt = MagicMock()
        filt.is_empty = MagicMock(return_value=False)
        filt.value = "not-a-list"
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=filt)
        assert connector._pass_extension_filter("pdf") is True


class TestCheckIndexFilter:
    def test_file_type_disabled(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = MimeTypes.XLSX.value
        assert connector._check_index_filter(record) is True

    def test_unknown_mime_passes(self):
        connector = _make_connector()
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=True)
        record = MagicMock()
        record.indexing_status = "QUEUED"
        record.mime_type = "application/custom"
        assert connector._check_index_filter(record) is False


class TestRemoveUnwantedTags:
    def test_removes_scripts_and_nav(self):
        connector = _make_connector()
        html = "<html><body><script>alert(1)</script><nav>menu</nav><p>content</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_unwanted_tags(soup)
        assert soup.find("script") is None
        assert soup.find("nav") is None
        assert soup.find("p") is not None

    def test_removes_css_selectors(self):
        connector = _make_connector()
        html = '<html><body><div class="sidebar">side</div><div class="main">content</div></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_unwanted_tags(soup)
        assert soup.find("div", class_="sidebar") is None

    def test_removes_form_elements(self):
        connector = _make_connector()
        html = "<html><body><form><input type='text'/><button>Submit</button></form><p>text</p></body></html>"
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_unwanted_tags(soup)
        assert soup.find("form") is None
        assert soup.find("button") is None


class TestRemoveImageTags:
    def test_removes_img_and_svg(self):
        connector = _make_connector()
        html = '<html><body><img src="test.png"/><svg><rect/></svg><p>keep</p></body></html>'
        soup = BeautifulSoup(html, "html.parser")
        connector._remove_image_tags(soup)
        assert soup.find("img") is None
        assert soup.find("svg") is None
        assert soup.find("p") is not None


class TestCleanBase64String:
    def test_valid_base64(self):
        connector = _make_connector()
        data = base64.b64encode(b"hello").decode()
        assert connector._clean_base64_string(data) == data

    def test_empty_string(self):
        connector = _make_connector()
        assert connector._clean_base64_string("") == ""

    def test_url_encoded_padding(self):
        connector = _make_connector()
        data = base64.b64encode(b"hello world!!!").decode()
        url_encoded = data.replace("=", "%3D")
        result = connector._clean_base64_string(url_encoded)
        assert result != ""

    def test_invalid_chars(self):
        connector = _make_connector()
        assert connector._clean_base64_string("invalid!!chars@@") == ""

    def test_missing_padding_fixed(self):
        connector = _make_connector()
        data = base64.b64encode(b"test data here").decode().rstrip("=")
        result = connector._clean_base64_string(data)
        assert result != ""


class TestCleanDataUrisInHtml:
    def test_valid_data_uri_kept(self):
        connector = _make_connector()
        b64 = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()
        html = f'<img src="data:image/png;base64,{b64}"/>'
        result = connector._clean_data_uris_in_html(html)
        assert "data:image/png;base64," in result

    def test_invalid_data_uri_removed(self):
        connector = _make_connector()
        html = '<img src="data:image/png;base64,!!!invalid!!!"/>'
        result = connector._clean_data_uris_in_html(html)
        assert "!!!" not in result


class TestHandleRecordUpdates:
    @pytest.mark.asyncio
    async def test_handle_metadata_only(self):
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
                status_code=404, content_bytes=b"Not found",
                headers={}, final_url="https://example.com/page", strategy="aiohttp",
            )
            result = await connector._fetch_and_process_url("https://example.com/page", 0)
        assert result is None
        assert "https://example.com/page" not in connector.retry_urls


class TestEnsureParentRecordsExist:
    @pytest.mark.asyncio
    async def test_none_parent_returns_immediately(self):
        connector = _make_connector()
        await connector._ensure_parent_records_exist(None)
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_existing_parent_stops_walk(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        existing = MagicMock()
        existing.id = "e-1"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await connector._ensure_parent_records_exist("https://example.com/docs/api/")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_missing_ancestors(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._ensure_parent_records_exist("https://example.com/docs/api/")
        connector.data_entities_processor.on_new_records.assert_awaited()


class TestCreateAncestorPlaceholders:
    @pytest.mark.asyncio
    async def test_no_segments_returns_early(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        await connector._create_ancestor_placeholder_records("https://example.com/")
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_creates_placeholders_for_intermediate_paths(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
        await connector._create_ancestor_placeholder_records("https://example.com/a/b/c/")
        connector.data_entities_processor.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_existing_record_reused(self):
        connector = _make_connector()
        connector.url = "https://example.com"
        existing = MagicMock()
        existing.id = "existing-ph-id"
        connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=existing)
        await connector._create_ancestor_placeholder_records("https://example.com/a/b/")
        call_args = connector.data_entities_processor.on_new_records.call_args
        records = call_args[0][0]
        assert records[0][0].id == "existing-ph-id"


class TestCreateFailedPlaceholderRecord:
    @pytest.mark.asyncio
    async def test_creates_placeholder(self):
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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


class TestProcessRetryUrls:
    @pytest.mark.asyncio
    async def test_batches_and_flushes(self):
        connector = _make_connector()
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
        connector = _make_connector()
        await connector.reindex_records([])
        connector.data_entities_processor.reindex_existing_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_calls_reindex(self):
        connector = _make_connector()
        records = [MagicMock()]
        await connector.reindex_records(records)
        connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with(records)


class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_returns_weburl(self):
        connector = _make_connector()
        record = MagicMock()
        record.weburl = "https://example.com/page"
        result = await connector.get_signed_url(record)
        assert result == "https://example.com/page"

    @pytest.mark.asyncio
    async def test_returns_none_when_no_weburl(self):
        connector = _make_connector()
        record = MagicMock()
        record.weburl = None
        result = await connector.get_signed_url(record)
        assert result is None


class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_raises_not_implemented(self):
        connector = _make_connector()
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("key")


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_noop(self):
        connector = _make_connector()
        await connector.handle_webhook_notification({})


class TestCleanupAdvanced:
    @pytest.mark.asyncio
    async def test_cleanup_clears_retry_urls(self):
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
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
        connector = _make_connector()
        connector.session = None
        record = MagicMock()
        record.weburl = None
        result = await connector._extract_links_from_content("https://example.com", None, record)
        assert result == []


class TestRecursiveGeneratorRetry:
    @pytest.mark.asyncio
    async def test_retry_re_enqueue(self):
        connector = _make_connector()
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
        connector = _make_connector()
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
            conn = await WebConnector.create_connector(logger, dsp, cs, "wc-factory")
            assert isinstance(conn, WebConnector)
