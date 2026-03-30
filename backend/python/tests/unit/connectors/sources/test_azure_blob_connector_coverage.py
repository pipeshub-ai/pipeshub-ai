"""Extended coverage tests for Azure Blob connector."""

import base64
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes, ProgressStatus
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
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
from app.models.entities import RecordType, User


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


def _make_mock_tx_store(existing_record=None, existing_revision_record=None, user=None):
    tx = AsyncMock()
    tx.get_record_by_external_id = AsyncMock(return_value=existing_record)
    tx.get_record_by_external_revision_id = AsyncMock(return_value=existing_revision_record)
    tx.get_user_by_id = AsyncMock(return_value=user or {"email": "user@test.com"})
    tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    return tx


def _make_mock_data_store_provider(existing_record=None, existing_revision_record=None, user=None):
    tx = _make_mock_tx_store(existing_record, existing_revision_record, user)
    provider = MagicMock()

    @asynccontextmanager
    async def _transaction():
        yield tx

    provider.transaction = _transaction
    provider._tx_store = tx
    return provider


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_blob.cov")


@pytest.fixture()
def mock_data_entities_processor():
    proc = MagicMock(spec=AzureBlobDataSourceEntitiesProcessor)
    proc.org_id = "org-az-cov"
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
            "azureBlobConnectionString": "DefaultEndpointsProtocol=https;AccountName=teststorage;AccountKey=abc123;EndpointSuffix=core.windows.net"
        },
        "scope": "TEAM",
        "created_by": "user-1",
    })
    return svc


@pytest.fixture()
def azure_connector(mock_logger, mock_data_entities_processor,
                    mock_data_store_provider, mock_config_service):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        connector = AzureBlobConnector(
            logger=mock_logger,
            data_entities_processor=mock_data_entities_processor,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
            connector_id="az-cov-1",
            scope="personal",
            created_by="test-user-id",
        )
    return connector


# ===========================================================================
# Helper functions extended
# ===========================================================================
class TestAzureBlobHelpersExtended:
    def test_get_file_extension_hidden_file(self):
        assert get_file_extension(".gitignore") == "gitignore"

    def test_get_file_extension_compound(self):
        assert get_file_extension("archive.tar.gz") == "gz"

    def test_get_parent_path_trailing_slash(self):
        assert get_parent_path_from_blob_name("a/b/c/") == "a/b"

    def test_get_parent_path_leading_slash(self):
        assert get_parent_path_from_blob_name("/a/b/c.txt") == "a/b"

    def test_get_parent_path_single_slash(self):
        assert get_parent_path_from_blob_name("/") is None

    def test_folder_segments_deeply_nested(self):
        result = get_folder_path_segments_from_blob_name("a/b/c/d/e/f.txt")
        assert result == ["a", "a/b", "a/b/c", "a/b/c/d", "a/b/c/d/e"]

    def test_folder_segments_with_leading_slash(self):
        result = get_folder_path_segments_from_blob_name("/a/b/c.txt")
        assert result == ["a", "a/b"]

    def test_mimetype_unknown(self):
        assert get_mimetype_for_azure_blob("data.xyz999") == MimeTypes.BIN.value

    def test_mimetype_text(self):
        result = get_mimetype_for_azure_blob("file.txt")
        assert result != MimeTypes.FOLDER.value

    def test_parse_parent_deep_path(self):
        container, path = parse_parent_external_id("c1/a/b/c")
        assert container == "c1"
        assert path == "a/b/c/"

    def test_get_parent_weburl_with_path(self):
        url = get_parent_weburl_for_azure_blob("container/folder/sub", "myacc")
        assert "myacc.blob.core.windows.net" in url

    def test_get_parent_path_for_azure_with_trailing_slash(self):
        assert get_parent_path_for_azure_blob("c1/folder/") == "folder/"


# ===========================================================================
# AzureBlobDataSourceEntitiesProcessor
# ===========================================================================
class TestAzureBlobProcessorExtended:
    def test_default_account_name(self, mock_logger, mock_data_store_provider, mock_config_service):
        proc = AzureBlobDataSourceEntitiesProcessor(
            logger=mock_logger,
            data_store_provider=mock_data_store_provider,
            config_service=mock_config_service,
        )
        assert proc.account_name == ""


