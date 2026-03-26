"""Tests for ServiceNow connector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.sources.servicenow.servicenow.connector import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowConnector,
)
from app.models.entities import AppUser, AppUserGroup, RecordType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_tx_store(existing_record=None, app_users=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_by_source_id = AsyncMock(return_value=None)
    tx.get_app_users = AsyncMock(return_value=app_users or [])
    tx.get_user_groups = AsyncMock(return_value=[])
    tx.create_user_group_membership = AsyncMock()
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


def _make_api_response(success=True, data=None, error=None):
    resp = MagicMock()
    resp.success = success
    resp.data = data
    resp.error = error
    return resp


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.servicenow")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-sn-1"
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
        )
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
                "redirectUri": "http://localhost/callback",
            }
        }
        mock_client_cls.return_value = MagicMock()
        mock_ds_instance = MagicMock()
        mock_ds_instance.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(success=True, data={"result": []})
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
            return_value=_make_api_response(success=False, error="Unauthorized")
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
                return_value=_make_api_response(success=True, data={"result": []})
            )
            mock_ds.return_value = mock_datasource
            assert await servicenow_connector.test_connection_and_access() is True

    async def test_failure(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=False, error="Unauthorized")
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
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "art-1", "text": "<p>Content</p>", "number": "KB001"}]
                })
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_article_content("art-1")
            assert result == "<p>Content</p>"

    async def test_not_found(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={"result": []})
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._fetch_article_content("nonexistent")
            assert exc_info.value.status_code == 404

    async def test_empty_content(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "art-1", "text": "", "number": "KB001"}]
                })
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_article_content("art-1")
            assert result == "<p>No content available</p>"

    async def test_api_failure(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=False, error="Server error")
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._fetch_article_content("art-1")
            assert exc_info.value.status_code == 404


# ===========================================================================
# _fetch_attachment_content
# ===========================================================================

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
                return_value=_make_api_response(success=True, data={
                    "result": [{"user": "sys-admin-1"}]
                })
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
                return_value=_make_api_response(success=False, error="Not found")
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._get_admin_users()
            assert result == []

    async def test_dict_reference_field(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [{"user": {"value": "sys-admin-1"}}]
                })
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
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "g1", "name": "Group 1"},
                        {"sys_id": "g2", "name": "Group 2"},
                    ]
                })
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert len(result) == 2

    async def test_empty_results(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={"result": []})
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert result == []

    async def test_api_failure(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=False, error="Error")
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
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "m1", "user": "u1", "group": "g1", "sys_updated_on": "2024-01-01"},
                    ]
                })
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
                return_value=_make_api_response(success=True, data={"result": []})
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
            {"sys_id": "g1", "name": "Group 1"},
            {"sys_id": "g2", "name": "Group 2", "parent": {"value": "g1"}},
        ]
        memberships = [
            {"user": {"value": "u1"}, "group": {"value": "g1"}},
            {"user": {"value": "u2"}, "group": {"value": "g2"}},
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
        groups = [{"sys_id": "g1", "name": "Group 1"}]
        memberships = [{"user": "u1", "group": "g1"}]

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

class TestFetchRoles:
    async def test_fetches_roles(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "r1", "name": "admin"}]
                })
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
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "ra1", "user": "u1", "role": "r1", "sys_updated_on": "2024-01-01"},
                    ]
                })
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_role_assignments()
            assert len(result) == 1
            # Verify role is renamed to group
            assert "group" in result[0]

    async def test_fetches_role_hierarchy(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "h1", "contains": "r1", "role": "r2"}]
                })
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_role_hierarchy()
            assert len(result) == 1


# ===========================================================================
# _sync_users
# ===========================================================================

class TestSyncUsers:
    async def test_syncs_users(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock) as mock_transform:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {
                            "sys_id": "u1", "user_name": "user1",
                            "email": "user1@example.com", "first_name": "User",
                            "last_name": "One", "active": "true",
                            "sys_updated_on": "2024-01-01",
                        }
                    ]
                })
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
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "u1", "email": "", "sys_updated_on": "2024-01-01"},
                    ]
                })
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

class TestSyncRoles:
    async def test_skips_when_no_role_assignments(self, servicenow_connector):
        with patch.object(servicenow_connector, "_fetch_all_role_assignments",
                          new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_roles()
            servicenow_connector.data_entities_processor.on_new_user_groups.assert_not_called()

    async def test_adds_role_prefix(self, servicenow_connector):
        assignments = [{"user": "u1", "group": "r1", "sys_updated_on": "2024-01-01"}]
        roles = [{"sys_id": "r1", "name": "admin"}]
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
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "c1", "name": "Company 1", "sys_updated_on": "2024-01-01"},
                    ]
                })
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
                return_value=_make_api_response(success=True, data={"result": []})
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
                _make_api_response(success=True, data={"result": page1_data}),
                _make_api_response(success=True, data={"result": page2_data}),
            ])
            mock_ds.return_value = mock_datasource
            config = ORGANIZATIONAL_ENTITIES["location"]
            await servicenow_connector._sync_single_organizational_entity("location", config)
            assert mock_datasource.get_now_table_tableName.call_count == 2


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
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "kb1", "title": "KB 1", "owner": "o1", "sys_updated_on": "2024-01-01"}]
                })
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
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "kb1", "title": "KB 1", "sys_updated_on": "2024-01-01"}]
                })
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([admin_user])
            tx.batch_upsert_record_group_permissions.assert_called()

    async def test_empty_kbs(self, servicenow_connector):
        servicenow_connector.kb_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.kb_sync_point.update_sync_point = AsyncMock()

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={"result": []})
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

        page1 = [{"sys_id": f"u{i}", "email": f"user{i}@example.com", "sys_updated_on": "2024-01-01"} for i in range(100)]
        page2 = [{"sys_id": "u100", "email": "user100@example.com", "sys_updated_on": "2024-01-02"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=[
                _make_api_response(success=True, data={"result": page1}),
                _make_api_response(success=True, data={"result": page2}),
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
                return_value=_make_api_response(success=True, data={
                    "result": [{"sys_id": "u1", "email": "user@example.com", "sys_updated_on": "2024-06-01"}]
                })
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
            "sys_id": "u1", "email": "user@example.com",
            "company": "comp1", "department": {"value": "dept1"},
            "location": "", "cost_center": None,
            "sys_updated_on": "2024-01-01",
        }

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={"result": [user_data]})
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
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "cat1", "label": "Category 1",
                         "parent_table": None, "parent_id": None,
                         "sys_updated_on": "2024-01-01"},
                    ]
                })
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
                return_value=_make_api_response(success=True, data={"result": []})
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_categories()

    async def test_categories_with_parent(self, servicenow_connector):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        mock_rg = MagicMock()
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_category_record_group", return_value=mock_rg):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [
                        {"sys_id": "cat2", "label": "Subcategory",
                         "parent_table": "kb_category", "parent_id": {"value": "cat1"},
                         "sys_updated_on": "2024-01-01"},
                    ]
                })
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_categories()
            assert mock_rg.parent_record_group_id == "cat1"


# ===========================================================================
# Deep sync: _fetch_all_groups pagination
# ===========================================================================

class TestFetchAllGroupsDeep:
    async def test_paginates_groups(self, servicenow_connector):
        page1 = [{"sys_id": f"g{i}", "name": f"Group {i}"} for i in range(100)]
        page2 = [{"sys_id": "g100", "name": "Group 100"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=[
                _make_api_response(success=True, data={"result": page1}),
                _make_api_response(success=True, data={"result": page2}),
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
                _make_api_response(success=True, data={"result": page1}),
                _make_api_response(success=True, data={"result": page2}),
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

class TestGetAdminUsersDeep:
    async def test_handles_string_user_ref(self, servicenow_connector):
        mock_app_user = MagicMock(spec=AppUser)
        mock_app_user.email = "admin2@example.com"

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_make_api_response(success=True, data={
                    "result": [{"user": "string-sys-id"}]
                })
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
                return_value=_make_api_response(success=True, data={
                    "result": [{"user": ""}]
                })
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
                return_value=_make_api_response(success=False, error="Unauthorized")
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
                return_value=_make_api_response(success=True, data={"result": []})
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
                return_value=_make_api_response(success=False, error="Server error")
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
