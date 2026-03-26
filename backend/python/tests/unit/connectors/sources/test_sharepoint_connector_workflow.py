"""Comprehensive workflow tests for SharePoint Online connector.

These tests exercise full sync workflows, covering initialization, site discovery,
drive processing, page processing, permissions, error handling, and record updates.
Each test covers many code paths to maximize statement coverage.
"""

import asyncio
import logging
import re
import urllib.parse
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest

from app.connectors.sources.microsoft.sharepoint_online.connector import (
    COMPOSITE_SITE_ID_COMMA_COUNT,
    COMPOSITE_SITE_ID_PARTS_COUNT,
    CountryToRegionMapper,
    MicrosoftRegion,
    SharePointConnector,
    SharePointCredentials,
    SharePointRecordType,
    SiteMetadata,
)
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    SyncFilterKey,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    SharePointListItemRecord,
    SharePointListRecord,
    SharePointPageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.sharepoint.workflow")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-sp-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.on_record_metadata_update = AsyncMock()
    data_entities_processor.on_updated_record_permissions = AsyncMock()
    data_entities_processor.on_record_content_update = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[
        MagicMock(email="user1@contoso.com"),
    ])

    data_store_provider = MagicMock()
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=None)

    class FakeTxContext:
        async def __aenter__(self):
            return mock_tx_store
        async def __aexit__(self, *args):
            pass

    data_store_provider.transaction = MagicMock(return_value=FakeTxContext())

    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service, mock_tx_store


def _make_connector():
    logger, dep, dsp, cs, tx = _make_mock_deps()
    connector = SharePointConnector(logger, dep, dsp, cs, "conn-sp-1")
    return connector, dep, dsp, cs, tx


def _make_mock_site(site_id, name, display_name=None, web_url=None, created=None, modified=None):
    site = MagicMock()
    site.id = site_id
    site.name = name
    site.display_name = display_name or name
    site.web_url = web_url or f"https://contoso.sharepoint.com/sites/{name}"
    site.created_date_time = created or datetime(2024, 1, 1, tzinfo=timezone.utc)
    site.last_modified_date_time = modified or datetime(2024, 6, 1, tzinfo=timezone.utc)
    site.description = f"Test site {name}"
    return site


def _make_mock_drive(drive_id, name, web_url=None, created=None, modified=None):
    drive = MagicMock()
    drive.id = drive_id
    drive.name = name
    drive.web_url = web_url or f"https://contoso.sharepoint.com/Shared Documents"
    drive.created_date_time = created or datetime(2024, 1, 1, tzinfo=timezone.utc)
    drive.last_modified_date_time = modified or datetime(2024, 6, 1, tzinfo=timezone.utc)
    return drive


def _make_mock_drive_item(item_id, name, is_file=True, is_deleted=False, is_root=False,
                          e_tag="etag1", size=1024, web_url=None, mime_type="application/pdf",
                          created=None, modified=None, parent_id=None, parent_path=None):
    item = MagicMock()
    item.id = item_id
    item.name = name
    item.e_tag = e_tag
    item.c_tag = "ctag1"
    item.web_url = web_url or f"https://contoso.sharepoint.com/{name}"
    item.size = size
    item.created_date_time = created or datetime(2024, 1, 1, tzinfo=timezone.utc)
    item.last_modified_date_time = modified or datetime(2024, 6, 1, tzinfo=timezone.utc)

    if is_root:
        item.root = MagicMock()
    else:
        item.root = None

    if is_deleted:
        item.deleted = MagicMock()
    else:
        item.deleted = None

    if is_file:
        item.folder = None
        item.file = MagicMock()
        item.file.mime_type = mime_type
        item.file.hashes = MagicMock()
        item.file.hashes.quick_xor_hash = "hash123"
        item.file.hashes.crc32_hash = None
        item.file.hashes.sha1_hash = None
        item.file.hashes.sha256_hash = None
    else:
        item.folder = MagicMock()
        item.file = None

    item.parent_reference = MagicMock()
    item.parent_reference.id = parent_id or "parent-1"
    item.parent_reference.path = parent_path or "/drive/root:/Docs"

    return item


def _make_mock_page(page_id, title, created=None, modified=None, e_tag="page-etag"):
    page = MagicMock()
    page.id = page_id
    page.title = title
    page.name = title
    page.web_url = f"https://contoso.sharepoint.com/SitePages/{title}.aspx"
    page.e_tag = e_tag
    page.created_date_time = created or datetime(2024, 1, 1, tzinfo=timezone.utc)
    page.last_modified_date_time = modified or datetime(2024, 6, 1, tzinfo=timezone.utc)
    page.page_layout = None
    page.promotion_kind = None
    # Non-system page
    page.created_by = MagicMock()
    page.created_by.user = MagicMock()
    page.created_by.user.display_name = "John Doe"
    return page


# ===========================================================================
# CountryToRegionMapper
# ===========================================================================


class TestCountryToRegionMapperExtended:

    def test_get_region_for_known_countries(self):
        assert CountryToRegionMapper.get_region("US") == MicrosoftRegion.NAM
        assert CountryToRegionMapper.get_region("GB") == MicrosoftRegion.GBR
        assert CountryToRegionMapper.get_region("IN") == MicrosoftRegion.IND
        assert CountryToRegionMapper.get_region("JP") == MicrosoftRegion.JPN
        assert CountryToRegionMapper.get_region("AU") == MicrosoftRegion.AUS
        assert CountryToRegionMapper.get_region("FR") == MicrosoftRegion.FRA
        assert CountryToRegionMapper.get_region("DE") == MicrosoftRegion.DEU

    def test_get_region_returns_default_for_unknown(self):
        assert CountryToRegionMapper.get_region("XX") == MicrosoftRegion.NAM

    def test_get_region_returns_default_for_none(self):
        assert CountryToRegionMapper.get_region(None) == MicrosoftRegion.NAM

    def test_get_region_case_insensitive(self):
        assert CountryToRegionMapper.get_region("us") == MicrosoftRegion.NAM
        assert CountryToRegionMapper.get_region("gb") == MicrosoftRegion.GBR

    def test_get_region_string(self):
        assert CountryToRegionMapper.get_region_string("US") == "NAM"
        assert CountryToRegionMapper.get_region_string(None) == "NAM"

    def test_is_valid_region(self):
        assert CountryToRegionMapper.is_valid_region("NAM") is True
        assert CountryToRegionMapper.is_valid_region("nam") is True
        assert CountryToRegionMapper.is_valid_region("INVALID") is False

    def test_get_all_regions(self):
        regions = CountryToRegionMapper.get_all_regions()
        assert "NAM" in regions
        assert "GBR" in regions
        assert len(regions) > 20

    def test_get_all_country_codes(self):
        codes = CountryToRegionMapper.get_all_country_codes()
        assert "US" in codes
        assert "GB" in codes


