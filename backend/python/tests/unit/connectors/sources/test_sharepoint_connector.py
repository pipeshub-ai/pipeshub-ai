"""Tests for app.connectors.sources.microsoft.sharepoint_online.connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
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


# ===========================================================================
# Helpers
# ===========================================================================


def _make_mock_deps():
    logger = logging.getLogger("test.sharepoint")
    data_entities_processor = MagicMock()
    data_entities_processor.org_id = "org-sp-1"
    data_entities_processor.on_new_app_users = AsyncMock()
    data_entities_processor.on_new_user_groups = AsyncMock()
    data_entities_processor.on_new_records = AsyncMock()
    data_entities_processor.on_new_record_groups = AsyncMock()
    data_entities_processor.on_record_deleted = AsyncMock()
    data_entities_processor.get_all_active_users = AsyncMock(return_value=[])

    data_store_provider = MagicMock()
    config_service = MagicMock()
    config_service.get_config = AsyncMock()

    return logger, data_entities_processor, data_store_provider, config_service


def _make_connector():
    logger, dep, dsp, cs = _make_mock_deps()
    return SharePointConnector(logger, dep, dsp, cs, "conn-sp-1")


# ===========================================================================
# Constants
# ===========================================================================


class TestSharePointConstants:

    def test_composite_site_id_constants(self):
        assert COMPOSITE_SITE_ID_COMMA_COUNT == 2
        assert COMPOSITE_SITE_ID_PARTS_COUNT == 3


# ===========================================================================
# SharePointRecordType
# ===========================================================================


class TestSharePointRecordType:

    def test_record_type_values(self):
        assert SharePointRecordType.SITE.value == "SITE"
        assert SharePointRecordType.SUBSITE.value == "SUBSITE"
        assert SharePointRecordType.DOCUMENT_LIBRARY.value == "SHAREPOINT_DOCUMENT_LIBRARY"
        assert SharePointRecordType.LIST.value == "SHAREPOINT_LIST"
        assert SharePointRecordType.LIST_ITEM.value == "SHAREPOINT_LIST_ITEM"
        assert SharePointRecordType.PAGE.value == "WEBPAGE"
        assert SharePointRecordType.FILE.value == "FILE"


# ===========================================================================
# SharePointCredentials
# ===========================================================================


class TestSharePointCredentials:

    def test_defaults(self):
        creds = SharePointCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1",
            sharepoint_domain="https://contoso.sharepoint.com",
        )
        assert creds.has_admin_consent is False
        assert creds.root_site_url is None
        assert creds.enable_subsite_discovery is True
        assert creds.certificate_path is None
        assert creds.certificate_data is None

    def test_with_all_fields(self):
        creds = SharePointCredentials(
            tenant_id="t1", client_id="c1", client_secret="s1",
            sharepoint_domain="https://contoso.sharepoint.com",
            has_admin_consent=True,
            root_site_url="contoso.sharepoint.com",
            enable_subsite_discovery=False,
        )
        assert creds.has_admin_consent is True
        assert creds.root_site_url == "contoso.sharepoint.com"
        assert creds.enable_subsite_discovery is False


# ===========================================================================
# SiteMetadata
# ===========================================================================


class TestSiteMetadata:

    def test_creation(self):
        meta = SiteMetadata(
            site_id="site-1",
            site_url="https://contoso.sharepoint.com/sites/test",
            site_name="Test Site",
            is_root=False,
            parent_site_id="root-site-id",
        )
        assert meta.site_id == "site-1"
        assert meta.is_root is False
        assert meta.parent_site_id == "root-site-id"
        assert meta.created_at is None
        assert meta.updated_at is None

    def test_root_site(self):
        meta = SiteMetadata(
            site_id="root-1",
            site_url="https://contoso.sharepoint.com",
            site_name="Root",
            is_root=True,
        )
        assert meta.is_root is True
        assert meta.parent_site_id is None


# ===========================================================================
# MicrosoftRegion / CountryToRegionMapper
# ===========================================================================


class TestMicrosoftRegion:

    def test_region_values(self):
        assert MicrosoftRegion.NAM.value == "NAM"
        assert MicrosoftRegion.EUR.value == "EUR"
        assert MicrosoftRegion.APC.value == "APC"
        assert MicrosoftRegion.IND.value == "IND"

    def test_country_to_region_us(self):
        assert CountryToRegionMapper.get_region("US") == MicrosoftRegion.NAM

    def test_country_to_region_gb(self):
        assert CountryToRegionMapper.get_region("GB") == MicrosoftRegion.GBR

    def test_country_to_region_in(self):
        assert CountryToRegionMapper.get_region("IN") == MicrosoftRegion.IND

    def test_country_to_region_unknown_defaults_to_nam(self):
        assert CountryToRegionMapper.get_region("XX") == MicrosoftRegion.NAM

    def test_country_to_region_none_defaults_to_nam(self):
        assert CountryToRegionMapper.get_region(None) == MicrosoftRegion.NAM

    def test_get_region_string(self):
        assert CountryToRegionMapper.get_region_string("US") == "NAM"
        assert CountryToRegionMapper.get_region_string("FR") == "FRA"

    def test_is_valid_region(self):
        assert CountryToRegionMapper.is_valid_region("NAM") is True
        assert CountryToRegionMapper.is_valid_region("INVALID") is False

    def test_get_all_regions(self):
        regions = CountryToRegionMapper.get_all_regions()
        assert isinstance(regions, list)
        assert "NAM" in regions

    def test_get_all_country_codes(self):
        codes = CountryToRegionMapper.get_all_country_codes()
        assert isinstance(codes, list)
        assert "US" in codes
        assert "IN" in codes

    def test_case_insensitive_country_code(self):
        assert CountryToRegionMapper.get_region("us") == MicrosoftRegion.NAM


# ===========================================================================
# SharePointConnector.__init__
# ===========================================================================


class TestSharePointConnectorInit:

    def test_connector_initializes(self):
        connector = _make_connector()
        assert connector.connector_name == Connectors.SHAREPOINT_ONLINE
        assert connector.connector_id == "conn-sp-1"
        assert connector.batch_size == 50
        assert connector.max_concurrent_batches == 1
        assert connector.enable_subsite_discovery is True

    def test_stats_initialized(self):
        connector = _make_connector()
        assert connector.stats["sites_processed"] == 0
        assert connector.stats["errors_encountered"] == 0


# ===========================================================================
# SharePointConnector.init
# ===========================================================================


class TestSharePointConnectorInitMethod:

    @pytest.mark.asyncio
    async def test_init_raises_when_no_config(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value=None)

        with pytest.raises(ValueError, match="not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_raises_when_empty_auth(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})

        with pytest.raises(ValueError, match="not found"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_raises_on_incomplete_credentials(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"tenantId": "t1", "clientId": "c1"}
        })

        with pytest.raises(ValueError, match="Incomplete"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_raises_on_missing_auth_method(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t1",
                "clientId": "c1",
                "sharepointDomain": "https://contoso.sharepoint.com",
            }
        })

        with pytest.raises(ValueError, match="Authentication credentials missing"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_success_with_client_secret(self):
        connector = _make_connector()
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t1",
                "clientId": "c1",
                "clientSecret": "s1",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "hasAdminConsent": True,
            }
        })

        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.ClientSecretCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_cred_instance = AsyncMock()
            mock_cred_instance.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_instance
            mock_filters.return_value = (MagicMock(), MagicMock())

            result = await connector.init()
            assert result is True
            assert connector.sharepoint_domain == "https://contoso.sharepoint.com"


# ===========================================================================
# SharePointConnector.cleanup
# ===========================================================================


class TestSharePointCleanup:

    @pytest.mark.asyncio
    async def test_cleanup_with_credential(self):
        connector = _make_connector()
        connector.credential = AsyncMock()
        connector.credential.close = AsyncMock()
        connector.client = MagicMock()
        connector.msgraph_client = MagicMock()
        connector.temp_cert_file = None

        await connector.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_without_credential(self):
        connector = _make_connector()
        # Should not raise
        await connector.cleanup()
