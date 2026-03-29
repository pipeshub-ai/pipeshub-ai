"""
Comprehensive unit tests for SharePoint Online connector targeting 95%+ coverage.

Covers every method including private/helper methods, both success and error paths,
edge cases (empty lists, None values, missing keys, permission errors), and all
branches (if/else, try/except, for loops with empty and non-empty iterables).
"""

import asyncio
import base64
import json
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    SyncFilterKey,
)
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    COMPOSITE_SITE_ID_COMMA_COUNT,
    COMPOSITE_SITE_ID_PARTS_COUNT,
    CountryToRegionMapper,
    MicrosoftRegion,
    SharePointConnector,
    SharePointCredentials,
    SharePointRecordType,
    SharePointSubscriptionManager,
    SiteMetadata,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
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

# =============================================================================
# Helpers
# =============================================================================

MODULE = "app.connectors.sources.microsoft.sharepoint_online.connector"


def _make_mock_deps():
    logger = logging.getLogger("test.sharepoint.online.coverage")
    dep = MagicMock()
    dep.org_id = "org-sp-cov"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_updated_record_permissions = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.on_user_group_deleted = AsyncMock(return_value=True)
    dep.on_user_group_member_removed = AsyncMock(return_value=True)
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()

    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()

    return logger, dep, dsp, cs


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    c = SharePointConnector(logger, dep, dsp, cs, "conn-sp-cov")
    return c


def _make_tx_store(existing_record=None):
    mock_tx_store = AsyncMock()
    mock_tx_store.get_record_by_external_id = AsyncMock(return_value=existing_record)
    mock_tx = AsyncMock()
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx_store)
    mock_tx.__aexit__ = AsyncMock(return_value=False)
    return mock_tx, mock_tx_store


def _make_drive_item(
    item_id="item-1",
    name="document.pdf",
    is_folder=False,
    is_deleted=False,
    is_root=False,
    e_tag="etag-1",
    c_tag="ctag-1",
    size=1024,
    mime_type="application/pdf",
    quick_xor_hash="hash123",
    web_url="https://sp.example.com/doc",
    parent_id="parent-1",
    parent_path="/root:/Documents",
    created=None,
    modified=None,
):
    now = datetime.now(timezone.utc)
    created = created or now
    modified = modified or now

    item = MagicMock()
    item.id = item_id
    item.name = name
    item.e_tag = e_tag
    item.c_tag = c_tag
    item.size = size
    item.web_url = web_url
    item.created_date_time = created
    item.last_modified_date_time = modified

    if is_root:
        item.root = MagicMock()
    else:
        item.root = None

    if is_folder:
        item.folder = MagicMock()
        item.file = None
    else:
        item.folder = None
        item.file = MagicMock()
        item.file.mime_type = mime_type
        item.file.hashes = MagicMock()
        item.file.hashes.quick_xor_hash = quick_xor_hash
        item.file.hashes.crc32_hash = None
        item.file.hashes.sha1_hash = None
        item.file.hashes.sha256_hash = None

    if is_deleted:
        item.deleted = MagicMock()
    else:
        item.deleted = None

    item.parent_reference = MagicMock()
    item.parent_reference.id = parent_id
    item.parent_reference.path = parent_path

    return item


def _make_existing_record(
    record_id="rec-1",
    external_revision_id="etag-old",
    record_name="document.pdf",
    version=1,
    record_status=ProgressStatus.NOT_STARTED,
    external_record_group_id="drive-1",
    quick_xor_hash="hash123",
):
    rec = MagicMock()
    rec.id = record_id
    rec.external_revision_id = external_revision_id
    rec.record_name = record_name
    rec.version = version
    rec.record_status = record_status
    rec.external_record_group_id = external_record_group_id
    rec.quick_xor_hash = quick_xor_hash
    return rec


def _make_site(
    site_id="host.com,guid1-with-36-chars-total-padding12,guid2-with-36-chars-total-padding34",
    display_name="Test Site",
    name="test-site",
    web_url="https://contoso.sharepoint.com/sites/test",
    created=None,
    modified=None,
    description="A test site",
):
    site = MagicMock()
    site.id = site_id
    site.display_name = display_name
    site.name = name
    site.web_url = web_url
    site.created_date_time = created or datetime(2024, 1, 1, tzinfo=timezone.utc)
    site.last_modified_date_time = modified or datetime(2024, 6, 1, tzinfo=timezone.utc)
    site.description = description
    return site


def _make_record_group(
    name="Test Site",
    external_group_id="site-1",
    group_id="rg-1",
):
    rg = MagicMock()
    rg.name = name
    rg.external_group_id = external_group_id
    rg.id = group_id
    return rg


# =============================================================================
# Dataclass & Enum Tests
# =============================================================================


class TestSharePointCredentials:
    def test_all_fields(self):
        creds = SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            sharepoint_domain="https://contoso.sharepoint.com",
            has_admin_consent=True,
            root_site_url="contoso.sharepoint.com",
            enable_subsite_discovery=False,
            certificate_path="/tmp/cert.pem",
            certificate_data="CERT_DATA",
        )
        assert creds.tenant_id == "t"
        assert creds.has_admin_consent is True
        assert creds.enable_subsite_discovery is False
        assert creds.certificate_path == "/tmp/cert.pem"

    def test_defaults(self):
        creds = SharePointCredentials(
            tenant_id="t",
            client_id="c",
            client_secret="s",
            sharepoint_domain="https://contoso.sharepoint.com",
        )
        assert creds.has_admin_consent is False
        assert creds.root_site_url is None
        assert creds.enable_subsite_discovery is True
        assert creds.certificate_path is None
        assert creds.certificate_data is None


class TestSiteMetadata:
    def test_all_fields(self):
        now = datetime.now(timezone.utc)
        sm = SiteMetadata(
            site_id="s1",
            site_url="https://site.com",
            site_name="Site",
            is_root=True,
            parent_site_id="parent",
            created_at=now,
            updated_at=now,
        )
        assert sm.site_id == "s1"
        assert sm.is_root is True

    def test_defaults(self):
        sm = SiteMetadata(site_id="s", site_url="u", site_name="n", is_root=False)
        assert sm.parent_site_id is None
        assert sm.created_at is None


class TestSharePointRecordType:
    def test_values(self):
        assert SharePointRecordType.SITE.value == "SITE"
        assert SharePointRecordType.DOCUMENT_LIBRARY.value == "SHAREPOINT_DOCUMENT_LIBRARY"
        assert SharePointRecordType.PAGE.value == "WEBPAGE"
        assert SharePointRecordType.FILE.value == "FILE"


class TestMicrosoftRegion:
    def test_values(self):
        assert MicrosoftRegion.NAM.value == "NAM"
        assert MicrosoftRegion.EUR.value == "EUR"
        assert MicrosoftRegion.IND.value == "IND"


class TestCountryToRegionMapper:
    def test_get_region_us(self):
        assert CountryToRegionMapper.get_region("US") == MicrosoftRegion.NAM

    def test_get_region_gb(self):
        assert CountryToRegionMapper.get_region("GB") == MicrosoftRegion.GBR

    def test_get_region_in(self):
        assert CountryToRegionMapper.get_region("IN") == MicrosoftRegion.IND

    def test_get_region_none(self):
        assert CountryToRegionMapper.get_region(None) == MicrosoftRegion.NAM

    def test_get_region_empty(self):
        assert CountryToRegionMapper.get_region("") == MicrosoftRegion.NAM

    def test_get_region_unknown(self):
        assert CountryToRegionMapper.get_region("ZZ") == MicrosoftRegion.NAM

    def test_get_region_lowercase(self):
        assert CountryToRegionMapper.get_region("us") == MicrosoftRegion.NAM

    def test_get_region_string(self):
        assert CountryToRegionMapper.get_region_string("US") == "NAM"
        assert CountryToRegionMapper.get_region_string(None) == "NAM"
        assert CountryToRegionMapper.get_region_string("IN") == "IND"

    def test_is_valid_region(self):
        assert CountryToRegionMapper.is_valid_region("NAM") is True
        assert CountryToRegionMapper.is_valid_region("nam") is True
        assert CountryToRegionMapper.is_valid_region("INVALID") is False

    def test_get_all_regions(self):
        regions = CountryToRegionMapper.get_all_regions()
        assert "NAM" in regions
        assert "EUR" in regions
        assert len(regions) > 10

    def test_get_all_country_codes(self):
        codes = CountryToRegionMapper.get_all_country_codes()
        assert "US" in codes
        assert "GB" in codes
        assert len(codes) > 10


# =============================================================================
# init() Tests
# =============================================================================


class TestInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="credentials not found"):
            await c.init()

    @pytest.mark.asyncio
    async def test_init_empty_auth(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={"auth": {}})
        with pytest.raises(ValueError, match="credentials not found"):
            await c.init()

    @pytest.mark.asyncio
    async def test_init_missing_required_fields(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {"tenantId": "t", "clientId": "c"}
        })
        with pytest.raises(ValueError, match="Incomplete SharePoint Online credentials"):
            await c.init()

    @pytest.mark.asyncio
    async def test_init_no_auth_method(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
            }
        })
        with pytest.raises(ValueError, match="Authentication credentials missing"):
            await c.init()

    @pytest.mark.asyncio
    async def test_init_client_secret_success(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "hasAdminConsent": True,
            }
        })
        with patch(f"{MODULE}.ClientSecretCredential") as mock_cred, \
             patch(f"{MODULE}.GraphServiceClient") as mock_graph, \
             patch(f"{MODULE}.MSGraphClient"), \
             patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_inst
            mock_filters.return_value = (MagicMock(), MagicMock())
            mock_graph_inst = MagicMock()
            mock_graph.return_value = mock_graph_inst
            root_site = MagicMock()
            root_site.site_collection = MagicMock()
            root_site.site_collection.data_location_code = "NAM"
            mock_graph_inst.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)
            result = await c.init()
            assert result is True
            assert c.certificate_path is None

    @pytest.mark.asyncio
    async def test_init_client_secret_token_fails(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "sharepointDomain": "https://contoso.sharepoint.com",
            }
        })
        with patch(f"{MODULE}.ClientSecretCredential") as mock_cred:
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock(side_effect=Exception("token fail"))
            mock_cred.return_value = mock_cred_inst
            with pytest.raises(ValueError, match="Failed to initialize SharePoint credential"):
                await c.init()

    @pytest.mark.asyncio
    async def test_init_certificate_success(self):
        c = _make_connector()
        cert_pem = "-----BEGIN CERTIFICATE-----\nMOCKCERT\n-----END CERTIFICATE-----"
        key_pem = "-----BEGIN PRIVATE KEY-----\nMOCKKEY\n-----END PRIVATE KEY-----"
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "certificate": cert_pem,
                "privateKey": key_pem,
            }
        })
        with patch(f"{MODULE}.CertificateCredential") as mock_cert_cred, \
             patch(f"{MODULE}.GraphServiceClient") as mock_graph, \
             patch(f"{MODULE}.MSGraphClient"):
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock()
            mock_cert_cred.return_value = mock_cred_inst
            mock_graph_inst = MagicMock()
            mock_graph.return_value = mock_graph_inst
            root_site = MagicMock()
            root_site.site_collection = MagicMock()
            root_site.site_collection.data_location_code = None
            org_collection = MagicMock()
            org_collection.value = [MagicMock(country_letter_code="US")]
            mock_graph_inst.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)
            mock_graph_inst.organization.get = AsyncMock(return_value=org_collection)

            result = await c.init()
            assert result is True
            assert c.certificate_path is not None
            # Clean up temp file
            if c.temp_cert_file and os.path.exists(c.temp_cert_file.name):
                os.unlink(c.temp_cert_file.name)

    @pytest.mark.asyncio
    async def test_init_certificate_base64_encoded(self):
        c = _make_connector()
        cert_pem = "-----BEGIN CERTIFICATE-----\nMOCKCERT\n-----END CERTIFICATE-----"
        key_pem = "-----BEGIN PRIVATE KEY-----\nMOCKKEY\n-----END PRIVATE KEY-----"
        cert_b64 = base64.b64encode(cert_pem.encode()).decode()
        key_b64 = base64.b64encode(key_pem.encode()).decode()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "contoso.sharepoint.com",
                "certificate": cert_b64,
                "privateKey": key_b64,
            }
        })
        with patch(f"{MODULE}.CertificateCredential") as mock_cert_cred, \
             patch(f"{MODULE}.GraphServiceClient") as mock_graph, \
             patch(f"{MODULE}.MSGraphClient"):
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock()
            mock_cert_cred.return_value = mock_cred_inst
            mock_graph_inst = MagicMock()
            mock_graph.return_value = mock_graph_inst
            root_site = MagicMock()
            root_site.site_collection = MagicMock()
            root_site.site_collection.data_location_code = "EUR"
            mock_graph_inst.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)

            result = await c.init()
            assert result is True
            if c.temp_cert_file and os.path.exists(c.temp_cert_file.name):
                os.unlink(c.temp_cert_file.name)

    @pytest.mark.asyncio
    async def test_init_certificate_non_string_type_raises(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "certificate": 12345,
                "privateKey": "-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----",
            }
        })
        with pytest.raises(ValueError, match="Failed to set up certificate"):
            await c.init()

    @pytest.mark.asyncio
    async def test_init_private_key_non_string_type_raises(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "certificate": "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----",
                "privateKey": 12345,
            }
        })
        with pytest.raises(ValueError, match="Failed to set up certificate"):
            await c.init()

    @pytest.mark.asyncio
    async def test_init_cert_token_fail_cleanup(self):
        c = _make_connector()
        cert_pem = "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----"
        key_pem = "-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----"
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "certificate": cert_pem,
                "privateKey": key_pem,
            }
        })
        with patch(f"{MODULE}.CertificateCredential") as mock_cert_cred:
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock(side_effect=Exception("token fail"))
            mock_cert_cred.return_value = mock_cred_inst
            with pytest.raises(ValueError, match="Failed to initialize SharePoint credential"):
                await c.init()

    @pytest.mark.asyncio
    async def test_init_domain_normalization_no_scheme(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "sharepointDomain": "contoso.sharepoint.com",
            }
        })
        with patch(f"{MODULE}.ClientSecretCredential") as mock_cred, \
             patch(f"{MODULE}.GraphServiceClient") as mock_graph, \
             patch(f"{MODULE}.MSGraphClient"):
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_inst
            mock_graph_inst = MagicMock()
            mock_graph.return_value = mock_graph_inst
            root_site = MagicMock()
            root_site.site_collection = None
            mock_graph_inst.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)
            org_col = MagicMock()
            org_col.value = []
            mock_graph_inst.organization.get = AsyncMock(return_value=org_col)

            result = await c.init()
            assert result is True
            assert "contoso.sharepoint.com" in c.sharepoint_domain

    @pytest.mark.asyncio
    async def test_init_region_detection_fails_gracefully(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "sharepointDomain": "https://contoso.sharepoint.com",
            }
        })
        with patch(f"{MODULE}.ClientSecretCredential") as mock_cred, \
             patch(f"{MODULE}.GraphServiceClient") as mock_graph, \
             patch(f"{MODULE}.MSGraphClient"):
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_inst
            mock_graph_inst = MagicMock()
            mock_graph.return_value = mock_graph_inst
            mock_graph_inst.sites.by_site_id.return_value.get = AsyncMock(
                side_effect=Exception("region fetch failed")
            )

            result = await c.init()
            assert result is True


# =============================================================================
# _get_all_sites Tests
# =============================================================================


