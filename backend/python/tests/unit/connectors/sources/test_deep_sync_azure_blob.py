"""Deep sync loop tests for AzureBlobConnector."""

import logging
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import MimeTypes
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.core.registry.filters import FilterCollection, FilterOperator
from app.connectors.sources.azure_blob.connector import (
    AzureBlobConnector,
    get_file_extension,
    get_folder_path_segments_from_blob_name,
    get_mimetype_for_azure_blob,
    get_parent_path_from_blob_name,
)
from app.models.entities import User
from app.models.permission import EntityType, PermissionType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_logger():
    return logging.getLogger("test.azure_blob_deep")


@pytest.fixture()
def mock_dep():
    proc = MagicMock()
    proc.org_id = "org-az-deep"
    proc.on_new_app_users = AsyncMock()
    proc.on_new_record_groups = AsyncMock()
    proc.on_new_records = AsyncMock()
    proc.get_all_active_users = AsyncMock(return_value=[])
    proc.account_name = "teststorage"
    proc.get_user_by_user_id = AsyncMock(
        return_value=User(
            email="user@test.com",
            source_user_id="src-1",
            org_id="org-az-deep",
            full_name="Test User",
            title="Title",
        )
    )
    return proc


@pytest.fixture()
def mock_ds_provider():
    provider = MagicMock()
    mock_tx = MagicMock()
    mock_tx.get_record_by_external_id = AsyncMock(return_value=None)
    mock_tx.get_record_by_external_revision_id = AsyncMock(return_value=None)
    mock_tx.get_user_by_user_id = AsyncMock(return_value={"email": "user@test.com"})
    mock_tx.ensure_team_app_edge = AsyncMock()
    mock_tx.delete_parent_child_edge_to_record = AsyncMock(return_value=0)
    mock_tx.__aenter__ = AsyncMock(return_value=mock_tx)
    mock_tx.__aexit__ = AsyncMock(return_value=None)
    provider.transaction.return_value = mock_tx
    return provider


@pytest.fixture()
def mock_config():
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={
        "auth": {"azureBlobConnectionString": "AccountName=teststorage;AccountKey=abc"},
        "scope": "TEAM",
        "created_by": "user-1",
    })
    return svc


def _make_response(success=True, data=None, error=None):
    r = MagicMock()
    r.success = success
    r.data = data
    r.error = error
    return r


@pytest.fixture()
def connector(mock_logger, mock_dep, mock_ds_provider, mock_config):
    with patch("app.connectors.sources.azure_blob.connector.AzureBlobApp"):
        conn = AzureBlobConnector(
            logger=mock_logger,
            data_entities_processor=mock_dep,
            data_store_provider=mock_ds_provider,
            config_service=mock_config,
            connector_id="az-deep-1",
            scope="personal",
            created_by="test-user-id",
        )
    conn.data_source = AsyncMock()
    conn.account_name = "teststorage"
    conn.sync_filters = FilterCollection()
    conn.indexing_filters = FilterCollection()
    conn.record_sync_point = AsyncMock()
    conn.record_sync_point.read_sync_point = AsyncMock(return_value=None)
    conn.record_sync_point.update_sync_point = AsyncMock()
    return conn


# ---------------------------------------------------------------------------
# run_sync deep paths
# ---------------------------------------------------------------------------