# ===========================================================================
# Connector Construction & Init
# ===========================================================================


class TestSharePointConnectorInit:

    def test_constructor_sets_defaults(self):
        connector, *_ = _make_connector()
        assert connector.connector_id == "conn-sp-1"
        assert connector.batch_size == 50
        assert connector.enable_subsite_discovery is True
        assert connector.stats['sites_processed'] == 0
        assert connector.tenant_region is None

    @pytest.mark.asyncio
    async def test_init_with_client_secret(self):
        connector, dep, dsp, cs, tx = _make_connector()
        cs.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "tenant-1",
                "clientId": "client-1",
                "clientSecret": "secret-1",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "hasAdminConsent": True,
            }
        })

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.ClientSecretCredential") as mock_cred_cls, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient") as mock_graph_cls, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient") as mock_msgraph_cls:

            mock_cred = AsyncMock()
            mock_cred.get_token = AsyncMock(return_value=MagicMock(token="test-token"))
            mock_cred_cls.return_value = mock_cred

            mock_client = MagicMock()
            # Mock root site for region detection
            root_site = MagicMock()
            root_site.site_collection = MagicMock()
            root_site.site_collection.data_location_code = "NAM"
            mock_client.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)
            mock_graph_cls.return_value = mock_client

            result = await connector.init()
            assert result is True
            assert connector.sharepoint_domain == "https://contoso.sharepoint.com"
            assert connector.tenant_id == "tenant-1"

    @pytest.mark.asyncio
    async def test_init_missing_credentials_raises(self):
        connector, dep, dsp, cs, tx = _make_connector()
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="credentials not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_missing_auth_block_raises(self):
        connector, dep, dsp, cs, tx = _make_connector()
        cs.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="credentials not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_incomplete_credentials_raises(self):
        connector, dep, dsp, cs, tx = _make_connector()
        cs.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                # missing sharepointDomain
            }
        })
        with pytest.raises(ValueError, match="Incomplete"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_auth_method_raises(self):
        connector, dep, dsp, cs, tx = _make_connector()
        cs.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
                # no clientSecret, no certificate
            }
        })
        with pytest.raises(ValueError, match="Authentication credentials missing"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_normalizes_domain_without_scheme(self):
        connector, dep, dsp, cs, tx = _make_connector()
        cs.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "sharepointDomain": "contoso.sharepoint.com",
            }
        })
        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.ClientSecretCredential") as mock_cred_cls, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient") as mock_graph_cls, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"):

            mock_cred = AsyncMock()
            mock_cred.get_token = AsyncMock(return_value=MagicMock(token="t"))
            mock_cred_cls.return_value = mock_cred

            mock_client = MagicMock()
            root_site = MagicMock()
            root_site.site_collection = MagicMock()
            root_site.site_collection.data_location_code = None
            mock_client.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)
            mock_client.organization.get = AsyncMock(return_value=MagicMock(value=[
                MagicMock(country_letter_code="US")
            ]))
            mock_graph_cls.return_value = mock_client

            result = await connector.init()
            assert result is True
            assert "contoso.sharepoint.com" in connector.sharepoint_domain


# ===========================================================================
# Utility Methods
# ===========================================================================


class TestSharePointUtilities:

    def test_construct_site_url_empty(self):
        connector, *_ = _make_connector()
        assert connector._construct_site_url("") == ""

    def test_construct_site_url_passthrough(self):
        connector, *_ = _make_connector()
        assert connector._construct_site_url("site-1") == "site-1"

    def test_validate_site_id_empty(self):
        connector, *_ = _make_connector()
        assert connector._validate_site_id("") is False

    def test_validate_site_id_root(self):
        connector, *_ = _make_connector()
        assert connector._validate_site_id("root") is True

    def test_validate_site_id_composite_valid(self):
        connector, *_ = _make_connector()
        guid = "a" * 36
        site_id = f"contoso.sharepoint.com,{guid},{guid}"
        assert connector._validate_site_id(site_id) is True

    def test_validate_site_id_composite_invalid_parts(self):
        connector, *_ = _make_connector()
        assert connector._validate_site_id("a,b") is False

    def test_validate_site_id_short_unrecognized(self):
        connector, *_ = _make_connector()
        assert connector._validate_site_id("short") is False

    def test_validate_site_id_long_single(self):
        connector, *_ = _make_connector()
        assert connector._validate_site_id("a" * 50) is True

    def test_normalize_site_id_already_composite(self):
        connector, *_ = _make_connector()
        guid = "a" * 36
        composite = f"host.com,{guid},{guid}"
        assert connector._normalize_site_id(composite) == composite

    def test_normalize_site_id_from_cache(self):
        connector, *_ = _make_connector()
        guid1 = "a" * 36
        guid2 = "b" * 36
        composite = f"host.com,{guid1},{guid2}"
        connector.site_cache[composite] = MagicMock()
        result = connector._normalize_site_id(f"{guid1},{guid2}")
        assert result == composite

    def test_normalize_site_id_prepend_domain(self):
        connector, *_ = _make_connector()
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        guid1 = "a" * 36
        guid2 = "b" * 36
        result = connector._normalize_site_id(f"{guid1},{guid2}")
        assert result.startswith("contoso.sharepoint.com,")

    def test_normalize_site_id_empty(self):
        connector, *_ = _make_connector()
        assert connector._normalize_site_id("") == ""

    def test_parse_datetime_none(self):
        connector, *_ = _make_connector()
        assert connector._parse_datetime(None) is None

    def test_parse_datetime_iso_string(self):
        connector, *_ = _make_connector()
        result = connector._parse_datetime("2024-01-01T00:00:00Z")
        assert result is not None
        assert isinstance(result, int)

    def test_parse_datetime_datetime_object(self):
        connector, *_ = _make_connector()
        dt = datetime(2024, 1, 1, tzinfo=timezone.utc)
        result = connector._parse_datetime(dt)
        assert result is not None

    def test_parse_datetime_invalid(self):
        connector, *_ = _make_connector()
        assert connector._parse_datetime("not-a-date") is None


# ===========================================================================
# Safe API Call
# ===========================================================================