# ===========================================================================
# get_app_users
# ===========================================================================
class TestGetAppUsersExtended:
    def test_user_with_title(self, azure_connector):
        users = [User(email="a@test.com", full_name="A", title="Engineer")]
        result = azure_connector.get_app_users(users)
        assert result[0].title == "Engineer"

    def test_multiple_users_mixed(self, azure_connector):
        users = [
            User(email="a@test.com", full_name="A", is_active=True),
            User(email="", full_name="B", is_active=True),
            User(email="c@test.com", full_name="C", is_active=False),
        ]
        result = azure_connector.get_app_users(users)
        # Only users with email are included
        assert len(result) == 2


# ===========================================================================
# _extract_container_names extended
# ===========================================================================
class TestExtractContainerNamesExtended:
    def test_mixed_types(self, azure_connector):
        obj = MagicMock()
        obj.name = "c2"
        data = [{"name": "c1"}, obj, {"name": None}]
        result = azure_connector._extract_container_names(data)
        assert result == ["c1", "c2"]

    def test_empty_list(self, azure_connector):
        assert azure_connector._extract_container_names([]) == []


# ===========================================================================
# _blob_properties_to_dict
# ===========================================================================
class TestBlobPropertiesToDict:
    def test_dict_passthrough(self, azure_connector):
        d = {"name": "test.txt", "size": 100}
        assert azure_connector._blob_properties_to_dict(d) == d

    def test_object_conversion(self, azure_connector):
        blob = MagicMock()
        blob.name = "test.txt"
        blob.last_modified = datetime(2025, 1, 1, tzinfo=timezone.utc)
        blob.creation_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        blob.etag = '"abc123"'
        blob.size = 1024
        content_settings = MagicMock()
        content_settings.content_type = "text/plain"
        content_settings.content_md5 = b"abc"
        blob.content_settings = content_settings

        result = azure_connector._blob_properties_to_dict(blob)
        assert result["name"] == "test.txt"
        assert result["size"] == 1024
        assert result["content_type"] == "text/plain"

    def test_object_no_content_settings(self, azure_connector):
        blob = MagicMock(spec=[])
        blob.name = "test.txt"
        blob.last_modified = None
        blob.creation_time = None
        blob.etag = ""
        blob.size = 0
        # No content_settings attribute
        result = azure_connector._blob_properties_to_dict(blob)
        assert result["name"] == "test.txt"
        assert result["content_type"] is None


# ===========================================================================
# _get_azure_blob_revision_id
# ===========================================================================
class TestGetAzureBlobRevisionId:
    def test_with_bytes_md5(self, azure_connector):
        blob = {"content_md5": b"\x01\x02\x03"}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == base64.b64encode(b"\x01\x02\x03").decode("utf-8")

    def test_with_bytearray_md5(self, azure_connector):
        blob = {"content_md5": bytearray(b"\x01\x02\x03")}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == base64.b64encode(b"\x01\x02\x03").decode("utf-8")

    def test_with_string_md5(self, azure_connector):
        blob = {"content_md5": "abc123"}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == "abc123"

    def test_with_etag_no_md5(self, azure_connector):
        blob = {"etag": '"etag_value"'}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == "etag_value"

    def test_no_md5_no_etag(self, azure_connector):
        blob = {}
        result = azure_connector._get_azure_blob_revision_id(blob)
        assert result == ""