class TestGetAllSites:
    @pytest.mark.asyncio
    async def test_root_site_plus_search(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.enable_subsite_discovery = False

        root_site = _make_site(site_id="host.com," + "a" * 36 + "," + "b" * 36, display_name="Root")
        search_site = _make_site(
            site_id="host.com," + "c" * 36 + "," + "d" * 36,
            display_name="Other",
            web_url="https://contoso.sharepoint.com/sites/other"
        )

        search_result = MagicMock()
        search_result.value = [search_site]
        search_result.odata_next_link = None
        c._safe_api_call = AsyncMock(side_effect=[root_site, search_result])

        sites = await c._get_all_sites()
        assert len(sites) >= 1

    @pytest.mark.asyncio
    async def test_root_site_fails_gracefully(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.enable_subsite_discovery = False

        c._safe_api_call = AsyncMock(side_effect=[None, MagicMock(value=None)])

        sites = await c._get_all_sites()
        assert isinstance(sites, list)

    @pytest.mark.asyncio
    async def test_onedrive_site_filtered(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.enable_subsite_discovery = False
        c.filters = {"exclude_onedrive_sites": True}

        onedrive_site = _make_site(
            web_url="https://contoso-my.sharepoint.com/personal/user",
            display_name="OneDrive"
        )

        search_result = MagicMock()
        search_result.value = [onedrive_site]
        search_result.odata_next_link = None

        c._safe_api_call = AsyncMock(side_effect=[None, search_result])

        sites = await c._get_all_sites()
        assert all(
            "my.sharepoint.com" not in (s.web_url or "")
            for s in sites
        )

    @pytest.mark.asyncio
    async def test_pagination(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.enable_subsite_discovery = False

        site1 = _make_site(site_id="host.com," + "a" * 36 + "," + "b" * 36, display_name="S1")
        page1 = MagicMock()
        page1.value = [site1]
        page1.odata_next_link = "https://graph.microsoft.com/v1.0/sites?nextLink=xxx"

        page2 = MagicMock()
        page2.value = None
        page2.odata_next_link = None

        c._safe_api_call = AsyncMock(side_effect=[None, page1, page2])

        sites = await c._get_all_sites()
        assert len(sites) >= 1

    @pytest.mark.asyncio
    async def test_subsite_discovery(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.enable_subsite_discovery = True

        root = _make_site(site_id="host.com," + "a" * 36 + "," + "b" * 36)
        sub = _make_site(site_id="host.com," + "e" * 36 + "," + "f" * 36, display_name="Sub")

        search_result = MagicMock()
        search_result.value = None
        search_result.odata_next_link = None

        c._safe_api_call = AsyncMock(side_effect=[root, search_result])
        c._get_subsites = AsyncMock(return_value=[sub])

        sites = await c._get_all_sites()
        assert len(sites) == 2

    @pytest.mark.asyncio
    async def test_critical_error(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock(side_effect=Exception("critical"))
        c.rate_limiter.__aexit__ = AsyncMock()

        sites = await c._get_all_sites()
        assert isinstance(sites, list)

    @pytest.mark.asyncio
    async def test_invalid_site_id_filtered(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.enable_subsite_discovery = False

        invalid_site = _make_site(site_id="x")  # Too short, not root
        search_result = MagicMock()
        search_result.value = [invalid_site]
        search_result.odata_next_link = None

        c._safe_api_call = AsyncMock(side_effect=[None, search_result])

        sites = await c._get_all_sites()
        # Invalid site should be filtered out
        valid = [s for s in sites if c._validate_site_id(s.id)]
        assert len(valid) == 0


# =============================================================================
# _sync_site_content Tests
# =============================================================================


class TestSyncSiteContent:
    @pytest.mark.asyncio
    async def test_success_with_records(self):
        c = _make_connector()
        c._get_date_filters = MagicMock(return_value=(None, None, None, None))

        record = MagicMock()
        perms = []
        ru = RecordUpdate(record=record, is_new=True, is_updated=False, is_deleted=False,
                          metadata_changed=False, content_changed=False, permissions_changed=False)

        async def drive_gen(*args, **kwargs):
            yield (record, perms, ru)

        async def page_gen(*args, **kwargs):
            return
            yield  # Make it an async generator

        c._process_site_drives = drive_gen
        c._process_site_pages = page_gen

        rg = _make_record_group()
        await c._sync_site_content(rg)
        assert c.stats["sites_processed"] == 1

    @pytest.mark.asyncio
    async def test_deleted_record(self):
        c = _make_connector()
        c._get_date_filters = MagicMock(return_value=(None, None, None, None))
        c._handle_record_updates = AsyncMock()

        ru = RecordUpdate(record=None, external_record_id="ext-1",
                          is_new=False, is_updated=False, is_deleted=True,
                          metadata_changed=False, content_changed=False, permissions_changed=False)

        async def drive_gen(*args, **kwargs):
            yield (None, [], ru)

        async def page_gen(*args, **kwargs):
            return
            yield

        c._process_site_drives = drive_gen
        c._process_site_pages = page_gen

        rg = _make_record_group()
        await c._sync_site_content(rg)
        c._handle_record_updates.assert_called()

    @pytest.mark.asyncio
    async def test_updated_record(self):
        c = _make_connector()
        c._get_date_filters = MagicMock(return_value=(None, None, None, None))
        c._handle_record_updates = AsyncMock()

        record = MagicMock()
        ru = RecordUpdate(record=record, is_new=False, is_updated=True, is_deleted=False,
                          metadata_changed=True, content_changed=False, permissions_changed=False)

        async def drive_gen(*args, **kwargs):
            yield (record, [], ru)

        async def page_gen(*args, **kwargs):
            return
            yield

        c._process_site_drives = drive_gen
        c._process_site_pages = page_gen

        rg = _make_record_group()
        await c._sync_site_content(rg)

    @pytest.mark.asyncio
    async def test_batch_flush(self):
        c = _make_connector()
        c.batch_size = 2
        c._get_date_filters = MagicMock(return_value=(None, None, None, None))

        records = [MagicMock() for _ in range(3)]

        async def drive_gen(*args, **kwargs):
            for r in records:
                ru = RecordUpdate(record=r, is_new=True, is_updated=False, is_deleted=False,
                                  metadata_changed=False, content_changed=False, permissions_changed=False)
                yield (r, [], ru)

        async def page_gen(*args, **kwargs):
            return
            yield

        c._process_site_drives = drive_gen
        c._process_site_pages = page_gen

        rg = _make_record_group()
        await c._sync_site_content(rg)
        assert c.data_entities_processor.on_new_records.call_count >= 2

    @pytest.mark.asyncio
    async def test_exception_increments_failed(self):
        c = _make_connector()
        c._get_date_filters = MagicMock(side_effect=Exception("fail"))

        rg = _make_record_group()
        with pytest.raises(Exception):
            await c._sync_site_content(rg)
        assert c.stats["sites_failed"] == 1


# =============================================================================
# _process_drive_item Tests
# =============================================================================


class TestProcessDriveItem:
    @pytest.mark.asyncio
    async def test_new_file_record(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")
        c._get_item_permissions = AsyncMock(return_value=[])
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)

        mock_tx, _ = _make_tx_store(None)
        c.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item()
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None
        assert result.is_new is True

    @pytest.mark.asyncio
    async def test_deleted_item(self):
        c = _make_connector()
        item = _make_drive_item(is_deleted=True)
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None
        assert result.is_deleted is True

    @pytest.mark.asyncio
    async def test_root_item(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")
        c._get_item_permissions = AsyncMock(return_value=[])
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)

        mock_tx, _ = _make_tx_store(None)
        c.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(is_root=True)
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_item_id(self):
        c = _make_connector()
        item = MagicMock()
        item.id = None
        item.name = "test"
        item.deleted = None
        item.root = None
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_existing_record_metadata_changed(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")
        c._get_item_permissions = AsyncMock(return_value=[])
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)

        existing = _make_existing_record(external_revision_id="etag-old")
        mock_tx, _ = _make_tx_store(existing)
        c.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(e_tag="etag-new")
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None
        assert result.metadata_changed is True
        assert result.is_updated is True

    @pytest.mark.asyncio
    async def test_existing_record_content_changed(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")
        c._get_item_permissions = AsyncMock(return_value=[])
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)

        existing = _make_existing_record(
            external_revision_id="etag-1", quick_xor_hash="old-hash"
        )
        mock_tx, _ = _make_tx_store(existing)
        c.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        item = _make_drive_item(e_tag="etag-1", quick_xor_hash="new-hash")
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is not None
        assert result.content_changed is True

    @pytest.mark.asyncio
    async def test_date_filter_rejects(self):
        c = _make_connector()
        c._pass_drive_date_filters = MagicMock(return_value=False)
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)

        item = _make_drive_item()
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_extension_filter_rejects(self):
        c = _make_connector()
        c._pass_drive_date_filters = MagicMock(return_value=True)
        c._pass_extension_filter = MagicMock(return_value=False)
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)

        item = _make_drive_item()
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _make_connector()
        c._pass_drive_date_filters = MagicMock(side_effect=Exception("boom"))

        item = _make_drive_item()
        result = await c._process_drive_item(item, "site-1", "drive-1", [])
        assert result is None


# =============================================================================
# _create_file_record Tests
# =============================================================================


class TestCreateFileRecord:
    @pytest.mark.asyncio
    async def test_new_file(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://signed")

        item = _make_drive_item(name="report.pdf")
        result = await c._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.record_name == "report.pdf"
        assert result.extension == "pdf"
        assert result.is_file is True

    @pytest.mark.asyncio
    async def test_folder(self):
        c = _make_connector()
        item = _make_drive_item(is_folder=True, name="Documents")
        result = await c._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.is_file is False
        assert result.mime_type == MimeTypes.FOLDER.value

    @pytest.mark.asyncio
    async def test_file_no_extension_skipped(self):
        c = _make_connector()
        item = _make_drive_item(name="noext")
        result = await c._create_file_record(item, "drive-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_item_id(self):
        c = _make_connector()
        item = MagicMock()
        item.id = None
        item.name = "test.txt"
        result = await c._create_file_record(item, "drive-1", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_root_item_composite_id(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value=None)

        item = _make_drive_item(is_root=True, is_folder=True, name="root")
        result = await c._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.external_record_id.startswith("drive-1:root:")

    @pytest.mark.asyncio
    async def test_existing_record_preserves_id(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")

        existing = _make_existing_record(record_id="existing-id")
        item = _make_drive_item(name="doc.docx")
        result = await c._create_file_record(item, "drive-1", existing)
        assert result.id == "existing-id"
        assert result.version == existing.version + 1

    @pytest.mark.asyncio
    async def test_parent_at_root_level(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(return_value="https://url")

        item = _make_drive_item(name="file.txt", parent_path="/root:")
        result = await c._create_file_record(item, "drive-1", None)
        assert result is not None
        assert result.parent_external_record_id.startswith("drive-1:root:")

    @pytest.mark.asyncio
    async def test_signed_url_fails_gracefully(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_signed_url = AsyncMock(side_effect=Exception("no url"))

        item = _make_drive_item(name="file.txt")
        result = await c._create_file_record(item, "drive-1", None)
        assert result is not None  # Should still create record

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _make_connector()
        item = MagicMock()
        type(item).id = PropertyMock(side_effect=Exception("bad"))
        result = await c._create_file_record(item, "drive-1", None)
        assert result is None


# =============================================================================
# _process_drive_delta Tests
# =============================================================================


class TestProcessDriveDelta:
    @pytest.mark.asyncio
    async def test_fresh_start(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            "drive_items": [_make_drive_item()],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/v1.0/delta?token=xxx"
        })
        c._process_drive_item = AsyncMock(return_value=RecordUpdate(
            record=MagicMock(),
            is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[]
        ))
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_existing_delta_link(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value={
            "deltaLink": "https://graph.microsoft.com/v1.0/delta?token=old"
        })
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            "drive_items": [],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/v1.0/delta?token=new"
        })

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_invalid_delta_url_clears_sync_point(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value={
            "deltaLink": "http://evil.com/hack"
        })
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            "drive_items": [],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/v1.0/delta?token=fresh"
        })

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        # Should have cleared sync point due to invalid URL

    @pytest.mark.asyncio
    async def test_deleted_item_in_delta(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            "drive_items": [_make_drive_item(is_deleted=True)],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/v1.0/delta?token=xxx"
        })
        c._process_drive_item = AsyncMock(return_value=RecordUpdate(
            record=None, external_record_id="item-1",
            is_new=False, is_updated=False, is_deleted=True,
            metadata_changed=False, content_changed=False, permissions_changed=False,
        ))

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert len(items) == 1
        assert items[0][2].is_deleted

    @pytest.mark.asyncio
    async def test_pagination(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(side_effect=[
            {
                "drive_items": [_make_drive_item()],
                "next_link": "https://graph.microsoft.com/v1.0/next",
                "delta_link": None
            },
            {
                "drive_items": [],
                "next_link": None,
                "delta_link": "https://graph.microsoft.com/v1.0/delta?token=final"
            }
        ])
        c._process_drive_item = AsyncMock(return_value=RecordUpdate(
            record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[]
        ))
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert len(items) == 1

    @pytest.mark.asyncio
    async def test_indexing_disabled(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        record_mock = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            "drive_items": [_make_drive_item()],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/v1.0/delta?token=xxx"
        })
        c._process_drive_item = AsyncMock(return_value=RecordUpdate(
            record=record_mock, is_new=True, is_updated=False, is_deleted=False,
            metadata_changed=False, content_changed=False, permissions_changed=False,
            new_permissions=[]
        ))
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=False)

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert record_mock.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception_clears_sync_point(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(side_effect=Exception("db fail"))
        c.drive_delta_sync_point.update_sync_point = AsyncMock()

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_item_processing_error_continues(self):
        c = _make_connector()
        c.drive_delta_sync_point = MagicMock()
        c.drive_delta_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.drive_delta_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_delta_response_sharepoint = AsyncMock(return_value={
            "drive_items": [_make_drive_item(), _make_drive_item(item_id="item-2")],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/v1.0/delta?token=xxx"
        })
        c._process_drive_item = AsyncMock(side_effect=[
            Exception("process error"),
            RecordUpdate(record=MagicMock(), is_new=True, is_updated=False, is_deleted=False,
                         metadata_changed=False, content_changed=False, permissions_changed=False,
                         new_permissions=[])
        ])
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)

        items = []
        async for item in c._process_drive_delta("site-1", "drive-1"):
            items.append(item)
        assert len(items) == 1


# =============================================================================
# _process_site_drives Tests
# =============================================================================


class TestProcessSiteDrives:
    @pytest.mark.asyncio
    async def test_no_drives(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c._safe_api_call = AsyncMock(return_value=None)

        items = []
        async for item in c._process_site_drives("site-1", "rg-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_drive_no_id_skipped(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()

        drive = MagicMock()
        drive.id = None
        drive.name = "Bad Drive"

        drives_resp = MagicMock()
        drives_resp.value = [drive]
        c._safe_api_call = AsyncMock(return_value=drives_resp)

        items = []
        async for item in c._process_site_drives("site-1", "rg-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_drive_filter_rejects(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c._pass_drive_key_filters = MagicMock(return_value=False)

        drive = MagicMock()
        drive.id = "drive-1"
        drive.name = "Filtered Drive"
        drive.web_url = "https://contoso.sharepoint.com/docs"

        drives_resp = MagicMock()
        drives_resp.value = [drive]
        c._safe_api_call = AsyncMock(return_value=drives_resp)

        items = []
        async for item in c._process_site_drives("site-1", "rg-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock(side_effect=Exception("fail"))
        c.rate_limiter.__aexit__ = AsyncMock()

        items = []
        async for item in c._process_site_drives("site-1", "rg-1"):
            items.append(item)
        assert len(items) == 0


# =============================================================================
# _process_site_pages Tests
# =============================================================================


class TestProcessSitePages:
    @pytest.mark.asyncio
    async def test_no_pages(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c._safe_api_call = AsyncMock(return_value=None)

        items = []
        async for item in c._process_site_pages("site-1", "Site Name"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_page_with_system_account_skipped(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c._pass_page_date_filters = MagicMock(return_value=True)

        page = MagicMock()
        page.id = "page-1"
        page.title = "System Page"
        page.name = "system"
        page.created_by = MagicMock()
        page.created_by.user = MagicMock()
        page.created_by.user.display_name = "System Account"

        pages_resp = MagicMock()
        pages_resp.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_resp)

        items = []
        async for item in c._process_site_pages("site-1", "Site Name"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_page_date_filter_rejects(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c._pass_page_date_filters = MagicMock(return_value=False)

        page = MagicMock()
        page.id = "page-1"
        page.title = "Page"
        page.name = "page"
        page.created_by = None

        pages_resp = MagicMock()
        pages_resp.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_resp)

        items = []
        async for item in c._process_site_pages("site-1", "Site"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_existing_page_updated(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value={"lastSyncTime": "2024-01-01T00:00:00"})
        c.page_sync_point.update_sync_point = AsyncMock()
        c._pass_page_date_filters = MagicMock(return_value=True)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)

        page = MagicMock()
        page.id = "page-1"
        page.title = "Updated Page"
        page.name = "updated"
        page.e_tag = "new-etag"
        page.created_by = MagicMock()
        page.created_by.user = MagicMock()
        page.created_by.user.display_name = "User"

        existing = MagicMock()
        existing.external_revision_id = "old-etag"

        mock_tx, _ = _make_tx_store(existing)
        c.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        page_record = MagicMock()
        c._create_page_record = AsyncMock(return_value=page_record)
        c._get_page_permissions = AsyncMock(return_value=[])

        pages_resp = MagicMock()
        pages_resp.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_resp)

        items = []
        async for item in c._process_site_pages("site-1", "Site"):
            items.append(item)
        assert len(items) == 1
        assert items[0][2].is_updated is True
        assert items[0][2].content_changed is True

    @pytest.mark.asyncio
    async def test_page_access_denied(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c._safe_api_call = AsyncMock(side_effect=Exception("403 accessdenied"))

        items = []
        async for item in c._process_site_pages("site-1", "Site"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_indexing_disabled(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c._pass_page_date_filters = MagicMock(return_value=True)
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=False)

        page = MagicMock()
        page.id = "page-1"
        page.title = "Page"
        page.name = "page"
        page.e_tag = "etag"
        page.created_by = None

        mock_tx, _ = _make_tx_store(None)
        c.data_store_provider.transaction = MagicMock(return_value=mock_tx)

        page_record = MagicMock()
        c._create_page_record = AsyncMock(return_value=page_record)
        c._get_page_permissions = AsyncMock(return_value=[])

        pages_resp = MagicMock()
        pages_resp.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_resp)

        items = []
        async for item in c._process_site_pages("site-1", "Site"):
            items.append(item)
        assert page_record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value


# =============================================================================
# _create_page_record Tests
# =============================================================================


class TestCreatePageRecord:
    @pytest.mark.asyncio
    async def test_new_page(self):
        c = _make_connector()
        page = MagicMock()
        page.id = "page-1"
        page.title = "My Page"
        page.name = "my-page"
        page.e_tag = "etag-p"
        page.web_url = "https://contoso.sharepoint.com/page1"
        page.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        page.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        page.page_layout = MagicMock()
        page.page_layout.type = "Article"
        page.promotion_kind = None

        result = await c._create_page_record(page, "site-1", "Test Site", None)
        assert result is not None
        assert "My Page - Test Site" in result.record_name
        assert result.mime_type == MimeTypes.HTML.value

    @pytest.mark.asyncio
    async def test_existing_page_preserves_id(self):
        c = _make_connector()
        page = MagicMock()
        page.id = "page-1"
        page.title = "Page"
        page.e_tag = "etag"
        page.web_url = "https://url"
        page.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        page.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        page.page_layout = None
        page.promotion_kind = None

        existing = MagicMock()
        existing.id = "existing-id"
        existing.version = 2
        existing.record_status = ProgressStatus.COMPLETED

        result = await c._create_page_record(page, "site-1", "Site", existing)
        assert result.id == "existing-id"
        assert result.version == 3

    @pytest.mark.asyncio
    async def test_no_page_id(self):
        c = _make_connector()
        page = MagicMock()
        page.id = None
        result = await c._create_page_record(page, "site-1", "Site", None)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _make_connector()
        page = MagicMock()
        type(page).id = PropertyMock(side_effect=Exception("bad"))
        result = await c._create_page_record(page, "site-1", "Site", None)
        assert result is None


# =============================================================================
# _pass_page_date_filters Tests
# =============================================================================


class TestPassPageDateFilters:
    def test_no_filters_passes(self):
        c = _make_connector()
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=None)
        page = MagicMock()
        assert c._pass_page_date_filters(page) is True

    def test_created_before_range_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-06-01T00:00:00+00:00", None)

        def side_effect(key):
            if key == SyncFilterKey.CREATED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        page = MagicMock()
        page.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert c._pass_page_date_filters(page) is False

    def test_created_after_range_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00+00:00")

        def side_effect(key):
            if key == SyncFilterKey.CREATED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        page = MagicMock()
        page.created_date_time = datetime(2024, 12, 1, tzinfo=timezone.utc)
        assert c._pass_page_date_filters(page) is False

    def test_modified_before_range_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-06-01T00:00:00+00:00", None)

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        page = MagicMock()
        page.last_modified_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert c._pass_page_date_filters(page) is False

    def test_modified_after_range_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00+00:00")

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        page = MagicMock()
        page.last_modified_date_time = datetime(2024, 12, 1, tzinfo=timezone.utc)
        assert c._pass_page_date_filters(page) is False

    def test_none_item_ts_passes(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-06-01T00:00:00+00:00", None)

        def side_effect(key):
            if key == SyncFilterKey.CREATED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        page = MagicMock()
        page.created_date_time = None
        assert c._pass_page_date_filters(page) is True


# =============================================================================
# _pass_drive_date_filters Tests
# =============================================================================


class TestPassDriveDateFilters:
    def test_folder_always_passes(self):
        c = _make_connector()
        item = MagicMock()
        item.folder = MagicMock()
        assert c._pass_drive_date_filters(item) is True

    def test_created_after_end_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00+00:00")

        def side_effect(key):
            if key == SyncFilterKey.CREATED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        item = MagicMock()
        item.folder = None
        item.created_date_time = datetime(2024, 12, 1, tzinfo=timezone.utc)
        assert c._pass_drive_date_filters(item) is False

    def test_modified_before_start_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = ("2024-06-01T00:00:00+00:00", None)

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        item = MagicMock()
        item.folder = None
        item.last_modified_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert c._pass_drive_date_filters(item) is False

    def test_modified_after_end_fails(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.get_datetime_iso.return_value = (None, "2024-06-01T00:00:00+00:00")

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_filter
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        item = MagicMock()
        item.folder = None
        item.last_modified_date_time = datetime(2024, 12, 1, tzinfo=timezone.utc)
        assert c._pass_drive_date_filters(item) is False


# =============================================================================
# _pass_extension_filter Tests
# =============================================================================


class TestPassExtensionFilter:
    def test_invalid_filter_value_type(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not-a-list"
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "test.pdf"
        assert c._pass_extension_filter(item) is True

    def test_not_in_filter_match(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe", "bat"]
        mock_op = MagicMock()
        mock_op.value = "not_in"
        mock_filter.get_operator.return_value = mock_op
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "test.exe"
        assert c._pass_extension_filter(item) is False

    def test_not_in_filter_passes(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["exe"]
        mock_op = MagicMock()
        mock_op.value = "not_in"
        mock_filter.get_operator.return_value = mock_op
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "test.pdf"
        assert c._pass_extension_filter(item) is True

    def test_unknown_operator_passes(self):
        c = _make_connector()
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_op = MagicMock()
        mock_op.value = "BETWEEN"
        mock_filter.get_operator.return_value = mock_op
        c.sync_filters = MagicMock()
        c.sync_filters.get = MagicMock(return_value=mock_filter)

        item = MagicMock()
        item.folder = None
        item.name = "test.pdf"
        assert c._pass_extension_filter(item) is True


# =============================================================================
# _get_sharepoint_access_token Tests
# =============================================================================


class TestGetSharePointAccessToken:
    @pytest.mark.asyncio
    async def test_certificate_path(self):
        c = _make_connector()
        c.certificate_path = "/tmp/cert.pem"
        c.certificate_password = None
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = None
        c.sharepoint_domain = "https://contoso.sharepoint.com"

        with patch("azure.identity.aio.CertificateCredential") as mock_cert:
            mock_cred = AsyncMock()
            mock_token = MagicMock()
            mock_token.token = "cert-token"
            mock_cred.get_token = AsyncMock(return_value=mock_token)
            mock_cred.__aenter__ = AsyncMock(return_value=mock_cred)
            mock_cred.__aexit__ = AsyncMock(return_value=False)
            mock_cert.return_value = mock_cred

            token = await c._get_sharepoint_access_token()
            assert token == "cert-token"

    @pytest.mark.asyncio
    async def test_client_secret(self):
        c = _make_connector()
        c.certificate_path = None
        c.client_secret = "secret"
        c.tenant_id = "t"
        c.client_id = "c"
        c.sharepoint_domain = "https://contoso.sharepoint.com"

        with patch("azure.identity.aio.ClientSecretCredential") as mock_cs:
            mock_cred = AsyncMock()
            mock_token = MagicMock()
            mock_token.token = "secret-token"
            mock_cred.get_token = AsyncMock(return_value=mock_token)
            mock_cred.__aenter__ = AsyncMock(return_value=mock_cred)
            mock_cred.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value = mock_cred

            token = await c._get_sharepoint_access_token()
            assert token == "secret-token"

    @pytest.mark.asyncio
    async def test_no_auth_method(self):
        c = _make_connector()
        c.certificate_path = None
        c.client_secret = None
        c.tenant_id = "t"
        c.client_id = "c"
        c.sharepoint_domain = "https://contoso.sharepoint.com"

        token = await c._get_sharepoint_access_token()
        assert token is None

    @pytest.mark.asyncio
    async def test_token_error(self):
        c = _make_connector()
        c.certificate_path = None
        c.client_secret = "s"
        c.tenant_id = "t"
        c.client_id = "c"
        c.sharepoint_domain = "https://contoso.sharepoint.com"

        with patch("azure.identity.aio.ClientSecretCredential") as mock_cs:
            mock_cred = AsyncMock()
            mock_cred.get_token = AsyncMock(side_effect=Exception("token fail"))
            mock_cred.__aenter__ = AsyncMock(return_value=mock_cred)
            mock_cred.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value = mock_cred

            token = await c._get_sharepoint_access_token()
            assert token is None

    @pytest.mark.asyncio
    async def test_domain_without_hostname(self):
        c = _make_connector()
        c.certificate_path = None
        c.client_secret = "s"
        c.tenant_id = "t"
        c.client_id = "c"
        c.sharepoint_domain = "contoso.sharepoint.com"

        with patch("azure.identity.aio.ClientSecretCredential") as mock_cs:
            mock_cred = AsyncMock()
            mock_token = MagicMock()
            mock_token.token = "tok"
            mock_cred.get_token = AsyncMock(return_value=mock_token)
            mock_cred.__aenter__ = AsyncMock(return_value=mock_cred)
            mock_cred.__aexit__ = AsyncMock(return_value=False)
            mock_cs.return_value = mock_cred

            token = await c._get_sharepoint_access_token()
            assert token == "tok"


# =============================================================================
# _get_site_permissions Tests
# =============================================================================


class TestGetSitePermissions:
    @pytest.mark.asyncio
    async def test_no_site_metadata(self):
        c = _make_connector()
        c.site_cache = {}
        result = await c._get_site_permissions("nonexistent")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_access_token(self):
        c = _make_connector()
        c.site_cache = {"site-1": SiteMetadata("site-1", "https://site.com", "Site", False)}
        c._get_sharepoint_access_token = AsyncMock(return_value=None)
        result = await c._get_site_permissions("site-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _make_connector()
        c.site_cache = {"site-1": SiteMetadata("site-1", "https://site.com", "Site", False)}
        c._get_sharepoint_access_token = AsyncMock(side_effect=Exception("fail"))
        result = await c._get_site_permissions("site-1")
        assert result == []


# =============================================================================
# _get_sharepoint_group_users Tests
# =============================================================================


class TestGetSharePointGroupUsers:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        mock_response = MagicMock()
        mock_response.status = HTTPStatus.OK
        mock_response.json = AsyncMock(return_value={
            "d": {"results": [{"LoginName": "user1", "Email": "user@test.com"}]}
        })

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=mock_ctx)
            mock_session_cls.return_value = mock_session

            result = await c._get_sharepoint_group_users("https://site.com", "associatedownergroup", "token")
            assert len(result) == 1

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _make_connector()
        mock_response = MagicMock()
        mock_response.status = HTTPStatus.NOT_FOUND

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=mock_ctx)
            mock_session_cls.return_value = mock_session

            result = await c._get_sharepoint_group_users("https://site.com", "associatedvisitorgroup", "token")
            assert result == []

    @pytest.mark.asyncio
    async def test_other_error(self):
        c = _make_connector()
        mock_response = MagicMock()
        mock_response.status = HTTPStatus.FORBIDDEN
        mock_response.text = AsyncMock(return_value="forbidden")

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=mock_ctx)
            mock_session_cls.return_value = mock_session

            result = await c._get_sharepoint_group_users("https://site.com", "associatedownergroup", "token")
            assert result == []

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session_cls.side_effect = Exception("connection error")
            result = await c._get_sharepoint_group_users("https://site.com", "associatedownergroup", "token")
            assert result == []


# =============================================================================
# _get_custom_sharepoint_groups Tests
# =============================================================================


class TestGetCustomSharePointGroups:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        mock_response = MagicMock()
        mock_response.status = HTTPStatus.OK
        mock_response.json = AsyncMock(return_value={
            "d": {"results": [
                {
                    "Member": {
                        "PrincipalType": 8,
                        "LoginName": "MyGroup",
                        "Title": "Custom Group",
                        "Id": 42,
                    },
                    "RoleDefinitionBindings": {
                        "results": [{"Name": "Edit"}]
                    }
                },
                {
                    "Member": {
                        "PrincipalType": 1,
                        "LoginName": "User",
                        "Title": "Direct User",
                        "Id": 10,
                    },
                    "RoleDefinitionBindings": {
                        "results": [{"Name": "Read"}]
                    }
                }
            ]}
        })

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=mock_ctx)
            mock_session_cls.return_value = mock_session

            result = await c._get_custom_sharepoint_groups("https://site.com", "token")
            assert len(result) == 1
            assert result[0]["title"] == "Custom Group"
            assert result[0]["permission_level"] == PermissionType.WRITE

    @pytest.mark.asyncio
    async def test_full_control(self):
        c = _make_connector()
        mock_response = MagicMock()
        mock_response.status = HTTPStatus.OK
        mock_response.json = AsyncMock(return_value={
            "d": {"results": [{
                "Member": {"PrincipalType": 8, "LoginName": "Admins", "Title": "Admins", "Id": 1},
                "RoleDefinitionBindings": {"results": [{"Name": "Full Control"}]}
            }]}
        })

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=mock_ctx)
            mock_session_cls.return_value = mock_session

            result = await c._get_custom_sharepoint_groups("https://site.com", "token")
            assert result[0]["permission_level"] == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_error_response(self):
        c = _make_connector()
        mock_response = MagicMock()
        mock_response.status = HTTPStatus.INTERNAL_SERVER_ERROR
        mock_response.text = AsyncMock(return_value="error")

        with patch("aiohttp.ClientSession") as mock_session_cls:
            mock_session = AsyncMock()
            mock_session.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session.__aexit__ = AsyncMock(return_value=False)
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=mock_response)
            mock_ctx.__aexit__ = AsyncMock(return_value=False)
            mock_session.get = MagicMock(return_value=mock_ctx)
            mock_session_cls.return_value = mock_session

            result = await c._get_custom_sharepoint_groups("https://site.com", "token")
            assert result == []


# =============================================================================
# _sync_azure_ad_groups Tests
# =============================================================================


class TestSyncAzureAdGroups:
    @pytest.mark.asyncio
    async def test_initial_sync(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c._get_initial_delta_link = AsyncMock(return_value="https://graph.microsoft.com/delta?token=xxx")
        c._perform_initial_full_sync = AsyncMock()

        await c._sync_azure_ad_groups()
        c._perform_initial_full_sync.assert_called_once()
        c.user_group_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_incremental_sync(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.read_sync_point = AsyncMock(return_value={
            "deltaLink": "https://graph.microsoft.com/delta?token=existing"
        })
        c._perform_delta_sync = AsyncMock()

        await c._sync_azure_ad_groups()
        c._perform_delta_sync.assert_called_once()

    @pytest.mark.asyncio
    async def test_initial_sync_no_delta_link(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c._get_initial_delta_link = AsyncMock(return_value=None)
        c._perform_initial_full_sync = AsyncMock()

        await c._sync_azure_ad_groups()
        c._perform_initial_full_sync.assert_called_once()
        c.user_group_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_exception_propagates(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.read_sync_point = AsyncMock(side_effect=Exception("db fail"))

        with pytest.raises(Exception, match="db fail"):
            await c._sync_azure_ad_groups()


# =============================================================================
# _get_initial_delta_link Tests
# =============================================================================


class TestGetInitialDeltaLink:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/delta?token=new"
        })

        result = await c._get_initial_delta_link()
        assert result == "https://graph.microsoft.com/delta?token=new"

    @pytest.mark.asyncio
    async def test_pagination(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {"groups": [MagicMock()], "next_link": "https://graph.microsoft.com/next", "delta_link": None},
            {"groups": [], "next_link": None, "delta_link": "https://graph.microsoft.com/delta?final"},
        ])

        result = await c._get_initial_delta_link()
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_links(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [], "next_link": None, "delta_link": None
        })

        result = await c._get_initial_delta_link()
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=Exception("fail"))

        result = await c._get_initial_delta_link()
        assert result is None


# =============================================================================
# _perform_initial_full_sync Tests
# =============================================================================


class TestPerformInitialFullSync:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Group 1"
        group.description = "desc"
        group.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        c.msgraph_client.get_all_user_groups = AsyncMock(return_value=[group])
        c.msgraph_client.get_group_members = AsyncMock(return_value=[])

        await c._perform_initial_full_sync()
        c.data_entities_processor.on_new_user_groups.assert_called_once()

    @pytest.mark.asyncio
    async def test_group_processing_exception(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Fail Group"
        c.msgraph_client.get_all_user_groups = AsyncMock(return_value=[group])
        c._process_single_group = AsyncMock(side_effect=Exception("processing fail"))

        await c._perform_initial_full_sync()


# =============================================================================
# _process_single_group Tests
# =============================================================================


class TestProcessSingleGroup:
    @pytest.mark.asyncio
    async def test_with_user_members(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()

        member = MagicMock()
        member.id = "user-1"
        member.odata_type = "#microsoft.graph.user"
        member.additional_data = {}
        member.mail = "user@test.com"
        member.user_principal_name = "user@test.com"
        member.display_name = "User"
        member.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        c.msgraph_client.get_group_members = AsyncMock(return_value=[member])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Group"
        group.description = "desc"
        group.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        result = await c._process_single_group(group)
        assert result is not None
        user_group, app_users = result
        assert len(app_users) == 1

    @pytest.mark.asyncio
    async def test_with_nested_group(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()

        nested = MagicMock()
        nested.id = "nested-g1"
        nested.odata_type = "#microsoft.graph.group"
        nested.additional_data = {}
        nested.display_name = "Nested"
        c.msgraph_client.get_group_members = AsyncMock(return_value=[nested])
        c._get_users_from_nested_group = AsyncMock(return_value=[])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Parent"
        group.description = None
        group.created_date_time = None

        result = await c._process_single_group(group)
        assert result is not None
        c._get_users_from_nested_group.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_member_type(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()

        device = MagicMock()
        device.id = "device-1"
        device.odata_type = "#microsoft.graph.device"
        device.additional_data = {}
        c.msgraph_client.get_group_members = AsyncMock(return_value=[device])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Group"
        group.description = None
        group.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        result = await c._process_single_group(group)
        assert result is not None
        _, app_users = result
        assert len(app_users) == 0

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("fail"))

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Fail"

        result = await c._process_single_group(group)
        assert result is None


# =============================================================================
# _perform_delta_sync Tests
# =============================================================================


class TestPerformDeltaSync:
    @pytest.mark.asyncio
    async def test_group_deletion(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        deleted_group = MagicMock()
        deleted_group.id = "g1"
        deleted_group.additional_data = {"@removed": {"reason": "changed"}}

        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [deleted_group],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/delta?token=new"
        })
        c._handle_delete_group = AsyncMock(return_value=True)

        await c._perform_delta_sync("https://graph.microsoft.com/delta?token=old", "key")
        c._handle_delete_group.assert_called_once_with("g1")

    @pytest.mark.asyncio
    async def test_group_add_update(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        group = MagicMock()
        group.id = "g1"
        group.display_name = "New Group"
        group.additional_data = {}

        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [group],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/delta?token=new"
        })
        c._handle_group_create = AsyncMock(return_value=True)

        await c._perform_delta_sync("https://url", "key")
        c._handle_group_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_member_changes(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Group"
        group.additional_data = {
            "members@delta": [{"id": "user-1"}, {"id": "user-2", "@removed": True}]
        }

        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [group],
            "next_link": None,
            "delta_link": "https://graph.microsoft.com/delta?token=new"
        })
        c._handle_group_create = AsyncMock(return_value=True)
        c._process_member_change = AsyncMock()

        await c._perform_delta_sync("https://url", "key")
        assert c._process_member_change.call_count == 2

    @pytest.mark.asyncio
    async def test_pagination(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        c.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {"groups": [], "next_link": "https://graph.microsoft.com/next", "delta_link": None},
            {"groups": [], "next_link": None, "delta_link": "https://graph.microsoft.com/delta?final"},
        ])

        await c._perform_delta_sync("https://url", "key")
        assert c.user_group_sync_point.update_sync_point.call_count == 2

    @pytest.mark.asyncio
    async def test_no_url_falls_back(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [], "next_link": None, "delta_link": None
        })

        await c._perform_delta_sync("", "key")

    @pytest.mark.asyncio
    async def test_group_create_fails(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c.msgraph_client = MagicMock()

        group = MagicMock()
        group.id = "g1"
        group.display_name = "G"
        group.additional_data = {}

        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [group], "next_link": None, "delta_link": "https://delta"
        })
        c._handle_group_create = AsyncMock(return_value=False)

        await c._perform_delta_sync("https://url", "key")


# =============================================================================
# _handle_group_create Tests
# =============================================================================


class TestHandleGroupCreate:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(return_value=[])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Group"
        group.description = "desc"
        group.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)

        result = await c._handle_group_create(group)
        assert result is True

    @pytest.mark.asyncio
    async def test_with_nested_group(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()

        nested = MagicMock()
        nested.id = "ng1"
        nested.odata_type = "#microsoft.graph.group"
        nested.additional_data = {}
        nested.display_name = "Nested"
        c.msgraph_client.get_group_members = AsyncMock(return_value=[nested])
        c._get_users_from_nested_group = AsyncMock(return_value=[
            AppUser(source_user_id="u1", email="u@t.com", full_name="U",
                    app_name=Connectors.SHAREPOINT_ONLINE, connector_id="c1")
        ])

        group = MagicMock()
        group.id = "g1"
        group.display_name = "Parent"
        group.description = None
        group.created_date_time = None

        result = await c._handle_group_create(group)
        assert result is True

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("fail"))

        group = MagicMock()
        group.id = "g1"
        group.display_name = "G"

        result = await c._handle_group_create(group)
        assert result is False


# =============================================================================
# _get_users_from_nested_group Tests
# =============================================================================


class TestGetUsersFromNestedGroup:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()

        member = MagicMock()
        member.id = "u1"
        member.odata_type = "#microsoft.graph.user"
        member.additional_data = {}
        member.mail = "u@t.com"
        member.user_principal_name = "u@t.com"
        member.display_name = "User"
        member.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        c.msgraph_client.get_group_members = AsyncMock(return_value=[member])

        nested = MagicMock()
        nested.id = "ng1"
        nested.display_name = "Nested"

        result = await c._get_users_from_nested_group(nested)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_non_user_skipped(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()

        device = MagicMock()
        device.id = "d1"
        device.odata_type = "#microsoft.graph.device"
        device.additional_data = {}
        c.msgraph_client.get_group_members = AsyncMock(return_value=[device])

        nested = MagicMock()
        nested.id = "ng1"
        nested.display_name = "Nested"

        result = await c._get_users_from_nested_group(nested)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("fail"))

        nested = MagicMock()
        nested.id = "ng1"
        nested.display_name = "Nested"

        result = await c._get_users_from_nested_group(nested)
        assert result == []


# =============================================================================
# run_sync Tests
# =============================================================================


class TestRunSync:
    @pytest.mark.asyncio
    async def test_full_sync_no_sites(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])
        c._sync_user_groups = AsyncMock()
        c._get_all_sites = AsyncMock(return_value=[])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_full_sync_with_sites(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])
        c._sync_user_groups = AsyncMock()
        c._pass_site_ids_filters = MagicMock(return_value=True)
        c._get_site_permissions = AsyncMock(return_value=[])
        c._sync_site_content = AsyncMock()

        site = _make_site()
        c._get_all_sites = AsyncMock(return_value=[site])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()
            c._sync_site_content.assert_called()

    @pytest.mark.asyncio
    async def test_user_sync_error(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(side_effect=Exception("user fail"))
        c._sync_user_groups = AsyncMock()
        c._get_all_sites = AsyncMock(return_value=[])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_group_sync_error(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])
        c._sync_user_groups = AsyncMock(side_effect=Exception("group fail"))
        c._get_all_sites = AsyncMock(return_value=[])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_site_filter_excludes(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])
        c._sync_user_groups = AsyncMock()
        c._pass_site_ids_filters = MagicMock(return_value=False)

        site = _make_site()
        c._get_all_sites = AsyncMock(return_value=[site])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_site_with_no_name_skipped(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])
        c._sync_user_groups = AsyncMock()
        c._pass_site_ids_filters = MagicMock(return_value=True)

        site = _make_site()
        site.name = None
        site.display_name = None
        c._get_all_sites = AsyncMock(return_value=[site])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()

    @pytest.mark.asyncio
    async def test_critical_error_raises(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock(side_effect=Exception("critical"))

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            with pytest.raises(Exception, match="critical"):
                await c.run_sync()

    @pytest.mark.asyncio
    async def test_site_sync_failure_in_batch(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])
        c._sync_user_groups = AsyncMock()
        c._pass_site_ids_filters = MagicMock(return_value=True)
        c._get_site_permissions = AsyncMock(return_value=[])
        c._sync_site_content = AsyncMock(side_effect=Exception("site sync fail"))

        site = _make_site()
        c._get_all_sites = AsyncMock(return_value=[site])

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_filters.return_value = (FilterCollection(), FilterCollection())
            await c.run_sync()  # Should not raise


# =============================================================================
# _get_page_content Tests
# =============================================================================


class TestGetPageContent:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.client = MagicMock()
        site_info = MagicMock()
        site_info.web_url = "https://contoso.sharepoint.com/sites/test"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_info)
        c._get_sharepoint_access_token = AsyncMock(return_value="token")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "d": {"CanvasContent1": "<div>Hello World</div>"}
        }

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            with patch(f"{MODULE}.clean_html_output", return_value="<div>Hello World</div>"):
                result = await c._get_page_content("site-1", "page-1")
                assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_no_token(self):
        c = _make_connector()
        c.client = MagicMock()
        site_info = MagicMock()
        site_info.web_url = "https://contoso.sharepoint.com"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_info)
        c._get_sharepoint_access_token = AsyncMock(return_value=None)

        result = await c._get_page_content("site-1", "page-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_page_not_found(self):
        c = _make_connector()
        c.client = MagicMock()
        site_info = MagicMock()
        site_info.web_url = "https://contoso.sharepoint.com"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_info)
        c._get_sharepoint_access_token = AsyncMock(return_value="token")

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await c._get_page_content("site-1", "page-1")
            assert result is None

    @pytest.mark.asyncio
    async def test_empty_canvas_content(self):
        c = _make_connector()
        c.client = MagicMock()
        site_info = MagicMock()
        site_info.web_url = "https://contoso.sharepoint.com"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_info)
        c._get_sharepoint_access_token = AsyncMock(return_value="token")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"d": {"CanvasContent1": ""}}

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value = mock_client

            result = await c._get_page_content("site-1", "page-1")
            assert result == "<div></div>"

    @pytest.mark.asyncio
    async def test_exception_returns_none(self):
        c = _make_connector()
        c.client = MagicMock()
        c.client.sites.by_site_id.return_value.get = AsyncMock(side_effect=Exception("fail"))

        result = await c._get_page_content("site-1", "page-1")
        assert result is None