class TestSafeApiCall:

    @pytest.mark.asyncio
    async def test_safe_api_call_success(self):
        connector, *_ = _make_connector()
        result = await connector._safe_api_call(asyncio.coroutine(lambda: "ok")())
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_safe_api_call_permission_denied(self):
        connector, *_ = _make_connector()

        async def raise_forbidden():
            raise Exception("403 forbidden accessdenied")

        result = await connector._safe_api_call(raise_forbidden(), max_retries=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_safe_api_call_not_found(self):
        connector, *_ = _make_connector()

        async def raise_notfound():
            raise Exception("404 notfound")

        result = await connector._safe_api_call(raise_notfound(), max_retries=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_safe_api_call_bad_request(self):
        connector, *_ = _make_connector()

        async def raise_bad_request():
            raise Exception("400 badrequest invalid")

        result = await connector._safe_api_call(raise_bad_request(), max_retries=0)
        assert result is None

    @pytest.mark.asyncio
    async def test_safe_api_call_exhausts_retries(self):
        connector, *_ = _make_connector()

        async def raise_general():
            raise Exception("403 forbidden accessdenied")

        # Permission error should immediately return None without retry
        result = await connector._safe_api_call(raise_general(), max_retries=0, retry_delay=0)
        assert result is None


# ===========================================================================
# Site Discovery
# ===========================================================================


class TestSiteDiscovery:

    @pytest.mark.asyncio
    async def test_get_all_sites_with_root_and_search(self):
        connector, dep, *_ = _make_connector()

        root_site = _make_mock_site("host.com,guid1,guid2", "root-site", web_url="https://contoso.sharepoint.com")
        search_site = _make_mock_site("host.com,guid3,guid4", "team-site", web_url="https://contoso.sharepoint.com/sites/team")

        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector._safe_api_call = AsyncMock(side_effect=[
            root_site,  # root site
            MagicMock(value=[search_site], odata_next_link=None),  # search
        ])
        connector._get_subsites = AsyncMock(return_value=[])

        sites = await connector._get_all_sites()
        assert len(sites) == 2

    @pytest.mark.asyncio
    async def test_get_all_sites_filters_onedrive(self):
        connector, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        onedrive_site = _make_mock_site("onedrive-1", "onedrive", web_url="https://contoso-my.sharepoint.com/personal/user")

        connector._safe_api_call = AsyncMock(side_effect=[
            None,  # root site returns None
            MagicMock(value=[onedrive_site], odata_next_link=None),  # search
        ])
        connector._get_subsites = AsyncMock(return_value=[])

        sites = await connector._get_all_sites()
        # OneDrive site should be filtered out
        assert len(sites) == 0

    @pytest.mark.asyncio
    async def test_get_subsites(self):
        connector, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        subsite = _make_mock_site("host.com,sub1,sub2", "subsite", web_url="https://contoso.sharepoint.com/sites/parent/sub")

        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[subsite]))

        subsites = await connector._get_subsites("host.com,parent,web")
        assert len(subsites) == 1
        assert "host.com,sub1,sub2" in connector.site_cache


# ===========================================================================
# Drive Processing & File Records
# ===========================================================================


