"""Comprehensive coverage tests for the SharePoint connector."""

import base64
import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from http import HTTPStatus
from unittest.mock import ANY, AsyncMock, MagicMock, patch

from fastapi import HTTPException

import pytest

from app.config.constants.arangodb import Connectors
from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.microsoft.common.msgraph_client import RecordUpdate
from app.connectors.sources.microsoft.sharepoint_online.connector import (
    COMPOSITE_SITE_ID_COMMA_COUNT,
    COMPOSITE_SITE_ID_PARTS_COUNT,
    CountryToRegionMapper,
    MicrosoftRegion,
    SharePointConnector,
    SharePointCredentials,
    SharePointRecordType,
    SiteMetadata,
    _sharepoint_filter_options_max_search_batches,
)
from app.models.entities import Record, RecordType
from app.models.permission import EntityType, Permission, PermissionType

MODULE = "app.connectors.sources.microsoft.sharepoint_online.connector"


def _make_connector(*, return_dep: bool = False):
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
    c = SharePointConnector(logger, dep, dsp, cs, "conn-sp-1", "team", "test-user-id")
    c.rate_limiter = AsyncMock()
    c.rate_limiter.__aenter__ = AsyncMock(return_value=None)
    c.rate_limiter.__aexit__ = AsyncMock(return_value=None)
    if return_dep:
        return c, dep
    return c


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

    def test_composite_four_parts_invalid(self):
        c = _make_connector()
        assert c._validate_site_id("host.com,g1,g2,g3") is False

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

    @pytest.mark.asyncio
    async def test_bad_request_no_retry(self):
        c = _make_connector()
        async def fail():
            raise Exception("400 badrequest invalid hostname")
        result = await c._safe_api_call(fail(), max_retries=2, retry_delay=0.01)
        assert result is None

    @pytest.mark.asyncio
    async def test_throttle_retries_then_succeeds(self):
        c = _make_connector()

        class _FlakyAwaitable:
            def __init__(self):
                self._attempts = 0

            def __await__(self):
                return self._run().__await__()

            async def _run(self):
                self._attempts += 1
                if self._attempts == 1:
                    raise Exception("429 too many requests throttle")
                return "ok"

        with patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            result = await c._safe_api_call(_FlakyAwaitable(), max_retries=1, retry_delay=0.01)
        assert result == "ok"
        mock_sleep.assert_awaited()

    @pytest.mark.asyncio
    async def test_exhausts_retries_returns_none(self):
        c = _make_connector()

        async def always_fail():
            raise Exception("500 internal server error")

        with patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock):
            result = await c._safe_api_call(always_fail(), max_retries=1, retry_delay=0.01)
        assert result is None
        assert c.stats["errors_encountered"] >= 1


class TestSharePointInitCertificate:
    @pytest.mark.asyncio
    async def test_init_certificate_non_string_raises(self):
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
    async def test_init_private_key_non_string_raises(self):
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
    async def test_init_certificate_base64_and_token_failure_cleanup(self):
        c = _make_connector()
        cert_pem = "-----BEGIN CERTIFICATE-----\nCERT\n-----END CERTIFICATE-----"
        key_pem = "-----BEGIN PRIVATE KEY-----\nKEY\n-----END PRIVATE KEY-----"
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "sharepointDomain": "https://contoso.sharepoint.com",
                "certificate": base64.b64encode(cert_pem.encode()).decode(),
                "privateKey": base64.b64encode(key_pem.encode()).decode(),
            }
        })
        with patch(f"{MODULE}.CertificateCredential") as mock_cert_cred:
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock(side_effect=Exception("token fail"))
            mock_cert_cred.return_value = mock_cred_inst
            with pytest.raises(ValueError, match="Failed to initialize SharePoint credential"):
                await c.init()

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

    @pytest.mark.asyncio
    async def test_reindex_updated_and_unchanged(self):
        c, dep = _make_connector(return_dep=True)
        c.msgraph_client = MagicMock()
        dep.get_all_active_users = AsyncMock(return_value=[])
        rec_updated = MagicMock()
        rec_updated.id = "r1"
        rec_unchanged = MagicMock()
        rec_unchanged.id = "r2"
        updated_record = MagicMock()
        updated_record.id = "r1"
        c._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(updated_record, []), None]
        )
        await c.reindex_records([rec_updated, rec_unchanged])
        dep.on_new_records.assert_awaited_once_with([(updated_record, [])])
        dep.reindex_existing_records.assert_awaited_once_with([rec_unchanged])

    @pytest.mark.asyncio
    async def test_reindex_skips_record_on_check_error(self):
        c, dep = _make_connector(return_dep=True)
        c.msgraph_client = MagicMock()
        dep.get_all_active_users = AsyncMock(return_value=[])
        rec = MagicMock()
        rec.id = "r1"
        c._check_and_fetch_updated_record = AsyncMock(side_effect=Exception("check failed"))
        await c.reindex_records([rec])
        dep.on_new_records.assert_not_awaited()
        dep.reindex_existing_records.assert_not_awaited()


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


# ---------------------------------------------------------------------------
# Filter-options batch budget
# ---------------------------------------------------------------------------


class TestSharePointFilterOptionsMaxBatches:
    def test_resume_with_cursor_scaling(self):
        resume = _sharepoint_filter_options_max_search_batches(
            page=3, filtered_page_limit=20, graph_batch=25, resume_with_cursor=True
        )
        stateless = _sharepoint_filter_options_max_search_batches(
            page=3, filtered_page_limit=20, graph_batch=25, resume_with_cursor=False
        )
        assert resume != stateless
        assert 8 <= resume <= 40
        assert 8 <= stateless <= 40

    def test_clamped_to_ceiling(self):
        high = _sharepoint_filter_options_max_search_batches(
            page=100, filtered_page_limit=500, graph_batch=1, resume_with_cursor=False
        )
        assert high == 40


class TestSharePointInitDomainException:
    @pytest.mark.asyncio
    async def test_domain_parse_exception_falls_back(self):
        c = _make_connector()
        c.config_service.get_config = AsyncMock(return_value={
            "auth": {
                "tenantId": "t",
                "clientId": "c",
                "clientSecret": "s",
                "sharepointDomain": "https://contoso.sharepoint.com",
            }
        })
        with patch(f"{MODULE}.urllib.parse.urlparse", side_effect=Exception("parse fail")), \
             patch(f"{MODULE}.ClientSecretCredential") as mock_cred, \
             patch(f"{MODULE}.GraphServiceClient") as mock_graph, \
             patch(f"{MODULE}.MSGraphClient"), \
             patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock) as mock_filters:
            mock_cred_inst = AsyncMock()
            mock_cred_inst.get_token = AsyncMock()
            mock_cred.return_value = mock_cred_inst
            mock_filters.return_value = (MagicMock(), MagicMock())
            root_site = MagicMock()
            root_site.site_collection = MagicMock()
            root_site.site_collection.data_location_code = "NAM"
            mock_graph.return_value.sites.by_site_id.return_value.get = AsyncMock(
                return_value=root_site
            )
            assert await c.init() is True
            assert c.sharepoint_domain == "https://contoso.sharepoint.com"