class TestAzureBlobRunSync:
    async def test_run_sync_not_initialized_raises(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector.run_sync()

    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_lists_all_containers(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(data=[{"name": "c1"}, {"name": "c2"}])
        )
        with patch.object(connector, "_create_record_groups_for_containers", new_callable=AsyncMock), \
             patch.object(connector, "_sync_container", new_callable=AsyncMock) as mock_sync:
            await connector.run_sync()
            assert mock_sync.call_count == 2

    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_uses_configured_container(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.container_name = "my-container"
        with patch.object(connector, "_create_record_groups_for_containers", new_callable=AsyncMock), \
             patch.object(connector, "_sync_container", new_callable=AsyncMock) as mock_sync:
            await connector.run_sync()
            mock_sync.assert_called_once_with("my-container")

    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_uses_filter_containers(self, mock_filters, connector):
        mock_filter = MagicMock()
        mock_filter.value = ["filtered-c1", "filtered-c2"]
        fc = MagicMock()
        fc.get = MagicMock(return_value=mock_filter)
        mock_filters.return_value = (fc, FilterCollection())
        connector.sync_filters = fc

        with patch.object(connector, "_create_record_groups_for_containers", new_callable=AsyncMock), \
             patch.object(connector, "_sync_container", new_callable=AsyncMock) as mock_sync:
            await connector.run_sync()
            assert mock_sync.call_count == 2

    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_no_containers_returns(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(data=[])
        )
        with patch.object(connector, "_sync_container", new_callable=AsyncMock) as mock_sync:
            await connector.run_sync()
            mock_sync.assert_not_called()

    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_failed_list_containers(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(success=False, error="Access denied")
        )
        with patch.object(connector, "_sync_container", new_callable=AsyncMock) as mock_sync:
            await connector.run_sync()
            mock_sync.assert_not_called()

    @patch("app.connectors.sources.azure_blob.connector.load_connector_filters", new_callable=AsyncMock)
    async def test_run_sync_container_error_continues(self, mock_filters, connector):
        mock_filters.return_value = (FilterCollection(), FilterCollection())
        connector.data_source.list_containers = AsyncMock(
            return_value=_make_response(data=[{"name": "c1"}, {"name": "c2"}])
        )
        call_count = 0

        async def sync_fail(name):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Container error")

        with patch.object(connector, "_create_record_groups_for_containers", new_callable=AsyncMock), \
             patch.object(connector, "_sync_container", side_effect=sync_fail):
            await connector.run_sync()
        assert call_count == 2


# ---------------------------------------------------------------------------
# _create_record_groups_for_containers
# ---------------------------------------------------------------------------

class TestCreateRecordGroups:
    async def test_team_scope_uses_org_permission(self, connector):
        connector.scope = ConnectorScope.TEAM.value
        await connector._create_record_groups_for_containers(["c1"])
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        _, permissions = call_args[0]
        assert permissions[0].entity_type == EntityType.ORG

    async def test_personal_scope_uses_creator_permission(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.creator_email = "creator@t.com"
        connector.created_by = "uid-1"
        await connector._create_record_groups_for_containers(["c1"])
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        _, permissions = call_args[0]
        assert permissions[0].email == "creator@t.com"
        assert permissions[0].type == PermissionType.OWNER

    async def test_personal_scope_no_creator_falls_back_to_org(self, connector):
        connector.scope = ConnectorScope.PERSONAL.value
        connector.creator_email = None
        await connector._create_record_groups_for_containers(["c1"])
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        _, permissions = call_args[0]
        assert permissions[0].entity_type == EntityType.ORG

    async def test_empty_list_no_op(self, connector):
        await connector._create_record_groups_for_containers([])
        connector.data_entities_processor.on_new_record_groups.assert_not_called()

    async def test_none_container_name_skipped(self, connector):
        await connector._create_record_groups_for_containers([None, "c1"])
        call_args = connector.data_entities_processor.on_new_record_groups.call_args[0][0]
        assert len(call_args) == 1


# ---------------------------------------------------------------------------
# _sync_container deep paths
# ---------------------------------------------------------------------------

class TestSyncContainer:

    async def test_not_initialized_raises(self, connector):
        connector.data_source = None
        with pytest.raises(ConnectionError):
            await connector._sync_container("c1")

    async def test_failed_list_blobs(self, connector):
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(success=False, error="failed")
        )
        await connector._sync_container("c1")
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_none_blobs_iterator(self, connector):
        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(data=None)
        )
        await connector._sync_container("c1")
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_processes_blob_to_record(self, connector):
        async def _blob_iter():
            yield {"name": "file.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 100, "etag": '"abc"'}

        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(data=_blob_iter())
        )

        with patch.object(connector, "_process_azure_blob", new_callable=AsyncMock) as mock_proc, \
             patch.object(connector, "_create_azure_blob_permissions", new_callable=AsyncMock, return_value=[]):
            mock_record = MagicMock()
            mock_proc.return_value = (mock_record, [])
            await connector._sync_container("c1")
            connector.data_entities_processor.on_new_records.assert_called()

    async def test_folder_blob_skips_ensure_parent(self, connector):
        async def _blob_iter():
            yield {"name": "folder/", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 0}

        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(data=_blob_iter())
        )

        with patch.object(connector, "_process_azure_blob", new_callable=AsyncMock, return_value=(MagicMock(), [])), \
             patch.object(connector, "_ensure_parent_folders_exist", new_callable=AsyncMock) as mock_ensure:
            await connector._sync_container("c1")
            mock_ensure.assert_not_called()

    async def test_blob_error_continues(self, connector):
        async def _blob_iter():
            yield {"name": "bad.txt", "last_modified": "invalid", "size": 0}
            yield {"name": "good.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc), "size": 100, "etag": '"x"'}

        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(data=_blob_iter())
        )

        call_count = 0

        async def _proc(blob, container):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("bad blob")
            return (MagicMock(), [])

        with patch.object(connector, "_process_azure_blob", side_effect=_proc):
            await connector._sync_container("c1")
        assert call_count == 2

    async def test_updates_sync_point_with_max_timestamp(self, connector):
        dt = datetime(2025, 6, 1, tzinfo=timezone.utc)

        async def _blob_iter():
            yield {"name": "file.txt", "last_modified": dt, "size": 100, "etag": '"x"'}

        connector.data_source.list_blobs = AsyncMock(
            return_value=_make_response(data=_blob_iter())
        )
        with patch.object(connector, "_process_azure_blob", new_callable=AsyncMock, return_value=(MagicMock(), [])):
            await connector._sync_container("c1")
        connector.record_sync_point.update_sync_point.assert_called_once()
        args = connector.record_sync_point.update_sync_point.call_args[0]
        assert args[1]["last_sync_time"] == int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# _pass_date_filters