class TestDriveProcessing:

    @pytest.mark.asyncio
    async def test_process_drive_item_new_file(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed.url")

        item = _make_mock_drive_item("item-1", "document.pdf")
        users = [MagicMock(email="user@test.com")]

        connector._get_item_permissions = AsyncMock(return_value=[])
        connector._pass_drive_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)

        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        result = await connector._process_drive_item(item, "site-1", "drive-1", users)
        assert result is not None
        assert result.is_new is True
        assert result.record is not None
        assert result.record.record_name == "document.pdf"

    @pytest.mark.asyncio
    async def test_process_drive_item_deleted(self):
        connector, dep, dsp, cs, tx = _make_connector()

        item = _make_mock_drive_item("item-2", "deleted.pdf", is_deleted=True)
        users = []

        connector.sync_filters = FilterCollection()

        result = await connector._process_drive_item(item, "site-1", "drive-1", users)
        assert result is not None
        assert result.is_deleted is True

    @pytest.mark.asyncio
    async def test_process_drive_item_root_gets_composite_id(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value=None)
        connector._get_item_permissions = AsyncMock(return_value=[])
        connector._pass_drive_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        item = _make_mock_drive_item("root-item-1", "root-folder", is_file=False, is_root=True)

        result = await connector._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None
        assert "drive-1:root:root-item-1" in result.record.external_record_id

    @pytest.mark.asyncio
    async def test_process_drive_item_existing_record_changed(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value=None)
        connector._get_item_permissions = AsyncMock(return_value=[])
        connector._pass_drive_date_filters = MagicMock(return_value=True)
        connector._pass_extension_filter = MagicMock(return_value=True)
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        # Existing record with different etag
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old-etag"
        existing.quick_xor_hash = "old-hash"
        existing.version = 1
        existing.record_status = ProgressStatus.NOT_STARTED.value
        tx.get_record_by_external_id = AsyncMock(return_value=existing)

        item = _make_mock_drive_item("item-3", "updated.docx", e_tag="new-etag")

        result = await connector._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None
        assert result.is_updated is True
        assert result.metadata_changed is True
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_create_file_record_no_extension_skipped(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value=None)

        item = _make_mock_drive_item("item-4", "noextension")
        result = await connector._create_file_record(item, "drive-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_create_file_record_folder(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.msgraph_client = MagicMock()

        item = _make_mock_drive_item("item-5", "MyFolder", is_file=False)
        result = await connector._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.mime_type == MimeTypes.FOLDER.value

    @pytest.mark.asyncio
    async def test_create_file_record_with_parent_at_root(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed.url")

        item = _make_mock_drive_item("item-6", "file.xlsx", parent_id="p1", parent_path="/drive/root:")
        result = await connector._create_file_record(item, "drive-1", None)
        assert result is not None
        assert "drive-1:root:" in result.parent_external_record_id


# ===========================================================================
# Page Processing
# ===========================================================================


class TestPageProcessing:

    @pytest.mark.asyncio
    async def test_create_page_record(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE

        page = _make_mock_page("page-1", "Welcome Page")

        result = await connector._create_page_record(page, "site-1", "Test Site")
        assert result is not None
        assert "Welcome Page" in result.record_name
        assert "Test Site" in result.record_name
        assert result.mime_type == MimeTypes.HTML.value
        assert result.inherit_permissions is True

    @pytest.mark.asyncio
    async def test_create_page_record_no_id(self):
        connector, *_ = _make_connector()
        page = MagicMock()
        page.id = None
        result = await connector._create_page_record(page, "site-1", "Site")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_page_record_existing(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE

        page = _make_mock_page("page-2", "Updated Page")
        existing = MagicMock()
        existing.id = "existing-page-id"
        existing.version = 2
        existing.record_status = ProgressStatus.COMPLETED.value

        result = await connector._create_page_record(page, "site-1", "Site", existing)
        assert result is not None
        assert result.id == "existing-page-id"
        assert result.version == 3


# ===========================================================================
# Filter Methods
# ===========================================================================


class TestSharePointFilters:

    def test_pass_drive_date_filters_folder_always_passes(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()

        item = _make_mock_drive_item("f1", "Folder", is_file=False)
        assert connector._pass_drive_date_filters(item) is True

    def test_pass_extension_filter_folder_always_passes(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        item = _make_mock_drive_item("f1", "Folder", is_file=False)
        assert connector._pass_extension_filter(item) is True

    def test_pass_extension_filter_no_filter_passes(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        item = _make_mock_drive_item("f1", "file.pdf")
        assert connector._pass_extension_filter(item) is True

    def test_pass_site_ids_filters_no_filter(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        assert connector._pass_site_ids_filters("site-1") is True

    def test_pass_site_ids_filters_empty_site_id(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["site-1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_site_ids_filters("") is False

    def test_pass_drive_key_filters_no_filter(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        assert connector._pass_drive_key_filters("drive-1") is True

    def test_pass_drive_key_filters_empty_key(self):
        connector, *_ = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["drive-1"]
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(return_value=mock_filter)
        assert connector._pass_drive_key_filters("") is False

    def test_pass_page_date_filters_no_filter(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        page = _make_mock_page("p1", "Page")
        assert connector._pass_page_date_filters(page) is True

    def test_should_skip_list_hidden(self):
        connector, *_ = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = True
        assert connector._should_skip_list(list_obj, "MyList") is True

    def test_should_skip_list_system_prefix(self):
        connector, *_ = _make_connector()
        list_obj = MagicMock(spec=[])
        assert connector._should_skip_list(list_obj, "_hidden_list") is True
        assert connector._should_skip_list(list_obj, "form templates") is True

    def test_should_skip_list_normal(self):
        connector, *_ = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "genericList"
        assert connector._should_skip_list(list_obj, "Tasks") is False

    def test_get_date_filters_empty(self):
        connector, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        ma, mb, ca, cb = connector._get_date_filters()
        assert ma is None
        assert mb is None
        assert ca is None
        assert cb is None


# ===========================================================================
# Record Group and Document Library
# ===========================================================================


class TestRecordGroupCreation:

    def test_create_document_library_record_group(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        drive = _make_mock_drive("drive-1", "Shared Documents")
        result = connector._create_document_library_record_group(drive, "site-1", "internal-site-1")
        assert result is not None
        assert result.external_group_id == "drive-1"
        assert result.name == "Shared Documents"
        assert result.inherit_permissions is True

    def test_create_document_library_record_group_no_id(self):
        connector, *_ = _make_connector()
        drive = MagicMock()
        drive.id = None
        result = connector._create_document_library_record_group(drive, "site-1", "internal-1")
        assert result is None


# ===========================================================================
# Handle Record Updates
# ===========================================================================


class TestHandleRecordUpdates:

    @pytest.mark.asyncio
    async def test_handle_deleted_record(self):
        connector, dep, *_ = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        update = RecordUpdate(
            record=None,
            external_record_id="ext-1",
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        await connector._handle_record_updates(update)
        dep.on_record_deleted.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_metadata_and_content_update(self):
        connector, dep, *_ = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        mock_record = MagicMock()
        mock_record.record_name = "test"

        update = RecordUpdate(
            record=mock_record,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=True,
            new_permissions=[MagicMock()],
        )
        await connector._handle_record_updates(update)
        dep.on_record_metadata_update.assert_called_once()
        dep.on_record_content_update.assert_called_once()
        dep.on_updated_record_permissions.assert_called_once()


# ===========================================================================
# List Processing
# ===========================================================================


class TestListProcessing:

    @pytest.mark.asyncio
    async def test_create_list_record(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE

        list_obj = MagicMock()
        list_obj.id = "list-1"
        list_obj.display_name = "Tasks"
        list_obj.name = "Tasks"
        list_obj.web_url = "https://contoso.sharepoint.com/Lists/Tasks"
        list_obj.e_tag = "list-etag"
        list_obj.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        list_obj.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        list_obj.list = MagicMock()
        list_obj.list.template = "genericList"
        list_obj.list.item_count = 42

        result = await connector._create_list_record(list_obj, "site-1")
        assert result is not None
        assert result.record_name == "Tasks"
        assert result.record_type == RecordType.SHAREPOINT_LIST

    @pytest.mark.asyncio
    async def test_create_list_item_record(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE

        item = MagicMock()
        item.id = "item-1"
        item.e_tag = "item-etag"
        item.web_url = "https://contoso.sharepoint.com/Lists/Tasks/1"
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.fields = MagicMock()
        item.fields.additional_data = {"Title": "Task 1", "Status": "Done"}
        item.content_type = MagicMock()
        item.content_type.name = "Item"

        result = await connector._create_list_item_record(item, "site-1", "list-1")
        assert result is not None
        assert result.record_name == "Task 1"
        assert result.record_type == RecordType.SHAREPOINT_LIST_ITEM


# ===========================================================================
# Full Sync Workflow
# ===========================================================================


class TestSharePointFullSync:

    @pytest.mark.asyncio
    async def test_run_sync_full_workflow(self):
        """Test run_sync covering users, groups, sites, drives, pages."""
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.tenant_id = "t"
        connector.client_id = "c"
        connector.client_secret = "s"
        connector.certificate_path = None

        # Mock credential
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock(token="t"))
        connector.credential.close = AsyncMock()

        # Mock msgraph_client
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(return_value=[
            MagicMock(email="user1@test.com"),
        ])

        # Mock client
        connector.client = MagicMock()

        # Mock filter loading
        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())):

            # Mock _sync_user_groups
            connector._sync_user_groups = AsyncMock()

            # Mock _get_all_sites to return one valid site
            site = _make_mock_site("contoso.sharepoint.com,guid1guid1guid1guid1guid1guid1guid1g,guid2guid2guid2guid2guid2guid2guid2g",
                                   "TestSite")
            connector._get_all_sites = AsyncMock(return_value=[site])
            connector._get_site_permissions = AsyncMock(return_value=[])
            connector._sync_site_content = AsyncMock()

            # Mock GraphServiceClient constructor and MSGraphClient
            with patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient"), \
                 patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"):
                await connector.run_sync()

            dep.on_new_app_users.assert_called_once()
            dep.on_new_record_groups.assert_called_once()
            connector._sync_site_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_sync_no_sites(self):
        """Test run_sync returns early when no sites found."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock(token="t"))
        connector.certificate_path = None
        connector.tenant_id = "t"
        connector.client_id = "c"
        connector.client_secret = "s"
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()
        connector.msgraph_client.get_all_users = AsyncMock(return_value=[])

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters",
                    new_callable=AsyncMock, return_value=(FilterCollection(), FilterCollection())), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"):
            connector._sync_user_groups = AsyncMock()
            connector._get_all_sites = AsyncMock(return_value=[])

            await connector.run_sync()

            # on_new_record_groups should NOT be called since no sites
            dep.on_new_record_groups.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_site_content(self):
        """Test _sync_site_content processes drives and pages."""
        connector, dep, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        site_rg = MagicMock()
        site_rg.external_group_id = "site-1"
        site_rg.name = "Test Site"
        site_rg.id = "internal-site-1"

        # Mock _process_site_drives to yield a new record
        async def fake_drives(*args, **kwargs):
            from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
            record = MagicMock()
            record.indexing_status = None
            update = RecordUpdate(
                record=record,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
            )
            yield (record, [], update)

        connector._process_site_drives = fake_drives
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        # Mock _process_site_pages to yield nothing
        async def fake_pages(*args, **kwargs):
            return
            yield  # make it async generator

        connector._process_site_pages = fake_pages

        await connector._sync_site_content(site_rg)
        dep.on_new_records.assert_called()
        assert connector.stats['sites_processed'] == 1


# ===========================================================================
# Reinitialize Credential
# ===========================================================================


class TestReinitializeCredential:

    @pytest.mark.asyncio
    async def test_reinitialize_credential_valid(self):
        connector, *_ = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.get_token = AsyncMock(return_value=MagicMock(token="t"))
        await connector._reinitialize_credential_if_needed()
        connector.credential.get_token.assert_called_once()

    @pytest.mark.asyncio
    async def test_reinitialize_credential_expired_client_secret(self):
        connector, *_ = _make_connector()
        connector.tenant_id = "t"
        connector.client_id = "c"
        connector.client_secret = "s"
        connector.certificate_path = None

        # First call fails (expired), recreated credential succeeds
        old_cred = AsyncMock()
        old_cred.get_token = AsyncMock(side_effect=Exception("transport closed"))
        old_cred.close = AsyncMock()
        connector.credential = old_cred

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.ClientSecretCredential") as mock_cls, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"):
            new_cred = AsyncMock()
            new_cred.get_token = AsyncMock(return_value=MagicMock(token="new"))
            mock_cls.return_value = new_cred

            await connector._reinitialize_credential_if_needed()
            assert connector.credential == new_cred


# ===========================================================================
# Permissions
# ===========================================================================


class TestSharePointPermissions:

    @pytest.mark.asyncio
    async def test_get_site_permissions_with_groups(self):
        connector, *_ = _make_connector()
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.tenant_id = "t"
        connector.client_id = "c"
        connector.client_secret = "s"
        connector.certificate_path = None

        # Add site to cache
        connector.site_cache["site-1"] = SiteMetadata(
            site_id="site-1",
            site_url="https://contoso.sharepoint.com/sites/test",
            site_name="Test Site",
            is_root=False,
        )

        connector._get_sharepoint_access_token = AsyncMock(return_value="token")
        connector._get_sharepoint_group_users = AsyncMock(side_effect=[
            # Owner group - M365 group
            [{"LoginName": "c:0o.c|federateddirectoryclaimprovider|abcdefab-1234-5678-9012-abcdefabcdef", "Title": "Team", "PrincipalType": 4}],
            # Member group - direct user
            [{"LoginName": "user@test.com", "PrincipalType": 1, "Email": "user@test.com", "Id": 1}],
            # Visitor group - everyone claim
            [{"LoginName": "c:0(.s|true|spo-grid-all-users", "PrincipalType": 0, "Title": "Everyone"}],
        ])
        connector._get_custom_sharepoint_groups = AsyncMock(return_value=[])

        perms = await connector._get_site_permissions("site-1")
        assert len(perms) >= 2  # M365 group + user + org access

    @pytest.mark.asyncio
    async def test_get_site_permissions_no_cache(self):
        connector, *_ = _make_connector()
        perms = await connector._get_site_permissions("missing-site")
        assert perms == []

    @pytest.mark.asyncio
    async def test_get_site_permissions_no_token(self):
        connector, *_ = _make_connector()
        connector.site_cache["site-1"] = SiteMetadata(
            site_id="site-1", site_url="https://test.com", site_name="Test", is_root=False
        )
        connector._get_sharepoint_access_token = AsyncMock(return_value=None)
        perms = await connector._get_site_permissions("site-1")
        assert perms == []


# ===========================================================================
# Stream Record
# ===========================================================================


class TestStreamRecord:

    @pytest.mark.asyncio
    async def test_stream_file_record(self):
        connector, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE

        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "test.pdf"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.create_stream_record_response") as mock_stream, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.stream_content") as mock_content:
            connector.get_signed_url = AsyncMock(return_value="https://signed.url")
            mock_stream.return_value = MagicMock()

            result = await connector.stream_record(record)
            mock_stream.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_file_no_url_raises(self):
        from fastapi import HTTPException
        connector, *_ = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "test.pdf"
        record.id = "rec-1"

        connector.get_signed_url = AsyncMock(return_value=None)

        with pytest.raises(HTTPException):
            await connector.stream_record(record)


# ===========================================================================
# Test Connection
# ===========================================================================


class TestConnectionTest:

    @pytest.mark.asyncio
    async def test_test_connection(self):
        connector, *_ = _make_connector()
        result = await connector.test_connection_and_access()
        assert result is True


# ===========================================================================
# Deep Sync: _process_drive_delta
# ===========================================================================


class TestProcessDriveDelta:

    @pytest.mark.asyncio
    async def test_fresh_delta_sync_no_sync_point(self):
        """When no sync point exists, start fresh delta from root URL."""
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        item = _make_mock_drive_item("item-1", "doc.pdf")
        record_update = MagicMock()
        record_update.is_deleted = False
        record_update.record = MagicMock()
        record_update.record.indexing_status = None
        record_update.new_permissions = []

        connector._process_drive_item = AsyncMock(return_value=record_update)
        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [item],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-token-123'
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 1
        connector.drive_delta_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_delta_sync_with_existing_sync_point(self):
        """When a valid sync point exists, resume from stored delta URL."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        valid_delta = "https://graph.microsoft.com/v1.0/sites/s/drives/d/root/delta?token=abc"
        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value={'deltaLink': valid_delta})
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        connector._process_drive_item = AsyncMock(return_value=None)
        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [_make_mock_drive_item("i1", "f.pdf")],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta-final'
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        connector.msgraph_client.get_delta_response_sharepoint.assert_awaited_with(valid_delta)

    @pytest.mark.asyncio
    async def test_delta_sync_invalid_url_clears_and_restarts(self):
        """When stored delta URL is invalid, clear sync point and start fresh."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        invalid_delta = "http://evil.com/delta"
        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value={'deltaLink': invalid_delta})
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        connector._process_drive_item = AsyncMock(return_value=None)
        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [],
            'next_link': None,
            'delta_link': None
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        # Should have cleared the invalid sync point
        calls = connector.drive_delta_sync_point.update_sync_point.call_args_list
        assert any(call.kwargs.get('sync_point_data', {}).get('deltaLink') is None
                   or call.args[1].get('deltaLink') is None
                   for call in calls if len(call.args) > 1 or call.kwargs)

    @pytest.mark.asyncio
    async def test_delta_sync_pagination_follows_next_link(self):
        """Delta sync should follow next_link for pagination."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        rec_update = MagicMock()
        rec_update.is_deleted = False
        rec_update.record = MagicMock()
        rec_update.record.indexing_status = None
        rec_update.new_permissions = []
        connector._process_drive_item = AsyncMock(return_value=rec_update)

        page1 = {
            'drive_items': [_make_mock_drive_item("i1", "a.pdf")],
            'next_link': 'https://graph.microsoft.com/v1.0/next-page',
            'delta_link': None
        }
        page2 = {
            'drive_items': [_make_mock_drive_item("i2", "b.pdf")],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/final-delta'
        }
        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(side_effect=[page1, page2])

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 2
        assert connector.msgraph_client.get_delta_response_sharepoint.await_count == 2

    @pytest.mark.asyncio
    async def test_delta_sync_deleted_items_yielded(self):
        """Deleted items should yield as (None, [], record_update)."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        deleted_update = MagicMock()
        deleted_update.is_deleted = True
        deleted_update.record = None
        connector._process_drive_item = AsyncMock(return_value=deleted_update)

        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [_make_mock_drive_item("del-1", "x.pdf", is_deleted=True)],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta'
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 1
        assert results[0][0] is None  # record is None for deleted
        assert results[0][2].is_deleted is True

    @pytest.mark.asyncio
    async def test_delta_sync_item_processing_error_continues(self):
        """Errors processing individual items should not stop the sync."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        good_update = MagicMock()
        good_update.is_deleted = False
        good_update.record = MagicMock()
        good_update.record.indexing_status = None
        good_update.new_permissions = []

        connector._process_drive_item = AsyncMock(
            side_effect=[Exception("API error"), good_update]
        )

        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [
                _make_mock_drive_item("fail-1", "bad.pdf"),
                _make_mock_drive_item("ok-1", "good.pdf"),
            ],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta'
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_delta_sync_empty_drive_items_breaks(self):
        """Empty drive_items in response should break the loop."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [],
            'next_link': None,
            'delta_link': None
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_delta_sync_null_response_returns_early(self):
        """Null response from get_delta_response_sharepoint should return early."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()
        connector.indexing_filters = FilterCollection()

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value=None)

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_delta_sync_indexing_filter_disables_indexing(self):
        """When documents indexing filter is disabled, set AUTO_INDEX_OFF."""
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sharepoint_domain = "https://contoso.sharepoint.com"
        connector.msgraph_client = MagicMock()

        # Create a filter collection where DOCUMENTS is disabled
        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        connector.drive_delta_sync_point = MagicMock()
        connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.drive_delta_sync_point.update_sync_point = AsyncMock()

        dep.get_all_active_users = AsyncMock(return_value=[])

        rec_update = MagicMock()
        rec_update.is_deleted = False
        rec_update.record = MagicMock()
        rec_update.record.indexing_status = None
        rec_update.new_permissions = []
        connector._process_drive_item = AsyncMock(return_value=rec_update)

        connector.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            'drive_items': [_make_mock_drive_item("i1", "a.pdf")],
            'next_link': None,
            'delta_link': 'https://graph.microsoft.com/v1.0/delta'
        })

        results = []
        async for r in connector._process_drive_delta("site-1", "drive-1"):
            results.append(r)

        assert len(results) == 1
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# ===========================================================================
# Deep Sync: _process_site_drives
# ===========================================================================


class TestProcessSiteDrives:

    @pytest.mark.asyncio
    async def test_no_drives_returns_early(self):
        connector, dep, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)
        connector.sync_filters = FilterCollection()

        results = []
        async for r in connector._process_site_drives("site-1", "internal-1"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_drives_with_no_id_skipped(self):
        connector, dep, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector.sync_filters = FilterCollection()

        no_id_drive = MagicMock()
        no_id_drive.id = None
        no_id_drive.name = "No-ID-Drive"

        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[no_id_drive]))

        results = []
        async for r in connector._process_site_drives("site-1", "internal-1"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_drives_filtered_by_key(self):
        connector, dep, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        drive = _make_mock_drive("d1", "Docs")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[drive]))
        connector._pass_drive_key_filters = MagicMock(return_value=False)
        connector._normalize_document_library_url = MagicMock(return_value="/docs")

        results = []
        async for r in connector._process_site_drives("site-1", "internal-1"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_drives_processed_and_yields_items(self):
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector.sync_filters = FilterCollection()

        drive = _make_mock_drive("d1", "Shared Documents")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[drive]))
        connector._pass_drive_key_filters = MagicMock(return_value=True)
        connector._normalize_document_library_url = MagicMock(return_value="/docs")

        # Mock _process_drive_delta to yield items
        async def fake_delta(*args, **kwargs):
            update = MagicMock()
            update.is_deleted = False
            update.record = MagicMock()
            update.new_permissions = []
            yield (update.record, [], update)

        connector._process_drive_delta = fake_delta

        results = []
        async for r in connector._process_site_drives("site-1", "internal-1"):
            results.append(r)

        assert len(results) == 1
        dep.on_new_record_groups.assert_called_once()


# ===========================================================================
# Deep Sync: _sync_site_content with mixed updates
# ===========================================================================


class TestSyncSiteContentDeep:

    @pytest.mark.asyncio
    async def test_sync_site_content_handles_all_update_types(self):
        """Test that _sync_site_content routes deletions, updates, and new records correctly."""
        connector, dep, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        site_rg = MagicMock()
        site_rg.external_group_id = "site-1"
        site_rg.name = "Test Site"
        site_rg.id = "internal-site-1"

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        # Drive yields: 1 deleted, 1 updated, 1 new
        deleted_update = RecordUpdate(
            record=None, external_record_id="del-1",
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False
        )
        updated_update = RecordUpdate(
            record=MagicMock(), is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=True, content_changed=True, permissions_changed=False
        )
        new_record = MagicMock()
        new_record.indexing_status = None
        new_update = RecordUpdate(
            record=new_record, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False
        )

        async def fake_drives(*args, **kwargs):
            yield (None, [], deleted_update)
            yield (updated_update.record, [], updated_update)
            yield (new_record, [], new_update)

        connector._process_site_drives = fake_drives
        connector._handle_record_updates = AsyncMock()
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        # Pages yield nothing
        async def fake_pages(*args, **kwargs):
            return
            yield

        connector._process_site_pages = fake_pages

        await connector._sync_site_content(site_rg)

        assert connector._handle_record_updates.await_count == 2
        dep.on_new_records.assert_called()
        assert connector.stats['sites_processed'] == 1

    @pytest.mark.asyncio
    async def test_sync_site_content_batches_records(self):
        """Test that records are batched by batch_size."""
        connector, dep, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.batch_size = 2  # Small batch for testing

        site_rg = MagicMock()
        site_rg.external_group_id = "site-1"
        site_rg.name = "Test Site"
        site_rg.id = "internal-site-1"

        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        async def fake_drives(*args, **kwargs):
            for i in range(5):
                rec = MagicMock()
                rec.indexing_status = None
                update = RecordUpdate(
                    record=rec, is_new=True, is_updated=False, is_deleted=False,
                    metadata_changed=False, content_changed=False, permissions_changed=False
                )
                yield (rec, [], update)

        connector._process_site_drives = fake_drives
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        async def fake_pages(*args, **kwargs):
            return
            yield

        connector._process_site_pages = fake_pages

        await connector._sync_site_content(site_rg)

        # With 5 records and batch_size=2, expect 3 calls: 2, 2, 1
        assert dep.on_new_records.await_count == 3

    @pytest.mark.asyncio
    async def test_sync_site_content_error_increments_failed(self):
        """Test that site errors increment sites_failed counter."""
        connector, dep, *_ = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()

        site_rg = MagicMock()
        site_rg.external_group_id = "site-1"
        site_rg.name = "Fail Site"
        site_rg.id = "internal-site-1"

        async def fail_drives(*args, **kwargs):
            raise Exception("API failure")
            yield

        connector._process_site_drives = fail_drives
        connector._get_date_filters = MagicMock(return_value=(None, None, None, None))

        with pytest.raises(Exception, match="API failure"):
            await connector._sync_site_content(site_rg)

        assert connector.stats['sites_failed'] == 1


# ===========================================================================
# Deep Sync: _process_site_pages
# ===========================================================================


class TestProcessSitePages:

    @pytest.mark.asyncio
    async def test_pages_no_pages_found(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_pages_new_page_yielded(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()

        page = _make_mock_page("page-1", "Welcome")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[page]))
        connector._get_page_permissions = AsyncMock(return_value=[])
        connector._pass_page_date_filters = MagicMock(return_value=True)

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 1
        record, perms, update = results[0]
        assert update.is_new is True
        assert connector.stats['pages_processed'] == 1

    @pytest.mark.asyncio
    async def test_pages_system_page_skipped(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()

        # Create system account page
        system_page = _make_mock_page("sp-1", "System Page")
        system_page.created_by.user.display_name = "System Account"
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[system_page]))

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_pages_existing_page_updated(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()

        page = _make_mock_page("page-2", "Updated Page", e_tag="new-etag")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[page]))
        connector._get_page_permissions = AsyncMock(return_value=[])
        connector._pass_page_date_filters = MagicMock(return_value=True)

        # Existing record with different etag
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old-etag"
        existing.version = 1
        existing.record_status = ProgressStatus.COMPLETED.value
        tx.get_record_by_external_id = AsyncMock(return_value=existing)

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 1
        record, perms, update = results[0]
        assert update.is_updated is True
        assert update.content_changed is True

    @pytest.mark.asyncio
    async def test_pages_date_filter_skips_page(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()

        page = _make_mock_page("page-3", "Filtered Page")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[page]))
        connector._pass_page_date_filters = MagicMock(return_value=False)

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_pages_no_id_skipped(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()

        no_id_page = MagicMock()
        no_id_page.id = None
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[no_id_page]))

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_pages_indexing_filter_off(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()

        connector.indexing_filters = MagicMock()
        connector.indexing_filters.is_enabled = MagicMock(return_value=False)

        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.page_sync_point.update_sync_point = AsyncMock()

        page = _make_mock_page("page-idx", "IndexOff Page")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[page]))
        connector._get_page_permissions = AsyncMock(return_value=[])
        connector._pass_page_date_filters = MagicMock(return_value=True)

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 1
        assert results[0][0].indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_pages_with_last_sync_time(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.sync_filters = FilterCollection()
        connector.indexing_filters = FilterCollection()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.page_sync_point = MagicMock()
        connector.page_sync_point.read_sync_point = AsyncMock(
            return_value={'lastSyncTime': '2024-06-01T00:00:00Z'}
        )
        connector.page_sync_point.update_sync_point = AsyncMock()

        page = _make_mock_page("page-inc", "Incremental Page")
        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[page]))
        connector._get_page_permissions = AsyncMock(return_value=[])
        connector._pass_page_date_filters = MagicMock(return_value=True)

        results = []
        async for r in connector._process_site_pages("site-1", "Test Site"):
            results.append(r)

        assert len(results) == 1