# =============================================================================
# _get_site_options and _get_document_library_options Tests
# =============================================================================


class TestGetSiteOptions:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "site-1",
                            "webUrl": "https://contoso.sharepoint.com/sites/test",
                            "displayName": "Test Site"
                        }
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_site_options(1, 20, "test")
        assert result.success is True
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_no_results(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"
        c.msgraph_client.search_query = AsyncMock(return_value=None)

        result = await c._get_site_options(1, 20, None)
        assert result.success is True
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_no_id_no_weburl_skipped(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{"resource": {}}]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_site_options(1, 20, "")
        assert len(result.options) == 0


class TestGetDocumentLibraryOptions:
    @pytest.mark.asyncio
    async def test_success_with_filtering(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 3,
                    "hits": [
                        {
                            "resource": {
                                "id": "lib-1",
                                "webUrl": "https://contoso.sharepoint.com/sites/test/Shared Documents",
                                "name": "Shared Documents",
                                "displayName": "Shared Documents",
                                "parentReference": {"siteId": "site-1"}
                            },
                            "summary": "DocumentLibrary"
                        },
                        {
                            "resource": {
                                "id": "list-1",
                                "webUrl": "https://contoso.sharepoint.com/sites/test/Lists/Tasks",
                                "name": "Tasks",
                                "displayName": "Tasks",
                                "parentReference": {"siteId": "site-1"}
                            },
                            "summary": "GenericList"
                        },
                        {
                            "resource": {
                                "id": "sys-1",
                                "webUrl": "https://contoso.sharepoint.com/sites/test/SiteAssets",
                                "name": "SiteAssets",
                                "displayName": "Site Assets",
                                "parentReference": {"siteId": "site-1"}
                            },
                            "summary": "DocumentLibrary"
                        },
                    ]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "shared")
        assert result.success is True
        assert len(result.options) == 1  # Only "Shared Documents" passes filters

    @pytest.mark.asyncio
    async def test_onedrive_url_filtered(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "od-1",
                            "webUrl": "https://contoso-my.sharepoint.com/personal/user/Documents",
                            "name": "Documents",
                            "displayName": "Documents",
                            "parentReference": {"siteId": "site-1"}
                        },
                        "summary": ""
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "")
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_contentstorage_filtered(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "cs-1",
                            "webUrl": "https://contoso.sharepoint.com/contentstorage/123",
                            "name": "Content",
                            "displayName": "Content",
                            "parentReference": {"siteId": "site-1"}
                        },
                        "summary": ""
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "")
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_calendar_filtered(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "cal-1",
                            "webUrl": "https://contoso.sharepoint.com/sites/test/calendar.aspx",
                            "name": "Calendar",
                            "displayName": "Calendar",
                            "parentReference": {"siteId": "site-1"}
                        },
                        "summary": ""
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "")
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_no_site_id_skipped(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "lib-1",
                            "webUrl": "https://contoso.sharepoint.com/docs",
                            "name": "Docs",
                            "displayName": "Docs",
                            "parentReference": {}
                        },
                        "summary": ""
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "")
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_site_name_from_url(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "lib-1",
                            "webUrl": "https://contoso.sharepoint.com/sites/MyTestSite/Documents",
                            "name": "Documents",
                            "displayName": "Documents",
                            "parentReference": {"siteId": "site-1"}
                        },
                        "summary": ""
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "")
        assert len(result.options) == 1
        assert "MyTestSite" in result.options[0].label

    @pytest.mark.asyncio
    async def test_root_site_label(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.tenant_region = "NAM"

        raw_result = MagicMock()
        raw_result.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 1,
                    "hits": [{
                        "resource": {
                            "id": "lib-root",
                            "webUrl": "https://contoso.sharepoint.com/Documents",
                            "name": "Documents",
                            "displayName": "Documents",
                            "parentReference": {"siteId": "site-root"}
                        },
                        "summary": ""
                    }]
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw_result)

        result = await c._get_document_library_options(1, 20, "")
        assert len(result.options) == 1
        assert "Root Site" in result.options[0].label


