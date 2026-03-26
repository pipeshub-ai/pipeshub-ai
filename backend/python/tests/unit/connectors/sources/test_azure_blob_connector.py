"""Tests for Azure Blob Storage connector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.azure_blob.connector import (
    AzureBlobConnector,
    AzureBlobDataSourceEntitiesProcessor,
    get_file_extension,
    get_folder_path_segments_from_blob_name,
    get_mimetype_for_azure_blob,
    get_parent_path_for_azure_blob,
    get_parent_path_from_blob_name,
    get_parent_weburl_for_azure_blob,
    parse_parent_external_id,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_blob")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock(spec=AzureBlobDataSourceEntitiesProcessor)
    proc.org_id = "org-az-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.account_name = "teststorage"
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config_service():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=abc123;EndpointSuffix=core.windows.net"
        },
        "scope": "TEAM",
        "created_by": "user-1",
    })
    return svc


@pytest.fixture()
def azure_blob_connector(mock_logger, mock_data_entities_processor,
                          mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        connector = AzureBlobConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="az-blob-1",
        )
    return connector


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# Helper functions
# ===========================================================================
class TestAzureBlobHelpers:
    def test_get_file_extension(self):
        assert get_file_extension("report.pdf") == "pdf"
        assert get_file_extension("a/b/c/file.docx") == "docx"
        assert get_file_extension("Makefile") is None

    def test_get_parent_path(self):
        assert get_parent_path_from_blob_name("a/b/c/file.txt") == "a/b/c"
        assert get_parent_path_from_blob_name("file.txt") is None
        assert get_parent_path_from_blob_name("") is None
        assert get_parent_path_from_blob_name("a/b/c/") == "a/b"

    def test_folder_segments(self):
        assert get_folder_path_segments_from_blob_name("a/b/c/file.txt") == ["a", "a/b", "a/b/c"]
        assert get_folder_path_segments_from_blob_name("file.txt") == []
        assert get_folder_path_segments_from_blob_name("") == []

    def test_mimetype(self):
        assert get_mimetype_for_azure_blob("folder/", is_folder=True) == MimeTypes.FOLDER.value
        assert get_mimetype_for_azure_blob("report.pdf") == MimeTypes.PDF.value
        assert get_mimetype_for_azure_blob("data.xyz999") == MimeTypes.BIN.value

    def test_parse_parent(self):
        container, path = parse_parent_external_id("mycontainer/path/to/dir")
        assert container == "mycontainer"
        assert path == "path/to/dir/"
        container, path = parse_parent_external_id("mycontainer")
        assert path is None

    def test_parent_weburl(self):
        url = get_parent_weburl_for_azure_blob("container/folder/", "testacc")
        assert "testacc.blob.core.windows.net" in url
        url = get_parent_weburl_for_azure_blob("container", "testacc")
        assert "testacc.blob.core.windows.net/container" in url

    def test_parent_path(self):
        assert get_parent_path_for_azure_blob("container/folder") == "folder/"
        assert get_parent_path_for_azure_blob("container") is None


# ===========================================================================
# Init
# ===========================================================================
class TestAzureBlobConnectorInit:
    def test_constructor(self, azure_blob_connector):
        assert azure_blob_connector.connector_id == "az-blob-1"
        assert azure_blob_connector.data_source is None
        assert azure_blob_connector.batch_size == 100

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSource")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build, azure_blob_connector):
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "teststorage"
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        assert await azure_blob_connector.init() is True

    async def test_init_fails_no_config(self, azure_blob_connector):
        azure_blob_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await azure_blob_connector.init() is False

    async def test_init_fails_no_connection_string(self, azure_blob_connector):
        azure_blob_connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        assert await azure_blob_connector.init() is False

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_exception(self, mock_build, azure_blob_connector):
        mock_build.side_effect = Exception("Connection failed")
        assert await azure_blob_connector.init() is False

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSource")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_account_name_extraction_failure(self, mock_filters, mock_ds_cls, mock_build, azure_blob_connector):
        mock_client = MagicMock()
        mock_client.get_account_name.side_effect = Exception("Cannot extract")
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await azure_blob_connector.init()
        assert result is True
        assert azure_blob_connector.account_name is None

    @patch("app.connectors.sources.azure_blob.connector.AzureBlobClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_blob.connector.AzureBlobDataSource")
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_personal_scope_with_creator(self, mock_filters, mock_ds_cls, mock_build, azure_blob_connector):
        azure_blob_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"azureBlobConnectionString": "AccountName=teststorage;AccountKey=abc"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        mock_client = MagicMock()
        mock_client.get_account_name.return_value = "teststorage"
        mock_build.return_value = mock_client
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await azure_blob_connector.init()
        assert result is True
        assert azure_blob_connector.creator_email == "user@test.com"


# ===========================================================================
# Web URLs
# ===========================================================================
class TestAzureBlobWebUrls:
    def test_generate_web_url(self, azure_blob_connector):
        azure_blob_connector.account_name = "testacc"
        url = azure_blob_connector._generate_web_url("container", "path/file.txt")
        assert "testacc.blob.core.windows.net" in url
        assert "container" in url

    def test_generate_parent_web_url(self, azure_blob_connector):
        azure_blob_connector.account_name = "testacc"
        url = azure_blob_connector._generate_parent_web_url("container/dir")
        assert "testacc.blob.core.windows.net" in url


# ===========================================================================
# App users
# ===========================================================================
class TestAzureBlobAppUsers:
    def test_get_app_users(self, azure_blob_connector):
        from app.models.entities import User
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = azure_blob_connector.get_app_users(users)
        assert len(app_users) == 1

    def test_get_app_users_none_active(self, azure_blob_connector):
        from app.models.entities import User
        users = [User(email="a@test.com", full_name="A", is_active=None)]
        app_users = azure_blob_connector.get_app_users(users)
        assert app_users[0].is_active is True


# ===========================================================================
# Container name extraction
# ===========================================================================
class TestAzureBlobExtractContainerNames:
    def test_dict_based(self, azure_blob_connector):
        data = [{"name": "c1"}, {"name": "c2"}]
        result = azure_blob_connector._extract_container_names(data)
        assert result == ["c1", "c2"]

    def test_object_based(self, azure_blob_connector):
        obj1 = MagicMock()
        obj1.name = "c1"
        obj2 = MagicMock()
        obj2.name = "c2"
        result = azure_blob_connector._extract_container_names([obj1, obj2])
        assert result == ["c1", "c2"]

    def test_none_input(self, azure_blob_connector):
        assert azure_blob_connector._extract_container_names(None) == []


# ===========================================================================
# DataSourceEntitiesProcessor
# ===========================================================================
class TestAzureBlobDataSourceEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="myaccount",
        )
        assert proc.account_name == "myaccount"