# ===========================================================================
# Deep Sync: _process_site_lists
# ===========================================================================


class TestProcessSiteLists:

    @pytest.mark.asyncio
    async def test_no_lists_returns_empty(self):
        connector, dep, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()
        connector._safe_api_call = AsyncMock(return_value=None)

        results = []
        async for r in connector._process_site_lists("site-1"):
            results.append(r)

        assert len(results) == 0

    @pytest.mark.asyncio
    async def test_lists_with_items(self):
        connector, dep, *_ = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        list_obj = MagicMock()
        list_obj.id = "list-1"
        list_obj.display_name = "Tasks"
        list_obj.name = "Tasks"
        list_obj.web_url = "https://contoso.sharepoint.com/Lists/Tasks"
        list_obj.e_tag = "list-etag"
        list_obj.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        list_obj.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "genericList"
        list_obj.list.item_count = 5

        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[list_obj]))
        connector._get_list_permissions = AsyncMock(return_value=[])

        # Mock _process_list_items to yield nothing
        async def empty_items(*args, **kwargs):
            return
            yield

        connector._process_list_items = empty_items

        results = []
        async for r in connector._process_site_lists("site-1"):
            results.append(r)

        assert len(results) == 1  # The list record itself
        assert results[0][2].is_new is True

    @pytest.mark.asyncio
    async def test_hidden_list_skipped(self):
        connector, dep, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        hidden_list = MagicMock()
        hidden_list.id = "hidden-1"
        hidden_list.display_name = "Hidden List"
        hidden_list.list = MagicMock()
        hidden_list.list.hidden = True

        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[hidden_list]))

        results = []
        async for r in connector._process_site_lists("site-1"):
            results.append(r)

        assert len(results) == 0


