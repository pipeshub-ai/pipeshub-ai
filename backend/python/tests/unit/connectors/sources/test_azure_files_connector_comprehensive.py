"""Comprehensive tests for Azure Files connector - extended coverage."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.filters import (
    FilterCollection,
    FilterOperator,
    MultiselectOperator,
    SyncFilterKey,
)
from app.connectors.sources.azure_files.connector import (
    AzureFilesConnector,
    AzureFilesDataSourceEntitiesProcessor,
    get_file_extension,
    get_mimetype_for_azure_files,
    get_parent_path,
)
from app.models.entities import FileRecord, RecordType, User
from app.models.permission import EntityType, Permission, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _make_mock_tx_store(existing_record=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    tx.remove_record_from_parent = AsyncMock()
    return tx


def _make_mock_data_store_provider(existing_record=None):
    tx = _make_mock_tx_store(existing_record)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_files_comp")


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
    return _make_mock_data_store_provider()


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
def connector(mock_logger, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
        c = AzureFilesConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="azf-comp-1",
        )
    return c


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


# ===========================================================================
# Helper functions - extended
# ===========================================================================
class TestHelperFunctions:
    def test_get_file_extension_deep_path(self):
        assert get_file_extension("a/b/c/d/e/report.xlsx") == "xlsx"

    def test_get_file_extension_double_dot(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_file_extension_dot_only(self):
        # edge case: filename is just a dot - returns empty string since last part is empty
        assert get_file_extension(".") == ""

    def test_get_parent_path_multiple_levels(self):
        assert get_parent_path("a/b/c/d/e/file.txt") == "a/b/c/d/e"

    def test_get_parent_path_single_dir(self):
        assert get_parent_path("dir/file.txt") == "dir"

    def test_get_mimetype_for_azure_files_directory(self):
        assert get_mimetype_for_azure_files("anything", is_directory=True) == MimeTypes.FOLDER.value

    def test_get_mimetype_for_azure_files_known(self):
        result = get_mimetype_for_azure_files("test.pdf")
        assert result == MimeTypes.PDF.value

    def test_get_mimetype_for_azure_files_unknown(self):
        result = get_mimetype_for_azure_files("test.xyz123")
        assert result == MimeTypes.BIN.value

    def test_get_mimetype_for_azure_files_no_extension(self):
        result = get_mimetype_for_azure_files("Makefile")
        assert result == MimeTypes.BIN.value


# ===========================================================================
# AzureFilesDataSourceEntitiesProcessor
# ===========================================================================
class TestAzureFilesProcessorComprehensive:
    def test_generate_directory_url_with_path(self):
        proc = AzureFilesDataSourceEntitiesProcessor.__new__(
            AzureFilesDataSourceEntitiesProcessor
        )
        proc.account_name = "myaccount"
        url = proc._generate_directory_url("myshare/subdir/folder")
        assert "myaccount.file.core.windows.net" in url
        assert "myshare" in url

    def test_generate_directory_url_root(self):
        proc = AzureFilesDataSourceEntitiesProcessor.__new__(
            AzureFilesDataSourceEntitiesProcessor
        )
        proc.account_name = "myaccount"
        url = proc._generate_directory_url("myshare")
        assert url == "https://myaccount.file.core.windows.net/myshare"

    def test_extract_path_from_external_id_with_path(self):
        proc = AzureFilesDataSourceEntitiesProcessor.__new__(
            AzureFilesDataSourceEntitiesProcessor
        )
        assert proc._extract_path_from_external_id("share/sub/path") == "sub/path"

    def test_extract_path_from_external_id_root(self):
        proc = AzureFilesDataSourceEntitiesProcessor.__new__(
            AzureFilesDataSourceEntitiesProcessor
        )
        assert proc._extract_path_from_external_id("share") is None


# ===========================================================================
# Init
# ===========================================================================
class TestInitComprehensive:
    @pytest.mark.asyncio
    async def test_init_no_config(self, connector):
        connector.config_service.get_config = AsyncMock(return_value=None)
        result = await connector.init()
        assert result is False

    @pytest.mark.asyncio
    async def test_init_no_connection_string(self, connector):
        connector.config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await connector.init()
        assert result is False


# ===========================================================================
# URL Generation
# ===========================================================================
class TestURLGeneration:
    def test_generate_web_url(self, connector):
        connector.account_name = "myaccount"
        url = connector._generate_web_url("myshare", "file.txt")
        assert "myaccount" in url
        assert "myshare" in url

    def test_generate_directory_url_with_path(self, connector):
        connector.account_name = "myaccount"
        url = connector._generate_directory_url("myshare", "subdir")
        assert "myshare" in url
        assert "subdir" in url

    def test_generate_directory_url_empty(self, connector):
        connector.account_name = "myaccount"
        url = connector._generate_directory_url("myshare", "")
        assert "myshare" in url


# ===========================================================================
# get_app_users
# ===========================================================================
class TestGetAppUsers:
    def test_converts_users(self, connector):
        user = User(
            email="user@test.com",
            full_name="Test User",
        )
        result = connector.get_app_users([user])
        assert len(result) == 1
        assert result[0].email == "user@test.com"


# ===========================================================================
# _pass_date_filters
# ===========================================================================
class TestPassDateFiltersComprehensive:
    def test_directory_always_passes(self, connector):
        # is_directory is passed inside the item dict
        assert connector._pass_date_filters({"is_directory": True}) is True

    def test_no_filters_passes(self, connector):
        assert connector._pass_date_filters({}) is True

    def test_with_datetime_object(self, connector):
        dt = datetime(2025, 1, 15, tzinfo=timezone.utc)
        modified_after_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

        result = connector._pass_date_filters(
            {"last_modified": dt},
            modified_after_ms=modified_after_ms,
        )
        assert result is True

    def test_with_string_date(self, connector):
        result = connector._pass_date_filters({"last_modified": "2025-01-15T00:00:00Z"})
        assert result is True

    def test_modified_before_cutoff_blocks(self, connector):
        dt = datetime(2025, 6, 15, tzinfo=timezone.utc)
        modified_before_ms = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        result = connector._pass_date_filters(
            {"last_modified": dt},
            modified_before_ms=modified_before_ms,
        )
        assert result is False

    def test_created_after_cutoff_blocks(self, connector):
        created_after_ms = int(datetime(2025, 6, 1, tzinfo=timezone.utc).timestamp() * 1000)
        result = connector._pass_date_filters(
            {"last_modified": datetime(2025, 6, 15, tzinfo=timezone.utc),
             "creation_time": datetime(2025, 1, 1, tzinfo=timezone.utc)},
            created_after_ms=created_after_ms,
        )
        assert result is False


# ===========================================================================
# _pass_extension_filter
# ===========================================================================
class TestPassExtensionFilterComprehensive:
    def test_directory_always_passes(self, connector):
        assert connector._pass_extension_filter("any/path", is_directory=True) is True

    def test_no_filter_passes(self, connector):
        connector.sync_filters = FilterCollection()
        assert connector._pass_extension_filter("file.txt") is True

    def test_in_operator_match(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["txt", "pdf"]
        mock_filter.get_operator.return_value = MultiselectOperator.IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_extension_filter("file.txt") is True

    def test_in_operator_no_match(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["txt", "pdf"]
        mock_filter.get_operator.return_value = MultiselectOperator.IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_extension_filter("file.xlsx") is False

    def test_not_in_operator(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["txt"]
        mock_filter.get_operator.return_value = MultiselectOperator.NOT_IN
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.return_value = mock_filter

        assert connector._pass_extension_filter("file.pdf") is True
        assert connector._pass_extension_filter("file.txt") is False


# ===========================================================================
# _get_azure_files_revision_id
# ===========================================================================
class TestGetRevisionId:
    def test_with_etag(self, connector):
        item = {"etag": '"0x12345"'}
        result = connector._get_azure_files_revision_id(item)
        assert "0x12345" in result

    def test_with_last_modified(self, connector):
        item = {"last_modified": datetime(2025, 1, 15, tzinfo=timezone.utc)}
        result = connector._get_azure_files_revision_id(item)
        assert result is not None

    def test_with_content_settings(self, connector):
        item = {"content_settings": MagicMock(content_md5=b"\x01\x02\x03")}
        result = connector._get_azure_files_revision_id(item)
        assert result is not None


# ===========================================================================
# _extract_account_name_from_connection_string
# ===========================================================================
class TestExtractAccountName:
    def test_normal_connection_string(self):
        conn_str = "DefaultEndpointsProtocol=https;AccountName=myaccount;AccountKey=abc;EndpointSuffix=core.windows.net"
        result = AzureFilesConnector._extract_account_name_from_connection_string(conn_str)
        assert result == "myaccount"

    def test_missing_account_name(self):
        conn_str = "DefaultEndpointsProtocol=https;AccountKey=abc"
        result = AzureFilesConnector._extract_account_name_from_connection_string(conn_str)
        assert result is None

    def test_empty_string(self):
        result = AzureFilesConnector._extract_account_name_from_connection_string("")
        assert result is None


# ===========================================================================
# _get_date_filters
# ===========================================================================
class TestGetDateFilters:
    def test_no_filters(self, connector):
        connector.sync_filters = FilterCollection()
        result = connector._get_date_filters()
        assert all(v is None for v in result)

    def test_with_modified_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        # Python 3.10 fromisoformat doesn't support Z suffix
        mock_filter.get_datetime_iso.return_value = ("2025-01-01T00:00:00+00:00", "2025-12-31T00:00:00+00:00")
        connector.sync_filters = MagicMock()
        connector.sync_filters.get.side_effect = lambda key: mock_filter if key == SyncFilterKey.MODIFIED else None

        result = connector._get_date_filters()
        assert result[0] is not None  # modified_after_ms
        assert result[1] is not None  # modified_before_ms


# ===========================================================================
# run_sync
# ===========================================================================
class TestRunSyncComprehensive:
    @pytest.mark.asyncio
    async def test_run_sync_no_data_source(self, connector):
        with patch(
            "app.connectors.sources.azure_files.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector.data_source = None
            with pytest.raises(Exception):
                await connector.run_sync()

    @pytest.mark.asyncio
    async def test_run_sync_list_shares(self, connector):
        with patch(
            "app.connectors.sources.azure_files.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector.data_source = AsyncMock()
            connector.data_source.list_shares = AsyncMock(return_value=_make_response(
                success=True, data=[{"name": "share1"}]
            ))
            connector._create_record_groups_for_shares = AsyncMock()
            connector._sync_share = AsyncMock()
            connector.account_name = "testaccount"
            connector.scope = "TEAM"

            await connector.run_sync()

            connector._sync_share.assert_awaited_once()


# ===========================================================================
# Misc
# ===========================================================================
class TestMiscComprehensive:
    def test_handle_webhook_notification_raises(self, connector):
        with pytest.raises(NotImplementedError):
            connector.handle_webhook_notification({})

    @pytest.mark.asyncio
    async def test_cleanup(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.close_async_client = AsyncMock()
        await connector.cleanup()
        assert connector.data_source is None

    @pytest.mark.asyncio
    async def test_run_incremental_sync_not_initialized(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_incremental_sync()

    @pytest.mark.asyncio
    async def test_run_incremental_sync_initialized(self, connector):
        connector.data_source = AsyncMock()
        connector.data_source.list_shares = AsyncMock(return_value=_make_response(data=[]))
        with patch(
            "app.connectors.sources.azure_files.connector.load_connector_filters",
            new_callable=AsyncMock,
            return_value=(FilterCollection(), FilterCollection()),
        ):
            connector._create_record_groups_for_shares = AsyncMock()
            connector._sync_share = AsyncMock()
            # incremental sync just calls run_sync under the hood after checking data_source
            await connector.run_incremental_sync()
