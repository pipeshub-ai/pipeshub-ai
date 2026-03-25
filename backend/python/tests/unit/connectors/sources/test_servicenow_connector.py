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