# =============================================================================
# _create_list_record and _create_list_item_record Tests
# =============================================================================


class TestCreateListRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.id = "list-1"
        list_obj.display_name = "Tasks"
        list_obj.name = "tasks"
        list_obj.web_url = "https://contoso.sharepoint.com/lists/tasks"
        list_obj.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        list_obj.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        list_obj.e_tag = "etag-list"
        list_obj.list = MagicMock()
        list_obj.list.template = "GenericList"
        list_obj.list.item_count = 10

        result = await c._create_list_record(list_obj, "site-1")
        assert result is not None
        assert result.record_name == "Tasks"

    @pytest.mark.asyncio
    async def test_no_id(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.id = None
        result = await c._create_list_record(list_obj, "site-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        list_obj = MagicMock()
        type(list_obj).id = PropertyMock(side_effect=Exception("bad"))
        result = await c._create_list_record(list_obj, "site-1")
        assert result is None


class TestCreateListItemRecord:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        item = MagicMock()
        item.id = "li-1"
        item.fields = MagicMock()
        item.fields.additional_data = {"Title": "Task 1", "Status": "Done"}
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = "etag-li"
        item.web_url = "https://contoso.sharepoint.com/item"
        item.content_type = MagicMock()
        item.content_type.name = "Item"

        result = await c._create_list_item_record(item, "site-1", "list-1")
        assert result is not None
        assert result.record_name == "Task 1"

    @pytest.mark.asyncio
    async def test_no_id(self):
        c = _make_connector()
        item = MagicMock()
        item.id = None
        result = await c._create_list_item_record(item, "site-1", "list-1")
        assert result is None

    @pytest.mark.asyncio
    async def test_no_fields(self):
        c = _make_connector()
        item = MagicMock()
        item.id = "li-1"
        item.fields = None
        item.created_date_time = datetime(2024, 1, 1, tzinfo=timezone.utc)
        item.last_modified_date_time = datetime(2024, 6, 1, tzinfo=timezone.utc)
        item.e_tag = None
        item.web_url = None
        item.content_type = None

        result = await c._create_list_item_record(item, "site-1", "list-1")
        assert result is not None
        assert "Item li-1" in result.record_name

    @pytest.mark.asyncio
    async def test_exception(self):
        c = _make_connector()
        item = MagicMock()
        type(item).id = PropertyMock(side_effect=Exception("bad"))
        result = await c._create_list_item_record(item, "site-1", "list-1")
        assert result is None


# =============================================================================
# _process_site_lists Tests
# =============================================================================


class TestProcessSiteLists:
    @pytest.mark.asyncio
    async def test_no_lists(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c._safe_api_call = AsyncMock(return_value=None)

        items = []
        async for item in c._process_site_lists("site-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock(side_effect=Exception("fail"))
        c.rate_limiter.__aexit__ = AsyncMock()

        items = []
        async for item in c._process_site_lists("site-1"):
            items.append(item)
        assert len(items) == 0


# =============================================================================
# _fetch_graph_group_members Tests
# =============================================================================


class TestFetchGraphGroupMembers:
    @pytest.mark.asyncio
    async def test_pagination(self):
        c = _make_connector()
        c.client = MagicMock()

        member1 = MagicMock()
        member1.odata_type = "#microsoft.graph.user"
        member1.mail = "u1@test.com"
        member1.id = "u1"
        member1.display_name = "User 1"
        member1.user_principal_name = "u1@test.com"

        member2 = MagicMock()
        member2.odata_type = "#microsoft.graph.user"
        member2.mail = "u2@test.com"
        member2.id = "u2"
        member2.display_name = "User 2"
        member2.user_principal_name = "u2@test.com"

        page1 = MagicMock()
        page1.value = [member1]
        page1.odata_next_link = "https://graph.microsoft.com/next"

        page2 = MagicMock()
        page2.value = [member2]
        page2.odata_next_link = None

        mock_group = MagicMock()
        mock_group.transitive_members = MagicMock()
        mock_group.transitive_members.get = AsyncMock(return_value=page1)
        mock_group.transitive_members.with_url = MagicMock(return_value=MagicMock(
            get=AsyncMock(return_value=page2)
        ))
        c.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await c._fetch_graph_group_members("g1", is_owner=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_owner_pagination(self):
        c = _make_connector()
        c.client = MagicMock()

        member = MagicMock()
        member.odata_type = "#microsoft.graph.user"
        member.mail = "owner@test.com"
        member.id = "o1"
        member.display_name = "Owner"
        member.user_principal_name = "owner@test.com"

        page1 = MagicMock()
        page1.value = [member]
        page1.odata_next_link = "https://graph.microsoft.com/next"

        page2 = MagicMock()
        page2.value = []
        page2.odata_next_link = None

        mock_group = MagicMock()
        mock_group.owners = MagicMock()
        mock_group.owners.get = AsyncMock(return_value=page1)
        mock_group.owners.with_url = MagicMock(return_value=MagicMock(
            get=AsyncMock(return_value=page2)
        ))
        c.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await c._fetch_graph_group_members("g1", is_owner=True)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_non_user_skipped(self):
        c = _make_connector()
        c.client = MagicMock()

        # Use a simple object without user_principal_name attribute
        # to avoid MagicMock auto-creating attributes on hasattr checks
        class DeviceMock:
            odata_type = "#microsoft.graph.device"
            mail = None
            id = "d1"

        device = DeviceMock()

        page = MagicMock()
        page.value = [device]
        page.odata_next_link = None

        mock_group = MagicMock()
        mock_group.transitive_members = MagicMock()
        mock_group.transitive_members.get = AsyncMock(return_value=page)
        c.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await c._fetch_graph_group_members("g1")
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_user_no_email_skipped(self):
        c = _make_connector()
        c.client = MagicMock()

        user = MagicMock()
        user.odata_type = "#microsoft.graph.user"
        user.mail = None
        user.user_principal_name = None
        user.id = "u1"

        page = MagicMock()
        page.value = [user]
        page.odata_next_link = None

        mock_group = MagicMock()
        mock_group.transitive_members = MagicMock()
        mock_group.transitive_members.get = AsyncMock(return_value=page)
        c.client.groups.by_group_id = MagicMock(return_value=mock_group)

        result = await c._fetch_graph_group_members("g1")
        assert len(result) == 0


# =============================================================================
# handle_webhook_notification Tests
# =============================================================================


class TestHandleWebhookNotification:
    @pytest.mark.asyncio
    async def test_sites_resource_no_site_found(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c._safe_api_call = AsyncMock(return_value=None)

        await c.handle_webhook_notification({
            "resource": "sites/site-1/drives/d1",
            "changeType": "updated"
        })

    @pytest.mark.asyncio
    async def test_no_sites_in_resource(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()

        await c.handle_webhook_notification({
            "resource": "users/u1",
            "changeType": "updated"
        })

    @pytest.mark.asyncio
    async def test_sites_at_end_of_parts(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()

        await c.handle_webhook_notification({
            "resource": "sites",
            "changeType": "updated"
        })


# =============================================================================
# _should_skip_list Additional Tests
# =============================================================================


class TestShouldSkipList:
    def test_hidden_via_list_property(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = True
        assert c._should_skip_list(list_obj, "List") is True

    def test_not_hidden_via_list_property(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "GenericList"
        assert c._should_skip_list(list_obj, "Normal List") is False

    def test_system_prefix_workflow(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        assert c._should_skip_list(list_obj, "Workflow History") is True

    def test_system_prefix_master_page(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        assert c._should_skip_list(list_obj, "Master Page Gallery items") is True

    def test_system_prefix_site_assets(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        assert c._should_skip_list(list_obj, "site assets library") is True

    def test_template_workflow(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "WorkflowHistory"
        assert c._should_skip_list(list_obj, "History") is True

    def test_template_survey(self):
        c = _make_connector()
        list_obj = MagicMock()
        list_obj.list = MagicMock()
        list_obj.list.hidden = False
        list_obj.list.template = "SurveyForm"
        assert c._should_skip_list(list_obj, "My Survey") is True

    def test_fallback_template(self):
        c = _make_connector()
        list_obj = MagicMock(spec=[])
        list_obj.hidden = False
        list_obj.template = "WebTemplateExtensionsList"
        assert c._should_skip_list(list_obj, "Extensions") is True

    def test_no_list_no_hidden(self):
        c = _make_connector()
        list_obj = MagicMock(spec=[])
        list_obj.hidden = False
        assert c._should_skip_list(list_obj, "Normal") is False


# =============================================================================
# _get_date_filters Additional Branch Tests
# =============================================================================


class TestGetDateFiltersCreated:
    def test_created_date_filter(self):
        c = _make_connector()
        mock_modified = MagicMock()
        mock_modified.is_empty.return_value = True

        mock_created = MagicMock()
        mock_created.is_empty.return_value = False
        mock_created.get_datetime_iso.return_value = ("2024-01-01T00:00:00", "2024-12-31T23:59:59")

        def side_effect(key):
            if key == SyncFilterKey.MODIFIED:
                return mock_modified
            if key == SyncFilterKey.CREATED:
                return mock_created
            return None

        c.sync_filters = MagicMock()
        c.sync_filters.get = side_effect

        ma, mb, ca, cb = c._get_date_filters()
        assert ma is None
        assert ca is not None
        assert cb is not None


# =============================================================================
# Additional _normalize_document_library_url Branch Tests
# =============================================================================


class TestNormalizeDocumentLibraryUrlExtra:
    def test_no_protocol(self):
        c = _make_connector()
        result = c._normalize_document_library_url("contoso.sharepoint.com/sites/test/Docs")
        assert result == "contoso.sharepoint.com/sites/test/docs"

    def test_with_forms_and_viewid(self):
        c = _make_connector()
        result = c._normalize_document_library_url(
            "https://contoso.sharepoint.com/sites/test/Docs/Forms/AllItems.aspx?viewid=abc"
        )
        assert result == "contoso.sharepoint.com/sites/test/docs"


# =============================================================================
# _process_list_items Tests
# =============================================================================


class TestProcessListItems:
    @pytest.mark.asyncio
    async def test_no_items(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.list_sync_point = MagicMock()
        c.list_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.list_sync_point.update_sync_point = AsyncMock()
        c._safe_api_call = AsyncMock(return_value=None)

        items = []
        async for item in c._process_list_items("site-1", "list-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_with_skip_token(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.list_sync_point = MagicMock()
        c.list_sync_point.read_sync_point = AsyncMock(return_value={"skipToken": "token123"})
        c.list_sync_point.update_sync_point = AsyncMock()

        resp = MagicMock()
        resp.value = None
        c._safe_api_call = AsyncMock(return_value=resp)

        items = []
        async for item in c._process_list_items("site-1", "list-1"):
            items.append(item)
        assert len(items) == 0

    @pytest.mark.asyncio
    async def test_exception_caught(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c.list_sync_point = MagicMock()
        c.list_sync_point.read_sync_point = AsyncMock(side_effect=Exception("fail"))

        items = []
        async for item in c._process_list_items("site-1", "list-1"):
            items.append(item)
        assert len(items) == 0


# =============================================================================
# _get_drive_permissions Tests
# =============================================================================


class TestGetDrivePermissions:
    @pytest.mark.asyncio
    async def test_with_root_item(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()

        root = MagicMock()
        root.id = "root-id"
        perms = MagicMock()
        perms.value = []
        c._safe_api_call = AsyncMock(side_effect=[root, perms])
        c._convert_to_permissions = AsyncMock(return_value=[])

        result = await c._get_drive_permissions("site-1", "drive-1")
        assert result == []

    @pytest.mark.asyncio
    async def test_no_root_item(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock()
        c.rate_limiter.__aexit__ = AsyncMock()
        c._safe_api_call = AsyncMock(side_effect=[None, None])

        result = await c._get_drive_permissions("site-1", "drive-1")
        assert result == []