# ---------------------------------------------------------------------------

class TestAzureBlobDateFilters:
    def test_folder_always_passes(self, connector):
        blob = {"name": "folder/"}
        assert connector._pass_date_filters(blob) is True

    def test_no_filters_passes(self, connector):
        blob = {"name": "file.txt", "last_modified": datetime(2025, 1, 1, tzinfo=timezone.utc)}
        assert connector._pass_date_filters(blob) is True

    def test_modified_after_fails(self, connector):
        blob = {"name": "file.txt", "last_modified": datetime(2024, 1, 1, tzinfo=timezone.utc)}
        cutoff = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, modified_after_ms=cutoff) is False

    def test_modified_before_fails(self, connector):
        blob = {"name": "file.txt", "last_modified": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        cutoff = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, modified_before_ms=cutoff) is False

    def test_created_after_fails(self, connector):
        blob = {
            "name": "file.txt",
            "last_modified": datetime(2025, 6, 1, tzinfo=timezone.utc),
            "creation_time": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }
        cutoff = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, created_after_ms=cutoff) is False

    def test_string_last_modified(self, connector):
        blob = {"name": "file.txt", "last_modified": "2024-01-01T00:00:00Z"}
        cutoff = int(datetime(2025, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        assert connector._pass_date_filters(blob, modified_after_ms=cutoff) is False

    def test_invalid_string_last_modified_passes(self, connector):
        blob = {"name": "file.txt", "last_modified": "invalid"}
        assert connector._pass_date_filters(blob, modified_after_ms=1000) is True

    def test_no_last_modified_passes(self, connector):
        blob = {"name": "file.txt"}
        assert connector._pass_date_filters(blob, modified_after_ms=1000) is True


# ---------------------------------------------------------------------------
# _pass_extension_filter
# ---------------------------------------------------------------------------

class TestAzureBlobExtensionFilter:
    def test_folder_passes(self, connector):
        assert connector._pass_extension_filter("folder/", is_folder=True) is True

    def test_no_filter_passes(self, connector):
        assert connector._pass_extension_filter("file.txt") is True

    def test_in_filter_matches(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )
        assert connector._pass_extension_filter("report.pdf") is True

    def test_in_filter_no_match(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )
        assert connector._pass_extension_filter("report.txt") is False

    def test_not_in_filter(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )
        assert connector._pass_extension_filter("report.txt") is True

    def test_no_extension_in_operator_fails(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )
        assert connector._pass_extension_filter("Makefile") is False

    def test_no_extension_not_in_operator_passes(self, connector):
        mock_filter = MagicMock()
        mock_filter.is_empty.return_value = False
        mock_filter.value = ["pdf"]
        mock_filter.get_operator.return_value = MagicMock(value=FilterOperator.NOT_IN)
        connector.sync_filters = MagicMock()
        connector.sync_filters.get = MagicMock(
            side_effect=lambda k: mock_filter if k == "file_extensions" else None
        )
        assert connector._pass_extension_filter("Makefile") is True


# ---------------------------------------------------------------------------
# _blob_properties_to_dict
# ---------------------------------------------------------------------------

class TestBlobPropertiesToDict:
    def test_dict_input_passthrough(self, connector):
        blob = {"name": "test.txt", "size": 100}
        assert connector._blob_properties_to_dict(blob) == blob

    def test_object_input_conversion(self, connector):
        blob = MagicMock()
        blob.name = "test.txt"
        blob.last_modified = datetime(2025, 1, 1, tzinfo=timezone.utc)
        blob.creation_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
        blob.etag = '"abc"'
        blob.size = 100
        content_settings = MagicMock()
        content_settings.content_type = "text/plain"
        content_settings.content_md5 = b"\x00\x01"
        blob.content_settings = content_settings

        result = connector._blob_properties_to_dict(blob)
        assert result["name"] == "test.txt"
        assert result["size"] == 100
        assert result["content_type"] == "text/plain"
        assert result["content_md5"] == b"\x00\x01"


# ---------------------------------------------------------------------------
# _get_azure_blob_revision_id
# ---------------------------------------------------------------------------

class TestGetRevisionId:
    def test_md5_bytes(self, connector):
        blob = {"content_md5": b"\xde\xad\xbe\xef"}
        result = connector._get_azure_blob_revision_id(blob)
        assert len(result) > 0

    def test_md5_string(self, connector):
        blob = {"content_md5": "abc123"}
        assert connector._get_azure_blob_revision_id(blob) == "abc123"

    def test_etag_fallback(self, connector):
        blob = {"etag": '"etag-val"'}
        assert connector._get_azure_blob_revision_id(blob) == "etag-val"

    def test_no_md5_no_etag(self, connector):
        assert connector._get_azure_blob_revision_id({}) == ""


# ---------------------------------------------------------------------------
# _ensure_parent_folders_exist
# ---------------------------------------------------------------------------

class TestEnsureParentFolders:
    async def test_empty_segments_no_op(self, connector):
        await connector._ensure_parent_folders_exist("c1", [])
        connector.data_entities_processor.on_new_records.assert_not_called()

    async def test_creates_folder_records(self, connector):
        with patch.object(connector, "_create_azure_blob_permissions", new_callable=AsyncMock, return_value=[]):
            await connector._ensure_parent_folders_exist("c1", ["a", "a/b"])
        assert connector.data_entities_processor.on_new_records.call_count == 2

    async def test_root_folder_has_no_parent(self, connector):
        with patch.object(connector, "_create_azure_blob_permissions", new_callable=AsyncMock, return_value=[]):
            await connector._ensure_parent_folders_exist("c1", ["a"])
        call_args = connector.data_entities_processor.on_new_records.call_args[0][0]
        record, _ = call_args[0]
        assert record.parent_external_record_id is None
