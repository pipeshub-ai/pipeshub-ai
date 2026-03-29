"""Extended coverage tests for ServiceNow connector."""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.connectors.sources.servicenow.servicenow.connector import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowConnector,
)
from app.models.entities import AppUser, RecordType, WebpageRecord, FileRecord


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
    return logging.getLogger("test.servicenow_cov")


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
            connector_id="sn-cov-1",
        )
    return c


# ===========================================================================
# Organizational entities config
# ===========================================================================
class TestOrganizationalEntities:
    def test_has_all_entity_types(self):
        assert "company" in ORGANIZATIONAL_ENTITIES
        assert "department" in ORGANIZATIONAL_ENTITIES
        assert "location" in ORGANIZATIONAL_ENTITIES
        assert "cost_center" in ORGANIZATIONAL_ENTITIES

    def test_entity_has_required_fields(self):
        for entity_type, config in ORGANIZATIONAL_ENTITIES.items():
            assert "table" in config
            assert "fields" in config
            assert "prefix" in config
            assert "sync_point_key" in config


# ===========================================================================
# Initialization
# ===========================================================================
class TestServiceNowInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
        })
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_no_oauth_config_found(self, mock_fetch, connector):
        mock_fetch.return_value = None
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_incomplete_config(self, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                # Missing clientSecret, instanceUrl, redirectUri
            }
        }
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_no_access_token(self, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                "clientSecret": "cs",
                "instanceUrl": "https://instance.service-now.com",
                "redirectUri": "http://localhost/callback",
            }
        }
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_connection_test_fails(self, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                "clientSecret": "cs",
                "instanceUrl": "https://instance.service-now.com",
                "redirectUri": "http://localhost/callback",
            }
        }
        connector.test_connection_and_access = AsyncMock(return_value=False)
        assert await connector.init() is False

    @pytest.mark.asyncio
    @patch("app.connectors.sources.servicenow.servicenow.connector.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_exception(self, mock_fetch, connector):
        mock_fetch.side_effect = Exception("network error")
        assert await connector.init() is False


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
    async def test_no_config_raises(self, connector):
        connector.servicenow_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(Exception, match="not found"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_no_token_raises(self, connector):
        connector.servicenow_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {}
        })
        with pytest.raises(Exception, match="No access token"):
            await connector._get_fresh_datasource()

    @pytest.mark.asyncio
    async def test_updates_token_when_changed(self, connector):
        connector.servicenow_client = MagicMock()
        connector.servicenow_client.access_token = "old-token"
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "new-token"}
        })
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource"):
            ds = await connector._get_fresh_datasource()
        assert connector.servicenow_client.access_token == "new-token"


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestServiceNowTestConnection:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector.servicenow_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(True, {"result": [{"sys_id": "1"}]})
        )
        connector._get_fresh_datasource.return_value = mock_ds
        assert await connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, connector):
        connector.servicenow_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(False, error="Unauthorized")
        )
        connector._get_fresh_datasource.return_value = mock_ds
        assert await connector.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.servicenow_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("boom"))
        assert await connector.test_connection_and_access() is False


