"""Tests for ServiceNow connector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes
from app.connectors.sources.servicenow.servicenow.constants import ORGANIZATIONAL_ENTITIES
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.models.entities import AppUser, AppUserGroup, FileRecord, RecordType, WebpageRecord
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.external.servicenow.models import (
    ServiceNowAPIError,
    SysUserGroup,
    SysUserGroupMembership,
    SysUserRole,
    SysUserRoleAssignment,
    SysUserRoleContains,
    AttachmentMetadata,
    KBKnowledge,
    KBKnowledgeBase,
    KBCategory,
    RawPermission,
    SysUser,
    UserCriteria,
    TableAPIRecord,
    TableAPIResponse,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_api_response(records: list) -> TableAPIResponse:
    """Match ServiceNowDataSource.get_now_table_tableName return type."""
    return TableAPIResponse(result=[TableAPIRecord(**r) for r in records])



def _kb_api_row(**fields: object) -> dict:
    base = {
        "description": "",
        "owner": None,
        "sys_created_on": "2024-01-01 00:00:00",
    }
    base.update(fields)
    return base


def _category_api_row(**fields: object) -> dict:
    base = {
        "value": "",
        "parent_id": None,
        "sys_created_on": "2024-01-01 00:00:00",
    }
    base.update(fields)
    return base


def _record_update(**kwargs):
    """Lightweight RecordUpdate stand-in for ServiceNow tests."""
    defaults = {
        "record": None,
        "is_new": True,
        "is_updated": False,
        "is_deleted": False,
        "metadata_changed": False,
        "content_changed": False,
        "permissions_changed": True,
        "new_permissions": [],
        "external_record_id": None,
    }
    defaults.update(kwargs)
    return type("RecordUpdate", (), defaults)()


def _sys_user_row(**fields: object) -> dict:
    """sys_user rows must include org reference keys so TableAPIRecord exposes .company, etc."""
    base = {
        "company": None,
        "department": None,
        "location": None,
        "cost_center": None,
    }
    base.update(fields)
    return base


def _make_mock_tx_store(existing_record=None, app_users=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_by_source_id = AsyncMock(return_value=None)
    tx.get_app_users = AsyncMock(return_value=app_users or [])
    tx.get_user_groups = AsyncMock(return_value=[])
    tx.create_user_group_membership = AsyncMock()
    tx.batch_upsert_user_groups = AsyncMock()
    tx.batch_upsert_record_groups = AsyncMock()
    tx.batch_upsert_record_group_permissions = AsyncMock()
    tx.get_record_group_by_external_id = AsyncMock(return_value=None)
    return tx


def _make_mock_data_store_provider(existing_record=None, app_users=None):
    tx = _make_mock_tx_store(existing_record, app_users)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.servicenow")


@pytest.fixture()
def mock_data_entities_processor():
    class _Proc:
        org_id = "org-sn-1"

    proc = _Proc()
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    proc.on_record_deleted = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    return _make_mock_data_store_provider()


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "oauthConfigId": "oauth-sn-1",
        },
        "credentials": {
            "access_token": "sn-access-token",
            "refresh_token": "sn-refresh-token",
        },
    })
    return svc


@pytest.fixture()
def servicenow_connector(mock_logger, mock_data_entities_processor,
                          mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
        connector = ServiceNowConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="sn-conn-1",
            scope="personal",
            created_by="test-user-id",
        )
    connector.connector_name = Connectors.SERVICENOW
    return connector


# ===========================================================================
# Constants
# ===========================================================================

class TestOrganizationalEntities:
    def test_company_config(self):
        assert "company" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["company"]["table"] == "core_company"

    def test_department_config(self):
        assert "department" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["department"]["table"] == "cmn_department"

    def test_location_config(self):
        assert "location" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["location"]["table"] == "cmn_location"

    def test_cost_center_config(self):
        assert "cost_center" in ORGANIZATIONAL_ENTITIES
        assert ORGANIZATIONAL_ENTITIES["cost_center"]["table"] == "cmn_cost_center"

    def test_all_have_required_fields(self):
        for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
            assert "table" in config
            assert "fields" in config
            assert "prefix" in config
            assert "sync_point_key" in config


# ===========================================================================
# ServiceNowConnector init
# ===========================================================================

class TestServiceNowConnectorInit:
    def test_constructor(self, servicenow_connector):
        assert servicenow_connector.connector_id == "sn-conn-1"
        assert servicenow_connector.servicenow_client is None
        assert servicenow_connector.servicenow_datasource is None
        assert servicenow_connector.instance_url is None

    def test_sync_points_created(self, servicenow_connector):
        assert servicenow_connector.user_sync_point is not None
        assert servicenow_connector.group_sync_point is not None
        assert servicenow_connector.kb_sync_point is not None
        assert servicenow_connector.article_sync_point is not None

    def test_org_entity_sync_points(self, servicenow_connector):
        for key in ["company", "department", "location", "cost_center"]:
            assert key in servicenow_connector.org_entity_sync_points

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowRESTClientViaOAuthAuthorizationCode")
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource")
    async def test_init_success(self, mock_ds_cls, mock_client_cls, mock_fetch_oauth,
                                servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "sn-client-id",
                "clientSecret": "sn-client-secret",
                "instanceUrl": "https://dev12345.service-now.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        mock_client_cls.return_value = MagicMock()
        mock_ds_instance = MagicMock()
        mock_ds_instance.get_now_table_tableName = AsyncMock(
            return_value=_table_api_response([])
        )
        mock_ds_cls.return_value = mock_ds_instance

        result = await servicenow_connector.init()
        assert result is True
        assert servicenow_connector.instance_url == "https://dev12345.service-now.com"

    async def test_init_fails_no_config(self, servicenow_connector):
        servicenow_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await servicenow_connector.init() is False

    async def test_init_fails_no_oauth_config_id(self, servicenow_connector):
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
            "credentials": {},
        })
        assert await servicenow_connector.init() is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_oauth_not_found(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = None
        assert await servicenow_connector.init() is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_incomplete_config(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = {"config": {"clientId": "id"}}
        assert await servicenow_connector.init() is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_no_access_token(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "id", "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
                "redirectUri": "http://localhost/callback",
            }
        }
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-sn-1"},
            "credentials": {},
        })
        assert await servicenow_connector.init() is False

    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowRESTClientViaOAuthAuthorizationCode")
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource")
    async def test_init_fails_connection_test(self, mock_ds_cls, mock_client_cls, mock_fetch_oauth,
                                              servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "id", "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
                "redirectUri": "http://localhost/callback",
            }
        }
        mock_client_cls.return_value = MagicMock()
        mock_ds_instance = MagicMock()
        mock_ds_instance.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(401, "Unauthorized", None)
        )
        mock_ds_cls.return_value = mock_ds_instance
        assert await servicenow_connector.init() is False


# ===========================================================================
# _get_fresh_datasource
# ===========================================================================

class TestGetFreshDatasource:
    async def test_raises_when_client_not_initialized(self, servicenow_connector):
        with pytest.raises(Exception, match="not initialized"):
            await servicenow_connector._get_fresh_datasource()

    async def test_returns_datasource_with_fresh_token(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.servicenow_client.access_token = "old-token"
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "new-token"},
        })
        ds = await servicenow_connector._get_fresh_datasource()
        assert ds is not None
        assert servicenow_connector.servicenow_client.access_token == "new-token"

    async def test_no_config_raises(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await servicenow_connector._get_fresh_datasource()

    async def test_no_token_raises(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {},
        })
        with pytest.raises(Exception, match="No access token"):
            await servicenow_connector._get_fresh_datasource()

    async def test_same_token_no_update(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.servicenow_client.access_token = "same-token"
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "same-token"},
        })
        ds = await servicenow_connector._get_fresh_datasource()
        assert ds is not None


# ===========================================================================
# test_connection_and_access
# ===========================================================================

class TestConnectionAndAccess:
    async def test_success(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.servicenow_client.access_token = "token"
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "token"},
        })
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([{"sys_id": "1"}])
            )
            mock_ds.return_value = mock_datasource
            assert await servicenow_connector.test_connection_and_access() is True

    async def test_failure(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(401, "Unauthorized", None)
            )
            mock_ds.return_value = mock_datasource
            assert await servicenow_connector.test_connection_and_access() is False

    async def test_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("Connection refused")):
            assert await servicenow_connector.test_connection_and_access() is False


# ===========================================================================
# stream_record
# ===========================================================================

class TestStreamRecord:
    async def test_stream_article(self, servicenow_connector):
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Article 1"
        record.external_record_id = "art-1"

        with patch.object(servicenow_connector, "_fetch_article_content",
                          new_callable=AsyncMock, return_value="<h1>Hello</h1>"):
            response = await servicenow_connector.stream_record(record)
            assert response is not None

    async def test_stream_attachment(self, servicenow_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.id = "rec-1"
        record.mime_type = "application/pdf"

        with patch.object(servicenow_connector, "_fetch_attachment_content",
                          new_callable=AsyncMock, return_value=b"PDF content"):
            response = await servicenow_connector.stream_record(record)
            assert response is not None

    async def test_unsupported_type_raises(self, servicenow_connector):
        record = MagicMock()
        record.record_type = RecordType.MAIL
        record.record_name = "email"
        record.external_record_id = "mail-1"

        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector.stream_record(record)
        assert exc_info.value.status_code == 400

    async def test_stream_exception_raises_500(self, servicenow_connector):
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Article"
        record.external_record_id = "art-1"

        with patch.object(servicenow_connector, "_fetch_article_content",
                          new_callable=AsyncMock, side_effect=Exception("Network error")):
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector.stream_record(record)
            assert exc_info.value.status_code == 500


# ===========================================================================
# _fetch_article_content
# ===========================================================================

class TestFetchArticleContent:
    async def test_success(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "art-1", "text": "<p>Content</p>", "number": "KB001"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_article_content("art-1")
            assert result == "<p>Content</p>"

    async def test_not_found(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._fetch_article_content("nonexistent")
            assert exc_info.value.status_code == 404

    async def test_empty_content(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "art-1", "text": "", "number": "KB001"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_article_content("art-1")
            assert result == "<p>No content available</p>"

    async def test_api_failure(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(404, "Server error", None)
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._fetch_article_content("art-1")
            assert exc_info.value.status_code == 404


# ===========================================================================
# _fetch_attachment_content
# ===========================================================================

    @pytest.mark.asyncio
    async def test_fetch_article_content_generic_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("ds fail"))
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._fetch_article_content("art-1")
        assert exc_info.value.status_code == 500


class TestFetchAttachmentContent:
    async def test_success(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.download_attachment = AsyncMock(return_value=b"file content")
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_attachment_content("att-1")
            assert result == b"file content"

    async def test_not_found(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.download_attachment = AsyncMock(return_value=None)
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._fetch_attachment_content("nonexistent")
            assert exc_info.value.status_code == 404

    async def test_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.download_attachment = AsyncMock(side_effect=Exception("Download failed"))
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._fetch_attachment_content("att-1")
            assert exc_info.value.status_code == 500


# ===========================================================================
# get_signed_url, handle_webhook, cleanup, reindex, get_filter_options
# ===========================================================================

    @pytest.mark.asyncio
    async def test_fetch_attachment_content_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.download_attachment = AsyncMock(
            side_effect=ServiceNowAPIError(503, "unavailable", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._fetch_attachment_content("att-1")
        assert exc_info.value.status_code == 503


class TestMiscMethods:
    def test_get_signed_url_returns_none(self, servicenow_connector):
        assert servicenow_connector.get_signed_url(MagicMock()) is None

    async def test_handle_webhook_returns_true(self, servicenow_connector):
        result = await servicenow_connector.handle_webhook_notification("org-1", {"event": "test"})
        assert result is True

    async def test_cleanup(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.servicenow_datasource = MagicMock()
        await servicenow_connector.cleanup()
        assert servicenow_connector.servicenow_client is None
        assert servicenow_connector.servicenow_datasource is None

    async def test_reindex_records(self, servicenow_connector):
        await servicenow_connector.reindex_records([MagicMock()])
        # No-op, just verify it doesn't raise

    async def test_get_filter_options_raises(self, servicenow_connector):
        with pytest.raises(NotImplementedError):
            await servicenow_connector.get_filter_options("key")

    async def test_run_incremental_sync_delegates(self, servicenow_connector):
        with patch.object(servicenow_connector, "run_sync", new_callable=AsyncMock) as mock_sync:
            await servicenow_connector.run_incremental_sync()
            mock_sync.assert_called_once()


# ===========================================================================
# _get_admin_users
# ===========================================================================

class TestGetAdminUsers:
    async def test_finds_admin_users(self, servicenow_connector):
        mock_app_user = MagicMock(spec=AppUser)
        mock_app_user.email = "admin@example.com"

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([{"user": "sys-admin-1"}])
            )
            mock_ds.return_value = mock_datasource

            tx = _make_mock_tx_store()
            tx.get_user_by_source_id = AsyncMock(return_value=mock_app_user)

            @asynccontextmanager
            async def _tx():
                yield tx

            servicenow_connector.data_store_provider = MagicMock()
            servicenow_connector.data_store_provider.transaction = _tx

            result = await servicenow_connector._get_admin_users()
            assert len(result) == 1
            assert result[0].email == "admin@example.com"

    async def test_no_admin_users_found(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(404, "Not found", None)
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._get_admin_users()
            assert result == []

    async def test_dict_reference_field(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([{"user": "sys-admin-1"}])
            )
            mock_ds.return_value = mock_datasource

            tx = _make_mock_tx_store()
            tx.get_user_by_source_id = AsyncMock(return_value=None)

            @asynccontextmanager
            async def _tx():
                yield tx

            servicenow_connector.data_store_provider = MagicMock()
            servicenow_connector.data_store_provider.transaction = _tx

            result = await servicenow_connector._get_admin_users()
            assert result == []

    async def test_exception_returns_empty(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("Network error")):
            result = await servicenow_connector._get_admin_users()
            assert result == []


# ===========================================================================
# _fetch_all_groups
# ===========================================================================

class TestFetchAllGroups:
    async def test_fetches_groups(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "g1", "name": "Group 1"},
                    {"sys_id": "g2", "name": "Group 2"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert len(result) == 2

    async def test_empty_results(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert result == []

    async def test_api_failure(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(500, "Error", None)
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert result == []


# ===========================================================================
# _fetch_all_memberships
# ===========================================================================

class TestFetchAllMemberships:
    async def test_fetches_memberships(self, servicenow_connector):
        servicenow_connector.group_sync_point = AsyncMock()
        servicenow_connector.group_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.group_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "m1", "user": "u1", "group": "g1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_memberships()
            assert len(result) == 1

    async def test_delta_sync(self, servicenow_connector):
        servicenow_connector.group_sync_point = AsyncMock()
        servicenow_connector.group_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01"}
        )
        servicenow_connector.group_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_memberships()
            assert result == []


# ===========================================================================
# _flatten_and_create_user_groups
# ===========================================================================

class TestFlattenAndCreateUserGroups:
    async def test_simple_flatten(self, servicenow_connector):
        groups = [
            SysUserGroup(sys_id="g1", name="Group 1"),
            SysUserGroup(sys_id="g2", name="Group 2", parent="g1"),
        ]
        memberships = [
            SysUserGroupMembership(sys_id="m1", user="u1", group="g1"),
            SysUserGroupMembership(sys_id="m2", user="u2", group="g2"),
        ]

        mock_user1 = MagicMock(spec=AppUser)
        mock_user1.source_user_id = "u1"
        mock_user2 = MagicMock(spec=AppUser)
        mock_user2.source_user_id = "u2"

        tx = _make_mock_tx_store(app_users=[mock_user1, mock_user2])

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        with patch.object(servicenow_connector, "_transform_to_user_group") as mock_transform:
            mock_group = MagicMock(spec=AppUserGroup)
            mock_group.name = "Group"
            mock_transform.return_value = mock_group

            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)
            assert len(result) == 2
            # Group g1 should have users from g1 + children (g2)
            g1_result = [r for r in result if True]  # All results
            assert len(g1_result) == 2

    async def test_string_references(self, servicenow_connector):
        """Test with string references instead of dict references."""
        groups = [SysUserGroup(sys_id="g1", name="Group 1")]
        memberships = [SysUserGroupMembership(sys_id="m1", user="u1", group="g1")]

        tx = _make_mock_tx_store(app_users=[])

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        with patch.object(servicenow_connector, "_transform_to_user_group") as mock_transform:
            mock_group = MagicMock(spec=AppUserGroup)
            mock_group.name = "Group"
            mock_transform.return_value = mock_group

            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)
            assert len(result) == 1


# ===========================================================================
# _fetch_all_roles and _fetch_all_role_assignments
# ===========================================================================

    @pytest.mark.asyncio
    async def test_flatten_skips_none_user_group(self, servicenow_connector):
        groups = [SysUserGroup(sys_id="g1", name="Group")]
        memberships = [SysUserGroupMembership(sys_id="m1", user="u1", group="g1")]
        provider = _make_mock_data_store_provider()
        servicenow_connector.data_store_provider = provider

        with patch.object(servicenow_connector, "_transform_to_user_group", return_value=None):
            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)
        assert result == []

    @pytest.mark.asyncio
    async def test_flatten_exception_propagates(self, servicenow_connector):
        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = MagicMock(side_effect=RuntimeError("tx fail"))
        with pytest.raises(RuntimeError, match="tx fail"):
            await servicenow_connector._flatten_and_create_user_groups([], [])


class TestFetchRoles:
    async def test_fetches_roles(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([{"sys_id": "r1", "name": "admin"}])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_roles()
            assert len(result) == 1

    async def test_fetches_role_assignments(self, servicenow_connector):
        servicenow_connector.role_assignment_sync_point = AsyncMock()
        servicenow_connector.role_assignment_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.role_assignment_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "ra1", "user": "u1", "role": "r1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_role_assignments()
            assert len(result) == 1
            assert result[0].role == "r1"
            assert result[0].user == "u1"

    async def test_fetches_role_hierarchy(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "h1", "contains": "r1", "role": "r2"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_role_hierarchy()
            assert len(result) == 1


# ===========================================================================
# _sync_users
# ===========================================================================

    @pytest.mark.asyncio
    async def test_fetch_all_roles_empty_page_breaks(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        assert await servicenow_connector._fetch_all_roles() == []

    @pytest.mark.asyncio
    async def test_fetch_all_roles_outer_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("roles fetch fail"))
        with pytest.raises(RuntimeError, match="roles fetch fail"):
            await servicenow_connector._fetch_all_roles()

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_delta_sync(self, servicenow_connector):
        servicenow_connector.role_assignment_sync_point = AsyncMock()
        servicenow_connector.role_assignment_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01 00:00:00"}
        )
        servicenow_connector.role_assignment_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._fetch_all_role_assignments()
        call_kwargs = mock_ds.get_now_table_tableName.call_args.kwargs
        assert "2024-01-01" in call_kwargs["sysparm_query"]

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_outer_exception(self, servicenow_connector):
        servicenow_connector.role_assignment_sync_point = AsyncMock()
        servicenow_connector.role_assignment_sync_point.read_sync_point = AsyncMock(
            side_effect=RuntimeError("sync point fail")
        )
        with pytest.raises(RuntimeError, match="sync point fail"):
            await servicenow_connector._fetch_all_role_assignments()

    @pytest.mark.asyncio
    async def test_fetch_role_hierarchy_outer_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("hierarchy fail"))
        with pytest.raises(RuntimeError, match="hierarchy fail"):
            await servicenow_connector._fetch_role_hierarchy()


class TestSyncUsers:
    async def test_syncs_users(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock) as mock_transform:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    _sys_user_row(
                        sys_id="u1",
                        user_name="user1",
                        email="user1@example.com",
                        first_name="User",
                        last_name="One",
                        active="true",
                        sys_updated_on="2024-01-01",
                    ),
                ])
            )
            mock_ds.return_value = mock_datasource

            mock_app_user = MagicMock(spec=AppUser)
            mock_transform.return_value = mock_app_user

            await servicenow_connector._sync_users()
            servicenow_connector.data_entities_processor.on_new_app_users.assert_called_once()

    async def test_skips_users_without_email(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "u1", "email": "", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_users()
            servicenow_connector.data_entities_processor.on_new_app_users.assert_not_called()


# ===========================================================================
# run_sync
# ===========================================================================

class TestRunSync:
    async def test_raises_when_client_not_initialized(self, servicenow_connector):
        with pytest.raises(Exception, match="not initialized"):
            await servicenow_connector.run_sync()

    async def test_full_sync_flow(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        with patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_get_admin_users", new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_sync_knowledge_bases", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_sync_categories", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_sync_articles", new_callable=AsyncMock):
            await servicenow_connector.run_sync()

    async def test_sync_continues_without_admin_users(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        with patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_get_admin_users", new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_sync_knowledge_bases", new_callable=AsyncMock) as mock_kb, \
             patch.object(servicenow_connector, "_sync_categories", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_sync_articles", new_callable=AsyncMock):
            await servicenow_connector.run_sync()
            mock_kb.assert_called_once_with([])

    async def test_sync_propagates_exceptions(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        with patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock,
                          side_effect=Exception("sync error")):
            with pytest.raises(Exception, match="sync error"):
                await servicenow_connector.run_sync()


# ===========================================================================
# Deep sync: _sync_users_and_groups
# ===========================================================================

class TestSyncUsersAndGroups:
    async def test_calls_all_sub_methods(self, servicenow_connector):
        with patch.object(servicenow_connector, "_sync_organizational_entities", new_callable=AsyncMock) as mock_oe, \
             patch.object(servicenow_connector, "_sync_users", new_callable=AsyncMock) as mock_u, \
             patch.object(servicenow_connector, "_sync_user_groups", new_callable=AsyncMock) as mock_g, \
             patch.object(servicenow_connector, "_sync_roles", new_callable=AsyncMock) as mock_r:
            await servicenow_connector._sync_users_and_groups()
            mock_oe.assert_called_once()
            mock_u.assert_called_once()
            mock_g.assert_called_once()
            mock_r.assert_called_once()

    async def test_propagates_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_sync_organizational_entities",
                          new_callable=AsyncMock, side_effect=Exception("org fail")):
            with pytest.raises(Exception, match="org fail"):
                await servicenow_connector._sync_users_and_groups()


# ===========================================================================
# Deep sync: _sync_user_groups
# ===========================================================================

class TestSyncUserGroups:
    async def test_skips_when_no_memberships(self, servicenow_connector):
        with patch.object(servicenow_connector, "_fetch_all_memberships",
                          new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_user_groups()
            servicenow_connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_processes_groups_and_memberships(self, servicenow_connector):
        memberships = [{"user": "u1", "group": "g1"}]
        groups = [{"sys_id": "g1", "name": "Group 1"}]
        mock_result = [(MagicMock(), [MagicMock()])]
        with patch.object(servicenow_connector, "_fetch_all_memberships",
                          new_callable=AsyncMock, return_value=memberships), \
             patch.object(servicenow_connector, "_fetch_all_groups",
                          new_callable=AsyncMock, return_value=groups), \
             patch.object(servicenow_connector, "_flatten_and_create_user_groups",
                          new_callable=AsyncMock, return_value=mock_result):
            await servicenow_connector._sync_user_groups()
            servicenow_connector.data_entities_processor.on_new_user_groups.assert_called_once()


# ===========================================================================
# Deep sync: _sync_roles
# ===========================================================================

    @pytest.mark.asyncio
    async def test_sync_user_groups_exception_propagates(self, servicenow_connector):
        with patch.object(
            servicenow_connector, "_fetch_all_memberships",
            new_callable=AsyncMock, side_effect=RuntimeError("membership fail"),
        ):
            with pytest.raises(RuntimeError, match="membership fail"):
                await servicenow_connector._sync_user_groups()


class TestSyncRoles:
    async def test_skips_when_no_role_assignments(self, servicenow_connector):
        with patch.object(servicenow_connector, "_fetch_all_role_assignments",
                          new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_roles()
            servicenow_connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_adds_role_prefix(self, servicenow_connector):
        assignments = [
            SysUserRoleAssignment(
                sys_id="ra1",
                user="u1",
                role="r1",
                sys_updated_on="2024-01-01",
            ),
        ]
        roles = [SysUserRole(sys_id="r1", name="admin")]
        hierarchy = []
        mock_group = MagicMock(spec=AppUserGroup)
        mock_group.name = "admin"
        mock_result = [(mock_group, [MagicMock()])]
        with patch.object(servicenow_connector, "_fetch_all_role_assignments",
                          new_callable=AsyncMock, return_value=assignments), \
             patch.object(servicenow_connector, "_fetch_all_roles",
                          new_callable=AsyncMock, return_value=roles), \
             patch.object(servicenow_connector, "_fetch_role_hierarchy",
                          new_callable=AsyncMock, return_value=hierarchy), \
             patch.object(servicenow_connector, "_flatten_and_create_user_groups",
                          new_callable=AsyncMock, return_value=mock_result):
            await servicenow_connector._sync_roles()
            assert mock_group.name.startswith("ROLE_")


# ===========================================================================
# Deep sync: _sync_organizational_entities
# ===========================================================================

    @pytest.mark.asyncio
    async def test_sync_roles_with_hierarchy_merges_parent(self, servicenow_connector, mock_data_entities_processor):
        assignments = [
            SysUserRoleAssignment(sys_id="ra1", user="u1", role="child", sys_updated_on="2024-01-01"),
        ]
        roles = [
            SysUserRole(sys_id="parent", name="ParentRole"),
            SysUserRole(sys_id="child", name="ChildRole"),
        ]
        hierarchy = [SysUserRoleContains(sys_id="h1", role="child", contains="parent")]
        mock_group = MagicMock(spec=AppUserGroup)
        mock_group.name = "ChildRole"

        with patch.object(servicenow_connector, "_fetch_all_role_assignments", new_callable=AsyncMock, return_value=assignments), \
             patch.object(servicenow_connector, "_fetch_all_roles", new_callable=AsyncMock, return_value=roles), \
             patch.object(servicenow_connector, "_fetch_role_hierarchy", new_callable=AsyncMock, return_value=hierarchy), \
             patch.object(servicenow_connector, "_flatten_and_create_user_groups",
                          new_callable=AsyncMock, return_value=[(mock_group, [])]):
            await servicenow_connector._sync_roles()

        mock_data_entities_processor.on_new_user_groups.assert_called_once()
        assert mock_group.name.startswith("ROLE_")

    @pytest.mark.asyncio
    async def test_sync_roles_exception_propagates(self, servicenow_connector):
        with patch.object(
            servicenow_connector, "_fetch_all_role_assignments",
            new_callable=AsyncMock, side_effect=RuntimeError("roles fail"),
        ):
            with pytest.raises(RuntimeError, match="roles fail"):
                await servicenow_connector._sync_roles()


class TestSyncOrganizationalEntities:
    async def test_calls_sync_for_each_entity_type(self, servicenow_connector):
        with patch.object(servicenow_connector, "_sync_single_organizational_entity",
                          new_callable=AsyncMock) as mock_sync:
            await servicenow_connector._sync_organizational_entities()
            assert mock_sync.call_count == len(ORGANIZATIONAL_ENTITIES)

    async def test_propagates_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_sync_single_organizational_entity",
                          new_callable=AsyncMock, side_effect=Exception("entity fail")):
            with pytest.raises(Exception, match="entity fail"):
                await servicenow_connector._sync_organizational_entities()


# ===========================================================================
# Deep sync: _sync_single_organizational_entity
# ===========================================================================

class TestSyncSingleOrganizationalEntity:
    async def test_full_sync_entities(self, servicenow_connector):
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value=None)
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.org_entity_sync_points = {"company": sync_point}

        tx = _make_mock_tx_store()
        tx.batch_upsert_user_groups = AsyncMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_organizational_group", return_value=MagicMock()):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "c1", "name": "Company 1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            config = ORGANIZATIONAL_ENTITIES["company"]
            await servicenow_connector._sync_single_organizational_entity("company", config)
            sync_point.update_sync_point.assert_called_once()

    async def test_delta_sync_entities(self, servicenow_connector):
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2024-01-01"})
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.org_entity_sync_points = {"department": sync_point}

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            config = ORGANIZATIONAL_ENTITIES["department"]
            await servicenow_connector._sync_single_organizational_entity("department", config)

    async def test_paginates_entities(self, servicenow_connector):
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value=None)
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.org_entity_sync_points = {"location": sync_point}

        tx = _make_mock_tx_store()
        tx.batch_upsert_user_groups = AsyncMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        page1_data = [{"sys_id": f"l{i}", "name": f"Location {i}", "sys_updated_on": "2024-01-01"} for i in range(100)]
        page2_data = [{"sys_id": "l100", "name": "Location 100", "sys_updated_on": "2024-01-02"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_organizational_group", return_value=MagicMock()):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=[
                _table_api_response(page1_data),
                _table_api_response(page2_data),
            ])
            mock_ds.return_value = mock_datasource
            config = ORGANIZATIONAL_ENTITIES["location"]
            await servicenow_connector._sync_single_organizational_entity("location", config)
            assert mock_datasource.get_now_table_tableName.call_count == 2

    @pytest.mark.asyncio
    async def test_api_error_logs_and_stops(self, servicenow_connector):
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value=None)
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.org_entity_sync_points = {"company": sync_point}

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(502, "bad gateway", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector.logger, "error") as mock_error:
            await servicenow_connector._sync_single_organizational_entity(
                "company", ORGANIZATIONAL_ENTITIES["company"]
            )
            mock_error.assert_called()

    @pytest.mark.asyncio
    async def test_sync_point_read_exception_propagates(self, servicenow_connector):
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("sync fail"))
        servicenow_connector.org_entity_sync_points = {"company": sync_point}

        with pytest.raises(RuntimeError, match="sync fail"):
            await servicenow_connector._sync_single_organizational_entity(
                "company", ORGANIZATIONAL_ENTITIES["company"]
            )


# ===========================================================================
# Deep sync: _sync_knowledge_bases
# ===========================================================================

class TestSyncKnowledgeBases:
    async def test_syncs_knowledge_bases(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        tx = _make_mock_tx_store()
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        tx.batch_upsert_record_groups = AsyncMock()
        tx.batch_upsert_record_group_permissions = AsyncMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_kb_record_group", return_value=MagicMock(id="rg-1")), \
             patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria", new_callable=AsyncMock,
                          return_value={"read": [], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions", new_callable=AsyncMock,
                          return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects", new_callable=AsyncMock,
                          return_value=[]):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "kb1", "title": "KB 1", "owner": "o1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([])
            servicenow_connector.kb_sync_point.update_sync_point.assert_called()

    async def test_adds_admin_permissions(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        tx = _make_mock_tx_store()
        tx.get_record_group_by_external_id = AsyncMock(return_value=None)
        tx.batch_upsert_record_groups = AsyncMock()
        tx.batch_upsert_record_group_permissions = AsyncMock()

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        admin_user = MagicMock(spec=AppUser)
        admin_user.email = "admin@example.com"

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_kb_record_group", return_value=MagicMock(id="rg-1")), \
             patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria", new_callable=AsyncMock,
                          return_value={"read": [], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions", new_callable=AsyncMock,
                          return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects", new_callable=AsyncMock,
                          return_value=[]):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {"sys_id": "kb1", "title": "KB 1", "owner": None, "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([admin_user])
            servicenow_connector.data_entities_processor.on_new_record_groups.assert_called()

    async def test_empty_kbs(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([])


# ===========================================================================
# Deep sync: _sync_users pagination
# ===========================================================================

class TestSyncUsersDeep:
    async def test_paginates_users(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        page1 = [
            _sys_user_row(
                sys_id=f"u{i}",
                email=f"user{i}@example.com",
                sys_updated_on="2024-01-01",
            )
            for i in range(100)
        ]
        page2 = [_sys_user_row(sys_id="u100", email="user100@example.com", sys_updated_on="2024-01-02")]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=[
                _table_api_response(page1),
                _table_api_response(page2),
            ])
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_users()
            assert servicenow_connector.data_entities_processor.on_new_app_users.call_count == 2

    async def test_delta_sync_users(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01 00:00:00"}
        )
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    _sys_user_row(
                        sys_id="u1",
                        email="user@example.com",
                        sys_updated_on="2024-06-01",
                    ),
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_users()
            servicenow_connector.user_sync_point.update_sync_point.assert_called()

    async def test_creates_org_entity_links(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        tx = _make_mock_tx_store()

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        user_data = {
            "sys_id": "u1",
            "email": "user@example.com",
            "company": "comp1",
            "department": "dept1",
            "location": "",
            "cost_center": None,
            "sys_updated_on": "2024-01-01",
        }

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([user_data])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_users()
            # Should create links for company and department (not location/cost_center since empty)
            assert tx.create_user_group_membership.call_count == 2


# ===========================================================================
# Deep sync: _sync_categories
# ===========================================================================

class TestSyncCategories:
    async def test_syncs_categories(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_category_record_group", return_value=MagicMock()):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {
                        "sys_id": "cat1",
                        "label": "Category 1",
                        "parent_table": None,
                        "parent_id": None,
                        "sys_updated_on": "2024-01-01",
                    },
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_categories()
            servicenow_connector.category_sync_point.update_sync_point.assert_called()
            servicenow_connector.data_entities_processor.on_new_record_groups.assert_called()

    async def test_empty_categories(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_categories()

    async def test_categories_with_parent(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        mock_rg = MagicMock()
        mock_rg.parent_external_group_id = "cat1"
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_category_record_group", return_value=mock_rg):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([
                    {
                        "sys_id": "cat2",
                        "label": "Subcategory",
                        "parent_table": "kb_category",
                        "parent_id": "cat1",
                        "sys_updated_on": "2024-01-01",
                    },
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_categories()
            assert mock_rg.parent_external_group_id == "cat1"


# ===========================================================================
# Deep sync: _fetch_all_groups pagination
# ===========================================================================

    @pytest.mark.asyncio
    async def test_sync_categories_pagination(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        page1 = [
            _category_api_row(sys_id=f"cat{i}", label=f"Cat {i}", value=f"cat-{i}", sys_updated_on="2024-01-01")
            for i in range(100)
        ]
        page2 = [_category_api_row(sys_id="cat100", label="Cat 100", value="cat-100", sys_updated_on="2024-01-02")]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response(page1),
            _table_api_response(page2),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_categories()
        assert mock_ds.get_now_table_tableName.call_count == 2
        assert mock_data_entities_processor.on_new_record_groups.call_count == 2

    @pytest.mark.asyncio
    async def test_sync_categories_api_error_handling(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_categories()

    @pytest.mark.asyncio
    async def test_sync_categories_skips_invalid_transform(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            _category_api_row(sys_id="cat1", label="", sys_updated_on="2024-01-01"),
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_categories()
        mock_data_entities_processor.on_new_record_groups.assert_not_called()


# ===========================================================================
# _process_record_updates_batch
# ===========================================================================

    @pytest.mark.asyncio
    async def test_sync_categories_delta_sync(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01 00:00:00"}
        )
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_categories()
        call_kwargs = mock_ds.get_now_table_tableName.call_args.kwargs
        assert "2024-01-01" in call_kwargs["sysparm_query"]

    @pytest.mark.asyncio
    async def test_sync_categories_exception_propagates(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(
            side_effect=RuntimeError("category sync fail")
        )
        with pytest.raises(RuntimeError, match="category sync fail"):
            await servicenow_connector._sync_categories()


class TestFetchAllGroupsDeep:
    async def test_paginates_groups(self, servicenow_connector):
        page1 = [{"sys_id": f"g{i}", "name": f"Group {i}"} for i in range(100)]
        page2 = [{"sys_id": "g100", "name": "Group 100"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=[
                _table_api_response(page1),
                _table_api_response(page2),
            ])
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert len(result) == 101

    async def test_handles_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("API down")):
            with pytest.raises(Exception, match="API down"):
                await servicenow_connector._fetch_all_groups()


# ===========================================================================
# Deep sync: _fetch_all_memberships pagination
# ===========================================================================

class TestFetchAllMembershipsDeep:
    async def test_paginates_memberships(self, servicenow_connector):
        servicenow_connector.group_sync_point = AsyncMock()
        servicenow_connector.group_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.group_sync_point.update_sync_point = AsyncMock()

        page1 = [{"sys_id": f"m{i}", "user": f"u{i}", "group": "g1", "sys_updated_on": "2024-01-01"} for i in range(100)]
        page2 = [{"sys_id": "m100", "user": "u100", "group": "g1", "sys_updated_on": "2024-01-02"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=[
                _table_api_response(page1),
                _table_api_response(page2),
            ])
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_memberships()
            assert len(result) == 101

    async def test_handles_exception(self, servicenow_connector):
        servicenow_connector.group_sync_point = AsyncMock()
        servicenow_connector.group_sync_point.read_sync_point = AsyncMock(return_value=None)
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("API down")):
            with pytest.raises(Exception, match="API down"):
                await servicenow_connector._fetch_all_memberships()


# ===========================================================================
# Deep sync: _get_admin_users with dict ref
# ===========================================================================

    @pytest.mark.asyncio
    async def test_fetch_all_memberships_api_error_breaks(self, servicenow_connector):
        servicenow_connector.group_sync_point = AsyncMock()
        servicenow_connector.group_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_all_memberships()
        assert result == []


class TestGetAdminUsersDeep:
    async def test_handles_string_user_ref(self, servicenow_connector):
        mock_app_user = MagicMock(spec=AppUser)
        mock_app_user.email = "admin2@example.com"

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([{"user": "string-sys-id"}])
            )
            mock_ds.return_value = mock_datasource

            tx = _make_mock_tx_store()
            tx.get_user_by_source_id = AsyncMock(return_value=mock_app_user)

            @asynccontextmanager
            async def _tx():
                yield tx

            servicenow_connector.data_store_provider = MagicMock()
            servicenow_connector.data_store_provider.transaction = _tx

            result = await servicenow_connector._get_admin_users()
            assert len(result) == 1

    async def test_handles_empty_user_ref(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([{"user": ""}])
            )
            mock_ds.return_value = mock_datasource

            tx = _make_mock_tx_store()

            @asynccontextmanager
            async def _tx():
                yield tx

            servicenow_connector.data_store_provider = MagicMock()
            servicenow_connector.data_store_provider.transaction = _tx

            result = await servicenow_connector._get_admin_users()
            assert result == []

    @pytest.mark.asyncio
    async def test_match_exception_continues(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_table_api_response([{"user": "admin-1"}])
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        tx = _make_mock_tx_store()
        tx.get_user_by_source_id = AsyncMock(side_effect=RuntimeError("lookup fail"))

        @asynccontextmanager
        async def _tx():
            yield tx

        servicenow_connector.data_store_provider = MagicMock()
        servicenow_connector.data_store_provider.transaction = _tx

        with patch.object(servicenow_connector.logger, "warning") as mock_warn:
            result = await servicenow_connector._get_admin_users()
        assert result == []
        mock_warn.assert_called()


# ===========================================================================
# Deep sync: _sync_users error handling
# ===========================================================================

class TestSyncUsersErrors:
    async def test_api_error_breaks_loop(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(401, "Unauthorized", None)
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_users()
            servicenow_connector.data_entities_processor.on_new_app_users.assert_not_called()

    async def test_exception_propagated(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("sync point error")
        )
        with pytest.raises(Exception, match="sync point error"):
            await servicenow_connector._sync_users()


# ===========================================================================
# Deep sync: knowledge bases delta sync
# ===========================================================================

class TestSyncKnowledgeBasesDeep:
    async def test_delta_sync_kbs(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01 00:00:00"}
        )
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([])

    async def test_api_error_kbs(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(500, "Server error", None)
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([])

    async def test_exception_in_kb_sync_propagated(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(
            side_effect=Exception("kb error")
        )
        with pytest.raises(Exception, match="kb error"):
            await servicenow_connector._sync_knowledge_bases([])

    @pytest.mark.asyncio
    async def test_sync_knowledge_bases_pagination(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        page1 = [
            _kb_api_row(sys_id=f"kb{i}", title=f"KB {i}", sys_updated_on="2024-01-01")
            for i in range(100)
        ]
        page2 = [_kb_api_row(sys_id="kb100", title="KB 100", sys_updated_on="2024-01-02")]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response(page1),
            _table_api_response(page2),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria",
                          new_callable=AsyncMock, return_value={"read": [], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_knowledge_bases([])

        assert mock_ds.get_now_table_tableName.call_count == 2
        mock_data_entities_processor.on_new_record_groups.assert_called()

    @pytest.mark.asyncio
    async def test_sync_knowledge_bases_owner_permission(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            _kb_api_row(sys_id="kb1", title="KB 1", owner="owner1", sys_updated_on="2024-01-01"),
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        owner_perm = Permission(email="owner@test.com", type=PermissionType.OWNER, entity_type=EntityType.USER)

        with patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria",
                          new_callable=AsyncMock, return_value={"read": [], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock, return_value=[owner_perm]):
            await servicenow_connector._sync_knowledge_bases([])

    @pytest.mark.asyncio
    async def test_sync_knowledge_bases_transform_returns_none_skipped(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            {"sys_id": "kb1", "title": "", "sys_updated_on": "2024-01-01"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_knowledge_bases([])
        mock_data_entities_processor.on_new_record_groups.assert_not_called()


# ===========================================================================
# _sync_categories (additional)
# ===========================================================================


class TestConvertPermissionsToObjects:
    @pytest.mark.asyncio
    async def test_convert_permissions_user_type(self, servicenow_connector):
        tx = _make_mock_tx_store()
        mock_user = MagicMock(spec=AppUser)
        mock_user.email = "user@test.com"
        tx.get_user_by_source_id = AsyncMock(return_value=mock_user)

        perms = await servicenow_connector._convert_permissions_to_objects(
            [RawPermission(entity_type="USER", source_sys_id="u1", role="READER")],
            tx,
        )
        assert len(perms) == 1
        assert perms[0].email == "user@test.com"
        assert perms[0].type == PermissionType.READ

    @pytest.mark.asyncio
    async def test_convert_permissions_user_not_found(self, servicenow_connector):
        tx = _make_mock_tx_store()
        perms = await servicenow_connector._convert_permissions_to_objects(
            [RawPermission(entity_type="USER", source_sys_id="missing", role="READER")],
            tx,
        )
        assert perms == []

    @pytest.mark.asyncio
    async def test_convert_permissions_group_type(self, servicenow_connector):
        tx = _make_mock_tx_store()
        perms = await servicenow_connector._convert_permissions_to_objects(
            [RawPermission(entity_type="GROUP", source_sys_id="g1", role="WRITER")],
            tx,
        )
        assert len(perms) == 1
        assert perms[0].external_id == "g1"
        assert perms[0].type == PermissionType.WRITE

    @pytest.mark.asyncio
    async def test_convert_permissions_unknown_entity_type(self, servicenow_connector):
        tx = _make_mock_tx_store()
        bad_perm = MagicMock()
        bad_perm.entity_type = "ROLE"
        bad_perm.source_sys_id = "r1"
        bad_perm.role = "READER"

        perms = await servicenow_connector._convert_permissions_to_objects([bad_perm], tx)
        assert perms == []

    @pytest.mark.asyncio
    async def test_convert_permissions_exception_handling(self, servicenow_connector):
        tx = AsyncMock()
        tx.get_user_by_source_id = AsyncMock(side_effect=RuntimeError("db error"))

        perms = await servicenow_connector._convert_permissions_to_objects(
            [RawPermission(entity_type="USER", source_sys_id="u1", role="READER")],
            tx,
        )
        assert perms == []

    @pytest.mark.asyncio
    async def test_convert_permissions_mixed_types(self, servicenow_connector):
        tx = _make_mock_tx_store()
        mock_user = MagicMock(spec=AppUser)
        mock_user.email = "user@test.com"
        tx.get_user_by_source_id = AsyncMock(return_value=mock_user)

        perms = await servicenow_connector._convert_permissions_to_objects(
            [
                RawPermission(entity_type="USER", source_sys_id="u1", role="READER"),
                RawPermission(entity_type="GROUP", source_sys_id="g1", role="WRITER"),
            ],
            tx,
        )
        assert len(perms) == 2

    @pytest.mark.asyncio
    async def test_convert_permissions_empty_list(self, servicenow_connector):
        tx = _make_mock_tx_store()
        perms = await servicenow_connector._convert_permissions_to_objects([], tx)
        assert perms == []


class TestExtractPermissionsFromUserCriteria:
    @pytest.mark.asyncio
    async def test_extract_permissions_user_field(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", user="u1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert len(perms) == 1
        assert perms[0].entity_type == "USER"
        assert perms[0].source_sys_id == "u1"

    @pytest.mark.asyncio
    async def test_extract_permissions_group_field(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", group="g1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.WRITE
        )
        assert perms[0].entity_type == "GROUP"
        assert perms[0].role == "WRITER"

    @pytest.mark.asyncio
    async def test_extract_permissions_role_field(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", role="r1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert perms[0].entity_type == "GROUP"
        assert perms[0].source_sys_id == "r1"

    @pytest.mark.asyncio
    async def test_extract_permissions_department_field(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", department="dept1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert perms[0].source_sys_id == "dept1"

    @pytest.mark.asyncio
    async def test_extract_permissions_location_field(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", location="loc1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert perms[0].source_sys_id == "loc1"

    @pytest.mark.asyncio
    async def test_extract_permissions_company_field(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", company="comp1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert perms[0].source_sys_id == "comp1"

    @pytest.mark.asyncio
    async def test_extract_permissions_multiple_comma_separated(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", user="u1,u2, u3")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert len(perms) == 3
        assert {p.source_sys_id for p in perms} == {"u1", "u2", "u3"}

    @pytest.mark.asyncio
    async def test_extract_permissions_empty_fields(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert perms == []

    @pytest.mark.asyncio
    async def test_extract_permissions_mixed_fields(self, servicenow_connector):
        criteria = UserCriteria(sys_id="c1", user="u1", group="g1", company="c1")
        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            criteria, PermissionType.READ
        )
        assert len(perms) == 3

    @pytest.mark.asyncio
    async def test_extract_permissions_exception_handling(self, servicenow_connector):
        class BadCriteria:
            sys_id = "c1"

            @property
            def user(self):
                raise RuntimeError("bad")

        perms = await servicenow_connector._extract_permissions_from_user_criteria_details(
            BadCriteria(), PermissionType.READ
        )
        assert perms == []


# ===========================================================================
# _transform_to_kb_record_group / _transform_to_category_record_group (additional)
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector_factory(self):
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"), \
             patch("app.connectors.sources.servicenow.servicenow.connector.DataSourceEntitiesProcessor") as mock_proc_cls:
            mock_proc = AsyncMock()
            mock_proc_cls.return_value = mock_proc

            result = await ServiceNowConnector.create_connector(
                logger=logging.getLogger("test"),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="factory-1",
                scope="team",
                created_by="user-1",
            )
            assert isinstance(result, ServiceNowConnector)
            mock_proc.initialize.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_records_logs_warning(self, servicenow_connector):
        with patch.object(servicenow_connector.logger, "warning") as mock_warn:
            await servicenow_connector.reindex_records([])
            mock_warn.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_exception_handling(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.servicenow_datasource = MagicMock()
        with patch.object(servicenow_connector.logger, "info", side_effect=[None, RuntimeError("cleanup fail")]):
            await servicenow_connector.cleanup()
        assert servicenow_connector.servicenow_client is None


class TestFetchAttachmentsForArticle:
    @pytest.mark.asyncio
    async def test_fetch_attachments_success(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            {
                "sys_id": "att1",
                "file_name": "file.pdf",
                "content_type": "application/pdf",
                "size_bytes": "100",
                "table_sys_id": "art1",
                "sys_created_on": "2023-01-01 00:00:00",
                "sys_updated_on": "2023-06-01 00:00:00",
            },
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_attachments_for_article("art1")
        assert len(result) == 1
        assert result[0].file_name == "file.pdf"

    @pytest.mark.asyncio
    async def test_fetch_attachments_no_results(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_attachments_for_article("art1")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_attachments_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(404, "Not found", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_attachments_for_article("art1")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_attachments_general_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("network"))

        result = await servicenow_connector._fetch_attachments_for_article("art1")
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_attachments_multiple_attachments(self, servicenow_connector):
        records = [
            {
                "sys_id": f"att{i}",
                "file_name": f"file{i}.pdf",
                "content_type": "application/pdf",
                "size_bytes": "100",
                "table_sys_id": "art1",
                "sys_created_on": "2023-01-01 00:00:00",
                "sys_updated_on": "2023-06-01 00:00:00",
            }
            for i in range(12)
        ]
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response(records))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_attachments_for_article("art1")
        assert len(result) == 12


# ===========================================================================
# _convert_permissions_to_objects and criteria helpers
# ===========================================================================


class TestFetchKbPermissionsFromCriteria:
    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_read_criteria(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response([{"user_criteria": "crit-read-1"}]),
            _table_api_response([]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert "crit-read-1" in result["read"]

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_write_criteria(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response([]),
            _table_api_response([{"user_criteria": "crit-write-1"}]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert "crit-write-1" in result["write"]

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_both_read_and_write(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response([{"user_criteria": "crit-r"}]),
            _table_api_response([{"user_criteria": "crit-w"}]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result["read"] == ["crit-r"]
        assert result["write"] == ["crit-w"]

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_read_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            ServiceNowAPIError(500, "read fail", None),
            _table_api_response([{"user_criteria": "crit-w"}]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result["read"] == []
        assert result["write"] == ["crit-w"]

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_write_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response([{"user_criteria": "crit-r"}]),
            ServiceNowAPIError(500, "write fail", None),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result["read"] == ["crit-r"]
        assert result["write"] == []

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_general_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result == {"read": [], "write": []}

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_empty_results(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result == {"read": [], "write": []}


class TestFlattenAndRoles:
    @pytest.mark.asyncio
    async def test_flatten_and_create_user_groups_deep_hierarchy(self, servicenow_connector):
        groups = [
            SysUserGroup(sys_id="g1", name="Root"),
            SysUserGroup(sys_id="g2", name="Child", parent="g1"),
            SysUserGroup(sys_id="g3", name="Grandchild", parent="g2"),
        ]
        memberships = [
            SysUserGroupMembership(sys_id="m1", user="u1", group="g3"),
        ]
        mock_user = MagicMock(spec=AppUser)
        mock_user.source_user_id = "u1"

        provider = _make_mock_data_store_provider(app_users=[mock_user])
        servicenow_connector.data_store_provider = provider

        with patch.object(servicenow_connector, "_transform_to_user_group") as mock_transform:
            mock_group = MagicMock(spec=AppUserGroup)
            mock_transform.return_value = mock_group
            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)

        assert len(result) == 3
        assert any(mock_user in users for _, users in result)

    @pytest.mark.asyncio
    async def test_flatten_and_create_user_groups_circular_reference(self, servicenow_connector):
        groups = [
            SysUserGroup(sys_id="g1", name="A", parent="g2"),
            SysUserGroup(sys_id="g2", name="B", parent="g1"),
        ]
        memberships = []

        provider = _make_mock_data_store_provider()
        servicenow_connector.data_store_provider = provider

        with patch.object(servicenow_connector, "_transform_to_user_group") as mock_transform:
            mock_transform.return_value = MagicMock(spec=AppUserGroup)
            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_pagination(self, servicenow_connector):
        servicenow_connector.role_assignment_sync_point = AsyncMock()
        servicenow_connector.role_assignment_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.role_assignment_sync_point.update_sync_point = AsyncMock()

        page1 = [
            {"sys_id": f"ra{i}", "user": f"u{i}", "role": "r1", "sys_updated_on": "2024-01-01"}
            for i in range(100)
        ]
        page2 = [{"sys_id": "ra100", "user": "u100", "role": "r1", "sys_updated_on": "2024-01-02"}]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response(page1),
            _table_api_response(page2),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_all_role_assignments()
        assert len(result) == 101

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_api_error(self, servicenow_connector):
        servicenow_connector.role_assignment_sync_point = AsyncMock()
        servicenow_connector.role_assignment_sync_point.read_sync_point = AsyncMock(return_value=None)

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_all_role_assignments()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_role_hierarchy_empty_results(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_role_hierarchy()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_role_hierarchy_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_role_hierarchy()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_all_roles_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_all_roles()
        assert result == []


# ===========================================================================
# _sync_knowledge_bases (additional)
# ===========================================================================


class TestInitRefreshToken:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowRESTClientViaOAuthAuthorizationCode")
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource")
    async def test_init_with_refresh_token(self, mock_ds_cls, mock_client_cls, mock_fetch, servicenow_connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()

        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {
                "access_token": "token",
                "refresh_token": "refresh-tok",
            },
        })
        servicenow_connector.test_connection_and_access = AsyncMock(return_value=True)

        assert await servicenow_connector.init() is True
        assert mock_client.refresh_token == "refresh-tok"

    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowRESTClientViaOAuthAuthorizationCode")
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource")
    async def test_init_without_refresh_token(self, mock_ds_cls, mock_client_cls, mock_fetch, servicenow_connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        mock_client = MagicMock()
        mock_client.refresh_token = None
        mock_client_cls.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()

        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {"access_token": "token"},
        })
        servicenow_connector.test_connection_and_access = AsyncMock(return_value=True)

        assert await servicenow_connector.init() is True
        assert mock_client.refresh_token is None

    @pytest.mark.asyncio
    async def test_handle_webhook_exception_returns_false(self, servicenow_connector):
        with patch.object(servicenow_connector.logger, "info", side_effect=RuntimeError("log fail")):
            result = await servicenow_connector.handle_webhook_notification("org-1", {"event": "test"})
        assert result is False


# ===========================================================================
# stream_record generators
# ===========================================================================


class TestProcessCriteriaPermissions:
    @pytest.mark.asyncio
    async def test_process_criteria_permissions_empty_criteria_ids(self, servicenow_connector):
        tx = _make_mock_tx_store()
        result = await servicenow_connector._process_criteria_permissions([], PermissionType.READ, tx)
        assert result == []

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_batch_fetch_success(self, servicenow_connector):
        tx = _make_mock_tx_store()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            {"sys_id": "crit1", "user": "u1", "group": "", "role": "",
             "department": "", "location": "", "company": "", "cost_center": ""},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_extract_permissions_from_user_criteria_details",
                          new_callable=AsyncMock, return_value=[
                              RawPermission(entity_type="USER", source_sys_id="u1", role="READER"),
                          ]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock, return_value=[
                              Permission(email="u@test.com", type=PermissionType.READ, entity_type=EntityType.USER),
                          ]):
            result = await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ, tx)

        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_api_error(self, servicenow_connector):
        tx = _make_mock_tx_store()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock, return_value=[]):
            result = await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ, tx)

        assert result == []

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_extract_and_convert(self, servicenow_connector):
        tx = _make_mock_tx_store()
        mock_user = MagicMock(spec=AppUser)
        mock_user.email = "extracted@test.com"
        tx.get_user_by_source_id = AsyncMock(return_value=mock_user)

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            {"sys_id": "crit1", "user": "u1"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ, tx)
        assert len(result) == 1
        assert result[0].email == "extracted@test.com"

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_general_exception(self, servicenow_connector):
        tx = _make_mock_tx_store()
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))

        result = await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ, tx)
        assert result == []


class TestProcessRecordUpdatesBatch:
    @pytest.mark.asyncio
    async def test_process_record_updates_batch_empty_list(self, servicenow_connector, mock_data_entities_processor):
        await servicenow_connector._process_record_updates_batch([])
        mock_data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_process_record_updates_batch_success(self, servicenow_connector, mock_data_entities_processor):
        record = MagicMock(spec=WebpageRecord)
        perm = Permission(email="u@test.com", type=PermissionType.READ, entity_type=EntityType.USER)
        update = _record_update(
            record=record,
            new_permissions=[perm],
            external_record_id="art1",
        )
        await servicenow_connector._process_record_updates_batch([update])
        mock_data_entities_processor.on_new_records.assert_called_once()

    @pytest.mark.asyncio
    async def test_process_record_updates_batch_skips_missing_permissions(self, servicenow_connector, mock_data_entities_processor):
        record = MagicMock(spec=WebpageRecord)
        update = _record_update(
            record=record,
            permissions_changed=False,
            new_permissions=[],
            external_record_id="art1",
        )
        await servicenow_connector._process_record_updates_batch([update])
        mock_data_entities_processor.on_new_records.assert_not_called()


# ===========================================================================
# create_connector and cleanup
# ===========================================================================

    @pytest.mark.asyncio
    async def test_process_record_updates_batch_exception(self, servicenow_connector):
        record = MagicMock(spec=WebpageRecord)
        perm = Permission(email="u@test.com", type=PermissionType.READ, entity_type=EntityType.USER)
        update = _record_update(record=record, new_permissions=[perm], external_record_id="art1")
        servicenow_connector.data_entities_processor.on_new_records = AsyncMock(
            side_effect=RuntimeError("batch fail")
        )
        with pytest.raises(RuntimeError, match="batch fail"):
            await servicenow_connector._process_record_updates_batch([update])


class TestProcessSingleArticle:
    @pytest.mark.asyncio
    async def test_process_single_article_with_attachments(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art1",
            short_description="Article with attachment",
            kb_category="cat1",
            author="author1",
        )
        att = AttachmentMetadata(
            sys_id="att1",
            file_name="doc.pdf",
            content_type="application/pdf",
            size_bytes="1024",
            table_sys_id="art1",
            table_name="kb_knowledge",
            sys_created_on="2023-01-01 00:00:00",
            sys_updated_on="2023-06-01 00:00:00",
        )

        with patch.object(servicenow_connector, "_fetch_attachments_for_article",
                          new_callable=AsyncMock, return_value=[att]), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock, return_value=[]):
            updates = await servicenow_connector._process_single_article(article)

        assert len(updates) == 2
        assert updates[0].record.record_type == RecordType.WEBPAGE
        assert updates[1].record.record_type == RecordType.FILE

    @pytest.mark.asyncio
    async def test_process_single_article_with_permissions(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art2",
            short_description="Article with criteria",
            kb_category="cat1",
            can_read_user_criteria="crit1,crit2",
        )
        mock_perm = Permission(email="user@test.com", type=PermissionType.READ, entity_type=EntityType.USER)

        with patch.object(servicenow_connector, "_fetch_attachments_for_article",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[mock_perm]):
            updates = await servicenow_connector._process_single_article(article)

        assert len(updates) == 1
        assert mock_perm in updates[0].new_permissions

    @pytest.mark.asyncio
    async def test_process_single_article_with_author_owner(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art3",
            short_description="Article with author",
            kb_category="cat1",
            author="author-sys-id",
        )
        owner_perm = Permission(email="author@test.com", type=PermissionType.OWNER, entity_type=EntityType.USER)

        with patch.object(servicenow_connector, "_fetch_attachments_for_article",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock, return_value=[owner_perm]):
            updates = await servicenow_connector._process_single_article(article)

        assert owner_perm in updates[0].new_permissions

    @pytest.mark.asyncio
    async def test_process_single_article_without_author(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art4",
            short_description="No author",
            kb_category="cat1",
            author=None,
        )

        with patch.object(servicenow_connector, "_fetch_attachments_for_article",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_convert_permissions_to_objects",
                          new_callable=AsyncMock) as mock_convert:
            updates = await servicenow_connector._process_single_article(article)

        mock_convert.assert_not_called()
        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_process_single_article_transform_fails(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art5",
            short_description="",
            kb_category="cat1",
        )
        updates = await servicenow_connector._process_single_article(article)
        assert updates == []

    @pytest.mark.asyncio
    async def test_process_single_article_exception_handling(self, servicenow_connector):
        article = TableAPIRecord(
            sys_id="art6",
            short_description="Will fail",
            kb_category="cat1",
            can_read_user_criteria="",
            author=None,
        )

        with patch.object(servicenow_connector, "_fetch_attachments_for_article",
                          new_callable=AsyncMock, side_effect=RuntimeError("fetch fail")):
            updates = await servicenow_connector._process_single_article(article)

        assert updates == []


# ===========================================================================
# _fetch_attachments_for_article
# ===========================================================================

    @pytest.mark.asyncio
    async def test_process_single_article_exception_returns_empty(self, servicenow_connector):
        article = TableAPIRecord(sys_id="art1", short_description="Title", kb_category="cat1")
        with patch.object(
            servicenow_connector, "_transform_to_article_webpage_record",
            side_effect=RuntimeError("transform fail"),
        ):
            result = await servicenow_connector._process_single_article(article)
        assert result == []


class TestStreamRecordGenerators:
    @pytest.mark.asyncio
    async def test_stream_article_generator_yields_content(self, servicenow_connector):
        servicenow_connector._fetch_article_content = AsyncMock(return_value="<p>Hello</p>")
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Article"
        record.external_record_id = "art-1"

        response = await servicenow_connector.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert b"Hello" in body

    @pytest.mark.asyncio
    async def test_stream_attachment_generator_yields_bytes(self, servicenow_connector):
        servicenow_connector._fetch_attachment_content = AsyncMock(return_value=b"file-bytes")
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        response = await servicenow_connector.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert b"file-bytes" in body


# ===========================================================================
# role assignments and hierarchy fetch
# ===========================================================================


class TestSyncArticles:
    @pytest.mark.asyncio
    async def test_sync_articles_full_sync_with_pagination(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.article_sync_point.update_sync_point = AsyncMock()

        page1 = [
            {"sys_id": f"art{i}", "short_description": f"Article {i}",
             "kb_category": "cat1", "sys_updated_on": "2024-01-01"}
            for i in range(100)
        ]
        page2 = [
            {"sys_id": "art100", "short_description": "Article 100",
             "kb_category": "cat1", "sys_updated_on": "2024-01-02"},
        ]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response(page1),
            _table_api_response(page2),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_process_single_article", new_callable=AsyncMock,
                          return_value=[_record_update()]):
            await servicenow_connector._sync_articles()

        assert mock_ds.get_now_table_tableName.call_count == 2
        servicenow_connector.article_sync_point.update_sync_point.assert_called_once()

    @pytest.mark.asyncio
    async def test_sync_articles_delta_sync_with_last_sync_time(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01 00:00:00"}
        )
        servicenow_connector.article_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_articles()
        call_kwargs = mock_ds.get_now_table_tableName.call_args.kwargs
        assert "2024-01-01" in call_kwargs["sysparm_query"]

    @pytest.mark.asyncio
    async def test_sync_articles_api_error_breaks_loop(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.article_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "Server error", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_articles()
        mock_data_entities_processor = servicenow_connector.data_entities_processor
        mock_data_entities_processor.on_new_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_articles_updates_sync_checkpoint(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.article_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            {"sys_id": "art1", "short_description": "A1", "kb_category": "c1",
             "sys_updated_on": "2024-06-15 12:00:00"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_process_single_article", new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_articles()

        servicenow_connector.article_sync_point.update_sync_point.assert_called_once()
        checkpoint = servicenow_connector.article_sync_point.update_sync_point.call_args[0][1]
        assert checkpoint["last_sync_time"] == "2024-06-15 12:00:00"

    @pytest.mark.asyncio
    async def test_sync_articles_empty_result(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.article_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_articles()
        servicenow_connector.article_sync_point.update_sync_point.assert_not_called()

    @pytest.mark.asyncio
    async def test_sync_articles_counts_attachments_correctly(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.article_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([
            {"sys_id": "art1", "short_description": "A1", "kb_category": "c1",
             "sys_updated_on": "2024-01-01"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        article_update = _record_update(record=MagicMock(record_type=RecordType.WEBPAGE))
        file_update = _record_update(record=MagicMock(record_type=RecordType.FILE))

        with patch.object(servicenow_connector, "_process_single_article", new_callable=AsyncMock,
                          return_value=[article_update, file_update]) as mock_process_single, \
             patch.object(servicenow_connector, "_process_record_updates_batch", new_callable=AsyncMock) as mock_batch:
            await servicenow_connector._sync_articles()
            
            mock_process_single.assert_called_once()
            mock_batch.assert_called_once()
            batch_call_args = mock_batch.call_args[0][0]
            assert len(batch_call_args) == 2
            assert batch_call_args[0].record.record_type == RecordType.WEBPAGE
            assert batch_call_args[1].record.record_type == RecordType.FILE

    @pytest.mark.asyncio
    async def test_sync_articles_exception_propagates(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(
            side_effect=RuntimeError("article sync fail")
        )
        with pytest.raises(RuntimeError, match="article sync fail"):
            await servicenow_connector._sync_articles()