# ===========================================================================
# Deep Sync: _process_list_items
# ===========================================================================


class TestProcessListItems:

    @pytest.mark.asyncio
    async def test_list_items_paginated(self):
        connector, dep, dsp, cs, tx = _make_connector()
        connector.connector_name = Connectors.SHAREPOINT_ONLINE
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.list_sync_point = MagicMock()
        connector.list_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.list_sync_point.update_sync_point = AsyncMock()

        item = MagicMock()
        item.id = "li-1"
        item.e_tag = "item-etag"
        item.web_url = "https://contoso.sharepoint.com/Lists/Tasks/1"
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.fields = MagicMock()
        item.fields.additional_data = {"Title": "Task 1"}
        item.content_type = MagicMock()
        item.content_type.name = "Item"

        connector._safe_api_call = AsyncMock(return_value=MagicMock(
            value=[item],
            odata_next_link=None
        ))
        connector._get_list_item_permissions = AsyncMock(return_value=[])

        results = []
        async for r in connector._process_list_items("site-1", "list-1"):
            results.append(r)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_list_items_empty_response(self):
        connector, dep, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector.list_sync_point = MagicMock()
        connector.list_sync_point.read_sync_point = AsyncMock(return_value=None)

        connector._safe_api_call = AsyncMock(return_value=None)

        results = []
        async for r in connector._process_list_items("site-1", "list-1"):
            results.append(r)

        assert len(results) == 0