# ---------------------------------------------------------------------------
# Site permissions (REST security groups / visitors)
# ---------------------------------------------------------------------------


class TestSharePointGetSitePermissionsExtended:
    @pytest.mark.asyncio
    async def test_security_group_members_and_visitors(self):
        c = _make_connector()
        c.sharepoint_domain = "https://contoso.sharepoint.com"
        c.site_cache["site-1"] = SiteMetadata(
            site_id="site-1",
            site_url="https://contoso.sharepoint.com/sites/test",
            site_name="Test",
            is_root=False,
        )
        c._get_sharepoint_access_token = AsyncMock(return_value="token")
        sec_guid = "00000000-0000-0000-0000-000000000001"
        c._get_sharepoint_group_users = AsyncMock(side_effect=[
            [{
                "LoginName": f"c:0t.c|tenant|{sec_guid}",
                "Title": "Sec Group",
                "PrincipalType": 4,
            }, {
                "LoginName": (
                    "c:0o.c|federateddirectoryclaimprovider|"
                    "abcdefab-1234-5678-9012-abcdefabcdef"
                ),
                "Title": "M365",
                "PrincipalType": 4,
            }],
            [],
            [{
                "LoginName": "c:0t.c|tenant|9908e57b-4444-4a0e-af96-e8ca83c0a0e5",
                "Title": "Everyone except external users",
                "PrincipalType": 4,
            }, {
                "LoginName": f"c:0t.c|tenant|{sec_guid}",
                "Title": "Visitor Sec",
                "PrincipalType": 4,
            }, {
                "LoginName": "c:0(.s|true|spo-grid-all-users",
                "PrincipalType": 0,
                "Title": "Everyone",
            }],
        ])
        c._get_custom_sharepoint_groups = AsyncMock(return_value=[{
            "id": "99",
            "title": "Custom",
            "permission_level": PermissionType.READ,
        }])
        perms = await c._get_site_permissions("site-1")
        assert any(getattr(p, "external_id", None) == sec_guid for p in perms)
        assert any(getattr(p, "entity_type", None) == EntityType.ORG for p in perms)
        assert any(getattr(p, "entity_type", None) == EntityType.GROUP for p in perms)

    @pytest.mark.asyncio
    async def test_security_group_without_guid(self):
        c = _make_connector()
        c.site_cache["site-1"] = SiteMetadata(
            site_id="site-1",
            site_url="https://contoso.sharepoint.com/sites/test",
            site_name="Test",
            is_root=False,
        )
        c._get_sharepoint_access_token = AsyncMock(return_value="token")
        c._get_sharepoint_group_users = AsyncMock(side_effect=[
            [{"LoginName": "bad-login", "Title": "Bad", "PrincipalType": 4}],
            [],
            [],
        ])
        c._get_custom_sharepoint_groups = AsyncMock(return_value=[])
        assert isinstance(await c._get_site_permissions("site-1"), list)


# ---------------------------------------------------------------------------
# _sync_user_groups
# ---------------------------------------------------------------------------


class TestSharePointSyncUserGroups:
    @pytest.mark.asyncio
    async def test_full_flow_client_secret(self):
        c, dep = _make_connector(return_dep=True)
        c.tenant_id = "tenant"
        c.client_id = "client"
        c.client_secret = "secret"
        c.certificate_path = None
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        c._sync_azure_ad_groups = AsyncMock()

        site = MagicMock()
        site.id = "contoso.sharepoint.com,g1,g2"
        site.display_name = "Site A"
        site.name = "site-a"
        c._get_all_sites = AsyncMock(return_value=[site])
        site_details = MagicMock()
        site_details.web_url = "https://contoso.sharepoint.com/sites/a"
        c.client = MagicMock()
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_details)

        m365_guid = "abcdefab-1234-5678-9012-abcdefabcdef"
        groups_resp = MagicMock()
        groups_resp.status_code = HTTPStatus.OK
        groups_resp.json.return_value = {
            "d": {"results": [{"Title": "Site Owners", "Id": 7, "Description": "Owners"}]}
        }
        users_resp = MagicMock()
        users_resp.status_code = HTTPStatus.OK
        users_resp.json.return_value = {
            "d": {
                "results": [
                    {
                        "LoginName": f"c:0o.c|federateddirectoryclaimprovider|{m365_guid}",
                        "Title": "Team",
                        "PrincipalType": 4,
                    },
                    {
                        "LoginName": "c:0t.c|tenant|9908e57b-4444-4a0e-af96-e8ca83c0a0e5",
                        "Title": "Everyone",
                        "PrincipalType": 4,
                    },
                    {
                        "Id": 3,
                        "Title": "Alice",
                        "Email": "alice@contoso.com",
                        "PrincipalType": 1,
                    },
                ]
            }
        }
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[groups_resp, users_resp])
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        mock_cred = AsyncMock()
        mock_cred.get_token = AsyncMock(return_value=MagicMock(token="sp-token"))
        c._fetch_graph_group_members = AsyncMock(
            return_value=[{"id": "u1", "email": "a@b.com", "name": "Alice"}]
        )
        cred_ctx = MagicMock()
        cred_ctx.__aenter__ = AsyncMock(return_value=mock_cred)
        cred_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{MODULE}.ClientSecretCredential", return_value=cred_ctx), \
             patch(f"{MODULE}.httpx.AsyncClient", return_value=mock_http):
            await c._sync_user_groups()
        dep.on_new_user_groups.assert_awaited_once()
        assert len(dep.on_new_user_groups.await_args[0][0]) >= 1

    @pytest.mark.asyncio
    async def test_certificate_credential_context(self):
        c, dep = _make_connector(return_dep=True)
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = None
        c.certificate_path = "/tmp/cert.pem"
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        c._sync_azure_ad_groups = AsyncMock()
        c._get_all_sites = AsyncMock(return_value=[])

        cred_ctx = MagicMock()
        cred_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        cred_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{MODULE}.CertificateCredential", return_value=cred_ctx), \
             patch(f"{MODULE}.httpx.AsyncClient") as mock_http_cls:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http_cls.return_value = mock_http
            await c._sync_user_groups()
        dep.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_azure_ad_error_and_site_without_web_url(self):
        c, dep = _make_connector(return_dep=True)
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = "s"
        c.certificate_path = None
        c._sync_azure_ad_groups = AsyncMock(side_effect=Exception("aad fail"))
        site = MagicMock()
        site.id = "s1"
        site.display_name = "No URL"
        site.name = "nourl"
        c._get_all_sites = AsyncMock(return_value=[site])
        no_url = MagicMock()
        no_url.web_url = None
        c.client = MagicMock()
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=no_url)
        cred_ctx = MagicMock()
        cred_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        cred_ctx.__aexit__ = AsyncMock(return_value=False)
        mock_http = AsyncMock()
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{MODULE}.ClientSecretCredential", return_value=cred_ctx), \
             patch(f"{MODULE}.httpx.AsyncClient", return_value=mock_http):
            await c._sync_user_groups()
        dep.on_new_user_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unauthorized_site_groups_response(self):
        c, dep = _make_connector(return_dep=True)
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = "s"
        c.certificate_path = None
        c._sync_azure_ad_groups = AsyncMock()
        site = MagicMock()
        site.id = "s1"
        site.display_name = "S"
        site.name = "s"
        c._get_all_sites = AsyncMock(return_value=[site])
        details = MagicMock()
        details.web_url = "https://contoso.sharepoint.com/sites/x"
        c.client = MagicMock()
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=details)
        unauth = MagicMock()
        unauth.status_code = HTTPStatus.UNAUTHORIZED
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(return_value=unauth)
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        cred_ctx = MagicMock()
        cred_ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
        cred_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{MODULE}.ClientSecretCredential", return_value=cred_ctx), \
             patch(f"{MODULE}.httpx.AsyncClient", return_value=mock_http):
            await c._sync_user_groups()
        dep.on_new_user_groups.assert_not_awaited()