# ===========================================================================
# stream_record
# ===========================================================================
class TestServiceNowStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_webpage_record(self, connector):
        connector._fetch_article_content = AsyncMock(return_value="<h1>Test</h1>")
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Test Article"
        record.external_record_id = "sys-123"
        record.mime_type = "text/html"
        record.id = "r-1"
        response = await connector.stream_record(record)
        assert response.media_type == "text/html"

    @pytest.mark.asyncio
    async def test_stream_file_record(self, connector):
        connector._fetch_attachment_content = AsyncMock(return_value=b"file-bytes")
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "attachment.pdf"
        record.external_record_id = "att-123"
        record.mime_type = "application/pdf"
        record.id = "r-2"
        response = await connector.stream_record(record)
        assert response is not None

    @pytest.mark.asyncio
    async def test_stream_unsupported_type(self, connector):
        record = MagicMock()
        record.record_type = "UNKNOWN"
        record.record_name = "bad"
        record.external_record_id = "bad-1"
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_stream_exception(self, connector):
        connector._fetch_article_content = AsyncMock(side_effect=RuntimeError("db"))
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Test"
        record.external_record_id = "sys-1"
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# _fetch_article_content
# ===========================================================================
class TestFetchArticleContent:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(True, {
                "result": [{"sys_id": "1", "text": "<p>Hello</p>", "short_description": "Test"}]
            })
        )
        connector._get_fresh_datasource.return_value = mock_ds
        content = await connector._fetch_article_content("sys-1")
        assert "<p>Hello</p>" in content

    @pytest.mark.asyncio
    async def test_empty_content(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(True, {
                "result": [{"sys_id": "1", "text": ""}]
            })
        )
        connector._get_fresh_datasource.return_value = mock_ds
        content = await connector._fetch_article_content("sys-1")
        assert "No content" in content

    @pytest.mark.asyncio
    async def test_no_result(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(True, {"result": []})
        )
        connector._get_fresh_datasource.return_value = mock_ds
        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_article_content("sys-missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_api_failure(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_make_api_response(False)
        )
        connector._get_fresh_datasource.return_value = mock_ds
        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_article_content("sys-1")
        assert exc_info.value.status_code == 404


# ===========================================================================
# _fetch_attachment_content
# ===========================================================================
class TestFetchAttachmentContent:
    @pytest.mark.asyncio
    async def test_success(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.download_attachment = AsyncMock(return_value=b"file-content")
        connector._get_fresh_datasource.return_value = mock_ds
        content = await connector._fetch_attachment_content("att-1")
        assert content == b"file-content"

    @pytest.mark.asyncio
    async def test_empty_content(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.download_attachment = AsyncMock(return_value=None)
        connector._get_fresh_datasource.return_value = mock_ds
        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_attachment_content("att-missing")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.download_attachment = AsyncMock(side_effect=RuntimeError("net"))
        connector._get_fresh_datasource.return_value = mock_ds
        with pytest.raises(HTTPException) as exc_info:
            await connector._fetch_attachment_content("att-1")
        assert exc_info.value.status_code == 500


# ===========================================================================
# Other methods
# ===========================================================================
class TestServiceNowMisc:
    def test_get_signed_url_returns_none(self, connector):
        record = MagicMock()
        assert connector.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_handle_webhook_returns_true(self, connector):
        result = await connector.handle_webhook_notification("org-1", {"type": "test"})
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
            await connector.get_filter_options("key")

    @pytest.mark.asyncio
    async def test_run_incremental_sync_delegates(self, connector):
        connector.run_sync = AsyncMock()
        await connector.run_incremental_sync()
        connector.run_sync.assert_awaited_once()


# ===========================================================================
# run_sync
# ===========================================================================
class TestServiceNowRunSync:
    @pytest.mark.asyncio
    async def test_no_client_raises(self, connector):
        connector.servicenow_client = None
        with pytest.raises(Exception, match="not initialized"):
            await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_orchestration(self, connector):
        connector.servicenow_client = MagicMock()
        connector._sync_users_and_groups = AsyncMock()
        connector._get_admin_users = AsyncMock(return_value=[])
        connector._sync_knowledge_bases = AsyncMock()
        connector._sync_categories = AsyncMock()
        connector._sync_articles = AsyncMock()
        await connector.run_sync()
        connector._sync_users_and_groups.assert_awaited_once()
        connector._sync_knowledge_bases.assert_awaited_once()
        connector._sync_categories.assert_awaited_once()
        connector._sync_articles.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_sync_with_admin_users(self, connector):
        connector.servicenow_client = MagicMock()
        admin_user = MagicMock()
        connector._sync_users_and_groups = AsyncMock()
        connector._get_admin_users = AsyncMock(return_value=[admin_user])
        connector._sync_knowledge_bases = AsyncMock()
        connector._sync_categories = AsyncMock()
        connector._sync_articles = AsyncMock()
        await connector.run_sync()
        connector._sync_knowledge_bases.assert_awaited_once()
