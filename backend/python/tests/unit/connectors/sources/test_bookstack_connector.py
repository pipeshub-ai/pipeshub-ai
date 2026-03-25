"""Tests for BookStack connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.bookstack.connector import BookStackConnector, RecordUpdate


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.bookstack")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock()
    proc.org_id = "org-bs-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.on_new_user_groups = AsyncMock()
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "base_url": "https://bookstack.example.com",
            "token_id": "tok-id-1",
            "token_secret": "tok-secret-1",
        },
    })
    return svc


@pytest.fixture()
def bookstack_connector(mock_logger, mock_data_entities_processor,
                        mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.bookstack.connector.BookStackApp"):
        connector = BookStackConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="bs-conn-1",
        )
    return connector


# ===========================================================================
# RecordUpdate dataclass
# ===========================================================================

class TestRecordUpdate:
    def test_construction(self):
        ru = RecordUpdate(
            record=None,
            is_new=True,
            is_updated=False,
            is_deleted=False,
            metadata_changed=False,
            content_changed=False,
            permissions_changed=False,
        )
        assert ru.is_new is True
        assert ru.external_record_id is None

    def test_with_external_id(self):
        ru = RecordUpdate(
            record=MagicMock(),
            is_new=False,
            is_updated=True,
            is_deleted=False,
            metadata_changed=True,
            content_changed=True,
            permissions_changed=False,
            external_record_id="ext-123",
        )
        assert ru.external_record_id == "ext-123"
        assert ru.is_updated is True


# ===========================================================================
# BookStackConnector
# ===========================================================================

class TestBookStackConnectorInit:
    def test_constructor(self, bookstack_connector):
        assert bookstack_connector.connector_id == "bs-conn-1"
        assert bookstack_connector.data_source is None
        assert bookstack_connector.batch_size == 100

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate", new_callable=AsyncMock)
    @patch("app.connectors.sources.bookstack.connector.BookStackDataSource")
    async def test_init_success(self, mock_ds_cls, mock_build, bookstack_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()

        result = await bookstack_connector.init()
        assert result is True
        assert bookstack_connector.bookstack_base_url == "https://bookstack.example.com"

    async def test_init_fails_no_config(self, bookstack_connector):
        bookstack_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await bookstack_connector.init()
        assert result is False

    async def test_init_fails_missing_credentials(self, bookstack_connector):
        bookstack_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"base_url": "https://bs.example.com"}
        })
        result = await bookstack_connector.init()
        assert result is False

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate", new_callable=AsyncMock)
    async def test_init_fails_client_validation_error(self, mock_build, bookstack_connector):
        mock_build.side_effect = ValueError("Invalid credentials")
        result = await bookstack_connector.init()
        assert result is False

    @patch("app.connectors.sources.bookstack.connector.BookStackClient.build_and_validate", new_callable=AsyncMock)
    async def test_init_fails_general_exception(self, mock_build, bookstack_connector):
        mock_build.side_effect = Exception("Network error")
        result = await bookstack_connector.init()
        assert result is False


class TestBookStackTestConnection:
    async def test_not_initialized(self, bookstack_connector):
        assert bookstack_connector.data_source is None
        result = await bookstack_connector.test_connection_and_access()
        assert result is False

    async def test_success(self, bookstack_connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = True
        mock_ds.list_books = AsyncMock(return_value=mock_response)
        bookstack_connector.data_source = mock_ds

        result = await bookstack_connector.test_connection_and_access()
        assert result is True

    async def test_failure(self, bookstack_connector):
        mock_ds = MagicMock()
        mock_response = MagicMock()
        mock_response.success = False
        mock_response.error = "Forbidden"
        mock_ds.list_books = AsyncMock(return_value=mock_response)
        bookstack_connector.data_source = mock_ds

        result = await bookstack_connector.test_connection_and_access()
        assert result is False

    async def test_exception(self, bookstack_connector):
        mock_ds = MagicMock()
        mock_ds.list_books = AsyncMock(side_effect=Exception("timeout"))
        bookstack_connector.data_source = mock_ds

        result = await bookstack_connector.test_connection_and_access()
        assert result is False


class TestBookStackGetSignedUrl:
    async def test_returns_api_url(self, bookstack_connector):
        bookstack_connector.bookstack_base_url = "https://bs.example.com/"
        record = MagicMock()
        record.external_record_id = "page/42"
        url = await bookstack_connector.get_signed_url(record)
        assert "api/pages/page/42/export/markdown" in url


class TestBookStackGetAppUsers:
    def test_converts_users(self, bookstack_connector):
        users = [
            {"id": 1, "name": "Alice", "email": "alice@test.com"},
            {"id": 2, "name": "Bob", "email": "bob@test.com"},
        ]
        app_users = bookstack_connector._get_app_users(users)
        assert len(app_users) == 2
        assert app_users[0].full_name == "Alice"
        assert app_users[1].email == "bob@test.com"


class TestBookStackGetAllUsers:
    async def test_pagination(self, bookstack_connector):
        mock_ds = MagicMock()
        # First page: 2 users. Second page: empty (end)
        page1 = MagicMock()
        page1.success = True
        page1.data = {"data": [{"id": 1, "name": "A", "email": "a@t.com"},
                                {"id": 2, "name": "B", "email": "b@t.com"}],
                       "total": 2}
        page2 = MagicMock()
        page2.success = True
        page2.data = {"data": [], "total": 2}

        mock_ds.list_users = AsyncMock(side_effect=[page1, page2])
        bookstack_connector.data_source = mock_ds

        result = await bookstack_connector.get_all_users()
        assert len(result) == 2

    async def test_no_users(self, bookstack_connector):
        mock_ds = MagicMock()
        response = MagicMock()
        response.success = True
        response.data = {"data": [], "total": 0}
        mock_ds.list_users = AsyncMock(return_value=response)
        bookstack_connector.data_source = mock_ds

        result = await bookstack_connector.get_all_users()
        assert result == []

    async def test_api_error(self, bookstack_connector):
        mock_ds = MagicMock()
        response = MagicMock()
        response.success = False
        response.data = None
        response.error = "Server Error"
        mock_ds.list_users = AsyncMock(return_value=response)
        bookstack_connector.data_source = mock_ds

        result = await bookstack_connector.get_all_users()
        assert result == []
