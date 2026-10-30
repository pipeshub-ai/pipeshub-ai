"""Extended coverage tests for ServiceNow connector."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from servicenow_fakes import block_groups, file_response

from app.config.constants.arangodb import MimeTypes
from app.connectors.sources.servicenow.servicenow.connector import (
    ORGANIZATIONAL_ENTITIES,
    ServiceNowConnector,
)
from app.models.entities import AppUser, RecordType, WebpageRecord, FileRecord
from app.sources.external.servicenow.models import (
    ServiceNowAPIError,
    TableAPIRecord,
    TableAPIResponse,
)
from app.connectors.core.base.connector.connector_service import ConnectorInitError


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


def _table_api_response(records: list, total: int | None = None) -> TableAPIResponse:
    # total: X-Total-Count, the rows the query matches from here on (default: these rows)
    return TableAPIResponse(result=[TableAPIRecord(**r) for r in records],
                            total_count=len(records) if total is None else total)


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.servicenow_cov")


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
    proc.full_sync_running = MagicMock(return_value=False)
    proc.reindex_existing_records = AsyncMock()
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
            scope="personal",
            created_by="test-user-id",
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
        assert "cost_center" not in ORGANIZATIONAL_ENTITIES  # no such user_criteria field


# ===========================================================================
# Initialization
# ===========================================================================
class TestServiceNowInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ConnectorInitError, match="no saved settings"):
            await connector.init()

    @pytest.mark.asyncio
    async def test_init_no_oauth_config_id(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {},
        })
        with pytest.raises(ConnectorInitError, match="not linked to an OAuth app"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_no_oauth_config_found(self, mock_fetch, connector):
        mock_fetch.return_value = None
        with pytest.raises(ConnectorInitError, match="was not found"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_incomplete_config(self, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                # Missing clientSecret, instanceUrl, redirectUri
            }
        }
        with pytest.raises(ConnectorInitError, match="missing the instance URL"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_no_access_token(self, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                "clientSecret": "cs",
                "instanceUrl": "https://instance.service-now.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"oauthConfigId": "oauth-1"},
            "credentials": {},
        })
        with pytest.raises(ConnectorInitError, match="not authorized yet"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
    async def test_init_connection_test_fails(self, mock_fetch, connector):
        mock_fetch.return_value = {
            "config": {
                "clientId": "cid",
                "clientSecret": "cs",
                "instanceUrl": "https://instance.service-now.com",
            },
            "redirectUri": "http://localhost/callback",
        }
        connector.test_connection_and_access = AsyncMock(side_effect=ConnectorInitError("cannot read user_criteria"))
        with pytest.raises(ConnectorInitError, match="cannot read user_criteria"):
            await connector.init()

    @pytest.mark.asyncio
    @patch("app.utils.oauth_config.fetch_oauth_config_by_id", new_callable=AsyncMock)
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
        with pytest.raises(HTTPException) as exc_info:
            await connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_no_config_raises(self, connector):
        connector.servicenow_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as exc_info:
            await connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_no_token_raises(self, connector):
        connector.servicenow_client = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {}
        })
        with pytest.raises(HTTPException) as exc_info:
            await connector._get_fresh_datasource()
        assert exc_info.value.status_code == 409

    @pytest.mark.asyncio
    async def test_updates_token_when_changed(self, connector):
        connector.servicenow_client = MagicMock()
        connector.servicenow_client.access_token = "old-token"
        connector.config_service.get_config = AsyncMock(return_value={
            "credentials": {"access_token": "new-token"}
        })
        with patch("app.connectors.sources.servicenow.servicenow.connector.ServiceNowDataSource"):
            ds = await connector._get_fresh_datasource()
        # The transport authenticates from headers, so a refreshed token that
        # only reaches the attribute leaves every request on the stale one.
        connector.servicenow_client.set_access_token.assert_called_once_with("new-token")


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
            return_value=_table_api_response([{"sys_id": "1"}])
        )
        connector._get_fresh_datasource.return_value = mock_ds
        assert await connector.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, connector):
        connector.servicenow_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            side_effect=ServiceNowAPIError(403, "User Not Authorized", None)
        )
        connector._get_fresh_datasource.return_value = mock_ds
        with pytest.raises(ConnectorInitError, match="answered HTTP 403"):
            await connector.test_connection_and_access()

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector.servicenow_client = MagicMock()
        connector._get_fresh_datasource = AsyncMock(side_effect=Exception("boom"))
        with pytest.raises(Exception, match="boom"):
            await connector.test_connection_and_access()


# ===========================================================================
# stream_record
# ===========================================================================
class TestServiceNowStreamRecord:
    @pytest.mark.asyncio
    async def test_stream_webpage_record(self, connector):
        connector._fetch_article_content = AsyncMock(return_value=b'{"block_groups": []}')
        record = MagicMock()
        record.record_type = RecordType.WEBPAGE
        record.record_name = "Test Article"
        record.external_record_id = "sys-123"
        record.mime_type = "text/html"
        record.id = "r-1"
        response = await connector.stream_record(record)
        assert response.media_type == MimeTypes.BLOCKS.value

    @pytest.mark.asyncio
    async def test_stream_file_record(self, connector):
        async def chunks() -> AsyncIterator[bytes]:
            yield b"file-"
            yield b"bytes"

        connector._open_attachment = AsyncMock(return_value=chunks())
        connector._current_attachment = AsyncMock(return_value="att-123")
        record = MagicMock()
        record.record_type = RecordType.FILE
        record.record_name = "attachment.pdf"
        record.external_record_id = "att-123"
        record.mime_type = "application/pdf"
        record.id = "r-2"
        response = await connector.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body == b"file-bytes"
        assert response.media_type == "application/pdf"
        connector._open_attachment.assert_awaited_once_with("att-123", is_html=False)

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
            return_value=_table_api_response([
                {"sys_id": "sys-1", "text": "<p>Hello</p>", "short_description": "Test"},
            ])
        )
        connector._get_fresh_datasource.return_value = mock_ds
        content = await connector._fetch_article_content("sys-1")
        assert block_groups(content)[0]["data"].startswith("# Test\n\n")
        assert block_groups(content)[0]["data"].endswith("Hello")

    @pytest.mark.asyncio
    async def test_empty_content(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_table_api_response([{"sys_id": "sys-1", "text": ""}])
        )
        connector._get_fresh_datasource.return_value = mock_ds
        content = await connector._fetch_article_content("sys-1")
        assert b"No content" not in content

    @pytest.mark.asyncio
    async def test_no_result(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.get_now_table_tableName = AsyncMock(
            return_value=_table_api_response([])
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
            side_effect=ServiceNowAPIError(404, "Not found", None)
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
        mock_ds.stream_attachment = AsyncMock(return_value=file_response(b"file-content"))
        connector._get_fresh_datasource.return_value = mock_ds
        chunks = await connector._open_attachment("att-1")
        assert b"".join([c async for c in chunks]) == b"file-content"

    @pytest.mark.asyncio
    async def test_html_page_for_a_pdf_is_rejected(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.stream_attachment = AsyncMock(return_value=file_response(b"<html>sign in</html>", "text/html"))
        connector._get_fresh_datasource.return_value = mock_ds
        with pytest.raises(HTTPException) as exc_info:
            await connector._open_attachment("att-1")  # a PDF answered with an HTML page
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_exception(self, connector):
        connector._get_fresh_datasource = AsyncMock()
        mock_ds = AsyncMock()
        mock_ds.stream_attachment = AsyncMock(side_effect=RuntimeError("net"))
        connector._get_fresh_datasource.return_value = mock_ds
        record = MagicMock(record_type=RecordType.FILE, external_record_id="att-1", mime_type="application/pdf")
        with pytest.raises(HTTPException) as exc_info:
            await connector.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# Other methods
# ===========================================================================
class TestServiceNowMisc:
    def test_get_signed_url_returns_none(self, connector):
        record = MagicMock()
        assert connector.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_cleanup(self, connector):
        client = connector.servicenow_client = MagicMock(close=AsyncMock())
        connector.servicenow_datasource = MagicMock()
        await connector.cleanup()
        client.close.assert_awaited_once()  # the connection pool is closed, not just dropped
        assert connector.servicenow_client is None
        assert connector.servicenow_datasource is None

    @pytest.mark.asyncio
    async def test_reindex_records(self, connector):
        await connector.reindex_records([])  # Should not raise

    @pytest.mark.asyncio
    async def test_get_filter_options_unknown_key_raises(self, connector):
        with pytest.raises(ValueError, match="Unsupported filter key"):
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
        connector._read_access_state = AsyncMock(return_value=(None, True))
        connector._sync_users_and_groups = AsyncMock()
        connector._get_admin_users = AsyncMock(return_value=set())
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
        connector._read_access_state = AsyncMock(return_value=(None, True))
        connector._sync_users_and_groups = AsyncMock()
        connector._get_admin_users = AsyncMock(return_value={"admin-1"})
        connector._sync_knowledge_bases = AsyncMock()
        connector._sync_categories = AsyncMock()
        connector._sync_articles = AsyncMock()
        await connector.run_sync()
        connector._sync_knowledge_bases.assert_awaited_once()