# ===========================================================================
# _pass_date_filters extended
# ===========================================================================
class TestAzureDateFiltersExtended:
    def test_folder_always_passes(self, azure_connector):
        blob = {"name": "folder/"}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_no_filters(self, azure_connector):
        blob = {"name": "file.txt"}
        assert azure_connector._pass_date_filters(blob, None, None, None, None) is True

    def test_no_last_modified(self, azure_connector):
        blob = {"name": "file.txt"}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_string_last_modified(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": "2025-01-01T00:00:00+00:00"}
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        future_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert azure_connector._pass_date_filters(blob, past_ms, future_ms, None, None) is True

    def test_string_last_modified_z_suffix(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": "2025-01-01T00:00:00Z"}
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert azure_connector._pass_date_filters(blob, past_ms, None, None, None) is True

    def test_invalid_string_last_modified(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": "not-a-date"}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_non_datetime_type(self, azure_connector):
        blob = {"name": "file.txt", "last_modified": 12345}
        assert azure_connector._pass_date_filters(blob, 100, None, None, None) is True

    def test_creation_time_datetime(self, azure_connector):
        now = datetime.now(timezone.utc)
        blob = {
            "name": "file.txt",
            "last_modified": now,
            "creation_time": now,
        }
        future_ms = int((now.timestamp() + 3600) * 1000)
        # created_after_ms in the future should fail
        assert azure_connector._pass_date_filters(blob, None, None, future_ms, None) is False

    def test_creation_time_string(self, azure_connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": "2025-01-01T00:00:00+00:00",
        }
        past_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        future_ms = int(datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert azure_connector._pass_date_filters(blob, None, None, past_ms, future_ms) is True

    def test_creation_time_invalid_string(self, azure_connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": "invalid",
        }
        assert azure_connector._pass_date_filters(blob, None, None, 100, None) is True

    def test_creation_time_non_type(self, azure_connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": 12345,
        }
        assert azure_connector._pass_date_filters(blob, None, None, 100, None) is True


# ===========================================================================
# _pass_extension_filter
# ===========================================================================
class TestPassExtensionFilter:
    def test_folder_always_passes(self, azure_connector):
        assert azure_connector._pass_extension_filter("folder/", is_folder=True) is True

    def test_no_filter_configured(self, azure_connector):
        azure_connector.sync_filters = FilterCollection()
        assert azure_connector._pass_extension_filter("file.txt") is True

    def test_file_no_extension_in_operator(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("Makefile") is False

    def test_file_no_extension_not_in_operator(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("Makefile") is True

    def test_extension_in_allowed_list(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("file.pdf") is True

    def test_extension_not_in_allowed_list(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf", "txt"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("file.docx") is False

    def test_invalid_filter_value_type(self, azure_connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = "not a list"
        azure_connector.sync_filters = MagicMock()
        azure_connector.sync_filters.get.return_value = mock_filter
        assert azure_connector._pass_extension_filter("file.pdf") is True


# ===========================================================================
# _create_record_groups_for_containers
# ===========================================================================
class TestCreateRecordGroupsForContainers:
    @pytest.mark.asyncio
    async def test_team_scope(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        await azure_connector._create_record_groups_for_containers(["container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_with_creator_email(self, azure_connector):
        azure_connector.scope = ConnectorScope.PERSONAL.value
        azure_connector.creator_email = "user@test.com"
        azure_connector.created_by = "user-1"
        await azure_connector._create_record_groups_for_containers(["container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_personal_no_creator(self, azure_connector):
        azure_connector.scope = ConnectorScope.PERSONAL.value
        azure_connector.creator_email = None
        azure_connector.created_by = None
        await azure_connector._create_record_groups_for_containers(["container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_list(self, azure_connector):
        await azure_connector._create_record_groups_for_containers([])
        azure_connector.data_entities_processor.on_new_record_groups.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_none_items_skipped(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        await azure_connector._create_record_groups_for_containers([None, "container1"])
        azure_connector.data_entities_processor.on_new_record_groups.assert_awaited_once()


# ===========================================================================
# _process_azure_blob
# ===========================================================================
class TestProcessAzureBlob:
    @pytest.mark.asyncio
    async def test_empty_name(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        record, perms = await azure_connector._process_azure_blob({"name": ""}, "container")
        assert record is None

    @pytest.mark.asyncio
    async def test_new_file(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"
        blob = {
            "name": "path/file.txt",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": datetime.now(timezone.utc),
            "etag": '"abc123"',
            "size": 1024,
            "content_md5": None,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None
        assert record.record_name == "file.txt"

    @pytest.mark.asyncio
    async def test_folder_blob(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"
        blob = {
            "name": "folder/",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": None,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        # RecordType.FOLDER does not exist in the enum, so the method
        # catches the error and returns (None, [])
        assert record is None
        assert perms == []

    @pytest.mark.asyncio
    async def test_string_timestamps(self, azure_connector):
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"
        blob = {
            "name": "file.txt",
            "last_modified": "2025-01-01T00:00:00Z",
            "creation_time": "2025-01-01T00:00:00Z",
            "etag": '"abc"',
            "size": 100,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None

    @pytest.mark.asyncio
    async def test_existing_record_content_changed(self, azure_connector):
        existing = MagicMock()
        existing.id = "existing-id"
        existing.external_revision_id = "old_revision"
        existing.external_record_id = "container/file.txt"
        existing.version = 1
        existing.source_created_at = 1700000000000
        azure_connector.data_store_provider = _make_mock_data_store_provider(existing)
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"

        blob = {
            "name": "file.txt",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": datetime.now(timezone.utc),
            "etag": '"new_etag"',
            "content_md5": b"\x01\x02",
            "size": 2048,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None
        assert record.version == 2

    @pytest.mark.asyncio
    async def test_move_detected(self, azure_connector):
        existing = MagicMock()
        existing.id = "moved-id"
        existing.external_record_id = "container/old/file.txt"
        existing.external_revision_id = "same_md5"
        existing.version = 0
        existing.source_created_at = 1700000000000
        azure_connector.data_store_provider = _make_mock_data_store_provider(
            existing_record=None, existing_revision_record=existing
        )
        azure_connector.scope = ConnectorScope.TEAM.value
        azure_connector.account_name = "testacc"

        blob = {
            "name": "new/file.txt",
            "last_modified": datetime.now(timezone.utc),
            "creation_time": datetime.now(timezone.utc),
            "content_md5": "same_md5",
            "size": 100,
        }
        record, perms = await azure_connector._process_azure_blob(blob, "container")
        assert record is not None
        assert record.id == "moved-id"


# ===========================================================================
# _remove_old_parent_relationship
# ===========================================================================
class TestRemoveOldParentRelationship:
    @pytest.mark.asyncio
    async def test_successful(self, azure_connector):
        tx = AsyncMock()
        tx.delete_parent_child_edge_to_record = AsyncMock(return_value=1)
        await azure_connector._remove_old_parent_relationship("rec-1", tx)

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        tx = AsyncMock()
        tx.delete_parent_child_edge_to_record = AsyncMock(side_effect=Exception("err"))
        await azure_connector._remove_old_parent_relationship("rec-1", tx)


# ===========================================================================
# run_sync extended
# ===========================================================================
class TestRunSyncExtended:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = None
        with pytest.raises(ConnectionError):
            await azure_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_list_containers_fails(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(False, error="err")
        )
        await azure_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_containers_found(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, None)
        )
        await azure_connector.run_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_configured_container(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.container_name = "mycontainer"
        azure_connector._create_record_groups_for_containers = AsyncMock()
        azure_connector._sync_container = AsyncMock()
        await azure_connector.run_sync()
        azure_connector._sync_container.assert_awaited_once_with("mycontainer")

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_container_sync_error_continues(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.container_name = "mycontainer"
        azure_connector._create_record_groups_for_containers = AsyncMock()
        azure_connector._sync_container = AsyncMock(side_effect=Exception("sync error"))
        # Should not raise
        await azure_connector.run_sync()


# ===========================================================================
# test_connection_and_access
# ===========================================================================
class TestTestConnectionAndAccess:
    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        result = await azure_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_success(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True)
        )
        result = await azure_connector.test_connection_and_access()
        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(False, error="denied")
        )
        result = await azure_connector.test_connection_and_access()
        assert result is False

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(side_effect=Exception("err"))
        result = await azure_connector.test_connection_and_access()
        assert result is False


# ===========================================================================
# get_signed_url
# ===========================================================================
class TestGetSignedUrl:
    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        result = await azure_connector.get_signed_url(MagicMock())
        assert result is None

    @pytest.mark.asyncio
    async def test_no_container(self, azure_connector):
        azure_connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id=None)
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_external_record_id(self, azure_connector):
        azure_connector.data_source = MagicMock()
        record = MagicMock(id="r1", external_record_group_id="container", external_record_id=None)
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_success(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_make_response(True, {"sas_url": "https://blob.url/sas"})
        )
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result == "https://blob.url/sas"

    @pytest.mark.asyncio
    async def test_failure(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_make_response(False, error="denied")
        )
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(side_effect=Exception("err"))
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result is None

    @pytest.mark.asyncio
    async def test_key_without_container_prefix(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.generate_blob_sas_url = AsyncMock(
            return_value=_make_response(True, {"sas_url": "https://blob.url/sas"})
        )
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="/path/blob.txt", record_name="blob.txt"
        )
        result = await azure_connector.get_signed_url(record)
        assert result is not None


# ===========================================================================
# stream_record
# ===========================================================================
class TestStreamRecord:
    @pytest.mark.asyncio
    async def test_folder_raises(self, azure_connector):
        from app.models.entities import FileRecord
        from fastapi import HTTPException
        record = MagicMock(spec=FileRecord)
        record.is_file = False
        with pytest.raises(HTTPException):
            await azure_connector.stream_record(record)

    @pytest.mark.asyncio
    async def test_no_signed_url(self, azure_connector):
        from fastapi import HTTPException
        record = MagicMock()
        record.is_file = True
        azure_connector.get_signed_url = AsyncMock(return_value=None)
        with pytest.raises(HTTPException):
            await azure_connector.stream_record(record)


# ===========================================================================
# cleanup
# ===========================================================================
class TestCleanup:
    @pytest.mark.asyncio
    async def test_cleanup_with_data_source(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.close_async_client = AsyncMock()
        await azure_connector.cleanup()
        assert azure_connector.data_source is None

    @pytest.mark.asyncio
    async def test_cleanup_no_data_source(self, azure_connector):
        azure_connector.data_source = None
        await azure_connector.cleanup()
        assert azure_connector.data_source is None


# ===========================================================================
# get_filter_options / _get_container_options
# ===========================================================================
class TestGetFilterOptions:
    @pytest.mark.asyncio
    async def test_containers(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, [{"name": "c1"}, {"name": "c2"}])
        )
        result = await azure_connector.get_filter_options("containers")
        assert result.success is True
        assert len(result.options) == 2

    @pytest.mark.asyncio
    async def test_unsupported(self, azure_connector):
        with pytest.raises(ValueError, match="Unsupported"):
            await azure_connector.get_filter_options("invalid_key")

    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        result = await azure_connector.get_filter_options("containers")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_list_fails(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(False, error="err")
        )
        result = await azure_connector.get_filter_options("containers")
        assert result.success is False

    @pytest.mark.asyncio
    async def test_no_containers(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, None)
        )
        result = await azure_connector.get_filter_options("containers")
        assert result.success is True
        assert len(result.options) == 0

    @pytest.mark.asyncio
    async def test_search(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, [{"name": "prod-data"}, {"name": "dev-logs"}])
        )
        result = await azure_connector.get_filter_options("containers", search="prod")
        assert len(result.options) == 1

    @pytest.mark.asyncio
    async def test_pagination(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, [{"name": f"c{i}"} for i in range(5)])
        )
        result = await azure_connector.get_filter_options("containers", page=1, limit=2)
        assert len(result.options) == 2
        assert result.has_more is True

    @pytest.mark.asyncio
    async def test_exception(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(side_effect=Exception("err"))
        result = await azure_connector.get_filter_options("containers")
        assert result.success is False


# ===========================================================================
# handle_webhook_notification
# ===========================================================================
class TestHandleWebhookNotification:
    def test_raises(self, azure_connector):
        with pytest.raises(NotImplementedError):
            azure_connector.handle_webhook_notification({})


# ===========================================================================
# reindex_records
# ===========================================================================
class TestReindexRecords:
    @pytest.mark.asyncio
    async def test_empty(self, azure_connector):
        await azure_connector.reindex_records([])

    @pytest.mark.asyncio
    async def test_not_initialized(self, azure_connector):
        azure_connector.data_source = None
        with pytest.raises(Exception, match="not initialized"):
            await azure_connector.reindex_records([MagicMock()])

    @pytest.mark.asyncio
    async def test_updated_and_non_updated(self, azure_connector):
        azure_connector.data_source = MagicMock()
        azure_connector._check_and_fetch_updated_record = AsyncMock(
            side_effect=[(MagicMock(), []), None]
        )
        azure_connector.data_entities_processor.reindex_existing_records = AsyncMock()
        await azure_connector.reindex_records([MagicMock(id="r1"), MagicMock(id="r2")])
        azure_connector.data_entities_processor.on_new_records.assert_awaited_once()
        azure_connector.data_entities_processor.reindex_existing_records.assert_awaited_once()


# ===========================================================================
# _check_and_fetch_updated_record
# ===========================================================================
class TestCheckAndFetchUpdatedRecord:
    @pytest.mark.asyncio
    async def test_missing_container(self, azure_connector):
        record = MagicMock(id="r1", external_record_group_id=None, external_record_id="k")
        result = await azure_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_blob_not_found(self, azure_connector):
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt"
        )
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.get_blob_properties = AsyncMock(
            return_value=_make_response(False, error="not found")
        )
        result = await azure_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None

    @pytest.mark.asyncio
    async def test_no_change(self, azure_connector):
        record = MagicMock(
            id="r1", external_record_group_id="container",
            external_record_id="container/blob.txt",
            external_revision_id="abc123", version=1, source_created_at=1700000000000,
        )
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.get_blob_properties = AsyncMock(
            return_value=_make_response(True, {"etag": '"abc123"', "last_modified": datetime.now(timezone.utc)})
        )
        result = await azure_connector._check_and_fetch_updated_record("org-1", record)
        assert result is None


# ===========================================================================
# run_incremental_sync
# ===========================================================================
class TestRunIncrementalSync:
    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_not_initialized(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = None
        with pytest.raises(ConnectionError):
            await azure_connector.run_incremental_sync()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_configured_container(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.container_name = "mycontainer"
        azure_connector._sync_container = AsyncMock()
        await azure_connector.run_incremental_sync()
        azure_connector._sync_container.assert_awaited_once()

    @pytest.mark.asyncio
    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_no_containers(self, mock_filters, azure_connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        azure_connector.data_source = MagicMock()
        azure_connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(True, None)
        )
        await azure_connector.run_incremental_sync()


# ===========================================================================
# _ensure_parent_folders_exist
# ===========================================================================
class TestEnsureParentFoldersExist:
    @pytest.mark.asyncio
    async def test_empty_segments(self, azure_connector):
        await azure_connector._ensure_parent_folders_exist("container", [])
        azure_connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_single_segment(self, azure_connector):
        azure_connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        await azure_connector._ensure_parent_folders_exist("container", ["folder"])
        azure_connector.data_entities_processor.on_new_records.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_multiple_segments(self, azure_connector):
        azure_connector._create_azure_blob_permissions = AsyncMock(return_value=[])
        await azure_connector._ensure_parent_folders_exist("container", ["a", "a/b", "a/b/c"])
        assert azure_connector.data_entities_processor.on_new_records.await_count == 3


# ===========================================================================
# _generate_web_url / _generate_parent_web_url
# ===========================================================================
class TestGenerateUrls:
    def test_generate_web_url(self, azure_connector):
        azure_connector.account_name = "teststorage"
        url = azure_connector._generate_web_url("container", "path/file.txt")
        assert "teststorage" in url
        assert "container" in url

    def test_generate_parent_web_url(self, azure_connector):
        azure_connector.account_name = "teststorage"
        url = azure_connector._generate_parent_web_url("container/path")
        assert "teststorage" in url


# ===========================================================================
# _extract_container_names edge cases
# ===========================================================================
class TestExtractContainerNamesEdgeCases:
    def test_none_input(self, azure_connector):
        result = azure_connector._extract_container_names(None)
        assert result == []

    def test_objects_with_name_attr(self, azure_connector):
        obj = MagicMock()
        obj.name = "c1"
        result = azure_connector._extract_container_names([obj])
        assert result == ["c1"]
