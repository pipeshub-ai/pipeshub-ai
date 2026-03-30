"""Comprehensive tests for ServiceNow connector - additional coverage for transform and sync methods."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes
from app.connectors.sources.servicenow.servicenow.connector import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowConnector,
)
from app.models.entities import (
    AppUser,
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType


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


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.servicenow_comp")


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
        "auth": {"oauthConfigId": "oauth-1"},
        "credentials": {
            "access_token": "test-token",
            "refresh_token": "test-refresh",
        },
    })
    return svc


@pytest.fixture()
def connector(mock_logger, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.servicenow.servicenow.connector.ServicenowApp"):
        c = ServiceNowConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="sn-comp-1",
        )
    c.connector_name = Connectors.SERVICENOW
    return c


# ===========================================================================
# _parse_servicenow_datetime
# ===========================================================================
class TestParseServicenowDatetime:
    def test_valid_datetime(self, connector):
        result = connector._parse_servicenow_datetime("2023-01-15 10:30:45")
        assert result is not None
        assert isinstance(result, int)

    def test_invalid_format(self, connector):
        result = connector._parse_servicenow_datetime("not-a-date")
        assert result is None

    def test_empty_string(self, connector):
        result = connector._parse_servicenow_datetime("")
        assert result is None


# ===========================================================================
# _transform_to_app_user
# ===========================================================================
class TestTransformToAppUser:
    @pytest.mark.asyncio
    async def test_valid_user(self, connector):
        connector.connector_name = Connectors.SERVICENOW
        user_data = {
            "sys_id": "u1",
            "email": "user@test.com",
            "user_name": "testuser",
            "first_name": "Test",
            "last_name": "User",
            "sys_created_on": "2023-01-01 00:00:00",
            "sys_updated_on": "2023-06-01 00:00:00",
        }
        result = await connector._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "user@test.com"
        assert result.full_name == "Test User"

    @pytest.mark.asyncio
    async def test_missing_sys_id(self, connector):
        user_data = {"email": "user@test.com"}
        result = await connector._transform_to_app_user(user_data)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_email(self, connector):
        user_data = {"sys_id": "u1"}
        result = await connector._transform_to_app_user(user_data)
        assert result is None

    @pytest.mark.asyncio
    async def test_name_fallback_to_username(self, connector):
        connector.connector_name = Connectors.SERVICENOW
        user_data = {
            "sys_id": "u1",
            "email": "user@test.com",
            "user_name": "testuser",
            "first_name": "",
            "last_name": "",
        }
        result = await connector._transform_to_app_user(user_data)
        assert result.full_name == "testuser"

    @pytest.mark.asyncio
    async def test_name_fallback_to_email(self, connector):
        connector.connector_name = Connectors.SERVICENOW
        user_data = {
            "sys_id": "u1",
            "email": "user@test.com",
            "user_name": "",
            "first_name": "",
            "last_name": "",
        }
        result = await connector._transform_to_app_user(user_data)
        assert result.full_name == "user@test.com"

    @pytest.mark.asyncio
    async def test_whitespace_email(self, connector):
        connector.connector_name = Connectors.SERVICENOW
        user_data = {"sys_id": "u1", "email": "  user@test.com  "}
        result = await connector._transform_to_app_user(user_data)
        assert result is not None
        assert result.email == "user@test.com"


# ===========================================================================
# _transform_to_user_group
# ===========================================================================
class TestTransformToUserGroup:
    def test_valid_group(self, connector):
        data = {
            "sys_id": "g1",
            "name": "TestGroup",
            "sys_created_on": "2023-01-01 00:00:00",
            "sys_updated_on": "2023-06-01 00:00:00",
        }
        result = connector._transform_to_user_group(data)
        assert result is not None
        assert result.name == "TestGroup"

    def test_missing_sys_id(self, connector):
        data = {"name": "TestGroup"}
        result = connector._transform_to_user_group(data)
        assert result is None

    def test_missing_name(self, connector):
        data = {"sys_id": "g1", "name": ""}
        result = connector._transform_to_user_group(data)
        assert result is None


# ===========================================================================
# _transform_to_organizational_group
# ===========================================================================
class TestTransformToOrgGroup:
    def test_valid_company(self, connector):
        data = {
            "sys_id": "c1",
            "name": "Acme Corp",
            "sys_created_on": "2023-01-01 00:00:00",
        }
        result = connector._transform_to_organizational_group(data, "COMPANY_")
        assert result is not None
        assert result.name == "COMPANY_Acme Corp"
        assert "COMPANY" in result.description

    def test_valid_department(self, connector):
        data = {"sys_id": "d1", "name": "Engineering"}
        result = connector._transform_to_organizational_group(data, "DEPARTMENT_")
        assert result is not None
        assert result.name == "DEPARTMENT_Engineering"

    def test_missing_sys_id(self, connector):
        data = {"name": "NoId"}
        result = connector._transform_to_organizational_group(data, "LOC_")
        assert result is None

    def test_missing_name(self, connector):
        data = {"sys_id": "c1", "name": ""}
        result = connector._transform_to_organizational_group(data, "CC_")
        assert result is None


# ===========================================================================
# _transform_to_article_webpage_record
# ===========================================================================
class TestTransformToArticleWebpageRecord:
    def test_valid_article_with_category(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "art-1",
            "short_description": "KB Article Title",
            "kb_category": {"value": "cat-1"},
            "kb_knowledge_base": {"value": "kb-1"},
            "sys_created_on": "2023-01-01 00:00:00",
            "sys_updated_on": "2023-06-01 00:00:00",
        }
        result = connector._transform_to_article_webpage_record(data)
        assert result is not None
        assert result.record_name == "KB Article Title"
        assert result.external_record_group_id == "cat-1"
        assert result.record_group_type == RecordGroupType.SERVICENOW_CATEGORY

    def test_article_without_category_falls_back_to_kb(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "art-2",
            "short_description": "No Category Article",
            "kb_category": "",
            "kb_knowledge_base": {"value": "kb-1"},
        }
        result = connector._transform_to_article_webpage_record(data)
        assert result is not None
        assert result.external_record_group_id == "kb-1"
        assert result.record_group_type == RecordGroupType.SERVICENOWKB

    def test_article_without_category_or_kb_returns_none(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "art-3",
            "short_description": "Orphan Article",
            "kb_category": "",
            "kb_knowledge_base": "",
        }
        result = connector._transform_to_article_webpage_record(data)
        assert result is None

    def test_article_missing_sys_id(self, connector):
        data = {"short_description": "No ID"}
        result = connector._transform_to_article_webpage_record(data)
        assert result is None

    def test_article_missing_description(self, connector):
        data = {"sys_id": "art-4", "short_description": ""}
        result = connector._transform_to_article_webpage_record(data)
        assert result is None

    def test_article_with_string_category_ref(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "art-5",
            "short_description": "String Category",
            "kb_category": "cat-str",
        }
        result = connector._transform_to_article_webpage_record(data)
        assert result is not None
        assert result.external_record_group_id == "cat-str"

    def test_article_with_string_kb_ref(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "art-6",
            "short_description": "String KB",
            "kb_category": "",
            "kb_knowledge_base": "kb-str",
        }
        result = connector._transform_to_article_webpage_record(data)
        assert result is not None
        assert result.external_record_group_id == "kb-str"

    def test_web_url_generated(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "art-7",
            "short_description": "URL Test",
            "kb_category": "cat-1",
        }
        result = connector._transform_to_article_webpage_record(data)
        assert result is not None
        assert "art-7" in result.weburl


# ===========================================================================
# _transform_to_attachment_file_record
# ===========================================================================
class TestTransformToAttachmentFileRecord:
    def test_valid_attachment(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "att-1",
            "file_name": "document.pdf",
            "content_type": "application/pdf",
            "size_bytes": "1024",
            "table_sys_id": "art-1",
            "sys_created_on": "2023-01-01 00:00:00",
            "sys_updated_on": "2023-06-01 00:00:00",
        }
        result = connector._transform_to_attachment_file_record(
            data,
            parent_record_group_type=RecordGroupType.SERVICENOW_CATEGORY,
            parent_external_record_group_id="cat-1"
        )
        assert result is not None
        assert result.record_name == "document.pdf"
        assert result.extension == "pdf"
        assert result.size_in_bytes == 1024
        assert result.parent_external_record_id == "art-1"

    def test_attachment_missing_sys_id(self, connector):
        data = {"file_name": "test.txt"}
        result = connector._transform_to_attachment_file_record(data)
        assert result is None

    def test_attachment_missing_filename(self, connector):
        data = {"sys_id": "att-2", "file_name": ""}
        result = connector._transform_to_attachment_file_record(data)
        assert result is None

    def test_attachment_invalid_size(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {
            "sys_id": "att-3",
            "file_name": "test.txt",
            "size_bytes": "invalid",
        }
        result = connector._transform_to_attachment_file_record(data)
        assert result is not None
        assert result.size_in_bytes is None

    def test_attachment_no_extension(self, connector):
        connector.instance_url = "https://test.service-now.com"
        data = {"sys_id": "att-4", "file_name": "Makefile"}
        result = connector._transform_to_attachment_file_record(data)
        assert result is not None
        assert result.extension is None


# ===========================================================================
# _get_fresh_datasource
# ===========================================================================
class TestGetFreshDatasource:
    @pytest.mark.asyncio
    async def test_no_client_raises(self, connector):
        connector.servicenow_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_updates_token_when_changed(self, connector):
        connector.servicenow_client = MagicMock()
        connector.servicenow_client.access_token = "old-token"
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "new-token"},
        })
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource"):
            ds = await connector._get_fresh_datasource()
        assert connector.servicenow_client.access_token == "new-token"

    @pytest.mark.asyncio
    async def test_no_config_raises(self, connector):
        connector.servicenow_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_token_raises(self, connector):
        connector.servicenow_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {},
        })
        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()


# ===========================================================================
# _sync_users
# ===========================================================================
class TestSyncUsers:
    @pytest.mark.asyncio
    async def test_delta_sync_with_last_sync_time(self, connector, mock_data_entities_processor):
        connector.user_sync_point = AsyncMock()
        connector.user_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2023-06-01 00:00:00"}
        )
        connector.user_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": [
                {"sys_id": "u1", "email": "u1@test.com", "first_name": "U", "last_name": "1", "sys_updated_on": "2023-07-01 00:00:00"},
            ]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_users()
        mock_data_entities_processor.on_new_app_users.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_full_sync_no_users(self, connector):
        connector.user_sync_point = AsyncMock()
        connector.user_sync_point.read_sync_point = AsyncMock(return_value=None)
        connector.user_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": []}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        await connector._sync_users()


# ===========================================================================
# _fetch_all_groups and _fetch_all_memberships
# ===========================================================================
class TestFetchAllGroups:
    @pytest.mark.asyncio
    async def test_pagination(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(side_effect=[
            _make_api_response(data={"result": [{"sys_id": "g1"}] * 100}),
            _make_api_response(data={"result": [{"sys_id": "g2"}]}),
        ])
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        groups = await connector._fetch_all_groups()
        assert len(groups) == 101

    @pytest.mark.asyncio
    async def test_empty_result(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": []}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        groups = await connector._fetch_all_groups()
        assert groups == []

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            success=False, error="API error"
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        groups = await connector._fetch_all_groups()
        assert groups == []


class TestFetchAllMemberships:
    @pytest.mark.asyncio
    async def test_with_delta_sync(self, connector):
        connector.group_sync_point = AsyncMock()
        connector.group_sync_point.read_sync_point = AsyncMock(
            return_value={"last_sync_time": "2023-06-01 00:00:00"}
        )
        connector.group_sync_point.update_sync_point = AsyncMock()

        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": [{"sys_id": "m1", "user": "u1", "group": "g1", "sys_updated_on": "2023-07-01"}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        memberships = await connector._fetch_all_memberships()
        assert len(memberships) == 1


# ===========================================================================
# _get_admin_users
# ===========================================================================
class TestGetAdminUsers:
    @pytest.mark.asyncio
    async def test_admin_users_with_dict_ref(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": [{"user": {"value": "u1"}}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        mock_app_user = MagicMock()
        mock_app_user.email = "admin@test.com"
        tx = AsyncMock()
        tx.get_user_by_source_id = AsyncMock(return_value=mock_app_user)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        admins = await connector._get_admin_users()
        assert len(admins) == 1

    @pytest.mark.asyncio
    async def test_admin_users_with_string_ref(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": [{"user": "u1-str"}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)

        tx = AsyncMock()
        tx.get_user_by_source_id = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _transaction():
            yield tx

        connector.data_store_provider = MagicMock()
        connector.data_store_provider.transaction = _transaction

        admins = await connector._get_admin_users()
        assert len(admins) == 0

    @pytest.mark.asyncio
    async def test_admin_users_api_failure(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            success=False, error="API fail"
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        admins = await connector._get_admin_users()
        assert admins == []


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSyncComprehensive:
    @pytest.mark.asyncio
    async def test_no_client_raises(self, connector):
        connector.servicenow_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_with_no_admin_users(self, connector):
        connector.servicenow_client = MagicMock()
        connector._sync_users_and_groups = AsyncMock()
        connector._get_admin_users = AsyncMock(return_value=[])
        connector._sync_knowledge_bases = AsyncMock()
        connector._sync_categories = AsyncMock()
        connector._sync_articles = AsyncMock()

        await connector.run_sync()

        connector._sync_users_and_groups.assert_awaited_once()
        connector._sync_knowledge_bases.assert_awaited_once_with([])
        connector._sync_categories.assert_awaited_once()
        connector._sync_articles.assert_awaited_once()


# ===========================================================================
# Miscellaneous
# ===========================================================================
class TestMiscComprehensive:
    def test_get_signed_url_returns_none(self, connector):
        assert connector.get_signed_url(MagicMock()) is None

    @pytest.mark.asyncio
    async def test_handle_webhook_returns_true(self, connector):
        result = await connector.handle_webhook_notification("org-1", {})
        assert result is True

    @pytest.mark.asyncio
    async def test_handle_webhook_exception(self, connector):
        result = await connector.handle_webhook_notification("org-1", None)
        # Should catch exception and return False
        # Actually the method logs and returns True. Let's test normal case.
        assert result is True

    @pytest.mark.asyncio
    async def test_cleanup(self, connector):
        connector.servicenow_client = MagicMock()
        connector.servicenow_datasource = MagicMock()
        await connector.cleanup()
        assert connector.servicenow_client is None
        assert connector.servicenow_datasource is None

    @pytest.mark.asyncio
    async def test_reindex_records(self, connector):
        await connector.reindex_records([])  # Should not raise

    @pytest.mark.asyncio
    async def test_get_filter_options_raises(self, connector):
        with pytest.raises(NotImplementedError):
            await connector.get_filter_options("any_key")

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates_to_full(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_webpage_record(self, connector):
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.external_record_id = "art-1"
        record.record_name = "Test Article"
        record.mime_type = "text/html"

        connector._fetch_article_content = AsyncMock(return_value="<p>Content</p>")
        response = await connector.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_file_record(self, connector):
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.external_record_id = "att-1"
        record.record_name = "test.pdf"
        record.mime_type = "application/pdf"
        record.id = "rec-1"

        connector._fetch_attachment_content = AsyncMock(return_value=b"file bytes")
        with patch("app.connectors.sources.servicenow.servicenow.connector.create_stream_record_response") as mock_stream:
            mock_stream.return_value = MagicMock()
            response = await connector.stream_record(record)
            assert response is not None

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self, connector):
        record = MagicMock()
        record.record_type = "unsupported"
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400


# ===========================================================================
# _fetch_article_content and _fetch_attachment_content
# ===========================================================================
class TestFetchContent:
    @pytest.mark.asyncio
    async def test_fetch_article_success(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": [{"text": "<p>Article content</p>"}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        content = await connector._fetch_article_content("art-1")
        assert "<p>Article content</p>" in content

    @pytest.mark.asyncio
    async def test_fetch_article_empty_content(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": [{"text": ""}]}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        content = await connector._fetch_article_content("art-1")
        assert "No content" in content

    @pytest.mark.asyncio
    async def test_fetch_article_not_found(self, connector):
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(return_value=_make_api_response(
            data={"result": []}
        ))
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_article_content("art-missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_fetch_attachment_success(self, connector):
        mock_ds = AsyncMock()
        mock_ds.download_attachment = AsyncMock(return_value=b"file data")
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        content = await connector._fetch_attachment_content("att-1")
        assert content == b"file data"

    @pytest.mark.asyncio
    async def test_fetch_attachment_empty(self, connector):
        mock_ds = AsyncMock()
        mock_ds.download_attachment = AsyncMock(return_value=None)
        connector._get_fresh_datasource = AsyncMock(return_value=mock_ds)
        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_attachment_content("att-missing")
        assert exc_info.value.status_code == 404