# ===========================================================================
# Deep Sync: _convert_to_permissions (SharePoint)
# ===========================================================================


class TestSharePointConvertToPermissions:

    @pytest.mark.asyncio
    async def test_user_via_granted_to_v2(self):
        connector, *_ = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        user = MagicMock()
        user.id = "user-1"
        user.additional_data = {"email": "user@test.com"}
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = user
        perm.granted_to_v2.group = None
        perm.granted_to_identities_v2 = None
        perm.link = None

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.USER

    @pytest.mark.asyncio
    async def test_group_via_granted_to_v2(self):
        connector, *_ = _make_connector()
        perm = MagicMock()
        perm.roles = ["write"]
        perm.granted_to_v2 = MagicMock()
        perm.granted_to_v2.user = None
        group = MagicMock()
        group.id = "g1"
        group.additional_data = {"email": "group@test.com"}
        perm.granted_to_v2.group = group
        perm.granted_to_identities_v2 = None
        perm.link = None

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_group_via_identities_v2(self):
        connector, *_ = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        perm.granted_to_v2 = None

        identity = MagicMock()
        identity.user = None
        group = MagicMock()
        group.id = "g2"
        group.additional_data = {}
        identity.group = group
        perm.granted_to_identities_v2 = [identity]
        perm.link = None

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.GROUP

    @pytest.mark.asyncio
    async def test_anonymous_link(self):
        connector, *_ = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        link = MagicMock()
        link.scope = "anonymous"
        link.type = "read"
        perm.link = link

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.ANYONE_WITH_LINK

    @pytest.mark.asyncio
    async def test_organization_link(self):
        connector, *_ = _make_connector()
        perm = MagicMock()
        perm.roles = ["read"]
        perm.granted_to_v2 = None
        perm.granted_to_identities_v2 = None
        link = MagicMock()
        link.scope = "organization"
        link.type = "edit"
        perm.link = link

        result = await connector._convert_to_permissions([perm])
        assert len(result) == 1
        assert result[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_empty_permissions_list(self):
        connector, *_ = _make_connector()
        result = await connector._convert_to_permissions([])
        assert result == []

    @pytest.mark.asyncio
    async def test_permission_error_continues(self):
        """Exception processing one perm should not stop processing."""
        connector, *_ = _make_connector()

        bad_perm = MagicMock()
        bad_perm.roles = None  # Will cause error
        bad_perm.granted_to_v2 = MagicMock()
        bad_perm.granted_to_v2.user = MagicMock()
        bad_perm.granted_to_v2.user.id = "u1"
        bad_perm.granted_to_v2.user.additional_data = None
        bad_perm.granted_to_v2.group = None
        bad_perm.granted_to_identities_v2 = None
        bad_perm.link = None

        # This will raise because roles is None -> perm.roles[0] fails
        result = await connector._convert_to_permissions([bad_perm])
        # Should not raise, just skip the bad permission
        assert isinstance(result, list)


# ===========================================================================
# Deep Sync: _get_item_permissions
# ===========================================================================


class TestGetItemPermissions:

    @pytest.mark.asyncio
    async def test_get_item_permissions_success(self):
        connector, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        perm_obj = MagicMock()
        perm_obj.roles = ["read"]
        perm_obj.granted_to_v2 = None
        perm_obj.granted_to_identities_v2 = None
        perm_obj.link = MagicMock()
        perm_obj.link.scope = "anonymous"
        perm_obj.link.type = "read"

        connector._safe_api_call = AsyncMock(return_value=MagicMock(value=[perm_obj]))

        result = await connector._get_item_permissions("site-1", "drive-1", "item-1")
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_get_item_permissions_error_returns_empty(self):
        connector, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector._safe_api_call = AsyncMock(side_effect=Exception("API error"))

        result = await connector._get_item_permissions("site-1", "drive-1", "item-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_get_item_permissions_empty_response(self):
        connector, *_ = _make_connector()
        connector.client = MagicMock()
        connector.rate_limiter = AsyncMock()
        connector.rate_limiter.__aenter__ = AsyncMock()
        connector.rate_limiter.__aexit__ = AsyncMock()

        connector._safe_api_call = AsyncMock(return_value=None)

        result = await connector._get_item_permissions("site-1", "drive-1", "item-1")
        assert result == []


# ===========================================================================
# Deep Sync: _handle_record_updates (SharePoint full coverage)
# ===========================================================================


class TestHandleRecordUpdatesDeep:

    @pytest.mark.asyncio
    async def test_handle_updates_error_caught(self):
        connector, dep, *_ = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        dep.on_record_deleted = AsyncMock(side_effect=Exception("DB error"))

        update = RecordUpdate(
            record=None, external_record_id="ext-1",
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        )
        # Should not raise
        await connector._handle_record_updates(update)

    @pytest.mark.asyncio
    async def test_handle_only_permissions_changed(self):
        connector, dep, *_ = _make_connector()
        from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate

        mock_record = MagicMock()
        new_perms = [MagicMock()]

        update = RecordUpdate(
            record=mock_record, is_new=False, is_updated=True, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=True,
            new_permissions=new_perms,
        )
        await connector._handle_record_updates(update)
        dep.on_updated_record_permissions.assert_called_once_with(mock_record, new_perms)
        dep.on_record_metadata_update.assert_not_called()
        dep.on_record_content_update.assert_not_called()
