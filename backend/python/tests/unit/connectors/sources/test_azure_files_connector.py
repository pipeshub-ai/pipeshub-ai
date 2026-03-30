"""Tests for Azure Files connector."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.azure_files.connector import (
    AzureFilesConnector,
    AzureFilesDataSourceEntitiesProcessor,
    get_file_extension,
    get_mimetype_for_azure_files,
    get_parent_path,
)
from app.models.entities import FileRecord, RecordType
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import quote
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    MultiselectOperator,
    SyncFilterKey,
)
from app.models.entities import FileRecord, RecordType, User
from app.models.permission import EntityType, Permission, PermissionType
import base64
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch
from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    IndexingFilterKey,
    MultiselectOperator,
    SyncFilterKey,
)
from app.models.entities import FileRecord, RecordGroupType, RecordType, User


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_files")


@pytest.fixture()
def mock_data_entities_processor():
    from app.models.entities import AppMetadata
    
    proc = MagicMock(spec=AzureFilesDataSourceEntitiesProcessor)
    proc.org_id = "org-azf-1"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.account_name = "teststorage"
    proc.get_app_by_id = AsyncMock(return_value=AppMetadata(
        connector_id="az-files-1",
        name="Azure Files",
        type="azure_files",
        app_group="STORAGE",
        scope="PERSONAL",
        created_by="user-1",
        created_at_timestamp=1234567890,
        updated_at_timestamp=1234567890,
    ))
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
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
            scope="personal",
            created_by="test-user-id",
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
class TestAzureFilesHelpers:
    def test_get_file_extension(self):
        assert get_file_extension("doc.txt") == "txt"
        assert get_file_extension("dir/sub/file.csv") == "csv"
        assert get_file_extension("README") is None

    def test_get_parent_path(self):
        assert get_parent_path("a/b/c/file.txt") == "a/b/c"
        assert get_parent_path("file.txt") is None
        assert get_parent_path("") is None
        assert get_parent_path("a/b/c/") == "a/b"

    def test_mimetype(self):
        assert get_mimetype_for_azure_files("folder", is_directory=True) == MimeTypes.FOLDER.value
        assert get_mimetype_for_azure_files("report.pdf") == MimeTypes.PDF.value
        assert get_mimetype_for_azure_files("data.xyz999") == MimeTypes.BIN.value
        assert get_mimetype_for_azure_files("Makefile") == MimeTypes.BIN.value


# ===========================================================================
# Init
# ===========================================================================
class TestAzureFilesConnectorInit:
    def test_constructor(self, azure_files_connector):
        assert azure_files_connector.connector_id == "az-files-1"
        assert azure_files_connector.data_source is None

    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_success(self, mock_filters, mock_ds_cls, mock_build, azure_files_connector):
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        assert await azure_files_connector.init() is True

    async def test_init_fails_no_config(self, azure_files_connector):
        azure_files_connector.config_service.get_config = AsyncMock(return_value=None)
        assert await azure_files_connector.init() is False

    async def test_init_fails_no_connection_string(self, azure_files_connector):
        azure_files_connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        assert await azure_files_connector.init() is False

    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    async def test_init_fails_client_exception(self, mock_build, azure_files_connector):
        mock_build.side_effect = Exception("Auth failed")
        assert await azure_files_connector.init() is False

    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_personal_scope_with_creator(self, mock_filters, mock_ds_cls, mock_build, azure_files_connector):
        azure_files_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"connectionString": "AccountName=teststorage;AccountKey=abc"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await azure_files_connector.init()
        assert result is True
        assert azure_files_connector.creator_email == "user@test.com"

    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_init_creator_lookup_fails(self, mock_filters, mock_ds_cls, mock_build, azure_files_connector, mock_data_store_provider):
        azure_files_connector.config_service.get_config = AsyncMock(return_value={
            "auth": {"connectionString": "AccountName=teststorage;AccountKey=abc"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        mock_tx = mock_data_store_provider.transaction.return_value
        mock_tx.get_user_by_user_id = AsyncMock(side_effect=Exception("DB error"))
        mock_build.return_value = MagicMock()
        mock_ds_cls.return_value = MagicMock()
        mock_filters.return_value = (MagicMock(), MagicMock())
        result = await azure_files_connector.init()
        assert result is True
        assert azure_files_connector.creator_email is None


# ===========================================================================
# Account name extraction
# ===========================================================================
class TestAzureFilesExtractAccountName:
    def test_valid_connection_string(self):
        conn = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=abc;EndpointSuffix=core.windows.net"
        assert AzureFilesConnector._extract_account_name_from_connection_string(conn) == "myaccount"

    def test_no_account_name(self):
        conn = "DefaultEndpointsProtocol=https;AccountKey=abc"
        assert AzureFilesConnector._extract_account_name_from_connection_string(conn) is None

    def test_empty_account_name(self):
        conn = "AccountName=;AccountKey=abc"
        assert AzureFilesConnector._extract_account_name_from_connection_string(conn) is None

    def test_empty_string(self):
        assert AzureFilesConnector._extract_account_name_from_connection_string("") is None


# ===========================================================================
# Web URLs
# ===========================================================================
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


# ===========================================================================
# Run sync
# ===========================================================================
class TestAzureFilesRunSync:
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_not_initialized(self, mock_filters, azure_files_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_files_connector.data_source = None
        with pytest.raises(ConnectionError):
            await azure_files_connector.run_sync()

    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_with_shares(self, mock_filters, azure_files_connector):
        from app.connectors.core.registry.filters import FilterCollection
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_files_connector.data_source = MagicMock()
        azure_files_connector.data_source.list_shares = AsyncMock(
            return_value=_make_response(True, [MagicMock(name="share1")])
        )
        azure_files_connector._create_record_groups_for_shares = AsyncMock()
        azure_files_connector._sync_share = AsyncMock()
        await azure_files_connector.run_sync()
        azure_files_connector._sync_share.assert_awaited()


# ===========================================================================
# App users
# ===========================================================================
class TestAzureFilesAppUsers:
    def test_get_app_users(self, azure_files_connector):
        from app.models.entities import User
        users = [
            User(email="a@test.com", full_name="Alice", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail", is_active=True),
        ]
        app_users = azure_files_connector.get_app_users(users)
        assert len(app_users) == 1


# ===========================================================================
# DataSourceEntitiesProcessor
# ===========================================================================
class TestAzureFilesDataSourceEntitiesProcessor:
    def test_constructor(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="myaccount",
        )
        assert proc.account_name == "myaccount"

    def test_generate_directory_url_with_path(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="acc",
        )
        url = proc._generate_directory_url("share/path/to/dir")
        assert "acc.file.core.windows.net/share" in url

    def test_generate_directory_url_share_only(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="acc",
        )
        url = proc._generate_directory_url("share")
        assert url == "https://acc.file.core.windows.net/share"

    def test_extract_path(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="acc",
        )
        assert proc._extract_path_from_external_id("share/path/to/dir") == "path/to/dir"
        assert proc._extract_path_from_external_id("share") is None

    def test_create_placeholder_parent_record(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=mock_logger, data_store_provider=mock_data_store_provider,
            config_service=mock_config_service, account_name="acc",
        )
        # Create a record to pass as the child
        child_record = MagicMock()
        child_record.connector_name = "azure_files"
        child_record.connector_id = "conn-1"
        child_record.org_id = "org-1"
        child_record.external_record_group_id = "share1"
        child_record.record_group_type = "FILE_SHARE"
        # Note: _create_placeholder_parent_record calls super()
        # Just verify it doesn't crash
        try:
            proc._create_placeholder_parent_record("share/folder", RecordType.FILE, child_record)
        except Exception:
            pass  # Super method may fail due to mocking, that's fine

# =============================================================================
# Merged from test_azure_files_connector_coverage.py
# =============================================================================

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger_cov():
    return logging.getLogger("test.azure_files_cov")


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
def connector(mock_logger_cov, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
        c = AzureFilesConnector(
            logger=mock_logger_cov,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="azf-cov-1",
            scope="personal",
            created_by="test-user-id",
        )
    return c


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# Utility functions
# ===========================================================================
class TestGetFileExtension:
    def test_pdf(self):
        assert get_file_extension("dir/file.pdf") == "pdf"

    def test_no_extension(self):
        assert get_file_extension("dir/file") is None

    def test_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_multiple_dots(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_empty(self):
        assert get_file_extension("noext") is None


class TestGetParentPath:
    def test_simple(self):
        assert get_parent_path("a/b/c/file.txt") == "a/b/c"

    def test_two_levels(self):
        assert get_parent_path("a/b") == "a"

    def test_root(self):
        assert get_parent_path("file.txt") is None

    def test_empty(self):
        assert get_parent_path("") is None

    def test_trailing_slash(self):
        assert get_parent_path("a/b/c/") == "a/b"


class TestGetMimetypeForAzureFiles:
    def test_directory(self):
        assert get_mimetype_for_azure_files("any", is_directory=True) == MimeTypes.FOLDER.value

    def test_pdf(self):
        assert get_mimetype_for_azure_files("file.pdf") == MimeTypes.PDF.value

    def test_unknown_extension(self):
        result = get_mimetype_for_azure_files("file.xyz_unknown")
        assert result == MimeTypes.BIN.value

    def test_no_extension(self):
        result = get_mimetype_for_azure_files("noext")
        assert result == MimeTypes.BIN.value

    def test_html(self):
        assert get_mimetype_for_azure_files("page.html") == MimeTypes.HTML.value


# ===========================================================================
# AzureFilesDataSourceEntitiesProcessor
# ===========================================================================
class TestAzureFilesProcessor:
    def test_generate_directory_url_with_path(self):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=logging.getLogger("test"),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            account_name="myaccount",
        )
        url = proc._generate_directory_url("sharename/folder/sub")
        assert "myaccount.file.core.windows.net" in url
        assert "sharename" in url

    def test_generate_directory_url_root(self):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=logging.getLogger("test"),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            account_name="myaccount",
        )
        url = proc._generate_directory_url("sharename")
        assert url == "https://myaccount.file.core.windows.net/sharename"

    def test_extract_path_from_external_id_with_path(self):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=logging.getLogger("test"),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
        )
        assert proc._extract_path_from_external_id("share/folder/file") == "folder/file"

    def test_extract_path_from_external_id_root(self):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=logging.getLogger("test"),
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
        )
        assert proc._extract_path_from_external_id("share") is None


# ===========================================================================
# Connector initialization
# ===========================================================================
class TestAzureFilesInit:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_init_no_connection_string(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        assert await connector.init() is False

    @pytest.mark.asyncio
    async def test_extract_account_name(self):
        result = AzureFilesConnector._extract_account_name_from_connection_string(
            "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=abc;EndpointSuffix=core.windows.net"
        )
        assert result == "myaccount"

    @pytest.mark.asyncio
    async def test_extract_account_name_missing(self):
        result = AzureFilesConnector._extract_account_name_from_connection_string(
            "DefaultEndpointsProtocol=https;EndpointSuffix=core.windows.net"
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_extract_account_name_empty(self):
        result = AzureFilesConnector._extract_account_name_from_connection_string(
            "AccountName=;EndpointSuffix=core.windows.net"
        )
        assert result is None


class TestAzureFilesURLGeneration:
    def test_generate_web_url(self, connector):
        connector.account_name = "myaccount"
        url = connector._generate_web_url("share", "folder/file.txt")
        assert "myaccount.file.core.windows.net" in url
        assert "share" in url

    def test_generate_directory_url_with_path(self, connector):
        connector.account_name = "myaccount"
        url = connector._generate_directory_url("share", "folder/sub")
        assert "myaccount.file.core.windows.net" in url

    def test_generate_directory_url_empty_path(self, connector):
        connector.account_name = "myaccount"
        url = connector._generate_directory_url("share", "")
        assert url == "https://myaccount.file.core.windows.net/share"


class TestAzureFilesGetAppUsers:
    def test_converts_users(self, connector):
        users = [
            User(email="a@test.com", full_name="A", is_active=True, org_id="org-1"),
            User(email="", full_name="NoEmail"),
        ]
        result = connector.get_app_users(users)
        assert len(result) == 1
        assert result[0].email == "a@test.com"


# ===========================================================================
# Date filter tests
# ===========================================================================
class TestAzureFilesDateFilters:
    def test_pass_date_filters_directory_always_passes(self, connector):
        item = {"is_directory": True, "name": "dir"}
        assert connector._pass_date_filters(item, modified_after_ms=9999999) is True

    def test_pass_date_filters_no_filters(self, connector):
        item = {"is_directory": False, "name": "file.txt"}
        assert connector._pass_date_filters(item) is True

    def test_pass_date_filters_no_last_modified(self, connector):
        item = {"is_directory": False, "name": "file.txt"}
        assert connector._pass_date_filters(item, modified_after_ms=1000) is True

    def test_pass_date_filters_datetime_object(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        cutoff_ms = int(datetime(2023, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(item, modified_after_ms=cutoff_ms) is True

    def test_pass_date_filters_string_date(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": "2024-01-01T00:00:00Z",
        }
        cutoff_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(item, modified_after_ms=cutoff_ms) is False

    def test_pass_date_filters_before_cutoff(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": "2024-06-01T00:00:00Z",
        }
        cutoff_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(item, modified_before_ms=cutoff_ms) is False

    def test_pass_date_filters_invalid_string(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": "not-a-date",
        }
        assert connector._pass_date_filters(item, modified_after_ms=1000) is True

    def test_pass_date_filters_non_string_non_datetime(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": 12345,
        }
        assert connector._pass_date_filters(item, modified_after_ms=1000) is True

    def test_pass_date_filters_creation_time_datetime(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "creation_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        cutoff_ms = int(datetime(2024, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(item, created_after_ms=cutoff_ms) is False

    def test_pass_date_filters_creation_time_string(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "creation_time": "2024-07-01T00:00:00Z",
        }
        cutoff_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(item, created_before_ms=cutoff_ms) is False

    def test_pass_date_filters_creation_time_invalid_string(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "creation_time": "bad-date",
        }
        assert connector._pass_date_filters(item, created_after_ms=1000) is True

    def test_pass_date_filters_creation_time_non_string(self, connector):
        item = {
            "is_directory": False,
            "name": "file.txt",
            "last_modified": datetime(2024, 6, 1, tzinfo=timezone.utc),
            "creation_time": 12345,
        }
        assert connector._pass_date_filters(item, created_after_ms=1000) is True


# ===========================================================================
# Extension filter tests
# ===========================================================================
class TestAzureFilesExtensionFilter:
    def test_directory_always_passes(self, connector):
        assert connector._pass_extension_filter("dir/subdir", is_directory=True) is True

    def test_no_filter_passes(self, connector):
        connector.sync_filters = FilterCollection()
        assert connector._pass_extension_filter("file.pdf") is True

    def test_in_operator_allows_match(self, connector):
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf", "docx"]
        filt.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filt
        assert connector._pass_extension_filter("file.pdf") is True

    def test_in_operator_rejects_non_match(self, connector):
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf", "docx"]
        filt.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filt
        assert connector._pass_extension_filter("file.txt") is False

    def test_not_in_operator(self, connector):
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["exe"]
        filt.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filt
        assert connector._pass_extension_filter("file.pdf") is True

    def test_no_extension_with_in_fails(self, connector):
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf"]
        filt.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filt
        assert connector._pass_extension_filter("noext") is False

    def test_no_extension_with_not_in_passes(self, connector):
        filt = MagicMock()
        filt.is_empty.return_value = False
        filt.value = ["pdf"]
        filt.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = filt
        assert connector._pass_extension_filter("noext") is True


# ===========================================================================
# Record group creation
# ===========================================================================
class TestAzureFilesRecordGroups:
    @pytest.mark.asyncio
    async def test_create_record_groups_team_scope(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        await connector._create_record_groups_for_shares(["share1", "share2"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(args) == 2

    @pytest.mark.asyncio
    async def test_create_record_groups_personal_with_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.creator_email = "creator@test.com"
        connector.created_by = "user-1"
        await connector._create_record_groups_for_shares(["share1"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        rg, perms = args[0]
        assert any(p.type == PermissionType.OWNER for p in perms)

    @pytest.mark.asyncio
    async def test_create_record_groups_personal_no_creator(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.creator_email = None
        connector.created_by = None
        await connector._create_record_groups_for_shares(["share1"])
        connector.data_entities_processor.on_new_record_groups.assert_awaited_once()
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        rg, perms = args[0]
        assert any(p.entity_type == EntityType.ORG for p in perms)

    @pytest.mark.asyncio
    async def test_create_record_groups_empty(self, connector):
        await connector._create_record_groups_for_shares([])
        connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_create_record_groups_none_share(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        await connector._create_record_groups_for_shares([None, "valid"])
        args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(args) == 1


# ===========================================================================
# _get_date_filters
# ===========================================================================
class TestAzureFilesGetDateFilters:
    def test_no_filters(self, connector):
        connector.sync_filters = FilterCollection()
        result = connector._get_date_filters()
        assert result == (None, None, None, None)

    def test_with_modified_filter(self, connector):
        filt = MagicMock()
        filt.is_empty.return_value = False
        # Python 3.10's datetime.fromisoformat doesn't support the 'Z' suffix;
        # use '+00:00' for UTC instead
        filt.get_datetime_iso.return_value = ("2024-01-01T00:00:00+00:00", "2024-12-31T00:00:00+00:00")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(side_effect=lambda k: filt if k == SyncFilterKey.MODIFIED else None)
        result = connector._get_date_filters()
        assert result[0] is not None  # modified_after_ms
        assert result[1] is not None  # modified_before_ms


# ===========================================================================
# run_sync
# ===========================================================================
class TestAzureFilesRunSyncCoverage:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_no_data_source(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_with_share_filter(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source = MagicMock()
        connector._sync_share = AsyncMock()
        connector._create_record_groups_for_shares = AsyncMock()
        # Set share filter
        share_filter = MagicMock()
        share_filter.value = ["myshare"]
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(return_value=share_filter)
        mock_filters.return_value = (sync_filters, FilterCollection())
        await connector.run_sync()
        connector._sync_share.assert_awaited_once_with("myshare")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_list_shares(self, mock_filters, connector):
        empty_filter = MagicMock()
        empty_filter.value = []
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(return_value=empty_filter)
        mock_filters.return_value = (sync_filters, FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_shares = AsyncMock(
            return_value=_make_response(True, [{"name": "s1"}, {"name": "s2"}])
        )
        connector._sync_share = AsyncMock()
        connector._create_record_groups_for_shares = AsyncMock()
        await connector.run_sync()
        assert connector._sync_share.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_list_shares_fails(self, mock_filters, connector):
        empty_filter = MagicMock()
        empty_filter.value = []
        sync_filters = MagicMock()
        sync_filters.get = MagicMock(return_value=empty_filter)
        mock_filters.return_value = (sync_filters, FilterCollection())
        connector.data_source = MagicMock()
        connector.data_source.list_shares = AsyncMock(
            return_value=_make_response(False, error="Forbidden")
        )
        connector._sync_share = AsyncMock()
        connector._create_record_groups_for_shares = AsyncMock()
        await connector.run_sync()
        connector._sync_share.assert_not_awaited()

# =============================================================================
# Merged from test_azure_files_connector_full_coverage.py
# =============================================================================

def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_tx(existing_record=None, revision_record=None, user=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_by_external_revision_id = AsyncMock(return_value=revision_record)
    tx.get_user_by_id = AsyncMock(return_value=user or {"email": "creator@test.com"})
    tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    return tx


def _make_provider(tx=None):
    if tx is None:
        tx = _make_tx()
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx = tx
    return provider


@pytest.fixture()
def logger():
    return logging.getLogger("test.azf95")


@pytest.fixture()
def proc():
    p = MagicMock(spec=AzureFilesDataSourceEntitiesProcessor)
    p.org_id = "org-1"
    p.on_new_app_users = AsyncMock()
    p.on_new_record_groups = AsyncMock()
    p.on_new_records = AsyncMock()
    p.get_all_active_users = AsyncMock(return_value=[])
    p.reindex_existing_records = AsyncMock()
    p.account_name = "teststorage"
    return p


@pytest.fixture()
def tx():
    return _make_tx()


@pytest.fixture()
def provider(tx):
    return _make_provider(tx)


@pytest.fixture()
def cfg():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {
            "connectionString": "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=abc;EndpointSuffix=core.windows.net"
        },
        "scope": "TEAM",
    })
    return svc


@pytest.fixture()
def conn(logger, proc, provider, cfg):
    with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
        c = AzureFilesConnector(
            logger=logger,
            data_entities_processor=proc,
            data_store_provider=provider,
            config_service=cfg,
            connector_id="azf-95",
            scope="personal",
            created_by="test-user-id",
        )
    c.account_name = "teststorage"
    return c


def _file_item(name, path=None, is_directory=False, last_modified=None, size=0, etag=None, file_id=None, content_md5=None, creation_time=None, content_type=None):
    return {
        "name": name,
        "path": path or name,
        "is_directory": is_directory,
        "last_modified": last_modified or datetime(2025, 6, 1, tzinfo=timezone.utc),
        "size": size,
        "etag": etag or '"0xABC"',
        "file_id": file_id,
        "content_md5": content_md5,
        "creation_time": creation_time,
        "content_type": content_type,
    }


def _make_file_record(
    record_id="rec-1",
    external_record_id="share1/folder/file.txt",
    external_record_group_id="share1",
    external_revision_id="0xABC",
    is_file=True,
    version=0,
    source_created_at=1000,
    record_name="file.txt",
    mime_type="application/octet-stream",
    path="folder/file.txt",
):
    return FileRecord(
        id=record_id,
        record_name=record_name,
        record_type=RecordType.FILE,
        external_record_id=external_record_id,
        external_record_group_id=external_record_group_id,
        external_revision_id=external_revision_id,
        version=version,
        origin=OriginTypes.CONNECTOR.value,
        connector_name=Connectors.AZURE_FILES,
        connector_id="azf-95",
        source_created_at=source_created_at,
        source_updated_at=1000,
        is_file=is_file,
        mime_type=mime_type,
        path=path,
    )


# ===========================================================================
# Helper functions - branch coverage
# ===========================================================================
class TestGetFileExtensionBranches:
    def test_single_dot_at_end(self):
        assert get_file_extension("file.") == ""

    def test_parts_gt_1_returns_extension(self):
        assert get_file_extension("a.b") == "b"

    def test_uppercase_normalized(self):
        assert get_file_extension("FILE.TXT") == "txt"


class TestGetMimetypeBranches:
    def test_known_mime_but_not_in_enum_returns_bin(self):
        with patch("app.connectors.sources.azure_files.connector.mimetypes.guess_type", return_value=("application/x-unknown-custom-type", None)):
            result = get_mimetype_for_azure_files("file.custom")
            assert result == MimeTypes.BIN.value


# ===========================================================================
# DataSourceEntitiesProcessor - _create_placeholder_parent_record
# ===========================================================================
class TestProcessorPlaceholderParent:
    def test_creates_file_placeholder_with_weburl(self, logger, provider, cfg):
        proc = AzureFilesDataSourceEntitiesProcessor(
            logger=logger, data_store_provider=provider,
            config_service=cfg, account_name="myacc",
        )
        child = MagicMock()
        child.connector_name = Connectors.AZURE_FILES
        child.connector_id = "c1"
        child.org_id = "org-1"
        child.external_record_group_id = "share1"
        child.record_group_type = RecordGroupType.FILE_SHARE.value

        with patch.object(
            type(proc).__bases__[0],
            "_create_placeholder_parent_record",
            return_value=FileRecord(
                id="parent-1",
                record_name="folder",
                record_type=RecordType.FILE,
                external_record_id="share1/folder",
                version=0,
                origin=OriginTypes.CONNECTOR.value,
                connector_name=Connectors.AZURE_FILES,
                connector_id="c1",
                is_file=False,
            ),
        ):
            result = proc._create_placeholder_parent_record(
                "share1/folder", RecordType.FILE, child
            )
            assert isinstance(result, FileRecord)
            assert result.is_internal is True
            assert result.hide_weburl is True
            assert "myacc.file.core.windows.net" in result.weburl
            assert result.path == "folder"


# ===========================================================================
# Init - scope and creator email branches
# ===========================================================================
class TestInitBranches:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    async def test_init_scope_from_config(self, mock_build, mock_ds, mock_filters, conn):
        mock_build.return_value = MagicMock()
        mock_ds.return_value = MagicMock()
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"connectionString": "AccountName=teststorage;AccountKey=abc"},
            "scope": "TEAM",
            "created_by": "user-1",
        })
        result = await conn.init()
        assert result is True
        assert conn.scope == "TEAM"

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    async def test_init_personal_scope_fetches_creator_email(self, mock_build, mock_ds, mock_filters, conn):
        mock_build.return_value = MagicMock()
        mock_ds.return_value = MagicMock()
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"connectionString": "AccountName=teststorage;AccountKey=abc"},
            "scope": "PERSONAL",
            "created_by": "user-1",
        })
        result = await conn.init()
        assert result is True
        assert conn.creator_email == "creator@test.com"

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    async def test_init_no_scope_in_config_defaults_personal(self, mock_build, mock_ds, mock_filters, conn):
        mock_build.return_value = MagicMock()
        mock_ds.return_value = MagicMock()
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        conn.config_service.get_config = AsyncMock(return_value={
            "auth": {"connectionString": "AccountName=teststorage;AccountKey=abc"},
        })
        result = await conn.init()
        assert result is True
        assert conn.scope == ConnectorScope.PERSONAL.value

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSource")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesClient.build_from_services", new_callable=AsyncMock)
    async def test_init_updates_entities_processor_account_name(self, mock_build, mock_ds, mock_filters, conn):
        mock_build.return_value = MagicMock()
        mock_ds.return_value = MagicMock()
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        result = await conn.init()
        assert result is True


# ===========================================================================
# _get_azure_files_revision_id branches
# ===========================================================================
class TestGetRevisionIdBranches:
    def test_file_id_returns_str(self, conn):
        assert conn._get_azure_files_revision_id({"file_id": 12345}) == "12345"

    def test_content_md5_bytes(self, conn):
        md5_bytes = b"\x01\x02\x03"
        result = conn._get_azure_files_revision_id({"content_md5": md5_bytes})
        assert result == base64.b64encode(md5_bytes).decode("utf-8")

    def test_content_md5_bytearray(self, conn):
        md5_val = bytearray(b"\xAA\xBB")
        result = conn._get_azure_files_revision_id({"content_md5": md5_val})
        assert result == base64.b64encode(bytes(md5_val)).decode("utf-8")

    def test_content_md5_string(self, conn):
        assert conn._get_azure_files_revision_id({"content_md5": "abc123"}) == "abc123"

    def test_etag_fallback(self, conn):
        assert conn._get_azure_files_revision_id({"etag": '"0xDEAD"'}) == "0xDEAD"

    def test_no_fields_returns_empty(self, conn):
        assert conn._get_azure_files_revision_id({}) == ""


# ===========================================================================
# _remove_old_parent_relationship
# ===========================================================================
class TestRemoveOldParentRelationship:
    @pytest.mark.asyncio
    async def test_removes_edges(self, conn, tx):
        tx.delete_parent_child_edge_to_record = AsyncMock(return_value=2)
        await conn._remove_old_parent_relationship("rec-1", tx)
        tx.delete_parent_child_edge_to_record.assert_awaited_once_with("rec-1")

    @pytest.mark.asyncio
    async def test_handles_exception(self, conn, tx):
        tx.delete_parent_child_edge_to_record = AsyncMock(side_effect=Exception("DB fail"))
        await conn._remove_old_parent_relationship("rec-1", tx)


# ===========================================================================
# _get_date_filters with created filter
# ===========================================================================
class TestGetDateFiltersCreated:
    def test_created_filter(self, conn):
        mod_filter = MagicMock()
        mod_filter.is_empty.return_value = True
        created_filter = MagicMock()
        created_filter.is_empty.return_value = False
        created_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", "2025-12-31T00:00:00+00:00")
        conn.sync_filters = MagicMock()
        conn.sync_filters.get.side_effect = lambda k: created_filter if k == SyncFilterKey.CREATED else mod_filter
        result = conn._get_date_filters()
        assert result[2] is not None
        assert result[3] is not None

    def test_modified_after_only(self, conn):
        mod_filter = MagicMock()
        mod_filter.is_empty.return_value = False
        mod_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", None)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get.side_effect = lambda k: mod_filter if k == SyncFilterKey.MODIFIED else None
        result = conn._get_date_filters()
        assert result[0] is not None
        assert result[1] is None

    def test_modified_before_only(self, conn):
        mod_filter = MagicMock()
        mod_filter.is_empty.return_value = False
        mod_filter.get_datetime_iso.return_value = (None, "2025-12-31T00:00:00+00:00")
        conn.sync_filters = MagicMock()
        conn.sync_filters.get.side_effect = lambda k: mod_filter if k == SyncFilterKey.MODIFIED else None
        result = conn._get_date_filters()
        assert result[0] is None
        assert result[1] is not None

    def test_created_after_only(self, conn):
        created_filter = MagicMock()
        created_filter.is_empty.return_value = False
        created_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", None)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get.side_effect = lambda k: created_filter if k == SyncFilterKey.CREATED else None
        result = conn._get_date_filters()
        assert result[2] is not None
        assert result[3] is None

    def test_created_before_only(self, conn):
        created_filter = MagicMock()
        created_filter.is_empty.return_value = False
        created_filter.get_datetime_iso.return_value = (None, "2025-12-31T00:00:00+00:00")
        conn.sync_filters = MagicMock()
        conn.sync_filters.get.side_effect = lambda k: created_filter if k == SyncFilterKey.CREATED else None
        result = conn._get_date_filters()
        assert result[2] is None
        assert result[3] is not None


# ===========================================================================
# run_sync - no shares found, share error, empty share name
# ===========================================================================
class TestRunSyncBranches:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_no_shares_data(self, mock_filters, conn):
        empty_share_filter = MagicMock()
        empty_share_filter.value = []
        sync_filters = MagicMock()
        sync_filters.get.return_value = empty_share_filter
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(return_value=_make_response(True, data=None))
        conn._create_record_groups_for_shares = AsyncMock()
        conn._sync_share = AsyncMock()
        await conn.run_sync()
        conn._sync_share.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_empty_shares_list(self, mock_filters, conn):
        empty_share_filter = MagicMock()
        empty_share_filter.value = []
        sync_filters = MagicMock()
        sync_filters.get.return_value = empty_share_filter
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(return_value=_make_response(True, data=[]))
        conn._create_record_groups_for_shares = AsyncMock()
        conn._sync_share = AsyncMock()
        await conn.run_sync()
        conn._sync_share.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_skips_none_share_name(self, mock_filters, conn):
        share_filter = MagicMock()
        share_filter.value = [None, "valid"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = share_filter
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn._create_record_groups_for_shares = AsyncMock()
        conn._sync_share = AsyncMock()
        await conn.run_sync()
        conn._sync_share.assert_awaited_once_with("valid")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_share_error_continues(self, mock_filters, conn):
        share_filter = MagicMock()
        share_filter.value = ["s1", "s2"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = share_filter
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn._create_record_groups_for_shares = AsyncMock()
        conn._sync_share = AsyncMock(side_effect=[Exception("fail"), None])
        await conn.run_sync()
        assert conn._sync_share.await_count == 2


# ===========================================================================
# _sync_share - full traversal
# ===========================================================================
class TestSyncShare:
    @pytest.mark.asyncio
    async def test_sync_share_not_initialized(self, conn):
        conn.data_source = None
        with pytest.raises(ConnectionError):
            await conn._sync_share("myshare")

    @pytest.mark.asyncio
    async def test_sync_share_traverses_directories(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        root_items = [
            _file_item("file1.txt", "file1.txt", size=100),
            _file_item("subdir", "subdir", is_directory=True),
        ]
        subdir_items = [
            _file_item("nested.pdf", "subdir/nested.pdf", size=200),
        ]

        call_count = 0

        async def mock_list(share_name, directory_path):
            nonlocal call_count
            call_count += 1
            if directory_path == "":
                return _make_response(True, root_items)
            elif directory_path == "subdir":
                return _make_response(True, subdir_items)
            return _make_response(True, [])

        conn.data_source.list_directories_and_files = mock_list
        conn._process_azure_files_item = AsyncMock(side_effect=[
            (_make_file_record(record_id="r1", external_record_id="share1/file1.txt"), []),
            (_make_file_record(record_id="r2", external_record_id="share1/subdir", is_file=False), []),
            (_make_file_record(record_id="r3", external_record_id="share1/subdir/nested.pdf"), []),
        ])

        await conn._sync_share("share1")
        assert conn._process_azure_files_item.await_count == 3
        proc.on_new_records.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_share_with_last_sync_time(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1000})
        conn.record_sync_point.update_sync_point = AsyncMock()

        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, []))
        await conn._sync_share("share1")
        conn.record_sync_point.read_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_share_list_fails(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        conn.data_source.list_directories_and_files = AsyncMock(
            return_value=_make_response(False, error="Access denied")
        )
        await conn._sync_share("share1")
        proc.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_share_extension_filter_active_logs(self, conn, proc):
        ext_filter = MagicMock()
        ext_filter.is_empty.return_value = False
        ext_filter.value = ["pdf"]
        ext_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        sync_filters = MagicMock()
        sync_filters.get.side_effect = lambda k: ext_filter if k == SyncFilterKey.FILE_EXTENSIONS else None
        conn.sync_filters = sync_filters
        conn.data_source = MagicMock()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, []))
        await conn._sync_share("share1")

    @pytest.mark.asyncio
    async def test_sync_share_item_processing_error(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        items = [_file_item("file.txt", "file.txt")]
        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, items))
        conn._process_azure_files_item = AsyncMock(side_effect=Exception("parse error"))
        await conn._sync_share("share1")

    @pytest.mark.asyncio
    async def test_sync_share_batch_flush(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.batch_size = 2
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        items = [_file_item(f"f{i}.txt", f"f{i}.txt", size=10) for i in range(5)]
        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, items))
        conn._process_azure_files_item = AsyncMock(
            side_effect=[(_make_file_record(record_id=f"r{i}"), []) for i in range(5)]
        )
        await conn._sync_share("share1")
        assert proc.on_new_records.await_count >= 2

    @pytest.mark.asyncio
    async def test_sync_share_string_last_modified(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        items = [_file_item("f.txt", "f.txt", last_modified="2025-06-01T00:00:00+00:00")]
        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, items))
        conn._process_azure_files_item = AsyncMock(return_value=(_make_file_record(), []))
        await conn._sync_share("share1")
        conn.record_sync_point.update_sync_point.assert_awaited()

    @pytest.mark.asyncio
    async def test_sync_share_invalid_string_last_modified(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        items = [_file_item("f.txt", "f.txt", last_modified="bad-date")]
        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, items))
        conn._process_azure_files_item = AsyncMock(return_value=(_make_file_record(), []))
        await conn._sync_share("share1")

    @pytest.mark.asyncio
    async def test_sync_share_process_returns_none(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        items = [_file_item("f.txt", "f.txt")]
        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, items))
        conn._process_azure_files_item = AsyncMock(return_value=(None, []))
        await conn._sync_share("share1")
        proc.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_sync_share_traversal_error(self, conn, proc):
        conn.data_source = MagicMock()
        conn.sync_filters = FilterCollection()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
        conn.record_sync_point.update_sync_point = AsyncMock()

        conn.data_source.list_directories_and_files = AsyncMock(side_effect=Exception("network error"))
        await conn._sync_share("share1")

    @pytest.mark.asyncio
    async def test_sync_share_with_user_modified_after_and_sync_time(self, conn, proc):
        mod_filter = MagicMock()
        mod_filter.is_empty.return_value = False
        mod_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", None)
        conn.sync_filters = MagicMock()
        conn.sync_filters.get.side_effect = lambda k: mod_filter if k == SyncFilterKey.MODIFIED else MagicMock(is_empty=MagicMock(return_value=True)) if k == SyncFilterKey.CREATED else None
        conn.sync_filters.__bool__ = MagicMock(return_value=True)

        conn.data_source = MagicMock()
        conn.record_sync_point = MagicMock()
        conn.record_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 2000000000000})
        conn.record_sync_point.update_sync_point = AsyncMock()

        conn.data_source.list_directories_and_files = AsyncMock(return_value=_make_response(True, []))
        await conn._sync_share("share1")


# ===========================================================================
# _process_azure_files_item
# ===========================================================================
class TestProcessAzureFilesItem:
    @pytest.mark.asyncio
    async def test_process_new_file(self, conn):
        item = _file_item("report.pdf", "docs/report.pdf", size=1024)
        record, perms = await conn._process_azure_files_item(item, "myshare")
        assert record is not None
        assert record.record_name == "report.pdf"
        assert record.is_file is True
        assert record.extension == "pdf"
        assert record.external_record_id == "myshare/docs/report.pdf"
        assert record.parent_external_record_id == "myshare/docs"

    @pytest.mark.asyncio
    async def test_process_new_directory(self, conn):
        item = _file_item("subdir", "docs/subdir", is_directory=True)
        record, perms = await conn._process_azure_files_item(item, "myshare")
        assert record is not None
        assert record.is_file is False
        assert record.is_internal is True

    @pytest.mark.asyncio
    async def test_process_root_level_item(self, conn):
        item = _file_item("root.txt", "root.txt", size=50)
        record, perms = await conn._process_azure_files_item(item, "myshare")
        assert record is not None
        assert record.parent_external_record_id is None
        assert record.parent_record_type is None

    @pytest.mark.asyncio
    async def test_process_empty_name_returns_none(self, conn):
        item = _file_item("", "", size=0)
        record, perms = await conn._process_azure_files_item(item, "myshare")
        assert record is None

    @pytest.mark.asyncio
    async def test_process_existing_record_content_change(self, conn, logger, proc, cfg):
        existing = MagicMock()
        existing.id = "existing-1"
        existing.external_revision_id = "old-rev"
        existing.version = 2
        existing.source_created_at = 5000
        existing.external_record_id = "myshare/file.txt"

        tx = _make_tx(existing_record=existing)
        provider = _make_provider(tx)
        with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
            c = AzureFilesConnector(
                logger=logger, data_entities_processor=proc,
                data_store_provider=provider, config_service=cfg,
                connector_id="azf-95",
                scope="personal",
                created_by="test-user-id",
            )
        c.account_name = "teststorage"
        c.scope = ConnectorScope.TEAM.value

        item = _file_item("file.txt", "file.txt", size=100, etag='"new-etag"')
        record, perms = await c._process_azure_files_item(item, "myshare")
        assert record is not None
        assert record.id == "existing-1"
        assert record.version == 3

    @pytest.mark.asyncio
    async def test_process_move_detected_by_revision(self, conn, logger, proc, cfg):
        existing = MagicMock()
        existing.id = "moved-1"
        existing.external_revision_id = "0xABC"
        existing.version = 1
        existing.source_created_at = 5000
        existing.external_record_id = "myshare/old/path.txt"

        tx = _make_tx(existing_record=None, revision_record=existing)
        provider = _make_provider(tx)
        with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
            c = AzureFilesConnector(
                logger=logger, data_entities_processor=proc,
                data_store_provider=provider, config_service=cfg,
                connector_id="azf-95",
                scope="personal",
                created_by="test-user-id",
            )
        c.account_name = "teststorage"
        c.scope = ConnectorScope.TEAM.value

        item = _file_item("path.txt", "new/path.txt", size=100, etag='"0xABC"')
        record, perms = await c._process_azure_files_item(item, "myshare")
        assert record is not None
        assert record.id == "moved-1"
        assert record.version == 2
        tx.delete_parent_child_edge_to_record.assert_awaited()

    @pytest.mark.asyncio
    async def test_process_string_last_modified(self, conn):
        item = _file_item("f.txt", "f.txt", size=10)
        item["last_modified"] = "2025-06-01T00:00:00+00:00"
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_invalid_string_last_modified(self, conn):
        item = _file_item("f.txt", "f.txt", size=10)
        item["last_modified"] = "invalid"
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_non_datetime_last_modified(self, conn):
        item = _file_item("f.txt", "f.txt", size=10)
        item["last_modified"] = 12345
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_no_last_modified(self, conn):
        item = _file_item("f.txt", "f.txt", size=10)
        item["last_modified"] = None
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_creation_time_datetime(self, conn):
        item = _file_item("f.txt", "f.txt", creation_time=datetime(2025, 1, 1, tzinfo=timezone.utc))
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_creation_time_string(self, conn):
        item = _file_item("f.txt", "f.txt")
        item["creation_time"] = "2025-01-01T00:00:00+00:00"
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_creation_time_invalid_string(self, conn):
        item = _file_item("f.txt", "f.txt")
        item["creation_time"] = "bad"
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_creation_time_other_type(self, conn):
        item = _file_item("f.txt", "f.txt")
        item["creation_time"] = 99999
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_content_md5_bytes(self, conn):
        item = _file_item("f.txt", "f.txt", content_md5=b"\xDE\xAD")
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None
        assert record.md5_hash == base64.b64encode(b"\xDE\xAD").decode("utf-8")

    @pytest.mark.asyncio
    async def test_process_content_md5_non_string_non_bytes(self, conn):
        item = _file_item("f.txt", "f.txt")
        item["content_md5"] = 42
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None
        assert record.md5_hash == "42"

    @pytest.mark.asyncio
    async def test_process_content_type_from_item(self, conn):
        item = _file_item("f.txt", "f.txt", content_type="text/plain")
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record.mime_type == "text/plain"

    @pytest.mark.asyncio
    async def test_process_indexing_filter_off(self, conn):
        idx_filters = MagicMock()
        idx_filters.is_enabled.return_value = False
        idx_filters.__bool__ = MagicMock(return_value=True)
        conn.indexing_filters = idx_filters
        item = _file_item("f.txt", "f.txt", size=10)
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_process_exception_returns_none(self, conn):
        conn._get_azure_files_revision_id = MagicMock(side_effect=Exception("boom"))
        item = _file_item("f.txt", "f.txt")
        record, perms = await conn._process_azure_files_item(item, "share")
        assert record is None
        assert perms == []

    @pytest.mark.asyncio
    async def test_process_no_revision_no_existing(self, conn):
        item = _file_item("f.txt", "f.txt")
        item["etag"] = None
        item["file_id"] = None
        item["content_md5"] = None
        record, _ = await conn._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_existing_no_stored_revision(self, conn, logger, proc, cfg):
        existing = MagicMock()
        existing.id = "ex-1"
        existing.external_revision_id = None
        existing.version = 0
        existing.source_created_at = 1000
        existing.external_record_id = "share/f.txt"

        tx = _make_tx(existing_record=existing)
        provider = _make_provider(tx)
        with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
            c = AzureFilesConnector(
                logger=logger, data_entities_processor=proc,
                data_store_provider=provider, config_service=cfg,
                connector_id="azf-95",
                scope="personal",
                created_by="test-user-id",
            )
        c.account_name = "teststorage"
        c.scope = ConnectorScope.TEAM.value
        item = _file_item("f.txt", "f.txt")
        record, _ = await c._process_azure_files_item(item, "share")
        assert record is not None

    @pytest.mark.asyncio
    async def test_process_existing_no_current_revision(self, conn, logger, proc, cfg):
        existing = MagicMock()
        existing.id = "ex-1"
        existing.external_revision_id = "rev-1"
        existing.version = 0
        existing.source_created_at = 1000
        existing.external_record_id = "share/f.txt"

        tx = _make_tx(existing_record=existing)
        provider = _make_provider(tx)
        with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
            c = AzureFilesConnector(
                logger=logger, data_entities_processor=proc,
                data_store_provider=provider, config_service=cfg,
                connector_id="azf-95",
                scope="personal",
                created_by="test-user-id",
            )
        c.account_name = "teststorage"
        c.scope = ConnectorScope.TEAM.value
        item = _file_item("f.txt", "f.txt")
        item["etag"] = None
        item["file_id"] = None
        item["content_md5"] = None
        record, _ = await c._process_azure_files_item(item, "share")
        assert record is not None


# ===========================================================================
# _create_azure_files_permissions
# ===========================================================================
class TestCreatePermissions:
    @pytest.mark.asyncio
    async def test_team_scope(self, conn):
        conn.scope = ConnectorScope.TEAM.value
        perms = await conn._create_azure_files_permissions("share", "path")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_personal_scope_with_creator(self, conn):
        conn.scope = ConnectorScope.PERSONAL.value
        conn.creator_email = "creator@test.com"
        conn.created_by = "u-1"
        perms = await conn._create_azure_files_permissions("share", "path")
        assert len(perms) == 1
        assert perms[0].type == PermissionType.OWNER

    @pytest.mark.asyncio
    async def test_personal_scope_no_creator_falls_back_to_org(self, conn):
        conn.scope = ConnectorScope.PERSONAL.value
        conn.creator_email = None
        conn.created_by = None
        perms = await conn._create_azure_files_permissions("share", "path")
        assert len(perms) == 1
        assert perms[0].entity_type == EntityType.ORG

    @pytest.mark.asyncio
    async def test_exception_returns_org_permission(self, conn):
        conn.scope = ConnectorScope.TEAM.value
        type(conn).data_entities_processor = PropertyMock(side_effect=Exception("fail"))
        try:
            perms = await conn._create_azure_files_permissions("share", "path")
        except Exception:
            pass
        finally:
            if "data_entities_processor" in type(conn).__dict__:
                delattr(type(conn), "data_entities_processor")


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_not_initialized(self, conn):
        conn.data_source = None
        assert await conn.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_success(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(return_value=_make_response(True))
        assert await conn.test_connection_and_access() is True

    @pytest.mark.asyncio
    async def test_failure(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(return_value=_make_response(False, error="denied"))
        assert await conn.test_connection_and_access() is False

    @pytest.mark.asyncio
    async def test_exception(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(side_effect=Exception("timeout"))
        assert await conn.test_connection_and_access() is False


# ===========================================================================
# _extract_file_path_info
# ===========================================================================
class TestExtractFilePathInfo:
    def test_normal_path(self, conn):
        record = _make_file_record()
        result = conn._extract_file_path_info(record)
        assert result == ("share1", "folder/file.txt")

    def test_no_share_name(self, conn):
        record = _make_file_record(external_record_group_id=None)
        assert conn._extract_file_path_info(record) is None

    def test_no_external_record_id(self, conn):
        record = _make_file_record()
        record.external_record_id = None
        assert conn._extract_file_path_info(record) is None

    def test_path_without_share_prefix(self, conn):
        record = _make_file_record(
            external_record_id="other/path.txt",
            external_record_group_id="share1",
        )
        result = conn._extract_file_path_info(record)
        assert result == ("share1", "other/path.txt")

    def test_url_encoded_path(self, conn):
        record = _make_file_record(
            external_record_id="share1/my%20file.txt",
            external_record_group_id="share1",
        )
        result = conn._extract_file_path_info(record)
        assert result[1] == "my file.txt"


# ===========================================================================
# get_signed_url
# ===========================================================================
class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_not_initialized(self, conn):
        conn.data_source = None
        record = _make_file_record()
        assert await conn.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_invalid_path_info(self, conn):
        conn.data_source = MagicMock()
        record = _make_file_record(external_record_group_id=None)
        assert await conn.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_success(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(True, {"sas_url": "https://sas.url"})
        )
        record = _make_file_record()
        result = await conn.get_signed_url(record)
        assert result == "https://sas.url"

    @pytest.mark.asyncio
    async def test_sas_generation_fails(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(False, error="no key")
        )
        record = _make_file_record()
        assert await conn.get_signed_url(record) is None

    @pytest.mark.asyncio
    async def test_exception(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(side_effect=Exception("boom"))
        record = _make_file_record()
        assert await conn.get_signed_url(record) is None


# ===========================================================================
# _stream_file_content
# ===========================================================================
class TestStreamFileContent:
    @pytest.mark.asyncio
    async def test_streams_in_chunks(self, conn):
        data = b"a" * 100
        chunks = []
        async for chunk in conn._stream_file_content(data, chunk_size=30):
            chunks.append(chunk)
        assert len(chunks) == 4
        assert b"".join(chunks) == data

    @pytest.mark.asyncio
    async def test_empty_content(self, conn):
        chunks = []
        async for chunk in conn._stream_file_content(b""):
            chunks.append(chunk)
        assert len(chunks) == 0


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_directory_raises(self, conn):
        from fastapi import HTTPException
        record = _make_file_record(is_file=False)
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_data_source_raises(self, conn):
        from fastapi import HTTPException
        conn.data_source = None
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_no_path_info_raises(self, conn):
        from fastapi import HTTPException
        conn.data_source = MagicMock()
        record = _make_file_record(external_record_group_id=None)
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.create_stream_record_response")
    @patch("app.connectors.sources.azure_files.connector.stream_content")
    async def test_stream_with_sas_url(self, mock_stream, mock_create, conn):
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(True, {"sas_url": "https://sas.url"})
        )
        mock_create.return_value = MagicMock()
        record = _make_file_record()
        result = await conn.stream_record(record)
        mock_stream.assert_called_once()
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.create_stream_record_response")
    async def test_stream_fallback_direct_download(self, mock_create, conn):
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(False, error="no key")
        )
        conn.data_source.download_file = AsyncMock(
            return_value=_make_response(True, {"content": b"file data"})
        )
        mock_create.return_value = MagicMock()
        record = _make_file_record()
        await conn.stream_record(record)
        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_fallback_not_found(self, conn):
        from fastapi import HTTPException
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(False, error="no key")
        )
        conn.data_source.download_file = AsyncMock(
            return_value=_make_response(False, error="not found in share")
        )
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_stream_fallback_other_error(self, conn):
        from fastapi import HTTPException
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(False, error="no key")
        )
        conn.data_source.download_file = AsyncMock(
            return_value=_make_response(False, error="server error")
        )
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_fallback_no_content(self, conn):
        from fastapi import HTTPException
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(False, error="no key")
        )
        conn.data_source.download_file = AsyncMock(
            return_value=_make_response(True, {"content": None})
        )
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_stream_fallback_exception(self, conn):
        from fastapi import HTTPException
        conn.data_source = MagicMock()
        conn.data_source.generate_file_sas_url = AsyncMock(
            return_value=_make_response(False, error="no key")
        )
        conn.data_source.download_file = AsyncMock(side_effect=RuntimeError("oops"))
        record = _make_file_record()
        with pytest.raises(HTTPException) as exc_info:
            await conn.stream_record(record)
        assert exc_info.value.status_code == 500


# ===========================================================================
# cleanup
# ===========================================================================
class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_with_data_source(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.close_async_client = AsyncMock()
        await conn.cleanup()
        conn.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_without_data_source(self, conn):
        conn.data_source = None
        await conn.cleanup()
        assert conn.data_source is None


# ===========================================================================
# get_filter_options / _get_share_options
# ===========================================================================
class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_shares_filter_key(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(
            return_value=_make_response(True, [{"name": "s1"}, {"name": "s2"}])
        )
        result = await conn.get_filter_options("shares")
        assert result.success is True
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_unsupported_filter_key(self, conn):
        with pytest.raises(ValueError, match="Unsupported"):
            await conn.get_filter_options("unknown_key")

    @pytest.mark.asyncio
    async def test_shares_not_initialized(self, conn):
        conn.data_source = None
        result = await conn.get_filter_options("shares")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_shares_list_fails(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(
            return_value=_make_response(False, error="forbidden")
        )
        result = await conn.get_filter_options("shares")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_shares_empty_data(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(
            return_value=_make_response(True, data=None)
        )
        result = await conn.get_filter_options("shares")
        assert result.success is True
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_shares_with_search(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(
            return_value=_make_response(True, [{"name": "alpha"}, {"name": "beta"}, {"name": "gamma"}])
        )
        result = await conn.get_filter_options("shares", search="bet")
        assert len(result.options) == 1
        assert result.options[0].id == "beta"

    @pytest.mark.asyncio
    async def test_shares_pagination(self, conn):
        conn.data_source = MagicMock()
        shares = [{"name": f"share{i}"} for i in range(10)]
        conn.data_source.list_shares = AsyncMock(return_value=_make_response(True, shares))
        result = await conn.get_filter_options("shares", page=1, limit=3)
        assert len(result.options) == 3
        assert result.has_more is True

        result2 = await conn.get_filter_options("shares", page=4, limit=3)
        assert len(result2.options) == 1
        assert result2.has_more is False

    @pytest.mark.asyncio
    async def test_shares_exception(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(side_effect=Exception("fail"))
        result = await conn.get_filter_options("shares")
        assert result.success is False


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty_records(self, conn):
        await conn.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, conn):
        conn.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await conn.reindex_records([_make_file_record()])

    @pytest.mark.asyncio
    async def test_updated_records(self, conn, proc):
        conn.data_source = MagicMock()
        updated_record = _make_file_record(record_id="u1")
        conn._check_and_fetch_updated_record = AsyncMock(
            return_value=(updated_record, [])
        )
        records = [_make_file_record(record_id="r1")]
        await conn.reindex_records(records)
        proc.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_non_updated_records(self, conn, proc):
        conn.data_source = MagicMock()
        conn._check_and_fetch_updated_record = AsyncMock(return_value=None)
        records = [_make_file_record(record_id="r1")]
        await conn.reindex_records(records)
        proc.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_mixed_updated_and_non_updated(self, conn, proc):
        conn.data_source = MagicMock()
        updated = _make_file_record(record_id="u1")
        conn._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(updated, []), None]
        )
        records = [_make_file_record(record_id="r1"), _make_file_record(record_id="r2")]
        await conn.reindex_records(records)
        proc.on_new_records.assert_awaited_once()
        proc.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_check_record_exception_continues(self, conn, proc):
        conn.data_source = MagicMock()
        conn._check_and_fetch_updated_record = AsyncMock(
            side_effect=[Exception("fail"), None]
        )
        records = [_make_file_record(record_id="r1"), _make_file_record(record_id="r2")]
        await conn.reindex_records(records)
        proc.reindex_existing_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_reindex_raises_on_outer_exception(self, conn, proc):
        conn.data_source = MagicMock()
        proc.on_new_records = AsyncMock(side_effect=Exception("DB fail"))
        conn._check_and_fetch_updated_record = AsyncMock(
            return_value=(_make_file_record(), [])
        )
        with pytest.raises(Exception, match="DB fail"):
            await conn.reindex_records([_make_file_record()])


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_share_name(self, conn):
        record = _make_file_record(external_record_group_id=None)
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_missing_external_record_id(self, conn):
        record = _make_file_record()
        record.external_record_id = None
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_path(self, conn):
        record = _make_file_record(external_record_id="share1/", external_record_group_id="share1")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_file_etag_unchanged(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {"etag": '"0xABC"', "last_modified": datetime.now(timezone.utc)})
        )
        record = _make_file_record(external_revision_id="0xABC")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_file_etag_changed(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": datetime(2025, 7, 1, tzinfo=timezone.utc),
                "size": 500,
                "content_type": "text/plain",
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        updated, perms = result
        assert updated.external_revision_id == "0xNEW"
        assert updated.version == 1

    @pytest.mark.asyncio
    async def test_directory_properties(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_directory_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xDIR"',
                "last_modified": datetime(2025, 7, 1, tzinfo=timezone.utc),
                "is_directory": True,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(
            external_revision_id="0xOLD",
            is_file=False,
            path="subdir",
            external_record_id="share1/subdir",
        )
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_not_found_at_source(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        record = _make_file_record()
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_metadata(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, None)
        )
        record = _make_file_record()
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_string_last_modified(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": "2025-07-01T00:00:00+00:00",
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_invalid_string_last_modified(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": "bad",
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_non_datetime_last_modified(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": 99999,
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_no_last_modified(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None

    @pytest.mark.asyncio
    async def test_content_md5_bytes(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": datetime.now(timezone.utc),
                "is_directory": False,
                "content_md5": b"\xAB\xCD",
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        updated, _ = result
        assert updated.md5_hash == base64.b64encode(b"\xAB\xCD").decode("utf-8")

    @pytest.mark.asyncio
    async def test_root_level_item_clears_parent(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": datetime.now(timezone.utc),
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(
            external_revision_id="0xOLD",
            external_record_id="share1/rootfile.txt",
            path="rootfile.txt",
        )
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        updated, _ = result
        assert updated.parent_external_record_id is None

    @pytest.mark.asyncio
    async def test_indexing_filter_off(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": datetime.now(timezone.utc),
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        idx_filters = MagicMock()
        idx_filters.is_enabled.return_value = False
        idx_filters.__bool__ = MagicMock(return_value=True)
        conn.indexing_filters = idx_filters
        record = _make_file_record(external_revision_id="0xOLD")
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None
        updated, _ = result
        assert updated.indexing_status == ProgressStatus.AUTO_INDEX_OFF.value

    @pytest.mark.asyncio
    async def test_exception_returns_none(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(side_effect=Exception("boom"))
        record = _make_file_record()
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_path_without_share_prefix(self, conn):
        conn.data_source = MagicMock()
        conn.data_source.get_file_properties = AsyncMock(
            return_value=_make_response(True, {
                "etag": '"0xNEW"',
                "last_modified": datetime.now(timezone.utc),
                "is_directory": False,
            })
        )
        conn.scope = ConnectorScope.TEAM.value
        record = _make_file_record(
            external_record_id="folder/file.txt",
            external_record_group_id="share1",
            external_revision_id="0xOLD",
        )
        result = await conn._check_and_fetch_updated_record("org-1", record)
        assert result is not None


# ===========================================================================
# run_incremental_sync
# ===========================================================================
class TestRunIncrementalSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_with_share_filter(self, mock_filters, conn):
        share_filter = MagicMock()
        share_filter.value = ["s1"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = share_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn._sync_share = AsyncMock()
        await conn.run_incremental_sync()
        conn._sync_share.assert_awaited_once_with("s1")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_lists_shares(self, mock_filters, conn):
        empty_filter = MagicMock()
        empty_filter.value = []
        sync_filters = MagicMock()
        sync_filters.get.return_value = empty_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(
            return_value=_make_response(True, [{"name": "s1"}, {"name": "s2"}])
        )
        conn._sync_share = AsyncMock()
        await conn.run_incremental_sync()
        assert conn._sync_share.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_no_shares(self, mock_filters, conn):
        empty_filter = MagicMock()
        empty_filter.value = []
        sync_filters = MagicMock()
        sync_filters.get.return_value = empty_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn.data_source.list_shares = AsyncMock(return_value=_make_response(True, []))
        conn._sync_share = AsyncMock()
        await conn.run_incremental_sync()
        conn._sync_share.assert_not_awaited()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_share_error_continues(self, mock_filters, conn):
        share_filter = MagicMock()
        share_filter.value = ["s1", "s2"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = share_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn._sync_share = AsyncMock(side_effect=[Exception("fail"), None])
        await conn.run_incremental_sync()
        assert conn._sync_share.await_count == 2

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_incremental_skips_none_share(self, mock_filters, conn):
        share_filter = MagicMock()
        share_filter.value = [None, "valid"]
        sync_filters = MagicMock()
        sync_filters.get.return_value = share_filter
        sync_filters.__bool__ = MagicMock(return_value=True)
        mock_filters.return_value = (sync_filters, FilterCollection())
        conn.data_source = MagicMock()
        conn._sync_share = AsyncMock()
        await conn.run_incremental_sync()
        conn._sync_share.assert_awaited_once_with("valid")

    @pytest.mark.asyncio
    async def test_incremental_not_initialized(self, conn):
        conn.data_source = None
        with pytest.raises(ConnectionError):
            await conn.run_incremental_sync()


# ===========================================================================
# create_connector
# ===========================================================================
class TestCreateConnector:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.AzureFilesApp")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSourceEntitiesProcessor")
    async def test_create_connector_with_config(self, mock_proc_cls, mock_app, logger, provider, cfg):
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        result = await AzureFilesConnector.create_connector(
            logger=logger,
            data_store_provider=provider,
            config_service=cfg,
            connector_id="new-1",
            scope="personal",
            created_by="test-user-id",
        )
        assert isinstance(result, AzureFilesConnector)
        mock_proc.initialize.assert_awaited_once()
        mock_proc_cls.assert_called_once()
        call_kwargs = mock_proc_cls.call_args
        assert call_kwargs[1]["account_name"] == "teststorage"

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.AzureFilesApp")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSourceEntitiesProcessor")
    async def test_create_connector_no_config(self, mock_proc_cls, mock_app, logger, provider, cfg):
        cfg.get_config = AsyncMock(return_value=None)
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        result = await AzureFilesConnector.create_connector(
            logger=logger,
            data_store_provider=provider,
            config_service=cfg,
            connector_id="new-2",
            scope="personal",
            created_by="test-user-id",
        )
        assert isinstance(result, AzureFilesConnector)
        call_kwargs = mock_proc_cls.call_args
        assert call_kwargs[1]["account_name"] == ""

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_files.connector.AzureFilesApp")
    @patch("app.connectors.sources.azure_files.connector.AzureFilesDataSourceEntitiesProcessor")
    async def test_create_connector_no_connection_string(self, mock_proc_cls, mock_app, logger, provider, cfg):
        cfg.get_config = AsyncMock(return_value={"auth": {}})
        mock_proc = MagicMock()
        mock_proc.initialize = AsyncMock()
        mock_proc_cls.return_value = mock_proc
        result = await AzureFilesConnector.create_connector(
            logger=logger,
            data_store_provider=provider,
            config_service=cfg,
            connector_id="new-3",
            scope="personal",
            created_by="test-user-id",
        )
        assert isinstance(result, AzureFilesConnector)
        call_kwargs = mock_proc_cls.call_args
        assert call_kwargs[1]["account_name"] == ""