# ---------------------------------------------------------------------------
# Page HTML processing
# ---------------------------------------------------------------------------


class TestSharePointGetPageContentExtended:
    @pytest.mark.asyncio
    async def test_embedded_images_and_list_webparts(self):
        c = _make_connector()
        c.client = MagicMock()
        site_info = MagicMock()
        site_info.web_url = "https://contoso.sharepoint.com/sites/test"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_info)
        c._get_sharepoint_access_token = AsyncMock(return_value="token")
        list_id = "11111111-2222-3333-4444-555555555555"
        wp_data = {
            "properties": {"selectedListId": list_id, "webPartTitle": "Tasks"},
            "serverProcessedContent": {"searchablePlainTexts": {"listTitle": "My Tasks"}},
        }
        canvas = (
            '<img src="/sites/test/Images/pic.png"/>'
            f"<div data-sp-webpartdata='{json.dumps(wp_data)}'></div>"
        )
        page_resp = MagicMock()
        page_resp.status_code = HTTPStatus.OK.value
        page_resp.json.return_value = {"d": {"CanvasContent1": canvas}}
        img_resp = MagicMock()
        img_resp.status_code = HTTPStatus.OK.value
        img_resp.headers = {"Content-Type": "image/png"}
        img_resp.content = b"\x89PNG"
        list_resp = MagicMock()
        list_resp.status_code = HTTPStatus.OK.value
        list_resp.json.return_value = {
            "d": {"results": [{"Title": "Task 1", "Description": "Do it"}]}
        }
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[page_resp, img_resp, list_resp])
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{MODULE}.httpx.AsyncClient", return_value=mock_http), \
             patch(f"{MODULE}.clean_html_output", side_effect=lambda soup, **_: str(soup)):
            result = await c._get_page_content("site-1", "page-1")
        assert result is not None
        assert mock_http.get.await_count >= 2

    @pytest.mark.asyncio
    async def test_list_webpart_fetch_failure(self):
        c = _make_connector()
        c.client = MagicMock()
        site_info = MagicMock()
        site_info.web_url = "https://contoso.sharepoint.com/sites/test"
        c.client.sites.by_site_id.return_value.get = AsyncMock(return_value=site_info)
        c._get_sharepoint_access_token = AsyncMock(return_value="token")
        list_id = "11111111-2222-3333-4444-555555555555"
        wp_data = {"properties": {"selectedListId": list_id}}
        canvas = f"<div data-sp-webpartdata='{json.dumps(wp_data)}'></div>"
        page_resp = MagicMock()
        page_resp.status_code = HTTPStatus.OK.value
        page_resp.json.return_value = {"d": {"CanvasContent1": canvas}}
        list_resp = MagicMock()
        list_resp.status_code = HTTPStatus.FORBIDDEN.value
        mock_http = AsyncMock()
        mock_http.get = AsyncMock(side_effect=[page_resp, list_resp])
        mock_http.__aenter__ = AsyncMock(return_value=mock_http)
        mock_http.__aexit__ = AsyncMock(return_value=False)
        with patch(f"{MODULE}.httpx.AsyncClient", return_value=mock_http), \
             patch(f"{MODULE}.clean_html_output", side_effect=lambda soup, **_: str(soup)):
            result = await c._get_page_content("site-1", "page-1")
        assert result is not None


# ---------------------------------------------------------------------------
# Filter-options search pagination
# ---------------------------------------------------------------------------


class TestSharePointFilterOptionsPagination:
    @pytest.mark.asyncio
    async def test_stateless_page_two_skips_first_batch(self):
        c = _make_connector()
        c.tenant_region = "NAM"
        c.msgraph_client = MagicMock()

        def _hit(site_id: str, label: str):
            return {
                "resource": {
                    "id": site_id,
                    "webUrl": f"https://contoso.sharepoint.com/sites/{site_id}",
                    "displayName": label,
                }
            }

        raw = MagicMock()
        raw.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "moreResultsAvailable": False,
                    "hits": [
                        _hit("s1", "One"),
                        _hit("s2", "Two"),
                        _hit("s3", "Three"),
                        _hit("s4", "Four"),
                    ],
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw)
        page2 = await c._get_site_options(page=2, limit=2, search="")
        assert page2.success is True
        assert len(page2.options) == 2
        assert page2.options[0].label == "Three"

    @pytest.mark.asyncio
    async def test_resume_cursor_accepted_skip(self):
        c = _make_connector()
        c.tenant_region = "NAM"
        c.msgraph_client = MagicMock()
        hits = [{
            "resource": {
                "id": f"site-{i}",
                "webUrl": f"https://contoso.sharepoint.com/sites/site-{i}",
                "displayName": f"Site {i}",
            }
        } for i in range(5)]
        raw = MagicMock()
        raw.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "moreResultsAvailable": True,
                    "hits": hits,
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw)
        first = await c._get_site_options(page=1, limit=2, search="team")
        assert first.has_more is True
        assert first.cursor
        resumed = await c._get_site_options(
            page=1, limit=2, search="team", cursor=first.cursor
        )
        assert resumed.success is True

    @pytest.mark.asyncio
    async def test_extra_filtered_in_same_batch_sets_cursor(self):
        c = _make_connector()
        c.tenant_region = "NAM"
        c.msgraph_client = MagicMock()

        def _hit(i: int):
            return {
                "resource": {
                    "id": f"s{i}",
                    "webUrl": f"https://contoso.sharepoint.com/sites/s{i}",
                    "displayName": f"S{i}",
                }
            }

        raw = MagicMock()
        raw.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "moreResultsAvailable": True,
                    "hits": [_hit(i) for i in range(5)],
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw)
        result = await c._get_site_options(page=1, limit=2, search="x")
        assert len(result.options) == 2
        assert result.has_more is True
        assert result.cursor


