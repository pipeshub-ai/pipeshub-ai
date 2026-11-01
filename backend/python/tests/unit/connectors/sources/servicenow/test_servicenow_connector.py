"""Tests for ServiceNow connector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from servicenow_fakes import (
    block_groups,
    file_response,
    keyset_page,
    make_tx_store,
    migrated_sync_point,
)

from app.config.constants.arangodb import AccessRule, MimeTypes
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.servicenow.servicenow.connector import (
    ServiceNowConnector,
    _CriteriaUnreadable,
)
from app.connectors.sources.servicenow.servicenow.constants import (
    ORGANIZATIONAL_ENTITIES,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    RecordGroupType,
    RecordType,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.sources.client.servicenow.servicenow import (
    ServiceNowRESTClientViaOAuthAuthorizationCode,
)
from app.sources.external.servicenow.models import (
    AttachmentMetadata,
    KBKnowledge,
    ServiceNowAPIError,
    SysUserGroup,
    SysUserGroupMembership,
    SysUserRole,
    SysUserRoleAssignment,
    SysUserRoleContains,
    TableAPIRecord,
    TableAPIResponse,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _table_api_response(records: list, total: int | None = None) -> TableAPIResponse:
    """Match ServiceNowDataSource.get_now_table_tableName return type."""
    # total: X-Total-Count, the rows the query matches from here on (default: these rows)
    return TableAPIResponse(result=[TableAPIRecord(**r) for r in records],
                            total_count=len(records) if total is None else total)

def _listing(*pages: list) -> list[TableAPIResponse]:
    """Responses of one Table API listing: these pages, each with the X-Total-Count of the rows
    left from it on, so the last one ends the listing (one empty page if there are none)."""
    return [_table_api_response(p, sum(map(len, pages[i:]))) for i, p in enumerate(pages)] or [_table_api_response([])]




def _category_listing(*pages: list) -> list[TableAPIResponse]:
    """The responses of _sync_categories: its listing of every category id (one page), then ``pages``."""
    return [_table_api_response([r for p in pages for r in p]), *_listing(*pages)]


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

    def test_no_cost_center_org_groups_synced(self):
        # user_criteria has no cost_center field, so a cost-centre group could never grant.
        assert "cost_center" not in ORGANIZATIONAL_ENTITIES

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
        assert servicenow_connector.category_sync_point is not None
        assert servicenow_connector.article_sync_point is not None

    def test_no_sync_point_for_groups_roles_and_knowledge_bases(self, servicenow_connector):
        """These three reads must stay full, so none may carry a checkpoint."""
        assert not hasattr(servicenow_connector, "group_sync_point")
        assert not hasattr(servicenow_connector, "role_assignment_sync_point")
        assert not hasattr(servicenow_connector, "kb_sync_point")

    def test_org_entity_sync_points(self, servicenow_connector):
        for key in ["company", "department", "location"]:
            assert key in servicenow_connector.org_entity_sync_points

    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
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
        with pytest.raises(ConnectorInitError, match="no saved settings"):
            await servicenow_connector.init()

    async def test_init_fails_no_oauth_config_id(self, servicenow_connector):
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
            "credentials": {},
        })
        with pytest.raises(ConnectorInitError, match="not linked to an OAuth app"):
            await servicenow_connector.init()

    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_oauth_not_found(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = None
        with pytest.raises(ConnectorInitError, match="OAuth app this connector is linked to was not found"):
            await servicenow_connector.init()

    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_incomplete_config(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = {"config": {"clientId": "id"}}
        with pytest.raises(ConnectorInitError, match="missing the instance URL"):
            await servicenow_connector.init()

    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_fails_no_access_token(self, mock_fetch_oauth, servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "id", "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-sn-1"},
            "credentials": {},
        })
        with pytest.raises(ConnectorInitError, match="not authorized yet"):
            await servicenow_connector.init()

    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowRESTClientViaOAuthAuthorizationCode")
    @patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource")
    async def test_init_fails_connection_test(self, mock_ds_cls, mock_client_cls, mock_fetch_oauth,
                                              servicenow_connector):
        mock_fetch_oauth.return_value = {
            "config": {
                "clientId": "id", "clientSecret": "secret",
                "instanceUrl": "https://sn.example.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        mock_client_cls.return_value = MagicMock()
        mock_ds_instance = MagicMock()
        mock_ds_instance.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(401, "Unauthorized", None)
        )
        mock_ds_cls.return_value = mock_ds_instance
        with pytest.raises(ConnectorInitError, match=r"refused the connector's sign-in \(HTTP 401: Unauthorized\)"):
            await servicenow_connector.init()


# ===========================================================================
# _get_fresh_datasource
# ===========================================================================

class TestGetFreshDatasource:
    async def test_raises_when_client_not_initialized(self, servicenow_connector):
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409

    async def test_returns_datasource_with_fresh_token(self, servicenow_connector):
        """A real client, because the transport sends headers, not the attribute."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://example.service-now.com",
            client_id="cid", client_secret="secret",
            redirect_uri="http://localhost/cb", access_token="old-token",
        )
        servicenow_connector.servicenow_client = client
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "new-token"},
        })
        ds = await servicenow_connector._get_fresh_datasource()
        assert ds is not None
        assert client.access_token == "new-token"
        assert client.headers["Authorization"] == "Bearer new-token"

    async def test_no_config_raises(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409

    async def test_no_token_raises(self, servicenow_connector):
        """A missing access token is the reconnect case, not an opaque 500."""
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {},
        })
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409

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
                side_effect=ServiceNowAPIError(503, "Unavailable", None)
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(ConnectorInitError, match="answered HTTP 503: Unavailable"):
                await servicenow_connector.test_connection_and_access()

    async def test_exception(self, servicenow_connector):
        # Not a ServiceNow or network error: the caller reports it as a failure of its own.
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("Connection refused")), \
                pytest.raises(Exception, match="Connection refused"):
            await servicenow_connector.test_connection_and_access()


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
                          new_callable=AsyncMock, return_value=b'{"block_groups": []}'):
            response = await servicenow_connector.stream_record(record)
            assert response.media_type == MimeTypes.BLOCKS.value

    async def test_stream_attachment(self, servicenow_connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.id = "rec-1"
        record.mime_type = "application/pdf"

        async def chunks():
            yield b"PDF content"

        servicenow_connector._current_attachment = AsyncMock(return_value="sys-att-1")
        with patch.object(servicenow_connector, "_open_attachment",
                          new_callable=AsyncMock, return_value=chunks()) as opened:
            response = await servicenow_connector.stream_record(record)
            assert response.media_type == "application/pdf"
        assert opened.await_args.args == ("sys-att-1",)

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
            assert block_groups(result)[0]["data"] == "# KB001\n\nKB001\n\nContent"

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
            # Only the title and number: a placeholder phrase would be indexed and cited.
            assert block_groups(result)[0]["data"] == "# KB001\n\nKB001"

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
            mock_datasource.stream_attachment = AsyncMock(return_value=file_response(b"file content"))
            mock_ds.return_value = mock_datasource
            chunks = await servicenow_connector._open_attachment("att-1")
            assert b"".join([c async for c in chunks]) == b"file content"

    async def test_not_found(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.stream_attachment = AsyncMock(side_effect=ServiceNowAPIError(404, "No Record found", None))
            mock_ds.return_value = mock_datasource
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector._open_attachment("nonexistent")
            assert exc_info.value.status_code == 404

    async def test_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.stream_attachment = AsyncMock(side_effect=Exception("Download failed"))
            mock_ds.return_value = mock_datasource
            record = MagicMock(record_type=RecordType.FILE, external_record_id="att-1", mime_type="application/pdf")
            with pytest.raises(HTTPException) as exc_info:
                await servicenow_connector.stream_record(record)
            assert exc_info.value.status_code == 500


# ===========================================================================
# get_signed_url, handle_webhook, cleanup, reindex, get_filter_options
# ===========================================================================

    @pytest.mark.asyncio
    async def test_fetch_attachment_content_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.stream_attachment = AsyncMock(
            side_effect=ServiceNowAPIError(503, "unavailable", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._open_attachment("att-1")
        # ServiceNow's raw status is mapped, never forwarded: a source 401 must not
        # reach the frontend as a 401 and log the user out of PipesHub.
        assert exc_info.value.status_code == 502

    async def test_fetch_attachment_content_401_maps_to_reconnect(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.stream_attachment = AsyncMock(
            side_effect=ServiceNowAPIError(401, "unauthorized", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await servicenow_connector._open_attachment("att-1")
        assert exc_info.value.status_code == 409


class TestMiscMethods:
    def test_get_signed_url_returns_none(self, servicenow_connector):
        assert servicenow_connector.get_signed_url(MagicMock()) is None

    async def test_cleanup(self, servicenow_connector):
        client = servicenow_connector.servicenow_client = MagicMock(close=AsyncMock())
        servicenow_connector.servicenow_datasource = MagicMock()
        await servicenow_connector.cleanup()
        client.close.assert_awaited_once()  # the connection pool is closed, not just dropped
        assert servicenow_connector.servicenow_client is None
        assert servicenow_connector.servicenow_datasource is None

    async def test_reindex_records(self, servicenow_connector):
        record = MagicMock()
        await servicenow_connector.reindex_records([record])
        servicenow_connector.data_entities_processor.reindex_existing_records.assert_awaited_once_with([record])

    async def test_get_filter_options_unknown_key_raises(self, servicenow_connector):
        with pytest.raises(ValueError, match="Unsupported filter key"):
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
                side_effect=_listing([{"user": "sys-admin-1"}])
            )
            mock_ds.return_value = mock_datasource

            servicenow_connector.data_entities_processor.get_user_by_source_id = AsyncMock(return_value=mock_app_user)

            result = await servicenow_connector._get_admin_users()
            assert result == {"sys-admin-1"}
            servicenow_connector.data_entities_processor.get_user_by_source_id.assert_not_awaited()

    async def test_failed_admin_read_raises(self, servicenow_connector) -> None:
        # An empty list would be written as "no admins" and remove their grants.
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(500, "fail", None)
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(ServiceNowAPIError):
                await servicenow_connector._get_admin_users()

    async def test_dict_reference_field(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([{"user": "sys-admin-1"}])
            )
            mock_ds.return_value = mock_datasource

            tx = make_tx_store()
            tx.get_user_by_source_id = AsyncMock(return_value=None)

            @asynccontextmanager
            async def _tx():
                yield tx

            servicenow_connector.data_store_provider = MagicMock()
            servicenow_connector.data_store_provider.transaction = _tx

            result = await servicenow_connector._get_admin_users()
            assert result == {"sys-admin-1"}  # granted only if synced (_users_to_permissions)

    async def test_exception_raises(self, servicenow_connector) -> None:
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("Network error")), pytest.raises(Exception, match="Network error"):
            await servicenow_connector._get_admin_users()


# ===========================================================================
# _fetch_all_groups
# ===========================================================================

class TestFetchAllGroups:
    async def test_fetches_groups(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
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

    async def test_api_failure_propagates(self, servicenow_connector):
        """A partial group list would make the caller delete edges it cannot rebuild."""
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(500, "Error", None)
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(ServiceNowAPIError):
                await servicenow_connector._fetch_all_groups()


# ===========================================================================
# _fetch_all_memberships
# ===========================================================================

class TestFetchAllMemberships:
    @pytest.mark.asyncio
    async def test_membership_query_carries_no_delta_even_with_a_sync_point(self, servicenow_connector):
        """on_new_user_groups replaces every edge, so a delta read loses members."""
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2026-08-26 09:00:00"})
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.group_sync_point = sync_point

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._fetch_all_memberships()

        query = mock_ds.get_now_table_tableName.call_args.kwargs["sysparm_query"]
        assert "sys_updated_on>" not in query

    async def test_fetches_memberships(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
                    {"sys_id": "m1", "user": "u1", "group": "g1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_memberships()
            assert len(result) == 1

    async def test_reads_every_row(self, servicenow_connector):
        """A delta read plus a replace write removes members that nobody removed."""
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_memberships()
            assert result == []
            call_kwargs = mock_datasource.get_now_table_tableName.call_args.kwargs
            assert "sys_updated_on>" not in call_kwargs["sysparm_query"]


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

        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[mock_user1, mock_user2])

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

        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

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
        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        with patch.object(servicenow_connector, "_transform_to_user_group", return_value=None):
            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)
        assert result == []

    @pytest.mark.asyncio
    async def test_flatten_exception_propagates(self, servicenow_connector):
        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("tx fail"))
        with pytest.raises(RuntimeError, match="tx fail"):
            await servicenow_connector._flatten_and_create_user_groups([], [])


class TestFetchRoles:
    async def test_fetches_roles(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([{"sys_id": "r1", "name": "admin"}])
            )
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_roles()
            assert len(result) == 1

    async def test_fetches_role_assignments(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
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
                side_effect=_listing([
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
    async def test_fetch_all_role_assignments_reads_every_row(self, servicenow_connector):
        """A delta read plus a replace write removes roles that nobody revoked."""
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._fetch_all_role_assignments()
        call_kwargs = mock_ds.get_now_table_tableName.call_args.kwargs
        assert "sys_updated_on>" not in call_kwargs["sysparm_query"]

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_outer_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(
            side_effect=RuntimeError("datasource fail")
        )
        with pytest.raises(RuntimeError, match="datasource fail"):
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
        servicenow_connector._users_gone = AsyncMock(return_value=set())  # the reconcile listing has tests of its own

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock) as mock_transform:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
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
        servicenow_connector._users_gone = AsyncMock(return_value=set())  # the reconcile listing has tests of its own

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
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
        with patch.object(servicenow_connector, "_read_access_state", new_callable=AsyncMock, return_value=(None, True)), \
             patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_get_admin_users", new_callable=AsyncMock, return_value=set()), \
             patch.object(servicenow_connector, "_sync_knowledge_bases", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_sync_categories", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_sync_articles", new_callable=AsyncMock):
            await servicenow_connector.run_sync()

    async def test_sync_continues_without_admin_users(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        with patch.object(servicenow_connector, "_read_access_state", new_callable=AsyncMock, return_value=(None, True)), \
             patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_get_admin_users", new_callable=AsyncMock, return_value=set()), \
             patch.object(servicenow_connector, "_sync_knowledge_bases", new_callable=AsyncMock) as mock_kb, \
             patch.object(servicenow_connector, "_sync_categories", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_sync_articles", new_callable=AsyncMock):
            await servicenow_connector.run_sync()
            mock_kb.assert_called_once_with(set(), grants=True)

    async def test_a_failed_admin_read_writes_no_kb_so_stored_admin_grants_stay(self, servicenow_connector) -> None:
        servicenow_connector.servicenow_client = MagicMock()
        with patch.object(servicenow_connector, "_read_access_state", new_callable=AsyncMock, return_value=(None, True)), \
             patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock), \
             patch.object(servicenow_connector, "_get_admin_users", new_callable=AsyncMock,
                          side_effect=ServiceNowAPIError(500, "fail", None)), \
             patch.object(servicenow_connector, "_sync_knowledge_bases", new_callable=AsyncMock) as mock_kb, \
             patch.object(servicenow_connector, "_sync_articles", new_callable=AsyncMock) as mock_articles:
            with pytest.raises(ServiceNowAPIError):
                await servicenow_connector.run_sync()
        assert not mock_kb.await_count and not mock_articles.await_count, (
            f"KBs written {mock_kb.await_count}x, articles {mock_articles.await_count}x after a failed admin read"
        )

    async def test_sync_propagates_exceptions(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        with patch.object(servicenow_connector, "_read_access_state", new_callable=AsyncMock, return_value=(None, True)), \
             patch.object(servicenow_connector, "_sync_users_and_groups", new_callable=AsyncMock,
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
             patch.object(servicenow_connector, "_sync_roles", new_callable=AsyncMock,
                          return_value=(set(), MagicMock())) as mock_r, \
             patch.object(servicenow_connector, "_reconcile_user_groups", new_callable=AsyncMock) as mock_rc:
            await servicenow_connector._sync_users_and_groups()
            mock_oe.assert_called_once()
            mock_u.assert_called_once()
            mock_g.assert_called_once()
            mock_r.assert_called_once()
            mock_rc.assert_called_once()

    async def test_propagates_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_sync_organizational_entities",
                          new_callable=AsyncMock, side_effect=Exception("org fail")):
            with pytest.raises(Exception, match="org fail"):
                await servicenow_connector._sync_users_and_groups()


# ===========================================================================
# Deep sync: _sync_user_groups
# ===========================================================================

class TestSyncUserGroups:
    async def test_group_with_no_members_is_written_empty(self, servicenow_connector):
        # Its last members left: written with none, so they lose it (an early return kept them).
        with patch.object(servicenow_connector, "_fetch_all_memberships", new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_fetch_all_groups", new_callable=AsyncMock,
                          return_value=[SysUserGroup(sys_id="g1", name="Group 1")]):
            await servicenow_connector._sync_user_groups()
        (written,) = servicenow_connector.data_entities_processor.on_new_user_groups.await_args.args
        assert [(g.source_user_group_id, members) for g, members in written] == [("g1", [])]

    async def test_processes_groups_and_memberships(self, servicenow_connector):
        memberships = [SysUserGroupMembership(sys_id="m1", user="u1", group="g1")]
        groups = [SysUserGroup(sys_id="g1", name="Group 1")]
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
    async def test_role_with_no_assignments_is_written_empty(self, servicenow_connector):
        with patch.object(servicenow_connector, "_fetch_all_role_assignments", new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_fetch_all_roles", new_callable=AsyncMock,
                          return_value=[SysUserRole(sys_id="r1", name="itil")]), \
             patch.object(servicenow_connector, "_fetch_role_hierarchy", new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_roles()
        (written,) = servicenow_connector.data_entities_processor.on_new_user_groups.await_args.args
        assert [(g.name, members) for g, members in written] == [("ROLE_itil", [])]

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
        mock_group.source_user_group_id = "r1"
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
        mock_group.source_user_group_id = "child"

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

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_organizational_group", return_value=MagicMock()):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
                    {"sys_id": "c1", "name": "Company 1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            config = ORGANIZATIONAL_ENTITIES["company"]
            await servicenow_connector._sync_single_organizational_entity("company", config)
            sync_point.update_sync_point.assert_called_once()

    async def test_delta_sync_entities(self, servicenow_connector):
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2024-01-01 00:00:00"})
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

        page1_data = [{"sys_id": f"l{i}", "name": f"Location {i}", "sys_updated_on": "2024-01-01"} for i in range(1000)]
        page2_data = [{"sys_id": "l1000", "name": "Location 100", "sys_updated_on": "2024-01-02"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_organizational_group", return_value=MagicMock()):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=_listing(page1_data, page2_data))
            mock_ds.return_value = mock_datasource
            config = ORGANIZATIONAL_ENTITIES["location"]
            await servicenow_connector._sync_single_organizational_entity("location", config)
            # X-Total-Count ends the listing, so two pages take two calls
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

        with patch.object(servicenow_connector.logger, "error") as mock_error, \
             pytest.raises(ServiceNowAPIError):
            await servicenow_connector._sync_single_organizational_entity(
                "company", ORGANIZATIONAL_ENTITIES["company"]
            )
        mock_error.assert_not_called()  # logged once, with its traceback, where the run ends (_run_sync)
        # A page that failed must not leave a checkpoint claiming it was read.
        sync_point.update_sync_point.assert_not_awaited()

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
    @pytest.fixture(autouse=True)
    def _criteria_read_per_kb(self, servicenow_connector) -> None:
        # These tests script the datasource call by call: each KB reads its own criteria.
        servicenow_connector._load_kb_criteria = AsyncMock()

    @pytest.mark.asyncio
    async def test_knowledge_base_query_carries_no_delta_even_with_a_sync_point(self, servicenow_connector):
        """Read grants live in kb_uc_can_read_mtom, which never bumps the base row."""
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": "2026-08-25 07:30:06"})
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.kb_sync_point = sync_point

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_knowledge_bases([])

        query = mock_ds.get_now_table_tableName.call_args.kwargs["sysparm_query"]
        assert "sys_updated_on>" not in query

    async def test_syncs_knowledge_bases(self, servicenow_connector):
        tx = make_tx_store()
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
                          return_value=[]):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
                    {"sys_id": "kb1", "title": "KB 1", "owner": "o1", "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([])
            call_kwargs = mock_datasource.get_now_table_tableName.call_args_list[0].kwargs
            assert "sys_updated_on>" not in call_kwargs["sysparm_query"]

    async def test_kb_with_unreadable_deny_rules_is_skipped_and_kept(self, servicenow_connector) -> None:
        servicenow_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        servicenow_connector.data_entities_processor.note_kept_record_group = AsyncMock()
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_kb_record_group", return_value=MagicMock(id="rg-1")), \
             patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria", new_callable=AsyncMock,
                          return_value={"read": ["c"], "cannot_read": ["d"], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions", new_callable=AsyncMock,
                          side_effect=_CriteriaUnreadable("boom")):
            mock_ds.return_value.get_now_table_tableName = AsyncMock(
                side_effect=_listing([{"sys_id": "kb1", "title": "KB 1", "owner": "o1"}]))
            await servicenow_connector._sync_knowledge_bases([])

        servicenow_connector.data_entities_processor.note_kept_record_group.assert_awaited_once_with(
            servicenow_connector.connector_id, "kb1")
        assert servicenow_connector.data_entities_processor.on_new_record_groups.await_args.args[0] == []


    @pytest.mark.parametrize("failing", ["read", "write"])
    async def test_kb_with_unreadable_allow_criteria_is_skipped_and_kept(self, servicenow_connector, failing) -> None:
        servicenow_connector.data_entities_processor.on_new_record_groups = AsyncMock()
        servicenow_connector.data_entities_processor.note_kept_record_group = AsyncMock()

        async def criteria(ids: list[str], permission_type: PermissionType, **_: object) -> list:
            if (permission_type == PermissionType.READ) == (failing == "read"):
                raise _CriteriaUnreadable("membership read failed")
            return []

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_kb_record_group", return_value=MagicMock(id="rg-1")), \
             patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria", new_callable=AsyncMock,
                          return_value={"read": ["c"], "cannot_read": [], "write": ["w"]}), \
             patch.object(servicenow_connector, "_process_criteria_permissions", side_effect=criteria):
            mock_ds.return_value.get_now_table_tableName = AsyncMock(
                side_effect=_listing([{"sys_id": "kb1", "title": "KB 1", "owner": "o1"}]))
            await servicenow_connector._sync_knowledge_bases([])

        servicenow_connector.data_entities_processor.note_kept_record_group.assert_awaited_once_with(
            servicenow_connector.connector_id, "kb1")
        assert servicenow_connector.data_entities_processor.on_new_record_groups.await_args.args[0] == []

    async def test_adds_admin_permissions(self, servicenow_connector):
        tx = make_tx_store()
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
                          return_value=[]):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
                    {"sys_id": "kb1", "title": "KB 1", "owner": None, "sys_updated_on": "2024-01-01"},
                ])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([admin_user])
            servicenow_connector.data_entities_processor.on_new_record_groups.assert_called()

    async def test_empty_kbs(self, servicenow_connector):
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
        servicenow_connector._users_gone = AsyncMock(return_value=set())  # the reconcile listing has tests of its own

        page1 = [
            _sys_user_row(
                sys_id=f"u{i}",
                email=f"user{i}@example.com",
                sys_updated_on="2024-01-01",
            )
            for i in range(1000)
        ]
        page2 = [_sys_user_row(sys_id="u1000", email="user100@example.com", sys_updated_on="2024-01-02")]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=_listing(page1, page2))
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_users()
            assert servicenow_connector.data_entities_processor.on_new_app_users.call_count == 2

    async def test_delta_sync_users(self, servicenow_connector):
        servicenow_connector.user_sync_point = AsyncMock()
        servicenow_connector.user_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2024-01-01 00:00:00"}
        )
        servicenow_connector.user_sync_point.update_sync_point = AsyncMock()
        servicenow_connector._users_gone = AsyncMock(return_value=set())  # the reconcile listing has tests of its own

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds, \
             patch.object(servicenow_connector, "_transform_to_app_user", new_callable=AsyncMock, return_value=MagicMock(spec=AppUser)):
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([
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
                side_effect=_category_listing([
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
                side_effect=_category_listing([
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
            for i in range(1000)
        ]
        page2 = [_category_api_row(sys_id="cat1000", label="Cat 100", value="cat-100", sys_updated_on="2024-01-02")]

        mock_ds = AsyncMock()
        # the id listing, then the rows: two pages of 1000 each
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[*_listing(page1, page2), *_listing(page1, page2)])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_categories()
        # X-Total-Count ends each listing, no empty page
        assert mock_ds.get_now_table_tableName.call_count == 4
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

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._sync_categories()
        servicenow_connector.category_sync_point.update_sync_point.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_categories_writes_unlabelled_category(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(return_value=None)
        servicenow_connector.category_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_category_listing([
            _category_api_row(sys_id="cat1", label="", sys_updated_on="2024-01-01"),
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_categories()
        # Named by its sys_id: its articles hang under it (see _category_ids)
        written = [g for c in mock_data_entities_processor.on_new_record_groups.await_args_list for g, _ in c.args[0]]
        assert [(g.external_group_id, g.name) for g in written] == [("cat1", "cat1")]


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
        # read from DELTA_OVERLAP_SECONDS before the checkpoint
        assert "sys_updated_on>2023-12-31 23:58:00" in call_kwargs["sysparm_query"]

    @pytest.mark.asyncio
    async def test_sync_categories_exception_propagates(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=AsyncMock(
            get_now_table_tableName=AsyncMock(return_value=_table_api_response([]))))
        servicenow_connector.category_sync_point = AsyncMock()
        servicenow_connector.category_sync_point.read_sync_point = AsyncMock(
            side_effect=RuntimeError("category sync fail")
        )
        with pytest.raises(RuntimeError, match="category sync fail"):
            await servicenow_connector._sync_categories()


class TestFetchAllGroupsDeep:
    async def test_paginates_groups(self, servicenow_connector):
        page1 = [{"sys_id": f"g{i:03d}", "name": f"Group {i}"} for i in range(1000)]
        page2 = [{"sys_id": "g9999", "name": "Group 1000"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=_listing(page1, page2))
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_groups()
            assert len(result) == 1001

    async def test_handles_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("API down")):
            with pytest.raises(Exception, match="API down"):
                await servicenow_connector._fetch_all_groups()


# ===========================================================================
# Deep sync: _fetch_all_memberships pagination
# ===========================================================================

class TestFetchAllMembershipsDeep:
    @pytest.mark.asyncio
    async def test_a_failed_page_does_not_yield_a_partial_membership_list(self, servicenow_connector):
        """A partial list makes the caller delete edges it cannot rebuild."""
        sync_point = AsyncMock()
        sync_point.read_sync_point = AsyncMock(return_value=None)
        sync_point.update_sync_point = AsyncMock()
        servicenow_connector.group_sync_point = sync_point

        full_page = _table_api_response([
            {"sys_id": f"m{i}", "user": f"u{i}", "group": "g1", "sys_updated_on": "2026-08-01 10:00:00"}
            for i in range(1000)
        ], total=1500)
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=[full_page, ServiceNowAPIError(500, "page two failed", None)]
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_all_memberships()

    async def test_paginates_memberships(self, servicenow_connector):
        page1 = [{"sys_id": f"m{i}", "user": f"u{i}", "group": "g1", "sys_updated_on": "2024-01-01"} for i in range(1000)]
        page2 = [{"sys_id": "m1000", "user": "u1000", "group": "g1", "sys_updated_on": "2024-01-02"}]

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(side_effect=_listing(page1, page2))
            mock_ds.return_value = mock_datasource
            result = await servicenow_connector._fetch_all_memberships()
            assert len(result) == 1001

    async def test_handles_exception(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock,
                          side_effect=Exception("API down")):
            with pytest.raises(Exception, match="API down"):
                await servicenow_connector._fetch_all_memberships()


# ===========================================================================
# Deep sync: _get_admin_users with dict ref
# ===========================================================================

    @pytest.mark.asyncio
    async def test_fetch_all_memberships_api_error_propagates(self, servicenow_connector):
        """A partial membership list would silently remove users from their groups."""
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_all_memberships()


class TestGetAdminUsersDeep:
    async def test_handles_string_user_ref(self, servicenow_connector):
        mock_app_user = MagicMock(spec=AppUser)
        mock_app_user.email = "admin2@example.com"

        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([{"user": "string-sys-id"}])
            )
            mock_ds.return_value = mock_datasource

            servicenow_connector.data_entities_processor.get_user_by_source_id = AsyncMock(return_value=mock_app_user)

            result = await servicenow_connector._get_admin_users()
            assert len(result) == 1

    async def test_handles_empty_user_ref(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=_listing([{"user": ""}])
            )
            mock_ds.return_value = mock_datasource

            tx = make_tx_store()

            @asynccontextmanager
            async def _tx():
                yield tx

            servicenow_connector.data_store_provider = MagicMock()
            servicenow_connector.data_store_provider.transaction = _tx

            result = await servicenow_connector._get_admin_users()
            assert result == set()


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
            with pytest.raises(ServiceNowAPIError):
                await servicenow_connector._sync_users()
            servicenow_connector.data_entities_processor.on_new_app_users.assert_not_called()
            # A page that failed must not leave a checkpoint claiming it was read.
            servicenow_connector.user_sync_point.update_sync_point.assert_not_awaited()

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
    @pytest.fixture(autouse=True)
    def _criteria_read_per_kb(self, servicenow_connector) -> None:
        # These tests script the datasource call by call: each KB reads its own criteria.
        servicenow_connector._load_kb_criteria = AsyncMock()

    async def test_reads_every_knowledge_base(self, servicenow_connector):
        """A criterion change does not touch the base row, so a delta would miss it."""
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                return_value=_table_api_response([])
            )
            mock_ds.return_value = mock_datasource
            await servicenow_connector._sync_knowledge_bases([])

    async def test_api_error_kbs(self, servicenow_connector):
        with patch.object(servicenow_connector, "_get_fresh_datasource", new_callable=AsyncMock) as mock_ds:
            mock_datasource = AsyncMock()
            mock_datasource.get_now_table_tableName = AsyncMock(
                side_effect=ServiceNowAPIError(500, "Server error", None)
            )
            mock_ds.return_value = mock_datasource
            with pytest.raises(ServiceNowAPIError):
                await servicenow_connector._sync_knowledge_bases([])

    async def test_exception_in_kb_sync_propagated(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(
            side_effect=Exception("kb error")
        )
        with pytest.raises(Exception, match="kb error"):
            await servicenow_connector._sync_knowledge_bases([])

    @pytest.mark.asyncio
    async def test_sync_knowledge_bases_pagination(self, servicenow_connector, mock_data_entities_processor):
        page1 = [
            _kb_api_row(sys_id=f"kb{i}", title=f"KB {i}", sys_updated_on="2024-01-01")
            for i in range(100)
        ]
        page2 = [_kb_api_row(sys_id="kb100", title="KB 100", sys_updated_on="2024-01-02")]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing(page1, page2))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria",
                          new_callable=AsyncMock, return_value={"read": [], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_knowledge_bases([])

        assert mock_ds.get_now_table_tableName.call_count == 2  # X-Total-Count ends it, no empty page
        mock_data_entities_processor.on_new_record_groups.assert_called()

    @pytest.mark.asyncio
    async def test_sync_knowledge_bases_owner_permission(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
            _kb_api_row(sys_id="kb1", title="KB 1", owner="owner1", sys_updated_on="2024-01-01"),
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        servicenow_connector._email_by_source_id = {"owner1": "owner@test.com"}

        with patch.object(servicenow_connector, "_fetch_kb_permissions_from_criteria",
                          new_callable=AsyncMock, return_value={"read": [], "write": []}), \
             patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]):
            await servicenow_connector._sync_knowledge_bases([])

        ((_, permissions),) = servicenow_connector.data_entities_processor.on_new_record_groups.await_args.args[0]
        assert [(p.email, p.type, p.entity_type) for p in permissions] == [("owner@test.com", PermissionType.OWNER, EntityType.USER)]

    @pytest.mark.asyncio
    async def test_sync_knowledge_bases_transform_returns_none_skipped(self, servicenow_connector, mock_data_entities_processor):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
            {"sys_id": "kb1", "title": "", "sys_updated_on": "2024-01-01"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_knowledge_bases([])
        mock_data_entities_processor.on_new_record_groups.assert_not_called()


# ===========================================================================
# _sync_categories (additional)
# ===========================================================================


class TestCriteriaGrants:
    """A criterion that needs no user resolution becomes grants: its users by email (one with no app
    user gets none), its groups, roles, departments, locations and companies as group grants."""

    @staticmethod
    async def _grants(sc, row: dict, permission_type: PermissionType = PermissionType.READ) -> list[Permission]:
        sc._criteria_rows["c1"] = TableAPIRecord(sys_id="c1", active="true", **row)
        sc._email_by_source_id = {"u1": "u1@x.com", "u2": "u2@x.com"}
        return await sc._process_criteria_permissions(["c1"], permission_type)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("field", ["group", "role", "department", "location", "company"])
    async def test_each_group_field_is_a_group_grant(self, servicenow_connector, field):
        perms = await self._grants(servicenow_connector, {field: "x1, x2"}, PermissionType.WRITE)
        assert [(p.entity_type, p.external_id, p.type) for p in perms] == [
            (EntityType.GROUP, "x1", PermissionType.WRITE), (EntityType.GROUP, "x2", PermissionType.WRITE)]

    @pytest.mark.asyncio
    async def test_users_by_email_and_unknown_users_get_nothing(self, servicenow_connector):
        perms = await self._grants(servicenow_connector, {"user": "u2,missing, u1"})
        assert [(p.entity_type, p.email) for p in perms] == [(EntityType.USER, "u1@x.com"), (EntityType.USER, "u2@x.com")]
        servicenow_connector.data_entities_processor.get_user_by_source_id.assert_not_awaited()  # no lookup per user

    @pytest.mark.asyncio
    async def test_mixed_fields(self, servicenow_connector):
        perms = await self._grants(servicenow_connector, {"user": "u1", "group": "g1", "company": "co1"})
        assert {(p.entity_type, p.email or p.external_id) for p in perms} == {
            (EntityType.USER, "u1@x.com"), (EntityType.GROUP, "g1"), (EntityType.GROUP, "co1")}

    @pytest.mark.asyncio
    async def test_no_field_is_everyone(self, servicenow_connector):
        perms = await self._grants(servicenow_connector, {})
        assert [p.entity_type for p in perms] == [EntityType.ORG]


# ===========================================================================
# _transform_to_kb_record_group / _transform_to_category_record_group (additional)
# ===========================================================================


class TestCreateConnector:
    @pytest.mark.asyncio
    async def test_create_connector_factory(self):
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
            processor = MagicMock()
            processor.org_id = "org-1"

            result = await ServiceNowConnector.create_connector(
                logger=logging.getLogger("test"),
                data_store_provider=MagicMock(),
                config_service=AsyncMock(),
                connector_id="factory-1",
                scope="team",
                created_by="user-1",
                data_entities_processor=processor,
            )
            assert isinstance(result, ServiceNowConnector)

    @pytest.mark.asyncio
    async def test_reindex_records_with_nothing_queues_nothing(self, servicenow_connector):
        await servicenow_connector.reindex_records([])
        servicenow_connector.data_entities_processor.reindex_existing_records.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_exception_handling(self, servicenow_connector):
        servicenow_connector.servicenow_client = MagicMock()
        servicenow_connector.servicenow_datasource = MagicMock()
        with patch.object(servicenow_connector.logger, "info", side_effect=[None, RuntimeError("cleanup fail")]):
            await servicenow_connector.cleanup()
        assert servicenow_connector.servicenow_client is None


class TestFetchAttachmentsForArticles:
    @pytest.mark.asyncio
    async def test_fetch_attachments_success(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
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

        result = (await servicenow_connector._fetch_attachments_for_articles(["art1"]))["art1"]
        assert len(result) == 1
        assert result[0].file_name == "file.pdf"

    @pytest.mark.asyncio
    async def test_fetch_attachments_no_results(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        assert await servicenow_connector._fetch_attachments_for_articles(["art1"]) == {}

    @pytest.mark.asyncio
    async def test_fetch_attachments_api_error(self, servicenow_connector):
        # A failed listing is not "no attachments": a full sync would unlink them.
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(404, "Not found", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_attachments_for_articles(["art1"])

    @pytest.mark.asyncio
    async def test_fetch_attachments_general_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("network"))

        with pytest.raises(RuntimeError, match="network"):
            await servicenow_connector._fetch_attachments_for_articles(["art1"])

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
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing(records))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = (await servicenow_connector._fetch_attachments_for_articles(["art1"]))["art1"]
        assert len(result) == 12


# ===========================================================================
# criteria helpers
# ===========================================================================


class TestCriteriaDenyAndMatchAll:
    """Cannot Read and Match All are evaluated at sync time (allowed minus denied)."""

    @staticmethod
    def _wire(connector, criteria, members, children=None) -> None:
        """criteria: {id: row}; members: {(table, field_value_id): [user ids]};
        children: {parent group id: [child group ids]}."""
        async def table(tableName, sysparm_query, sysparm_fields=None, sysparm_limit="100", **_) -> TableAPIResponse:
            ids = sysparm_query.split("IN", 1)[1].split("^")[0].split(",")
            if tableName == "sys_user_group":
                rows = [{"sys_id": c} for i in ids for c in (children or {}).get(i, [])]
            elif tableName == "user_criteria":  # every requested field, "" when empty, as the Table API answers
                rows = [{f: criteria[i].get(f, "") for f in sysparm_fields.split(",")} for i in sorted(ids) if i in criteria]
            else:
                user_field = sysparm_fields.split(",")[0]
                users = [u for i in ids for u in members.get((tableName, i), [])]
                # a sys_user read returns the user's own sys_id; other tables have a row id of their own
                rows = [{user_field: u} if user_field == "sys_id" else {user_field: u, "sys_id": f"{n:06d}"}
                        for n, u in enumerate(users)]
            after = keyset_page(sorted(rows, key=lambda r: r["sys_id"]), sysparm_query, None)
            return _table_api_response(after[:int(sysparm_limit)], len(after))

        ds = AsyncMock()
        ds.get_now_table_tableName = AsyncMock(side_effect=table)
        connector._get_fresh_datasource = AsyncMock(return_value=ds)
        connector._kb_privileged_users = AsyncMock(return_value=set())  # admins/owner/managers: tested separately
        everyone = {u for us in members.values() for u in us}
        for row in criteria.values():
            everyone |= set(str(row.get("user") or "").split(","))
        connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[
            MagicMock(source_user_id=u, email=f"{u}@x.com") for u in everyone if u
        ])

    @staticmethod
    def _users(perms) -> list[str]:
        return sorted(p.email.split("@")[0] for p in perms)

    @pytest.mark.asyncio
    async def test_cannot_read_user_is_excluded(self, servicenow_connector) -> None:
        self._wire(
            servicenow_connector,
            {"can": {"sys_id": "can", "group": "g1"}, "deny": {"sys_id": "deny", "user": "u2"}},
            {("sys_user_grmember", "g1"): ["u1", "u2", "u3"]},
        )
        perms = await servicenow_connector._process_criteria_permissions(
            ["can"], PermissionType.READ, deny_criteria_ids=["deny"]
        )
        assert self._users(perms) == ["u1", "u3"]

    @pytest.mark.asyncio
    async def test_cannot_read_by_group_excludes_its_members(self, servicenow_connector) -> None:
        self._wire(
            servicenow_connector,
            {"can": {"sys_id": "can", "user": "u1,u2"}, "deny": {"sys_id": "deny", "group": "contractors"}},
            {("sys_user_grmember", "contractors"): ["u2"]},
        )
        perms = await servicenow_connector._process_criteria_permissions(
            ["can"], PermissionType.READ, deny_criteria_ids=["deny"]
        )
        assert self._users(perms) == ["u1"]

    @pytest.mark.asyncio
    async def test_match_all_requires_every_populated_field(self, servicenow_connector) -> None:
        self._wire(
            servicenow_connector,
            {"c": {"sys_id": "c", "match_all": "true", "group": "g1", "department": "d1,d2"}},
            {
                ("sys_user_grmember", "g1"): ["u1", "u2"],
                ("sys_user", "d1"): ["u2", "u3"],
                ("sys_user", "d2"): ["u4"],
            },
        )
        perms = await servicenow_connector._process_criteria_permissions(["c"], PermissionType.READ)
        assert self._users(perms) == ["u2"]  # in g1 AND in d1-or-d2

    @pytest.mark.asyncio
    async def test_without_match_all_any_field_is_enough(self, servicenow_connector) -> None:
        self._wire(
            servicenow_connector,
            {"c": {"sys_id": "c", "match_all": "false", "group": "g1", "department": "d1"}},
            {},
        )
        perms = await servicenow_connector._process_criteria_permissions(["c"], PermissionType.READ)
        assert {(p.entity_type, p.external_id) for p in perms} == {(EntityType.GROUP, "g1"), (EntityType.GROUP, "d1")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unevaluable", [{"script": "answer = true;"}, {"advanced": "false", "script": "answer = gs.getUser().isMemberOf('x');"}])
    @pytest.mark.parametrize("fields", [{"group": "g1"}, {"group": "g1", "department": "d1"}])
    async def test_match_all_with_an_unevaluable_condition_grants_nobody(self, servicenow_connector, unevaluable, fields) -> None:
        # A script is not evaluated, so under Match All the row can never be known to match.
        self._wire(servicenow_connector, {"c": {"sys_id": "c", "match_all": "true", **fields, **unevaluable}},
                   {("sys_user_grmember", "g1"): ["u1"], ("sys_user", "d1"): ["u1"]})
        assert await servicenow_connector._process_criteria_permissions(["c"], PermissionType.READ) == []
        assert await servicenow_connector._process_criteria_permissions(["c"], PermissionType.WRITE) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unevaluable", [{"script": "answer = true;"}, {"advanced": "false", "script": "answer = gs.getUser().isMemberOf('x');"}])
    async def test_match_any_with_an_unevaluable_condition_grants_through_the_rest(self, servicenow_connector, unevaluable) -> None:
        self._wire(servicenow_connector, {"c": {"sys_id": "c", "match_all": "false", "group": "g1", **unevaluable}}, {})
        perms = await servicenow_connector._process_criteria_permissions(["c"], PermissionType.READ)
        assert {(p.entity_type, p.external_id) for p in perms} == {(EntityType.GROUP, "g1")}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("unevaluable", [{"script": "answer = true;"}, {"advanced": "false", "script": "answer = gs.getUser().isMemberOf('x');"}])
    async def test_with_a_deny_the_unevaluable_allow_condition_still_fails_closed(self, servicenow_connector, unevaluable) -> None:
        criteria = {
            "any": {"sys_id": "any", "match_all": "false", "user": "u1", **unevaluable},
            "all": {"sys_id": "all", "match_all": "true", "user": "u2", **unevaluable},
            "d": {"sys_id": "d", "user": "u9"},
        }
        self._wire(servicenow_connector, criteria, {})
        perms = await servicenow_connector._process_criteria_permissions(
            ["any", "all"], PermissionType.READ, deny_criteria_ids=["d"]
        )
        assert self._users(perms) == ["u1"]

    @pytest.mark.asyncio
    async def test_inactive_criteria_grants_nothing(self, servicenow_connector) -> None:
        self._wire(servicenow_connector, {"c": {"sys_id": "c", "active": "false", "user": "u1"}}, {})
        assert await servicenow_connector._process_criteria_permissions(["c"], PermissionType.READ) == []

    _CHILD_FIXTURE = (
        {"can": {"sys_id": "can", "group": "eng"}, "deny": {"sys_id": "deny", "group": "ctr"}},
        {
            ("sys_user_grmember", "eng"): ["u1", "u3"],
            ("sys_user_grmember", "eng-core"): ["u2"],
            ("sys_user_grmember", "ctr"): ["u9"],
            ("sys_user_grmember", "ctr-sub"): ["u3"],
        },
    )

    @pytest.mark.asyncio
    async def test_child_groups_expand_on_both_paths(self, servicenow_connector) -> None:
        # Verified on an instance: a group criterion matches the members of its child groups.
        criteria, members = self._CHILD_FIXTURE
        self._wire(servicenow_connector, criteria, members, children={"eng": ["eng-core"], "ctr": ["ctr-sub"]})
        perms = await servicenow_connector._process_criteria_permissions(
            ["can"], PermissionType.READ, deny_criteria_ids=["deny"]
        )
        assert self._users(perms) == ["u1", "u2"]  # u2 via eng-core; u3 denied via ctr-sub

    @pytest.mark.asyncio
    async def test_user_listing_is_read_once_for_many_articles(self, servicenow_connector) -> None:
        self._wire(servicenow_connector, {"d": {"sys_id": "d", "user": "u9"}, "c": {"sys_id": "c", "user": "u1"}}, {})
        for _ in range(25):
            await servicenow_connector._process_criteria_permissions(
                ["c"], PermissionType.READ, deny_criteria_ids=["d"]
            )
        servicenow_connector.data_entities_processor.get_all_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_departments_do_not_include_child_departments(self, servicenow_connector) -> None:
        self._wire(
            servicenow_connector,
            {"c": {"sys_id": "c", "match_all": "true", "user": "u1,u2", "department": "it"}},
            {("sys_user", "it"): ["u1"], ("sys_user", "it-child"): ["u2"]},
            children={"it": ["it-child"]},
        )
        perms = await servicenow_connector._process_criteria_permissions(["c"], PermissionType.READ)
        assert self._users(perms) == ["u1"]

    @pytest.mark.asyncio
    async def test_unreadable_denied_group_raises_so_nothing_is_granted(self, servicenow_connector) -> None:
        self._wire(
            servicenow_connector,
            {"can": {"sys_id": "can", "user": "u1"}, "deny": {"sys_id": "deny", "group": "g9"}},
            {},
        )
        real = servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.side_effect

        async def flaky(tableName, **kw) -> TableAPIResponse:
            if tableName == "sys_user_grmember":
                raise ServiceNowAPIError(500, "boom", None)
            return await real(tableName=tableName, **kw)

        servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.side_effect = flaky
        with pytest.raises(_CriteriaUnreadable):
            await servicenow_connector._process_criteria_permissions(
                ["can"], PermissionType.READ, deny_criteria_ids=["deny"]
            )

    @pytest.mark.asyncio
    async def test_five_thousand_users_use_one_user_listing_and_paged_reads(self, servicenow_connector) -> None:
        users = [f"u{i}" for i in range(5000)]
        self._wire(
            servicenow_connector,
            {"can": {"sys_id": "can", "group": "big"}, "deny": {"sys_id": "deny", "user": "u0"}},
            {("sys_user_grmember", "big"): users},
        )
        servicenow_connector.data_entities_processor.get_user_by_source_id = AsyncMock()
        perms = await servicenow_connector._process_criteria_permissions(
            ["can"], PermissionType.READ, deny_criteria_ids=["deny"]
        )
        assert len(perms) == 4999
        servicenow_connector.data_entities_processor.get_user_by_source_id.assert_not_awaited()
        servicenow_connector.data_entities_processor.get_all_app_users.assert_awaited_once()
        calls = servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.await_count
        assert calls < 70  # ~50 member pages of 100, plus criteria and child-group reads

    @pytest.mark.asyncio
    async def test_failed_allowed_membership_read_raises(self, servicenow_connector) -> None:
        self._wire(servicenow_connector, {"can": {"sys_id": "can", "group": "g1"}, "d": {"sys_id": "d", "user": "u9"}}, {})
        real = servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.side_effect

        async def flaky(tableName, **kw) -> TableAPIResponse:
            if tableName == "sys_user_grmember":
                raise ServiceNowAPIError(500, "boom", None)
            return await real(tableName=tableName, **kw)

        servicenow_connector._get_fresh_datasource.return_value.get_now_table_tableName.side_effect = flaky
        # Unknown is not empty: the caller keeps the stored grants instead of writing none.
        with pytest.raises(_CriteriaUnreadable):
            await servicenow_connector._process_criteria_permissions(["can"], PermissionType.READ, deny_criteria_ids=["d"])

    @pytest.mark.asyncio
    @pytest.mark.parametrize("deny_row", [
        {"sys_id": "d", "advanced": "false", "script": "answer = true;", "user": "u9"},
        {"sys_id": "d", "advanced": "true", "script": "answer = false;"},
        {"sys_id": "d"},
    ])
    async def test_a_deny_row_that_cannot_be_evaluated_denies_everyone(self, servicenow_connector, deny_row) -> None:
        self._wire(servicenow_connector, {"can": {"sys_id": "can", "user": "u1"}, "d": deny_row}, {})
        assert await servicenow_connector._process_criteria_permissions(
            ["can"], PermissionType.READ, deny_criteria_ids=["d"]
        ) == []


class TestFetchKbPermissionsFromCriteria:
    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_read_criteria(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            *_listing([{"user_criteria": "crit-read-1"}]),
            _table_api_response([]),
            _table_api_response([]),
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
            _table_api_response([]),
            *_listing([{"user_criteria": "crit-write-1"}]),
            _table_api_response([]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert "crit-write-1" in result["write"]

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_both_read_and_write(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            *_listing([{"user_criteria": "crit-r"}]),
            *_listing([{"user_criteria": "crit-d"}]),
            *_listing([{"user_criteria": "crit-w"}]),
            *_listing([{"user_criteria": "crit-wd"}]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result["read"] == ["crit-r"]
        assert result["cannot_read"] == ["crit-d"]
        assert result["write"] == ["crit-w"]
        assert result["cannot_write"] == ["crit-wd"]  # Cannot Contribute is read too

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_read_api_error_raises(self, servicenow_connector):
        # An empty Can Read list would remove every reader of the KB.
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            ServiceNowAPIError(500, "read fail", None),
            _table_api_response([]),
            _table_api_response([{"user_criteria": "crit-w"}]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_write_api_error_raises(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            *_listing([{"user_criteria": "crit-r"}]),
            _table_api_response([]),
            ServiceNowAPIError(500, "write fail", None),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_general_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError):
            await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_cannot_read_api_error_raises(self, servicenow_connector) -> None:
        # Unknown deny must not be read as "no deny": the KB would be granted to denied users.
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            *_listing([{"user_criteria": "crit-r"}]),
            ServiceNowAPIError(500, "deny fail", None),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")

    @pytest.mark.asyncio
    async def test_fetch_kb_permissions_empty_results(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_kb_permissions_from_criteria("kb1")
        assert result == {"read": [], "cannot_read": [], "write": [], "cannot_write": []}


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

        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[mock_user])

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

        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[])

        with patch.object(servicenow_connector, "_transform_to_user_group") as mock_transform:
            mock_transform.return_value = MagicMock(spec=AppUserGroup)
            result = await servicenow_connector._flatten_and_create_user_groups(groups, memberships)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_pagination(self, servicenow_connector):
        page1 = [
            {"sys_id": f"ra{i}", "user": f"u{i}", "role": "r1", "sys_updated_on": "2024-01-01"}
            for i in range(1000)
        ]
        page2 = [{"sys_id": "ra1000", "user": "u1000", "role": "r1", "sys_updated_on": "2024-01-02"}]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing(page1, page2))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_all_role_assignments()
        assert len(result) == 1001

    @pytest.mark.asyncio
    async def test_fetch_all_role_assignments_api_error_propagates(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_all_role_assignments()

    @pytest.mark.asyncio
    async def test_fetch_role_hierarchy_empty_results(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._fetch_role_hierarchy()
        assert result == []

    @pytest.mark.asyncio
    async def test_fetch_role_hierarchy_api_error_propagates(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_role_hierarchy()

    @pytest.mark.asyncio
    async def test_fetch_all_roles_api_error_propagates(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._fetch_all_roles()


# ===========================================================================
# _sync_knowledge_bases (additional)
# ===========================================================================


class TestInitRefreshToken:
    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
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
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
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



# ===========================================================================
# stream_record generators
# ===========================================================================


class TestProcessCriteriaPermissions:
    @pytest.mark.asyncio
    async def test_process_criteria_permissions_empty_criteria_ids(self, servicenow_connector):
        result = await servicenow_connector._process_criteria_permissions([], PermissionType.READ)
        assert result == []

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_batch_fetch_success(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
            {"sys_id": "crit1", "active": "true", "match_all": "false", "advanced": "false", "script": "", "user": "u1", "group": "", "role": "", "department": "", "location": "", "company": ""},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        servicenow_connector._email_by_source_id = {"u1": "u@test.com"}

        result = await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ)

        assert [(p.email, p.type, p.entity_type) for p in result] == [("u@test.com", PermissionType.READ, EntityType.USER)]

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_api_error(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "fail", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(_CriteriaUnreadable):
            await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ)

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_extract_and_convert(self, servicenow_connector):
        servicenow_connector.data_entities_processor.get_all_app_users = AsyncMock(return_value=[
            MagicMock(source_user_id="u1", email="extracted@test.com")])

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
            {"sys_id": "crit1", "active": "true", "match_all": "false", "advanced": "false", "script": "", "user": "u1", "group": "", "role": "", "department": "", "location": "", "company": ""},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        result = await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ)
        assert len(result) == 1
        assert result[0].email == "extracted@test.com"

    @pytest.mark.asyncio
    async def test_process_criteria_permissions_general_exception(self, servicenow_connector):
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("boom"))

        with pytest.raises(_CriteriaUnreadable):
            await servicenow_connector._process_criteria_permissions(["crit1"], PermissionType.READ)


class TestProcessSingleArticle:
    @pytest.mark.asyncio
    async def test_process_single_article_with_attachments(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art1", number="KB-art1",
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

        with patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]):
            updates = await servicenow_connector._process_single_article(article, [att])

        assert [(r.record_type, p) for r, p in updates] == [(RecordType.WEBPAGE, []), (RecordType.FILE, [])]

    @pytest.mark.asyncio
    async def test_process_single_article_with_permissions(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art2", number="KB-art2",
            short_description="Article with criteria",
            kb_category="cat1",
            can_read_user_criteria="crit1,crit2",
        )
        mock_perm = Permission(email="user@test.com", type=PermissionType.READ, entity_type=EntityType.USER)

        with patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[mock_perm]), \
             patch.object(servicenow_connector, "_kb_privileged_users", new_callable=AsyncMock, return_value=set()), \
             patch.object(servicenow_connector, "_kb_contributor_grants", new_callable=AsyncMock, return_value=[]):
            updates = await servicenow_connector._process_single_article(article, [])

        assert len(updates) == 1
        assert mock_perm in updates[0][1]
        assert updates[0][0].access_rule == AccessRule.RESTRICTED

    @pytest.mark.asyncio
    async def test_process_single_article_author_gets_no_grant(self, servicenow_connector):
        # The author reads nothing by authorship (instance: the author of every row read none).
        article = KBKnowledge(
            sys_id="art3", number="KB-art3",
            short_description="Article with author",
            kb_category="cat1",
            author="author-sys-id",
        )
        servicenow_connector._email_by_source_id = {"author-sys-id": "author@test.com"}

        with patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]):
            updates = await servicenow_connector._process_single_article(article, [])

        assert updates[0][1] == []

    @pytest.mark.asyncio
    async def test_process_single_article_without_author(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art4", number="KB-art4",
            short_description="No author",
            kb_category="cat1",
            author=None,
        )

        with patch.object(servicenow_connector, "_process_criteria_permissions",
                          new_callable=AsyncMock, return_value=[]):
            updates = await servicenow_connector._process_single_article(article, [])

        assert len(updates) == 1

    @pytest.mark.asyncio
    async def test_process_single_article_transform_fails(self, servicenow_connector):
        article = KBKnowledge(
            sys_id="art5",
            short_description="Title",
            kb_category="cat1",
        )  # no number, so no record key
        updates = await servicenow_connector._process_single_article(article, [])
        assert updates == []

    @pytest.mark.asyncio
    async def test_process_single_article_exception_raises(self, servicenow_connector):
        article = TableAPIRecord(sys_id="art1", short_description="Title", kb_category="cat1")
        with patch.object(
            servicenow_connector, "_transform_to_article_webpage_record",
            side_effect=RuntimeError("transform fail"),
        ), pytest.raises(RuntimeError, match="transform fail"):
            await servicenow_connector._process_single_article(article, [])


class TestStreamRecordGenerators:
    @pytest.mark.asyncio
    async def test_stream_article_generator_yields_content(self, servicenow_connector):
        servicenow_connector._fetch_article_content = AsyncMock(return_value=b'{"block_groups": ["Hello"]}')
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Article"
        record.external_record_id = "art-1"

        response = await servicenow_connector.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body == b'{"block_groups": ["Hello"]}'
        assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_attachment_generator_yields_bytes(self, servicenow_connector):
        datasource = AsyncMock()
        datasource.stream_attachment = AsyncMock(return_value=file_response(b"file-bytes" * 10000))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=datasource)
        servicenow_connector._current_attachment = AsyncMock(return_value="att-1")
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "file.pdf"
        record.external_record_id = "att-1"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        response = await servicenow_connector.stream_record(record)
        chunks = [chunk async for chunk in response.body_iterator]
        assert len(chunks) > 1  # streamed, not held whole
        assert b"".join(chunks) == b"file-bytes" * 10000


# ===========================================================================
# role assignments and hierarchy fetch
# ===========================================================================


class TestSyncArticles:
    @pytest.fixture(autouse=True)
    def _reread_due(self, servicenow_connector) -> None:
        # These tests script the datasource call by call: the access check and the feedback
        # count are not among the calls.
        servicenow_connector._access_reread_due = AsyncMock(return_value=(None, True))
        servicenow_connector._feedback_count = AsyncMock(return_value=None)

    @pytest.mark.asyncio
    async def test_sync_articles_full_sync_with_pagination(self, servicenow_connector, mock_data_entities_processor):
        servicenow_connector.article_sync_point = migrated_sync_point()

        page1 = [
            {"sys_id": f"art{i}", "number": f"KB{i}", "short_description": f"Article {i}",
             "kb_category": "cat1", "sys_updated_on": "2024-01-01"}
            for i in range(100)
        ]
        page2 = [
            {"sys_id": "art100", "number": "KB100", "short_description": "Article 100",
             "kb_category": "cat1", "sys_updated_on": "2024-01-02"},
        ]

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing(page1, page2))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_process_single_article", new_callable=AsyncMock,
                          return_value=[(MagicMock(record_type=RecordType.WEBPAGE), [])]), \
             patch.object(servicenow_connector, "_fetch_attachments_for_articles", new_callable=AsyncMock, return_value={}), \
             patch.object(servicenow_connector, "_feedback", new_callable=AsyncMock, return_value={}):
            await servicenow_connector._sync_articles()

        assert mock_ds.get_now_table_tableName.call_count == 2  # X-Total-Count ends it, no empty page
        # A finished full read clears where it would have resumed.
        assert servicenow_connector.article_sync_point.data["articles"] == {
            "last_sync_time": "2024-01-02 00:00:00", "full_resume_after": [],
            "synced_in_overlap": ["2024-01-02|art100"]}

    @pytest.mark.asyncio
    async def test_sync_articles_delta_sync_with_last_sync_time(self, servicenow_connector):
        servicenow_connector.article_sync_point = migrated_sync_point("2024-01-01 00:00:00")

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_articles()
        call_kwargs = mock_ds.get_now_table_tableName.call_args_list[0].kwargs
        # read from DELTA_OVERLAP_SECONDS before the checkpoint
        assert "sys_updated_on>2023-12-31 23:58:00" in call_kwargs["sysparm_query"]

    @pytest.mark.asyncio
    async def test_sync_articles_api_error_breaks_loop(self, servicenow_connector):
        servicenow_connector.article_sync_point = migrated_sync_point()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(500, "Server error", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with pytest.raises(ServiceNowAPIError):
            await servicenow_connector._sync_articles()
        mock_data_entities_processor = servicenow_connector.data_entities_processor
        mock_data_entities_processor.on_new_records.assert_not_called()
        assert "articles" not in servicenow_connector.article_sync_point.data

    @pytest.mark.asyncio
    async def test_sync_articles_updates_sync_checkpoint(self, servicenow_connector):
        servicenow_connector.article_sync_point = migrated_sync_point()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
            {"sys_id": "art1", "number": "KB1", "short_description": "A1", "kb_category": "c1",
             "sys_updated_on": "2024-06-15 12:00:00"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        with patch.object(servicenow_connector, "_process_single_article", new_callable=AsyncMock, return_value=[]), \
             patch.object(servicenow_connector, "_fetch_attachments_for_articles", new_callable=AsyncMock, return_value={}), \
             patch.object(servicenow_connector, "_feedback", new_callable=AsyncMock, return_value={}):
            await servicenow_connector._sync_articles()

        checkpoint = servicenow_connector.article_sync_point.data["articles"]
        assert checkpoint["last_sync_time"] == "2024-06-15 12:00:00"

    @pytest.mark.asyncio
    async def test_sync_articles_empty_result(self, servicenow_connector):
        servicenow_connector.article_sync_point = migrated_sync_point()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_table_api_response([]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await servicenow_connector._sync_articles()
        assert "articles" not in servicenow_connector.article_sync_point.data

    @pytest.mark.asyncio
    async def test_sync_articles_counts_attachments_correctly(self, servicenow_connector):
        servicenow_connector.article_sync_point = migrated_sync_point()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=_listing([
            {"sys_id": "art1", "number": "KB1", "short_description": "A1", "kb_category": "c1",
             "sys_updated_on": "2024-01-01"},
        ]))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        article = (MagicMock(record_type=RecordType.WEBPAGE), [])
        file = (MagicMock(record_type=RecordType.FILE), [])

        with patch.object(servicenow_connector, "_process_single_article", new_callable=AsyncMock,
                          return_value=[article, file]) as mock_process_single, \
             patch.object(servicenow_connector, "_fetch_attachments_for_articles", new_callable=AsyncMock, return_value={}), \
             patch.object(servicenow_connector, "_feedback", new_callable=AsyncMock, return_value={}):
            await servicenow_connector._sync_articles()

        mock_process_single.assert_called_once()
        servicenow_connector.data_entities_processor.on_new_records.assert_awaited_once_with([article, file])

    @pytest.mark.asyncio
    async def test_sync_articles_exception_propagates(self, servicenow_connector):
        servicenow_connector.article_sync_point = AsyncMock()
        servicenow_connector.article_sync_point.read_sync_point = AsyncMock(
            side_effect=RuntimeError("article sync fail")
        )
        with pytest.raises(RuntimeError, match="article sync fail"):
            await servicenow_connector._sync_articles()


# ===========================================================================
# _resolve_kb_portal_suffix and the article web URL
# ===========================================================================

class TestResolveKbPortalSuffix:
    @pytest.mark.asyncio
    async def test_uses_the_instance_property(self, servicenow_connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_table_api_response([{"value": "kb"}])
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        assert await servicenow_connector._resolve_kb_portal_suffix() == "kb"
        assert mock_ds.get_now_table_tableName.await_count == 1

    @pytest.mark.asyncio
    async def test_falls_back_to_the_default_portal(self, servicenow_connector):
        """An empty property means the default portal serves the knowledge pages."""
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _table_api_response([{"value": ""}]),
            _table_api_response([{"url_suffix": "esc"}]),
        ])
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        assert await servicenow_connector._resolve_kb_portal_suffix() == "esc"

    @pytest.mark.asyncio
    async def test_falls_back_to_the_knowledge_portal_on_error(self, servicenow_connector):
        """A wrong link is better than a failed sync."""
        servicenow_connector._get_fresh_datasource = AsyncMock(side_effect=RuntimeError("no access"))

        assert await servicenow_connector._resolve_kb_portal_suffix() == "kb"


class TestArticleWebUrl:
    def test_article_url_uses_the_resolved_portal(self, servicenow_connector):
        servicenow_connector.instance_url = "https://example.service-now.com"
        servicenow_connector.kb_portal_suffix = "esc"

        record = servicenow_connector._transform_to_article_webpage_record(
            KBKnowledge(sys_id="art1", number="KB-art1", short_description="Title", kb_category="cat1")
        )

        assert record.weburl == (
            "https://example.service-now.com/esc?id=kb_article_view&sysparm_article=KB-art1"
        )


# ===========================================================================
# glide.knowman.apply_article_read_criteria
# ===========================================================================

class TestRecordRevision:
    """A stored record is rewritten, and re-queued for indexing, only when
    external_revision_id changes. Leaving it unset froze every record at its
    first index: title, webUrl and content never updated again."""

    def test_article_carries_a_revision_id(self, servicenow_connector):
        record = servicenow_connector._transform_to_article_webpage_record(
            KBKnowledge(
                sys_id="art1", number="KB-art1",
                short_description="Title",
                kb_category="cat1",
                sys_updated_on="2026-08-26 09:15:00",
            )
        )
        assert record.external_revision_id

    def test_a_changed_body_gets_a_different_revision_id(self, servicenow_connector):
        def build(text, updated_on):
            return servicenow_connector._transform_to_article_webpage_record(
                KBKnowledge(
                    sys_id="art1", number="KB-art1",
                    short_description="Title",
                    text=text,
                    kb_category="cat1",
                    sys_updated_on=updated_on,
                )
            )

        assert build("<p>a</p>", "2026-08-26 09:15:00").external_revision_id \
            == build("<p>a</p>", "2026-08-26 11:42:00").external_revision_id  # a touch is no change
        assert build("<p>a</p>", "2026-08-26 09:15:00").external_revision_id \
            != build("<p>b</p>", "2026-08-26 09:15:00").external_revision_id

    def test_attachment_carries_a_revision_id(self, servicenow_connector):
        record = servicenow_connector._transform_to_attachment_file_record(
            AttachmentMetadata(
                sys_id="att1",
                file_name="doc.pdf",
                content_type="application/pdf",
                size_bytes="1024",
                table_sys_id="art1",
                sys_created_on="2026-08-01 10:00:00",
                sys_updated_on="2026-08-26 11:42:00",
            ),
            parent_record_group_type=RecordGroupType.SERVICENOW_CATEGORY,
            parent_external_record_group_id="cat1",
        )
        # sys_updated_on moves with no change to the bytes, which never change
        assert record.external_revision_id == "1024:2026-08-01 10:00:00"


class TestArticleReadCriteriaResolution:
    """The property decides who reaches an article, so a read that fails must
    not be read as "the instance does not apply criteria"."""

    def _connector(self, connector, *, value=None, error=None):
        if error is not None:
            connector._read_instance_property = AsyncMock(side_effect=error)
        else:
            connector._read_instance_property = AsyncMock(return_value=value)
        return connector

    @pytest.mark.asyncio
    async def test_property_true_applies_the_override(self, servicenow_connector):
        self._connector(servicenow_connector, value="true")
        assert await servicenow_connector._resolve_article_read_criteria() is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["false", "", None])
    async def test_unset_or_false_means_the_base_grant_carries(self, servicenow_connector, value):
        """ServiceNow leaves the property unset on a default instance, so unset
        is a real answer and it means false."""
        self._connector(servicenow_connector, value=value)
        assert await servicenow_connector._resolve_article_read_criteria() is False

    @pytest.mark.asyncio
    async def test_an_unreadable_property_leans_restrictive(self, servicenow_connector):
        """Not "could not tell, therefore no restriction". A token without rights
        on sys_properties would otherwise open every article that carries its own
        criteria, for the whole life of the process."""
        self._connector(servicenow_connector, error=ServiceNowAPIError(403, "forbidden", None))
        assert await servicenow_connector._resolve_article_read_criteria() is True

    @pytest.mark.asyncio
    async def test_an_unreadable_property_does_not_stop_the_connector(self, servicenow_connector):
        self._connector(servicenow_connector, error=RuntimeError("network"))
        assert await servicenow_connector._resolve_article_read_criteria() is True

    @pytest.mark.asyncio
    async def test_a_sys_properties_outage_does_not_open_restricted_articles(
        self, servicenow_connector
    ):
        """The behavioural check, driven through the sync path rather than the
        resolver: when sys_properties cannot be read at all, contributors are held
        to an article's own criteria (they get no grant of their own on it).
        """
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(403, "no rights on sys_properties", None)
        )
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        servicenow_connector.apply_article_read_criteria = (
            await servicenow_connector._resolve_article_read_criteria()
        )

        servicenow_connector._process_criteria_permissions = AsyncMock(return_value=[])
        servicenow_connector._kb_privileged_users = AsyncMock(return_value=set())
        servicenow_connector._kb_contributor_grants = AsyncMock(side_effect=AssertionError("contributors not held"))
        access = await servicenow_connector._article_access(
            KBKnowledge(
                sys_id="art1", number="KB-art1",
                short_description="Restricted",
                kb_category="cat1",
                can_read_user_criteria="crit1",
            )
        )
        assert access == (True, AccessRule.RESTRICTED, [])

    @pytest.mark.asyncio
    async def test_a_failed_portal_read_still_falls_back(self, servicenow_connector):
        """The portal suffix only affects how a link looks, so it keeps falling
        back instead of leaning anywhere."""
        servicenow_connector._read_instance_property = AsyncMock(
            side_effect=ServiceNowAPIError(403, "forbidden", None)
        )
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=RuntimeError("no portal either"))
        servicenow_connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        assert await servicenow_connector._resolve_kb_portal_suffix() == "kb"


class TestArticleReadCriteriaOverride:
    """An article's own Can Read narrows its knowledge base for readers under both
    values of glide.knowman.apply_article_read_criteria (verified on a Zurich
    instance with personas); the property only decides whether contributors are
    held to it too."""

    def _article(self):
        return KBKnowledge(
            sys_id="art1", number="KB-art1",
            short_description="Restricted",
            kb_category="cat1",
            can_read_user_criteria="crit1",
        )

    def test_article_inherits_the_base_grant_by_default(self, servicenow_connector):
        servicenow_connector.apply_article_read_criteria = False

        record = servicenow_connector._transform_to_article_webpage_record(self._article())

        assert record.inherit_permissions is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("applies", [False, True])
    async def test_own_criteria_narrow_the_kb_under_both_property_values(self, servicenow_connector, applies):
        servicenow_connector.apply_article_read_criteria = applies
        servicenow_connector._process_criteria_permissions = AsyncMock(return_value=[])
        servicenow_connector._kb_privileged_users = AsyncMock(return_value=set())
        servicenow_connector._kb_contributor_grants = AsyncMock(return_value=[])

        inherit, rule, _ = await servicenow_connector._article_access(self._article())

        assert (inherit, rule) == (True, AccessRule.RESTRICTED)

    def test_an_article_without_criteria_always_inherits(self, servicenow_connector):
        servicenow_connector.apply_article_read_criteria = True
        article = self._article()
        article.can_read_user_criteria = ""

        record = servicenow_connector._transform_to_article_webpage_record(article)

        assert record.inherit_permissions is True

    def test_the_attachment_follows_its_article(self, servicenow_connector):
        record = servicenow_connector._transform_to_attachment_file_record(
            AttachmentMetadata(
                sys_id="att1",
                file_name="doc.pdf",
                content_type="application/pdf",
                size_bytes="1024",
                table_sys_id="art1",
                sys_created_on="2026-08-01 10:00:00",
                sys_updated_on="2026-08-01 10:00:00",
            ),
            parent_record_group_type=RecordGroupType.SERVICENOW_CATEGORY,
            parent_external_record_group_id="cat1",
            inherit_permissions=False,
        )

        assert record.inherit_permissions is False


# ===========================================================================
# Token refresh reaches the transport
# ===========================================================================

class TestAccessTokenRefresh:
    def test_set_access_token_rewrites_the_authorization_header(self):
        """The transport sends headers, so a token written anywhere else is inert."""
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            instance_url="https://example.service-now.com",
            client_id="cid",
            client_secret="secret",
            redirect_uri="http://localhost/cb",
            access_token="first-token",
        )
        assert client.headers["Authorization"] == "Bearer first-token"

        client.set_access_token("second-token")

        assert client.access_token == "second-token"
        assert client.headers["Authorization"] == "Bearer second-token"
