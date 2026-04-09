"""Extended coverage tests for Azure Files connector."""

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import quote

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.connector_builder import ConnectorScope
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
@pytest.fixture()
def mock_logger():
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
    proc.get_user_by_user_id = AsyncMock(
        return_value=User(
            email="user@test.com",
            source_user_id="src-1",
            org_id="org-azf-1",
            full_name="Test User",
            title="Title",
        )
    )
    return proc


@pytest.fixture()
def mock_data_store_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.ensure_team_app_edge = AsyncMock()
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
def connector(mock_logger, mock_data_entities_processor,
              mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_files.connector.AzureFilesApp"):
        c = AzureFilesConnector(
            logger=mock_logger,
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
class TestAzureFilesRunSync:
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