# ---------------------------------------------------------------------------
# _sync_site_content page branches
# ---------------------------------------------------------------------------


class TestSharePointSyncSiteContentPages:
    @pytest.mark.asyncio
    async def test_pages_deleted_updated_and_batch_flush(self):
        c, dep = _make_connector(return_dep=True)
        c.batch_size = 1
        c._get_date_filters = MagicMock(return_value=(None, None, None, None))
        c._handle_record_updates = AsyncMock()
        record = MagicMock()
        deleted = RecordUpdate(
            record=None,
            external_record_id="p-del",
            is_new=False,
            is_updated=False,
            is_deleted=True,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        updated = RecordUpdate(
            record=record,
            external_record_id="p-upd",
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
        )
        created = RecordUpdate(
            record=record,
            external_record_id="p-new",
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )

        async def drive_gen(*_a, **_k):
            return
            yield

        async def page_gen(*_a, **_k):
            yield (None, [], deleted)
            yield (None, [], updated)
            yield (record, [], created)

        c._process_site_drives = drive_gen
        c._process_site_pages = page_gen
        rg = MagicMock()
        rg.external_group_id = "site-1"
        rg.name = "Site"
        rg.id = "rg-1"
        await c._sync_site_content(rg)
        assert c._handle_record_updates.await_count == 2
        assert dep.on_new_records.await_count >= 1


# ---------------------------------------------------------------------------
# SharePoint REST token
# ---------------------------------------------------------------------------


class TestSharePointAccessToken:
    @pytest.mark.asyncio
    async def test_domain_without_hostname_uses_strip(self):
        c = _make_connector()
        c.sharepoint_domain = "contoso.sharepoint.com"
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = "secret"
        c.certificate_path = None
        mock_cred = AsyncMock()
        mock_cred.get_token = AsyncMock(return_value=MagicMock(token="tok"))
        cred_ctx = MagicMock()
        cred_ctx.__aenter__ = AsyncMock(return_value=mock_cred)
        cred_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("azure.identity.aio.ClientSecretCredential", return_value=cred_ctx):
            token = await c._get_sharepoint_access_token()
        assert token == "tok"
        assert mock_cred.get_token.await_args[0][0].startswith("https://contoso.sharepoint.com")

    @pytest.mark.asyncio
    async def test_certificate_path_token(self):
        c = _make_connector()
        c.sharepoint_domain = "https://contoso.sharepoint.com"
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = None
        c.certificate_path = "/tmp/cert.pem"
        c.certificate_password = None
        mock_cred = AsyncMock()
        mock_cred.get_token = AsyncMock(return_value=MagicMock(token="cert-tok"))
        cred_ctx = MagicMock()
        cred_ctx.__aenter__ = AsyncMock(return_value=mock_cred)
        cred_ctx.__aexit__ = AsyncMock(return_value=False)
        with patch("azure.identity.aio.CertificateCredential", return_value=cred_ctx):
            token = await c._get_sharepoint_access_token()
        assert token == "cert-tok"


# ---------------------------------------------------------------------------
# Subsites and Azure AD group sync
# ---------------------------------------------------------------------------


class TestSharePointGetSubsites:
    @pytest.mark.asyncio
    async def test_returns_subsites_and_populates_cache(self):
        c = _make_connector()
        c.client = MagicMock()
        subsite = MagicMock()
        subsite.id = "sub-1"
        subsite.web_url = "https://contoso.sharepoint.com/sites/sub"
        subsite.display_name = "Sub"
        subsite.name = "sub"
        subsite.created_date_time = None
        subsite.last_modified_date_time = None
        result = MagicMock()
        result.value = [subsite]
        c._safe_api_call = AsyncMock(return_value=result)
        subsites = await c._get_subsites("site-1")
        assert len(subsites) == 1
        assert "sub-1" in c.site_cache

    @pytest.mark.asyncio
    async def test_exception_returns_empty(self):
        c = _make_connector()
        c.client = MagicMock()
        c._safe_api_call = AsyncMock(side_effect=Exception("fail"))
        assert await c._get_subsites("site-1") == []


class TestSharePointSyncAzureAdGroups:
    @pytest.mark.asyncio
    async def test_initial_full_sync_path(self):
        c, dep = _make_connector(return_dep=True)
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.user_group_sync_point.update_sync_point = AsyncMock()
        c._get_initial_delta_link = AsyncMock(return_value="https://delta")
        c._perform_initial_full_sync = AsyncMock()
        await c._sync_azure_ad_groups()
        c._perform_initial_full_sync.assert_awaited_once()
        c.user_group_sync_point.update_sync_point.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_delta_sync_when_sync_point_exists(self):
        c = _make_connector()
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.read_sync_point = AsyncMock(
            return_value={"deltaLink": "https://existing-delta"}
        )
        c._perform_delta_sync = AsyncMock()
        await c._sync_azure_ad_groups()
        c._perform_delta_sync.assert_awaited_once_with(
            "https://existing-delta",
            ANY,
        )


# ---------------------------------------------------------------------------
# Lists / list items processing
# ---------------------------------------------------------------------------


class TestSharePointProcessSiteLists:
    @pytest.mark.asyncio
    async def test_yields_list_record(self):
        c = _make_connector()
        c.client = MagicMock()
        list_obj = MagicMock()
        list_obj.id = "list-1"
        list_obj.display_name = "Tasks"
        list_obj.name = "tasks"
        lists_response = MagicMock()
        lists_response.value = [list_obj]
        c._safe_api_call = AsyncMock(return_value=lists_response)
        c._should_skip_list = MagicMock(return_value=False)
        list_record = MagicMock()
        c._create_list_record = AsyncMock(return_value=list_record)
        c._get_list_permissions = AsyncMock(return_value=[])

        async def _empty_items(*_a, **_k):
            return
            yield

        c._process_list_items = _empty_items
        collected = []
        async for item in c._process_site_lists("site-1"):
            collected.append(item)
        assert len(collected) == 1
        assert collected[0][0] is list_record


class TestSharePointProcessListItems:
    @pytest.mark.asyncio
    async def test_yields_list_item_record(self):
        c = _make_connector()
        c.client = MagicMock()
        c.list_sync_point = MagicMock()
        c.list_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.list_sync_point.update_sync_point = AsyncMock()
        sp_item = MagicMock()
        sp_item.id = "item-1"
        items_response = MagicMock()
        items_response.value = [sp_item]
        items_response.odata_next_link = None
        c._safe_api_call = AsyncMock(return_value=items_response)
        list_item_record = MagicMock()
        c._create_list_item_record = AsyncMock(return_value=list_item_record)
        c._get_list_item_permissions = AsyncMock(return_value=[])
        collected = []
        async for item in c._process_list_items("site-1", "list-1"):
            collected.append(item)
        assert len(collected) == 1
        c.list_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_pagination_with_skip_token(self):
        c = _make_connector()
        c.client = MagicMock()
        c.list_sync_point = MagicMock()
        c.list_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.list_sync_point.update_sync_point = AsyncMock()
        sp_item = MagicMock()
        sp_item.id = "item-1"
        page1 = MagicMock()
        page1.value = [sp_item]
        page1.odata_next_link = (
            "https://graph.microsoft.com/v1.0/items?$skiptoken=nextpage"
        )
        page2 = MagicMock()
        page2.value = []
        page2.odata_next_link = None
        c._safe_api_call = AsyncMock(side_effect=[page1, page2])
        c._create_list_item_record = AsyncMock(return_value=MagicMock())
        c._get_list_item_permissions = AsyncMock(return_value=[])
        collected = []
        async for _item in c._process_list_items("site-1", "list-1"):
            collected.append(_item)
        assert len(collected) == 1
        c.list_sync_point.update_sync_point.assert_awaited()


# ---------------------------------------------------------------------------
# Azure AD full / delta sync (execute real helpers)
# ---------------------------------------------------------------------------


class TestSharePointPerformInitialFullSync:
    @pytest.mark.asyncio
    async def test_processes_groups_and_publishes(self):
        c, dep = _make_connector(return_dep=True)
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Engineering"
        group.description = "Team"
        group.created_date_time = None
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_user_groups = AsyncMock(return_value=[group])
        user_group = MagicMock()
        c._process_single_group = AsyncMock(return_value=(user_group, []))
        await c._perform_initial_full_sync()
        dep.on_new_user_groups.assert_awaited_once()


class TestSharePointPerformDeltaSync:
    @pytest.mark.asyncio
    async def test_removed_group_and_member_delta(self):
        c, dep = _make_connector(return_dep=True)
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        removed_group = MagicMock()
        removed_group.id = "g-del"
        removed_group.display_name = "Old"
        removed_group.additional_data = {"@removed": {}}
        updated_group = MagicMock()
        updated_group.id = "g2"
        updated_group.display_name = "New"
        updated_group.additional_data = {
            "members@delta": [
                {"id": "u1", "@removed": {}},
                {"id": "u2"},
            ]
        }
        c._handle_delete_group = AsyncMock(return_value=True)
        c._handle_group_create = AsyncMock(return_value=True)
        c._process_member_change = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [removed_group, updated_group],
            "delta_link": "https://delta/final",
        })
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        await c._perform_delta_sync("https://delta/start", "sync-key")
        c._handle_delete_group.assert_awaited_once_with("g-del")
        c._handle_group_create.assert_awaited_once()
        assert c._process_member_change.await_count == 2

    @pytest.mark.asyncio
    async def test_delete_and_create_failures_logged(self):
        c = _make_connector()
        removed_group = MagicMock()
        removed_group.id = "g-del"
        removed_group.additional_data = {"@removed": {}}
        updated_group = MagicMock()
        updated_group.id = "g2"
        updated_group.display_name = "G2"
        updated_group.additional_data = {}
        c._handle_delete_group = AsyncMock(return_value=False)
        c._handle_group_create = AsyncMock(return_value=False)
        c._process_member_change = AsyncMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(return_value={
            "groups": [removed_group, updated_group],
            "delta_link": "https://delta/final",
        })
        c.user_group_sync_point = MagicMock()
        c.user_group_sync_point.update_sync_point = AsyncMock()
        await c._perform_delta_sync("https://delta/start", "sync-key")
        c._handle_delete_group.assert_awaited_once()
        c._handle_group_create.assert_awaited_once()


