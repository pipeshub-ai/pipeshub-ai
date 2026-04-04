"""Comprehensive coverage tests for the SharePoint connector."""

import logging
import urllib.parse
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import Connectors
from app.connectors.core.registry.filters import FilterCollection
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
from app.models.entities import Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType


def _make_connector():
    logger = logging.getLogger("test.sharepoint")
    dep = MagicMock()
    dep.org_id = "org-sp-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.get_all_active_users = AsyncMock(return_value=[])
    dep.reindex_existing_records = AsyncMock()
    dsp = MagicMock()
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return SharePointConnector(logger, dep, dsp, cs, "conn-sp-1", "team", "test-user-id")


class TestSharePointSiteUrlConstruction:
    def test_construct_site_url_empty(self):
        c = _make_connector()
        assert c._construct_site_url("") == ""

    def test_construct_site_url_valid(self):
        c = _make_connector()
        assert c._construct_site_url("host,g1,g2") == "host,g1,g2"


class TestSharePointValidateSiteId:
    def test_empty_id(self):
        c = _make_connector()
        assert c._validate_site_id("") is False

    def test_root_id(self):
        c = _make_connector()
        assert c._validate_site_id("root") is True

    def test_composite_valid(self):
        c = _make_connector()
        site_id = f"contoso.sharepoint.com,{'a' * 36},{'b' * 36}"
        assert c._validate_site_id(site_id) is True

    def test_composite_two_parts(self):
        c = _make_connector()
        assert c._validate_site_id("a,b") is False

    def test_long_single_part(self):
        c = _make_connector()
        assert c._validate_site_id("a" * 11) is True


class TestSharePointNormalizeSiteId:
    def test_empty(self):
        c = _make_connector()
        assert c._normalize_site_id("") == ""

    def test_already_composite(self):
        c = _make_connector()
        assert c._normalize_site_id("h,g1,g2") == "h,g1,g2"

    def test_cache_lookup(self):
        c = _make_connector()
        c.site_cache = {"host.com,guid1,guid2": SiteMetadata("host.com,guid1,guid2", "url", "name", False)}
        assert c._normalize_site_id("guid1,guid2") == "host.com,guid1,guid2"

    def test_prepend_hostname(self):
        c = _make_connector()
        c.sharepoint_domain = "https://contoso.sharepoint.com"
        result = c._normalize_site_id("guid1,guid2")
        assert result == "contoso.sharepoint.com,guid1,guid2"


class TestSharePointSafeApiCall:
    @pytest.mark.asyncio
    async def test_success(self):
        c = _make_connector()
        async def ok():
            return "result"
        result = await c._safe_api_call(ok())
        assert result == "result"

    @pytest.mark.asyncio
    async def test_permission_denied(self):
        c = _make_connector()
        async def fail():
            raise Exception("403 forbidden")
        result = await c._safe_api_call(fail(), max_retries=0, retry_delay=0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_not_found(self):
        c = _make_connector()
        async def fail():
            raise Exception("404 notfound")
        result = await c._safe_api_call(fail(), max_retries=0, retry_delay=0.01)
        assert result is None


class TestSharePointInitCertificate:
    @pytest.mark.asyncio
    async def test_init_with_client_secret(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t1", "clientId": "c1", "clientSecret": "s1",
                "sharepointDomain": "https://contoso.sharepoint.com",
            }
        })
        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.ClientSecretCredential") as mock_cred, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.GraphServiceClient") as mock_graph, \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.MSGraphClient"), \
             patch("app.connectors.sources.microsoft.sharepoint_online.connector.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_cred_instance = AsyncMock()
            mock_cred_instance.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_instance
            mock_filters.return_value = (MagicMock(), MagicMock())
            mock_graph_instance = MagicMock()
            mock_graph.return_value = mock_graph_instance
            mock_root_site = MagicMock()
            mock_root_site.site_collection = MagicMock()
            mock_root_site.site_collection.data_location_code = "NAM"
            mock_graph_instance.sites.by_site_id.return_value.get = AsyncMock(return_value=mock_root_site)
            result = await c.init()
            assert result is True


class TestSharePointCleanupExtended:
    @pytest.mark.asyncio
    async def test_cleanup_no_credential(self):
        c = _make_connector()
        c.credential = None
        c.temp_cert_file = None
        await c.cleanup()

    @pytest.mark.asyncio
    async def test_cleanup_close_error(self):
        c = _make_connector()
        c.credential = AsyncMock()
        c.credential.close = AsyncMock(side_effect=Exception("close error"))
        c.temp_cert_file = None
        await c.cleanup()


class TestSharePointTestConnection:
    @pytest.mark.asyncio
    async def test_connection_success(self):
        c = _make_connector()
        c.client = MagicMock()
        root_site = MagicMock()
        root_site.display_name = "Root Site"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=root_site)
        result = await c.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_connection_always_returns_true(self):
        """SharePoint test_connection_and_access always returns True (no actual check)."""
        c = _make_connector()
        c.client = MagicMock()
        result = await c.test_connection_and_access()
        assert result is True


class TestSharePointMisc:
    @pytest.mark.asyncio
    async def test_run_incremental_sync(self):
        c = _make_connector()
        c._reinitialize_credential_if_needed = AsyncMock()
        c._get_all_sites = AsyncMock(return_value=[])
        await c.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_handle_webhook(self):
        c = _make_connector()
        await c.handle_webhook_notification({})


class TestSharePointReindex:
    @pytest.mark.asyncio
    async def test_reindex_empty(self):
        c = _make_connector()
        await c.reindex_records([])


class TestSharePointCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector(self):
        with patch("app.connectors.sources.microsoft.sharepoint_online.connector.DataSourceEntitiesProcessor") as MockDSEP:
            mock_dep = MagicMock()
            mock_dep.initialize = AsyncMock()
            MockDSEP.return_value = mock_dep
            connector = await SharePointConnector.create_connector(
                logger=logging.getLogger("test"),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="test-sp",
                scope="personal",
                created_by="test-user-id",
            )
            assert isinstance(connector, SharePointConnector)
