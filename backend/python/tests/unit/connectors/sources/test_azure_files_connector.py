"""Tests for Azure Files connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.azure_files.connector import (
    AzureFilesConnector,
    AzureFilesDataSourceEntitiesProcessor,
    get_file_extension,
    get_mimetype_for_azure_files,
    get_parent_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_files")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock(spec=AzureFilesDataSourceEntitiesProcessor)
    proc.org_id = "org-azf-1"
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
            "connectionString": "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=abc;EndpointSuffix=core.windows.net"
        },
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def azure_files_connector(mock_logger, mock_data_entities_processor,
                           mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
        connector = AzureFilesConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="az-files-1",
        )
    return connector


# ===========================================================================
# Helper functions
# ===========================================================================

class TestAzureFilesGetFileExtension:
    def test_normal(self):
        assert get_file_extension("doc.txt") == "txt"

    def test_path(self):
        assert get_file_extension("dir/sub/file.csv") == "csv"

    def test_no_ext(self):
        assert get_file_extension("README") is None


class TestAzureFilesGetParentPath:
    def test_nested(self):
        assert get_parent_path("a/b/c/file.txt") == "a/b/c"

    def test_root_file(self):
        assert get_parent_path("file.txt") is None

    def test_empty(self):
        assert get_parent_path("") is None

    def test_with_trailing_slash(self):
        assert get_parent_path("a/b/c/") == "a/b"


class TestAzureFilesMimeType:
    def test_directory(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_azure_files("folder", is_directory=True) == MimeTypes.FOLDER.value

    def test_pdf(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_azure_files("report.pdf") == MimeTypes.PDF.value

    def test_unknown(self):
        from app.config.constants.arangodb import MimeTypes
        assert get_mimetype_for_azure_files("data.xyz999") == MimeTypes.BIN.value

    def test_no_extension(self):
        from app.config.constants.arangodb import MimeTypes
        result = get_mimetype_for_azure_files("Makefile")
        assert result == MimeTypes.BIN.value


# ===========================================================================
# AzureFilesConnector
# ===========================================================================

class TestAzureFilesConnectorInit:
    def test_constructor(self, azure_files_connector):
        assert azure_files_connector.connector_id == "az-files-1"
        assert azure_files_connector.data_source is None

    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build,
                                azure_files_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())

        result = await azure_files_connector.init()
        assert result is True

    async def test_init_fails_no_config(self, azure_files_connector):
        azure_files_connector.config_service.get_config = AsyncMock(return_value=None)
        result = await azure_files_connector.init()
        assert result is False

    async def test_init_fails_no_connection_string(self, azure_files_connector):
        azure_files_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {}
        })
        result = await azure_files_connector.init()
        assert result is False

    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_exception(self, mock_build, azure_files_connector):
        mock_build.side_effect = Exception("Auth failed")
        result = await azure_files_connector.init()
        assert result is False


class TestAzureFilesExtractAccountName:
    def test_valid_connection_string(self):
        conn = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=abc;EndpointSuffix=core.windows.net"
        result = AzureFilesConnector._extract_account_name_from_connection_string(conn)
        assert result == "myaccount"

    def test_no_account_name(self):
        conn = "DefaultEndpointsProtocol=https;AccountKey=abc"
        result = AzureFilesConnector._extract_account_name_from_connection_string(conn)
        assert result is None

    def test_empty_account_name(self):
        conn = "AccountName=;AccountKey=abc"
        result = AzureFilesConnector._extract_account_name_from_connection_string(conn)
        assert result is None


class TestAzureFilesWebUrls:
    def test_generate_web_url(self, azure_files_connector):
        azure_files_connector.account_name = "testacc"
        url = azure_files_connector._generate_web_url("myshare", "dir/file.txt")
        assert "testacc.file.core.windows.net" in url
        assert "myshare" in url

    def test_generate_directory_url_with_path(self, azure_files_connector):
        azure_files_connector.account_name = "testacc"
        url = azure_files_connector._generate_directory_url("myshare", "subdir")
        assert "testacc.file.core.windows.net/myshare" in url

    def test_generate_directory_url_root(self, azure_files_connector):
        azure_files_connector.account_name = "testacc"
        url = azure_files_connector._generate_directory_url("myshare", "")
        assert url == "https://testacc.file.core.windows.net/myshare"


class TestAzureFilesDataSourceEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            account_name="myaccount",
        )
        assert proc.account_name == "myaccount"

    def test_generate_directory_url_with_path(self, mock_logger, mock_data_store_provider,
                                               mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            account_name="acc",
        )
        url = proc._generate_directory_url("share/path/to/dir")
        assert "acc.file.core.windows.net/share" in url

    def test_extract_path(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            account_name="acc",
        )
        assert proc._extract_path_from_external_id("share/path/to/dir") == "path/to/dir"
        assert proc._extract_path_from_external_id("share") is None