# ---------------------------------------------------------------------------
# Reindex / source check helpers
# ---------------------------------------------------------------------------


class TestSharePointCheckUpdatedFileRecord:
    @pytest.mark.asyncio
    async def test_returns_updated_file_tuple(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"
        item = MagicMock()
        item.deleted = None
        item.parent_reference = MagicMock(site_id="site-1")
        updated = MagicMock()
        updated.id = "rec-1"
        record_update = RecordUpdate(
            record=updated,
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=False,
            permissions_changed=False,
            new_permissions=[MagicMock()],
        )
        c._safe_api_call = AsyncMock(return_value=item)
        c._process_drive_item = AsyncMock(return_value=record_update)
        result = await c._check_and_fetch_updated_file_record(record, [])
        assert result is not None
        assert result[0] is updated

    @pytest.mark.asyncio
    async def test_missing_ids_returns_none(self):
        c = _make_connector()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_group_id = None
        record.external_record_id = "item-1"
        assert await c._check_and_fetch_updated_file_record(record, []) is None


class TestSharePointGetSitePermissionsCustomGroupDedup:
    @pytest.mark.asyncio
    async def test_skips_duplicate_custom_group_key(self):
        c = _make_connector()
        c.site_cache["site-1"] = SiteMetadata(
            site_id="site-1",
            site_url="https://contoso.sharepoint.com/sites/test",
            site_name="Test",
            is_root=False,
        )
        c._get_sharepoint_access_token = AsyncMock(return_value="token")
        c._get_sharepoint_group_users = AsyncMock(side_effect=[[], [], []])
        c._get_custom_sharepoint_groups = AsyncMock(return_value=[
            {"id": "99", "title": "Dup", "permission_level": PermissionType.READ},
            {"id": "99", "title": "Dup Again", "permission_level": PermissionType.WRITE},
        ])
        perms = await c._get_site_permissions("site-1")
        group_perms = [p for p in perms if getattr(p, "entity_type", None) == EntityType.GROUP]
        assert len(group_perms) == 1


class TestSharePointSyncSiteContentDriveBatch:
    @pytest.mark.asyncio
    async def test_drive_batch_flush_before_pages(self):
        c, dep = _make_connector(return_dep=True)
        c.batch_size = 2
        c._get_date_filters = MagicMock(return_value=(None, None, None, None))
        records = [MagicMock(), MagicMock(), MagicMock()]
        updates = [
            RecordUpdate(
                record=r,
                is_new=True,
                is_updated=False,
                is_deleted=False,
                metadata_changed=False,
                content_changed=False,
                permissions_changed=False,
            )
            for r in records
        ]

        async def drive_gen(*_a, **_k):
            for r, ru in zip(records, updates):
                yield (r, [], ru)

        async def page_gen(*_a, **_k):
            return
            yield

        c._process_site_drives = drive_gen
        c._process_site_pages = page_gen
        rg = MagicMock()
        rg.external_group_id = "site-1"
        rg.name = "Site"
        rg.id = "rg-1"
        await c._sync_site_content(rg)
        assert dep.on_new_records.await_count >= 2


class TestSharePointGetInitialDeltaLink:
    @pytest.mark.asyncio
    async def test_follows_next_link_then_returns_delta(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_groups_delta_response = AsyncMock(side_effect=[
            {"groups": [], "next_link": "https://graph/next"},
            {"groups": [], "delta_link": "https://graph/delta-final"},
        ])
        link = await c._get_initial_delta_link()
        assert link == "https://graph/delta-final"


class TestSharePointProcessSingleGroup:
    @pytest.mark.asyncio
    async def test_user_member(self):
        c = _make_connector()
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Team"
        group.description = "d"
        group.created_date_time = None
        member = MagicMock()
        member.odata_type = "#microsoft.graph.user"
        member.id = "u1"
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(return_value=[member])
        app_user = MagicMock()
        c._create_app_user_from_member = MagicMock(return_value=app_user)
        result = await c._process_single_group(group)
        assert result is not None
        assert len(result[1]) == 1

    @pytest.mark.asyncio
    async def test_nested_group_expansion(self):
        c = _make_connector()
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Parent"
        group.description = None
        group.created_date_time = None
        nested = MagicMock()
        nested.odata_type = "#microsoft.graph.group"
        nested.id = "g2"
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(return_value=[nested])
        c._get_users_from_nested_group = AsyncMock(return_value=[MagicMock()])
        result = await c._process_single_group(group)
        assert result is not None
        c._get_users_from_nested_group.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_skips_unknown_member_type(self):
        c = _make_connector()
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Team"
        group.description = None
        group.created_date_time = None
        unknown = MagicMock()
        unknown.odata_type = "#microsoft.graph.device"
        unknown.id = "d1"
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(return_value=[unknown])
        result = await c._process_single_group(group)
        assert result is not None
        assert result[1] == []

    @pytest.mark.asyncio
    async def test_processing_failure_returns_none(self):
        c = _make_connector()
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        group = MagicMock()
        group.id = "g1"
        group.display_name = "Bad"
        group.description = None
        group.created_date_time = None
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_group_members = AsyncMock(side_effect=Exception("boom"))
        assert await c._process_single_group(group) is None


class TestSharePointCheckUpdatedPageRecord:
    @pytest.mark.asyncio
    async def test_returns_updated_page(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-p"
        record.external_record_group_id = "site-1"
        record.external_record_id = "page-1"
        record.external_revision_id = "old-etag"
        page = MagicMock()
        page.e_tag = "new-etag"
        c._safe_api_call = AsyncMock(return_value=page)
        page_record = MagicMock()
        page_record.id = "rec-p"
        c._create_page_record = AsyncMock(return_value=page_record)
        c._get_page_permissions = AsyncMock(return_value=[])
        result = await c._check_and_fetch_updated_page_record(record)
        assert result is not None
        assert result[0] is page_record

    @pytest.mark.asyncio
    async def test_unchanged_etag_returns_none(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-p"
        record.external_record_group_id = "site-1"
        record.external_record_id = "page-1"
        record.external_revision_id = "same-etag"
        page = MagicMock()
        page.e_tag = "same-etag"
        c._safe_api_call = AsyncMock(return_value=page)
        assert await c._check_and_fetch_updated_page_record(record) is None

    @pytest.mark.asyncio
    async def test_site_name_fetch_warning_still_updates(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-p"
        record.external_record_group_id = "site-1"
        record.external_record_id = "page-1"
        record.external_revision_id = "old"
        page = MagicMock()
        page.e_tag = "new"
        site_response = MagicMock()
        site_response.display_name = "My Site"
        c._safe_api_call = AsyncMock(side_effect=[page, site_response])
        page_record = MagicMock()
        c._create_page_record = AsyncMock(return_value=page_record)
        c._get_page_permissions = AsyncMock(return_value=[])
        result = await c._check_and_fetch_updated_page_record(record)
        assert result is not None
        assert result[0].id == "rec-p"

    @pytest.mark.asyncio
    async def test_forbidden_returns_none(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-p"
        record.external_record_group_id = "site-1"
        record.external_record_id = "page-1"
        c._safe_api_call = AsyncMock(
            side_effect=Exception(f"{HttpStatusCode.NOT_FOUND.value} notfound")
        )
        assert await c._check_and_fetch_updated_page_record(record) is None


class TestSharePointCheckUpdatedListItemRecord:
    @pytest.mark.asyncio
    async def test_returns_updated_list_item(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-li"
        record.external_record_id = "item-1"
        record.external_revision_id = "old"
        record.semantic_metadata = {"site_id": "site-1", "list_id": "list-1"}
        item = MagicMock()
        item.e_tag = "new"
        list_record = MagicMock()
        c._safe_api_call = AsyncMock(return_value=item)
        c._create_list_item_record = AsyncMock(return_value=list_record)
        c._get_list_item_permissions = AsyncMock(return_value=[])
        result = await c._check_and_fetch_updated_list_item_record(record)
        assert result is not None
        assert result[0].id == "rec-li"

    @pytest.mark.asyncio
    async def test_forbidden_returns_none(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-li"
        record.external_record_id = "item-1"
        record.external_revision_id = "e1"
        record.semantic_metadata = {"site_id": "site-1", "list_id": "list-1"}
        c._safe_api_call = AsyncMock(
            side_effect=Exception(f"{HttpStatusCode.FORBIDDEN.value} accessdenied")
        )
        assert await c._check_and_fetch_updated_list_item_record(record) is None


class TestSharePointCheckUpdatedRecordDispatch:
    @pytest.mark.asyncio
    async def test_unsupported_record_type(self):
        c = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = "ext-1"
        record.record_type = RecordType.MAIL
        assert await c._check_and_fetch_updated_record(record, []) is None

    @pytest.mark.asyncio
    async def test_missing_external_id(self):
        c = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.external_record_id = None
        record.record_type = RecordType.FILE
        assert await c._check_and_fetch_updated_record(record, []) is None


class TestSharePointRunSyncMultiBatch:
    @pytest.mark.asyncio
    async def test_sleep_between_site_batches(self):
        c, dep = _make_connector(return_dep=True)
        c.connector_name = Connectors.SHAREPOINT_ONLINE
        c.max_concurrent_batches = 1
        c.credential = AsyncMock()
        c.credential.get_token = AsyncMock(return_value=MagicMock(token="t"))
        c.tenant_id = "t"
        c.client_id = "c"
        c.client_secret = "s"
        c.certificate_path = None
        c.client = MagicMock()
        c.msgraph_client = MagicMock()
        c.msgraph_client.get_all_users = AsyncMock(return_value=[])

        def _site(suffix: str):
            site = MagicMock()
            site.id = (
                f"contoso.sharepoint.com,"
                f"{'a' * 36},"
                f"{suffix * 36}"
            )
            site.display_name = f"Site {suffix}"
            site.name = suffix
            site.description = None
            site.web_url = f"https://contoso.sharepoint.com/sites/{suffix}"
            site.created_date_time = None
            site.last_modified_date_time = None
            return site

        c._sync_user_groups = AsyncMock()
        c._get_all_sites = AsyncMock(return_value=[_site("1"), _site("2")])
        c._get_site_permissions = AsyncMock(return_value=[])
        c._sync_site_content = AsyncMock()

        with patch(f"{MODULE}.load_connector_filters", new_callable=AsyncMock,
                   return_value=(FilterCollection(), FilterCollection())), \
             patch(f"{MODULE}.GraphServiceClient"), \
             patch(f"{MODULE}.MSGraphClient"), \
             patch(f"{MODULE}.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
            await c.run_sync()
            mock_sleep.assert_awaited_once()
        dep.on_new_record_groups.assert_awaited_once()
        assert c._sync_site_content.await_count == 2


class TestSharePointProcessSitePages:
    @pytest.mark.asyncio
    async def test_pages_not_accessible_returns_early(self):
        c = _make_connector()
        c.client = MagicMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c._safe_api_call = AsyncMock(
            side_effect=Exception(f"{HttpStatusCode.FORBIDDEN.value} accessdenied")
        )
        collected = []
        async for item in c._process_site_pages("site-1", "Site A"):
            collected.append(item)
        assert collected == []

    @pytest.mark.asyncio
    async def test_skips_system_account_page(self):
        c = _make_connector()
        c.client = MagicMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        page = MagicMock()
        page.id = "page-1"
        page.title = "Template"
        page.name = "template"
        page.e_tag = "e1"
        created_by = MagicMock()
        user = MagicMock()
        user.display_name = "System Account"
        created_by.user = user
        page.created_by = created_by
        pages_response = MagicMock()
        pages_response.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_response)
        c._pass_page_date_filters = MagicMock(return_value=True)
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        c.data_store_provider.transaction.return_value = mock_tx
        collected = []
        async for item in c._process_site_pages("site-1", "Site A"):
            collected.append(item)
        assert collected == []
        c.page_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_non_access_error_swallowed_by_outer_handler(self):
        c = _make_connector()
        c.client = MagicMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()

        async def exploding_safe(*_args, **_kwargs):
            raise Exception("graph outage")

        c._safe_api_call = exploding_safe
        collected = []
        async for item in c._process_site_pages("site-1", "Site A"):
            collected.append(item)
        assert collected == []
        c.page_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_yields_updated_existing_page(self):
        c = _make_connector()
        c.client = MagicMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        page = MagicMock()
        page.id = "page-1"
        page.title = "Updated"
        page.name = "updated"
        page.e_tag = "etag-new"
        page.created_by = None
        pages_response = MagicMock()
        pages_response.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_response)
        c._pass_page_date_filters = MagicMock(return_value=True)
        existing = MagicMock()
        existing.external_revision_id = "etag-old"
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=existing)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        c.data_store_provider.transaction.return_value = mock_tx
        page_record = MagicMock()
        c._create_page_record = AsyncMock(return_value=page_record)
        c._get_page_permissions = AsyncMock(return_value=[])
        collected = []
        async for item in c._process_site_pages("site-1", "Site A"):
            collected.append(item)
        assert len(collected) == 1
        update = collected[0][2]
        assert update.is_updated is True
        assert update.content_changed is True

    @pytest.mark.asyncio
    async def test_yields_new_page_record(self):
        c = _make_connector()
        c.client = MagicMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        page = MagicMock()
        page.id = "page-1"
        page.title = "Home"
        page.name = "home"
        page.e_tag = "etag-1"
        page.created_by = None
        pages_response = MagicMock()
        pages_response.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_response)
        c._pass_page_date_filters = MagicMock(return_value=True)
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        c.data_store_provider.transaction.return_value = mock_tx
        page_record = MagicMock()
        c._create_page_record = AsyncMock(return_value=page_record)
        c._get_page_permissions = AsyncMock(return_value=[])
        collected = []
        async for item in c._process_site_pages("site-1", "Site A"):
            collected.append(item)
        assert len(collected) == 1
        assert c.stats["pages_processed"] >= 1

    @pytest.mark.asyncio
    async def test_page_processing_error_continues(self):
        c = _make_connector()
        c.client = MagicMock()
        c.page_sync_point = MagicMock()
        c.page_sync_point.read_sync_point = AsyncMock(return_value=None)
        c.page_sync_point.update_sync_point = AsyncMock()
        c.indexing_filters = MagicMock()
        c.indexing_filters.is_enabled = MagicMock(return_value=True)
        page = MagicMock()
        page.id = "page-1"
        page.title = "Bad Page"
        page.name = "bad"
        page.e_tag = "e1"
        page.created_by = None
        pages_response = MagicMock()
        pages_response.value = [page]
        c._safe_api_call = AsyncMock(return_value=pages_response)
        c._pass_page_date_filters = MagicMock(return_value=True)
        mock_tx = MagicMock()
        mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
        mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
        mock_tx.__aexit__ = AsyncMock(return_value=False)
        c.data_store_provider.transaction.return_value = mock_tx
        c._create_page_record = AsyncMock(side_effect=Exception("create fail"))
        collected = []
        async for item in c._process_site_pages("site-1", "Site A"):
            collected.append(item)
        assert collected == []


class TestSharePointSearchCursorDecode:
    def test_invalid_cursor_returns_none(self):
        assert SharePointConnector._sharepoint_search_cursor_decode("not-valid!!!") is None

    def test_oversized_from_offset_rejected(self):
        import base64
        payload = json.dumps({"f": 999999, "b": 40, "q": "abc"})
        token = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
        assert SharePointConnector._sharepoint_search_cursor_decode(token) is None


class TestSharePointCheckUpdatedFileRecordDeleted:
    @pytest.mark.asyncio
    async def test_deleted_item_returns_none(self):
        c = _make_connector()
        c.client = MagicMock()
        record = MagicMock()
        record.id = "rec-1"
        record.external_record_group_id = "drive-1"
        record.external_record_id = "item-1"
        item = MagicMock()
        item.deleted = MagicMock()
        c._safe_api_call = AsyncMock(return_value=item)
        assert await c._check_and_fetch_updated_file_record(record, []) is None


class TestSharePointDocumentLibraryFilterBranches:
    @pytest.mark.asyncio
    async def test_rejects_hits_without_site_id_and_duplicates(self):
        c = _make_connector()
        c.msgraph_client = MagicMock()
        raw = MagicMock()
        raw.additional_data = {
            "value": [{
                "hitsContainers": [{
                    "total": 4,
                    "hits": [
                        {
                            "resource": {
                                "id": "lib-1",
                                "webUrl": "https://contoso.sharepoint.com/Shared%20Documents",
                                "name": "Documents",
                                "displayName": "Root - Documents",
                                "parentReference": {},
                            },
                            "summary": "",
                        },
                        {
                            "resource": {
                                "id": "lib-1",
                                "webUrl": "https://contoso.sharepoint.com/sites/x/Shared%20Documents",
                                "name": "Documents",
                                "displayName": "Site - Documents",
                                "parentReference": {"siteId": "s1"},
                            },
                            "summary": "",
                        },
                        {
                            "resource": {
                                "id": "lib-1",
                                "webUrl": "https://contoso.sharepoint.com/sites/x/Shared%20Documents",
                                "name": "Documents",
                                "displayName": "Dup",
                                "parentReference": {"siteId": "s1"},
                            },
                            "summary": "DocumentLibrary",
                        },
                        {
                            "resource": {
                                "id": "lib-cal",
                                "webUrl": "https://contoso.sharepoint.com/sites/x/calendar.aspx",
                                "name": "Calendar",
                                "displayName": "Cal",
                                "parentReference": {"siteId": "s1"},
                            },
                            "summary": "DocumentLibrary",
                        },
                    ],
                }]
            }]
        }
        c.msgraph_client.search_query = AsyncMock(return_value=raw)
        result = await c._get_document_library_options(page=1, limit=10, search="")
        assert len(result.options) == 1
        assert "Documents" in result.options[0].label


class TestSharePointGetAllSitesBranches:
    @pytest.mark.asyncio
    async def test_root_exception_continues_with_search(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock(return_value=None)
        c.rate_limiter.__aexit__ = AsyncMock(return_value=None)
        c.enable_subsite_discovery = False
        c.filters = {}
        search_result = MagicMock()
        search_result.value = None
        search_result.odata_next_link = None
        c._safe_api_call = AsyncMock(
            side_effect=[RuntimeError("root fetch blew up"), search_result]
        )
        sites = await c._get_all_sites()
        assert isinstance(sites, list)

    @pytest.mark.asyncio
    async def test_skips_duplicate_search_site_id(self):
        c = _make_connector()
        c.client = MagicMock()
        c.rate_limiter = AsyncMock()
        c.rate_limiter.__aenter__ = AsyncMock(return_value=None)
        c.rate_limiter.__aexit__ = AsyncMock(return_value=None)
        c.enable_subsite_discovery = False
        site_id = f"contoso.sharepoint.com,{'a' * 36},{'b' * 36}"
        root = MagicMock()
        root.id = site_id
        root.display_name = "Root"
        root.name = "root"
        root.web_url = "https://contoso.sharepoint.com/"
        root.created_date_time = None
        root.last_modified_date_time = None
        dup = MagicMock()
        dup.id = site_id
        dup.display_name = "Dup"
        dup.name = "dup"
        dup.web_url = "https://contoso.sharepoint.com/sites/dup"
        dup.created_date_time = None
        dup.last_modified_date_time = None
        search_result = MagicMock()
        search_result.value = [dup]
        search_result.odata_next_link = None
        c._safe_api_call = AsyncMock(side_effect=[root, search_result])
        sites = await c._get_all_sites()
        assert len(sites) == 1


class TestSharePointStreamRecord:
    @pytest.mark.asyncio
    async def test_streams_file_via_signed_url(self):
        c = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.FILE
        record.record_name = "doc.pdf"
        record.mime_type = "application/pdf"
        c.get_signed_url = AsyncMock(return_value="https://signed.example/doc")
        with patch(f"{MODULE}.create_stream_record_response") as mock_response, \
             patch(f"{MODULE}.stream_content") as mock_stream:
            mock_response.return_value = "stream-response"
            mock_stream.return_value = AsyncMock()
            result = await c.stream_record(record)
        assert result == "stream-response"

    @pytest.mark.asyncio
    async def test_streams_page_content(self):
        c = _make_connector()
        record = MagicMock()
        record.id = "r1"
        record.record_type = RecordType.SHAREPOINT_PAGE
        record.external_record_group_id = "site-1"
        record.external_record_id = "page-1"
        record.record_name = "Home.aspx"
        c._get_page_content = AsyncMock(return_value="<p>hello</p>")
        with patch(f"{MODULE}.create_stream_record_response") as mock_response:
            mock_response.return_value = "page-stream"
            result = await c.stream_record(record)
        assert result == "page-stream"

    @pytest.mark.asyncio
    async def test_unsupported_type_raises_bad_request(self):
        c = _make_connector()
        record = MagicMock()
        record.record_type = RecordType.MAIL
        with pytest.raises(HTTPException) as exc_info:
            await c.stream_record(record)
        assert exc_info.value.status_code == HttpStatusCode.BAD_REQUEST.value


class TestSharePointCleanupOuterError:
    @pytest.mark.asyncio
    async def test_cleanup_logs_outer_exception(self):
        c = _make_connector()
        c.credential = AsyncMock()
        c.credential.close = AsyncMock()
        c.site_cache = MagicMock()
        c.site_cache.clear = MagicMock(side_effect=RuntimeError("boom"))
        await c.cleanup()
